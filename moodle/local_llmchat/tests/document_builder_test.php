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
 * Tests for the per-modname document rules.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat;

use local_llmchat\content\document;
use local_llmchat\content\document_builder;

defined('MOODLE_INTERNAL') || die();

require_once(__DIR__ . '/fixtures/module_helper.php');

/**
 * Tests for {@see document_builder}.
 *
 * @covers \local_llmchat\content\document_builder
 * @covers \local_llmchat\content\document
 * @covers \local_llmchat\content\file_hasher
 */
final class document_builder_test extends \advanced_testcase {
    /** @var \stdClass */
    private $course;

    protected function setUp(): void {
        parent::setUp();
        $this->resetAfterTest();
        $this->setAdminUser();
        $this->course = $this->getDataGenerator()->create_course(['fullname' => 'Netzwerke LF7', 'numsections' => 2]);
    }

    /**
     * Build docs for one cm.
     *
     * @param int $cmid
     * @return document[] keyed by doc_id
     */
    private function build(int $cmid): array {
        $cm = get_fast_modinfo($this->course->id)->get_cm($cmid);
        $out = [];
        foreach ((new document_builder())->build($cm, get_course($this->course->id)) as $doc) {
            $out[$doc->id()] = $doc;
        }
        return $out;
    }

    /**
     * Add a file to a module's content area.
     *
     * @param int $cmid
     * @param string $component
     * @param string $filename
     * @param string $content
     * @param string $filepath
     * @return \stored_file
     */
    private function add_file(
        int $cmid,
        string $component,
        string $filename,
        string $content,
        string $filepath = '/'
    ): \stored_file {
        return get_file_storage()->create_file_from_string([
            'contextid' => \context_module::instance($cmid)->id, 'component' => $component, 'filearea' => 'content',
            'itemid' => 0, 'filepath' => $filepath, 'filename' => $filename,
        ], $content);
    }

    public function test_page_yields_html_document_with_metadata(): void {
        $page = local_llmchat_test_create_module('page', [
            'course' => $this->course->id, 'section' => 1, 'name' => 'Subnetting',
            'content' => '<p>CIDR /24 hat 254 Hosts</p>', 'intro' => '',
        ]);
        $docs = $this->build($page->cmid);
        $id = "{$this->course->id}:{$page->cmid}:page";
        $this->assertSame([$id], array_keys($docs));
        $data = $docs[$id]->to_wire();
        $this->assertSame('html', $data['kind']);
        $this->assertStringContainsString('CIDR /24 hat 254 Hosts', $data['text']);
        $this->assertSame((int)$this->course->id, $data['course_id']);
        $this->assertSame((int)$page->cmid, $data['cmid']);
        $this->assertSame('page', $data['modname']);
        $this->assertSame('Subnetting', $data['module_name']);
        $this->assertSame('Subnetting', $data['title']);
        $this->assertSame(1, $data['section_order']);
        $this->assertSame(['Netzwerke LF7', $data['section_name'], 'Subnetting'], $data['header_path']);
        $this->assertStringContainsString('/mod/page/view.php?id=' . $page->cmid, $data['module_url']);
        $this->assertSame(hash('sha256', $data['text']), $data['content_hash']);
        $this->assertIsInt($data['timemodified']);
    }

    public function test_intro_becomes_separate_document(): void {
        $assign = $this->getDataGenerator()->create_module('assign', [
            'course' => $this->course->id, 'name' => 'Abgabe 1', 'intro' => '<p>Bis Freitag abgeben</p>',
        ]);
        $docs = $this->build($assign->cmid);
        $id = "{$this->course->id}:{$assign->cmid}:intro";
        $this->assertSame([$id], array_keys($docs));
        $this->assertStringContainsString('Bis Freitag abgeben', $docs[$id]->to_wire()['text']);
    }

    public function test_empty_intro_is_skipped(): void {
        $forum = $this->getDataGenerator()->create_module('forum', [
            'course' => $this->course->id, 'intro' => '<p> </p>',
        ]);
        $this->assertSame([], $this->build($forum->cmid));
    }

    public function test_label_uses_intro_as_content(): void {
        $label = $this->getDataGenerator()->create_module('label', [
            'course' => $this->course->id, 'intro' => '<p>Klausur am 12.12.</p>',
        ]);
        $docs = array_values($this->build($label->cmid));
        $this->assertCount(1, $docs);
        $this->assertSame('html', $docs[0]->to_wire()['kind']);
        $this->assertStringContainsString('Klausur am 12.12.', $docs[0]->to_wire()['text']);
    }

    public function test_url_module_is_external(): void {
        $url = local_llmchat_test_create_module('url', [
            'course' => $this->course->id, 'name' => 'Video', 'externalurl' => 'https://www.youtube.com/watch?v=abc',
            'intro' => '',
        ]);
        $docs = $this->build($url->cmid);
        $data = $docs["{$this->course->id}:{$url->cmid}:url"]->to_wire();
        // The API's ContentKind: anything else is rejected with HTTP 422.
        $this->assertSame('external', $data['kind']);
        $this->assertSame('https://www.youtube.com/watch?v=abc', $data['external_url']);
        $this->assertArrayNotHasKey('text', $data);
        $this->assertSame(hash('sha256', 'https://www.youtube.com/watch?v=abc'), $data['content_hash']);
    }

    public function test_resource_file_document_carries_sha256_and_stored_file(): void {
        $resource = local_llmchat_test_create_module('resource', ['course' => $this->course->id, 'intro' => '']);
        $context = \context_module::instance($resource->cmid);
        get_file_storage()->delete_area_files($context->id, 'mod_resource', 'content');
        $this->add_file($resource->cmid, 'mod_resource', 'skript.pdf', '%PDF-1.4 fake');
        $docs = $this->build($resource->cmid);
        $id = "{$this->course->id}:{$resource->cmid}:file:/skript.pdf";
        $this->assertSame([$id], array_keys($docs));
        $data = $docs[$id]->to_wire();
        $this->assertSame('file', $data['kind']);
        $this->assertSame([
            'sha256' => hash('sha256', '%PDF-1.4 fake'),
            'filename' => 'skript.pdf',
            'mimetype' => 'application/pdf',
            'filesize' => 13,
        ], $data['file']);
        $this->assertSame($data['file']['sha256'], $data['content_hash']);
        $this->assertSame($data['file']['sha256'], $docs[$id]->sha256());
        $this->assertSame('%PDF-1.4 fake', $docs[$id]->file->get_content());
    }

    public function test_folder_yields_one_document_per_allowed_file(): void {
        $folder = local_llmchat_test_create_module('folder', ['course' => $this->course->id, 'name' => 'Material',
            'intro' => '']);
        $this->add_file($folder->cmid, 'mod_folder', 'a.docx', 'docx-bytes');
        $this->add_file($folder->cmid, 'mod_folder', 'b.txt', 'text', '/Unterordner/');
        $this->add_file($folder->cmid, 'mod_folder', 'tool.exe', 'MZ');
        $docs = $this->build($folder->cmid);
        $prefix = "{$this->course->id}:{$folder->cmid}:";
        $this->assertSame([$prefix . 'file:/Unterordner/b.txt', $prefix . 'file:/a.docx'], array_keys($docs));
        $data = $docs[$prefix . 'file:/Unterordner/b.txt']->to_wire();
        $this->assertSame(['Netzwerke LF7', $data['section_name'], 'Material', 'Unterordner', 'b.txt'], $data['header_path']);
        $this->assertSame('b.txt', $data['title']);
        $this->assertStringContainsString('pluginfile.php', $data['module_url']);
    }

    public function test_files_over_size_limit_are_skipped(): void {
        set_config('maxfilesize', 1, 'local_llmchat');
        $folder = local_llmchat_test_create_module('folder', ['course' => $this->course->id, 'intro' => '']);
        $this->add_file($folder->cmid, 'mod_folder', 'big.txt', str_repeat('x', 1024 * 1024 + 1));
        $this->add_file($folder->cmid, 'mod_folder', 'small.txt', 'x');
        $docs = $this->build($folder->cmid);
        $this->assertSame(["{$this->course->id}:{$folder->cmid}:file:/small.txt"], array_keys($docs));
    }

    public function test_book_yields_one_document_per_visible_chapter(): void {
        $book = local_llmchat_test_create_module('book', ['course' => $this->course->id, 'name' => 'Handbuch',
            'intro' => '']);
        $bookgen = $this->getDataGenerator()->get_plugin_generator('mod_book');
        $c1 = $bookgen->create_chapter(['bookid' => $book->id, 'title' => 'Einleitung', 'content' => '<p>Hallo</p>']);
        $c2 = $bookgen->create_chapter(['bookid' => $book->id, 'title' => 'Geheim', 'content' => 'x', 'hidden' => 1]);
        $docs = $this->build($book->cmid);
        $id = "{$this->course->id}:{$book->cmid}:chapter:{$c1->id}";
        $this->assertSame([$id], array_keys($docs));
        $data = $docs[$id]->to_wire();
        $this->assertSame('Einleitung', $data['title']);
        $this->assertSame('html', $data['kind']);
        $this->assertStringContainsString('chapterid=' . $c1->id, $data['module_url']);
        $this->assertSame('Einleitung', end($data['header_path']));
        $this->assertNotContains("{$this->course->id}:{$book->cmid}:chapter:{$c2->id}", array_keys($docs));
    }

    public function test_manifest_entry_shape(): void {
        $page = local_llmchat_test_create_module('page', ['course' => $this->course->id, 'intro' => '']);
        $doc = array_values($this->build($page->cmid))[0];
        $this->assertSame(['doc_id', 'timemodified', 'content_hash'], array_keys($doc->manifest_entry()));
    }

    public function test_sha256_is_cached_by_contenthash(): void {
        $folder = local_llmchat_test_create_module('folder', ['course' => $this->course->id, 'intro' => '']);
        $file = $this->add_file($folder->cmid, 'mod_folder', 'a.txt', 'same');
        $this->assertSame(hash('sha256', 'same'), \local_llmchat\content\file_hasher::sha256($file));
        $cache = \cache::make('local_llmchat', 'sha256');
        $this->assertSame(hash('sha256', 'same'), $cache->get($file->get_contenthash()));
    }
}
