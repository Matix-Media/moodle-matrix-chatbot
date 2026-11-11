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

// Tests for local_llmchat/activity. Run: node --test moodle/local_llmchat/tests/js/
import {test} from 'node:test';
import assert from 'node:assert/strict';
import * as activity from '../../amd/src/activity.js';

/**
 * Feed frame types through step() and collect what the message shows after each.
 *
 * @param {string[]} types
 * @returns {Array} [{type, indicator, newThinkingBlock, endThinkingBlock}]
 */
const run = (types) => {
    let state = activity.initial();
    return types.map((type) => {
        const before = state;
        state = activity.step(state, type);
        return {
            type,
            indicator: activity.showsIndicator(state),
            newThinkingBlock: activity.startsThinking(before, state),
            endThinkingBlock: activity.endsThinking(before, state),
        };
    });
};

const indicators = (types) => run(types).map((r) => r.indicator);

test('a new turn shows the working indicator until something streams', () => {
    assert.equal(activity.showsIndicator(activity.initial()), true);
    assert.deepEqual(indicators(['token', 'final']), [false, false]);
});

test('the indicator comes back once every tool is done and the model works again', () => {
    assert.deepEqual(
        indicators(['tool_call', 'tool_call', 'tool_result', 'tool_result', 'token', 'final']),
        [false, false, false, true, false, false],
    );
});

test('a live thinking block replaces the indicator', () => {
    assert.deepEqual(indicators(['thinking', 'thinking', 'tool_call', 'tool_result', 'thinking']),
        [false, false, false, true, false]);
});

test('each model step that thinks gets its own thinking block', () => {
    const rows = run(['thinking', 'thinking', 'tool_call', 'tool_result', 'thinking', 'token', 'final']);
    assert.deepEqual(rows.map((r) => r.newThinkingBlock), [true, false, false, false, true, false, false]);
    assert.deepEqual(rows.map((r) => r.endThinkingBlock), [false, false, true, false, false, true, false]);
});

test('a thinking block ends when the turn stops for any reason', () => {
    for (const type of ['question', 'final', 'error', 'cancelled', 'turn_interrupted', 'rate_limited']) {
        const [, row] = run(['thinking', type]);
        assert.equal(row.endThinkingBlock, true, type);
        assert.equal(row.indicator, false, type);
    }
});

test('answering a question or continuing a turn shows the indicator again', () => {
    assert.deepEqual(indicators(['tool_call', 'question', 'user_answer']), [false, false, true]);
    assert.deepEqual(indicators(['turn_interrupted', 'continue', 'thinking']), [false, true, false]);
    // A fresh step after the answer: earlier tool cards no longer count as running.
    assert.deepEqual(indicators(['tool_call', 'question', 'user_answer', 'tool_call', 'tool_result']),
        [false, false, true, false, true]);
});

test('frames that change nothing on screen keep the state', () => {
    const state = activity.step(activity.initial(), 'thinking');
    for (const type of ['quota', 'citations', 'tool_progress', 'usage', 'something_new']) {
        assert.equal(activity.step(state, type), state, type);
    }
});

test('a stray tool result never drives the running count below zero', () => {
    assert.deepEqual(indicators(['tool_result', 'tool_call']), [true, false]);
});
