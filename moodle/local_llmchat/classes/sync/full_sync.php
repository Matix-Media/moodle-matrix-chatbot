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
 * Manifest-based, resumable full sync.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat\sync;

use local_llmchat\api_client;
use local_llmchat\content\collector;
use local_llmchat\quota\policy;

/**
 * Full sync (A0b): one manifest chunk per course; the API answers which documents and blobs it
 * needs; only those are pushed. Progress is persisted after every course so a cron or Moodle
 * restart resumes at the next course with the same sync id. A run idle for longer than the
 * API's expiry is abandoned (never finished), so the API never tombstones from a stale run.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class full_sync extends pusher {
    /** Progress table. */
    public const TABLE = 'local_llmchat_sync';
    /** A running run older than this is abandoned (the API expires runs after 24 h). */
    public const STALE_AFTER = 23 * HOURSECS;

    /** @var collector */
    private collector $collector;

    /**
     * Constructor.
     *
     * @param api_client $client
     * @param collector|null $collector
     */
    public function __construct(api_client $client, ?collector $collector = null) {
        parent::__construct($client);
        $this->collector = $collector ?? new collector();
    }

    /**
     * Most recent run record.
     *
     * @return \stdClass|null
     */
    public static function latest(): ?\stdClass {
        global $DB;
        $rows = $DB->get_records(self::TABLE, null, 'id DESC', '*', 0, 1);
        return $rows ? reset($rows) : null;
    }

    /**
     * The resumable running run, abandoning stale ones (or all of them when restarting).
     *
     * @param bool $restart
     * @return \stdClass|null
     */
    private function resumable(bool $restart): ?\stdClass {
        global $DB;
        $resume = null;
        foreach ($DB->get_records(self::TABLE, ['state' => 'running'], 'id DESC') as $run) {
            if (!$restart && !$resume && $run->timemodified > time() - self::STALE_AFTER) {
                $resume = $run;
                continue;
            }
            $DB->update_record(self::TABLE, (object)['id' => $run->id, 'state' => 'abandoned', 'timemodified' => time()]);
        }
        return $resume;
    }

    /**
     * Open a new run on the API and record it.
     *
     * @return \stdClass
     */
    private function open(): \stdClass {
        global $DB;
        $syncid = \core\uuid::generate();
        $this->client->sync_start($syncid, 'full');
        // Policy push on every full sync, so a lost settings-save push self-heals.
        $this->client->put_policy(policy::site_policy());
        $now = time();
        $run = (object)[
            'syncid' => $syncid,
            'mode' => 'full',
            'state' => 'running',
            'courseids' => json_encode($this->collector->course_ids()),
            'chunkno' => 0,
            'lastcourseid' => 0,
            'documents' => 0,
            'blobs' => 0,
            'timestarted' => $now,
            'timemodified' => $now,
        ];
        $run->id = $DB->insert_record(self::TABLE, $run);
        return $run;
    }

    /**
     * Run (or resume) a full sync.
     *
     * @param int|null $deadline stop before starting a course after this time
     * @param bool $restart abandon any running run and start over
     * @return string finished|partial
     * @throws \local_llmchat\api_exception progress is kept; the next run resumes
     */
    public function run(?int $deadline = null, bool $restart = false): string {
        global $DB;
        $run = $this->resumable($restart) ?? $this->open();
        $courseids = json_decode($run->courseids, true) ?: [];
        $of = count($courseids);

        for ($i = (int)$run->chunkno; $i < $of; $i++) {
            if ($deadline !== null && time() > $deadline) {
                return 'partial';
            }
            $courseid = (int)$courseids[$i];
            try {
                $stats = $this->sync_course($run->syncid, $courseid, $i + 1, $of);
            } catch (\Throwable $e) {
                $DB->update_record(self::TABLE, (object)[
                    'id' => $run->id,
                    'lasterror' => \core_text::substr($e->getMessage() .
                        (($e instanceof \moodle_exception && $e->debuginfo) ? ' — ' . $e->debuginfo : ''), 0, 1000),
                    'timemodified' => time(),
                ]);
                throw $e;
            }
            $run->chunkno = $i + 1;
            $run->documents += $stats['documents'];
            $run->blobs += $stats['blobs'];
            $DB->update_record(self::TABLE, (object)[
                'id' => $run->id,
                'chunkno' => $run->chunkno,
                'lastcourseid' => $courseid,
                'documents' => $run->documents,
                'blobs' => $run->blobs,
                'lasterror' => null,
                'timemodified' => time(),
            ]);
        }

        $this->client->sync_finish($run->syncid);
        $DB->update_record(self::TABLE, (object)[
            'id' => $run->id, 'state' => 'finished', 'timefinished' => time(), 'timemodified' => time(),
        ]);
        return 'finished';
    }

    /**
     * Push one course chunk.
     *
     * @param string $syncid
     * @param int $courseid
     * @param int $chunkno
     * @param int $of
     * @return array{documents: int, blobs: int}
     */
    private function sync_course(string $syncid, int $courseid, int $chunkno, int $of): array {
        $bundle = $this->collector->collect_course($courseid);
        $documents = $bundle['documents'] ?? [];
        if ($bundle) {
            $this->client->push_courses([$bundle['course']]);
        }
        $answer = $this->client->push_manifest(
            $syncid,
            $chunkno,
            $of,
            array_map(fn($d) => $d->manifest_entry(), $documents)
        );
        $need = array_fill_keys($answer['need'] ?? [], true);
        $needed = array_values(array_filter($documents, fn($d) => isset($need[$d->id()])));
        $needblobs = $answer['need_blobs'] ?? [];
        if ($needed) {
            return $this->push_documents($needed, $needblobs);
        }
        return ['documents' => 0, 'blobs' => $this->upload_blobs($needblobs, $documents)];
    }
}
