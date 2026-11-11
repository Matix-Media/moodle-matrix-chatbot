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
 * Chat block: the current conversation of the course chat, next to the course.
 *
 * @package    block_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

use local_llmchat\output\chat_page;

/**
 * The course chat in a block.
 *
 * Shows only the current conversation (the last one open in this course, or a new one). Its header
 * opens that conversation in the full chat page, which lists all conversations.
 *
 * @package    block_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class block_llmchat extends block_base {
    /**
     * Title and defaults.
     */
    public function init() {
        $this->title = get_string('chattitle', 'local_llmchat');
    }

    /**
     * Course pages, activities, the front page and the Dashboard.
     *
     * @return array
     */
    public function applicable_formats() {
        return ['all' => true];
    }

    /**
     * One chat per page.
     *
     * @return bool
     */
    public function instance_allow_multiple() {
        return false;
    }

    /**
     * The course of the page, or 0 outside courses (the global chat).
     *
     * @return int
     */
    private function courseid(): int {
        $course = $this->page->course;
        return ($course && (int)$course->id !== (int)SITEID) ? (int)$course->id : 0;
    }

    /**
     * Whether the current user gets the chat here.
     *
     * @param int $courseid
     * @return bool
     */
    private function chat_available(int $courseid): bool {
        if (!isloggedin() || isguestuser() || !\local_llmchat\api_client::is_configured()) {
            return false;
        }
        // The full chat page already is the chat.
        $chaturl = new moodle_url('/local/llmchat/chat.php');
        if ($this->page->has_set_url() && $this->page->url->compare($chaturl, URL_MATCH_BASE)) {
            return false;
        }
        $context = $courseid ? context_course::instance($courseid) : context_system::instance();
        return has_capability('local/llmchat:use', $context);
    }

    /**
     * The compact chat, started by local_llmchat/chat.
     *
     * @return stdClass
     */
    public function get_content() {
        if ($this->content !== null) {
            return $this->content;
        }
        $this->content = new stdClass();
        $this->content->text = '';
        $this->content->footer = '';

        $courseid = $this->courseid();
        if (!$this->chat_available($courseid)) {
            return $this->content;
        }
        $coursename = '';
        if ($courseid) {
            $options = ['context' => context_course::instance($courseid)];
            $coursename = format_string($this->page->course->fullname, true, $options);
        }
        $rootid = 'block-llmchat-' . (int)$this->instance->id;
        $chat = new chat_page($courseid, $coursename, '', true, $rootid);
        $output = $this->page->get_renderer('core');
        $this->content->text = $output->render_from_template('local_llmchat/chat_page', $chat->export_for_template($output));
        $this->page->requires->js_call_amd('local_llmchat/chat', 'init', [$chat->js_config()]);
        return $this->content;
    }
}
