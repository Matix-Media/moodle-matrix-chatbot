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

use local_llmchat\access\chat_token;
use local_llmchat\api_client;

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

    /** @var array|null the user's conversations, when {@see preload()} got them */
    private ?array $conversations = null;

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
     * Fetch the conversation list from the API, so the page arrives with it instead of the client
     * loading it after it rendered. The chat itself is always the client's: it loads the open
     * conversation and shows it loading meanwhile. Waits at most {@see api_client::PAGE_TIMEOUT}
     * seconds; if that fails the client loads the list too. Not for the compact block view,
     * which has no list and must not hold up the course page.
     */
    public function preload(): void {
        global $USER;
        if ($this->compact) {
            return;
        }
        try {
            $client = api_client::from_config(api_client::PAGE_TIMEOUT);
            $token = chat_token::issue((int)$USER->id, $this->courseid ?: null)['token'];
            $this->conversations = $client->conversations($token);
        } catch (\Throwable $e) {
            debugging('local_llmchat: loading the conversation list failed: ' . $e->getMessage(), DEBUG_DEVELOPER);
        }
    }

    /**
     * Config for local_llmchat/chat init(). Contains no profile data. "preloaded.list" says
     * whether the page already shows the conversation list.
     *
     * @return array
     */
    public function js_config(): array {
        return [
            'courseid' => $this->courseid,
            'rootid' => $this->rootid,
            'conversationid' => $this->conversationid,
            'preloaded' => ['list' => $this->conversations !== null],
        ];
    }

    /**
     * The conversation list as a local_llmchat/conversation_list context (null: not loaded).
     *
     * @return array|null
     */
    private function conversation_list(): ?array {
        if ($this->conversations === null) {
            return null;
        }
        $items = [];
        foreach ($this->conversations as $c) {
            if (!is_array($c) || !isset($c['id'])) {
                continue;
            }
            $items[] = [
                'id' => (string)$c['id'],
                'title' => (string)(($c['title'] ?? '') ?: get_string('untitled', 'local_llmchat')),
                'active' => (string)$c['id'] === $this->conversationid,
            ];
        }
        return ['hasconversations' => count($items) > 0, 'conversations' => $items];
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
            'conversationlist' => $this->conversation_list(),
            // The client opens this conversation; until it has, the chat shows it loading.
            'loading' => $this->conversationid !== '',
        ];
    }
}
