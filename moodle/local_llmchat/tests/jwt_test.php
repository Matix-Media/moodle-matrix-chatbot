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
 * Tests for the HS256 chat-token codec.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat;

use local_llmchat\access\jwt;
use local_llmchat\access\jwt_exception;

/**
 * Tests for {@see jwt}.
 *
 * @covers \local_llmchat\access\jwt
 */
final class jwt_test extends \basic_testcase {
    /**
     * The shared test vectors.
     *
     * @return array
     */
    private static function vectors(): array {
        return json_decode(file_get_contents(__DIR__ . '/fixtures/jwt_vectors.json'), true)['vectors'];
    }

    public function test_encode_matches_shared_vectors_byte_for_byte(): void {
        // Decode to objects so empty JSON objects ({} for roles) survive the round trip.
        $vectors = json_decode(file_get_contents(__DIR__ . '/fixtures/jwt_vectors.json'))->vectors;
        foreach ($vectors as $vector) {
            $key = secret::derive_chat_key($vector->secret);
            $this->assertSame($vector->token, jwt::encode((array)$vector->claims, $key));
        }
    }

    public function test_decode_accepts_shared_vectors(): void {
        foreach (self::vectors() as $vector) {
            $key = secret::derive_chat_key($vector['secret']);
            $claims = jwt::decode($vector['token'], $key, $vector['claims']['iss'], $vector['claims']['iat'] + 1);
            $this->assertSame($vector['claims'], $claims);
        }
    }

    /**
     * A small valid token for the negative tests.
     *
     * @param array $override
     * @return array [token, key]
     */
    private function sample(array $override = []): array {
        $key = secret::derive_chat_key('secret');
        $claims = array_merge(['iss' => 'https://m.example', 'sub' => '1', 'iat' => 1000, 'exp' => 1600], $override);
        return [jwt::encode($claims, $key), $key];
    }

    public function test_header_pins_hs256(): void {
        [$token] = $this->sample();
        $header = json_decode(jwt::base64url_decode(explode('.', $token)[0]), true);
        $this->assertSame(['alg' => 'HS256', 'typ' => 'JWT'], $header);
    }

    public function test_rejects_wrong_key(): void {
        [$token] = $this->sample();
        $this->expectException(jwt_exception::class);
        jwt::decode($token, secret::derive_chat_key('other'), 'https://m.example', 1001);
    }

    public function test_rejects_alg_none(): void {
        [$token, $key] = $this->sample();
        [, $payload] = explode('.', $token);
        $forged = jwt::base64url_encode('{"alg":"none","typ":"JWT"}') . '.' . $payload . '.';
        $this->expectException(jwt_exception::class);
        jwt::decode($forged, $key, 'https://m.example', 1001);
    }

    public function test_rejects_expired(): void {
        [$token, $key] = $this->sample();
        $this->expectException(jwt_exception::class);
        jwt::decode($token, $key, 'https://m.example', 1600);
    }

    public function test_rejects_issuer_mismatch(): void {
        [$token, $key] = $this->sample();
        $this->expectException(jwt_exception::class);
        jwt::decode($token, $key, 'https://evil.example', 1001);
    }

    public function test_rejects_tampered_payload(): void {
        [$token, $key] = $this->sample();
        [$h, , $s] = explode('.', $token);
        $p = jwt::base64url_encode('{"iss":"https://m.example","sub":"2","iat":1000,"exp":1600}');
        $this->expectException(jwt_exception::class);
        jwt::decode("$h.$p.$s", $key, 'https://m.example', 1001);
    }

    public function test_rejects_malformed(): void {
        $this->expectException(jwt_exception::class);
        jwt::decode('not-a-token', 'k', 'https://m.example', 1);
    }

    public function test_encode_keeps_slashes_and_unicode_unescaped(): void {
        $key = secret::derive_chat_key('x');
        $token = jwt::encode(['iss' => 'https://a/b', 'name' => 'Jürgen'], $key);
        $payload = jwt::base64url_decode(explode('.', $token)[1]);
        $this->assertSame('{"iss":"https://a/b","name":"Jürgen"}', $payload);
    }
}
