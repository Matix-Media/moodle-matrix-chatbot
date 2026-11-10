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
 * Outbox of pending content changes.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat\sync;

use local_llmchat\api_client;
use local_llmchat\api_exception;

/**
 * Persistent outbox (A0b): observers append, the drain task pushes with per-row exponential
 * backoff (1 min doubling, capped at 6 h). Rows that exhausted maxattempts stay visible on the
 * status page. An authentication failure (revoked/rotated secret) pauses the whole drain
 * without burning attempts.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
final class outbox {
    /** Table name. */
    public const TABLE = 'local_llmchat_outbox';
    /** Upsert action. */
    public const UPSERT = 'upsert';
    /** Delete action. */
    public const DELETE = 'delete';
    /** Default maximum attempts per row. */
    public const DEFAULT_MAXATTEMPTS = 12;
    /** Backoff cap in seconds. */
    public const MAX_BACKOFF = 6 * HOURSECS;

    /**
     * Add or replace the pending row for (course, cm). cmid 0 means the whole course; a course
     * delete collapses every pending row of that course into one.
     *
     * @param int $courseid
     * @param int $cmid
     * @param string $action
     * @param int|null $now
     */
    public static function enqueue(int $courseid, int $cmid, string $action, ?int $now = null): void {
        global $DB;
        $now = $now ?? time();
        if ($cmid === 0 && $action === self::DELETE) {
            $DB->delete_records_select(self::TABLE, 'courseid = :c AND cmid <> 0', ['c' => $courseid]);
        }
        $fields = [
            'action' => $action,
            'timeenqueued' => $now,
            'attempts' => 0,
            'nextattempt' => $now,
            'lasterror' => null,
        ];
        $existing = $DB->get_record(self::TABLE, ['courseid' => $courseid, 'cmid' => $cmid]);
        if ($existing) {
            $DB->update_record(self::TABLE, (object)(['id' => $existing->id] + $fields));
            return;
        }
        try {
            $DB->insert_record(self::TABLE, (object)(['courseid' => $courseid, 'cmid' => $cmid] + $fields));
        } catch (\dml_write_exception $e) {
            // Lost a race with a concurrent insert of the same (course, cm): replace it.
            $existing = $DB->get_record(self::TABLE, ['courseid' => $courseid, 'cmid' => $cmid], '*', MUST_EXIST);
            $DB->update_record(self::TABLE, (object)(['id' => $existing->id] + $fields));
        }
    }

    /**
     * Configured maximum attempts.
     *
     * @return int
     */
    public static function max_attempts(): int {
        $value = (int)get_config('local_llmchat', 'maxattempts');
        return $value > 0 ? $value : self::DEFAULT_MAXATTEMPTS;
    }

    /**
     * Backoff after the given number of failed attempts.
     *
     * @param int $attempts >= 1
     * @return int seconds
     */
    public static function backoff(int $attempts): int {
        $attempts = max(1, $attempts);
        if ($attempts > 20) {
            return self::MAX_BACKOFF;
        }
        return (int)min(self::MAX_BACKOFF, MINSECS * (2 ** ($attempts - 1)));
    }

    /**
     * Rows due now, ordered by course.
     *
     * @param int $now
     * @param int $limit
     * @return \stdClass[]
     */
    public static function due(int $now, int $limit): array {
        global $DB;
        return array_values($DB->get_records_select(self::TABLE, 'nextattempt <= :now AND attempts < :max',
            ['now' => $now, 'max' => self::max_attempts()], 'courseid, cmid, id', '*', 0, $limit));
    }

    /**
     * Remove a pushed row unless it was re-enqueued meanwhile.
     *
     * @param \stdClass $row
     */
    public static function record_success(\stdClass $row): void {
        global $DB;
        $DB->delete_records(self::TABLE, ['id' => $row->id, 'timeenqueued' => $row->timeenqueued]);
    }

    /**
     * Back off a failed row.
     *
     * @param \stdClass $row
     * @param string $error
     * @param int $now
     */
    public static function record_failure(\stdClass $row, string $error, int $now): void {
        global $DB;
        $attempts = (int)$row->attempts + 1;
        $DB->update_record(self::TABLE, (object)[
            'id' => $row->id,
            'attempts' => $attempts,
            'nextattempt' => $now + self::backoff($attempts),
            'lasterror' => \core_text::substr($error, 0, 1000),
        ]);
    }

    /**
     * Push due rows.
     *
     * @param api_client $client
     * @param int|null $now
     * @param int $limit rows per run
     * @param incremental|null $pusher
     * @return array{pushed: int, failed: int, paused: bool}
     */
    public static function drain(api_client $client, ?int $now = null, int $limit = 200,
            ?incremental $pusher = null): array {
        $now = $now ?? time();
        $pusher = $pusher ?? new incremental($client);
        $pushed = $failed = 0;
        foreach (self::due($now, $limit) as $row) {
            try {
                $pusher->process($row);
            } catch (api_exception $e) {
                if ($e->is_auth_failure()) {
                    set_config('authfailedat', $now, 'local_llmchat');
                    return ['pushed' => $pushed, 'failed' => $failed, 'paused' => true];
                }
                self::record_failure($row, self::describe($e), $now);
                $failed++;
                continue;
            } catch (\moodle_exception | \dml_exception $e) {
                self::record_failure($row, self::describe($e), $now);
                $failed++;
                continue;
            }
            self::record_success($row);
            if (get_config('local_llmchat', 'authfailedat')) {
                unset_config('authfailedat', 'local_llmchat');
            }
            $pushed++;
        }
        return ['pushed' => $pushed, 'failed' => $failed, 'paused' => false];
    }

    /**
     * Error text for the status page.
     *
     * @param \Throwable $e
     * @return string
     */
    private static function describe(\Throwable $e): string {
        $text = $e->getMessage();
        if ($e instanceof \moodle_exception && !empty($e->debuginfo)) {
            $text .= ' — ' . $e->debuginfo;
        }
        return $text;
    }

    /**
     * Counts for the status page.
     *
     * @return array{pending: int, failing: int, exhausted: int}
     */
    public static function counts(): array {
        global $DB;
        $max = self::max_attempts();
        return [
            'pending' => $DB->count_records_select(self::TABLE, 'attempts < :max', ['max' => $max]),
            'failing' => $DB->count_records_select(self::TABLE, 'attempts > 0 AND attempts < :max', ['max' => $max]),
            'exhausted' => $DB->count_records_select(self::TABLE, 'attempts >= :max', ['max' => $max]),
        ];
    }

    /**
     * Rows with errors, for the status page.
     *
     * @param int $limit
     * @return \stdClass[]
     */
    public static function failed_rows(int $limit = 50): array {
        global $DB;
        return array_values($DB->get_records_select(self::TABLE, 'attempts > 0', null,
            'attempts DESC, courseid, cmid', '*', 0, $limit));
    }

    /**
     * Give exhausted rows another full set of attempts.
     */
    public static function retry_exhausted(): void {
        global $DB;
        $DB->execute('UPDATE {' . self::TABLE . '} SET attempts = 0, nextattempt = :now WHERE attempts >= :max',
            ['now' => time(), 'max' => self::max_attempts()]);
    }

    /**
     * Remove every row (used by "clear + reimport", since a full sync supersedes the outbox).
     */
    public static function clear(): void {
        global $DB;
        $DB->delete_records(self::TABLE);
    }
}
