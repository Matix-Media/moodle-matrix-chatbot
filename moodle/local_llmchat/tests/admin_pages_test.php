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
 * Tests for the status and quota page renderables.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat;

use local_llmchat\http\response;
use local_llmchat\output\quota_report;
use local_llmchat\output\status_page;
use local_llmchat\quota\policy;
use local_llmchat\sync\outbox;
use local_llmchat\tests\fake_transport;

defined('MOODLE_INTERNAL') || die();

require_once(__DIR__ . '/fixtures/fake_transport.php');

/**
 * Tests for the admin page renderables.
 *
 * @covers \local_llmchat\output\status_page
 * @covers \local_llmchat\output\quota_report
 */
final class admin_pages_test extends \advanced_testcase {
    /** @var \renderer_base */
    private $output;

    protected function setUp(): void {
        global $PAGE;
        parent::setUp();
        $this->resetAfterTest();
        $this->setAdminUser();
        $this->output = $PAGE->get_renderer('core');
    }

    public function test_status_page_shows_api_status_and_outbox(): void {
        $t = new fake_transport();
        $t->routes['GET /ingest/status'] = fake_transport::json([
            'site_id' => 'abc', 'documents' => 12, 'stages' => ['embed' => ['queued' => 3, 'failed' => 0]],
        ]);
        outbox::enqueue(1, 2, outbox::DELETE);
        $row = array_values(outbox::due(time(), 1))[0];
        outbox::record_failure($row, 'HTTP 500', time());
        $ctx = (new status_page(new api_client('https://api.example', 's', $t)))->export_for_template($this->output);
        $this->assertTrue($ctx['api']['ok']);
        $this->assertContains(['label' => 'stages.embed.queued', 'value' => '3'], $ctx['api']['rows']);
        $this->assertSame('abc', get_config('local_llmchat', 'siteid'));
        $this->assertSame(1, $ctx['outbox']['failing']);
        $this->assertSame('HTTP 500', $ctx['failed'][0]['lasterror']);
        $html = $this->output->render_from_template('local_llmchat/status_page', $ctx);
        $this->assertStringContainsString('stages.embed.queued', $html);
    }

    public function test_status_page_labels_auth_failure(): void {
        $t = new fake_transport(fn() => new response(401, '{}'));
        $ctx = (new status_page(new api_client('https://api.example', 's', $t)))->export_for_template($this->output);
        $this->assertFalse($ctx['api']['ok']);
        $this->assertSame(get_string('status:authfailed', 'local_llmchat'), $ctx['api']['error']);
    }

    public function test_quota_report_renders_usage_from_stubbed_api(): void {
        $user = $this->getDataGenerator()->create_user(['firstname' => 'Uta', 'lastname' => 'Nutzer']);
        set_config('user_tokens_per_day', 1000, 'local_llmchat');
        policy::save_override((int)$user->id, null, 0, true, 'Lehrkraft');
        $t = new fake_transport();
        $t->routes['GET /api/usage'] = fake_transport::json([
            'rows' => [['user_id' => (int)$user->id, 'tokens' => 4321, 'estimated' => true]], 'site_total' => 9999,
        ]);
        $ctx = (new quota_report(new api_client('https://api.example', 's', $t)))->export_for_template($this->output);
        $this->assertSame('1.000', $ctx['site']['userdaily']);
        $this->assertSame(get_string('quota:unlimited', 'local_llmchat'), $ctx['site']['sitedaily']);
        $this->assertSame('Uta Nutzer', $ctx['overrides'][0]['fullname']);
        $this->assertSame(get_string('quota:sitedefault', 'local_llmchat'), $ctx['overrides'][0]['day']);
        $this->assertSame(get_string('quota:unlimited', 'local_llmchat'), $ctx['overrides'][0]['month']);
        $this->assertTrue($ctx['overrides'][0]['exempt']);
        $this->assertSame('Uta Nutzer', $ctx['usage'][0]['rows'][0]['user']);
        $this->assertSame('4.321', $ctx['usage'][0]['rows'][0]['tokens']);
        $this->assertTrue($ctx['usage'][0]['rows'][0]['estimated']);
        $this->assertSame('9.999', $ctx['usage'][0]['sitetotal']);
        $requests = array_map(fn($r) => $r['url'], $t->requests);
        $this->assertContains('https://api.example/api/usage?period=day&by=user', $requests);
        $this->assertContains('https://api.example/api/usage?period=month&by=user', $requests);
        $html = $this->output->render_from_template('local_llmchat/quota_report', $ctx);
        $this->assertStringContainsString('Uta Nutzer', $html);
    }

    public function test_quota_report_without_api(): void {
        $ctx = (new quota_report(null))->export_for_template($this->output);
        $this->assertSame([], $ctx['usage'][0]['rows']);
    }
}
