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
 * Tests for the API client.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat;

use local_llmchat\http\response;
use local_llmchat\tests\fake_transport;

defined('MOODLE_INTERNAL') || die();

require_once(__DIR__ . '/fixtures/fake_transport.php');

/**
 * Tests for {@see api_client}.
 *
 * @covers \local_llmchat\api_client
 */
final class api_client_test extends \advanced_testcase {
    public function test_sends_bearer_secret_and_json(): void {
        $t = new fake_transport();
        $t->routes['POST /ingest/documents'] = fake_transport::json(['needs_blob' => ['abc']]);
        $client = new api_client('https://api.example/', 'sekret', $t);
        $result = $client->push_documents([['doc_id' => '1:2:intro']]);

        $this->assertSame(['needs_blob' => ['abc']], $result);
        $req = $t->requests[0];
        $this->assertSame('https://api.example/ingest/documents', $req['url']);
        $this->assertContains('Authorization: Bearer sekret', $req['headers']);
        $this->assertContains('Content-Type: application/json', $req['headers']);
        $this->assertSame('[{"doc_id":"1:2:intro"}]', $req['body']);
    }

    public function test_conversation_routes_authenticate_as_the_user(): void {
        $t = new fake_transport();
        $t->routes['GET /api/conversations'] = fake_transport::json([['id' => 'c1', 'title' => 'Frage']]);
        $t->routes['GET /api/conversations/a-b_c'] = fake_transport::json(['events' => [], 'last_seq' => 0]);
        $client = new api_client('https://api.example', 'sekret', $t);

        $this->assertSame([['id' => 'c1', 'title' => 'Frage']], $client->conversations('chat.jwt'));
        $this->assertSame(['events' => [], 'last_seq' => 0], $client->conversation('chat.jwt', 'a-b_c'));
        foreach ($t->requests as $req) {
            $this->assertSame('GET', $req['method']);
            $this->assertContains('Authorization: Bearer chat.jwt', $req['headers']);
            $this->assertStringNotContainsString('sekret', implode("\n", $req['headers']));
        }
        $this->assertSame('https://api.example/api/conversations/a-b_c', $t->requests[1]['url']);
    }

    public function test_chat_token_is_redacted_from_errors(): void {
        $t = new fake_transport();
        $t->routes['GET /api/conversations'] = new response(401, 'bad token chat.jwt');
        $client = new api_client('https://api.example', 'sekret', $t);
        try {
            $client->conversations('chat.jwt');
            $this->fail('expected api_exception');
        } catch (api_exception $e) {
            $this->assertStringNotContainsString('chat.jwt', $e->getMessage() . ' ' . $e->debuginfo);
        }
    }

    public function test_status_uses_get(): void {
        $t = new fake_transport();
        $t->routes['GET /ingest/status'] = fake_transport::json(['site_id' => 'x', 'documents' => 3]);
        $client = new api_client('https://api.example', 'sekret', $t);
        $this->assertSame(['site_id' => 'x', 'documents' => 3], $client->status());
    }

    public function test_route_helpers_hit_documented_paths(): void {
        $t = new fake_transport();
        $client = new api_client('https://api.example', 's', $t);
        $client->sync_start('sid', 'full');
        $client->push_courses([['course_id' => 1]]);
        $client->push_manifest('sid', 1, 2, []);
        $client->delete_documents(['1:2:*']);
        $client->sync_finish('sid');
        $client->put_policy(['user_tokens_per_day' => null]);
        $client->put_blob(str_repeat('a', 64), 'bytes');
        $client->usage('day', 'user');
        $this->assertSame([
            'POST /ingest/sync/start',
            'POST /ingest/courses',
            'POST /ingest/manifest',
            'POST /ingest/documents/delete',
            'POST /ingest/sync/finish',
            'PUT /ingest/policy',
            'PUT /ingest/blobs/' . str_repeat('a', 64),
            'GET /ingest/usage',
        ], $t->sequence());
        $this->assertSame(
            ['sync_id' => 'sid', 'chunk_no' => 1, 'of' => 2, 'entries' => []],
            $t->bodies('POST', '/ingest/manifest')[0]
        );
        $this->assertStringEndsWith('?period=day&by=user', end($t->requests)['url']);
        $blob = $t->requests[6];
        $this->assertSame('bytes', $blob['body']);
        $this->assertContains('Content-Type: application/octet-stream', $blob['headers']);
        $this->assertContains('Content-Digest: sha-256=:' . base64_encode(hash('sha256', 'bytes', true)) . ':', $blob['headers']);
    }

    public function test_http_url_refused_unless_debugdeveloper(): void {
        $this->resetAfterTest();
        set_debugging(DEBUG_NORMAL);
        try {
            new api_client('http://api.example', 's', new fake_transport());
            $this->fail('http accepted');
        } catch (api_exception $e) {
            $this->assertSame('error:httpsrequired', $e->errorcode);
        }
        set_debugging(DEBUG_DEVELOPER);
        $this->assertInstanceOf(api_client::class, new api_client('http://localhost:8000', 's', new fake_transport()));
    }

    public function test_non_http_urls_refused(): void {
        $this->expectException(api_exception::class);
        new api_client('ftp://api.example', 's', new fake_transport());
    }

    public function test_error_status_raises_with_status_but_never_the_secret(): void {
        $t = new fake_transport(fn() => new response(401, '{"detail":"bad secret sekret-value"}'));
        $client = new api_client('https://api.example', 'sekret-value', $t);
        try {
            $client->status();
            $this->fail('no exception');
        } catch (api_exception $e) {
            $this->assertSame(401, $e->status);
            $this->assertTrue($e->is_auth_failure());
            $this->assertStringNotContainsString('sekret-value', $e->getMessage());
            $this->assertStringNotContainsString('sekret-value', (string)$e->debuginfo);
        }
    }

    public function test_transport_error_raises_status_zero(): void {
        $t = new fake_transport(fn() => new response(0, '', 'Connection refused'));
        $client = new api_client('https://api.example', 's', $t);
        try {
            $client->status();
            $this->fail('no exception');
        } catch (api_exception $e) {
            $this->assertSame(0, $e->status);
            $this->assertFalse($e->is_auth_failure());
        }
    }

    public function test_from_config(): void {
        $this->resetAfterTest();
        $this->assertFalse(api_client::is_configured());
        set_config('apiurl', 'https://api.example', 'local_llmchat');
        secret::set('abc');
        $this->assertTrue(api_client::is_configured());
        $t = new fake_transport();
        api_client::set_test_transport($t);
        api_client::from_config()->status();
        api_client::set_test_transport(null);
        $this->assertContains('Authorization: Bearer abc', $t->requests[0]['headers']);
    }

    public function test_from_config_unconfigured_throws(): void {
        $this->resetAfterTest();
        $this->expectException(api_exception::class);
        api_client::from_config();
    }

    public function test_ws_url_derivation(): void {
        $this->assertSame('wss://api.example/base/ws/chat', api_client::ws_url('https://api.example/base/'));
        $this->assertSame('ws://localhost:8000/ws/chat', api_client::ws_url('http://localhost:8000'));
    }
}
