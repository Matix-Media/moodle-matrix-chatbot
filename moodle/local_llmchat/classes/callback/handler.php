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
 * Logic of the bearer-protected callback endpoint.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat\callback;

use local_llmchat\access\check_access;
use local_llmchat\access\scope_builder;
use local_llmchat\access\user_unavailable_exception;
use local_llmchat\secret;

/**
 * API → Moodle callbacks (A0): `ping`, `check_access`, `resolve_user`. Authenticated by the
 * site secret as a bearer token (constant-time compare). Responses never contain the secret
 * or profile PII beyond what the agent needs.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class handler {
    /** Max cmids per check_access call. */
    public const MAX_CMIDS = 5000;

    /**
     * Handle one request.
     *
     * @param string $method
     * @param string|null $authorization raw Authorization header
     * @param string $rawbody
     * @return array{status: int, body: array}
     */
    public function handle(string $method, ?string $authorization, string $rawbody): array {
        if (strtoupper($method) !== 'POST') {
            return self::out(405, ['error' => 'method_not_allowed']);
        }
        if (!$authorization || !preg_match('/^Bearer\s+(\S+)\s*$/i', $authorization, $m) || !secret::matches($m[1])) {
            return self::out(401, ['error' => 'unauthorized']);
        }
        $body = json_decode($rawbody, true);
        if (!is_array($body) || !isset($body['action']) || !is_string($body['action'])) {
            return self::out(400, ['error' => 'bad_request']);
        }
        try {
            switch ($body['action']) {
                case 'ping':
                    return self::out(200, $this->ping());
                case 'check_access':
                    $userid = self::int_param($body, 'user_id');
                    $cmids = $body['cmids'] ?? null;
                    if ($userid === null || !is_array($cmids) || count($cmids) > self::MAX_CMIDS
                            || array_filter($cmids, fn($c) => !is_int($c))) {
                        return self::out(400, ['error' => 'bad_request']);
                    }
                    return self::out(200, ['allowed' => (new check_access())->check($userid, $cmids)]);
                case 'resolve_user':
                    $userid = self::int_param($body, 'user_id');
                    if ($userid === null) {
                        return self::out(400, ['error' => 'bad_request']);
                    }
                    return self::out(200, $this->resolve_user($userid));
                default:
                    return self::out(400, ['error' => 'unknown_action']);
            }
        } catch (user_unavailable_exception $e) {
            return self::out(403, ['error' => 'user_unavailable']);
        }
    }

    /**
     * Integer parameter or null.
     *
     * @param array $body
     * @param string $name
     * @return int|null
     */
    private static function int_param(array $body, string $name): ?int {
        return isset($body[$name]) && is_int($body[$name]) && $body[$name] > 0 ? $body[$name] : null;
    }

    /**
     * Response helper.
     *
     * @param int $status
     * @param array $body
     * @return array
     */
    private static function out(int $status, array $body): array {
        return ['status' => $status, 'body' => $body];
    }

    /**
     * Health check.
     *
     * @return array
     */
    private function ping(): array {
        global $CFG;
        return ['ok' => true, 'site_url' => $CFG->wwwroot, 'plugin_version' => (int)get_config('local_llmchat', 'version')];
    }

    /**
     * Profile details for the agent's current_user tool.
     *
     * @param int $userid
     * @return array
     */
    private function resolve_user(int $userid): array {
        global $CFG;
        $user = check_access::require_active_user($userid);
        $scope = (new scope_builder())->build($userid);
        $courses = [];
        foreach ($scope->courses as $courseid) {
            $courses[] = [
                'course_id' => $courseid,
                'fullname' => $scope->coursenames[$courseid]['fullname'] ?? '',
                'shortname' => $scope->coursenames[$courseid]['shortname'] ?? '',
                'roles' => $scope->roles[$courseid] ?? [],
                'groups' => $scope->groups[$courseid] ?? [],
            ];
        }
        return [
            'user_id' => (int)$user->id,
            'fullname' => fullname($user),
            'lang' => $user->lang ?: ($CFG->lang ?? 'en'),
            'courses' => $courses,
        ];
    }
}
