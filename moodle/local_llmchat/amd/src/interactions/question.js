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
 * Renderer for kind "question": free text plus optional options (single or multi select).
 * Response: {text, selected: [option…]}.
 *
 * @module     local_llmchat/interactions/question
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

import {el, lock, questionText} from 'local_llmchat/interactions/dom';

/**
 * Render.
 *
 * @param {Object} payload {question, options?, multi_select?}
 * @param {Function} submit (response) => void
 * @param {Object} strings
 * @returns {HTMLElement}
 */
export const render = (payload, submit, strings) => {
    const options = Array.isArray(payload.options) ? payload.options.map(String) : [];
    const multi = Boolean(payload.multi_select || payload.multiSelect);
    const box = el('div', {className: 'local-llmchat-interaction local-llmchat-question'});
    box.appendChild(el('p', {className: 'fw-bold mb-2', text: questionText(payload)}));
    const done = (response) => {
        lock(box);
        submit(response);
    };
    const checked = [];
    if (options.length) {
        const row = el('div', {className: 'd-flex flex-wrap gap-2 mb-2'});
        options.forEach((option, i) => {
            if (multi) {
                const id = `llmchat-opt-${Date.now()}-${i}`;
                const input = el('input', {type: 'checkbox', className: 'form-check-input', id, value: option});
                checked.push(input);
                row.appendChild(el('div', {className: 'form-check'}, [
                    input, el('label', {className: 'form-check-label', 'for': id, text: option}),
                ]));
            } else {
                const button = el('button', {type: 'button', className: 'btn btn-outline-primary btn-sm', text: option});
                button.addEventListener('click', () => done({text: option, selected: [option]}));
                row.appendChild(button);
            }
        });
        box.appendChild(row);
    }
    const input = el('input', {type: 'text', className: 'form-control form-control-sm',
        placeholder: options.length ? strings.other : strings.answer, 'aria-label': strings.answer});
    const send = el('button', {type: 'submit', className: 'btn btn-primary btn-sm', text: strings.answer});
    const form = el('form', {className: 'd-flex gap-2'}, [input, send]);
    form.addEventListener('submit', (e) => {
        e.preventDefault();
        const selected = checked.filter((c) => c.checked).map((c) => c.value);
        const text = input.value.trim();
        if (!text && !selected.length) {
            return;
        }
        done({text: text || selected.join(', '), selected});
    });
    box.appendChild(form);
    return box;
};
