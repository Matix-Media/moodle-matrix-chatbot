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
 * Privacy provider.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat\privacy;

use core_privacy\local\metadata\collection;
use core_privacy\local\request\approved_contextlist;
use core_privacy\local\request\approved_userlist;
use core_privacy\local\request\contextlist;
use core_privacy\local\request\transform;
use core_privacy\local\request\userlist;
use core_privacy\local\request\writer;

/**
 * Privacy provider: quota overrides are stored locally; conversations live on the external API.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class provider implements
    \core_privacy\local\metadata\provider,
    \core_privacy\local\request\core_userlist_provider,
    \core_privacy\local\request\plugin\provider {
    /**
     * Metadata.
     *
     * @param collection $collection
     * @return collection
     */
    public static function get_metadata(collection $collection): collection {
        $collection->add_database_table('local_llmchat_quota_override', [
            'userid' => 'privacy:metadata:override:userid',
            'tokensperday' => 'privacy:metadata:override:tokensperday',
            'tokenspermonth' => 'privacy:metadata:override:tokenspermonth',
            'exempt' => 'privacy:metadata:override:exempt',
            'note' => 'privacy:metadata:override:note',
            'timemodified' => 'privacy:metadata:override:timemodified',
        ], 'privacy:metadata:override');
        $collection->add_external_location_link('llmchat_api', [
            'userid' => 'privacy:metadata:api:userid',
            'fullname' => 'privacy:metadata:api:fullname',
            'lang' => 'privacy:metadata:api:lang',
            'courses' => 'privacy:metadata:api:courses',
            'conversation' => 'privacy:metadata:api:conversation',
        ], 'privacy:metadata:api');
        return $collection;
    }

    /**
     * Contexts with data for a user.
     *
     * @param int $userid
     * @return contextlist
     */
    public static function get_contexts_for_userid(int $userid): contextlist {
        global $DB;
        $list = new contextlist();
        if ($DB->record_exists('local_llmchat_quota_override', ['userid' => $userid])) {
            $list->add_system_context();
        }
        return $list;
    }

    /**
     * Users with data in a context.
     *
     * @param userlist $userlist
     */
    public static function get_users_in_context(userlist $userlist) {
        if ($userlist->get_context()->contextlevel != CONTEXT_SYSTEM) {
            return;
        }
        $userlist->add_from_sql('userid', 'SELECT userid FROM {local_llmchat_quota_override}', []);
    }

    /**
     * Export.
     *
     * @param approved_contextlist $contextlist
     */
    public static function export_user_data(approved_contextlist $contextlist) {
        global $DB;
        $userid = $contextlist->get_user()->id;
        foreach ($contextlist->get_contexts() as $context) {
            if ($context->contextlevel != CONTEXT_SYSTEM) {
                continue;
            }
            $override = $DB->get_record('local_llmchat_quota_override', ['userid' => $userid]);
            if ($override) {
                writer::with_context($context)->export_data([get_string('quotas', 'local_llmchat')], (object)[
                    'tokensperday' => $override->tokensperday,
                    'tokenspermonth' => $override->tokenspermonth,
                    'exempt' => transform::yesno($override->exempt),
                    'note' => $override->note,
                    'timemodified' => transform::datetime($override->timemodified),
                ]);
            }
        }
    }

    /**
     * Delete all data in a context.
     *
     * @param \context $context
     */
    public static function delete_data_for_all_users_in_context(\context $context) {
        global $DB;
        if ($context->contextlevel == CONTEXT_SYSTEM) {
            $DB->delete_records('local_llmchat_quota_override');
        }
    }

    /**
     * Delete one user's data.
     *
     * @param approved_contextlist $contextlist
     */
    public static function delete_data_for_user(approved_contextlist $contextlist) {
        global $DB;
        foreach ($contextlist->get_contexts() as $context) {
            if ($context->contextlevel == CONTEXT_SYSTEM) {
                $DB->delete_records('local_llmchat_quota_override', ['userid' => $contextlist->get_user()->id]);
            }
        }
    }

    /**
     * Delete several users' data.
     *
     * @param approved_userlist $userlist
     */
    public static function delete_data_for_users(approved_userlist $userlist) {
        global $DB;
        if ($userlist->get_context()->contextlevel == CONTEXT_SYSTEM && $userlist->get_userids()) {
            $DB->delete_records_list('local_llmchat_quota_override', 'userid', $userlist->get_userids());
        }
    }
}
