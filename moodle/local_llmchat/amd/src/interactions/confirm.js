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
 * Renderer for kind "confirm": yes/no with explanation. Response: {confirmed: bool}.
 *
 * @module     local_llmchat/interactions/confirm
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

import {el, lock, questionText} from 'local_llmchat/interactions/dom';

/**
 * Render.
 *
 * @param {Object} payload {question|message, explanation?}
 * @param {Function} submit
 * @param {Object} strings
 * @returns {HTMLElement}
 */
export const render = (payload, submit, strings) => {
    const box = el('div', {className: 'local-llmchat-interaction local-llmchat-confirm'});
    box.appendChild(el('p', {className: 'fw-bold mb-1', text: questionText(payload)}));
    if (payload.explanation) {
        box.appendChild(el('p', {className: 'small text-muted mb-2', text: String(payload.explanation)}));
    }
    const yes = el('button', {type: 'button', className: 'btn btn-primary btn-sm', text: strings.yes});
    const no = el('button', {type: 'button', className: 'btn btn-outline-secondary btn-sm', text: strings.no});
    const answer = (confirmed) => {
        lock(box);
        submit({confirmed});
    };
    yes.addEventListener('click', () => answer(true));
    no.addEventListener('click', () => answer(false));
    box.appendChild(el('div', {className: 'd-flex gap-2'}, [yes, no]));
    return box;
};
