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
 * Tests for the site secret store and chat-key derivation.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat;

/**
 * Tests for {@see secret}.
 *
 * @covers \local_llmchat\secret
 */
final class secret_test extends \advanced_testcase {
    /**
     * The shared test vectors.
     *
     * @return array
     */
    private static function vectors(): array {
        return json_decode(file_get_contents(__DIR__ . '/fixtures/jwt_vectors.json'), true)['cases'];
    }

    public function test_unconfigured_secret_is_null(): void {
        $this->resetAfterTest();
        $this->assertNull(secret::get());
        $this->assertFalse(secret::is_configured());
    }

    public function test_secret_is_encrypted_at_rest_and_round_trips(): void {
        $this->resetAfterTest();
        secret::set('  top-secret-value  ');
        $stored = get_config('local_llmchat', 'secret');
        $this->assertNotEmpty($stored);
        $this->assertStringNotContainsString('top-secret-value', $stored);
        $this->assertSame('top-secret-value', secret::get());
        $this->assertTrue(secret::is_configured());
    }

    public function test_admin_setting_and_secret_class_share_storage(): void {
        global $CFG;
        require_once($CFG->libdir . '/adminlib.php');
        $this->resetAfterTest();
        $setting = new \admin_setting_encryptedpassword('local_llmchat/secret', 'x', 'y');
        $setting->write_setting('from-admin-form');
        $this->assertSame('from-admin-form', secret::get());
    }

    public function test_chat_key_matches_shared_vectors(): void {
        foreach (self::vectors() as $vector) {
            $this->assertSame($vector['derived_key_hex'], bin2hex(secret::derive_chat_key($vector['secret'])));
        }
    }

    public function test_chat_key_differs_from_raw_secret(): void {
        $key = secret::derive_chat_key('abc');
        $this->assertSame(32, strlen($key));
        $this->assertNotSame('abc', $key);
    }

    public function test_chat_key_requires_configured_secret(): void {
        $this->resetAfterTest();
        $this->expectException(\moodle_exception::class);
        secret::chat_key();
    }

    public function test_matches_compares_presented_secret(): void {
        $this->resetAfterTest();
        $this->assertFalse(secret::matches('anything'), 'unconfigured never matches');
        secret::set('s3cr3t');
        $this->assertTrue(secret::matches('s3cr3t'));
        $this->assertFalse(secret::matches('s3cr3T'));
        $this->assertFalse(secret::matches(''));
    }
}
