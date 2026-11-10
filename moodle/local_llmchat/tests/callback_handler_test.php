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
 * Tests for the bearer-protected callback endpoint logic.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat;

use local_llmchat\callback\handler;

/**
 * Tests for {@see handler}.
 *
 * @covers \local_llmchat\callback\handler
 */
final class callback_handler_test extends \advanced_testcase {
    /** @var \stdClass */
    private $course;
    /** @var \stdClass */
    private $user;
    /** @var int */
    private $visible;
    /** @var int */
    private $hidden;

    protected function setUp(): void {
        parent::setUp();
        $this->resetAfterTest();
        secret::set('the-secret');
        $gen = $this->getDataGenerator();
        $this->course = $gen->create_course(['fullname' => 'Mathe', 'shortname' => 'M']);
        $this->user = $gen->create_user(['firstname' => 'Ada', 'lastname' => 'L', 'lang' => 'en']);
        $gen->enrol_user($this->user->id, $this->course->id, 'student');
        $group = $gen->create_group(['courseid' => $this->course->id, 'name' => '7b']);
        $gen->create_group_member(['groupid' => $group->id, 'userid' => $this->user->id]);
        $this->visible = (int)$gen->create_module('page', ['course' => $this->course->id])->cmid;
        $this->hidden = (int)$gen->create_module('page', ['course' => $this->course->id, 'visible' => 0])->cmid;
    }

    /**
     * Call the handler.
     *
     * @param array|null $body
     * @param string|null $auth
     * @param string $method
     * @return array [status, decoded body]
     */
    private function call(?array $body, ?string $auth = 'Bearer the-secret', string $method = 'POST'): array {
        $result = (new handler())->handle($method, $auth, $body === null ? '' : json_encode($body));
        return [$result['status'], $result['body']];
    }

    public function test_missing_bearer_is_401(): void {
        [$status, $body] = $this->call(['action' => 'ping'], null);
        $this->assertSame(401, $status);
        $this->assertSame(['error' => 'unauthorized'], $body);
    }

    public function test_wrong_bearer_is_401_with_generic_body(): void {
        [$status, $body] = $this->call(['action' => 'ping'], 'Bearer nope');
        $this->assertSame(401, $status);
        $this->assertSame(['error' => 'unauthorized'], $body);
        [$status] = $this->call(['action' => 'ping'], 'Basic the-secret');
        $this->assertSame(401, $status);
    }

    public function test_unconfigured_plugin_refuses_everything(): void {
        set_config('secret', '', 'local_llmchat');
        [$status] = $this->call(['action' => 'ping'], 'Bearer ');
        $this->assertSame(401, $status);
    }

    public function test_non_post_is_405(): void {
        [$status] = $this->call(null, 'Bearer the-secret', 'GET');
        $this->assertSame(405, $status);
    }

    public function test_bad_json_and_unknown_action_are_400(): void {
        $result = (new handler())->handle('POST', 'Bearer the-secret', '{not json');
        $this->assertSame(400, $result['status']);
        [$status] = $this->call(['action' => 'nope']);
        $this->assertSame(400, $status);
        [$status] = $this->call(['action' => 'check_access', 'userid' => 'x', 'cmids' => 'y']);
        $this->assertSame(400, $status);
    }

    public function test_ping(): void {
        global $CFG;
        [$status, $body] = $this->call(['action' => 'ping']);
        $this->assertSame(200, $status);
        $this->assertTrue($body['ok']);
        $this->assertSame($CFG->wwwroot, $body['site_url']);
        $this->assertSame(get_config('local_llmchat', 'version'), (string)$body['plugin_version']);
    }

    public function test_check_access(): void {
        [$status, $body] = $this->call(['action' => 'check_access', 'userid' => (int)$this->user->id,
            'cmids' => [$this->visible, $this->hidden]]);
        $this->assertSame(200, $status);
        $this->assertSame(['allowed' => [$this->visible]], $body);
    }

    public function test_check_access_suspended_user_is_403(): void {
        global $DB;
        $DB->set_field('user', 'suspended', 1, ['id' => $this->user->id]);
        [$status, $body] = $this->call(['action' => 'check_access', 'userid' => (int)$this->user->id,
            'cmids' => [$this->visible]]);
        $this->assertSame(403, $status);
        $this->assertSame(['error' => 'user_unavailable'], $body);
    }

    public function test_user_info_returns_groups_per_course(): void {
        [$status, $body] = $this->call(['action' => 'user_info', 'userid' => (int)$this->user->id]);
        $this->assertSame(200, $status);
        $this->assertSame(['groups' => [(string)$this->course->id => ['7b']]], $body);
        $this->assertSame('{"groups":{"' . $this->course->id . '":["7b"]}}', json_encode($body));
    }

    public function test_user_info_empty_groups_encode_as_object(): void {
        $other = $this->getDataGenerator()->create_user();
        [$status, $body] = $this->call(['action' => 'user_info', 'userid' => (int)$other->id]);
        $this->assertSame(200, $status);
        $this->assertSame('{"groups":{}}', json_encode($body));
    }

    public function test_user_info_suspended_user_is_403(): void {
        global $DB;
        $DB->set_field('user', 'suspended', 1, ['id' => $this->user->id]);
        [$status] = $this->call(['action' => 'user_info', 'userid' => (int)$this->user->id]);
        $this->assertSame(403, $status);
    }

    public function test_legacy_user_id_key_is_still_accepted(): void {
        [$status, $body] = $this->call(['action' => 'check_access', 'user_id' => (int)$this->user->id,
            'cmids' => [$this->visible]]);
        $this->assertSame(200, $status);
        $this->assertSame(['allowed' => [$this->visible]], $body);
    }

    public function test_resolve_user_returns_profile_without_pii(): void {
        [$status, $body] = $this->call(['action' => 'resolve_user', 'userid' => (int)$this->user->id]);
        $this->assertSame(200, $status);
        $this->assertSame([
            'user_id' => (int)$this->user->id,
            'fullname' => 'Ada L',
            'lang' => 'en',
            'courses' => [[
                'course_id' => (int)$this->course->id,
                'fullname' => 'Mathe',
                'shortname' => 'M',
                'roles' => ['student'],
                'groups' => ['7b'],
            ]],
        ], $body);
        $this->assertStringNotContainsString($this->user->email, json_encode($body));
    }

    public function test_secret_never_echoed(): void {
        foreach ([['action' => 'ping'], ['action' => 'nope'], null] as $req) {
            $result = (new handler())->handle('POST', 'Bearer the-secret', $req === null ? 'x' : json_encode($req));
            $this->assertStringNotContainsString('the-secret', json_encode($result));
        }
    }
}
