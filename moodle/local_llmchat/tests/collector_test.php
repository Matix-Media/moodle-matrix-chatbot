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
 * Tests for the course collector.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat;

use local_llmchat\content\collector;

defined('MOODLE_INTERNAL') || die();

require_once(__DIR__ . '/fixtures/module_helper.php');

/**
 * Tests for {@see collector}.
 *
 * @covers \local_llmchat\content\collector
 */
final class collector_test extends \advanced_testcase {
    protected function setUp(): void {
        parent::setUp();
        $this->resetAfterTest();
        $this->setAdminUser();
    }

    public function test_course_ids_skip_site_and_honour_included_setting(): void {
        $a = $this->getDataGenerator()->create_course();
        $b = $this->getDataGenerator()->create_course(['visible' => 0]);
        $collector = new collector();
        $this->assertSame([(int)$a->id, (int)$b->id], $collector->course_ids());
        set_config('includedcourses', $b->id, 'local_llmchat');
        $this->assertSame([(int)$b->id], $collector->course_ids());
    }

    public function test_collect_course_returns_course_record_and_all_documents(): void {
        $course = $this->getDataGenerator()->create_course(['fullname' => 'Kurs', 'shortname' => 'K1',
            'startdate' => 1700000000, 'enddate' => 1710000000]);
        $p1 = local_llmchat_test_create_module('page', ['course' => $course->id, 'intro' => '']);
        $p2 = local_llmchat_test_create_module('page', ['course' => $course->id, 'intro' => '', 'visible' => 0]);
        $bundle = (new collector())->collect_course((int)$course->id);

        $record = $bundle['course'];
        $this->assertSame((int)$course->id, $record['course_id']);
        $this->assertSame('Kurs', $record['fullname']);
        $this->assertSame('K1', $record['shortname']);
        $this->assertTrue($record['visible']);
        $this->assertSame(1700000000, $record['startdate']);
        $this->assertSame(1710000000, $record['enddate']);
        $this->assertIsInt($record['timemodified']);

        $ids = array_map(fn($d) => $d->id(), $bundle['documents']);
        // Hidden cms are indexed too: per-user visibility is enforced by scope + check_access.
        $this->assertSame(["{$course->id}:{$p1->cmid}:page", "{$course->id}:{$p2->cmid}:page"], $ids);
    }

    public function test_collect_cm(): void {
        $course = $this->getDataGenerator()->create_course();
        $page = local_llmchat_test_create_module('page', ['course' => $course->id, 'intro' => '']);
        $bundle = (new collector())->collect_cm((int)$page->cmid);
        $this->assertSame((int)$course->id, $bundle['course']['course_id']);
        $this->assertCount(1, $bundle['documents']);
        $this->assertNull((new collector())->collect_cm(999999));
    }

    public function test_collect_course_missing_returns_null(): void {
        $this->assertNull((new collector())->collect_course(999999));
    }

    public function test_deletion_in_progress_cms_skipped(): void {
        global $DB;
        $course = $this->getDataGenerator()->create_course();
        $page = local_llmchat_test_create_module('page', ['course' => $course->id, 'intro' => '']);
        $DB->set_field('course_modules', 'deletioninprogress', 1, ['id' => $page->cmid]);
        rebuild_course_cache($course->id, true);
        $this->assertSame([], (new collector())->collect_course((int)$course->id)['documents']);
    }
}
