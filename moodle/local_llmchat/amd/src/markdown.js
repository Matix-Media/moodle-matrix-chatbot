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
 * Small, safe markdown renderer for answers: everything is escaped first, then a subset
 * (code, bold, italics, headings, lists, links) is re-introduced, and [n] markers become
 * citation links to the module URL.
 *
 * @module     local_llmchat/markdown
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

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
 * Inline formatting on escaped text.
 *
 * @param {string} text escaped
 * @param {Map} citations index => citation
 * @returns {string}
 */
const inline = (text, citations) => {
    // Code spans are parked behind private-use placeholders while the other rules run.
    const codes = [];
    let out = text.replace(/`([^`]+)`/g, (m, code) => {
        codes.push(code);
        return `\uE000${codes.length - 1}\uE000`;
    });
    out = out.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, (m, label, url) => {
        const href = safeUrl(url.replace(/&amp;/g, '&'));
        return href ? `<a href="${escapeHtml(href)}" target="_blank" rel="noopener">${label}</a>` : m;
    });
    out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    out = out.replace(/(^|[^*])\*([^*\s][^*]*)\*/g, '$1<em>$2</em>');
    out = out.replace(/\[(\d{1,3})\]/g, (m, n) => {
        const cite = citations.get(Number(n));
        const href = cite && cite.url ? safeUrl(cite.url) : null;
        if (!href) {
            return m;
        }
        const title = escapeHtml([cite.title, cite.page ? `S. ${cite.page}` : ''].filter(Boolean).join(' · '));
        return `<a class="local-llmchat-cite" href="${escapeHtml(href)}" target="_blank" rel="noopener"` +
            ` title="${title}">[${n}]</a>`;
    });
    return out.replace(/\uE000(\d+)\uE000/g, (m, i) => `<code>${codes[Number(i)]}</code>`);
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
    const lines = escapeHtml(markdown || '').split('\n');
    const html = [];
    let list = null;
    let para = [];
    let code = null;
    const flushPara = () => {
        if (para.length) {
            html.push(`<p>${inline(para.join('<br>'), cites)}</p>`);
            para = [];
        }
    };
    const flushList = () => {
        if (list) {
            html.push(`<${list.tag}>${list.items.map((i) => `<li>${inline(i, cites)}</li>`).join('')}</${list.tag}>`);
            list = null;
        }
    };
    for (const line of lines) {
        if (code !== null) {
            if (/^```/.test(line)) {
                html.push(`<pre><code>${code.join('\n')}</code></pre>`);
                code = null;
            } else {
                code.push(line);
            }
            continue;
        }
        if (/^```/.test(line)) {
            flushPara();
            flushList();
            code = [];
            continue;
        }
        const heading = line.match(/^(#{1,4})\s+(.*)$/);
        const bullet = line.match(/^\s*[-*]\s+(.*)$/);
        const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);
        if (heading) {
            flushPara();
            flushList();
            const level = Math.min(6, heading[1].length + 3);
            html.push(`<h${level}>${inline(heading[2], cites)}</h${level}>`);
        } else if (bullet || numbered) {
            flushPara();
            const tag = bullet ? 'ul' : 'ol';
            if (!list || list.tag !== tag) {
                flushList();
                list = {tag, items: []};
            }
            list.items.push((bullet || numbered)[1]);
        } else if (line.trim() === '') {
            flushPara();
            flushList();
        } else {
            flushList();
            para.push(line);
        }
    }
    if (code !== null) {
        html.push(`<pre><code>${code.join('\n')}</code></pre>`);
    }
    flushPara();
    flushList();
    return html.join('');
};
