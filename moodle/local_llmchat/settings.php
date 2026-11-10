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
 * Admin settings for local_llmchat.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

defined('MOODLE_INTERNAL') || die();

if ($hassiteconfig) {
    $ADMIN->add('localplugins', new admin_category('local_llmchat_category', get_string('pluginname', 'local_llmchat')));

    $settings = new admin_settingpage('local_llmchat', get_string('settings', 'local_llmchat'));

    // Connection.
    $settings->add(new admin_setting_heading('local_llmchat/connection',
        get_string('settings:connection', 'local_llmchat'),
        get_string('settings:connection_desc', 'local_llmchat',
            (new moodle_url('/local/llmchat/admin/sync.php', ['action' => 'test', 'sesskey' => sesskey()]))->out(false))));
    $setting = new \local_llmchat\admin\setting_apiurl('local_llmchat/apiurl',
        get_string('settings:apiurl', 'local_llmchat'), get_string('settings:apiurl_desc', 'local_llmchat'), '', PARAM_URL);
    $setting->set_updatedcallback('local_llmchat_connection_updated');
    $settings->add($setting);
    $setting = new admin_setting_encryptedpassword('local_llmchat/secret',
        get_string('settings:secret', 'local_llmchat'), get_string('settings:secret_desc', 'local_llmchat'));
    $setting->set_updatedcallback('local_llmchat_connection_updated');
    $settings->add($setting);

    // Sync.
    $settings->add(new admin_setting_heading('local_llmchat/sync', get_string('settings:sync', 'local_llmchat'), ''));
    $settings->add(new admin_setting_configcheckbox('local_llmchat/syncenabled',
        get_string('settings:syncenabled', 'local_llmchat'), get_string('settings:syncenabled_desc', 'local_llmchat'), 1));
    $settings->add(new admin_setting_configtext('local_llmchat/includedcourses',
        get_string('settings:includedcourses', 'local_llmchat'),
        get_string('settings:includedcourses_desc', 'local_llmchat'), '', PARAM_SEQUENCE));
    $settings->add(new admin_setting_configtext('local_llmchat/maxfilesize',
        get_string('settings:maxfilesize', 'local_llmchat'), get_string('settings:maxfilesize_desc', 'local_llmchat'),
        \local_llmchat\content\document_builder::DEFAULT_MAXFILESIZE_MB, PARAM_INT));
    $settings->add(new admin_setting_configtext('local_llmchat/filetypes',
        get_string('settings:filetypes', 'local_llmchat'), get_string('settings:filetypes_desc', 'local_llmchat'),
        \local_llmchat\content\document_builder::DEFAULT_FILETYPES, PARAM_TEXT));
    $settings->add(new admin_setting_configtext('local_llmchat/maxattempts',
        get_string('settings:maxattempts', 'local_llmchat'), get_string('settings:maxattempts_desc', 'local_llmchat'),
        \local_llmchat\sync\outbox::DEFAULT_MAXATTEMPTS, PARAM_INT));
    $settings->add(new admin_setting_configtext('local_llmchat/syncmaxruntime',
        get_string('settings:syncmaxruntime', 'local_llmchat'), get_string('settings:syncmaxruntime_desc', 'local_llmchat'),
        \local_llmchat\task\full_sync_task::DEFAULT_MAXRUNTIME, PARAM_INT));

    // Quotas (A8b): 0 = unlimited. Every change is pushed to the API as the site policy.
    $settings->add(new admin_setting_heading('local_llmchat/quota', get_string('settings:quota', 'local_llmchat'),
        get_string('settings:quota_desc', 'local_llmchat')));
    foreach (['site_tokens_per_day', 'site_tokens_per_month', 'user_tokens_per_day', 'user_tokens_per_month',
            'max_tokens_per_turn'] as $name) {
        $setting = new admin_setting_configtext("local_llmchat/$name", get_string("settings:$name", 'local_llmchat'),
            get_string('settings:limit_desc', 'local_llmchat'), 0, PARAM_INT);
        $setting->set_updatedcallback('local_llmchat_policy_updated');
        $settings->add($setting);
    }
    foreach (['count_reasoning' => 1, 'exempt_users_bypass_site' => 0] as $name => $default) {
        $setting = new admin_setting_configcheckbox("local_llmchat/$name", get_string("settings:$name", 'local_llmchat'),
            get_string("settings:{$name}_desc", 'local_llmchat'), $default);
        $setting->set_updatedcallback('local_llmchat_policy_updated');
        $settings->add($setting);
    }

    $ADMIN->add('local_llmchat_category', $settings);
    $ADMIN->add('local_llmchat_category', new admin_externalpage('local_llmchat_sync',
        get_string('syncstatus', 'local_llmchat'), new moodle_url('/local/llmchat/admin/sync.php'), 'local/llmchat:manage'));
    $ADMIN->add('local_llmchat_category', new admin_externalpage('local_llmchat_quotas',
        get_string('quotas', 'local_llmchat'), new moodle_url('/local/llmchat/admin/quotas.php'), 'local/llmchat:manage'));
}
