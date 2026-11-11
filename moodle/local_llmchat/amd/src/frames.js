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
 * Builders for every frame the chat client sends. State-changing frames (send, answer,
 * continue) must carry a fresh single-use chat token, so their builders refuse to build one
 * without it; cancel and resume need none.
 *
 * @module     local_llmchat/frames
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

/**
 * Fail loudly instead of sending a frame the API would reject as unauthorized.
 *
 * @param {string} type
 * @param {string|undefined} token
 */
const requireToken = (type, token) => {
    if (typeof token !== 'string' || token === '') {
        throw new Error(`${type} frame needs a fresh chat token`);
    }
};

/**
 * A user message.
 *
 * @param {Object} args
 * @param {string} args.text
 * @param {string} args.token fresh chat token
 * @param {string} args.clientMsgId dedupes a resend after a dropped connection
 * @param {string|null} [args.conversationId] omitted for a new conversation
 * @param {number} [args.courseId] omitted outside a course
 * @returns {Object}
 */
export const send = ({text, token, clientMsgId, conversationId, courseId}) => {
    requireToken('send', token);
    return {
        type: 'send', text, token, 'client_msg_id': clientMsgId,
        ...(conversationId ? {'conversation_id': conversationId} : {}),
        ...(courseId ? {'course_id': courseId} : {}),
    };
};

/**
 * The answer to an interaction (ask_user etc.).
 *
 * @param {Object} args
 * @param {string} args.conversationId
 * @param {string} args.interruptId
 * @param {Object} args.response
 * @param {string} args.token fresh chat token
 * @returns {Object}
 */
export const answer = ({conversationId, interruptId, response, token}) => {
    requireToken('answer', token);
    return {type: 'answer', 'conversation_id': conversationId, 'interrupt_id': interruptId, response, token};
};

/**
 * Continue a turn the API reported as interrupted ("Fortsetzen").
 *
 * @param {Object} args
 * @param {string} args.conversationId
 * @param {string} args.turnId
 * @param {string} args.token fresh chat token
 * @returns {Object}
 */
export const continueTurn = ({conversationId, turnId, token}) => {
    requireToken('continue', token);
    return {type: 'continue', 'conversation_id': conversationId, 'turn_id': turnId, token};
};

/**
 * Stop the running turn.
 *
 * @param {Object} args
 * @param {string} args.conversationId
 * @returns {Object}
 */
export const cancel = ({conversationId}) => ({type: 'cancel', 'conversation_id': conversationId});

/**
 * Replay everything after lastSeq (after a reconnect or when opening a conversation).
 *
 * @param {Object} args
 * @param {string} args.conversationId
 * @param {number} [args.lastSeq]
 * @returns {Object}
 */
export const resume = ({conversationId, lastSeq}) => ({type: 'resume', 'conversation_id': conversationId,
    'last_seq': lastSeq || 0});
