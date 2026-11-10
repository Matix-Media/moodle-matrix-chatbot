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
 * Renderer for kind "choice": pick one structured item. Response: {id}.
 *
 * @module     local_llmchat/interactions/choice
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

import {el, lock, questionText} from 'local_llmchat/interactions/dom';

/**
 * Render.
 *
 * @param {Object} payload {question, items: [{id, label, description?}]}
 * @param {Function} submit
 * @param {Object} strings
 * @returns {HTMLElement}
 */
export const render = (payload, submit, strings) => {
    const items = Array.isArray(payload.items) ? payload.items : [];
    const box = el('div', {className: 'local-llmchat-interaction local-llmchat-choice'});
    box.appendChild(el('p', {className: 'fw-bold mb-2', text: questionText(payload)}));
    const list = el('div', {className: 'list-group'});
    items.forEach((item) => {
        const button = el('button', {type: 'button', className: 'list-group-item list-group-item-action',
            'aria-label': `${strings.select}: ${item.label}`}, [
            el('div', {className: 'fw-bold', text: String(item.label ?? item.id)}),
        ]);
        if (item.description) {
            button.appendChild(el('div', {className: 'small text-muted', text: String(item.description)}));
        }
        button.addEventListener('click', () => {
            lock(box);
            button.classList.add('active');
            submit({id: String(item.id)});
        });
        list.appendChild(button);
    });
    box.appendChild(list);
    return box;
};
