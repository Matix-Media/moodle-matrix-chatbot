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
 * Tests for the chat block.
 *
 * @package    block_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace block_llmchat;

/**
 * Tests for {@see \block_llmchat}.
 *
 * @covers \block_llmchat
 */
final class block_llmchat_test extends \advanced_testcase {
    /**
     * Configure the chat API connection.
     */
    private function configure(): void {
        set_config('apiurl', 'https://api.example', 'local_llmchat');
        \local_llmchat\secret::set('the-secret');
    }

    /**
     * A block instance on a page of the given course.
     *
     * @param \stdClass $course
     * @param string $url page URL
     * @return array [\block_llmchat, \moodle_page]
     */
    private function block(\stdClass $course, string $url = '/course/view.php'): array {
        $context = (int)$course->id === (int)SITEID ? \context_system::instance() : \context_course::instance($course->id);
        $record = $this->getDataGenerator()->create_block('llmchat', ['parentcontextid' => $context->id]);
        $page = new \moodle_page();
        $page->set_course($course);
        $page->set_context($context);
        $page->set_url(new \moodle_url($url, ['id' => $course->id]));
        $block = block_instance('llmchat', $record, $page);
        return [$block, $page];
    }

    public function test_course_page_shows_the_compact_chat_of_that_course(): void {
        $this->resetAfterTest();
        $this->configure();
        $course = $this->getDataGenerator()->create_course(['fullname' => 'Netzwerke LF7']);
        $this->setUser($this->getDataGenerator()->create_and_enrol($course, 'student'));
        [$block, $page] = $this->block($course);

        $html = $block->get_content()->text;

        $this->assertStringContainsString('id="block-llmchat-' . $block->instance->id . '"', $html);
        $this->assertStringContainsString('data-mode="compact"', $html);
        $this->assertStringContainsString('data-courseid="' . $course->id . '"', $html);
        // The other conversations are one click away, in the full page.
        $this->assertStringContainsString('/local/llmchat/chat.php?courseid=' . $course->id, $html);
        $this->assertStringNotContainsString('data-region="conversations"', $html);
        $this->assertStringContainsString('local_llmchat/chat', $page->requires->get_end_code());
    }

    public function test_outside_courses_it_is_the_global_chat(): void {
        $this->resetAfterTest();
        $this->configure();
        $this->setUser($this->getDataGenerator()->create_user());
        [$block] = $this->block(get_site(), '/my/index.php');

        $html = $block->get_content()->text;

        $this->assertStringContainsString('data-courseid="0"', $html);
        $this->assertMatchesRegularExpression('~/local/llmchat/chat\.php"~', $html);
    }

    public function test_nothing_without_a_configured_api(): void {
        $this->resetAfterTest();
        $course = $this->getDataGenerator()->create_course();
        $this->setUser($this->getDataGenerator()->create_and_enrol($course, 'student'));
        [$block] = $this->block($course);
        $this->assertSame('', $block->get_content()->text);
    }

    public function test_nothing_for_guests(): void {
        $this->resetAfterTest();
        $this->configure();
        $course = $this->getDataGenerator()->create_course();
        $this->setGuestUser();
        [$block] = $this->block($course);
        $this->assertSame('', $block->get_content()->text);
    }

    public function test_nothing_without_the_use_capability(): void {
        global $DB;
        $this->resetAfterTest();
        $this->configure();
        $course = $this->getDataGenerator()->create_course();
        $user = $this->getDataGenerator()->create_and_enrol($course, 'student');
        $role = (int)$DB->get_field('role', 'id', ['shortname' => 'student']);
        assign_capability('local/llmchat:use', CAP_PROHIBIT, $role, \context_course::instance($course->id));
        $this->setUser($user);
        [$block] = $this->block($course);
        $this->assertSame('', $block->get_content()->text);
    }

    public function test_nothing_on_the_full_chat_page(): void {
        $this->resetAfterTest();
        $this->configure();
        $course = $this->getDataGenerator()->create_course();
        $this->setUser($this->getDataGenerator()->create_and_enrol($course, 'student'));
        [$block] = $this->block($course, '/local/llmchat/chat.php');
        $this->assertSame('', $block->get_content()->text);
    }
}
