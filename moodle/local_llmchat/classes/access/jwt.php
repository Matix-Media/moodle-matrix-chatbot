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
 * Minimal HS256 JWT codec.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat\access;

/**
 * Minimal HS256 JSON Web Token codec (RFC 7519) with a pinned algorithm.
 *
 * Encoding is deterministic (compact JSON, unescaped slashes and unicode, sorted header keys)
 * so the shared test vectors match byte for byte on both sides.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
final class jwt {
    /** Maximum lifetime of a chat token in seconds (10 minutes). */
    public const MAX_TTL = 600;

    /** JSON flags used for every encoded segment. */
    private const JSON_FLAGS = JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR;

    /**
     * Sign claims.
     *
     * @param array $claims
     * @param string $key raw key bytes
     * @param string|int|null $kid key id (the API's site id), placed in the header
     * @return string compact JWS
     */
    public static function encode(array $claims, string $key, $kid = null): string {
        // Header keys are sorted (alg, kid, typ) so tokens match the shared vectors byte for byte.
        $header = ['alg' => 'HS256'] + ($kid !== null ? ['kid' => (string)$kid] : []) + ['typ' => 'JWT'];
        $header = self::base64url_encode(json_encode($header, self::JSON_FLAGS));
        $payload = self::base64url_encode(json_encode($claims, self::JSON_FLAGS));
        $signature = self::base64url_encode(hash_hmac('sha256', "$header.$payload", $key, true));
        return "$header.$payload.$signature";
    }

    /**
     * Verify and decode a token.
     *
     * @param string $token
     * @param string $key raw key bytes
     * @param string $issuer required iss
     * @param int|null $now current time (defaults to time())
     * @param int $leeway seconds of clock skew allowed on exp
     * @return array claims
     * @throws jwt_exception
     */
    public static function decode(string $token, string $key, string $issuer, ?int $now = null, int $leeway = 0): array {
        $now = $now ?? time();
        $parts = explode('.', $token);
        if (count($parts) !== 3) {
            throw new jwt_exception('malformed');
        }
        [$h, $p, $s] = $parts;
        $header = json_decode(self::base64url_decode($h), true);
        if (!is_array($header) || ($header['alg'] ?? null) !== 'HS256') {
            throw new jwt_exception('alg');
        }
        $expected = self::base64url_encode(hash_hmac('sha256', "$h.$p", $key, true));
        if (!hash_equals($expected, $s)) {
            throw new jwt_exception('signature');
        }
        $claims = json_decode(self::base64url_decode($p), true);
        if (!is_array($claims)) {
            throw new jwt_exception('payload');
        }
        if (!isset($claims['exp']) || !is_int($claims['exp']) || $claims['exp'] + $leeway <= $now) {
            throw new jwt_exception('expired');
        }
        if (($claims['iss'] ?? null) !== $issuer) {
            throw new jwt_exception('issuer');
        }
        return $claims;
    }

    /**
     * Decoded header of a token (no verification).
     *
     * @param string $token
     * @return array
     */
    public static function header(string $token): array {
        $header = json_decode(self::base64url_decode(explode('.', $token)[0]), true);
        return is_array($header) ? $header : [];
    }

    /**
     * Base64url without padding.
     *
     * @param string $data
     * @return string
     */
    public static function base64url_encode(string $data): string {
        return rtrim(strtr(base64_encode($data), '+/', '-_'), '=');
    }

    /**
     * Decode base64url (padding optional).
     *
     * @param string $data
     * @return string
     */
    public static function base64url_decode(string $data): string {
        $decoded = base64_decode(strtr($data, '-_', '+/'), true);
        return $decoded === false ? '' : $decoded;
    }
}
