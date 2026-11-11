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
 * Live access check used by the callback endpoint.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat\access;

/**
 * (userid, cmids) → allowed cmids, refusing missing, deleted and suspended users.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class check_access {
    /** @var scope_builder */
    private scope_builder $builder;

    /**
     * Constructor.
     *
     * @param scope_builder|null $builder
     */
    public function __construct(?scope_builder $builder = null) {
        $this->builder = $builder ?? new scope_builder();
    }

    /**
     * Load an active user or fail.
     *
     * @param int $userid
     * @return \stdClass
     * @throws user_unavailable_exception
     */
    public static function require_active_user(int $userid): \stdClass {
        $user = \core_user::get_user($userid);
        if (!$user || !empty($user->deleted) || !empty($user->suspended) || isguestuser($user)) {
            throw new user_unavailable_exception();
        }
        return $user;
    }

    /**
     * Allowed subset of cmids.
     *
     * @param int $userid
     * @param int[] $cmids
     * @return int[]
     * @throws user_unavailable_exception
     */
    public function check(int $userid, array $cmids): array {
        self::require_active_user($userid);
        return $this->builder->visible_cmids($userid, $cmids);
    }
}
