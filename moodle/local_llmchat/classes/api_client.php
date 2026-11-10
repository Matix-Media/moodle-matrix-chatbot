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
 * Bearer-authenticated client for the chat API.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat;

use local_llmchat\http\curl_transport;
use local_llmchat\http\transport;

/**
 * Client for the API's /ingest/* and /api/* routes. HTTPS only (http allowed with
 * debugdeveloper for local development). Every request carries `Authorization: Bearer <secret>`.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class api_client {
    /** @var transport|null transport injected by unit tests for code paths that use from_config(); never set in production */
    private static ?transport $testtransport = null;

    /** @var string base URL without trailing slash */
    private string $baseurl;

    /** @var transport */
    private transport $transport;

    /**
     * Constructor.
     *
     * @param string $baseurl
     * @param string $secret
     * @param transport|null $transport
     * @throws api_exception on an unacceptable URL
     */
    public function __construct(string $baseurl, private string $secret, ?transport $transport = null) {
        self::validate_url($baseurl);
        $this->baseurl = rtrim($baseurl, '/');
        $this->transport = $transport ?? new curl_transport();
    }

    /**
     * Refuse anything but https (http only with debugdeveloper).
     *
     * @param string $url
     * @throws api_exception
     */
    public static function validate_url(string $url): void {
        $scheme = strtolower((string)parse_url($url, PHP_URL_SCHEME));
        $host = parse_url($url, PHP_URL_HOST);
        if (!$host || !in_array($scheme, ['https', 'http'], true)) {
            throw new api_exception('error:invalidapiurl');
        }
        if ($scheme === 'http' && !debugging('', DEBUG_DEVELOPER)) {
            throw new api_exception('error:httpsrequired');
        }
    }

    /**
     * Whether URL and secret are configured.
     *
     * @return bool
     */
    public static function is_configured(): bool {
        return trim((string)get_config('local_llmchat', 'apiurl')) !== '' && secret::is_configured();
    }

    /**
     * Client from plugin settings.
     *
     * @return self
     * @throws api_exception when not configured
     */
    public static function from_config(): self {
        $url = trim((string)get_config('local_llmchat', 'apiurl'));
        $secret = secret::get();
        if ($url === '' || $secret === null) {
            throw new api_exception('error:notconfigured');
        }
        return new self($url, $secret, self::$testtransport);
    }

    /**
     * Inject a transport for from_config() (unit tests only).
     *
     * @param transport|null $transport
     */
    public static function set_test_transport(?transport $transport): void {
        self::$testtransport = $transport;
    }

    /**
     * Websocket URL for a base URL (https → wss).
     *
     * @param string $baseurl
     * @return string
     */
    public static function ws_url(string $baseurl): string {
        $baseurl = rtrim($baseurl, '/');
        return preg_replace('#^http(s?)://#i', 'ws$1://', $baseurl) . '/ws/chat';
    }

    /**
     * Send a JSON request and decode the JSON answer.
     *
     * @param string $method
     * @param string $path
     * @param mixed $body JSON-encodable, or null
     * @param array $query
     * @return array
     * @throws api_exception
     */
    public function request(string $method, string $path, $body = null, array $query = []): array {
        $headers = ['Authorization: Bearer ' . $this->secret, 'Accept: application/json'];
        $payload = null;
        if ($body !== null) {
            $headers[] = 'Content-Type: application/json';
            $payload = json_encode($body, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_SUBSTITUTE);
        }
        return $this->send($method, $path, $headers, $payload, $query);
    }

    /**
     * Send and decode.
     *
     * @param string $method
     * @param string $path
     * @param array $headers
     * @param string|null $payload
     * @param array $query
     * @return array
     * @throws api_exception
     */
    private function send(string $method, string $path, array $headers, ?string $payload, array $query = []): array {
        $url = $this->baseurl . $path . ($query ? '?' . http_build_query($query, '', '&') : '');
        $response = $this->transport->request($method, $url, $headers, $payload);
        if ($response->status === 0) {
            throw new api_exception('error:apiunreachable', 0, $this->redact($response->error));
        }
        if ($response->status < 200 || $response->status >= 300) {
            throw new api_exception('error:apistatus', $response->status,
                $this->redact(\core_text::substr($response->body, 0, 500)), $response->status);
        }
        if (trim($response->body) === '') {
            return [];
        }
        $decoded = json_decode($response->body, true);
        return is_array($decoded) ? $decoded : [];
    }

    /**
     * Remove the secret from text that may end up in logs.
     *
     * @param string $text
     * @return string
     */
    private function redact(string $text): string {
        return str_replace($this->secret, '[redacted]', $text);
    }

    /**
     * GET /ingest/status.
     *
     * @return array
     */
    public function status(): array {
        return $this->request('GET', '/ingest/status');
    }

    /**
     * POST /ingest/sync/start.
     *
     * @param string $syncid
     * @param string $mode full|incremental
     * @return array
     */
    public function sync_start(string $syncid, string $mode): array {
        return $this->request('POST', '/ingest/sync/start', ['sync_id' => $syncid, 'mode' => $mode]);
    }

    /**
     * POST /ingest/sync/finish.
     *
     * @param string $syncid
     * @return array
     */
    public function sync_finish(string $syncid): array {
        return $this->request('POST', '/ingest/sync/finish', ['sync_id' => $syncid]);
    }

    /**
     * POST /ingest/courses.
     *
     * @param array $courses
     * @return array
     */
    public function push_courses(array $courses): array {
        return $this->request('POST', '/ingest/courses', array_values($courses));
    }

    /**
     * POST /ingest/manifest; returns {need: [doc_id], need_blobs: [sha256]}.
     *
     * @param string $syncid
     * @param int $chunkno 1-based
     * @param int $of total chunks
     * @param array $entries [{doc_id, timemodified, content_hash}]
     * @return array
     */
    public function push_manifest(string $syncid, int $chunkno, int $of, array $entries): array {
        return $this->request('POST', '/ingest/manifest',
            ['sync_id' => $syncid, 'chunk_no' => $chunkno, 'of' => $of, 'entries' => array_values($entries)]);
    }

    /**
     * POST /ingest/documents; returns {needs_blob: [sha256]}.
     *
     * @param array $documents DocumentIn records
     * @return array
     */
    public function push_documents(array $documents): array {
        return $this->request('POST', '/ingest/documents', array_values($documents));
    }

    /**
     * POST /ingest/documents/delete.
     *
     * @param string[] $docids ids; "<course>:<cmid>:*" / "<course>:*" mean all parts
     * @return array
     */
    public function delete_documents(array $docids): array {
        return $this->request('POST', '/ingest/documents/delete', array_values($docids));
    }

    /**
     * PUT /ingest/blobs/{sha256} with the raw bytes.
     *
     * @param string $sha256
     * @param string $content
     * @return array
     */
    public function put_blob(string $sha256, string $content): array {
        $headers = [
            'Authorization: Bearer ' . $this->secret,
            'Content-Type: application/octet-stream',
            'Content-Digest: sha-256=:' . base64_encode(hash('sha256', $content, true)) . ':',
            'Accept: application/json',
        ];
        return $this->send('PUT', '/ingest/blobs/' . $sha256, $headers, $content);
    }

    /**
     * PUT /ingest/policy.
     *
     * @param array $policy
     * @return array
     */
    public function put_policy(array $policy): array {
        return $this->request('PUT', '/ingest/policy', $policy);
    }

    /**
     * GET /api/usage.
     *
     * @param string $period day|month
     * @param string $by user|site
     * @return array
     */
    public function usage(string $period, string $by): array {
        return $this->request('GET', '/api/usage', null, ['period' => $period, 'by' => $by]);
    }
}
