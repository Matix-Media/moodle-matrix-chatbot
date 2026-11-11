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
 * Walks courses and builds DocumentIn records.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat\content;

use local_llmchat\access\scope_builder;

/**
 * Walks a course (sections → cms via modinfo) and builds its documents. Every cm is indexed,
 * including hidden ones; per-user visibility is enforced by the token scope and check_access.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class collector {
    /** @var document_builder */
    private document_builder $builder;

    /**
     * Constructor.
     *
     * @param document_builder|null $builder
     */
    public function __construct(?document_builder $builder = null) {
        $this->builder = $builder ?? new document_builder();
    }

    /**
     * Ids of every course to sync, ascending.
     *
     * @return int[]
     */
    public function course_ids(): array {
        global $DB;
        $ids = array_map('intval', $DB->get_fieldset_select('course', 'id', 'id <> :site', ['site' => SITEID]));
        sort($ids);
        return array_values(array_filter($ids, fn($id) => scope_builder::course_included($id)));
    }

    /**
     * Course record for POST /ingest/courses.
     *
     * @param \stdClass $course
     * @return array
     */
    public function course_record(\stdClass $course): array {
        return [
            'course_id' => (int)$course->id,
            'fullname' => trim(strip_tags($course->fullname)),
            'shortname' => trim(strip_tags($course->shortname)),
            'visible' => (bool)$course->visible,
            'startdate' => (int)$course->startdate,
            'enddate' => (int)$course->enddate,
            'timemodified' => (int)$course->timemodified,
        ];
    }

    /**
     * Course record and all documents of a course.
     *
     * @param int $courseid
     * @return array{course: array, documents: document[]}|null null when the course is gone
     */
    public function collect_course(int $courseid): ?array {
        global $DB;
        $course = $DB->get_record('course', ['id' => $courseid]);
        if (!$course) {
            return null;
        }
        $documents = [];
        foreach (get_fast_modinfo($course)->get_cms() as $cm) {
            foreach ($this->builder->build($cm, $course) as $doc) {
                $documents[] = $doc;
            }
        }
        return ['course' => $this->course_record($course), 'documents' => $documents];
    }

    /**
     * Course record and documents of a single cm.
     *
     * @param int $cmid
     * @return array{course: array, documents: document[]}|null null when the cm is gone
     */
    public function collect_cm(int $cmid): ?array {
        global $DB;
        $courseid = $DB->get_field('course_modules', 'course', ['id' => $cmid]);
        if (!$courseid) {
            return null;
        }
        $course = $DB->get_record('course', ['id' => $courseid], '*', MUST_EXIST);
        $cm = get_fast_modinfo($course)->cms[$cmid] ?? null;
        if (!$cm) {
            return null;
        }
        return ['course' => $this->course_record($course), 'documents' => $this->builder->build($cm, $course)];
    }
}
