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
 * Markdown renderer for answers, built on markdown-it (CommonMark plus GFM tables and
 * strikethrough). Raw HTML is escaped, only http(s) links are followed, images are not
 * embedded, and [n] markers become citation links to the module URL.
 *
 * @module     local_llmchat/markdown
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

import MarkdownIt from 'local_llmchat/markdown-it';

/**
 * Escape HTML.
 *
 * @param {string} text
 * @returns {string}
 */
export const escapeHtml = (text) => String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');

/**
 * Only http(s) URLs are linked.
 *
 * @param {string} url
 * @returns {string|null}
 */
export const safeUrl = (url) => {
    try {
        const parsed = new URL(url, window.location.href);
        return (parsed.protocol === 'https:' || parsed.protocol === 'http:') ? parsed.href : null;
    } catch (e) {
        return null;
    }
};

/**
 * Citation [n] => its link, or null when the answer has no linkable source n.
 *
 * @param {Map} citations index => citation
 * @param {number} n
 * @returns {{href: string, title: string}|null}
 */
const citationLink = (citations, n) => {
    const cite = citations.get(n);
    const href = cite && cite.url ? safeUrl(cite.url) : null;
    if (!href) {
        return null;
    }
    const pages = Array.isArray(cite.pages) && cite.pages.length ? cite.pages : [cite.page].filter(Boolean);
    return {href, title: [cite.title, pages.length ? `S. ${pages.join(', ')}` : ''].filter(Boolean).join(' · ')};
};

/**
 * Inline rule: [n] naming a known source becomes a citation token, glued to the word before it.
 *
 * @param {Object} state markdown-it StateInline; state.env.citations is the citation map
 * @param {boolean} silent
 * @returns {boolean}
 */
const citationRule = (state, silent) => {
    const match = state.src.slice(state.pos).match(/^\[(\d{1,3})\]/);
    const link = match && state.env.citations ? citationLink(state.env.citations, Number(match[1])) : null;
    if (!link) {
        return false;
    }
    if (!silent) {
        state.pending = state.pending.replace(/\s+$/, '');
        const token = state.push('citation', '', 0);
        token.content = match[1];
        token.meta = link;
    }
    state.pos += match[0].length;
    return true;
};

const md = new MarkdownIt('default', {html: false, linkify: true, breaks: true, typographer: false});
md.linkify.set({fuzzyEmail: false});
md.disable('image');
md.validateLink = (url) => /^https?:\/\//i.test(url.trim());
md.inline.ruler.before('link', 'citation', citationRule);

// Answers sit inside a page that already has h1-h3: # is h4, everything from ### on is h6.
md.core.ruler.push('demote_headings', (state) => {
    state.tokens.filter((t) => t.type === 'heading_open' || t.type === 'heading_close')
        .forEach((t) => {
            t.tag = `h${Math.min(6, Number(t.tag.slice(1)) + 3)}`;
        });
});

const rules = md.renderer.rules;
rules.link_open = (tokens, idx, options, env, self) => {
    tokens[idx].attrSet('target', '_blank');
    tokens[idx].attrSet('rel', 'noopener');
    return self.renderToken(tokens, idx, options);
};
// Wide tables scroll inside their own box instead of widening the chat.
rules.table_open = (tokens, idx, options, env, self) =>
    '<div class="local-llmchat-table">' + self.renderToken(tokens, idx, options);
rules.table_close = () => '</table></div>\n';
rules.citation = (tokens, idx) => {
    const {href, title} = tokens[idx].meta;
    const hidden = (text) => `<span class="sr-only visually-hidden">${text}</span>`;
    return `&nbsp;<a class="local-llmchat-cite" href="${escapeHtml(href)}" target="_blank" rel="noopener"` +
        ` title="${escapeHtml(title)}">${hidden('[')}${tokens[idx].content}${hidden(']')}</a>`;
};

/**
 * Render markdown to safe HTML.
 *
 * @param {string} markdown
 * @param {Array} [citations] [{index, title, url, page, ...}]
 * @returns {string}
 */
export const render = (markdown, citations = []) => {
    const cites = new Map((citations || []).map((c) => [Number(c.index), c]));
    // Models write narrow/thin spaces in times and dates ("08:30 Uhr" with U+202F); in the chat font
    // they read as a squashed gap next to normal spaces. Keep them no-break, at normal width.
    const text = String(markdown || '').replace(/[\u202f\u2009]/g, '\u00a0');
    return md.render(text, {citations: cites});
};
