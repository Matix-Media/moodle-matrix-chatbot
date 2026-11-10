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
 * API call failure.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat;

/**
 * A failed call to the chat API. Messages never contain the secret.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class api_exception extends \moodle_exception {
    /** @var int HTTP status, 0 = never reached the server / not configured */
    public int $status;

    /**
     * Constructor.
     *
     * @param string $errorcode lang string key
     * @param int $status
     * @param string $debuginfo
     * @param mixed $a lang string parameter
     */
    public function __construct(string $errorcode, int $status = 0, string $debuginfo = '', $a = null) {
        $this->status = $status;
        parent::__construct($errorcode, 'local_llmchat', '', $a, $debuginfo);
    }

    /**
     * Whether the API rejected our secret (revoked/rotated): the outbox pauses on this.
     *
     * @return bool
     */
    public function is_auth_failure(): bool {
        return $this->status === 401 || $this->status === 403;
    }
}
