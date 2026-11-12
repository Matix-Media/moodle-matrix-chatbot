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
 * A stored conversation rendered on the server.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat\output;

use local_llmchat\content\ref_resolver;
use local_llmchat\markdown;

/**
 * Renders the finished turns of a stored conversation with the templates the client streams
 * into, following the frame handlers of amd/src/chat.js, so the chat page arrives with its
 * history and looks as if it had been streamed.
 *
 * Only completed turns are rendered: everything up to the last final, cancelled or error event.
 * What follows (a running turn, a question waiting for an answer, an interrupted last turn)
 * needs the client's listeners; the client resumes after {@see render()}'s `lastseq` and the
 * service replays exactly that part over the websocket.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
final class conversation_view {
    /** Event types that end a turn. */
    private const TURN_ENDS = ['final', 'cancelled', 'error'];

    /** @var callable fn(array $refs): array "kind:id" => url */
    private $resolverefs;

    /**
     * Constructor.
     *
     * @param \renderer_base $output
     * @param tool_display $display
     * @param callable|null $resolverefs fn(array $refs): array "kind:id" => url ('' when not accessible);
     *     default: what the current user can open ({@see ref_resolver})
     */
    public function __construct(
        /** @var \renderer_base */
        private \renderer_base $output,
        /** @var tool_display */
        private tool_display $display,
        ?callable $resolverefs = null,
    ) {
        $this->resolverefs = $resolverefs ?? [self::class, 'resolve_for_current_user'];
    }

    /**
     * Links for tool card locations, for the current user (as local_llmchat_resolve_refs).
     *
     * @param array $refs [{kind, id}]
     * @return array "kind:id" => url
     */
    public static function resolve_for_current_user(array $refs): array {
        global $USER;
        return (new ref_resolver())->resolve($refs, (int)$USER->id);
    }

    /**
     * Render the completed turns.
     *
     * @param array $events stored events in seq order (one event per streamed run)
     * @param array $tools tool metadata from the API's manifest [{name, label, icon, ...}]
     * @return array{html: string, lastseq: int} lastseq: the seq of the last rendered event (0: none)
     */
    public function render(array $events, array $tools): array {
        $events = array_values(array_filter($events, 'is_array'));
        $end = -1;
        foreach ($events as $i => $event) {
            if (in_array($event['type'] ?? '', self::TURN_ENDS, true)) {
                $end = $i;
            }
        }
        if ($end < 0) {
            return ['html' => '', 'lastseq' => 0];
        }
        $events = array_slice($events, 0, $end + 1);
        $meta = [];
        foreach ($tools as $tool) {
            if (is_array($tool) && isset($tool['name'])) {
                $meta[(string)$tool['name']] = $tool;
            }
        }
        $messages = $this->fold($events);
        $links = $this->resolve_links($messages);
        $html = '';
        foreach ($messages as $message) {
            $html .= $message['role'] === 'user'
                ? $this->output->render_from_template('local_llmchat/message_user', ['text' => $message['text']])
                : $this->render_turn($message, $meta, $links);
        }
        $lastseq = 0;
        foreach ($events as $event) {
            $lastseq = max($lastseq, (int)($event['seq'] ?? 0));
        }
        return ['html' => $html, 'lastseq' => $lastseq];
    }

    /**
     * Fold events into user messages and assistant turns, as chat.js's handlers build the DOM:
     * the body in stream order, a new thinking block whenever the model starts thinking again,
     * consecutive tool cards in one group, and a new text segment once anything was placed after
     * the current one.
     *
     * @param array $events
     * @return array [{role: user, text} | {role: assistant, parts, tools, ...}]
     */
    private function fold(array $events): array {
        $messages = [];
        $turn = null;
        $current = function (array $event) use (&$messages, &$turn): int {
            $id = $event['turn_id'] ?? null;
            if ($turn !== null && ($id === null || $messages[$turn]['id'] === null || $messages[$turn]['id'] === $id)) {
                $messages[$turn]['id'] = $messages[$turn]['id'] ?? $id;
                return $turn;
            }
            $messages[] = ['role' => 'assistant', 'id' => $id, 'tools' => [], 'citations' => [], 'thinking' => false,
                'parts' => [['kind' => 'text', 'raw' => '', 'citations' => []]]];
            $turn = count($messages) - 1;
            return $turn;
        };
        foreach ($events as $event) {
            $type = (string)($event['type'] ?? '');
            if ($type === 'user_message') {
                $messages[] = ['role' => 'user', 'text' => (string)($event['text'] ?? ($event['message']['text'] ?? ''))];
                $turn = null;
                continue;
            }
            if (
                !in_array($type, ['thinking', 'token', 'citations', 'tool_call', 'tool_result', 'question', 'user_answer',
                    'final', 'turn_interrupted', 'cancelled', 'error'], true)
            ) {
                // Quota, rate limits, progress and unknown types change nothing in the history.
                continue;
            }
            $m = &$messages[$current($event)];
            $last = count($m['parts']) - 1;
            // The current text segment, or a new one when something was placed after it.
            $segment = function () use (&$m): int {
                $last = count($m['parts']) - 1;
                if ($m['parts'][$last]['kind'] !== 'text') {
                    $m['parts'][] = ['kind' => 'text', 'raw' => '', 'citations' => []];
                    $last++;
                }
                return $last;
            };
            // Every frame but a thinking delta (or a citations list) ends a run of thinking.
            $wasthinking = $m['thinking'];
            if ($type !== 'citations' && !($type === 'tool_result' && !isset($m['tools'][(string)($event['id'] ?? '')]))) {
                $m['thinking'] = $type === 'thinking';
            }
            switch ($type) {
                case 'thinking':
                    if (!$wasthinking) {
                        $m['parts'][] = ['kind' => 'thinking', 'text' => ''];
                    }
                    // The latest thinking block gets the delta.
                    $blocks = array_keys(array_column($m['parts'], 'kind'), 'thinking', true);
                    $m['parts'][end($blocks)]['text'] .= (string)($event['delta'] ?? '');
                    break;
                case 'token':
                    $i = $segment();
                    $m['parts'][$i]['raw'] .= (string)($event['delta'] ?? '');
                    $m['parts'][$i]['citations'] = $m['citations'];
                    break;
                case 'citations':
                    $m['citations'] = $event['citations'] ?? [];
                    break;
                case 'tool_call':
                    $id = (string)($event['id'] ?? '');
                    $m['tools'][$id] = ['call' => $event, 'result' => null];
                    if ($m['parts'][$last]['kind'] === 'tools') {
                        $m['parts'][$last]['ids'][] = $id;
                    } else {
                        $m['parts'][] = ['kind' => 'tools', 'ids' => [$id]];
                    }
                    break;
                case 'tool_result':
                    $id = (string)($event['id'] ?? '');
                    if (isset($m['tools'][$id])) {
                        $m['tools'][$id]['result'] = $event;
                    }
                    break;
                case 'question':
                    $interrupt = (string)($event['interrupt_id'] ?? '');
                    $m['parts'] = array_values(array_filter(
                        $m['parts'],
                        fn($p) => $p['kind'] !== 'question' || $p['interrupt'] !== $interrupt
                    ));
                    $m['parts'][] = ['kind' => 'question', 'interrupt' => $interrupt, 'question' => $event, 'answer' => null];
                    $m['parts'][] = ['kind' => 'text', 'raw' => '', 'citations' => []];
                    break;
                case 'user_answer':
                    foreach ($m['parts'] as $i => $part) {
                        if ($part['kind'] === 'question' && $part['interrupt'] === (string)($event['interrupt_id'] ?? '')) {
                            $m['parts'][$i]['answer'] = is_array($event['response'] ?? null) ? $event['response'] : [];
                        }
                    }
                    break;
                case 'final':
                    $m['citations'] = $event['citations'] ?? $m['citations'];
                    $i = $segment();
                    $m['parts'][$i]['raw'] = (string)($event['text'] ?? $m['parts'][$i]['raw']);
                    $m['parts'][$i]['citations'] = $m['citations'];
                    $m['parts'][] = ['kind' => 'footer', 'final' => $event, 'raw' => $m['parts'][$i]['raw'],
                        'citations' => $m['citations']];
                    $turn = null;
                    break;
                case 'turn_interrupted':
                    $m['parts'][] = ['kind' => 'html', 'html' => $this->output->render_from_template('local_llmchat/notice', [
                        'type' => 'info', 'icon' => 'i/info', 'component' => 'core',
                        'message' => get_string('turninterrupted', 'local_llmchat'), 'hasaction' => false,
                    ])];
                    break;
                case 'cancelled':
                case 'error':
                    $cancelled = $type === 'cancelled' || ($event['code'] ?? '') === 'cancelled';
                    // The client shows other errors as a page notification; in a history they belong to their turn.
                    $text = $cancelled ? get_string('cancelled', 'local_llmchat')
                        : get_string('error', 'local_llmchat', (string)(($event['message'] ?? '') ?: ($event['code'] ?? '')));
                    $class = $cancelled ? 'small text-muted' : 'small text-danger';
                    $m['parts'][] = ['kind' => 'html', 'html' => \html_writer::div(s($text), $class)];
                    $turn = null;
                    break;
            }
            unset($m, $segment);
        }
        return $messages;
    }

    /**
     * Resolve the locations of all tool cards at once.
     *
     * @param array $messages
     * @return array "kind:id" => url
     */
    private function resolve_links(array $messages): array {
        $refs = [];
        foreach ($messages as $message) {
            foreach ($message['tools'] ?? [] as $tool) {
                $described = $this->display->describe_call(self::args($tool['call']), self::locations($tool['call']));
                $found = $described['refs'];
                if (is_array($tool['result'] ?? null)) {
                    $found = array_merge($found, $this->display->describe_result($tool['result'])['refs']);
                }
                foreach ($found as $ref) {
                    $refs["{$ref['kind']}:{$ref['id']}"] = $ref;
                }
            }
        }
        if (!$refs) {
            return [];
        }
        try {
            return ($this->resolverefs)(array_values($refs));
        } catch (\Throwable $e) {
            // Links are a convenience; the cards show the plain names without them.
            debugging('local_llmchat: resolving tool card links failed: ' . $e->getMessage(), DEBUG_DEVELOPER);
            return [];
        }
    }

    /**
     * A tool_call event's arguments.
     *
     * @param array $call
     * @return array
     */
    private static function args(array $call): array {
        return is_array($call['args'] ?? null) ? $call['args'] : [];
    }

    /**
     * A tool_call event's locations (none for calls saved before them).
     *
     * @param array $call
     * @return array
     */
    private static function locations(array $call): array {
        return is_array($call['locations'] ?? null) ? $call['locations'] : [];
    }

    /**
     * One assistant turn, its parts in stream order.
     *
     * @param array $turn
     * @param array $meta tool name => metadata
     * @param array $links "kind:id" => url
     * @return string
     */
    private function render_turn(array $turn, array $meta, array $links): string {
        $parts = [];
        $html = fn(string $html): array => ['istext' => false, 'istools' => false, 'ishtml' => true, 'html' => $html];
        foreach ($turn['parts'] as $part) {
            switch ($part['kind']) {
                case 'text':
                    $parts[] = ['istext' => true, 'istools' => false, 'ishtml' => false,
                        'html' => markdown::render($part['raw'], $part['citations'])];
                    break;
                case 'thinking':
                    // The client hides a thinking block that streamed no text.
                    if (trim($part['text']) !== '') {
                        $parts[] = $html($this->output->render_from_template('local_llmchat/thinking', [
                            'uniqid' => \html_writer::random_id('llmchat-thinking'), 'done' => true, 'text' => $part['text'],
                        ]));
                    }
                    break;
                case 'tools':
                    $cards = '';
                    foreach ($part['ids'] as $id) {
                        $cards .= $this->render_tool($turn['tools'][$id]['call'], $turn['tools'][$id]['result'], $meta, $links);
                    }
                    $parts[] = ['istext' => false, 'istools' => true, 'ishtml' => false, 'html' => $cards];
                    break;
                case 'question':
                    // An unanswered question of a finished turn can no longer be answered.
                    if ($part['answer'] !== null) {
                        $parts[] = $html($this->output->render_from_template(
                            'local_llmchat/interaction_answered',
                            ['answer' => self::answer_summary($part['question'], $part['answer'])]
                        ));
                    }
                    break;
                case 'footer':
                    $parts[] = $html($this->render_footer($part['final'], $part['raw'], $part['citations']));
                    break;
                default:
                    $parts[] = $html($part['html']);
            }
        }
        return $this->output->render_from_template('local_llmchat/message_assistant', [
            'done' => true, 'hasparts' => true, 'parts' => $parts,
        ]);
    }

    /**
     * A finished tool card.
     *
     * @param array $call tool_call event
     * @param array|null $result tool_result event
     * @param array $meta
     * @param array $links
     * @return string
     */
    private function render_tool(array $call, ?array $result, array $meta, array $links): string {
        $name = (string)($call['name'] ?? '');
        $args = self::args($call);
        $locations = self::locations($call);
        $toolmeta = $meta[$name] ?? [];
        [$icon, $component] = tool_display::icon($name, $toolmeta);
        $described = $this->display->describe_call($args, $locations);
        foreach ($described['params'] as $i => $param) {
            if ($param['ispath']) {
                $described['params'][$i]['segments'] = tool_display::link_segments($param['segments'], $links);
            }
        }
        $context = array_merge([
            'uniqid' => \html_writer::random_id('llmchat-tool'),
            'icon' => $icon,
            'component' => $component,
            'label' => tool_display::label($name, $args, $toolmeta, $locations, get_string('toolval:root', 'local_llmchat')),
        ], $described, [
            'done' => $result !== null,
            'summary' => $result !== null && !empty($result['summary']) ? (string)$result['summary'] : '',
            'result' => $result !== null ? tool_display::link_entries($this->display->describe_result($result), $links) : null,
        ]);
        return $this->output->render_from_template('local_llmchat/tool_call', $context);
    }

    /**
     * Sources and actions under a final answer (as chat.js onFinal).
     *
     * @param array $final
     * @param string $raw the answer text
     * @param array $citations
     * @return string
     */
    private function render_footer(array $final, string $raw, array $citations): string {
        $items = [];
        foreach ($citations as $c) {
            $items[] = [
                'index' => $c['index'] ?? '',
                'title' => (string)(($c['title'] ?? '') ?: ($c['url'] ?? '')),
                'meta' => tool_display::citation_meta($c, get_string('page')),
                'url' => (string)($c['url'] ?? ''),
                'hasurl' => !empty($c['url']),
            ];
        }
        return $this->output->render_from_template('local_llmchat/answer_footer', [
            'budgetexhausted' => !empty($final['budget_exhausted']) || !empty($final['truncated_by_budget']),
            'hascitations' => count($items) > 0,
            'citations' => $items,
            'copytext' => $raw,
        ]);
    }

    /**
     * What the client shows once a question was answered (the renderers' summaries).
     *
     * @param array $question question event {kind, payload}
     * @param array $response user_answer response
     * @return string
     */
    public static function answer_summary(array $question, array $response): string {
        $payload = is_array($question['payload'] ?? null) ? $question['payload'] : [];
        switch ($question['kind'] ?? '') {
            case 'question':
                $selected = array_map('strval', is_array($response['selected'] ?? null) ? $response['selected'] : []);
                return (string)(($response['text'] ?? '') ?: implode(', ', $selected));
            case 'confirm':
                return get_string(!empty($response['confirmed']) ? 'confirm:yes' : 'confirm:no', 'local_llmchat');
            case 'choice':
                foreach ($payload['items'] ?? [] as $item) {
                    if (is_array($item) && isset($item['id']) && (string)$item['id'] === (string)($response['id'] ?? '')) {
                        return (string)($item['label'] ?? $item['id']);
                    }
                }
                return (string)($response['id'] ?? '');
            default:
                return (string)($response['text'] ?? '');
        }
    }
}
