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
 * What a running turn is doing right now, so its message never looks idle while it works.
 *
 * Pure: {@link step} maps the previous state and a frame type to the next state. The phases:
 *
 * - waiting: the model is working and nothing streams (new turn, after the last tool result,
 *   after an answer) — the working indicator shows.
 * - thinking: reasoning streams into a thinking block, which has its own spinner. Every step
 *   of the model that thinks gets a new block, so blocks sit between the tool cards in order.
 * - tools: at least one tool card is spinning.
 * - answering: answer text streams.
 * - idle: the turn ended or waits for the user.
 *
 * @module     local_llmchat/activity
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

/** Frame types after which the model works again without streaming anything yet. */
const RESUMING = ['user_message', 'user_answer', 'continue'];

/** Frame types that end the turn or hand it to the user. */
const STOPPING = ['question', 'final', 'error', 'cancelled', 'turn_interrupted', 'rate_limited'];

/**
 * State at the start of a turn.
 *
 * @returns {Object} {phase, runningTools}
 */
export const initial = () => ({phase: 'waiting', runningTools: 0});

/**
 * Next state after a frame; frames that change nothing on screen return the same object.
 *
 * @param {Object} state
 * @param {string} type frame type (or 'user_answer' / 'continue' for what the client sent)
 * @returns {Object}
 */
export const step = (state, type) => {
    if (RESUMING.includes(type)) {
        return initial();
    }
    if (STOPPING.includes(type)) {
        return {phase: 'idle', runningTools: 0};
    }
    switch (type) {
        case 'thinking':
            return state.phase === 'thinking' ? state : {phase: 'thinking', runningTools: state.runningTools};
        case 'token':
            return state.phase === 'answering' ? state : {phase: 'answering', runningTools: state.runningTools};
        case 'tool_call':
            return {phase: 'tools', runningTools: state.runningTools + 1};
        case 'tool_result': {
            const runningTools = Math.max(0, state.runningTools - 1);
            return {phase: runningTools > 0 ? 'tools' : 'waiting', runningTools};
        }
        default:
            return state;
    }
};

/**
 * Whether the working indicator shows.
 *
 * @param {Object} state
 * @returns {boolean}
 */
export const showsIndicator = (state) => state.phase === 'waiting';

/**
 * Whether this transition opens a new thinking block.
 *
 * @param {Object} before
 * @param {Object} after
 * @returns {boolean}
 */
export const startsThinking = (before, after) => before.phase !== 'thinking' && after.phase === 'thinking';

/**
 * Whether this transition closes the live thinking block.
 *
 * @param {Object} before
 * @param {Object} after
 * @returns {boolean}
 */
export const endsThinking = (before, after) => before.phase === 'thinking' && after.phase !== 'thinking';
