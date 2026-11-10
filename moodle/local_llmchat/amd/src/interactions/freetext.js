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

import {el, lock, questionText} from 'local_llmchat/interactions/dom';

/**
 * Render.
 *
 * @param {Object} payload
 * @param {Function} submit
 * @param {Object} strings
 * @returns {HTMLElement}
 */
export const render = (payload, submit, strings) => {
    const box = el('div', {className: 'local-llmchat-interaction local-llmchat-freetext'});
    box.appendChild(el('p', {className: 'fw-bold mb-2', text: questionText(payload) || strings.unknowninteraction}));
    const input = el('input', {type: 'text', className: 'form-control form-control-sm', 'aria-label': strings.answer});
    const form = el('form', {className: 'd-flex gap-2'}, [
        input, el('button', {type: 'submit', className: 'btn btn-primary btn-sm', text: strings.answer}),
    ]);
    form.addEventListener('submit', (e) => {
        e.preventDefault();
        if (input.value.trim()) {
            lock(box);
            submit({text: input.value.trim()});
        }
    });
    box.appendChild(form);
    return box;
};
