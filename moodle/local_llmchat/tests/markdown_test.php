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
 * Tests for the PHP port of the answer markdown renderer.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat;

/**
 * Tests for {@see markdown}: the same vectors as amd/src/markdown.js (tests/js/markdown.test.mjs).
 *
 * @covers \local_llmchat\markdown
 */
final class markdown_test extends \basic_testcase {
    /**
     * Shared vectors.
     *
     * @return array
     */
    public static function vector_provider(): array {
        $data = json_decode(file_get_contents(__DIR__ . '/fixtures/markdown_vectors.json'), true);
        $out = [];
        foreach ($data['vectors'] as $vector) {
            $out[$vector['name']] = [$vector['markdown'], $vector['citations'], $vector['html']];
        }
        return $out;
    }

    /**
     * Renders exactly what the client renders.
     *
     * @dataProvider vector_provider
     * @param string $markdown
     * @param array $citations
     * @param string $html
     */
    public function test_matches_the_client_renderer(string $markdown, array $citations, string $html): void {
        $this->assertSame($html, markdown::render($markdown, $citations));
    }

    public function test_escape_matches_the_client(): void {
        $this->assertSame('&lt;a href=&quot;x&quot;&gt;&#39;&amp;', markdown::escape('<a href="x">\'&'));
    }

    public function test_safe_url_allows_only_http_and_https(): void {
        $this->assertSame('https://moodle.example/x', markdown::safe_url('https://moodle.example/x'));
        $this->assertSame('http://moodle.example/', markdown::safe_url('http://moodle.example/'));
        $this->assertNull(markdown::safe_url('javascript:alert(1)'));
        $this->assertNull(markdown::safe_url('ftp://moodle.example/'));
        $this->assertNull(markdown::safe_url(''));
    }
}
