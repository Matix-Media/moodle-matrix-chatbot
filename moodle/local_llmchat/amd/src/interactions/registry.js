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
 * Interaction renderers by wire `kind`; unknown kinds degrade to a free-text prompt.
 *
 * @module     local_llmchat/interactions/registry
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

import * as question from 'local_llmchat/interactions/question';
import * as confirm from 'local_llmchat/interactions/confirm';
import * as choice from 'local_llmchat/interactions/choice';
import * as freetext from 'local_llmchat/interactions/freetext';

const RENDERERS = {question, confirm, choice};

/**
 * Renderer module for a kind.
 *
 * @param {string} kind
 * @returns {Object} module with async render(payload, submit, strings)
 */
export const rendererFor = (kind) => RENDERERS[kind] || freetext;
