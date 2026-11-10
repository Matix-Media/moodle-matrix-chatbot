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
 * Template helpers: every piece of chat markup comes from a local_llmchat Mustache template,
 * so themes can override it and icons go through the theme's icon system.
 *
 * @module     local_llmchat/render
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

import Templates from 'core/templates';

let counter = 0;

/**
 * A page-unique id for aria-controls / label pairs.
 *
 * @param {string} prefix
 * @returns {string}
 */
export const uniqid = (prefix) => `${prefix}-${Date.now().toString(36)}-${++counter}`;

/**
 * Render a template to a single element.
 *
 * @param {string} name template name without component
 * @param {Object} context
 * @returns {Promise<HTMLElement>}
 */
export const renderNode = async(name, context = {}) => {
    const {html, js} = await Templates.renderForPromise(`local_llmchat/${name}`, context);
    const wrapper = document.createElement('div');
    wrapper.innerHTML = html.trim();
    const node = wrapper.firstElementChild;
    Templates.runTemplateJS(js);
    return node;
};

/**
 * Render a template (local_llmchat or core) and replace a container's content with it.
 *
 * @param {HTMLElement} container
 * @param {string} name full template name, e.g. core/notification_warning
 * @param {Object} context
 * @returns {Promise<void>}
 */
export const renderInto = async(container, name, context = {}) => {
    const {html, js} = await Templates.renderForPromise(name, context);
    Templates.replaceNodeContents(container, html, js);
};

/**
 * Question text of any interaction payload shape.
 *
 * @param {Object} payload
 * @returns {string}
 */
export const questionText = (payload) => String((payload && (payload.question || payload.message || payload.text)) || '');
