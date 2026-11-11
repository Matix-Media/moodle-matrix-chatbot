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
 * Event observers: every content change is written to the outbox, never sent directly.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

defined('MOODLE_INTERNAL') || die();

$observers = [
    ['eventname' => '\core\event\course_module_created', 'callback' => '\local_llmchat\observer::course_module_changed'],
    ['eventname' => '\core\event\course_module_updated', 'callback' => '\local_llmchat\observer::course_module_changed'],
    ['eventname' => '\core\event\course_module_deleted', 'callback' => '\local_llmchat\observer::course_module_deleted'],
    ['eventname' => '\core\event\course_created', 'callback' => '\local_llmchat\observer::course_changed'],
    ['eventname' => '\core\event\course_updated', 'callback' => '\local_llmchat\observer::course_changed'],
    ['eventname' => '\core\event\course_restored', 'callback' => '\local_llmchat\observer::course_changed'],
    ['eventname' => '\core\event\course_section_created', 'callback' => '\local_llmchat\observer::course_changed'],
    ['eventname' => '\core\event\course_section_updated', 'callback' => '\local_llmchat\observer::course_changed'],
    ['eventname' => '\core\event\course_section_deleted', 'callback' => '\local_llmchat\observer::course_changed'],
    ['eventname' => '\core\event\course_deleted', 'callback' => '\local_llmchat\observer::course_deleted'],
    ['eventname' => '\mod_book\event\chapter_created', 'callback' => '\local_llmchat\observer::course_module_changed'],
    ['eventname' => '\mod_book\event\chapter_updated', 'callback' => '\local_llmchat\observer::course_module_changed'],
    ['eventname' => '\mod_book\event\chapter_deleted', 'callback' => '\local_llmchat\observer::course_module_changed'],
];
