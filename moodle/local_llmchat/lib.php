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
 * Library callbacks for local_llmchat.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

/**
 * Add "Chat" to the course navigation.
 *
 * @param navigation_node $navigation
 * @param stdClass $course
 * @param context $context
 */
function local_llmchat_extend_navigation_course(navigation_node $navigation, stdClass $course, context $context) {
    if (!local_llmchat_chat_available() || !has_capability('local/llmchat:use', $context)) {
        return;
    }
    $url = new moodle_url('/local/llmchat/chat.php', ['courseid' => $course->id]);
    $navigation->add(get_string('chat', 'local_llmchat'), $url, navigation_node::TYPE_SETTING, null,
        'local_llmchat', new pix_icon('t/message', ''));
}

/**
 * Whether the chat is usable at all (configured API).
 *
 * @return bool
 */
function local_llmchat_chat_available(): bool {
    return \local_llmchat\api_client::is_configured();
}

/**
 * Settings-save callback for quota settings: queue a policy push to the API.
 */
function local_llmchat_policy_updated(): void {
    \local_llmchat\quota\policy::queue_push();
}

/**
 * Settings-save callback for the API URL / secret: forget the cached site id.
 */
function local_llmchat_connection_updated(): void {
    unset_config('siteid', 'local_llmchat');
    unset_config('authfailedat', 'local_llmchat');
    \local_llmchat\quota\policy::queue_push();
}
