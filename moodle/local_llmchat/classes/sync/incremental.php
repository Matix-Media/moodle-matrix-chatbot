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
 * Pushes one outbox row.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat\sync;

use local_llmchat\content\collector;

/**
 * Pushes one outbox row: an upsert re-collects the cm (or the whole course for cmid 0); a
 * delete, or an upsert of something that no longer exists, sends a prefix delete
 * ("{course}:{cmid}:*" or "{course}:*").
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class incremental extends pusher {
    /** @var collector */
    private collector $collector;

    /**
     * Constructor.
     *
     * @param \local_llmchat\api_client $client
     * @param collector|null $collector
     */
    public function __construct(\local_llmchat\api_client $client, ?collector $collector = null) {
        parent::__construct($client);
        $this->collector = $collector ?? new collector();
    }

    /**
     * The prefix doc id for a row.
     *
     * @param int $courseid
     * @param int $cmid
     * @return string
     */
    public static function prefix(int $courseid, int $cmid): string {
        return $cmid ? "$courseid:$cmid:*" : "$courseid:*";
    }

    /**
     * Process one row; throws on failure (the outbox backs off).
     *
     * @param \stdClass $row
     */
    public function process(\stdClass $row): void {
        $courseid = (int)$row->courseid;
        $cmid = (int)$row->cmid;
        if ($row->action === outbox::UPSERT) {
            $bundle = $cmid ? $this->collector->collect_cm($cmid) : $this->collector->collect_course($courseid);
            if ($bundle !== null) {
                $this->client->push_courses([$bundle['course']]);
                $this->push_documents($bundle['documents']);
                return;
            }
        }
        $this->client->delete_documents([self::prefix($courseid, $cmid)]);
    }
}
