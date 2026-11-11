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
 * Recording HTTP transport standing in for the chat API in tests.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat\tests;

use local_llmchat\http\response;
use local_llmchat\http\transport;

/**
 * Records requests and answers them from a route table or a callback.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class fake_transport implements transport {
    /** @var array recorded requests: [method, url, headers, body] */
    public array $requests = [];

    /** @var callable|null fn(string $method, string $path, ?array $json, string $body): response */
    public $handler = null;

    /** @var array "METHOD path" => response|callable */
    public array $routes = [];

    /**
     * Constructor.
     *
     * @param callable|null $handler
     */
    public function __construct(?callable $handler = null) {
        $this->handler = $handler;
    }

    /**
     * Record and answer a request.
     *
     * @param string $method
     * @param string $url
     * @param array $headers
     * @param string|null $body
     * @return response
     */
    public function request(string $method, string $url, array $headers, ?string $body): response {
        $this->requests[] = ['method' => $method, 'url' => $url, 'headers' => $headers, 'body' => $body];
        $path = parse_url($url, PHP_URL_PATH);
        $json = $body !== null && $body !== '' ? json_decode($body, true) : null;
        $key = "$method $path";
        if (isset($this->routes[$key])) {
            $route = $this->routes[$key];
            return is_callable($route) ? $route($json, (string)$body) : $route;
        }
        if ($this->handler) {
            return ($this->handler)($method, $path, $json, (string)$body);
        }
        return new response(200, '{}');
    }

    /**
     * "METHOD path" of every recorded request.
     *
     * @return string[]
     */
    public function sequence(): array {
        return array_map(fn($r) => $r['method'] . ' ' . parse_url($r['url'], PHP_URL_PATH), $this->requests);
    }

    /**
     * Decoded JSON bodies of requests to one route.
     *
     * @param string $method
     * @param string $path
     * @return array
     */
    public function bodies(string $method, string $path): array {
        $out = [];
        foreach ($this->requests as $r) {
            if ($r['method'] === $method && parse_url($r['url'], PHP_URL_PATH) === $path) {
                $out[] = json_decode((string)$r['body'], true);
            }
        }
        return $out;
    }

    /**
     * JSON response helper.
     *
     * @param mixed $data
     * @param int $status
     * @return response
     */
    public static function json($data, int $status = 200): response {
        return new response($status, json_encode($data));
    }
}
