<?php  // Local dev stack only (dev/docker-compose.yml). Never use outside it.
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

// Same URL for the host's browser and for the API's callbacks (shared namespace).
$CFG->wwwroot   = 'http://localhost:8080';
$CFG->dataroot  = '/var/moodledata';
$CFG->admin     = 'admin';
$CFG->directorypermissions = 0777;

// Developer debugging lets local_llmchat accept the plain-http API URL of the stack.
$CFG->debug = (E_ALL | E_STRICT);
$CFG->debugdisplay = 1;
$CFG->noemailever = true;
$CFG->passwordpolicy = 0;
// The plugin is bind-mounted: don't cache JS or templates, so edits show on reload.
$CFG->cachejs = false;
$CFG->cachetemplates = false;

require_once(__DIR__ . '/lib/setup.php');
