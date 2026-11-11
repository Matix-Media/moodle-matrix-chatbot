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
    /** @var string Root element id of the full chat page. */
    public const ROOT_ID = 'local-llmchat-root';

    /**
     * Constructor.
     *
     * @param int $courseid 0 for the global chat
     * @param string $coursename
     * @param string $conversationid conversation to open on load ('' = a new one)
     * @param bool $compact the single-conversation view of the chat block (no conversation list)
     * @param string $rootid root element id, unique on the page
     */
    public function __construct(
        /** @var int */
        private int $courseid,
        /** @var string */
        private string $coursename = '',
        /** @var string */
        private string $conversationid = '',
        /** @var bool */
        private bool $compact = false,
        /** @var string */
        private string $rootid = self::ROOT_ID,
    ) {
    }

    /**
     * The full chat page for this scope, optionally with a conversation open.
     *
     * @param string $conversationid
     * @return \moodle_url
     */
    public function full_url(string $conversationid = ''): \moodle_url {
        $params = [];
        if ($this->courseid > 0) {
            $params['courseid'] = $this->courseid;
        }
        if ($conversationid !== '') {
            $params['conversation'] = $conversationid;
        }
        return new \moodle_url('/local/llmchat/chat.php', $params);
    }

    /**
     * Config for local_llmchat/chat init(). Contains no profile data.
     *
     * @return array
     */
    public function js_config(): array {
        return [
            'courseid' => $this->courseid,
            'rootid' => $this->rootid,
            'conversationid' => $this->conversationid,
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
            'rootid' => $this->rootid,
            'courseid' => $this->courseid,
            'compact' => $this->compact,
            'fullurl' => $this->full_url($this->conversationid)->out(false),
            'coursename' => $this->coursename,
            'courseurl' => $hascourse ? (new \moodle_url('/course/view.php', ['id' => $this->courseid]))->out(false) : '',
            'hascourse' => $hascourse,
            'intro' => $hascourse ? get_string('emptyintrocourse', 'local_llmchat', $this->coursename)
                : get_string('emptyintro', 'local_llmchat'),
            'suggestions' => $suggestions,
        ];
    }
}
