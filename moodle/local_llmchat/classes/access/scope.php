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
 * Computed access scope of one user.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat\access;

/**
 * What one user may see: courses, visible cmids, role archetypes and group names per course.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
final class scope {
    /**
     * Constructor.
     *
     * @param int[] $courses sorted course ids
     * @param int[] $cmids sorted visible cmids
     * @param array $roles course id => list of role archetypes
     * @param array $groups course id => list of group names
     * @param array $coursenames course id => ['fullname' => ..., 'shortname' => ...]
     */
    public function __construct(
        /** @var int[] sorted course ids */
        public readonly array $courses,
        /** @var int[] sorted visible cmids */
        public readonly array $cmids,
        /** @var array course id => role archetypes */
        public readonly array $roles,
        /** @var array course id => group names */
        public readonly array $groups,
        /** @var array course id => names */
        public readonly array $coursenames = [],
    ) {
    }

    /**
     * Roles as a JSON object (string keys), as carried in the chat token.
     *
     * @return array
     */
    public function roles_claim(): array {
        $out = [];
        foreach ($this->roles as $courseid => $roles) {
            $out[(string)$courseid] = array_values($roles);
        }
        return $out;
    }
}
