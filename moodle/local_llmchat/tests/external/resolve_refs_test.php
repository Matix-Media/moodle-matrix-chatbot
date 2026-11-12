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
 * Tests for the resolve_refs external function.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat\external;

use core_external\external_api;

/**
 * Tests for {@see resolve_refs}.
 *
 * @covers \local_llmchat\external\resolve_refs
 */
final class resolve_refs_test extends \advanced_testcase {
    public function test_resolves_for_the_current_user_only(): void {
        $this->resetAfterTest();
        $course = $this->getDataGenerator()->create_course(['shortname' => 'LF7']);
        $student = $this->getDataGenerator()->create_user();
        $other = $this->getDataGenerator()->create_user();
        $this->getDataGenerator()->enrol_user($student->id, $course->id, 'student');
        $refs = [['kind' => 'course', 'id' => (string)$course->id], ['kind' => 'module', 'id' => '999999']];

        $this->setUser($student);
        $result = external_api::clean_returnvalue(resolve_refs::execute_returns(), resolve_refs::execute($refs));
        $this->assertSame('course', $result[0]['kind']);
        $this->assertSame((string)$course->id, $result[0]['id']);
        $this->assertStringContainsString('/course/view.php?id=' . $course->id, $result[0]['url']);
        $this->assertSame('', $result[1]['url'], 'an unknown object has an empty url');

        $this->setUser($other);
        $result = external_api::clean_returnvalue(resolve_refs::execute_returns(), resolve_refs::execute($refs));
        $this->assertSame('', $result[0]['url']);
    }

    public function test_limits_the_number_of_refs(): void {
        $this->resetAfterTest();
        $this->setUser($this->getDataGenerator()->create_user());
        $this->expectException(\invalid_parameter_exception::class);
        resolve_refs::execute(array_fill(0, resolve_refs::MAX_REFS + 1, ['kind' => 'course', 'id' => '1']));
    }

    public function test_guest_refused(): void {
        $this->resetAfterTest();
        $this->setGuestUser();
        $this->expectException(\moodle_exception::class);
        resolve_refs::execute([['kind' => 'course', 'id' => '1']]);
    }
}
