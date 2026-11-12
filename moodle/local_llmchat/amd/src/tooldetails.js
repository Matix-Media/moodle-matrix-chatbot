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
 * friendly labels, locations as breadcrumbs, dates and relative ranges in words, lists as chips,
 * patterns as code. Pure functions (no DOM, no imports), unit-tested with node --test.
 *
 * Places in Moodle arrive as locations - [{kind, id, name}] from the course down, resolved by the
 * API - never as paths to parse: breadcrumbs show the names and are linked by "kind:id" refs.
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

/** Kinds of location segments. */
const SEGMENT_KINDS = ['course', 'section', 'module', 'document'];

/**
 * The well-formed segments of a location ([] for anything else).
 *
 * @param {*} location
 * @returns {Array} [{kind, id, name}]
 */
const segmentsOf = (location) => (Array.isArray(location) ? location : [])
    .filter((s) => s && typeof s === 'object' && SEGMENT_KINDS.includes(s.kind)
        && ['string', 'number'].includes(typeof s.id) && String(s.id) !== '')
    .map((s) => ({kind: s.kind, id: String(s.id), name: typeof s.name === 'string' ? s.name : ''}));

/**
 * The link key of a segment.
 *
 * @param {Object} segment {kind, id}
 * @returns {string}
 */
const refOf = (segment) => `${segment.kind}:${segment.id}`;

/**
 * Breadcrumb data for a location, unlinked.
 *
 * @param {Array} segments from segmentsOf()
 * @returns {Array} [{name, kind, id, ref, last, url, hasurl}]
 */
const breadcrumb = (segments) => segments.map((s, i) => ({
    name: s.name, kind: s.kind, id: s.id, ref: refOf(s), last: i === segments.length - 1, url: '', hasurl: false,
}));

/**
 * Unique {kind, id} pairs, in order.
 *
 * @param {Array} segments
 * @returns {Array} [{kind, id}]
 */
const uniqueRefs = (segments) => {
    const seen = new Map();
    segments.forEach((s) => seen.set(refOf(s), {kind: s.kind, id: s.id}));
    return [...seen.values()];
};

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
 * @param {Object} locations argument name => location, as the API resolved it
 * @returns {Object|null} null when there is nothing to show
 */
const describeParam = (key, value, strings, locale, locations) => {
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
        // Shown as the API resolved it; a path it could not resolve (or a call saved before
        // locations) is shown as typed, never parsed.
        const location = Object.prototype.hasOwnProperty.call(locations, key) ? locations[key] : null;
        if (Array.isArray(location) && !location.length) {
            return row('text', {text: strings['toolval:root']});
        }
        const segments = segmentsOf(location);
        return segments.length ? row('path', {arg: key, segments: breadcrumb(segments)}) : row('text', {text});
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
 * @param {Object} [options.locations] the call's locations (tool_call.locations)
 * @returns {Object} {hasparams, params, raw, refs} - refs: [{kind, id}] of the breadcrumbs, for linking
 */
export const describeCall = (args, {strings, locale, locations}) => {
    const input = args && typeof args === 'object' ? args : {};
    const keys = [
        ...KNOWN_ARGS.filter((key) => key in input),
        ...Object.keys(input).filter((key) => !KNOWN_ARGS.includes(key)),
    ];
    const known = locations && typeof locations === 'object' && !Array.isArray(locations) ? locations : {};
    const params = keys.map((key) => describeParam(key, input[key], strings, locale, known)).filter(Boolean);
    const refs = uniqueRefs(params.filter((p) => p.ispath).flatMap((p) => p.segments));
    return {hasparams: params.length > 0, params, raw: JSON.stringify(input, null, 2), refs};
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
 * A scalar as text ('' for anything else).
 *
 * @param {*} value
 * @returns {string}
 */
const textOf = (value) => (['string', 'number'].includes(typeof value) ? String(value) : '');

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
        // Hits saved before locations only have a path: shown as it is, not parsed.
        const names = segmentsOf(hit.location).map((s) => s.name);
        const url = httpUrl(cite && cite.url);
        return {
            index: hit.cite ?? '',
            title: (cite && cite.title) || names[names.length - 1] || textOf(hit.path),
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
        const location = segmentsOf(entry.location);
        return {
            name: String(entry.name || ''), ref: location.length ? refOf(location[location.length - 1]) : '',
            isfolder: type === 'folder',
            // Filled once the links are resolved (ChatController.linkEntries).
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
        const names = segmentsOf(block.location).map((s) => s.name);
        const url = httpUrl(cite && cite.url);
        const ranged = [block.start, block.end, block.total].every((n) => typeof n === 'number')
            && block.total > block.end - block.start;
        return {
            istext: true, text: String(block.text || ''), index: block.cite ?? '',
            title: (cite && cite.title) || names[names.length - 1] || textOf(block.title),
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
 * @returns {Object} {hasresult, blocks, hascitations, citations, refs} - refs: [{kind, id}] of the entries, for linking
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
    const refs = uniqueRefs(display.filter((b) => b.kind === 'entries' && Array.isArray(b.items))
        .flatMap((b) => b.items.map((e) => segmentsOf(e && e.location)).filter((l) => l.length).map((l) => l[l.length - 1])));
    return {hasresult: blocks.length > 0 || citations.length > 0, blocks, hascitations: citations.length > 0, citations, refs};
};

/**
 * Attach the links the page resolved (local_llmchat_resolve_refs) to breadcrumb segments, by ref.
 * Only http(s) urls link; a segment the user cannot open stays plain text.
 *
 * @param {Array} segments [{name, kind, id, ref, last}]
 * @param {Object|undefined} links ref ("kind:id") => url, '' when not accessible
 * @returns {Array} [{name, kind, id, ref, last, url, hasurl}]
 */
export const linkSegments = (segments, links) => segments.map((segment) => {
    const url = links && typeof links === 'object' ? httpUrl(links[segment.ref]) : '';
    return Object.assign({}, segment, {url, hasurl: url !== ''});
});

/**
 * The tool card's title: the first string argument, or for a path argument the name of the place
 * it resolved to.
 *
 * @param {Object} args
 * @param {Object} locations
 * @param {string} root the name for all courses
 * @returns {string}
 */
export const callTarget = (args, locations, root) => {
    const input = args && typeof args === 'object' ? args : {};
    const key = Object.keys(input).find((k) => typeof input[k] === 'string');
    if (key === undefined) {
        return '';
    }
    const location = locations && typeof locations === 'object' ? locations[key] : undefined;
    if (Array.isArray(location) && !location.length) {
        return root;
    }
    const segments = segmentsOf(location);
    return segments.length ? segments[segments.length - 1].name : input[key];
};
