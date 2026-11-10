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
 * External function issuing a chat token for the current user.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat\external;

use core_external\external_api;
use core_external\external_function_parameters;
use core_external\external_single_structure;
use core_external\external_value;
use local_llmchat\access\chat_token;
use local_llmchat\api_client;

/**
 * AJAX (logged-in user, sesskey checked by the AJAX framework): a fresh chat token for self.
 * The JS calls this before every send, so scope changes land within one message.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class issue_token extends external_api {
    /**
     * Parameters.
     *
     * @return external_function_parameters
     */
    public static function execute_parameters(): external_function_parameters {
        return new external_function_parameters([
            'courseid' => new external_value(PARAM_INT, 'Course the chat was opened from, 0 for none', VALUE_DEFAULT, 0),
        ]);
    }

    /**
     * Issue the token.
     *
     * @param int $courseid
     * @return array
     */
    public static function execute(int $courseid = 0): array {
        global $USER;
        ['courseid' => $courseid] = self::validate_parameters(self::execute_parameters(), ['courseid' => $courseid]);
        if ($courseid && $courseid != SITEID) {
            $context = \context_course::instance($courseid);
        } else {
            $courseid = 0;
            $context = \context_system::instance();
        }
        self::validate_context($context);
        if (isguestuser() || !isloggedin()) {
            throw new \require_login_exception('guest');
        }
        require_capability('local/llmchat:use', $context);

        $issued = chat_token::issue((int)$USER->id, $courseid ?: null);
        $apiurl = rtrim((string)get_config('local_llmchat', 'apiurl'), '/');
        return [
            'token' => $issued['token'],
            'expires' => $issued['expires'],
            'apiurl' => $apiurl,
            'wsurl' => api_client::ws_url($apiurl),
        ];
    }

    /**
     * Return structure.
     *
     * @return external_single_structure
     */
    public static function execute_returns(): external_single_structure {
        return new external_single_structure([
            'token' => new external_value(PARAM_RAW, 'HS256 chat token'),
            'expires' => new external_value(PARAM_INT, 'Expiry timestamp'),
            'apiurl' => new external_value(PARAM_URL, 'API base URL'),
            'wsurl' => new external_value(PARAM_RAW, 'Websocket URL'),
        ]);
    }
}
