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
 * Tests for the issue_token external function.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat\external;

use core_external\external_api;
use local_llmchat\access\jwt;
use local_llmchat\secret;

/**
 * Tests for {@see issue_token}.
 *
 * @covers \local_llmchat\external\issue_token
 */
final class issue_token_test extends \advanced_testcase {
    protected function setUp(): void {
        parent::setUp();
        $this->resetAfterTest();
        secret::set('k');
        set_config('siteid', '1', 'local_llmchat');
        set_config('apiurl', 'https://api.example/', 'local_llmchat');
    }

    public function test_returns_token_for_self_that_verifies(): void {
        global $CFG;
        $course = $this->getDataGenerator()->create_course();
        $user = $this->getDataGenerator()->create_user();
        $this->getDataGenerator()->enrol_user($user->id, $course->id, 'student');
        $this->setUser($user);

        $result = issue_token::execute((int)$course->id);
        $result = external_api::clean_returnvalue(issue_token::execute_returns(), $result);
        $claims = jwt::decode($result['token'], secret::chat_key(), $CFG->wwwroot);
        $this->assertSame((string)$user->id, $claims['sub']);
        $this->assertSame('1', \local_llmchat\access\jwt::header($result['token'])['kid']);
        $this->assertSame($claims['exp'], $result['expires']);
        $this->assertSame('wss://api.example/ws/chat', $result['wsurl']);
        $this->assertSame('https://api.example', $result['apiurl']);
    }

    public function test_requires_capability(): void {
        $user = $this->getDataGenerator()->create_user();
        $this->setUser($user);
        $roleid = $this->getDataGenerator()->create_role();
        assign_capability('local/llmchat:use', CAP_PROHIBIT, $roleid, \context_system::instance()->id);
        role_assign($roleid, $user->id, \context_system::instance()->id);
        $this->expectException(\required_capability_exception::class);
        issue_token::execute(0);
    }

    public function test_guest_refused(): void {
        $this->setGuestUser();
        $this->expectException(\moodle_exception::class);
        issue_token::execute(0);
    }
}
