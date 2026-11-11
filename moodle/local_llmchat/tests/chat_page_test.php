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
 * Tests for the chat page renderable, the plugin icons and the templates.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat;

use local_llmchat\output\chat_page;

defined('MOODLE_INTERNAL') || die();

global $CFG;
require_once($CFG->dirroot . '/local/llmchat/lib.php');

/**
 * Tests for {@see chat_page}, {@see local_llmchat_get_fontawesome_icon_map()} and the templates.
 *
 * @covers \local_llmchat\output\chat_page
 */
final class chat_page_test extends \advanced_testcase {
    /** Templates the chat client renders, each needs a working example context. */
    private const TEMPLATES = [
        'chat_page', 'message_user', 'message_assistant', 'thinking', 'tool_call', 'tool_details', 'tool_result',
        'path_breadcrumb', 'answer_footer', 'notice', 'conversation_list', 'interaction_question', 'interaction_confirm',
        'interaction_choice', 'interaction_freetext', 'interaction_answered', 'quota_panel', 'status_page', 'quota_report',
    ];

    /**
     * Render a page context for a user.
     *
     * @param int $courseid
     * @param string $coursename
     * @return array [context, jsconfig]
     */
    private function export(int $courseid = 0, string $coursename = ''): array {
        global $PAGE;
        $page = new chat_page($courseid, $coursename);
        return [$page->export_for_template($PAGE->get_renderer('core')), $page->js_config()];
    }

    public function test_context_has_suggestions(): void {
        $this->resetAfterTest();
        $this->setUser($this->getDataGenerator()->create_user());
        [$ctx] = $this->export();
        $this->assertFalse($ctx['hascourse']);
        $this->assertArrayNotHasKey('userpicture', $ctx, 'no author icons');
        $this->assertCount(3, $ctx['suggestions']);
        foreach ($ctx['suggestions'] as $suggestion) {
            $this->assertNotEmpty($suggestion['text']);
        }
        $this->assertSame(get_string('emptyintro', 'local_llmchat'), $ctx['intro']);
    }

    public function test_messages_have_no_author_icons(): void {
        global $PAGE;
        $this->resetAfterTest();
        $this->setAdminUser();
        $output = $PAGE->get_renderer('core');
        foreach (['message_user' => ['text' => 'Hallo'], 'message_assistant' => []] as $name => $context) {
            $html = $output->render_from_template("local_llmchat/$name", $context);
            $this->assertStringNotContainsString('local-llmchat-avatar', $html, $name);
            $this->assertStringContainsString('sr-only', $html, "$name: still says who wrote it");
        }
    }

    public function test_course_context_names_the_course(): void {
        $this->resetAfterTest();
        $course = $this->getDataGenerator()->create_course(['fullname' => 'LF7 Vernetzte Systeme']);
        $this->setAdminUser();
        [$ctx, $config] = $this->export((int)$course->id, 'LF7 Vernetzte Systeme');
        $this->assertTrue($ctx['hascourse']);
        $this->assertSame('LF7 Vernetzte Systeme', $ctx['coursename']);
        $this->assertStringContainsString('/course/view.php?id=' . $course->id, $ctx['courseurl']);
        $this->assertSame(get_string('emptyintrocourse', 'local_llmchat', 'LF7 Vernetzte Systeme'), $ctx['intro']);
        $this->assertSame((int)$course->id, $config['courseid']);
    }

    public function test_js_config_carries_no_profile_pii(): void {
        $this->resetAfterTest();
        $user = $this->getDataGenerator()->create_user(['email' => 'lena@example.org', 'idnumber' => 'S-1']);
        $this->setUser($user);
        [, $config] = $this->export();
        $this->assertSame(['courseid', 'rootid', 'conversationid'], array_keys($config));
        $this->assertStringNotContainsString('lena@example.org', json_encode($config));
    }

    public function test_icon_map_registers_plugin_icons_and_svg_fallbacks_exist(): void {
        $map = local_llmchat_get_fontawesome_icon_map();
        foreach (['assistant', 'thinking', 'tool', 'conversation', 'offline', 'send', 'expand'] as $icon) {
            $this->assertArrayHasKey("local_llmchat:$icon", $map);
            $this->assertStringStartsWith('fa-', $map["local_llmchat:$icon"]);
            $this->assertFileExists(__DIR__ . "/../pix/$icon.svg");
        }
    }

    public function test_conversation_to_open_is_passed_to_the_client(): void {
        $this->resetAfterTest();
        $this->setAdminUser();
        $page = new chat_page(7, 'LF7', 'k3Yb_x-9');
        $this->assertSame('k3Yb_x-9', $page->js_config()['conversationid']);
        $this->assertSame('local-llmchat-root', $page->js_config()['rootid']);
    }

    public function test_full_url_names_the_scope_and_the_conversation(): void {
        $this->assertSame('/local/llmchat/chat.php', (new chat_page(0))->full_url()->out_as_local_url(false));
        $this->assertSame('/local/llmchat/chat.php?courseid=7', (new chat_page(7))->full_url()->out_as_local_url(false));
        $this->assertSame(
            '/local/llmchat/chat.php?courseid=7&conversation=abc',
            (new chat_page(7))->full_url('abc')->out_as_local_url(false)
        );
    }

    public function test_compact_context_links_the_full_page(): void {
        global $PAGE;
        $this->resetAfterTest();
        $this->setAdminUser();
        $page = new chat_page(7, 'LF7', '', true, 'block-llmchat-3');
        $ctx = $page->export_for_template($PAGE->get_renderer('core'));
        $config = $page->js_config();
        $this->assertTrue($ctx['compact']);
        $this->assertSame('block-llmchat-3', $ctx['rootid']);
        $this->assertSame('block-llmchat-3', $config['rootid']);
        $this->assertStringEndsWith('/local/llmchat/chat.php?courseid=7', $ctx['fullurl']);
    }

    public function test_compact_chat_has_no_list_and_links_the_full_page(): void {
        $this->resetAfterTest();
        $this->setAdminUser();
        $xpath = $this->chat_page_dom(['compact' => true, 'rootid' => 'block-llmchat-3']);
        $root = $xpath->query('//*[@id="block-llmchat-3"]')->item(0);
        $this->assertSame('compact', $root->getAttribute('data-mode'));
        $this->assertSame(0, $xpath->query('//*[@data-region="conversations"]')->length, 'only the current conversation');
        $this->assertSame(0, $xpath->query('//*[@data-action="toggle"]')->length);
        $header = $xpath->query('//*[contains(@class, "local-llmchat-header")]')->item(0);
        $actions = [];
        foreach ($xpath->query('.//button', $header) as $button) {
            $actions[] = $button->getAttribute('data-action');
        }
        $this->assertSame(['quota-toggle', 'new-conversation'], $actions);
        $expand = $xpath->query('.//a[@data-region="expand"]', $header)->item(0);
        $this->assertNotNull($expand, 'a link to the full page');
        $this->assertSame('https://moodle.example/local/llmchat/chat.php?courseid=2', $expand->getAttribute('href'));
        $this->assertNotSame('', trim($expand->getAttribute('title')));
        $this->assertSame(1, $xpath->query('.//*[@data-region="title"]', $header)->length, 'shows the conversation title');
    }

    public function test_full_chat_has_the_conversation_list(): void {
        $this->resetAfterTest();
        $this->setAdminUser();
        $xpath = $this->chat_page_dom();
        $this->assertSame('full', $xpath->query('//*[@data-region="llmchat"]')->item(0)->getAttribute('data-mode'));
        $this->assertSame(1, $xpath->query('//*[@data-region="conversations"]')->length);
        $this->assertSame(0, $xpath->query('//*[@data-region="expand"]')->length);
    }

    /**
     * The chat page rendered from its own example context, as a DOM.
     *
     * @param array $overrides context values to change
     * @return \DOMXPath
     */
    private function chat_page_dom(array $overrides = []): \DOMXPath {
        global $PAGE;
        $source = file_get_contents(__DIR__ . '/../templates/chat_page.mustache');
        preg_match_all('/{{!([\s\S]*?)}}/', $source, $blocks);
        preg_match('/Example context \(json\):([\s\S]*)/', $blocks[1][1], $m);
        $context = array_merge(json_decode($m[1], true), $overrides);
        $html = $PAGE->get_renderer('core')->render_from_template('local_llmchat/chat_page', $context);
        $dom = new \DOMDocument();
        libxml_use_internal_errors(true);
        $dom->loadHTML('<?xml encoding="utf-8"?>' . $html);
        libxml_clear_errors();
        return new \DOMXPath($dom);
    }

    public function test_header_has_only_toggle_title_and_token_ring(): void {
        $this->resetAfterTest();
        $this->setAdminUser();
        $xpath = $this->chat_page_dom();
        $header = $xpath->query('//*[contains(@class, "local-llmchat-header")]')->item(0);
        $this->assertNotNull($header);
        $this->assertSame(
            0,
            $xpath->query('.//*[contains(@class, "local-llmchat-avatar")]', $header)->length,
            'no assistant icon in the header'
        );
        $this->assertSame(0, $xpath->query('.//a', $header)->length, 'no course link in the header');
        $this->assertSame(1, $xpath->query('.//h2', $header)->length);
        $buttons = $xpath->query('.//button', $header);
        $actions = [];
        foreach ($buttons as $button) {
            $actions[] = $button->getAttribute('data-action');
        }
        $this->assertSame(['toggle', 'quota-toggle'], $actions);
    }

    public function test_token_ring_controls_a_details_panel(): void {
        $this->resetAfterTest();
        $this->setAdminUser();
        $xpath = $this->chat_page_dom();
        $button = $xpath->query('//button[@data-action="quota-toggle"]')->item(0);
        $this->assertSame('false', $button->getAttribute('aria-expanded'));
        $panelid = $button->getAttribute('aria-controls');
        $panel = $xpath->query('//*[@id="' . $panelid . '"]')->item(0);
        $this->assertNotNull($panel, 'aria-controls points at the panel');
        $this->assertSame('quota-panel', $panel->getAttribute('data-region'));
        $this->assertSame(1, $xpath->query('.//*[local-name()="svg"]//*[@data-region="quota-ring"]', $button)->length);
        $this->assertNotSame('', trim($xpath->query('.//*[contains(@class, "sr-only")]', $button)->item(0)->textContent));
    }

    public function test_every_template_renders_its_example_context(): void {
        global $PAGE;
        $this->resetAfterTest();
        $this->setAdminUser();
        $output = $PAGE->get_renderer('core');
        foreach (self::TEMPLATES as $name) {
            $source = file_get_contents(__DIR__ . "/../templates/$name.mustache");
            // Same extraction as the Moodle mustache linter: comment blocks end at the first "}}".
            preg_match_all('/{{!([\s\S]*?)}}/', $source, $blocks);
            $this->assertStringContainsString('This file is part of Moodle', $blocks[1][0], "$name: GPL header first");
            $this->assertStringNotContainsString('@template', $blocks[1][0], "$name: GPL header is closed");
            $docs = array_values(array_filter($blocks[1], fn($b) => str_contains($b, '@template')));
            $this->assertCount(1, $docs, "$name: exactly one docs block");
            $this->assertStringContainsString("@template local_llmchat/$name", $docs[0], $name);
            $this->assertMatchesRegularExpression('/Example context \(json\):([\s\S]*)/', $docs[0], $name);
            preg_match('/Example context \(json\):([\s\S]*)/', $docs[0], $m);
            $context = json_decode($m[1], true);
            $this->assertIsArray($context, "$name example context is valid JSON");
            $html = $output->render_from_template("local_llmchat/$name", $context);
            $this->assertNotEmpty(trim($html), $name);
        }
    }
}
