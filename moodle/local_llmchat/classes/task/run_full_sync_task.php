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
 * Adhoc full sync (admin button or continuation).
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat\task;

/**
 * Adhoc full sync: queued by "Run full import now" (restart) or as the continuation of a
 * partial nightly run.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class run_full_sync_task extends \core\task\adhoc_task {
    /**
     * Create the task.
     *
     * @param bool $restart abandon the running run first
     * @return self
     */
    public static function create(bool $restart): self {
        $task = new self();
        $task->set_custom_data(['restart' => $restart]);
        return $task;
    }

    /**
     * Run.
     */
    public function execute() {
        $data = $this->get_custom_data();
        full_sync_task::run_slice(!empty($data->restart));
    }
}
