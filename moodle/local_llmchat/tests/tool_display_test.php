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
 * Tests for the PHP port of the tool card display data.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat;

use local_llmchat\output\tool_display;

/**
 * Tests for {@see tool_display}: the same vectors as amd/src/tooldetails.js.
 *
 * @covers \local_llmchat\output\tool_display
 */
final class tool_display_test extends \advanced_testcase {
    /**
     * The shared vectors file.
     *
     * @return array
     */
    private static function vectors(): array {
        return json_decode(file_get_contents(__DIR__ . '/fixtures/tool_display_vectors.json'), true);
    }

    /**
     * Calls.
     *
     * @return array
     */
    public static function call_provider(): array {
        $out = [];
        foreach (self::vectors()['calls'] as $v) {
            $out[$v['name']] = [$v['args'], $v['expected']];
        }
        return $out;
    }

    /**
     * Results.
     *
     * @return array
     */
    public static function result_provider(): array {
        $out = [];
        foreach (self::vectors()['results'] as $v) {
            $out[$v['name']] = [$v['frame'], $v['expected']];
        }
        return $out;
    }

    /**
     * Links.
     *
     * @return array
     */
    public static function link_provider(): array {
        $out = [];
        foreach (self::vectors()['links'] as $v) {
            $out[$v['name']] = [$v['segments'], $v['resolved'], $v['expected']];
        }
        return $out;
    }

    /**
     * Arguments are described like the client describes them.
     *
     * @dataProvider call_provider
     * @param array $args
     * @param array $expected
     */
    public function test_describe_call_matches_the_client(array $args, array $expected): void {
        $this->resetAfterTest();
        $display = new tool_display(self::vectors()['strings']);
        $this->assertEquals($expected, $display->describe_call($args));
    }

    /**
     * Results are described like the client describes them.
     *
     * @dataProvider result_provider
     * @param array $frame
     * @param array $expected
     */
    public function test_describe_result_matches_the_client(array $frame, array $expected): void {
        $display = new tool_display(self::vectors()['strings']);
        $this->assertEquals($expected, $display->describe_result($frame));
    }

    /**
     * Breadcrumb links follow the same rule as the client.
     *
     * @dataProvider link_provider
     * @param array $segments
     * @param array|null $resolved
     * @param array $expected
     */
    public function test_link_segments_matches_the_client(array $segments, ?array $resolved, array $expected): void {
        $this->assertEquals($expected, tool_display::link_segments($segments, $resolved));
    }

    public function test_dates_are_shown_in_the_users_format(): void {
        $this->resetAfterTest();
        $display = tool_display::from_lang();
        $row = $display->describe_call(['since' => '2026-03-01'])['params'][0];
        $this->assertSame(userdate(strtotime('2026-03-01'), get_string('strftimedate', 'langconfig')), $row['text']);
        $this->assertSame(get_string('toolarg:since', 'local_llmchat'), $row['label']);
    }

    public function test_result_dates_are_shown_in_the_users_format(): void {
        $this->resetAfterTest();
        $display = tool_display::from_lang();
        $time = make_timestamp(2026, 11, 9, 12);
        $result = $display->describe_result(['display' => [
            ['kind' => 'hits', 'items' => [['cite' => 1, 'path' => '/LF7/Plan', 'modified' => $time]]],
            ['kind' => 'fields', 'items' => [['key' => 'modified', 'label' => 'x', 'value' => '2026-11-09', 'format' => 'date']]],
            ['kind' => 'entries', 'items' => [['name' => 'Skript', 'type' => 'file', 'path' => '/LF7/Skript', 'chars' => 18000]]],
        ]]);
        $date = userdate($time, get_string('strftimedate', 'langconfig'));
        $this->assertSame('LF7 · ' . $date, $result['blocks'][0]['items'][0]['meta']);
        $day = userdate(strtotime('2026-11-09'), get_string('strftimedate', 'langconfig'));
        $this->assertSame($day, $result['blocks'][1]['items'][0]['text']);
        $this->assertSame(get_string('toolfield:modified', 'local_llmchat'), $result['blocks'][1]['items'][0]['label']);
        $chars = get_string('toolresult:chars', 'local_llmchat', '18' . get_string('thousandssep', 'langconfig') . '000');
        $this->assertStringContainsString($chars, $result['blocks'][2]['items'][0]['meta']);
    }

    public function test_entries_link_to_the_last_resolved_segment(): void {
        $display = new tool_display(self::vectors()['strings']);
        $result = $display->describe_result(['display' => [['kind' => 'entries', 'items' => [
            ['name' => 'A', 'type' => 'folder', 'path' => '/X/A'],
            ['name' => 'B', 'type' => 'folder', 'path' => '/X/B'],
            ['name' => 'C', 'type' => 'folder', 'path' => '/X/C'],
        ]]]]);
        $linked = tool_display::link_entries($result, [
            '/X/A' => [['name' => 'X', 'url' => 'https://m/x'], ['name' => 'A', 'url' => 'https://m/a']],
            '/X/B' => [['name' => 'X', 'url' => 'https://m/x'], ['name' => 'B', 'url' => 'javascript:alert(1)']],
        ]);
        $items = $linked['blocks'][0]['items'];
        $links = array_map(fn($e) => [$e['url'], $e['hasurl']], $items);
        $this->assertSame([['https://m/a', true], ['', false], ['', false]], $links);
    }

    public function test_citation_meta_lists_pages_and_falls_back_to_page(): void {
        $both = ['course_name' => 'LF7', 'pages' => [2, 5], 'page' => 2];
        $this->assertSame('LF7 · Page 2, 5', tool_display::citation_meta($both, 'Page'));
        $this->assertSame('LF7 · Page 3', tool_display::citation_meta(['course_name' => 'LF7', 'page' => 3], 'Page'));
        $this->assertSame('', tool_display::citation_meta(['pages' => []], 'Page'));
    }

    public function test_label_and_icon_follow_the_manifest(): void {
        $this->assertSame('Search „Raum“', tool_display::label(
            'search_text',
            ['query' => 'Raum', 'k' => 3],
            ['label' => 'Search']
        ));
        $this->assertSame('list_dir', tool_display::label('list_dir', ['depth' => 2], []));
        $this->assertSame('Read /a (3)', tool_display::label(
            'read_file',
            ['path' => '/a', 'page' => 3],
            ['summary_template' => 'Read {path} ({page})']
        ));
        $this->assertSame(['a/search', 'core'], tool_display::icon('search_text', []));
        $this->assertSame(['tool', 'local_llmchat'], tool_display::icon('unknown', []));
        $this->assertSame(['i/star', 'core'], tool_display::icon('search_text', ['icon' => 'core:i/star']));
    }
}
