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
        return row('path', {segments: names.map((name, i) => ({name, last: i === names.length - 1}))});
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
 * @returns {Object} {hasparams, params, raw}
 */
export const describeCall = (args, {strings, locale}) => {
    const input = args && typeof args === 'object' ? args : {};
    const keys = [
        ...KNOWN_ARGS.filter((key) => key in input),
        ...Object.keys(input).filter((key) => !KNOWN_ARGS.includes(key)),
    ];
    const params = keys.map((key) => describeParam(key, input[key], strings, locale)).filter(Boolean);
    return {hasparams: params.length > 0, params, raw: JSON.stringify(input, null, 2)};
};

/**
 * Display data for a tool result: summary plus the sources (citations, link attachments) it found.
 *
 * @param {Object} frame tool_result{summary, citations?, attachments?}
 * @param {Object} options
 * @param {Object} options.strings loaded language strings
 * @returns {Object} {hasresult, summary, hascitations, citations}
 */
export const describeResult = (frame, {strings}) => {
    const citations = (frame.citations || []).map((c) => {
        const {'course_name': courseName} = c;
        return {
            index: c.index ?? '',
            title: c.title || c.url || '',
            meta: [courseName, c.page ? `${strings.page} ${c.page}` : ''].filter(Boolean).join(' · '),
            url: c.url || '',
            hasurl: Boolean(c.url),
        };
    });
    (frame.attachments || []).filter((a) => a && a.type === 'link' && a.url).forEach((a) => {
        citations.push({index: '', title: a.title || a.url, meta: '', url: a.url, hasurl: true});
    });
    const summary = frame.summary ? String(frame.summary) : '';
    return {hasresult: Boolean(summary) || citations.length > 0, summary, hascitations: citations.length > 0, citations};
};
