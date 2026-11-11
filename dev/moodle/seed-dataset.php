<?php
// Dev stack only: seed the dataset described by <dataset>/manifest.json (format:
// dev/seed/README.md, built by dev/seed/build_manifest.py) into this Moodle, so the
// plugin syncs it to the API like any other content:
//
// * courses → sections → modules, created with Moodle's own generators;
// * backups/*.mbz, restored with Moodle's restore API;
// * the manifest's users (created if missing) enrolled as it says.
//
// Idempotent and convergent: everything carries the manifest's idnumber, a restart
// creates only what is missing and re-applies visibility and section restrictions.
// Deleting a course in the UI therefore makes the next start recreate it. Seeding only
// ever adds: switching to another dataset with the same prefix needs `down -v` first.
//
//   php seed-dataset.php /opt/dataset

define('CLI_SCRIPT', true);
require('/var/www/html/config.php');
require_once($CFG->libdir . '/testing/generator/lib.php');
require_once($CFG->libdir . '/clilib.php');
require_once($CFG->dirroot . '/course/lib.php');
require_once($CFG->dirroot . '/backup/util/includes/restore_includes.php');

global $DB, $USER;

const DATASET_MANIFEST_VERSION = 2;
const DATASET_PASSWORD = 'Test1234!';

// The dev config runs DEBUG_DEVELOPER; each enrolment would print a backtrace for the
// suppressed welcome mail. Real problems still surface through cli_problem() below.
set_debugging(DEBUG_NORMAL);

$root = rtrim($argv[1] ?? '/opt/dataset', '/');
$manifestfile = "$root/manifest.json";
if (!is_readable($manifestfile)) {
    cli_writeln("no dataset at $manifestfile, skipping");
    exit(0);
}
$manifest = json_decode(file_get_contents($manifestfile), true, 512, JSON_THROW_ON_ERROR);
if (($manifest['version'] ?? null) !== DATASET_MANIFEST_VERSION) {
    cli_error("$manifestfile: manifest version " . ($manifest['version'] ?? '?') . ', expected '
        . DATASET_MANIFEST_VERSION . '. Rebuild it: python dev/seed/build_manifest.py <dataset>');
}

\core\session\manager::set_user(get_admin());
$generator = new testing_data_generator();

$catidnumber = $manifest['prefix'] . '-dataset';
$category = ($DB->get_record('course_categories', ['idnumber' => $catidnumber])
    ?: core_course_category::create(['name' => $manifest['category'], 'idnumber' => $catidnumber]))->id;

/**
 * Absolute path of a dataset file: a manifest file entry ('blob' sha256 or 'path') or a
 * relative path. Refuses anything outside the dataset.
 */
function dataset_path(string $root, $file): string {
    $relative = is_array($file)
        ? ($file['path'] ?? 'blobs/' . substr($file['blob'], 0, 2) . '/' . $file['blob'])
        : $file;
    $path = realpath("$root/$relative");
    if ($path === false || strpos($path, realpath($root) . '/') !== 0) {
        throw new moodle_exception('invalidpath', 'error', '', null, "$relative (outside the dataset or missing)");
    }
    return $path;
}

/**
 * A draft area holding the given manifest files, for generators that take 'files'.
 */
function dataset_draft(string $root, array $files): int {
    global $USER;
    $fs = get_file_storage();
    $itemid = file_get_unused_draft_itemid();
    $context = context_user::instance($USER->id);
    foreach ($files as $i => $f) {
        $fs->create_file_from_pathname([
            'contextid' => $context->id, 'component' => 'user', 'filearea' => 'draft',
            'itemid' => $itemid, 'filepath' => $f['filepath'], 'filename' => $f['filename'],
            'sortorder' => $i === 0 ? 1 : 0,
        ], dataset_path($root, $f));
    }
    return $itemid;
}

/**
 * Create one module from the manifest; returns false if it already exists.
 */
function dataset_module(testing_data_generator $generator, string $root, stdClass $course,
        int $section, array $m): bool {
    global $DB;
    if ($DB->record_exists('course_modules', ['course' => $course->id, 'idnumber' => $m['idnumber']])) {
        return false;
    }
    $record = [
        'course' => $course->id,
        'section' => $section,
        'name' => $m['name'],
        'idnumber' => $m['idnumber'],
        'intro' => $m['intro'] ?? '',
        'introformat' => FORMAT_HTML,
        'showdescription' => isset($m['intro']) ? 1 : 0,
    ];
    switch ($m['modname']) {
        case 'page':
            $record['content'] = $m['content'] ?? $m['intro'] ?? '';
            $record['contentformat'] = FORMAT_HTML;
            break;
        case 'url':
            $record['externalurl'] = $m['externalurl'];
            break;
        case 'resource':
        case 'folder':
            $record['files'] = dataset_draft($root, $m['files']);
            break;
        case 'forum':
            $record['type'] = 'general';
            break;
    }
    $instance = $generator->create_module($m['modname'], $record);

    if ($m['modname'] === 'page' && !empty($m['files'])) {
        // Embedded images: straight into the page's content area (@@PLUGINFILE@@ refs).
        $context = context_module::instance($instance->cmid);
        $fs = get_file_storage();
        foreach ($m['files'] as $f) {
            $fs->create_file_from_pathname([
                'contextid' => $context->id, 'component' => 'mod_page', 'filearea' => 'content',
                'itemid' => 0, 'filepath' => $f['filepath'], 'filename' => $f['filename'],
            ], dataset_path($root, $f));
        }
    }
    if ($m['modname'] === 'book') {
        $books = $generator->get_plugin_generator('mod_book');
        foreach ($m['chapters'] as $i => $chapter) {
            $books->create_chapter([
                'bookid' => $instance->id, 'title' => $chapter['title'],
                'content' => $chapter['content'], 'contentformat' => FORMAT_HTML,
                'pagenum' => $i + 1,
            ]);
        }
    }
    return true;
}

/**
 * Converge a module's visibility on the manifest's (a restart applies a changed rule).
 */
function dataset_visibility(stdClass $course, array $m): bool {
    global $DB;
    $cm = $DB->get_record('course_modules', ['course' => $course->id, 'idnumber' => $m['idnumber']]);
    $want = ($m['visible'] ?? true) ? 1 : 0;
    if (!$cm || (int)$cm->visible === $want) {
        return false;
    }
    set_coursemodule_visible($cm->id, $want);
    \core\event\course_module_updated::create_from_cm(get_coursemodule_from_id('', $cm->id))->trigger();
    return true;
}

/**
 * Converge a section's access restriction: shown greyed out, opening on a date that
 * never arrives. Teachers bypass it (moodle/course:ignoreavailabilityrestrictions).
 */
function dataset_availability(stdClass $course, int $num, array $s): bool {
    global $DB;
    $section = $DB->get_record('course_sections', ['course' => $course->id, 'section' => $num], '*', MUST_EXIST);
    $want = isset($s['available_from']) ? json_encode([
        'op' => '&',
        'c' => [['type' => 'date', 'd' => '>=', 't' => (int)$s['available_from']]],
        'showc' => [true],
    ]) : null;
    if ($section->availability === $want) {
        return false;
    }
    course_update_section($course, $section, ['availability' => $want]);
    return true;
}

/**
 * Set a restore setting if the backup has it and it is not locked.
 */
function dataset_restore_setting(restore_controller $rc, string $name, $value): void {
    $plan = $rc->get_plan();
    if ($plan->setting_exists($name)) {
        $setting = $plan->get_setting($name);
        if ($setting->get_status() == base_setting::NOT_LOCKED) {
            $setting->set_value($value);
        }
    }
}

/**
 * Restore a .mbz into a new course in the category; returns the course id.
 */
function dataset_restore(string $file, int $category, bool $withusers): int {
    global $USER;
    $tempdir = restore_controller::get_tempdir_name(SITEID, $USER->id);
    $path = make_backup_temp_directory($tempdir);
    if (!get_file_packer('application/vnd.moodle.backup')->extract_to_pathname($file, $path)) {
        throw new moodle_exception('invalidbackupfile', 'error', '', null, $file);
    }
    [$fullname, $shortname] = restore_dbops::calculate_course_names(0,
        get_string('restoringcourse', 'backup'), get_string('restoringcourseshortname', 'backup'));
    $courseid = restore_dbops::create_new_course($fullname, $shortname, $category);
    $rc = new restore_controller($tempdir, $courseid, backup::INTERACTIVE_NO, backup::MODE_GENERAL,
        $USER->id, backup::TARGET_NEW_COURSE);
    try {
        foreach (['users', 'role_assignments', 'comments', 'userscompletion', 'logs', 'grade_histories'] as $name) {
            dataset_restore_setting($rc, $name, $withusers ? 1 : 0);
        }
        if (!$rc->execute_precheck() && !empty($rc->get_precheck_results()['errors'])) {
            throw new moodle_exception('restoreprecheckerrors', 'backup', '', null,
                implode('; ', $rc->get_precheck_results()['errors']));
        }
        $rc->execute_plan();
    } catch (Throwable $e) {
        $rc->destroy();
        delete_course($courseid, false);
        throw $e;
    }
    $rc->destroy();
    return $courseid;
}

$users = [];
foreach ($manifest['users'] as $u) {
    $users[$u['username']] = $DB->get_record('user', ['username' => $u['username']])
        ?: $generator->create_user([
            'username' => $u['username'],
            'password' => DATASET_PASSWORD,
            'firstname' => $u['firstname'] ?: $u['username'],
            'lastname' => 'Dataset',
            'email' => $u['username'] . '@example.invalid',
        ]);
}

/**
 * Enrol the manifest's users; a bad role name is reported, not fatal.
 */
function dataset_enrol(testing_data_generator $generator, array $users, int $courseid, array $enrol): void {
    foreach ($enrol as $username => $role) {
        try {
            $generator->enrol_user($users[$username]->id, $courseid, $role);
        } catch (Throwable $e) {
            cli_problem("enrol $username as $role in course $courseid: " . $e->getMessage());
        }
    }
}

$created = $restored = $changed = 0;
$started = time();
foreach ($manifest['courses'] as $c) {
    $course = $DB->get_record('course', ['idnumber' => $c['idnumber']]);
    if (!$course) {
        $course = $generator->create_course([
            'fullname' => $c['fullname'],
            'shortname' => $c['shortname'],
            'idnumber' => $c['idnumber'],
            'category' => $category,
            'format' => 'topics',
            'numsections' => max(0, count($c['sections']) - 1),
            'enablecompletion' => 0,
        ]);
    }
    foreach ($c['sections'] as $num => $s) {
        $section = $DB->get_record('course_sections', ['course' => $course->id, 'section' => $num]);
        if (!$section) {
            course_create_sections_if_missing($course, $num);
            $section = $DB->get_record('course_sections', ['course' => $course->id, 'section' => $num]);
        }
        if ($section->name !== $s['name']) {
            course_update_section($course, $section, ['name' => $s['name']]);
        }
        foreach ($s['modules'] as $m) {
            try {
                $created += dataset_module($generator, $root, $course, $num, $m) ? 1 : 0;
            } catch (Throwable $e) {
                cli_problem("course {$c['idnumber']} module {$m['idnumber']} ({$m['modname']}): "
                    . $e->getMessage());
            }
            $changed += dataset_visibility($course, $m) ? 1 : 0;
        }
        $changed += dataset_availability($course, $num, $s) ? 1 : 0;
    }
    dataset_enrol($generator, $users, $course->id, $c['enrol']);
}

foreach ($manifest['backups'] ?? [] as $b) {
    $courseid = $DB->get_field('course', 'id', ['idnumber' => $b['idnumber']]);
    if (!$courseid) {
        try {
            $courseid = dataset_restore(dataset_path($root, $b['file']), $category, !empty($manifest['restore_users']));
        } catch (Throwable $e) {
            cli_problem("backup {$b['file']}: " . $e->getMessage());
            continue;
        }
        // The idnumber is what makes the next start skip it; the backup's own is kept
        // nowhere else, which is fine for a dev copy.
        $DB->set_field('course', 'idnumber', $b['idnumber'], ['id' => $courseid]);
        $restored++;
        cli_writeln("restored {$b['file']} as course $courseid");
    }
    dataset_enrol($generator, $users, (int)$courseid, $b['enrol']);
}

rebuild_course_cache(0, true);
cli_writeln(sprintf('dataset seed done: %d courses, %d modules created, %d backups restored, '
    . '%d access changes in %ds', count($manifest['courses']) + count($manifest['backups'] ?? []),
    $created, $restored, $changed, time() - $started));
