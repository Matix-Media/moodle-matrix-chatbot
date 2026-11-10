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
 * Tests for the outbox: dedup, backoff, exhaustion, drain.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat;

use local_llmchat\http\response;
use local_llmchat\sync\outbox;
use local_llmchat\tests\fake_transport;

defined('MOODLE_INTERNAL') || die();

require_once(__DIR__ . '/fixtures/fake_transport.php');
require_once(__DIR__ . '/fixtures/module_helper.php');

/**
 * Tests for {@see outbox} and the incremental push.
 *
 * @covers \local_llmchat\sync\outbox
 * @covers \local_llmchat\sync\incremental
 * @covers \local_llmchat\task\drain_outbox_task
 */
final class outbox_test extends \advanced_testcase {
    /** @var fake_transport */
    private $transport;

    protected function setUp(): void {
        global $DB;
        parent::setUp();
        $this->resetAfterTest();
        $this->setAdminUser();
        set_config('apiurl', 'https://api.example', 'local_llmchat');
        set_config('syncenabled', 1, 'local_llmchat');
        set_config('maxattempts', 4, 'local_llmchat');
        secret::set('s');
        $this->transport = new fake_transport();
        api_client::set_test_transport($this->transport);
        $DB->delete_records('local_llmchat_outbox');
    }

    protected function tearDown(): void {
        api_client::set_test_transport(null);
        parent::tearDown();
    }

    /**
     * The only outbox row.
     *
     * @return \stdClass
     */
    private function row(): \stdClass {
        global $DB;
        $rows = $DB->get_records('local_llmchat_outbox');
        $this->assertCount(1, $rows);
        return reset($rows);
    }

    public function test_enqueue_dedups_and_resets_backoff(): void {
        outbox::enqueue(5, 7, outbox::UPSERT, 1000);
        outbox::record_failure($this->row(), 'boom', 1000);
        outbox::enqueue(5, 7, outbox::DELETE, 2000);
        $row = $this->row();
        $this->assertSame('delete', $row->action);
        $this->assertSame(0, (int)$row->attempts);
        $this->assertSame(2000, (int)$row->nextattempt);
        $this->assertNull($row->lasterror);
    }

    public function test_backoff_doubles_and_caps_at_six_hours(): void {
        $this->assertSame(60, outbox::backoff(1));
        $this->assertSame(120, outbox::backoff(2));
        $this->assertSame(240, outbox::backoff(3));
        $this->assertSame(6 * HOURSECS, outbox::backoff(20));
    }

    public function test_due_skips_future_and_exhausted_rows(): void {
        global $DB;
        outbox::enqueue(1, 1, outbox::UPSERT, 100);
        outbox::enqueue(1, 2, outbox::UPSERT, 100);
        outbox::enqueue(2, 3, outbox::UPSERT, 100);
        $DB->set_field('local_llmchat_outbox', 'nextattempt', 5000, ['cmid' => 2]);
        $DB->set_field('local_llmchat_outbox', 'attempts', 4, ['cmid' => 3]);
        $due = outbox::due(1000, 50);
        $this->assertSame([1], array_map(fn($r) => (int)$r->cmid, $due));
        $this->assertSame(1, outbox::counts()['exhausted']);
    }

    public function test_drain_pushes_upsert_and_removes_row(): void {
        $course = $this->getDataGenerator()->create_course();
        $page = local_llmchat_test_create_module('page', ['course' => $course->id, 'intro' => '']);
        outbox::clear();
        outbox::enqueue((int)$course->id, (int)$page->cmid, outbox::UPSERT, 0);
        $result = outbox::drain(api_client::from_config(), time());
        $this->assertSame(['pushed' => 1, 'failed' => 0, 'paused' => false], $result);
        $this->assertSame(['POST /ingest/courses', 'POST /ingest/documents'], $this->transport->sequence());
        $docs = $this->transport->bodies('POST', '/ingest/documents')[0];
        $this->assertSame("{$course->id}:{$page->cmid}:page", $docs[0]['doc_id']);
        $this->assertSame(0, outbox::counts()['pending']);
    }

    public function test_drain_uploads_missing_blobs(): void {
        $course = $this->getDataGenerator()->create_course();
        $folder = local_llmchat_test_create_module('folder', ['course' => $course->id, 'intro' => '']);
        get_file_storage()->create_file_from_string([
            'contextid' => \context_module::instance($folder->cmid)->id, 'component' => 'mod_folder',
            'filearea' => 'content', 'itemid' => 0, 'filepath' => '/', 'filename' => 'a.txt',
        ], 'hello');
        outbox::clear();
        $sha = hash('sha256', 'hello');
        $this->transport->routes['POST /ingest/documents'] = fake_transport::json(['needs_blob' => [$sha]]);
        outbox::enqueue((int)$course->id, (int)$folder->cmid, outbox::UPSERT, 0);
        outbox::drain(api_client::from_config(), time());
        $this->assertSame(['POST /ingest/courses', 'POST /ingest/documents', 'PUT /ingest/blobs/' . $sha],
            $this->transport->sequence());
        $this->assertSame('hello', $this->transport->requests[2]['body']);
    }

    public function test_drain_cm_delete_sends_prefix(): void {
        outbox::enqueue(5, 7, outbox::DELETE, 0);
        outbox::enqueue(6, 0, outbox::DELETE, 0);
        outbox::drain(api_client::from_config(), time());
        $this->assertSame([['5:7:*'], ['6:*']], $this->transport->bodies('POST', '/ingest/documents/delete'));
        $this->assertSame(0, outbox::counts()['pending']);
    }

    public function test_upsert_of_vanished_cm_becomes_delete(): void {
        outbox::enqueue(5, 999999, outbox::UPSERT, 0);
        outbox::drain(api_client::from_config(), time());
        $this->assertSame([['5:999999:*']], $this->transport->bodies('POST', '/ingest/documents/delete'));
    }

    public function test_failure_backs_off_then_exhausts(): void {
        $this->transport->handler = fn() => new response(500, 'oops');
        outbox::enqueue(5, 7, outbox::DELETE, 0);
        $now = 10000;
        for ($i = 1; $i <= 4; $i++) {
            $result = outbox::drain(api_client::from_config(), $now);
            $this->assertSame(1, $result['failed']);
            $row = $this->row();
            $this->assertSame($i, (int)$row->attempts);
            $this->assertSame($now + outbox::backoff($i), (int)$row->nextattempt);
            $this->assertNotEmpty($row->lasterror);
            $now = (int)$row->nextattempt;
        }
        $this->assertSame(['pushed' => 0, 'failed' => 0, 'paused' => false], outbox::drain(api_client::from_config(), $now));
        $this->assertSame(1, outbox::counts()['exhausted']);
        outbox::retry_exhausted();
        $this->assertSame(0, (int)$this->row()->attempts);
    }

    public function test_auth_failure_pauses_without_burning_attempts(): void {
        $this->transport->handler = fn() => new response(401, '{"detail":"unauthorized"}');
        outbox::enqueue(5, 7, outbox::DELETE, 0);
        outbox::enqueue(5, 8, outbox::DELETE, 0);
        $result = outbox::drain(api_client::from_config(), 10000);
        $this->assertTrue($result['paused']);
        $this->assertCount(1, $this->transport->requests, 'stops at the first 401');
        global $DB;
        $this->assertSame(0, (int)$DB->count_records_select('local_llmchat_outbox', 'attempts > 0'));
        $this->assertNotEmpty(get_config('local_llmchat', 'authfailedat'));
        $this->transport->handler = null;
        outbox::drain(api_client::from_config(), 10001);
        $this->assertEmpty(get_config('local_llmchat', 'authfailedat'), 'cleared after a success');
    }

    public function test_drain_orders_by_course(): void {
        outbox::enqueue(9, 1, outbox::DELETE, 0);
        outbox::enqueue(3, 1, outbox::DELETE, 0);
        outbox::drain(api_client::from_config(), time());
        $this->assertSame([['3:1:*'], ['9:1:*']], $this->transport->bodies('POST', '/ingest/documents/delete'));
    }

    public function test_task_drains_when_configured(): void {
        outbox::enqueue(5, 7, outbox::DELETE, 0);
        ob_start();
        (new \local_llmchat\task\drain_outbox_task())->execute();
        ob_end_clean();
        $this->assertSame(0, outbox::counts()['pending']);
    }

    public function test_task_does_nothing_when_unconfigured(): void {
        set_config('apiurl', '', 'local_llmchat');
        outbox::enqueue(5, 7, outbox::DELETE, 0);
        ob_start();
        (new \local_llmchat\task\drain_outbox_task())->execute();
        ob_end_clean();
        $this->assertSame(1, outbox::counts()['pending']);
        $this->assertSame([], $this->transport->requests);
    }
}
