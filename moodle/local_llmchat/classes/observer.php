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
 * Event observers writing to the outbox.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat;

use local_llmchat\access\scope_builder;
use local_llmchat\sync\outbox;

/**
 * Event observers. They only ever write to the outbox — never call the network.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
final class observer {
    /**
     * Whether changes for this course should be recorded.
     *
     * @param int $courseid
     * @return bool
     */
    private static function enabled(int $courseid): bool {
        return $courseid > 0 && (bool)get_config('local_llmchat', 'syncenabled') && scope_builder::course_included($courseid);
    }

    /**
     * A module (or something inside it, such as a book chapter) changed.
     *
     * @param \core\event\base $event
     */
    public static function course_module_changed(\core\event\base $event): void {
        $cmid = $event->contextlevel == CONTEXT_MODULE ? (int)$event->contextinstanceid : (int)$event->objectid;
        if (self::enabled((int)$event->courseid) && $cmid) {
            outbox::enqueue((int)$event->courseid, $cmid, outbox::UPSERT);
        }
    }

    /**
     * A module was deleted.
     *
     * @param \core\event\course_module_deleted $event
     */
    public static function course_module_deleted(\core\event\course_module_deleted $event): void {
        if (self::enabled((int)$event->courseid)) {
            outbox::enqueue((int)$event->courseid, (int)$event->objectid, outbox::DELETE);
        }
    }

    /**
     * Course metadata or sections changed: re-push the whole course (header paths change).
     *
     * @param \core\event\base $event
     */
    public static function course_changed(\core\event\base $event): void {
        if (self::enabled((int)$event->courseid)) {
            outbox::enqueue((int)$event->courseid, 0, outbox::UPSERT);
        }
    }

    /**
     * A course was deleted.
     *
     * @param \core\event\course_deleted $event
     */
    public static function course_deleted(\core\event\course_deleted $event): void {
        if (self::enabled((int)$event->objectid)) {
            outbox::enqueue((int)$event->objectid, 0, outbox::DELETE);
        }
    }
}
