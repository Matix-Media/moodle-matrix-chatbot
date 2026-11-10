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
 * Sync status page renderable.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat\output;

use local_llmchat\api_client;
use local_llmchat\api_exception;
use local_llmchat\sync\full_sync;
use local_llmchat\sync\outbox;

/**
 * Data for the sync status page: API connection + /ingest/status, local sync progress,
 * outbox counts and failing rows.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class status_page implements \renderable, \templatable {
    /**
     * Constructor.
     *
     * @param api_client|null $client null when not configured
     */
    public function __construct(
        /** @var api_client|null */
        private ?api_client $client,
    ) {
    }

    /**
     * Flatten nested status JSON into label/value rows.
     *
     * @param array $data
     * @param string $prefix
     * @return array
     */
    public static function flatten(array $data, string $prefix = ''): array {
        $rows = [];
        foreach ($data as $key => $value) {
            $label = $prefix === '' ? (string)$key : "$prefix.$key";
            if (is_array($value) && $value !== [] && !array_is_list($value)) {
                $rows = array_merge($rows, self::flatten($value, $label));
            } else {
                $rows[] = ['label' => $label, 'value' => is_scalar($value) || $value === null
                    ? (is_bool($value) ? ($value ? 'true' : 'false') : (string)($value ?? '–'))
                    : json_encode($value, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE)];
            }
        }
        return $rows;
    }

    /**
     * Template context.
     *
     * @param \renderer_base $output
     * @return array
     */
    public function export_for_template(\renderer_base $output): array {
        $api = ['configured' => $this->client !== null, 'ok' => false, 'rows' => [], 'error' => ''];
        if ($this->client) {
            try {
                $status = $this->client->status();
                $api['ok'] = true;
                $api['rows'] = self::flatten($status);
                if (isset($status['site_id'])) {
                    set_config('siteid', (string)$status['site_id'], 'local_llmchat');
                }
            } catch (api_exception $e) {
                $api['error'] = $e->is_auth_failure() ? get_string('status:authfailed', 'local_llmchat')
                    : $e->getMessage();
            }
        }
        $run = full_sync::latest();
        $sync = null;
        if ($run) {
            $total = count(json_decode($run->courseids, true) ?: []);
            $sync = [
                'syncid' => $run->syncid,
                'state' => $run->state,
                'progress' => "{$run->chunkno} / {$total}",
                'documents' => (int)$run->documents,
                'blobs' => (int)$run->blobs,
                'started' => userdate($run->timestarted),
                'modified' => userdate($run->timemodified),
                'finished' => $run->timefinished ? userdate($run->timefinished) : '–',
                'lasterror' => (string)$run->lasterror,
            ];
        }
        $failed = array_map(fn($r) => [
            'courseid' => (int)$r->courseid,
            'cmid' => (int)$r->cmid,
            'action' => $r->action,
            'attempts' => (int)$r->attempts,
            'nextattempt' => userdate($r->nextattempt),
            'lasterror' => (string)$r->lasterror,
        ], outbox::failed_rows());
        $authfailedat = get_config('local_llmchat', 'authfailedat');
        return [
            'api' => $api,
            'sync' => $sync,
            'hassync' => $sync !== null,
            'outbox' => outbox::counts(),
            'failed' => $failed,
            'hasfailed' => !empty($failed),
            'authfailed' => $authfailedat ? userdate((int)$authfailedat) : '',
            'sesskey' => sesskey(),
            'actionurl' => (new \moodle_url('/local/llmchat/admin/sync.php'))->out(false),
        ];
    }
}
