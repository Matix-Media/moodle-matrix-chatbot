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

// Tests for local_llmchat/frames. Run: node --test moodle/local_llmchat/tests/js/
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import * as frames from '../../amd/src/frames.js';

test('state-changing frames carry the fresh token', () => {
    assert.deepEqual(frames.send({text: 'Hallo', token: 'T1', clientMsgId: 'c1', conversationId: 'conv', courseId: 5}),
        {type: 'send', text: 'Hallo', token: 'T1', 'client_msg_id': 'c1', 'conversation_id': 'conv', 'course_id': 5});
    assert.deepEqual(frames.answer({conversationId: 'conv', interruptId: 'i1', response: {text: 'IPv4'}, token: 'T2'}),
        {type: 'answer', 'conversation_id': 'conv', 'interrupt_id': 'i1', response: {text: 'IPv4'}, token: 'T2'});
    assert.deepEqual(frames.continueTurn({conversationId: 'conv', turnId: 't1', token: 'T3'}),
        {type: 'continue', 'conversation_id': 'conv', 'turn_id': 't1', token: 'T3'});
});

test('a new conversation outside a course sends no empty ids', () => {
    assert.deepEqual(frames.send({text: 'x', token: 'T', clientMsgId: 'c'}),
        {type: 'send', text: 'x', token: 'T', 'client_msg_id': 'c'});
});

test('state-changing frames refuse to be built without a token', () => {
    for (const token of [undefined, null, '']) {
        assert.throws(() => frames.send({text: 'x', token, clientMsgId: 'c'}), /token/);
        assert.throws(() => frames.answer({conversationId: 'c', interruptId: 'i', response: {}, token}), /token/);
        assert.throws(() => frames.continueTurn({conversationId: 'c', turnId: 't', token}), /token/);
    }
});

test('cancel and resume need no token', () => {
    assert.deepEqual(frames.cancel({conversationId: 'conv'}), {type: 'cancel', 'conversation_id': 'conv'});
    assert.deepEqual(frames.resume({conversationId: 'conv', lastSeq: 7}), {type: 'resume', 'conversation_id': 'conv', 'last_seq': 7});
    assert.deepEqual(frames.resume({conversationId: 'conv'}), {type: 'resume', 'conversation_id': 'conv', 'last_seq': 0});
});

test('chat.js only sends frames built by local_llmchat/frames', () => {
    const source = readFileSync(new URL('../../amd/src/chat.js', import.meta.url), 'utf8');
    const sends = [...source.matchAll(/\.socket\.send\(\s*([^\s(]+)/g)].map((m) => m[1]);
    assert.ok(sends.length >= 5, `found ${sends.length} sends`);
    for (const argument of sends) {
        assert.match(argument, /^frames\.(send|answer|continueTurn|cancel|resume)$/, `socket.send(${argument}...)`);
    }
    // Every state-changing builder call gets its token from a fresh issueToken() result.
    for (const call of source.matchAll(/frames\.(send|answer|continueTurn)\(\{([^}]*)\}/g)) {
        assert.match(call[2], /token: t\.token/, `${call[1]} without token: t.token`);
    }
});
