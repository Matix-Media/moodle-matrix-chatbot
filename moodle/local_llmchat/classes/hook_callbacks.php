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
 * Hook callbacks.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat;

/**
 * Hook callbacks.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
final class hook_callbacks {
    /**
     * Add "Chat" to the user menu.
     *
     * @param \core_user\hook\extend_user_menu $hook
     */
    public static function extend_user_menu(\core_user\hook\extend_user_menu $hook): void {
        if (during_initial_install() || !isloggedin() || isguestuser() || !api_client::is_configured()) {
            return;
        }
        if (!has_capability('local/llmchat:use', \context_system::instance())) {
            return;
        }
        $item = new \stdClass();
        $item->itemtype = 'link';
        $item->url = new \moodle_url('/local/llmchat/chat.php');
        $item->title = get_string('chat', 'local_llmchat');
        $item->titleidentifier = 'chat,local_llmchat';
        $hook->add_navitem($item);
    }
}
