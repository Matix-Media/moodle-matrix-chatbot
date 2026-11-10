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
 * Token quota policy.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat\quota;

/**
 * Site quota policy (plugin settings) and per-user overrides/exemptions (A8b).
 *
 * Settings use 0 for "unlimited"; on the wire unlimited is null. Overrides use NULL for
 * "inherit the site default" and 0 for "unlimited".
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
final class policy {
    /** Override table. */
    public const TABLE = 'local_llmchat_quota_override';

    /**
     * Integer setting, 0/empty → null (unlimited).
     *
     * @param string $name
     * @return int|null
     */
    private static function limit(string $name): ?int {
        $value = (int)get_config('local_llmchat', $name);
        return $value > 0 ? $value : null;
    }

    /**
     * Body for PUT /ingest/policy.
     *
     * @return array
     */
    public static function site_policy(): array {
        return [
            'site_tokens_per_day' => self::limit('site_tokens_per_day'),
            'site_tokens_per_month' => self::limit('site_tokens_per_month'),
            'user_tokens_per_day' => self::limit('user_tokens_per_day'),
            'user_tokens_per_month' => self::limit('user_tokens_per_month'),
            'max_tokens_per_turn' => self::limit('max_tokens_per_turn'),
            'count_reasoning' => (bool)get_config('local_llmchat', 'count_reasoning'),
            'exempt_users_bypass_site' => (bool)get_config('local_llmchat', 'exempt_users_bypass_site'),
            'timezone' => \core_date::get_server_timezone(),
        ];
    }

    /**
     * The user's effective per-user limits, as carried in the chat token's quota claim.
     *
     * @param int $userid
     * @return array{day: int|null, month: int|null, exempt: bool}
     */
    public static function effective_for_user(int $userid): array {
        global $DB;
        $day = self::limit('user_tokens_per_day');
        $month = self::limit('user_tokens_per_month');
        $exempt = has_capability('local/llmchat:unlimited', \context_system::instance(), $userid);
        $override = $DB->get_record(self::TABLE, ['userid' => $userid]);
        if ($override) {
            if ($override->tokensperday !== null) {
                $day = (int)$override->tokensperday > 0 ? (int)$override->tokensperday : null;
            }
            if ($override->tokenspermonth !== null) {
                $month = (int)$override->tokenspermonth > 0 ? (int)$override->tokenspermonth : null;
            }
            $exempt = $exempt || !empty($override->exempt);
        }
        return ['day' => $day, 'month' => $month, 'exempt' => $exempt];
    }

    /**
     * Create or replace a user's override.
     *
     * @param int $userid
     * @param int|null $day null = site default, 0 = unlimited
     * @param int|null $month null = site default, 0 = unlimited
     * @param bool $exempt
     * @param string $note
     */
    public static function save_override(int $userid, ?int $day, ?int $month, bool $exempt, string $note): void {
        global $DB, $USER;
        $now = time();
        $record = $DB->get_record(self::TABLE, ['userid' => $userid]);
        $data = (object)[
            'userid' => $userid,
            'tokensperday' => $day,
            'tokenspermonth' => $month,
            'exempt' => $exempt ? 1 : 0,
            'note' => $note,
            'usermodified' => (int)($USER->id ?? 0),
            'timemodified' => $now,
        ];
        if ($record) {
            $data->id = $record->id;
            $DB->update_record(self::TABLE, $data);
        } else {
            $data->timecreated = $now;
            $DB->insert_record(self::TABLE, $data);
        }
    }

    /**
     * Remove a user's override.
     *
     * @param int $userid
     */
    public static function delete_override(int $userid): void {
        global $DB;
        $DB->delete_records(self::TABLE, ['userid' => $userid]);
    }

    /**
     * All overrides with user names, for the admin page.
     *
     * @return array
     */
    public static function overrides(): array {
        global $DB;
        $fields = \core_user\fields::for_name()->get_sql('u', false, '', '', false)->selects;
        return array_values($DB->get_records_sql(
            "SELECT o.*, $fields
               FROM {" . self::TABLE . "} o
               JOIN {user} u ON u.id = o.userid
           ORDER BY u.lastname, u.firstname"
        ));
    }

    /**
     * Queue a policy push to the API (settings-save callback); deduplicated.
     */
    public static function queue_push(): void {
        $task = new \local_llmchat\task\push_policy_task();
        \core\task\manager::queue_adhoc_task($task, true);
    }
}
