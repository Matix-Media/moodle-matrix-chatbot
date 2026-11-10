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
 * Renderer for kind "question": options as buttons (checkboxes when multi-select) plus free text.
 * Response: {text, selected: [option…]}.
 *
 * @module     local_llmchat/interactions/question
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

import {renderNode, uniqid, questionText} from 'local_llmchat/render';

/**
 * Render.
 *
 * @param {Object} payload {question, options?, multi_select?}
 * @param {Function} submit (response, summary) => void
 * @returns {Promise<HTMLElement>}
 */
export const render = async(payload, submit) => {
    const {'multi_select': multiSelect} = payload;
    const options = Array.isArray(payload.options) ? payload.options.map(String) : [];
    const node = await renderNode('interaction_question', {
        uniqid: uniqid('llmchat-q'),
        question: questionText(payload),
        hasoptions: options.length > 0,
        multiselect: Boolean(multiSelect || payload.multiSelect),
        options: options.map((value, index) => ({value, index})),
    });
    node.querySelectorAll('[data-action="option"]').forEach((button) => {
        button.addEventListener('click', () => submit({text: button.dataset.value, selected: [button.dataset.value]},
            button.dataset.value));
    });
    node.querySelector('[data-region="freeform"]').addEventListener('submit', (e) => {
        e.preventDefault();
        const selected = [...node.querySelectorAll('[data-region="option"]:checked')].map((c) => c.value);
        const text = node.querySelector('[data-region="freetext"]').value.trim();
        if (!text && !selected.length) {
            return;
        }
        const summary = text || selected.join(', ');
        submit({text: summary, selected}, summary);
    });
    return node;
};
