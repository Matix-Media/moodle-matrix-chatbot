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
 * Chat page renderable.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat\output;

/**
 * Data for the chat page and the AMD module's init config.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class chat_page implements \renderable, \templatable {
    /**
     * Constructor.
     *
     * @param int $courseid 0 for the global chat
     * @param string $coursename
     */
    public function __construct(
        /** @var int */
        private int $courseid,
        /** @var string */
        private string $coursename = '',
    ) {
    }

    /**
     * The current user's picture as theme HTML (no link, decorative).
     *
     * @param \renderer_base $output
     * @return string
     */
    private function user_picture(\renderer_base $output): string {
        global $USER;
        return $output->user_picture($USER, ['size' => 35, 'link' => false, 'visibletoscreenreaders' => false]);
    }

    /**
     * Config for local_llmchat/chat init(). Contains no profile data beyond the avatar markup.
     *
     * @return array
     */
    public function js_config(): array {
        global $OUTPUT;
        return [
            'courseid' => $this->courseid,
            'rootid' => 'local-llmchat-root',
            'userpicture' => $this->user_picture($OUTPUT),
        ];
    }

    /**
     * Template context.
     *
     * @param \renderer_base $output
     * @return array
     */
    public function export_for_template(\renderer_base $output): array {
        $hascourse = $this->courseid > 0;
        $suggestions = [];
        foreach (['suggestion1', 'suggestion2', 'suggestion3'] as $key) {
            $suggestions[] = ['text' => get_string($key, 'local_llmchat')];
        }
        return [
            'rootid' => 'local-llmchat-root',
            'courseid' => $this->courseid,
            'coursename' => $this->coursename,
            'courseurl' => $hascourse ? (new \moodle_url('/course/view.php', ['id' => $this->courseid]))->out(false) : '',
            'hascourse' => $hascourse,
            'intro' => $hascourse ? get_string('emptyintrocourse', 'local_llmchat', $this->coursename)
                : get_string('emptyintro', 'local_llmchat'),
            'suggestions' => $suggestions,
            'userpicture' => $this->user_picture($output),
        ];
    }
}
