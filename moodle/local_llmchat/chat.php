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
 * The chat page.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

require(__DIR__ . '/../../config.php');

$courseid = optional_param('courseid', 0, PARAM_INT);
$conversationid = optional_param('conversation', '', PARAM_ALPHANUMEXT);
$params = [];
$coursename = '';
if ($courseid && $courseid != SITEID) {
    $course = get_course($courseid);
    require_login($course);
    $context = context_course::instance($course->id);
    $params['courseid'] = $course->id;
    $coursename = format_string($course->fullname, true, ['context' => $context]);
    $PAGE->set_pagelayout('incourse');
} else {
    $courseid = 0;
    require_login(null, false);
    $context = context_system::instance();
    $PAGE->set_context($context);
    $PAGE->set_pagelayout('standard');
}
if (isguestuser()) {
    throw new require_login_exception('guest');
}
require_capability('local/llmchat:use', $context);

// The conversation parameter only picks what opens first; the page URL stays the scope's.
$PAGE->set_url(new moodle_url('/local/llmchat/chat.php', $params));
$PAGE->set_title(get_string('chat', 'local_llmchat'));
$PAGE->set_heading($coursename !== '' ? $coursename : get_string('chat', 'local_llmchat'));
// One width for the course and the global chat: Moodle's medium page width (as mod_data uses),
// instead of 'incourse' going edge to edge and 'standard' capping the content at 830px.
$PAGE->add_body_class('local-llmchat-fullpage');
$PAGE->add_body_class('mediumwidth');

$page = new \local_llmchat\output\chat_page($courseid, $coursename, $conversationid);

echo $OUTPUT->header();
if (!\local_llmchat\api_client::is_configured()) {
    echo $OUTPUT->notification(get_string('error:notconfigured', 'local_llmchat'), 'warning');
} else {
    echo $OUTPUT->render_from_template('local_llmchat/chat_page', $page->export_for_template($OUTPUT));
    $PAGE->requires->js_call_amd('local_llmchat/chat', 'init', [$page->js_config()]);
}
echo $OUTPUT->footer();
