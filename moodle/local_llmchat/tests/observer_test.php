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
 * Tests that events only ever write to the outbox.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat;

use local_llmchat\tests\fake_transport;

defined('MOODLE_INTERNAL') || die();

require_once(__DIR__ . '/fixtures/fake_transport.php');

/**
 * Tests for {@see observer}.
 *
 * @covers \local_llmchat\observer
 * @covers \local_llmchat\sync\outbox::enqueue
 */
final class observer_test extends \advanced_testcase {
    /** @var fake_transport */
    private $transport;

    protected function setUp(): void {
        parent::setUp();
        $this->resetAfterTest();
        $this->setAdminUser();
        set_config('syncenabled', 1, 'local_llmchat');
        set_config('apiurl', 'https://api.example', 'local_llmchat');
        secret::set('s');
        $this->transport = new fake_transport();
        api_client::set_test_transport($this->transport);
    }

    protected function tearDown(): void {
        api_client::set_test_transport(null);
        parent::tearDown();
    }

    /**
     * Outbox rows as [courseid, cmid, action].
     *
     * @return array
     */
    private function rows(): array {
        global $DB;
        return array_values(array_map(
            fn($r) => [(int)$r->courseid, (int)$r->cmid, $r->action],
            $DB->get_records('local_llmchat_outbox', null, 'courseid, cmid')
        ));
    }

    public function test_module_created_and_updated_enqueue_one_deduplicated_upsert(): void {
        $course = $this->getDataGenerator()->create_course();
        $this->clear();
        $page = $this->getDataGenerator()->create_module('page', ['course' => $course->id]);
        $cm = get_coursemodule_from_id('page', $page->cmid);
        \core\event\course_module_updated::create_from_cm($cm)->trigger();
        \core\event\course_module_updated::create_from_cm($cm)->trigger();
        $this->assertContains([(int)$course->id, (int)$page->cmid, 'upsert'], $this->rows());
        $this->assertSame(1, count(array_filter($this->rows(), fn($r) => $r[1] === (int)$page->cmid)));
        $this->assertSame([], $this->transport->requests, 'observers never call the network');
    }

    public function test_module_deleted_replaces_pending_upsert(): void {
        $course = $this->getDataGenerator()->create_course();
        $page = $this->getDataGenerator()->create_module('page', ['course' => $course->id]);
        $this->clear();
        course_delete_module($page->cmid);
        $this->assertSame([[(int)$course->id, (int)$page->cmid, 'delete']], array_values(array_filter(
            $this->rows(),
            fn($r) => $r[1] === (int)$page->cmid
        )));
    }

    public function test_course_update_enqueues_course_row(): void {
        $course = $this->getDataGenerator()->create_course();
        $this->clear();
        $course->fullname = 'Neu';
        update_course($course);
        $this->assertSame([[(int)$course->id, 0, 'upsert']], $this->rows());
    }

    public function test_course_delete_collapses_course_rows(): void {
        $course = $this->getDataGenerator()->create_course();
        $this->getDataGenerator()->create_module('page', ['course' => $course->id]);
        $this->assertNotEmpty($this->rows());
        delete_course($course, false);
        $this->assertSame([[(int)$course->id, 0, 'delete']], $this->rows());
    }

    public function test_book_chapter_event_enqueues_cm(): void {
        $course = $this->getDataGenerator()->create_course();
        $book = $this->getDataGenerator()->create_module('book', ['course' => $course->id]);
        $this->clear();
        $chapter = $this->getDataGenerator()->get_plugin_generator('mod_book')->create_chapter(['bookid' => $book->id]);
        $cm = get_coursemodule_from_id('book', $book->cmid);
        \mod_book\event\chapter_updated::create_from_chapter($book, \context_module::instance($cm->id), $chapter)->trigger();
        $this->assertSame([[(int)$course->id, (int)$book->cmid, 'upsert']], $this->rows());
    }

    public function test_disabled_sync_and_excluded_courses_enqueue_nothing(): void {
        $course = $this->getDataGenerator()->create_course();
        $other = $this->getDataGenerator()->create_course();
        $this->clear();
        set_config('syncenabled', 0, 'local_llmchat');
        $this->getDataGenerator()->create_module('page', ['course' => $course->id]);
        $this->assertSame([], $this->rows());
        set_config('syncenabled', 1, 'local_llmchat');
        set_config('includedcourses', (string)$other->id, 'local_llmchat');
        $this->getDataGenerator()->create_module('page', ['course' => $course->id]);
        $this->assertSame([], $this->rows());
    }

    /**
     * Empty the outbox.
     */
    private function clear(): void {
        global $DB;
        $DB->delete_records('local_llmchat_outbox');
    }
}
