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
 * Tests for chat token claims.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat;

use local_llmchat\access\chat_token;
use local_llmchat\access\jwt;

/**
 * Tests for {@see chat_token}.
 *
 * @covers \local_llmchat\access\chat_token
 */
final class chat_token_test extends \advanced_testcase {
    /** @var \stdClass */
    private $course;
    /** @var \stdClass */
    private $user;
    /** @var int */
    private $cmid;

    protected function setUp(): void {
        parent::setUp();
        $this->resetAfterTest();
        secret::set('test-secret');
        set_config('siteid', 'site-7', 'local_llmchat');
        set_config('user_tokens_per_day', 1234, 'local_llmchat');
        $gen = $this->getDataGenerator();
        $this->course = $gen->create_course();
        $this->user = $gen->create_user(['firstname' => 'Erika', 'lastname' => 'Muster', 'lang' => 'en',
            'email' => 'erika@example.org', 'idnumber' => 'X-99', 'phone1' => '0123']);
        $gen->enrol_user($this->user->id, $this->course->id, 'student');
        $this->cmid = (int)$gen->create_module('page', ['course' => $this->course->id])->cmid;
    }

    public function test_issued_token_verifies_and_carries_all_claims(): void {
        global $CFG;
        $now = 1900000000;
        $issued = chat_token::issue((int)$this->user->id, null, $now);
        $claims = jwt::decode($issued['token'], secret::chat_key(), $CFG->wwwroot, $now + 1);

        $this->assertSame($CFG->wwwroot, $claims['iss']);
        $this->assertSame((string)$this->user->id, $claims['sub']);
        $this->assertArrayNotHasKey('site_id', $claims);
        $this->assertSame('site-7', jwt::header($issued['token'])['kid']);
        $this->assertSame('Erika Muster', $claims['name']);
        $this->assertSame('en', $claims['lang']);
        $this->assertSame(['read', 'interact'], $claims['caps']);
        $this->assertSame([(int)$this->course->id], $claims['courses']);
        $this->assertSame([(string)$this->course->id => ['student']], $claims['roles']);
        $this->assertSame([$this->cmid], $claims['cmids']);
        $this->assertSame(['day' => 1234, 'month' => null, 'exempt' => false], $claims['quota']);
        $this->assertMatchesRegularExpression('/^[0-9a-f]{32}$/', $claims['jti']);
        $this->assertSame($now, $claims['iat']);
        $this->assertLessThanOrEqual(jwt::MAX_TTL, $claims['exp'] - $claims['iat']);
        $this->assertSame($claims['exp'], $issued['expires']);
    }

    public function test_jti_is_unique_per_token(): void {
        $a = chat_token::issue((int)$this->user->id);
        $b = chat_token::issue((int)$this->user->id);
        $this->assertNotSame($a['claims']['jti'], $b['claims']['jti']);
    }

    public function test_no_profile_pii_in_token(): void {
        $issued = chat_token::issue((int)$this->user->id);
        $payload = jwt::base64url_decode(explode('.', $issued['token'])[1]);
        $this->assertStringNotContainsString('erika@example.org', $payload);
        $this->assertStringNotContainsString('X-99', $payload);
        $this->assertStringNotContainsString('0123', $payload);
        $this->assertSame(
            ['iss', 'sub', 'name', 'lang', 'caps', 'courses', 'roles', 'cmids', 'quota', 'jti', 'iat', 'exp'],
            array_keys($issued['claims'])
        );
    }

    public function test_empty_roles_encode_as_json_object(): void {
        $other = $this->getDataGenerator()->create_user();
        $issued = chat_token::issue((int)$other->id);
        $payload = jwt::base64url_decode(explode('.', $issued['token'])[1]);
        $this->assertStringContainsString('"roles":{}', $payload);
        $this->assertStringContainsString('"cmids":[]', $payload);
    }

    public function test_scope_change_lands_in_next_token(): void {
        $first = chat_token::issue((int)$this->user->id);
        set_coursemodule_visible($this->cmid, 0);
        $second = chat_token::issue((int)$this->user->id);
        $this->assertSame([$this->cmid], $first['claims']['cmids']);
        $this->assertSame([], $second['claims']['cmids']);
    }

    public function test_course_claim_only_when_opened_from_a_course(): void {
        $this->assertArrayNotHasKey('course', chat_token::issue((int)$this->user->id)['claims']);
        $issued = chat_token::issue((int)$this->user->id, (int)$this->course->id);
        $this->assertSame((int)$this->course->id, $issued['claims']['course']);
        $this->assertSame('course', array_key_last($issued['claims']));
    }

    public function test_lifetime_stays_within_api_limit(): void {
        $issued = chat_token::issue((int)$this->user->id);
        $this->assertLessThanOrEqual(900, $issued['claims']['exp'] - $issued['claims']['iat']);
    }

    public function test_requires_configured_secret(): void {
        set_config('secret', '', 'local_llmchat');
        $this->expectException(\moodle_exception::class);
        chat_token::issue((int)$this->user->id);
    }
}
