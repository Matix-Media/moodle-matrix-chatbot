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
 * Chat quotas: per-user overrides, exemptions and usage.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

require(__DIR__ . '/../../../config.php');
require_once($CFG->libdir . '/adminlib.php');

use local_llmchat\api_client;
use local_llmchat\form\quota_override_form;
use local_llmchat\quota\policy;

admin_externalpage_setup('local_llmchat_quotas');
require_capability('local/llmchat:manage', context_system::instance());

$url = new moodle_url('/local/llmchat/admin/quotas.php');
$PAGE->set_url($url);

$delete = optional_param('delete', 0, PARAM_INT);
if ($delete) {
    require_sesskey();
    policy::delete_override($delete);
    redirect($url, get_string('quota:deleted', 'local_llmchat'), null, \core\output\notification::NOTIFY_SUCCESS);
}

$userid = optional_param('userid', 0, PARAM_INT);
$users = [];
if ($userid && ($user = core_user::get_user($userid))) {
    $users[$userid] = fullname($user);
}
$form = new quota_override_form($url, ['users' => $users]);
if ($userid && !$form->is_submitted()) {
    $existing = $DB->get_record(policy::TABLE, ['userid' => $userid]);
    if ($existing) {
        $form->set_data([
            'userid' => $userid,
            'tokensperday' => $existing->tokensperday,
            'tokenspermonth' => $existing->tokenspermonth,
            'exempt' => $existing->exempt,
            'note' => $existing->note,
        ]);
    }
}
if ($form->is_cancelled()) {
    redirect($url);
} else if ($data = $form->get_data()) {
    $limit = fn($v) => trim((string)$v) === '' ? null : (int)$v;
    policy::save_override(
        (int)$data->userid,
        $limit($data->tokensperday),
        $limit($data->tokenspermonth),
        !empty($data->exempt),
        (string)$data->note
    );
    redirect($url, get_string('quota:saved', 'local_llmchat'), null, \core\output\notification::NOTIFY_SUCCESS);
}

$client = null;
if (api_client::is_configured()) {
    try {
        $client = api_client::from_config();
    } catch (\local_llmchat\api_exception $e) {
        \core\notification::error($e->getMessage());
    }
}

echo $OUTPUT->header();
echo $OUTPUT->heading(get_string('quotas', 'local_llmchat'));
echo $OUTPUT->render_from_template(
    'local_llmchat/quota_report',
    (new \local_llmchat\output\quota_report($client))->export_for_template($OUTPUT)
);
echo $OUTPUT->heading(get_string('quota:addoverride', 'local_llmchat'), 3);
$form->display();
echo $OUTPUT->footer();
