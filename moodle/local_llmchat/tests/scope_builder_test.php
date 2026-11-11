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
 * Tests for the per-user scope builder and check_access.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat;

use local_llmchat\access\check_access;
use local_llmchat\access\scope_builder;
use local_llmchat\access\user_unavailable_exception;

/**
 * Tests for {@see scope_builder} and {@see check_access}.
 *
 * @covers \local_llmchat\access\scope_builder
 * @covers \local_llmchat\access\check_access
 * @covers \local_llmchat\access\scope
 */
final class scope_builder_test extends \advanced_testcase {
    /** @var \stdClass */
    private $coursea;
    /** @var \stdClass */
    private $courseb;
    /** @var \stdClass */
    private $hiddencourse;
    /** @var \stdClass */
    private $suspendedcourse;
    /** @var \stdClass */
    private $student;
    /** @var \stdClass */
    private $teacher;
    /** @var array name => cmid */
    private $cms = [];
    /** @var \stdClass */
    private $group;

    protected function setUp(): void {
        global $CFG;
        parent::setUp();
        $this->resetAfterTest();
        $CFG->enableavailability = 1;
        $gen = $this->getDataGenerator();

        $this->coursea = $gen->create_course(['numsections' => 3, 'fullname' => 'Course A', 'shortname' => 'A']);
        $this->courseb = $gen->create_course(['fullname' => 'Course B', 'shortname' => 'B']);
        $this->hiddencourse = $gen->create_course(['visible' => 0, 'shortname' => 'H']);
        $this->suspendedcourse = $gen->create_course(['shortname' => 'S']);

        $this->student = $gen->create_user();
        $this->teacher = $gen->create_user();
        $gen->enrol_user($this->student->id, $this->coursea->id, 'student');
        $gen->enrol_user($this->student->id, $this->hiddencourse->id, 'student');
        $gen->enrol_user($this->student->id, $this->suspendedcourse->id, 'student', 'manual', 0, 0, ENROL_USER_SUSPENDED);
        $gen->enrol_user($this->teacher->id, $this->coursea->id, 'editingteacher');
        $gen->enrol_user($this->teacher->id, $this->courseb->id, 'editingteacher');

        $this->group = $gen->create_group(['courseid' => $this->coursea->id, 'name' => 'Gruppe 1']);
        $other = $gen->create_group(['courseid' => $this->coursea->id, 'name' => 'Gruppe 2']);
        $gen->create_group_member(['groupid' => $other->id, 'userid' => $this->student->id]);

        $this->cms['visible'] = $gen->create_module('page', ['course' => $this->coursea->id, 'section' => 1])->cmid;
        $this->cms['hidden'] = $gen->create_module('page', ['course' => $this->coursea->id, 'section' => 1, 'visible' => 0])->cmid;
        $this->cms['insection'] = $gen->create_module('page', ['course' => $this->coursea->id, 'section' => 2])->cmid;
        $availability = json_encode(['op' => '&', 'c' => [['type' => 'group', 'id' => (int)$this->group->id]], 'showc' => [true]]);
        $this->cms['grouponly'] = $gen->create_module('page', [
            'course' => $this->coursea->id, 'section' => 1, 'availability' => $availability,
        ])->cmid;
        $this->cms['courseb'] = $gen->create_module('page', ['course' => $this->courseb->id])->cmid;
        $this->cms['hiddencourse'] = $gen->create_module('page', ['course' => $this->hiddencourse->id])->cmid;
        $this->cms['suspended'] = $gen->create_module('page', ['course' => $this->suspendedcourse->id])->cmid;
        set_section_visible($this->coursea->id, 2, 0);
        rebuild_course_cache($this->coursea->id, true);
    }

    public function test_student_scope_excludes_hidden_unenrolled_suspended_and_restricted(): void {
        $scope = (new scope_builder())->build($this->student->id);
        $this->assertSame([(int)$this->coursea->id], $scope->courses);
        $this->assertSame([(int)$this->cms['visible']], $scope->cmids);
        $this->assertSame([(string)$this->coursea->id => ['student']], $scope->roles_claim());
        $this->assertSame(['Gruppe 2'], $scope->groups[(int)$this->coursea->id]);
    }

    public function test_teacher_scope_includes_hidden_activities_and_both_courses(): void {
        $scope = (new scope_builder())->build($this->teacher->id);
        $this->assertSame([(int)$this->coursea->id, (int)$this->courseb->id], $scope->courses);
        foreach (['visible', 'hidden', 'insection', 'grouponly', 'courseb'] as $name) {
            $this->assertContains((int)$this->cms[$name], $scope->cmids, $name);
        }
        $this->assertSame(['editingteacher'], $scope->roles[(int)$this->courseb->id]);
    }

    public function test_cmids_are_sorted_unique_ints(): void {
        $scope = (new scope_builder())->build($this->teacher->id);
        $sorted = $scope->cmids;
        sort($sorted);
        $this->assertSame($sorted, $scope->cmids);
        foreach ($scope->cmids as $cmid) {
            $this->assertIsInt($cmid);
        }
    }

    public function test_included_courses_setting_limits_scope(): void {
        set_config('includedcourses', (string)$this->courseb->id, 'local_llmchat');
        $scope = (new scope_builder())->build($this->teacher->id);
        $this->assertSame([(int)$this->courseb->id], $scope->courses);
        $this->assertSame([(int)$this->cms['courseb']], $scope->cmids);
    }

    public function test_context_course_is_added_when_accessible(): void {
        $this->setAdminUser();
        $admin = get_admin();
        $scope = (new scope_builder())->build($admin->id, (int)$this->courseb->id);
        $this->assertSame([(int)$this->courseb->id], $scope->courses);
        $this->assertSame([(int)$this->cms['courseb']], $scope->cmids);
    }

    public function test_check_access_returns_visible_subset(): void {
        $all = array_map('intval', array_values($this->cms));
        $allowed = (new check_access())->check($this->student->id, array_merge($all, [999999]));
        $this->assertSame([(int)$this->cms['visible']], $allowed);
    }

    public function test_check_access_agrees_with_scope(): void {
        $all = array_map('intval', array_values($this->cms));
        foreach ([$this->student, $this->teacher] as $user) {
            $scope = (new scope_builder())->build($user->id);
            $allowed = (new check_access())->check($user->id, $all);
            $this->assertSame($scope->cmids, $allowed);
        }
    }

    public function test_group_membership_grants_restricted_cm(): void {
        $this->getDataGenerator()->create_group_member(['groupid' => $this->group->id, 'userid' => $this->student->id]);
        $allowed = (new check_access())->check($this->student->id, [(int)$this->cms['grouponly']]);
        $this->assertSame([(int)$this->cms['grouponly']], $allowed);
    }

    public function test_check_access_refuses_suspended_user(): void {
        global $DB;
        $DB->set_field('user', 'suspended', 1, ['id' => $this->student->id]);
        $this->expectException(user_unavailable_exception::class);
        (new check_access())->check($this->student->id, [(int)$this->cms['visible']]);
    }

    public function test_check_access_refuses_deleted_and_unknown_user(): void {
        delete_user($this->student);
        try {
            (new check_access())->check($this->student->id, [(int)$this->cms['visible']]);
            $this->fail('deleted user accepted');
        } catch (user_unavailable_exception $e) {
            $this->assertInstanceOf(user_unavailable_exception::class, $e);
        }
        $this->expectException(user_unavailable_exception::class);
        (new check_access())->check(987654, [1]);
    }

    public function test_check_access_empty_list(): void {
        $this->assertSame([], (new check_access())->check($this->student->id, []));
    }
}
