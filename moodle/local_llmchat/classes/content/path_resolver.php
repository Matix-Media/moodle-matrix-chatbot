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
 * Resolves agent paths to Moodle links for one user.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat\content;

use local_llmchat\access\scope_builder;

/**
 * Turns the agent's virtual paths ("/<course>/<section>/<activity>[/<folder dirs>/<file> | /<chapter>]")
 * into one link per segment, but only to what the given user can open: the walk goes through the
 * user's own modinfo, so hidden or restricted sections and activities, hidden book chapters and
 * courses the user cannot access resolve to null. Once a segment fails, the rest stay null.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class path_resolver {
    /**
     * Resolve a path.
     *
     * @param string $path
     * @param int $userid
     * @return array list of ['name' => string, 'url' => string|null]
     */
    public function resolve(string $path, int $userid): array {
        global $CFG;
        // Not loaded in AJAX requests (PHPUnit loads it through the data generators).
        require_once($CFG->dirroot . '/course/lib.php');
        $names = array_values(array_filter(array_map('trim', explode('/', $path)), fn($s) => $s !== ''));
        $out = array_map(fn($name) => ['name' => $name, 'url' => null], $names);
        if (!$names) {
            return [];
        }
        $course = $this->find_course($names[0], $userid);
        if (!$course) {
            return $out;
        }
        $out[0]['url'] = course_get_url($course)->out(false);
        if (count($names) < 2) {
            return $out;
        }
        $modinfo = get_fast_modinfo($course, $userid);
        $section = null;
        foreach ($modinfo->get_section_info_all() as $candidate) {
            if ($candidate->uservisible && self::same(get_section_name($course, $candidate), $names[1])) {
                $section = $candidate;
                break;
            }
        }
        if (!$section) {
            return $out;
        }
        $sectionurl = self::section_url($course, $section);
        $out[1]['url'] = $sectionurl->out(false);
        if (count($names) < 3) {
            return $out;
        }
        $cm = null;
        foreach ($modinfo->sections[$section->section] ?? [] as $cmid) {
            $candidate = $modinfo->cms[$cmid];
            if ($candidate->uservisible && empty($candidate->deletioninprogress) && self::same($candidate->name, $names[2])) {
                $cm = $candidate;
                break;
            }
        }
        if (!$cm) {
            return $out;
        }
        $out[2]['url'] = $cm->url ? $cm->url->out(false)
            : (new \moodle_url(course_get_url($course), null, 'module-' . $cm->id))->out(false);
        $rest = array_slice($names, 3);
        foreach ($this->resolve_inside($cm, $rest, $userid) as $i => $url) {
            $out[3 + $i]['url'] = $url;
        }
        return $out;
    }

    /**
     * The page of one section: /course/section.php (Moodle 4.4+), else the course page at that section.
     *
     * @param \stdClass $course
     * @param \section_info $section
     * @return \moodle_url
     */
    private static function section_url(\stdClass $course, \section_info $section): \moodle_url {
        global $CFG;
        if (file_exists($CFG->dirroot . '/course/section.php')) {
            return new \moodle_url('/course/section.php', ['id' => $section->id]);
        }
        return new \moodle_url('/course/view.php', ['id' => $course->id, 'section' => $section->section]);
    }

    /**
     * Links for the segments below an activity (folder directories and files, resource file,
     * book chapter).
     *
     * @param \cm_info $cm
     * @param string[] $rest
     * @param int $userid
     * @return array index => url (only resolved ones)
     */
    private function resolve_inside(\cm_info $cm, array $rest, int $userid): array {
        global $DB;
        if (!$rest) {
            return [];
        }
        $context = \context_module::instance($cm->id);
        $cmurl = $cm->url ? $cm->url->out(false) : null;
        if ($cm->modname === 'folder' || $cm->modname === 'resource') {
            $component = 'mod_' . $cm->modname;
            $dirs = array_slice($rest, 0, -1);
            $filename = end($rest);
            $wanted = '/' . ($dirs ? implode('/', $dirs) . '/' : '');
            $files = get_file_storage()->get_area_files($context->id, $component, 'content', 0, 'filepath, filename', false);
            foreach ($files as $file) {
                if (self::same($file->get_filepath(), $wanted) && self::same($file->get_filename(), $filename)) {
                    $links = array_fill(0, count($dirs), $cmurl);
                    $links[] = $cm->modname === 'folder'
                        ? \moodle_url::make_pluginfile_url(
                            $context->id,
                            $component,
                            'content',
                            0,
                            $file->get_filepath(),
                            $file->get_filename()
                        )->out(false)
                        : $cmurl;
                    return $links;
                }
            }
            return [];
        }
        if ($cm->modname === 'book' && count($rest) === 1) {
            $hidden = has_capability('mod/book:viewhiddenchapters', $context, $userid);
            foreach ($DB->get_records('book_chapters', ['bookid' => $cm->instance], 'pagenum') as $chapter) {
                if (($hidden || !$chapter->hidden) && self::same($chapter->title, $rest[0])) {
                    return [(new \moodle_url('/mod/book/view.php', ['id' => $cm->id, 'chapterid' => $chapter->id]))->out(false)];
                }
            }
        }
        return [];
    }

    /**
     * A course the user can access, by id, short name or full name.
     *
     * @param string $name
     * @param int $userid
     * @return \stdClass|null
     */
    private function find_course(string $name, int $userid): ?\stdClass {
        global $DB;
        $candidates = [];
        if (ctype_digit($name)) {
            $candidates = $DB->get_records('course', ['id' => (int)$name]);
        }
        if (!$candidates) {
            $select = $DB->sql_equal('shortname', ':s', false) . ' OR ' . $DB->sql_equal('fullname', ':f', false);
            $candidates = $DB->get_records_select('course', $select, ['s' => $name, 'f' => $name], 'id');
        }
        foreach ($candidates as $course) {
            if (
                $course->id != SITEID && scope_builder::course_included((int)$course->id)
                    && can_access_course($course, $userid, '', true)
            ) {
                return $course;
            }
        }
        return null;
    }

    /**
     * Case- and whitespace-insensitive comparison of display names.
     *
     * @param string $a
     * @param string $b
     * @return bool
     */
    private static function same(string $a, string $b): bool {
        $normalise = fn($s) => \core_text::strtolower(preg_replace('/\s+/u', ' ', trim(strip_tags($s))));
        return $normalise($a) === $normalise($b);
    }
}
