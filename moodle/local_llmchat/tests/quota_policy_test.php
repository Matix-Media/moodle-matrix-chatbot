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
 * Tests for quota policy, overrides and exemptions.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat;

use local_llmchat\quota\policy;

/**
 * Tests for {@see policy}.
 *
 * @covers \local_llmchat\quota\policy
 */
final class quota_policy_test extends \advanced_testcase {
    protected function setUp(): void {
        parent::setUp();
        $this->resetAfterTest();
        set_config('user_tokens_per_day', 1000, 'local_llmchat');
        set_config('user_tokens_per_month', 20000, 'local_llmchat');
        set_config('site_tokens_per_day', 0, 'local_llmchat');
        set_config('site_tokens_per_month', 500000, 'local_llmchat');
        set_config('max_tokens_per_turn', 30000, 'local_llmchat');
        set_config('count_reasoning', 1, 'local_llmchat');
        set_config('exempt_users_bypass_site', 0, 'local_llmchat');
    }

    public function test_site_defaults_apply_without_override(): void {
        $user = $this->getDataGenerator()->create_user();
        $this->assertSame(['day' => 1000, 'month' => 20000, 'exempt' => false], policy::effective_for_user($user->id));
    }

    public function test_override_replaces_defaults_and_null_inherits(): void {
        $user = $this->getDataGenerator()->create_user();
        policy::save_override($user->id, 5000, null, false, 'Projektarbeit');
        $this->assertSame(['day' => 5000, 'month' => 20000, 'exempt' => false], policy::effective_for_user($user->id));
    }

    public function test_override_zero_means_unlimited(): void {
        $user = $this->getDataGenerator()->create_user();
        policy::save_override($user->id, 0, 0, false, '');
        $this->assertSame(['day' => null, 'month' => null, 'exempt' => false], policy::effective_for_user($user->id));
    }

    public function test_override_exempt_flag(): void {
        $user = $this->getDataGenerator()->create_user();
        policy::save_override($user->id, null, null, true, '');
        $this->assertTrue(policy::effective_for_user($user->id)['exempt']);
    }

    public function test_capability_exempts_user(): void {
        $user = $this->getDataGenerator()->create_user();
        $roleid = $this->getDataGenerator()->create_role();
        assign_capability('local/llmchat:unlimited', CAP_ALLOW, $roleid, \context_system::instance()->id);
        role_assign($roleid, $user->id, \context_system::instance()->id);
        $this->assertTrue(policy::effective_for_user($user->id)['exempt']);
    }

    public function test_save_override_updates_existing_row_and_delete(): void {
        global $DB;
        $user = $this->getDataGenerator()->create_user();
        policy::save_override($user->id, 1, 2, false, 'a');
        policy::save_override($user->id, 3, 4, true, 'b');
        $this->assertSame(1, $DB->count_records('local_llmchat_quota_override', ['userid' => $user->id]));
        $this->assertSame(['day' => 3, 'month' => 4, 'exempt' => true], policy::effective_for_user($user->id));
        policy::delete_override($user->id);
        $this->assertSame(['day' => 1000, 'month' => 20000, 'exempt' => false], policy::effective_for_user($user->id));
    }

    public function test_site_policy_wire_body(): void {
        $body = policy::site_policy();
        $this->assertSame([
            'site_tokens_per_day' => null,
            'site_tokens_per_month' => 500000,
            'user_tokens_per_day' => 1000,
            'user_tokens_per_month' => 20000,
            'max_tokens_per_turn' => 30000,
            'count_reasoning' => true,
            'exempt_users_bypass_site' => false,
            'timezone' => \core_date::get_server_timezone(),
        ], $body);
    }

    public function test_settings_change_queues_policy_push(): void {
        policy::queue_push();
        policy::queue_push();
        $tasks = \core\task\manager::get_adhoc_tasks(\local_llmchat\task\push_policy_task::class);
        $this->assertCount(1, $tasks);
    }
}
