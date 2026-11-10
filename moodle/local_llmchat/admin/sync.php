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
 * Sync status page: test connection, run/clear+reimport, outbox state.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

require(__DIR__ . '/../../../config.php');
require_once($CFG->libdir . '/adminlib.php');

use local_llmchat\api_client;
use local_llmchat\api_exception;
use local_llmchat\sync\outbox;
use local_llmchat\task\run_full_sync_task;

admin_externalpage_setup('local_llmchat_sync');
$context = context_system::instance();
require_capability('local/llmchat:manage', $context);

$action = optional_param('action', '', PARAM_ALPHA);
$url = new moodle_url('/local/llmchat/admin/sync.php');
$PAGE->set_url($url);

if ($action !== '') {
    require_sesskey();
    switch ($action) {
        case 'test':
            try {
                $status = api_client::from_config()->status();
                if (isset($status['site_id'])) {
                    set_config('siteid', (string)$status['site_id'], 'local_llmchat');
                }
                unset_config('authfailedat', 'local_llmchat');
                redirect($url, get_string('status:connectionok', 'local_llmchat'), null, \core\output\notification::NOTIFY_SUCCESS);
            } catch (api_exception $e) {
                redirect(
                    $url,
                    get_string('status:connectionfailed', 'local_llmchat', $e->getMessage()),
                    null,
                    \core\output\notification::NOTIFY_ERROR
                );
            }
            break;
        case 'run':
            \core\task\manager::queue_adhoc_task(run_full_sync_task::create(false), true);
            redirect($url, get_string('status:queued', 'local_llmchat'), null, \core\output\notification::NOTIFY_SUCCESS);
            break;
        case 'reimport':
            // A full sync supersedes pending incremental rows; tombstoning of everything not
            // pushed again happens only when the new run finishes.
            outbox::clear();
            \core\task\manager::queue_adhoc_task(run_full_sync_task::create(true), true);
            redirect($url, get_string('status:queued', 'local_llmchat'), null, \core\output\notification::NOTIFY_SUCCESS);
            break;
        case 'retry':
            outbox::retry_exhausted();
            redirect($url, get_string('status:retried', 'local_llmchat'), null, \core\output\notification::NOTIFY_SUCCESS);
            break;
    }
}

$client = null;
if (api_client::is_configured()) {
    try {
        $client = api_client::from_config();
    } catch (api_exception $e) {
        \core\notification::error($e->getMessage());
    }
}

echo $OUTPUT->header();
echo $OUTPUT->heading(get_string('syncstatus', 'local_llmchat'));
if (!$client) {
    echo $OUTPUT->notification(get_string('error:notconfigured', 'local_llmchat'), 'warning');
}
echo $OUTPUT->render_from_template(
    'local_llmchat/status_page',
    (new \local_llmchat\output\status_page($client))->export_for_template($OUTPUT)
);
echo $OUTPUT->footer();
