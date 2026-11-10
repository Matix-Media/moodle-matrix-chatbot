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
 * Form for a per-user quota override.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat\form;

defined('MOODLE_INTERNAL') || die();

global $CFG;
require_once($CFG->libdir . '/formslib.php');

/**
 * Per-user override form: empty limit = site default, 0 = unlimited.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class quota_override_form extends \moodleform {
    /**
     * Definition.
     */
    protected function definition() {
        $mform = $this->_form;
        $mform->addElement('autocomplete', 'userid', get_string('user'), $this->_customdata['users'] ?? [], [
            'ajax' => 'core_user/form_user_selector',
            'multiple' => false,
        ]);
        $mform->addRule('userid', null, 'required', null, 'client');
        $mform->setType('userid', PARAM_INT);
        foreach (['tokensperday', 'tokenspermonth'] as $field) {
            $mform->addElement('text', $field, get_string("quota:$field", 'local_llmchat'));
            $mform->setType($field, PARAM_RAW_TRIMMED);
            $mform->addHelpButton($field, 'quota:limit', 'local_llmchat');
        }
        $mform->addElement('advcheckbox', 'exempt', get_string('quota:exempt', 'local_llmchat'));
        $mform->addElement('textarea', 'note', get_string('quota:note', 'local_llmchat'), ['rows' => 2]);
        $mform->setType('note', PARAM_TEXT);
        $this->add_action_buttons(true, get_string('savechanges'));
    }

    /**
     * Validation.
     *
     * @param array $data
     * @param array $files
     * @return array
     */
    public function validation($data, $files) {
        $errors = parent::validation($data, $files);
        foreach (['tokensperday', 'tokenspermonth'] as $field) {
            $value = trim((string)($data[$field] ?? ''));
            if ($value !== '' && !preg_match('/^\d+$/', $value)) {
                $errors[$field] = get_string('error:notanumber', 'local_llmchat');
            }
        }
        return $errors;
    }
}
