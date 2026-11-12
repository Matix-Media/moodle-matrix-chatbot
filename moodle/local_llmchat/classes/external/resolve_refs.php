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
 * External function linking Moodle objects by id for the current user.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat\external;

use core_external\external_api;
use core_external\external_function_parameters;
use core_external\external_multiple_structure;
use core_external\external_single_structure;
use core_external\external_value;
use local_llmchat\content\ref_resolver;

/**
 * AJAX: links for the locations shown in tool cards, by id, resolved for the current user only, so
 * the chat links nothing the user could not open anyway.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class resolve_refs extends external_api {
    /** Maximum refs per call. */
    public const MAX_REFS = 100;

    /**
     * Parameters.
     *
     * @return external_function_parameters
     */
    public static function execute_parameters(): external_function_parameters {
        return new external_function_parameters([
            'refs' => new external_multiple_structure(new external_single_structure([
                'kind' => new external_value(PARAM_ALPHA, 'course, section, module or document'),
                'id' => new external_value(PARAM_RAW, 'Course id, section id, cmid or doc_id'),
            ])),
        ]);
    }

    /**
     * Resolve.
     *
     * @param array $refs [{kind, id}]
     * @return array
     */
    public static function execute(array $refs): array {
        global $USER;
        ['refs' => $refs] = self::validate_parameters(self::execute_parameters(), ['refs' => $refs]);
        if (count($refs) > self::MAX_REFS) {
            throw new \invalid_parameter_exception('Too many refs');
        }
        $context = \context_system::instance();
        self::validate_context($context);
        if (isguestuser() || !isloggedin()) {
            throw new \require_login_exception('guest');
        }
        require_capability('local/llmchat:use', $context);

        $refs = array_map(fn($r) => ['kind' => $r['kind'], 'id' => \core_text::substr($r['id'], 0, 1000)], $refs);
        $links = (new ref_resolver())->resolve($refs, (int)$USER->id);
        return array_map(fn($r) => $r + ['url' => $links["{$r['kind']}:{$r['id']}"] ?? ''], $refs);
    }

    /**
     * Return structure.
     *
     * @return external_multiple_structure
     */
    public static function execute_returns(): external_multiple_structure {
        return new external_multiple_structure(new external_single_structure([
            'kind' => new external_value(PARAM_ALPHA, 'As given'),
            'id' => new external_value(PARAM_RAW, 'As given'),
            'url' => new external_value(PARAM_RAW, 'Link, empty when the user cannot open it'),
        ]));
    }
}
