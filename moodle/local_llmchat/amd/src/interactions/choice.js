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

import {renderNode, questionText} from 'local_llmchat/render';

/**
 * Render.
 *
 * @param {Object} payload {question, items: [{id, label, description?}]}
 * @param {Function} submit (response, summary) => void
 * @returns {Promise<HTMLElement>}
 */
export const render = async(payload, submit) => {
    const items = (Array.isArray(payload.items) ? payload.items : []).map((item) => ({
        id: String(item.id),
        label: String(item.label ?? item.id),
        description: item.description ? String(item.description) : '',
    }));
    const node = await renderNode('interaction_choice', {question: questionText(payload), items});
    node.querySelectorAll('[data-action="choose"]').forEach((button) => {
        const item = items.find((i) => i.id === button.dataset.id);
        button.addEventListener('click', () => submit({id: item.id}, item.label));
    });
    return node;
};
