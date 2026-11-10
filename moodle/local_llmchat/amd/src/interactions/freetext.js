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
 * Fallback renderer for unknown interaction kinds: a free-text prompt. Response: {text}.
 *
 * @module     local_llmchat/interactions/freetext
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

import {renderNode, uniqid, questionText} from 'local_llmchat/render';

/**
 * Render.
 *
 * @param {Object} payload
 * @param {Function} submit (response, summary) => void
 * @param {Object} strings {unknowninteraction}
 * @returns {Promise<HTMLElement>}
 */
export const render = async(payload, submit, strings) => {
    const node = await renderNode('interaction_freetext', {
        uniqid: uniqid('llmchat-f'),
        question: questionText(payload) || strings.unknowninteraction,
        hasoptions: false,
    });
    node.querySelector('[data-region="freeform"]').addEventListener('submit', (e) => {
        e.preventDefault();
        const text = node.querySelector('[data-region="freetext"]').value.trim();
        if (text) {
            submit({text}, text);
        }
    });
    return node;
};
