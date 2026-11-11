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
 * Transport based on core curl.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat\http;

defined('MOODLE_INTERNAL') || die();

global $CFG;
require_once($CFG->libdir . '/filelib.php');

/**
 * Transport built on Moodle's {@see \curl} (proxy settings, security helper).
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class curl_transport implements transport {
    /**
     * Constructor.
     *
     * @param int $timeout seconds
     * @param int $connecttimeout seconds
     */
    public function __construct(
        /** @var int request timeout in seconds */
        private int $timeout = 60,
        /** @var int connect timeout in seconds */
        private int $connecttimeout = 10,
    ) {
    }

    /**
     * Send a request.
     *
     * @param string $method
     * @param string $url
     * @param array $headers
     * @param string|null $body
     * @return response
     */
    public function request(string $method, string $url, array $headers, ?string $body): response {
        // The API host is configured by the admin; the request must not be blocked by the
        // generic URL blocklist that guards user-supplied URLs.
        $curl = new \curl(['ignoresecurity' => true]);
        $curl->setHeader($headers);
        $options = [
            'CURLOPT_TIMEOUT' => $this->timeout,
            'CURLOPT_CONNECTTIMEOUT' => $this->connecttimeout,
            'CURLOPT_PROTOCOLS' => CURLPROTO_HTTPS | CURLPROTO_HTTP,
            'CURLOPT_FOLLOWLOCATION' => false,
        ];
        switch ($method) {
            case 'GET':
                $result = $curl->get($url, [], $options);
                break;
            case 'POST':
                $result = $curl->post($url, (string)$body, $options);
                break;
            default:
                $options['CURLOPT_CUSTOMREQUEST'] = $method;
                $options['CURLOPT_POSTFIELDS'] = (string)$body;
                $result = $curl->post($url, (string)$body, $options);
                break;
        }
        $info = $curl->get_info();
        $status = (int)($info['http_code'] ?? 0);
        $error = $curl->get_errno() ? (string)$curl->error : '';
        return new response($status, is_string($result) ? $result : '', $error);
    }
}
