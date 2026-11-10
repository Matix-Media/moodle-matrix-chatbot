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
 * Issues browser chat tokens.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat\access;

use local_llmchat\api_client;
use local_llmchat\quota\policy;
use local_llmchat\secret;

/**
 * Builds the claims of a short-lived chat token for one user and signs them (A0).
 *
 * Only non-sensitive profile fields are included (no email, idnumber, phone).
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
final class chat_token {
    /** Token lifetime in seconds. */
    public const TTL = jwt::MAX_TTL;

    /** Capabilities granted by local/llmchat:use. Shared enum with the API. */
    public const CAPS = ['read', 'interact'];

    /**
     * Issue a token.
     *
     * @param int $userid
     * @param int|null $contextcourseid course the chat was opened from
     * @param int|null $now
     * @return array{token: string, expires: int, claims: array}
     */
    public static function issue(int $userid, ?int $contextcourseid = null, ?int $now = null): array {
        global $CFG;
        $key = secret::chat_key();
        $now = $now ?? time();
        $user = check_access::require_active_user($userid);
        $scope = (new scope_builder())->build($userid, $contextcourseid);
        $roles = $scope->roles_claim();
        $claims = [
            'iss' => $CFG->wwwroot,
            'sub' => (string)$userid,
            'site_id' => self::site_id(),
            'name' => fullname($user),
            'lang' => $user->lang ?: ($CFG->lang ?? 'en'),
            'caps' => self::CAPS,
            'courses' => $scope->courses,
            'roles' => $roles ?: new \stdClass(),
            'cmids' => $scope->cmids,
            'quota' => policy::effective_for_user($userid),
            'jti' => bin2hex(random_bytes(16)),
            'iat' => $now,
            'exp' => $now + self::TTL,
        ];
        $token = jwt::encode($claims, $key);
        $claims['roles'] = $roles;
        return ['token' => $token, 'expires' => $claims['exp'], 'claims' => $claims];
    }

    /**
     * The API's site id for this Moodle, learned from GET /ingest/status and cached.
     *
     * @return string|int|null
     */
    public static function site_id() {
        $cached = get_config('local_llmchat', 'siteid');
        if ($cached !== false && $cached !== '') {
            return ctype_digit((string)$cached) ? (int)$cached : $cached;
        }
        try {
            $status = api_client::from_config()->status();
        } catch (\Throwable $e) {
            return null;
        }
        if (isset($status['site_id'])) {
            set_config('siteid', (string)$status['site_id'], 'local_llmchat');
            return $status['site_id'];
        }
        return null;
    }
}
