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
 * SHA-256 of stored files, cached by Moodle contenthash.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat\content;

/**
 * Streams a stored file through SHA-256 once per contenthash (Moodle stores SHA-1).
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
final class file_hasher {
    /**
     * SHA-256 hex digest of the file content.
     *
     * @param \stored_file $file
     * @return string
     */
    public static function sha256(\stored_file $file): string {
        $cache = \cache::make('local_llmchat', 'sha256');
        $key = $file->get_contenthash();
        $hit = $cache->get($key);
        if (is_string($hit) && strlen($hit) === 64) {
            return $hit;
        }
        $ctx = hash_init('sha256');
        $handle = $file->get_content_file_handle();
        if ($handle) {
            hash_update_stream($ctx, $handle);
            fclose($handle);
        } else {
            hash_update($ctx, $file->get_content());
        }
        $digest = hash_final($ctx);
        $cache->set($key, $digest);
        return $digest;
    }
}
