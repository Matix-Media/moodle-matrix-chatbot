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
 * DOM helpers shared by the interaction renderers.
 *
 * @module     local_llmchat/interactions/dom
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

/**
 * Create an element.
 *
 * @param {string} tag
 * @param {Object} [attrs] className, text, type, and any attribute
 * @param {Array} [children]
 * @returns {HTMLElement}
 */
export const el = (tag, attrs = {}, children = []) => {
    const node = document.createElement(tag);
    Object.entries(attrs).forEach(([key, value]) => {
        if (value === undefined || value === null) {
            return;
        }
        if (key === 'className') {
            node.className = value;
        } else if (key === 'text') {
            node.textContent = value;
        } else {
            node.setAttribute(key, value);
        }
    });
    children.forEach((child) => node.appendChild(child));
    return node;
};

/**
 * Disable every control inside a form-like container (after the answer was sent).
 *
 * @param {HTMLElement} container
 */
export const lock = (container) => {
    container.querySelectorAll('button, input, textarea').forEach((c) => {
        c.disabled = true;
    });
    container.classList.add('local-llmchat-answered');
};

/**
 * Question text of any payload shape.
 *
 * @param {Object} payload
 * @returns {string}
 */
export const questionText = (payload) => String((payload && (payload.question || payload.message || payload.text)) || '');
