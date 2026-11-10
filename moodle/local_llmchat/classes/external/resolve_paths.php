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
 * External function linking agent paths for the current user.
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
use local_llmchat\content\path_resolver;

/**
 * AJAX: links for the paths shown in tool cards, resolved for the current user only, so the
 * chat links nothing the user could not open anyway.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class resolve_paths extends external_api {
    /** Maximum paths per call. */
    public const MAX_PATHS = 50;

    /**
     * Parameters.
     *
     * @return external_function_parameters
     */
    public static function execute_parameters(): external_function_parameters {
        return new external_function_parameters([
            'paths' => new external_multiple_structure(new external_value(PARAM_RAW, 'Agent path, e.g. /LF7/Section/Activity')),
        ]);
    }

    /**
     * Resolve.
     *
     * @param string[] $paths
     * @return array
     */
    public static function execute(array $paths): array {
        global $USER;
        ['paths' => $paths] = self::validate_parameters(self::execute_parameters(), ['paths' => $paths]);
        if (count($paths) > self::MAX_PATHS) {
            throw new \invalid_parameter_exception('Too many paths');
        }
        $context = \context_system::instance();
        self::validate_context($context);
        if (isguestuser() || !isloggedin()) {
            throw new \require_login_exception('guest');
        }
        require_capability('local/llmchat:use', $context);

        $resolver = new path_resolver();
        $out = [];
        foreach ($paths as $path) {
            $segments = [];
            foreach ($resolver->resolve(\core_text::substr($path, 0, 1000), (int)$USER->id) as $segment) {
                $segments[] = ['name' => $segment['name'], 'url' => $segment['url'] ?? ''];
            }
            $out[] = ['path' => $path, 'segments' => $segments];
        }
        return $out;
    }

    /**
     * Return structure.
     *
     * @return external_multiple_structure
     */
    public static function execute_returns(): external_multiple_structure {
        return new external_multiple_structure(new external_single_structure([
            'path' => new external_value(PARAM_RAW, 'The path as given'),
            'segments' => new external_multiple_structure(new external_single_structure([
                'name' => new external_value(PARAM_RAW, 'Segment name'),
                'url' => new external_value(PARAM_RAW, 'Link, empty when the user cannot open it'),
            ])),
        ]));
    }
}
