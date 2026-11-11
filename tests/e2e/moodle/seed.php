<?php
// E2E seed: courses, users and content with known words, plus the plugin's connection.
// Idempotent: a container restart re-runs it without duplicating anything.

define('CLI_SCRIPT', true);
require('/var/www/html/config.php');
require_once($CFG->libdir . '/testing/generator/lib.php');
require_once($CFG->libdir . '/clilib.php');

global $DB;

$generator = new testing_data_generator();
$password = 'Test1234!';

/**
 * Get or create a user by username.
 */
function e2e_user(testing_data_generator $generator, string $username, string $first, string $password): stdClass {
    global $DB;
    if ($user = $DB->get_record('user', ['username' => $username])) {
        return $user;
    }
    return $generator->create_user([
        'username' => $username,
        'password' => $password,
        'firstname' => $first,
        'lastname' => 'E2E',
        'email' => "$username@example.invalid",
    ]);
}

/**
 * Get or create a course by shortname.
 */
function e2e_course(testing_data_generator $generator, string $shortname, string $fullname): stdClass {
    global $DB;
    if ($course = $DB->get_record('course', ['shortname' => $shortname])) {
        return $course;
    }
    return $generator->create_course([
        'shortname' => $shortname,
        'fullname' => $fullname,
        'numsections' => 2,
        'enablecompletion' => 0,
    ]);
}

/**
 * Get or create a page module by name.
 */
function e2e_page(testing_data_generator $generator, stdClass $course, string $name, string $content,
        int $section = 1, int $visible = 1): void {
    global $DB;
    if ($DB->record_exists('page', ['course' => $course->id, 'name' => $name])) {
        return;
    }
    $generator->create_module('page', [
        'course' => $course->id,
        'name' => $name,
        'content' => $content,
        'contentformat' => FORMAT_HTML,
        'section' => $section,
        'visible' => $visible,
    ]);
}

$student = e2e_user($generator, 'student', 'Sophie', $password);
$other = e2e_user($generator, 'student2', 'Bruno', $password);
$teacher = e2e_user($generator, 'teacher', 'Tina', $password);

$lf7 = e2e_course($generator, 'LF7', 'Netzwerke LF7');
$pv = e2e_course($generator, 'PV', 'Prüfungsvorbereitung');

e2e_page($generator, $lf7, 'Stundenplan',
    '<p>Der Unterricht im Lernfeld 7 findet montags um 8:00 Uhr in Raum B204 statt.</p>');
e2e_page($generator, $lf7, 'VLAN-Skript',
    '<p>Ein VLAN trennt Broadcast-Domänen. Tagging nach IEEE 802.1Q.</p>', 2);
e2e_page($generator, $lf7, 'Musterlösung',
    '<p>Musterlösung Kranich: Antwort C ist richtig.</p>', 2, 0);
e2e_page($generator, $pv, 'Altklausur',
    '<p>Prüfungsfragen Zebrafink aus dem Vorjahr.</p>');

// The chat block in LF7, on the course page and its activities (block drawer).
$lf7context = context_course::instance($lf7->id);
if (!$DB->record_exists('block_instances', ['blockname' => 'llmchat', 'parentcontextid' => $lf7context->id])) {
    $generator->create_block('llmchat', [
        'parentcontextid' => $lf7context->id,
        'pagetypepattern' => '*',
        'showinsubcontexts' => 1,
        'defaultregion' => 'side-pre',
    ]);
}

$generator->enrol_user($student->id, $lf7->id, 'student');
$generator->enrol_user($other->id, $lf7->id, 'student');
$generator->enrol_user($teacher->id, $lf7->id, 'editingteacher');
$generator->enrol_user($teacher->id, $pv->id, 'editingteacher');

// Connection to the API (the secret was written by the API container on start).
$secret = trim(file_get_contents('/shared/site-secret'));
set_config('apiurl', getenv('E2E_API_URL') ?: 'http://api:8000', 'local_llmchat');
\local_llmchat\secret::set($secret);
set_config('syncenabled', 1, 'local_llmchat');

// No first-visit user tours: their backdrop would sit over the page under test.
$DB->set_field('tool_usertours_tours', 'enabled', 0);

purge_all_caches();
cli_writeln('e2e seed done: courses ' . $lf7->id . ',' . $pv->id);
