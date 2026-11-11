<?php  // E2E-only Moodle configuration. Never use outside the test stack.
unset($CFG);
global $CFG;
$CFG = new stdClass();

$CFG->dbtype    = 'pgsql';
$CFG->dblibrary = 'native';
$CFG->dbhost    = 'postgres';
$CFG->dbname    = 'moodle';
$CFG->dbuser    = 'moodle';
$CFG->dbpass    = 'moodle';
$CFG->prefix    = 'mdl_';
$CFG->dboptions = ['dbport' => 5432];

// The browser (Playwright, on the compose network) reaches Moodle as http://moodle.
$CFG->wwwroot   = 'http://moodle';
$CFG->dataroot  = '/var/moodledata';
$CFG->admin     = 'admin';
$CFG->directorypermissions = 0777;

// Developer debugging lets local_llmchat accept the plain-http API URL of the stack.
$CFG->debug = (E_ALL | E_STRICT);
$CFG->debugdisplay = 0;
$CFG->noemailever = true;
$CFG->passwordpolicy = 0;
$CFG->cachejs = true;

require_once(__DIR__ . '/lib/setup.php');
