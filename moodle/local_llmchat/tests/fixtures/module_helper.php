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
 * Test helper: create a module whose intro really is empty.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

defined('MOODLE_INTERNAL') || die();

/**
 * Create a module; the data generator replaces an empty intro with default text, so an
 * explicitly empty intro is blanked afterwards.
 *
 * @param string $modname
 * @param array $record
 * @return stdClass the generator's result (has ->cmid, ->id)
 */
function local_llmchat_test_create_module(string $modname, array $record): stdClass {
    global $DB;
    $module = testing_util::get_data_generator()->create_module($modname, $record);
    if (array_key_exists('intro', $record) && $record['intro'] === '') {
        $DB->set_field($modname, 'intro', '', ['id' => $module->id]);
        rebuild_course_cache($module->course, true);
    }
    return $module;
}
