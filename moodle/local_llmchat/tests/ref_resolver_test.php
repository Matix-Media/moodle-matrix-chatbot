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
 * Tests for linking Moodle objects by id.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat;

use local_llmchat\content\ref_resolver;

defined('MOODLE_INTERNAL') || die();

require_once(__DIR__ . '/fixtures/module_helper.php');

/**
 * Tests for {@see ref_resolver}.
 *
 * @covers \local_llmchat\content\ref_resolver
 */
final class ref_resolver_test extends \advanced_testcase {
    /** @var \stdClass */
    private $course;
    /** @var \stdClass */
    private $student;
    /** @var \stdClass */
    private $folder;
    /** @var \stdClass */
    private $book;
    /** @var \stdClass */
    private $chapter;
    /** @var \stdClass */
    private $hiddenchapter;
    /** @var \stdClass */
    private $hiddenpage;
    /** @var \stdClass */
    private $solution;
    /** @var \stdClass */
    private $project;
    /** @var \stdClass */
    private $secret;

    protected function setUp(): void {
        global $DB;
        parent::setUp();
        $this->resetAfterTest();
        // Content is created as admin; the resolver is always asked for an explicit user.
        $this->setAdminUser();
        $gen = $this->getDataGenerator();
        $this->course = $gen->create_course(['fullname' => 'LF7 Vernetzte Systeme', 'shortname' => 'LF7', 'numsections' => 3]);
        $this->project = $DB->get_record('course_sections', ['course' => $this->course->id, 'section' => 2]);
        course_update_section($this->course, $this->project, ['name' => 'Projekt / Subnetting']);
        $this->secret = $DB->get_record('course_sections', ['course' => $this->course->id, 'section' => 3]);
        course_update_section($this->course, $this->secret, ['name' => 'Projekt / Subnetting']);
        $this->folder = local_llmchat_test_create_module('folder', ['course' => $this->course->id, 'section' => 2,
            'name' => 'Material', 'intro' => '']);
        $fs = get_file_storage();
        $context = \context_module::instance($this->folder->cmid);
        foreach (['/' => 'Bewertungsraster.txt', '/Vorlagen/' => 'Adressplan.xlsx'] as $path => $name) {
            $fs->create_file_from_string(['contextid' => $context->id, 'component' => 'mod_folder', 'filearea' => 'content',
                'itemid' => 0, 'filepath' => $path, 'filename' => $name], 'x');
        }
        $this->book = local_llmchat_test_create_module('book', ['course' => $this->course->id, 'section' => 2,
            'name' => 'Handbuch', 'intro' => '']);
        $bookgen = $gen->get_plugin_generator('mod_book');
        $this->chapter = $bookgen->create_chapter(['bookid' => $this->book->id, 'title' => 'OSPF']);
        $this->hiddenchapter = $bookgen->create_chapter(['bookid' => $this->book->id, 'title' => 'Entwurf', 'hidden' => 1]);
        $this->hiddenpage = local_llmchat_test_create_module('page', ['course' => $this->course->id, 'section' => 2,
            'name' => 'Versteckt', 'visible' => 0, 'intro' => '']);
        $this->solution = local_llmchat_test_create_module('page', ['course' => $this->course->id, 'section' => 3,
            'name' => 'Lösung', 'intro' => '']);
        set_section_visible($this->course->id, 3, 0);
        $this->student = $gen->create_user();
        $gen->enrol_user($this->student->id, $this->course->id, 'student');
        rebuild_course_cache($this->course->id, true);
    }

    /**
     * Resolve refs for a user.
     *
     * @param array $refs [kind, id] pairs
     * @param int|null $userid default: the student
     * @return array "kind:id" => url
     */
    private function links(array $refs, ?int $userid = null): array {
        $refs = array_map(fn($r) => ['kind' => $r[0], 'id' => (string)$r[1]], $refs);
        return (new ref_resolver())->resolve($refs, $userid ?? (int)$this->student->id);
    }

    /**
     * The doc_id the plugin gives a document of a module.
     *
     * @param \stdClass $module
     * @param string $part
     * @return string
     */
    private function docid(\stdClass $module, string $part): string {
        return "{$this->course->id}:{$module->cmid}:{$part}";
    }

    public function test_course_section_module_and_folder_file_link_to_their_pages(): void {
        $file = $this->docid($this->folder, 'file:/Bewertungsraster.txt');
        $sub = $this->docid($this->folder, 'file:/Vorlagen/Adressplan.xlsx');
        $links = $this->links([['course', $this->course->id], ['section', $this->project->id],
            ['module', $this->folder->cmid], ['document', $file], ['document', $sub]]);
        $this->assertStringContainsString('/course/view.php?id=' . $this->course->id, $links['course:' . $this->course->id]);
        $this->assertMatchesRegularExpression(
            '#/course/(section\.php\?id=' . $this->project->id . '|view\.php\?id=\d+.*section=2)#',
            $links['section:' . $this->project->id]
        );
        $folder = 'module:' . $this->folder->cmid;
        $this->assertStringContainsString('/mod/folder/view.php?id=' . $this->folder->cmid, $links[$folder]);
        $this->assertStringContainsString('/mod_folder/content/0/Bewertungsraster.txt', $links["document:$file"]);
        $this->assertStringContainsString('/mod_folder/content/0/Vorlagen/Adressplan.xlsx', $links["document:$sub"]);
    }

    public function test_sections_with_the_same_name_link_to_themselves(): void {
        $teacher = $this->getDataGenerator()->create_user();
        $this->getDataGenerator()->enrol_user($teacher->id, $this->course->id, 'editingteacher');
        $links = $this->links([['section', $this->project->id], ['section', $this->secret->id]], (int)$teacher->id);
        $this->assertNotSame($links['section:' . $this->project->id], $links['section:' . $this->secret->id]);
    }

    public function test_book_chapter_links_to_the_chapter_hidden_chapter_does_not(): void {
        $open = $this->docid($this->book, 'chapter:' . $this->chapter->id);
        $hidden = $this->docid($this->book, 'chapter:' . $this->hiddenchapter->id);
        $links = $this->links([['document', $open], ['document', $hidden]]);
        $this->assertStringContainsString('chapterid=' . $this->chapter->id, $links["document:$open"]);
        $this->assertSame('', $links["document:$hidden"]);
    }

    public function test_hidden_module_and_hidden_section_are_not_linked(): void {
        $links = $this->links([['module', $this->hiddenpage->cmid], ['section', $this->secret->id],
            ['module', $this->solution->cmid], ['document', $this->docid($this->solution, 'page')]]);
        $this->assertSame(['', '', '', ''], array_values($links));
    }

    public function test_a_teacher_gets_links_to_hidden_content(): void {
        $teacher = $this->getDataGenerator()->create_user();
        $this->getDataGenerator()->enrol_user($teacher->id, $this->course->id, 'editingteacher');
        $hidden = $this->docid($this->book, 'chapter:' . $this->hiddenchapter->id);
        $links = $this->links(
            [['section', $this->secret->id], ['module', $this->solution->cmid], ['document', $hidden]],
            (int)$teacher->id
        );
        $this->assertNotContains('', $links);
    }

    public function test_unenrolled_user_gets_no_links_at_all(): void {
        $other = $this->getDataGenerator()->create_user();
        $links = $this->links([['course', $this->course->id], ['section', $this->project->id],
            ['module', $this->folder->cmid]], (int)$other->id);
        $this->assertSame(['', '', ''], array_values($links));
    }

    public function test_excluded_course_is_not_linked(): void {
        set_config('includedcourses', '999999', 'local_llmchat');
        $links = $this->links([['course', $this->course->id], ['module', $this->folder->cmid]]);
        $this->assertSame(['', ''], array_values($links));
    }

    public function test_unknown_and_malformed_refs_get_no_link(): void {
        $links = $this->links([
            ['course', 999999], ['section', 999999], ['module', 999999], ['module', 'abc'], ['planet', 1],
            ['document', 'nonsense'], ['document', $this->docid($this->folder, 'file:/fehlt.txt')],
            ['document', "1:{$this->folder->cmid}:intro"], ['document', $this->docid($this->book, 'chapter:999999')],
        ]);
        $this->assertCount(9, $links);
        $this->assertSame(array_fill(0, 9, ''), array_values($links));
    }

    public function test_other_documents_of_a_module_link_to_the_module(): void {
        $intro = $this->docid($this->book, 'intro');
        $links = $this->links([['document', $intro]]);
        $this->assertStringContainsString('/mod/book/view.php?id=' . $this->book->cmid, $links["document:$intro"]);
    }
}
