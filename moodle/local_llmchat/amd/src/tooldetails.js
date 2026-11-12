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
 * Turns tool-call arguments and results into display data for the tool card templates:
 * friendly labels, paths as breadcrumbs, dates and relative ranges in words, lists as chips,
 * patterns as code. Pure functions (no DOM, no imports), unit-tested with node --test.
 *
 * @module     local_llmchat/tooldetails
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

/** Arguments the agent's tools use, in display order. Others follow in their own order. */
const KNOWN_ARGS = [
    'question', 'query', 'pattern', 'path', 'options', 'course_ids', 'since', 'until', 'order', 'detail',
    'offset', 'limit', 'page', 'k', 'max_matches', 'context_chars',
];

/** How a known argument's value is shown. */
const ARG_KINDS = {
    path: 'path', pattern: 'code', since: 'date', until: 'date', order: 'enum', detail: 'enum',
};

/** Enum values with a translation. */
const ENUM_VALUES = ['modified_desc', 'modified_asc'];

/** Strings this module needs (component local_llmchat). */
export const STRING_KEYS = [
    ...KNOWN_ARGS.map((key) => `toolarg:${key}`),
    ...ENUM_VALUES.map((value) => `toolval:${value}`),
    'toolval:daysago', 'toolval:yes', 'toolval:no', 'toolval:root',
];

/**
 * Fill a {$a} placeholder.
 *
 * @param {string} template
 * @param {*} a
 * @returns {string}
 */
const fill = (template, a) => String(template).replace(/\{\$a\}/g, String(a));

/**
 * "max_depth" → "Max depth".
 *
 * @param {string} key
 * @returns {string}
 */
const humanise = (key) => {
    const words = String(key).replace(/[_-]+/g, ' ').replace(/([a-z])([A-Z])/g, '$1 $2').trim().toLowerCase();
    return words.charAt(0).toUpperCase() + words.slice(1);
};

/**
 * A date-ish argument in words: ISO date, ISO date-time, or a relative range such as "-7d".
 *
 * @param {string} value
 * @param {Object} strings
 * @param {string} locale
 * @returns {string}
 */
const formatDate = (value, strings, locale) => {
    const relative = String(value).match(/^-(\d+)d$/);
    if (relative) {
        return fill(strings['toolval:daysago'], Number(relative[1]));
    }
    if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
        return new Date(`${value}T00:00:00`).toLocaleDateString(locale);
    }
    const date = new Date(value);
    return isNaN(date.getTime()) ? String(value) : date.toLocaleString(locale);
};

/**
 * One parameter row for the template.
 *
 * @param {string} key
 * @param {*} value
 * @param {Object} strings
 * @param {string} locale
 * @returns {Object|null} null when there is nothing to show
 */
const describeParam = (key, value, strings, locale) => {
    if (value === null || value === undefined || value === '') {
        return null;
    }
    const label = strings[`toolarg:${key}`] || humanise(key);
    const row = (kind, extra) => Object.assign({label, istext: false, iscode: false, ispath: false, ischips: false,
        text: '', segments: [], chips: []}, {[`is${kind}`]: true}, extra);
    const kind = ARG_KINDS[key];
    if (typeof value === 'boolean') {
        return row('text', {text: value ? strings['toolval:yes'] : strings['toolval:no']});
    }
    if (typeof value === 'number') {
        return row('text', {text: value.toLocaleString(locale)});
    }
    if (Array.isArray(value)) {
        if (value.every((v) => ['string', 'number'].includes(typeof v))) {
            return row('chips', {chips: value.map((v) => ({text: String(v)}))});
        }
        return row('code', {text: JSON.stringify(value)});
    }
    if (typeof value === 'object') {
        return row('code', {text: JSON.stringify(value)});
    }
    const text = String(value);
    if (kind === 'path') {
        const names = text.split('/').map((s) => s.trim()).filter(Boolean);
        if (!names.length) {
            return row('text', {text: strings['toolval:root']});
        }
        return row('path', {path: text,
            segments: names.map((name, i) => ({name, last: i === names.length - 1, url: '', hasurl: false}))});
    }
    if (kind === 'code') {
        return row('code', {text});
    }
    if (kind === 'date') {
        return row('text', {text: formatDate(text, strings, locale)});
    }
    if (kind === 'enum') {
        return row('text', {text: strings[`toolval:${text}`] || humanise(text)});
    }
    return row('text', {text});
};

/**
 * Display data for a tool call's arguments.
 *
 * @param {Object} args
 * @param {Object} options
 * @param {Object} options.strings loaded language strings
 * @param {string} options.locale BCP 47 locale for numbers and dates
 * @returns {Object} {hasparams, params, raw, paths} - paths: the full paths shown, for linking
 */
export const describeCall = (args, {strings, locale}) => {
    const input = args && typeof args === 'object' ? args : {};
    const keys = [
        ...KNOWN_ARGS.filter((key) => key in input),
        ...Object.keys(input).filter((key) => !KNOWN_ARGS.includes(key)),
    ];
    const params = keys.map((key) => describeParam(key, input[key], strings, locale)).filter(Boolean);
    const paths = [...new Set(params.filter((p) => p.ispath).map((p) => p.path))];
    return {hasparams: params.length > 0, params, raw: JSON.stringify(input, null, 2), paths};
};

/**
 * The pages of a citation, from `pages` or (conversations saved before it) `page`.
 *
 * @param {Object} citation {page?, pages?}
 * @returns {number[]}
 */
export const citationPages = (citation) => {
    if (Array.isArray(citation.pages) && citation.pages.length) {
        return citation.pages;
    }
    return citation.page ? [citation.page] : [];
};

/**
 * Secondary line for a cited source: course and pages, e.g. "LF7 · Page 2, 5".
 *
 * @param {Object} citation {course_name?, page?, pages?}
 * @param {string} pageWord the word for "page"
 * @returns {string}
 */
export const citationMeta = (citation, pageWord) => {
    const pages = citationPages(citation);
    const {'course_name': courseName} = citation;
    return [courseName, pages.length ? `${pageWord} ${pages.join(', ')}` : ''].filter(Boolean).join(' · ');
};

/** Display-block labels with a translation; others use the label the server sent. */
const FIELD_KEYS = ['title', 'kind', 'size', 'modified', 'link', 'note', 'date', 'weekday', 'time', 'week', 'timezone',
    'name', 'lang', 'course', 'courses', 'teaching', 'roles', 'groups'];
const ENTRY_TYPES = ['folder', 'inline', 'html', 'file', 'external'];
const NOTICE_CODES = ['no_hits', 'empty', 'no_access', 'no_text', 'no_documents', 'path_not_found',
    'moodle_unavailable', 'invalid_input', 'access_note'];

/** Strings describeResult needs (component local_llmchat). */
export const RESULT_STRING_KEYS = [
    'toolresult:entries', 'toolresult:pages', 'toolresult:chars', 'toolresult:range',
    ...FIELD_KEYS.map((key) => `toolfield:${key}`),
    ...ENTRY_TYPES.map((type) => `toolentry:${type}`),
    ...NOTICE_CODES.map((code) => `toolnotice:${code}`),
];

/**
 * Fill {$a} or {$a->name} placeholders.
 *
 * @param {string} template
 * @param {*} a a value, or an object for {$a->name}
 * @returns {string}
 */
const fillAll = (template, a) => String(template || '')
    .replace(/\{\$a->(\w+)\}/g, (m, key) => String(a && typeof a === 'object' ? a[key] ?? '' : ''))
    .replace(/\{\$a\}/g, String(a));

/**
 * An http(s) URL, or '' for anything else (javascript:, data:, relative).
 *
 * @param {*} url
 * @returns {string}
 */
const httpUrl = (url) => (/^https?:\/\//i.test(String(url || '')) ? String(url) : '');

/**
 * Path segments of a course path.
 *
 * @param {string} path
 * @returns {string[]}
 */
const segmentsOf = (path) => String(path || '').split('/').map((s) => s.trim()).filter(Boolean);

/**
 * Epoch seconds or ISO date/date-time in the user's locale ('' when unknown).
 *
 * @param {number|string|null} value
 * @param {Object} strings
 * @param {string} locale
 * @returns {string}
 */
const formatWhen = (value, strings, locale) => {
    if (value === null || value === undefined || value === '') {
        return '';
    }
    if (typeof value === 'number') {
        return new Date(value * 1000).toLocaleDateString(locale);
    }
    return formatDate(String(value), strings, locale);
};

/**
 * Number in the user's locale.
 *
 * @param {number} n
 * @param {string} locale
 * @returns {string}
 */
const num = (n, locale) => Number(n).toLocaleString(locale);

/**
 * Template context for one display block, per kind: (block, items, cites, strings, locale) => context.
 */
const BLOCKS = {
    hits: (block, items, cites, strings, locale) => ({ishits: true, items: items.map((hit) => {
        const cite = cites.get(Number(hit.cite));
        const names = segmentsOf(hit.path);
        const url = httpUrl(cite && cite.url);
        return {
            index: hit.cite ?? '',
            title: (cite && cite.title) || names[names.length - 1] || String(hit.path || ''),
            url, hasurl: url !== '',
            meta: [names.slice(0, -1).join(' › '), hit.page ? `${strings.page} ${hit.page}` : '',
                formatWhen(hit.modified, strings, locale)].filter(Boolean).join(' · '),
            snippet: String(hit.snippet || ''),
        };
    })}),
    entries: (block, items, cites, strings, locale) => ({isentries: true, items: items.map((entry) => {
        const type = String(entry.type || '');
        const counted = entry.count !== null && entry.count !== undefined;
        const size = [
            counted ? fillAll(strings['toolresult:entries'], num(entry.count, locale)) : '',
            entry.pages ? fillAll(strings['toolresult:pages'], num(entry.pages, locale)) : '',
            !entry.pages && entry.chars ? fillAll(strings['toolresult:chars'], num(entry.chars, locale)) : '',
        ];
        return {
            name: String(entry.name || ''), path: String(entry.path || ''), isfolder: type === 'folder',
            // Filled where links are resolved: the page (linkEntries) or the server-side renderer.
            url: '', hasurl: false,
            detail: String(entry.detail || ''),
            meta: [strings[`toolentry:${type}`] || humanise(type), ...size, formatWhen(entry.modified, strings, locale)]
                .filter(Boolean).join(' · '),
        };
    })}),
    fields: (block, items, cites, strings, locale) => ({isfields: true, items: items.map((field) => {
        const value = String(field.value ?? '');
        const url = field.format === 'link' ? httpUrl(value) : '';
        return {
            label: strings[`toolfield:${field.key}`] || String(field.label || humanise(field.key)),
            text: field.format === 'date' ? formatWhen(value, strings, locale) : value,
            islink: url !== '', url, iscode: field.format === 'code',
        };
    })}),
    text: (block, items, cites, strings, locale) => {
        const cite = cites.get(Number(block.cite));
        const names = segmentsOf(block.title);
        const url = httpUrl(cite && cite.url);
        const ranged = [block.start, block.end, block.total].every((n) => typeof n === 'number')
            && block.total > block.end - block.start;
        return {
            istext: true, text: String(block.text || ''), index: block.cite ?? '',
            title: (cite && cite.title) || names[names.length - 1] || '',
            url, hasurl: url !== '',
            range: ranged ? fillAll(strings['toolresult:range'],
                {start: num(block.start, locale), end: num(block.end, locale), total: num(block.total, locale)}) : '',
        };
    },
    table: (block, items, cites, strings) => ({
        istable: true,
        columns: (block.columns || []).map((c) => ({label: strings[`toolfield:${c.key}`] || String(c.label || c.key)})),
        rows: (block.rows || []).map((row) => ({cells: (row || []).map((cell) => ({text: String(cell ?? '')}))})),
    }),
    notice: (block, items, cites, strings) => {
        const level = ['info', 'warning', 'error'].includes(block.level) ? block.level : 'info';
        const translated = strings[`toolnotice:${block.code}`];
        const own = String(block.text || '');
        return {
            isnotice: true, level, iserror: level === 'error', iswarning: level === 'warning',
            text: translated || own, detail: translated && level === 'error' && own !== translated ? own : '',
        };
    },
};

/**
 * Template context for one display block, by kind. Unknown kinds are shown generically.
 *
 * @param {Object} block
 * @param {Map} cites citation index => citation
 * @param {Object} strings
 * @param {string} locale
 * @returns {Object}
 */
const describeBlock = (block, cites, strings, locale) => {
    const items = Array.isArray(block.items) ? block.items : [];
    const known = Object.prototype.hasOwnProperty.call(BLOCKS, block.kind) ? BLOCKS[block.kind] : null;
    if (known) {
        return known(block, items, cites, strings, locale);
    }
    const rest = Object.assign({}, block);
    delete rest.kind;
    return {isgeneric: true, text: typeof block.text === 'string' ? block.text : JSON.stringify(rest, null, 2)};
};

/**
 * Display data for a tool result: its display blocks, plus the sources it cited that no block shows.
 *
 * @param {Object} frame tool_result{citations?, display?}
 * @param {Object} options
 * @param {Object} options.strings loaded language strings
 * @param {string} options.locale BCP 47 locale for numbers and dates
 * @returns {Object} {hasresult, blocks, hascitations, citations, paths} - paths: entry paths, for linking
 */
export const describeResult = (frame, {strings, locale}) => {
    const all = frame.citations || [];
    const cites = new Map(all.map((c) => [Number(c.index), c]));
    const display = (frame.display || []).filter((b) => b && typeof b === 'object');
    const blocks = display.map((b) => describeBlock(b, cites, strings, locale));
    const shown = new Set(display.flatMap((b) => [b.cite, ...(b.items || []).map((i) => i && i.cite)])
        .filter((n) => n !== null && n !== undefined).map(Number));
    const citations = all.filter((c) => !shown.has(Number(c.index))).map((c) => ({
        index: c.index ?? '',
        title: c.title || c.url || '',
        meta: citationMeta(c, strings.page),
        url: c.url || '',
        hasurl: Boolean(c.url),
    }));
    const paths = [...new Set(blocks.filter((b) => b.isentries).flatMap((b) => b.items.map((e) => e.path)).filter(Boolean))];
    return {hasresult: blocks.length > 0 || citations.length > 0, blocks, hascitations: citations.length > 0, citations, paths};
};

/**
 * Attach the links the page resolved for a path (local_llmchat_resolve_paths) to its breadcrumb
 * segments. A segment is linked only when the answer has an http(s) url for a segment of the
 * same name at the same position; anything else stays plain text.
 *
 * @param {Array} segments [{name, last}]
 * @param {Array|undefined} resolved [{name, url}] from the server, url '' when not accessible
 * @returns {Array} [{name, last, url, hasurl}]
 */
export const linkSegments = (segments, resolved) => segments.map((segment, i) => {
    const answer = Array.isArray(resolved) ? resolved[i] : null;
    const same = answer && String(answer.name).toLowerCase() === String(segment.name).toLowerCase();
    const url = same && /^https?:\/\//i.test(String(answer.url || '')) ? String(answer.url) : '';
    return Object.assign({}, segment, {url, hasurl: url !== ''});
});
