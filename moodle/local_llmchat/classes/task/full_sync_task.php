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
 * Nightly full sync.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat\task;

use local_llmchat\api_client;
use local_llmchat\sync\full_sync;

/**
 * Nightly full sync (also reconciliation for anything the outbox missed). Runs for at most
 * syncmaxruntime seconds; a partial run queues an adhoc continuation.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class full_sync_task extends \core\task\scheduled_task {
    /** Default maximum runtime per invocation, seconds. */
    public const DEFAULT_MAXRUNTIME = 1800;

    /**
     * Name.
     *
     * @return string
     */
    public function get_name() {
        return get_string('task:fullsync', 'local_llmchat');
    }

    /**
     * Run one bounded slice of the sync and queue a continuation if needed.
     *
     * @param bool $restart
     */
    public static function run_slice(bool $restart = false): void {
        if (!api_client::is_configured() || !get_config('local_llmchat', 'syncenabled')) {
            mtrace('local_llmchat: not configured or sync disabled');
            return;
        }
        $lock = \core\lock\lock_config::get_lock_factory('local_llmchat')->get_lock('full_sync', 0);
        if (!$lock) {
            mtrace('local_llmchat: full sync already running');
            return;
        }
        try {
            $max = get_config('local_llmchat', 'syncmaxruntime');
            $max = ($max === false || $max === '') ? self::DEFAULT_MAXRUNTIME : (int)$max;
            $state = (new full_sync(api_client::from_config()))->run(time() + $max, $restart);
            mtrace("local_llmchat: full sync $state");
            if ($state === 'partial') {
                \core\task\manager::queue_adhoc_task(run_full_sync_task::create(false), true);
            }
        } finally {
            $lock->release();
        }
    }

    /**
     * Run.
     */
    public function execute() {
        self::run_slice(false);
    }
}
