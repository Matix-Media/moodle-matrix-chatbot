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
 * Tests for resolving agent paths to Moodle links.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat;

use local_llmchat\content\path_resolver;

defined('MOODLE_INTERNAL') || die();

require_once(__DIR__ . '/fixtures/module_helper.php');

/**
 * Tests for {@see path_resolver}.
 *
 * @covers \local_llmchat\content\path_resolver
 */
final class path_resolver_test extends \advanced_testcase {
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

    protected function setUp(): void {
        global $DB;
        parent::setUp();
        $this->resetAfterTest();
        // Content is created as admin; the resolver is always asked for an explicit user.
        $this->setAdminUser();
        $gen = $this->getDataGenerator();
        $this->course = $gen->create_course(['fullname' => 'LF7 Vernetzte Systeme', 'shortname' => 'LF7', 'numsections' => 3]);
        $section = $DB->get_record('course_sections', ['course' => $this->course->id, 'section' => 2]);
        course_update_section($this->course, $section, ['name' => 'Subnetting-Projekt']);
        $section = $DB->get_record('course_sections', ['course' => $this->course->id, 'section' => 3]);
        course_update_section($this->course, $section, ['name' => 'Geheim']);
        $this->folder = local_llmchat_test_create_module('folder', ['course' => $this->course->id, 'section' => 2,
            'name' => 'Material Projekt', 'intro' => '']);
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
        $bookgen->create_chapter(['bookid' => $this->book->id, 'title' => 'Entwurf', 'hidden' => 1]);
        local_llmchat_test_create_module('page', ['course' => $this->course->id, 'section' => 2, 'name' => 'Versteckt',
            'visible' => 0, 'intro' => '']);
        local_llmchat_test_create_module('page', ['course' => $this->course->id, 'section' => 3, 'name' => 'Lösung',
            'intro' => '']);
        set_section_visible($this->course->id, 3, 0);
        $this->student = $gen->create_user();
        $gen->enrol_user($this->student->id, $this->course->id, 'student');
        rebuild_course_cache($this->course->id, true);
    }

    /**
     * Resolve for the student.
     *
     * @param string $path
     * @return array list of url|null per segment
     */
    private function urls(string $path): array {
        return array_column((new path_resolver())->resolve($path, (int)$this->student->id), 'url');
    }

    public function test_every_segment_of_a_folder_file_path_links_to_its_target(): void {
        $segments = (new path_resolver())->resolve(
            '/LF7/Subnetting-Projekt/Material Projekt/Bewertungsraster.txt',
            (int)$this->student->id
        );
        $this->assertSame(
            ['LF7', 'Subnetting-Projekt', 'Material Projekt', 'Bewertungsraster.txt'],
            array_column($segments, 'name')
        );
        [$course, $section, $folder, $file] = array_column($segments, 'url');
        $this->assertStringContainsString('/course/view.php?id=' . $this->course->id, $course);
        $this->assertMatchesRegularExpression('#/course/(section\.php\?id=\d+|view\.php\?id=\d+.*section=2)#', $section);
        $this->assertStringContainsString('/mod/folder/view.php?id=' . $this->folder->cmid, $folder);
        $this->assertStringContainsString('/pluginfile.php/', $file);
        $this->assertStringContainsString('/mod_folder/content/0/Bewertungsraster.txt', $file);
    }

    public function test_folder_subdirectory_links_to_the_folder_and_the_file_to_the_file(): void {
        $urls = $this->urls('/LF7/Subnetting-Projekt/Material Projekt/Vorlagen/Adressplan.xlsx');
        $this->assertStringContainsString('/mod/folder/view.php?id=' . $this->folder->cmid, $urls[3]);
        $this->assertStringContainsString('/mod_folder/content/0/Vorlagen/Adressplan.xlsx', $urls[4]);
    }

    public function test_matching_ignores_case_and_accepts_course_id_or_fullname(): void {
        $this->assertNotNull($this->urls('/lf7/subnetting-projekt/material projekt')[2]);
        $this->assertNotNull($this->urls('/' . $this->course->id . '/Subnetting-Projekt')[1]);
        $this->assertNotNull($this->urls('/LF7 Vernetzte Systeme')[0]);
    }

    public function test_book_chapter_links_to_the_chapter_hidden_chapter_does_not(): void {
        $urls = $this->urls('/LF7/Subnetting-Projekt/Handbuch/OSPF');
        $this->assertStringContainsString('chapterid=' . $this->chapter->id, $urls[3]);
        $this->assertNull($this->urls('/LF7/Subnetting-Projekt/Handbuch/Entwurf')[3]);
    }

    public function test_hidden_module_and_hidden_section_are_not_linked(): void {
        $this->assertNull($this->urls('/LF7/Subnetting-Projekt/Versteckt')[2]);
        $this->assertSame([true, false, false], array_map(fn($u) => $u !== null, $this->urls('/LF7/Geheim/Lösung')));
    }

    public function test_a_teacher_gets_links_to_hidden_content(): void {
        $teacher = $this->getDataGenerator()->create_user();
        $this->getDataGenerator()->enrol_user($teacher->id, $this->course->id, 'editingteacher');
        $urls = array_column((new path_resolver())->resolve('/LF7/Geheim/Lösung', (int)$teacher->id), 'url');
        $this->assertNotContains(null, $urls);
    }

    public function test_unenrolled_user_gets_no_links_at_all(): void {
        $other = $this->getDataGenerator()->create_user();
        $urls = array_column(
            (new path_resolver())->resolve('/LF7/Subnetting-Projekt/Material Projekt', (int)$other->id),
            'url'
        );
        $this->assertSame([null, null, null], $urls);
    }

    public function test_unknown_segment_stops_linking_from_there_on(): void {
        $this->assertSame([true, false, false], array_map(
            fn($u) => $u !== null,
            $this->urls('/LF7/Gibt es nicht/Material Projekt')
        ));
        $this->assertSame([], $this->urls('/'));
    }

    public function test_excluded_course_is_not_linked(): void {
        set_config('includedcourses', '999999', 'local_llmchat');
        $this->assertSame([null], $this->urls('/LF7'));
    }
}
