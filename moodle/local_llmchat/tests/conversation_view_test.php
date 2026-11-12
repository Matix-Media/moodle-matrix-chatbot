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
 * Tests for the server-rendered conversation history.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat;

use local_llmchat\output\conversation_view;
use local_llmchat\output\tool_display;

/**
 * Tests for {@see conversation_view}.
 *
 * @covers \local_llmchat\output\conversation_view
 */
final class conversation_view_test extends \advanced_testcase {
    /** @var int */
    private int $seq = 0;

    /** Tool metadata as the API's manifest sends it. */
    private const TOOLS = [['name' => 'search_text', 'label' => 'Suche', 'icon' => '', 'summary' => '']];

    /**
     * A stored event.
     *
     * @param string $type
     * @param array $fields
     * @param string $turn
     * @return array
     */
    private function ev(string $type, array $fields = [], string $turn = 't1'): array {
        return array_merge(['v' => 1, 'seq' => ++$this->seq, 'turn_id' => $turn, 'type' => $type], $fields);
    }

    /**
     * Render events with links resolved by a fake.
     *
     * @param array $events
     * @param array $links "kind:id" => url
     * @return array [xpath, lastseq, html]
     */
    private function render(array $events, array $links = []): array {
        global $PAGE;
        $this->resetAfterTest();
        $this->setAdminUser();
        $view = new conversation_view(
            $PAGE->get_renderer('core'),
            tool_display::from_lang(),
            fn(array $refs) => array_intersect_key($links, array_flip(array_map(fn($r) => "{$r['kind']}:{$r['id']}", $refs)))
        );
        $result = $view->render($events, self::TOOLS);
        $dom = new \DOMDocument();
        libxml_use_internal_errors(true);
        $dom->loadHTML('<?xml encoding="utf-8"?><div id="root">' . $result['html'] . '</div>');
        libxml_clear_errors();
        return [new \DOMXPath($dom), $result['lastseq'], $result['html']];
    }

    /**
     * An attribute of the first node matching a query.
     *
     * @param \DOMXPath $x
     * @param string $query
     * @param \DOMNode|null $context
     * @param string $name
     * @return string
     */
    private static function attr(\DOMXPath $x, string $query, ?\DOMNode $context, string $name): string {
        return $x->query($query, $context)->item(0)->getAttribute($name);
    }

    public function test_question_and_cited_answer(): void {
        [$x, $lastseq] = $this->render([
            $this->ev('user_message', ['text' => 'Wo ist <der> Unterricht?']),
            $this->ev('token', ['delta' => 'Raum B204 [1].']),
            $this->ev('final', ['text' => 'Raum **B204** [1].', 'citations' => [
                ['index' => 1, 'title' => 'Stundenplan', 'url' => 'https://moodle.example/mod/page/view.php?id=5'],
            ]]),
            $this->ev('quota', ['used_today' => 10]),
        ]);
        $this->assertSame(1, $x->query('//*[@data-role="user"]')->length);
        $usertext = $x->query('//*[@data-role="user"]//*[@data-region="text"]')->item(0)->textContent;
        $this->assertSame('Wo ist <der> Unterricht?', trim($usertext));
        $answer = $x->query('//*[@data-role="assistant"]')->item(0);
        $this->assertNotNull($answer);
        $this->assertSame(1, $x->query('.//strong[.="B204"]', $answer)->length, 'markdown rendered');
        $cite = $x->query('.//a[contains(@class, "local-llmchat-cite")]', $answer)->item(0);
        $this->assertSame('https://moodle.example/mod/page/view.php?id=5', $cite->getAttribute('href'));
        $this->assertStringContainsString('d-none', self::attr($x, './/*[@data-region="typing"]', $answer, 'class'));
        $copy = $x->query('.//*[@data-action="copy"]', $answer)->item(0);
        $this->assertSame('Raum **B204** [1].', $copy->getAttribute('data-copytext'));
        $this->assertSame(1, $x->query('.//*[contains(@class, "local-llmchat-sources")]//a[.="Stundenplan"]', $answer)->length);
        $this->assertSame(3, $lastseq, 'the quota after the final answer is left to the live client');
    }

    public function test_finished_tool_card_with_summary_result_and_linked_location(): void {
        [$x] = $this->render([
            $this->ev('user_message', ['text' => 'Suche']),
            $this->ev('tool_call', ['id' => 'c1', 'name' => 'search_text', 'args' => ['query' => 'Raum', 'path' => '/lf7/plan'],
                'locations' => ['path' => [
                    ['kind' => 'course', 'id' => '2', 'name' => 'LF7'], ['kind' => 'module', 'id' => '5', 'name' => 'Plan / Raum'],
                ]]]),
            $this->ev('tool_result', ['id' => 'c1', 'name' => 'search_text', 'summary' => '2 Treffer', 'citations' => [
                ['index' => 1, 'title' => 'Plan', 'url' => 'https://moodle.example/mod/page/view.php?id=5'],
            ]]),
            $this->ev('final', ['text' => 'Gefunden.']),
        ], ['course:2' => 'https://moodle.example/course/view.php?id=2', 'module:5' => '']);
        $card = $x->query('//*[@data-region="tool"]')->item(0);
        $this->assertNotNull($card);
        $this->assertStringNotContainsString('d-none', $x->query('//*[@data-region="tools"]')->item(0)->getAttribute('class'));
        $this->assertStringContainsString('Suche „Raum“', $card->textContent);
        $this->assertSame('2 Treffer', $x->query('.//*[@data-region="summary"]', $card)->item(0)->textContent);
        $this->assertStringContainsString('d-none', self::attr($x, './/*[@data-region="spinner"]', $card, 'class'));
        $this->assertStringNotContainsString('d-none', self::attr($x, './/*[@data-region="done"]', $card, 'class'));
        $result = $x->query('.//*[@data-region="result"]', $card)->item(0);
        $this->assertSame(1, $x->query('.//a[.="Plan"]', $result)->length, 'result sources');
        $crumb = $x->query('.//*[@data-region="path"]//a', $card);
        $this->assertSame(1, $crumb->length, 'only the segment the user can open is a link');
        $this->assertSame('https://moodle.example/course/view.php?id=2', $crumb->item(0)->getAttribute('href'));
        $names = array_map(fn($n) => trim($n->textContent), iterator_to_array($x->query('.//*[@data-region="path"]//li', $card)));
        $this->assertSame(['LF7', 'Plan / Raum'], $names, 'Moodle names, not path segments');
    }

    public function test_a_path_saved_without_a_location_is_shown_as_typed(): void {
        [$x] = $this->render([
            $this->ev('user_message', ['text' => 'Suche']),
            $this->ev('tool_call', ['id' => 'c1', 'name' => 'list_dir', 'args' => ['path' => '/LF7/Woche 1']]),
            $this->ev('final', ['text' => 'ok']),
        ]);
        $card = $x->query('//*[@data-region="tool"]')->item(0);
        $this->assertSame(0, $x->query('.//*[@data-region="path"]', $card)->length);
        $this->assertStringContainsString('/LF7/Woche 1', $card->textContent);
    }

    public function test_the_card_title_names_the_resolved_place(): void {
        [$x] = $this->render([
            $this->ev('user_message', ['text' => 'Zeig']),
            $this->ev('tool_call', ['id' => 'c1', 'name' => 'list_dir', 'args' => ['path' => '/lf7/woche-1'],
                'locations' => ['path' => [['kind' => 'course', 'id' => '2', 'name' => 'LF7'],
                    ['kind' => 'section', 'id' => '7', 'name' => 'Woche 1']]]]),
            $this->ev('final', ['text' => 'ok']),
        ]);
        $title = $x->query('//*[@data-region="tool"]//span[contains(@class, "text-truncate")]')->item(0);
        $this->assertNotNull($title);
        $this->assertStringContainsString('„Woche 1“', $title->textContent);
        $this->assertStringNotContainsString('woche-1', $title->textContent);
    }

    public function test_tool_results_render_their_display_blocks(): void {
        $plan = ['index' => 1, 'title' => 'Plan', 'course_name' => 'LF7', 'url' => 'https://moodle.example/mod/page/view.php?id=5',
            'page' => 2, 'pages' => [2, 5]];
        [$x] = $this->render([
            $this->ev('user_message', ['text' => 'Suche']),
            $this->ev('tool_call', ['id' => 'c1', 'name' => 'search_text', 'args' => ['query' => 'Raum']]),
            $this->ev('tool_result', ['id' => 'c1', 'name' => 'search_text', 'citations' => [$plan], 'display' => [
                ['kind' => 'hits', 'items' => [
                    ['cite' => 1, 'location' => [['kind' => 'course', 'id' => '2', 'name' => 'LF7'],
                        ['kind' => 'module', 'id' => '5', 'name' => 'Plan']], 'page' => 2, 'snippet' => 'Raum B204'],
                ]],
            ]]),
            $this->ev('tool_call', ['id' => 'c2', 'name' => 'list_dir', 'args' => ['path' => '/LF7']]),
            $this->ev('tool_result', ['id' => 'c2', 'name' => 'list_dir', 'display' => [
                ['kind' => 'entries', 'location' => [['kind' => 'course', 'id' => '2', 'name' => 'LF7']], 'items' => [
                    ['name' => 'Woche 1', 'type' => 'folder', 'count' => 3, 'location' => [
                        ['kind' => 'course', 'id' => '2', 'name' => 'LF7'], ['kind' => 'section', 'id' => '7', 'name' => 'Woche 1'],
                    ]],
                    ['name' => 'Geheim', 'type' => 'file', 'location' => [
                        ['kind' => 'course', 'id' => '2', 'name' => 'LF7'], ['kind' => 'module', 'id' => '9', 'name' => 'Geheim'],
                    ]],
                ]],
            ]]),
            $this->ev('tool_call', ['id' => 'c3', 'name' => 'list_dir', 'args' => ['path' => '/LF8']]),
            $this->ev('tool_result', ['id' => 'c3', 'name' => 'list_dir', 'display' => [
                ['kind' => 'notice', 'level' => 'error', 'code' => 'path_not_found', 'text' => 'Nicht gefunden: LF8'],
            ]]),
            $this->ev('final', ['text' => 'Raum B204 [1].', 'citations' => [$plan]]),
        ], ['course:2' => 'https://moodle.example/course/view.php?id=2',
            'section:7' => 'https://moodle.example/course/section.php?id=7', 'module:9' => '']);
        [$search, $listing, $failed] = iterator_to_array($x->query('//*[@data-region="tool"]'));
        $hit = $x->query('.//*[contains(@class, "local-llmchat-hits")]/li', $search)->item(0);
        $this->assertNotNull($hit, 'hits block');
        $this->assertStringContainsString('Raum B204', $hit->textContent);
        $this->assertSame('https://moodle.example/mod/page/view.php?id=5', self::attr($x, './/a', $hit, 'href'));
        $sourcelists = './/*[@data-region="result"]//ul[not(contains(@class, "local-llmchat-hits"))]';
        $this->assertSame(0, $x->query($sourcelists, $search)->length, 'a source shown as a hit is not listed again');

        $entries = $x->query('.//*[@data-region="entry"]', $listing);
        $this->assertSame(2, $entries->length);
        $this->assertSame('https://moodle.example/course/section.php?id=7', self::attr($x, './/a', $entries->item(0), 'href'));
        $this->assertSame(0, $x->query('.//a', $entries->item(1))->length, 'what the user cannot open is not a link');
        $this->assertStringContainsString(get_string('toolresult:entries', 'local_llmchat', 3), $listing->textContent);

        $notice = $x->query('.//p[contains(@class, "text-danger")]', $failed)->item(0);
        $this->assertSame(get_string('toolnotice:path_not_found', 'local_llmchat'), $notice->textContent);
        $this->assertStringContainsString('Nicht gefunden: LF8', $failed->textContent);

        $sources = $x->query('//*[contains(@class, "local-llmchat-sources")]')->item(0);
        $this->assertStringContainsString(get_string('page') . ' 2, 5', $sources->textContent);
    }

    public function test_thinking_is_collapsed_and_empty_thinking_is_left_out(): void {
        [$x] = $this->render([
            $this->ev('user_message', ['text' => 'a']),
            $this->ev('thinking', ['delta' => 'Ich überlege.']),
            $this->ev('final', ['text' => 'b']),
            $this->ev('user_message', ['text' => 'c'], 't2'),
            $this->ev('thinking', ['delta' => '  '], 't2'),
            $this->ev('final', ['text' => 'd'], 't2'),
        ]);
        $blocks = $x->query('//*[@data-region="thinking-block"]');
        $this->assertSame(1, $blocks->length);
        $this->assertSame('Ich überlege.', $x->query('.//*[@data-region="thinking-text"]', $blocks->item(0))->item(0)->textContent);
        $this->assertSame('false', self::attr($x, './/*[@data-action="toggle"]', $blocks->item(0), 'aria-expanded'));
        $collapsible = $x->query('.//*[@data-region="collapsible"]', $blocks->item(0))->item(0);
        $this->assertStringNotContainsString('show', $collapsible->getAttribute('class'));
    }

    public function test_body_keeps_stream_order_like_the_client(): void {
        $call = fn(string $id) => $this->ev('tool_call', ['id' => $id, 'name' => 'search_text', 'args' => ['query' => $id]]);
        $result = fn(string $id) => $this->ev('tool_result', ['id' => $id, 'name' => 'search_text', 'summary' => 'ok']);
        [$x] = $this->render([
            $this->ev('user_message', ['text' => 'Frage']),
            $this->ev('thinking', ['delta' => 'Erst suchen.']),
            $call('a'),
            $call('b'),
            $result('a'),
            $result('b'),
            $this->ev('thinking', ['delta' => 'Noch genauer.']),
            $call('c'),
            $result('c'),
            $this->ev('token', ['delta' => 'Antwort']),
            $this->ev('final', ['text' => 'Antwort']),
        ]);
        $order = [];
        foreach ($x->query('//*[@data-region="body"]/*') as $node) {
            $region = $node->getAttribute('data-region');
            if ($region === 'tools') {
                $region .= ':' . $x->query('.//*[@data-region="tool"]', $node)->length;
            }
            $order[] = $region ?: $node->getAttribute('class');
        }
        $this->assertSame(
            ['sr-only visually-hidden', 'text', 'thinking-block', 'tools:2', 'thinking-block', 'tools:1', 'text', 'footer',
                'typing'],
            $order,
            'a thinking block per step, consecutive cards grouped, the answer after them'
        );
        $texts = $x->query('//*[@data-region="body"]/*[@data-region="text"]');
        $this->assertSame('', trim($texts->item(0)->textContent), 'the shell\'s first segment stays empty, as in the client');
        $this->assertSame('Antwort', trim($texts->item(1)->textContent));
    }

    public function test_answered_question_shows_the_answer_and_text_after_it(): void {
        [$x] = $this->render([
            $this->ev('user_message', ['text' => 'Prüfung?']),
            $this->ev('question', ['interrupt_id' => 'i1', 'kind' => 'question', 'tool' => 'ask_user',
                'payload' => ['question' => 'Welcher Kurs?', 'options' => ['LF7', 'LF8']]]),
            $this->ev('user_answer', ['interrupt_id' => 'i1', 'response' => ['text' => 'LF7', 'selected' => ['LF7']]]),
            $this->ev('final', ['text' => 'Dann LF7.']),
        ]);
        $answered = $x->query('//*[contains(@class, "local-llmchat-answered")]');
        $this->assertSame(1, $answered->length);
        $this->assertSame('LF7', $x->query('.//strong', $answered->item(0))->item(0)->textContent);
        $this->assertSame(0, $x->query('//*[@data-action="option"]')->length, 'no live controls');
        $texts = $x->query('//*[@data-role="assistant"]//*[@data-region="text"]');
        $this->assertSame('Dann LF7.', trim($texts->item($texts->length - 1)->textContent), 'the answer follows the question');
    }

    public function test_answer_summaries_follow_the_client_renderers(): void {
        $this->resetAfterTest();
        $choice = ['kind' => 'choice', 'payload' => ['items' => [['id' => 'a', 'label' => 'Alpha'], ['id' => 'b']]]];
        $this->assertSame('Alpha', conversation_view::answer_summary($choice, ['id' => 'a']));
        $this->assertSame('b', conversation_view::answer_summary($choice, ['id' => 'b']));
        $this->assertSame(
            get_string('confirm:yes', 'local_llmchat'),
            conversation_view::answer_summary(['kind' => 'confirm'], ['confirmed' => true])
        );
        $this->assertSame(
            get_string('confirm:no', 'local_llmchat'),
            conversation_view::answer_summary(['kind' => 'confirm'], ['confirmed' => false])
        );
        $selected = ['text' => '', 'selected' => ['x', 'y']];
        $this->assertSame('x, y', conversation_view::answer_summary(['kind' => 'question'], $selected));
        $this->assertSame('frei', conversation_view::answer_summary(['kind' => 'other'], ['text' => 'frei']));
    }

    public function test_running_or_waiting_last_turn_is_left_to_the_client(): void {
        [$x, $lastseq] = $this->render([
            $this->ev('user_message', ['text' => 'Eins']),
            $this->ev('final', ['text' => 'Erste Antwort']),
            $this->ev('user_message', ['text' => 'Zwei'], 't2'),
            $this->ev('question', ['interrupt_id' => 'i9', 'kind' => 'question', 'tool' => 'ask_user',
                'payload' => ['question' => 'Wirklich?']], 't2'),
        ]);
        $this->assertSame(2, $lastseq);
        $this->assertSame(1, $x->query('//*[@data-role="user"]')->length);
        $this->assertStringNotContainsString('Zwei', $x->document->textContent);
        $this->assertStringNotContainsString('Wirklich?', $x->document->textContent);
    }

    public function test_nothing_finished_renders_nothing(): void {
        [, $lastseq, $html] = $this->render([$this->ev('user_message', ['text' => 'läuft']), $this->ev('token', ['delta' => 'a'])]);
        $this->assertSame('', $html);
        $this->assertSame(0, $lastseq);
    }

    public function test_cancelled_and_interrupted_turns(): void {
        [$x] = $this->render([
            $this->ev('user_message', ['text' => 'Eins']),
            $this->ev('token', ['delta' => 'Halb']),
            $this->ev('turn_interrupted'),
            $this->ev('user_message', ['text' => 'Zwei'], 't2'),
            $this->ev('token', ['delta' => 'Ab'], 't2'),
            $this->ev('cancelled', [], 't2'),
        ]);
        $notice = $x->query('//*[@data-region="notice"]');
        $this->assertSame(1, $notice->length);
        $this->assertSame(0, $x->query('//*[@data-action="notice-action"]')->length, 'an old interruption cannot be continued');
        $this->assertStringContainsString(get_string('cancelled', 'local_llmchat'), $x->document->textContent);
        $this->assertSame(2, $x->query('//*[@data-role="assistant"]')->length);
    }

    public function test_html_in_answers_is_escaped(): void {
        [$x, , $html] = $this->render([
            $this->ev('user_message', ['text' => '<img src=x onerror=alert(1)>']),
            $this->ev('final', ['text' => '<script>alert(1)</script>']),
        ]);
        $this->assertSame(0, $x->query('//script|//img')->length);
        $this->assertStringContainsString('&lt;script&gt;', $html);
    }
}
