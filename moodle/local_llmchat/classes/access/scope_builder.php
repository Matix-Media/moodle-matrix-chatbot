<?php
// This file is part of Moodle - https://moodle.org/
//
// Moodle is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// Moodle is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with Moodle.  If not, see <https://www.gnu.org/licenses/>.

/**
 * Builds per-user scopes from modinfo.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat\access;

/**
 * Computes what a user may see. The same code serves the chat token and the live
 * check_access callback, so both layers cannot disagree.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class scope_builder {
    /**
     * Build the scope for a user.
     *
     * @param int $userid
     * @param int|null $contextcourseid course the chat was opened from (added when accessible)
     * @return scope
     */
    public function build(int $userid, ?int $contextcourseid = null): scope {
        global $DB;
        $courses = [];
        foreach (enrol_get_all_users_courses($userid, true) as $course) {
            $courses[(int)$course->id] = $course;
        }
        if ($contextcourseid && $contextcourseid != SITEID && !isset($courses[$contextcourseid])) {
            $course = $DB->get_record('course', ['id' => $contextcourseid]);
            if ($course) {
                $courses[(int)$course->id] = $course;
            }
        }
        ksort($courses);

        $courseids = $cmids = $roles = $groups = $names = [];
        $archetypes = self::role_archetypes();
        foreach ($courses as $courseid => $course) {
            if (!self::course_included($courseid) || !can_access_course($course, $userid, '', true)) {
                continue;
            }
            $courseids[] = $courseid;
            $names[$courseid] = ['fullname' => $course->fullname, 'shortname' => $course->shortname];
            $modinfo = get_fast_modinfo($course, $userid);
            foreach ($modinfo->get_cms() as $cm) {
                if ($cm->uservisible && empty($cm->deletioninprogress)) {
                    $cmids[] = (int)$cm->id;
                }
            }
            $context = \context_course::instance($courseid);
            $courseroles = [];
            foreach (get_user_roles($context, $userid, false) as $ra) {
                $courseroles[] = $archetypes[$ra->roleid] ?? $ra->shortname;
            }
            $courseroles = array_values(array_unique($courseroles));
            sort($courseroles);
            $roles[$courseid] = $courseroles;
            $groupnames = array_values(array_map(fn($g) => $g->name, groups_get_all_groups($courseid, $userid)));
            sort($groupnames);
            $groups[$courseid] = $groupnames;
        }
        $cmids = array_values(array_unique($cmids));
        sort($cmids);
        return new scope($courseids, $cmids, $roles, $groups, $names);
    }

    /**
     * Which of the given cmids the user can see right now.
     *
     * @param int $userid
     * @param int[] $cmids
     * @return int[] sorted allowed cmids
     */
    public function visible_cmids(int $userid, array $cmids): array {
        global $DB;
        $cmids = array_values(array_unique(array_map('intval', $cmids)));
        if (!$cmids) {
            return [];
        }
        $bycourse = [];
        foreach ($DB->get_records_list('course_modules', 'id', $cmids, '', 'id, course') as $row) {
            $bycourse[(int)$row->course][] = (int)$row->id;
        }
        $allowed = [];
        foreach ($bycourse as $courseid => $ids) {
            $course = $DB->get_record('course', ['id' => $courseid]);
            if (!$course || !self::course_included($courseid) || !can_access_course($course, $userid, '', true)) {
                continue;
            }
            $modinfo = get_fast_modinfo($course, $userid);
            foreach ($ids as $cmid) {
                $cm = $modinfo->cms[$cmid] ?? null;
                if ($cm && $cm->uservisible && empty($cm->deletioninprogress)) {
                    $allowed[] = $cmid;
                }
            }
        }
        sort($allowed);
        return $allowed;
    }

    /**
     * Whether a course is part of the synced set (setting includedcourses, empty = all).
     *
     * @param int $courseid
     * @return bool
     */
    public static function course_included(int $courseid): bool {
        if ($courseid == SITEID) {
            return false;
        }
        $ids = self::included_course_ids();
        return $ids === null || in_array($courseid, $ids, true);
    }

    /**
     * Configured included course ids, or null for all.
     *
     * @return int[]|null
     */
    public static function included_course_ids(): ?array {
        $raw = trim((string)get_config('local_llmchat', 'includedcourses'));
        if ($raw === '') {
            return null;
        }
        $ids = array_filter(array_map('intval', preg_split('/[\s,;]+/', $raw)));
        return array_values($ids);
    }

    /**
     * Role id => archetype (or shortname for roles without an archetype).
     *
     * @return array
     */
    private static function role_archetypes(): array {
        $out = [];
        foreach (get_all_roles() as $role) {
            $out[$role->id] = $role->archetype !== '' && $role->archetype !== null ? $role->archetype : $role->shortname;
        }
        return $out;
    }
}
