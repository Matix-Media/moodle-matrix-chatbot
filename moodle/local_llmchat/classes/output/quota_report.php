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
 * Quota admin page renderable.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat\output;

use local_llmchat\api_client;
use local_llmchat\api_exception;
use local_llmchat\quota\policy;

/**
 * Data for the "Chat quotas" admin page: overrides table and usage from GET /api/usage.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class quota_report implements \renderable, \templatable {
    /**
     * Constructor.
     *
     * @param api_client|null $client
     */
    public function __construct(
        /** @var api_client|null */
        private ?api_client $client,
    ) {
    }

    /**
     * Format a limit for display.
     *
     * @param mixed $value
     * @param bool $inheritnull null means "site default" (overrides) instead of unlimited
     * @return string
     */
    private static function limit($value, bool $inheritnull = false): string {
        if ($value === null) {
            return $inheritnull ? get_string('quota:sitedefault', 'local_llmchat') : get_string('quota:unlimited', 'local_llmchat');
        }
        return (int)$value === 0 ? get_string('quota:unlimited', 'local_llmchat') : number_format((int)$value, 0, ',', '.');
    }

    /**
     * Usage rows for one period.
     *
     * @param string $period
     * @return array
     */
    private function usage(string $period): array {
        global $DB;
        $out = ['period' => $period, 'rows' => [], 'error' => '', 'sitetotal' => '–'];
        if (!$this->client) {
            return $out;
        }
        try {
            $data = $this->client->usage($period, 'user');
        } catch (api_exception $e) {
            $out['error'] = $e->getMessage();
            return $out;
        }
        $rows = $data['rows'] ?? [];
        $userids = array_filter(array_map(fn($r) => (int)($r['user_id'] ?? 0), $rows));
        $users = $userids ? $DB->get_records_list('user', 'id', $userids) : [];
        foreach ($rows as $row) {
            $uid = (int)($row['user_id'] ?? 0);
            $out['rows'][] = [
                'user' => isset($users[$uid]) ? fullname($users[$uid]) : ($uid ? "#$uid" : '–'),
                'tokens' => isset($row['tokens']) ? number_format((int)$row['tokens'], 0, ',', '.') : '–',
                'estimated' => !empty($row['estimated']),
            ];
        }
        if (isset($data['site_total'])) {
            $out['sitetotal'] = number_format((int)$data['site_total'], 0, ',', '.');
        }
        return $out;
    }

    /**
     * Template context.
     *
     * @param \renderer_base $output
     * @return array
     */
    public function export_for_template(\renderer_base $output): array {
        $site = policy::site_policy();
        $overrides = [];
        foreach (policy::overrides() as $o) {
            $overrides[] = [
                'userid' => (int)$o->userid,
                'fullname' => fullname($o),
                'day' => self::limit($o->tokensperday, true),
                'month' => self::limit($o->tokenspermonth, true),
                'exempt' => !empty($o->exempt),
                'note' => format_string((string)$o->note),
                'editurl' => (new \moodle_url('/local/llmchat/admin/quotas.php', ['userid' => $o->userid]))->out(false),
                'deleteurl' => (new \moodle_url(
                    '/local/llmchat/admin/quotas.php',
                    ['delete' => $o->userid, 'sesskey' => sesskey()]
                ))->out(false),
            ];
        }
        return [
            'site' => [
                'sitedaily' => self::limit($site['site_tokens_per_day']),
                'sitemonthly' => self::limit($site['site_tokens_per_month']),
                'userdaily' => self::limit($site['user_tokens_per_day']),
                'usermonthly' => self::limit($site['user_tokens_per_month']),
                'perturn' => self::limit($site['max_tokens_per_turn']),
            ],
            'overrides' => $overrides,
            'hasoverrides' => !empty($overrides),
            'usage' => [$this->usage('day'), $this->usage('month')],
        ];
    }
}
