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
 * Links Moodle objects by id for one user.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat\content;

use local_llmchat\access\scope_builder;

/**
 * Turns the refs of the API's locations - {kind, id} with kind course, section, module (cmid) or
 * document (the doc_id document_builder gave it, "{course}:{cmid}:{part}") - into links, but only to
 * what the given user can open: every check goes through the user's own modinfo, so hidden or
 * restricted sections and activities, hidden book chapters, missing files and courses the user
 * cannot access get an empty url. Nothing is matched by name.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class ref_resolver {
    /** @var array course id => \course_modinfo|null (null: the user cannot access the course) */
    private array $modinfo = [];

    /**
     * Resolve refs.
     *
     * @param array $refs [{kind, id}]
     * @param int $userid
     * @return array "kind:id" => url ('' when the user cannot open it or it does not exist)
     */
    public function resolve(array $refs, int $userid): array {
        global $CFG;
        // Not loaded in AJAX requests (PHPUnit loads it through the data generators).
        require_once($CFG->dirroot . '/course/lib.php');
        $this->modinfo = [];
        $out = [];
        foreach ($refs as $ref) {
            $kind = (string)($ref['kind'] ?? '');
            $id = (string)($ref['id'] ?? '');
            try {
                $url = $this->url($kind, $id, $userid);
            } catch (\moodle_exception $e) {
                $url = null;
            }
            $out["$kind:$id"] = $url ?? '';
        }
        return $out;
    }

    /**
     * The link for one ref, or null.
     *
     * @param string $kind
     * @param string $id
     * @param int $userid
     * @return string|null
     */
    private function url(string $kind, string $id, int $userid): ?string {
        global $DB;
        switch ($kind) {
            case 'course':
                if (!ctype_digit($id)) {
                    return null;
                }
                return $this->modinfo((int)$id, $userid) ? course_get_url((int)$id)->out(false) : null;
            case 'section':
                if (!ctype_digit($id)) {
                    return null;
                }
                $courseid = $DB->get_field('course_sections', 'course', ['id' => (int)$id]);
                $modinfo = $courseid ? $this->modinfo((int)$courseid, $userid) : null;
                $section = $modinfo ? $modinfo->get_section_info_by_id((int)$id) : null;
                return $section && $section->uservisible ? self::section_url($modinfo->get_course(), $section)->out(false) : null;
            case 'module':
                $cm = ctype_digit($id) ? $this->cm((int)$id, null, $userid) : null;
                return $cm ? self::cm_url($cm) : null;
            case 'document':
                return $this->document_url($id, $userid);
        }
        return null;
    }

    /**
     * The user's modinfo of a course they can access and the plugin includes, else null.
     *
     * @param int $courseid
     * @param int $userid
     * @return \course_modinfo|null
     */
    private function modinfo(int $courseid, int $userid): ?\course_modinfo {
        global $DB;
        if (!array_key_exists($courseid, $this->modinfo)) {
            $course = $courseid != SITEID ? $DB->get_record('course', ['id' => $courseid]) : false;
            $this->modinfo[$courseid] = $course && scope_builder::course_included($courseid)
                && can_access_course($course, $userid, '', true) ? get_fast_modinfo($course, $userid) : null;
        }
        return $this->modinfo[$courseid];
    }

    /**
     * An activity the user can open, optionally only in a given course.
     *
     * @param int $cmid
     * @param int|null $courseid
     * @param int $userid
     * @return \cm_info|null
     */
    private function cm(int $cmid, ?int $courseid, int $userid): ?\cm_info {
        global $DB;
        $actual = $DB->get_field('course_modules', 'course', ['id' => $cmid]);
        if (!$actual || ($courseid !== null && (int)$actual !== $courseid)) {
            return null;
        }
        $modinfo = $this->modinfo((int)$actual, $userid);
        $cm = $modinfo ? ($modinfo->cms[$cmid] ?? null) : null;
        return $cm && $cm->uservisible && empty($cm->deletioninprogress) ? $cm : null;
    }

    /**
     * The link for one document: a book chapter, a folder file, else its activity.
     *
     * @param string $docid "{course}:{cmid}:{part}"
     * @param int $userid
     * @return string|null
     */
    private function document_url(string $docid, int $userid): ?string {
        global $DB;
        $parts = explode(':', $docid, 3);
        if (count($parts) !== 3 || !ctype_digit($parts[0]) || !ctype_digit($parts[1])) {
            return null;
        }
        [$courseid, $cmid, $part] = [(int)$parts[0], (int)$parts[1], $parts[2]];
        $cm = $this->cm($cmid, $courseid, $userid);
        if (!$cm) {
            return null;
        }
        $context = \context_module::instance($cm->id);
        if ($cm->modname === 'book' && preg_match('/^chapter:(\d+)$/', $part, $m)) {
            $chapter = $DB->get_record('book_chapters', ['id' => (int)$m[1], 'bookid' => $cm->instance]);
            if (!$chapter || ($chapter->hidden && !has_capability('mod/book:viewhiddenchapters', $context, $userid))) {
                return null;
            }
            return (new \moodle_url('/mod/book/view.php', ['id' => $cm->id, 'chapterid' => $chapter->id]))->out(false);
        }
        if ($cm->modname === 'folder' && str_starts_with($part, 'file:/')) {
            $full = substr($part, strlen('file:'));
            $slash = strrpos($full, '/');
            [$filepath, $filename] = [substr($full, 0, $slash + 1), substr($full, $slash + 1)];
            $file = get_file_storage()->get_file($context->id, 'mod_folder', 'content', 0, $filepath, $filename);
            if (!$file) {
                return null;
            }
            return \moodle_url::make_pluginfile_url($context->id, 'mod_folder', 'content', 0, $filepath, $filename)->out(false);
        }
        return self::cm_url($cm);
    }

    /**
     * An activity's page, or its place on the course page for activities without one (labels).
     *
     * @param \cm_info $cm
     * @return string
     */
    private static function cm_url(\cm_info $cm): string {
        return $cm->url ? $cm->url->out(false)
            : (new \moodle_url(course_get_url($cm->course), null, 'module-' . $cm->id))->out(false);
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
}
