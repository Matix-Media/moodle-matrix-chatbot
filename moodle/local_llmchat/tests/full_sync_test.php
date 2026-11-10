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
 * Tests for the manifest-based, resumable full sync.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat;

use local_llmchat\http\response;
use local_llmchat\sync\full_sync;
use local_llmchat\tests\fake_transport;

defined('MOODLE_INTERNAL') || die();

require_once(__DIR__ . '/fixtures/fake_transport.php');
require_once(__DIR__ . '/fixtures/module_helper.php');

/**
 * Tests for {@see full_sync}.
 *
 * @covers \local_llmchat\sync\full_sync
 * @covers \local_llmchat\sync\pusher
 * @covers \local_llmchat\task\full_sync_task
 * @covers \local_llmchat\task\run_full_sync_task
 */
final class full_sync_test extends \advanced_testcase {
    /** @var fake_transport */
    private $transport;
    /** @var \stdClass */
    private $c1;
    /** @var \stdClass */
    private $c2;
    /** @var int */
    private $folder;

    protected function setUp(): void {
        global $DB;
        parent::setUp();
        $this->resetAfterTest();
        $this->setAdminUser();
        set_config('apiurl', 'https://api.example', 'local_llmchat');
        set_config('syncenabled', 1, 'local_llmchat');
        secret::set('s');
        $gen = $this->getDataGenerator();
        // Existing courses on a fresh test site would be part of the run; keep it to ours.
        $this->c1 = $gen->create_course(['shortname' => 'C1']);
        $this->c2 = $gen->create_course(['shortname' => 'C2']);
        local_llmchat_test_create_module('page', ['course' => $this->c1->id, 'intro' => '', 'content' => 'eins']);
        local_llmchat_test_create_module('page', ['course' => $this->c1->id, 'intro' => '', 'content' => 'zwei']);
        $this->folder = (int)local_llmchat_test_create_module('folder', ['course' => $this->c2->id, 'intro' => ''])->cmid;
        get_file_storage()->create_file_from_string([
            'contextid' => \context_module::instance($this->folder)->id, 'component' => 'mod_folder',
            'filearea' => 'content', 'itemid' => 0, 'filepath' => '/', 'filename' => 'f.txt',
        ], 'filebytes');
        set_config('includedcourses', "{$this->c1->id},{$this->c2->id}", 'local_llmchat');
        $this->transport = new fake_transport();
        api_client::set_test_transport($this->transport);
        $DB->delete_records('local_llmchat_outbox');
    }

    protected function tearDown(): void {
        api_client::set_test_transport(null);
        parent::tearDown();
    }

    /**
     * Make the fake API need every document and blob it is told about.
     */
    private function api_needs_everything(): void {
        $this->transport->routes['POST /ingest/manifest'] = function ($json) {
            return fake_transport::json(['need' => array_column($json['entries'], 'doc_id'), 'need_blobs' => []]);
        };
        $this->transport->routes['POST /ingest/documents'] = function ($json) {
            $shas = [];
            foreach ($json as $doc) {
                if (isset($doc['file'])) {
                    $shas[] = $doc['file']['sha256'];
                }
            }
            return fake_transport::json(['needs_blob' => $shas]);
        };
    }

    public function test_full_run_request_sequence(): void {
        $this->api_needs_everything();
        $state = (new full_sync(api_client::from_config()))->run();
        $this->assertSame('finished', $state);
        $sha = hash('sha256', 'filebytes');
        $this->assertSame([
            'POST /ingest/sync/start',
            'PUT /ingest/policy',
            'POST /ingest/courses',
            'POST /ingest/manifest',
            'POST /ingest/documents',
            'POST /ingest/courses',
            'POST /ingest/manifest',
            'POST /ingest/documents',
            'PUT /ingest/blobs/' . $sha,
            'POST /ingest/sync/finish',
        ], $this->transport->sequence());

        $start = $this->transport->bodies('POST', '/ingest/sync/start')[0];
        $this->assertSame('full', $start['mode']);
        $manifests = $this->transport->bodies('POST', '/ingest/manifest');
        $this->assertSame([1, 2], array_column($manifests, 'chunk_no'));
        $this->assertSame([2, 2], array_column($manifests, 'of'));
        $this->assertSame([$start['sync_id'], $start['sync_id']], array_column($manifests, 'sync_id'));
        $this->assertCount(2, $manifests[0]['entries']);
        $this->assertSame(['sync_id' => $start['sync_id']], $this->transport->bodies('POST', '/ingest/sync/finish')[0]);
        $run = full_sync::latest();
        $this->assertSame('finished', $run->state);
        $this->assertSame(3, (int)$run->documents);
        $this->assertSame(1, (int)$run->blobs);
    }

    public function test_only_needed_documents_and_blobs_are_pushed(): void {
        $sha = hash('sha256', 'filebytes');
        $this->transport->routes['POST /ingest/manifest'] = function ($json) use ($sha) {
            // API already has everything of C1 and the C2 doc metadata, but lost the blob.
            return fake_transport::json(['need' => [], 'need_blobs' => $json['chunk_no'] === 2 ? [$sha] : []]);
        };
        (new full_sync(api_client::from_config()))->run();
        $this->assertSame([], $this->transport->bodies('POST', '/ingest/documents'));
        $this->assertContains('PUT /ingest/blobs/' . $sha, $this->transport->sequence());
    }

    public function test_resume_after_abort_continues_at_next_course_with_same_sync_id(): void {
        $this->api_needs_everything();
        $calls = 0;
        $needall = $this->transport->routes['POST /ingest/manifest'];
        $this->transport->routes['POST /ingest/manifest'] = function ($json) use (&$calls, $needall) {
            $calls++;
            return $calls === 2 ? new response(503, 'down') : $needall($json);
        };
        try {
            (new full_sync(api_client::from_config()))->run();
            $this->fail('no exception');
        } catch (api_exception $e) {
            $this->assertSame(503, $e->status);
        }
        $run = full_sync::latest();
        $this->assertSame('running', $run->state);
        $this->assertSame(1, (int)$run->chunkno);
        $this->assertSame((int)$this->c1->id, (int)$run->lastcourseid);
        $this->assertNotEmpty($run->lasterror);

        $this->transport->requests = [];
        $state = (new full_sync(api_client::from_config()))->run();
        $this->assertSame('finished', $state);
        $seq = $this->transport->sequence();
        $this->assertNotContains('POST /ingest/sync/start', $seq);
        $manifest = $this->transport->bodies('POST', '/ingest/manifest');
        $this->assertCount(1, $manifest);
        $this->assertSame(2, $manifest[0]['chunk_no']);
        $this->assertSame($run->syncid, $manifest[0]['sync_id']);
        $this->assertSame('POST /ingest/sync/finish', end($seq));
    }

    public function test_deadline_returns_partial_and_persists_progress(): void {
        $this->api_needs_everything();
        $sync = new full_sync(api_client::from_config());
        $this->assertSame('partial', $sync->run(time() - 1));
        $this->assertSame(0, (int)full_sync::latest()->chunkno);
        $this->assertNotContains('POST /ingest/sync/finish', $this->transport->sequence());
    }

    public function test_stale_run_is_abandoned_and_never_finished(): void {
        global $DB;
        $this->api_needs_everything();
        $DB->insert_record('local_llmchat_sync', (object)[
            'syncid' => 'old', 'mode' => 'full', 'state' => 'running', 'courseids' => '[1]', 'chunkno' => 0,
            'lastcourseid' => 0, 'timestarted' => time() - DAYSECS, 'timemodified' => time() - DAYSECS,
        ]);
        (new full_sync(api_client::from_config()))->run();
        $this->assertSame('abandoned', $DB->get_field('local_llmchat_sync', 'state', ['syncid' => 'old']));
        foreach ($this->transport->bodies('POST', '/ingest/sync/finish') as $body) {
            $this->assertNotSame('old', $body['sync_id']);
        }
    }

    public function test_restart_abandons_running_run(): void {
        $this->api_needs_everything();
        $sync = new full_sync(api_client::from_config());
        $sync->run(time() - 1);
        $first = full_sync::latest()->syncid;
        $sync->run(null, true);
        $this->assertNotSame($first, full_sync::latest()->syncid);
        $this->assertSame('finished', full_sync::latest()->state);
    }

    public function test_scheduled_task_runs_and_queues_continuation_when_partial(): void {
        $this->api_needs_everything();
        ob_start();
        (new \local_llmchat\task\full_sync_task())->execute();
        ob_end_clean();
        $this->assertSame('finished', full_sync::latest()->state);

        set_config('syncmaxruntime', -1, 'local_llmchat');
        ob_start();
        (new \local_llmchat\task\full_sync_task())->execute();
        ob_end_clean();
        $this->assertCount(1, \core\task\manager::get_adhoc_tasks(\local_llmchat\task\run_full_sync_task::class));
    }

    public function test_adhoc_restart_task(): void {
        $this->api_needs_everything();
        $task = \local_llmchat\task\run_full_sync_task::create(true);
        ob_start();
        $task->execute();
        ob_end_clean();
        $this->assertSame('finished', full_sync::latest()->state);
    }
}
