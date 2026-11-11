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
 * Scheduled task draining the outbox.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat\task;

use local_llmchat\api_client;
use local_llmchat\sync\outbox;

/**
 * Pushes due outbox rows every minute, protected by a lock.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class drain_outbox_task extends \core\task\scheduled_task {
    /**
     * Name.
     *
     * @return string
     */
    public function get_name() {
        return get_string('task:drainoutbox', 'local_llmchat');
    }

    /**
     * Run.
     */
    public function execute() {
        if (!api_client::is_configured() || !get_config('local_llmchat', 'syncenabled')) {
            return;
        }
        $factory = \core\lock\lock_config::get_lock_factory('local_llmchat');
        $lock = $factory->get_lock('drain_outbox', 0);
        if (!$lock) {
            mtrace('local_llmchat: outbox drain already running');
            return;
        }
        try {
            $result = outbox::drain(api_client::from_config());
            mtrace("local_llmchat: pushed {$result['pushed']}, failed {$result['failed']}" .
                ($result['paused'] ? ', paused (API rejected the secret)' : ''));
        } finally {
            $lock->release();
        }
    }
}
