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

// Tests for local_llmchat/tooldetails. Run: node --test moodle/local_llmchat/tests/js/
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {describeCall, describeResult, linkSegments, STRING_KEYS} from '../../amd/src/tooldetails.js';

// Vectors shared with the PHP port (tests/tool_display_test.php).
const VECTORS = JSON.parse(readFileSync(new URL('../fixtures/tool_display_vectors.json', import.meta.url), 'utf8'));
for (const {name, args, expected} of VECTORS.calls) {
    test(`shared vector, call: ${name}`, () => {
        assert.deepEqual(describeCall(args, {strings: VECTORS.strings, locale: 'en'}), expected);
    });
}
for (const {name, frame, expected} of VECTORS.results) {
    test(`shared vector, result: ${name}`, () => {
        assert.deepEqual(describeResult(frame, {strings: VECTORS.strings}), expected);
    });
}
for (const {name, segments, resolved, expected} of VECTORS.links) {
    test(`shared vector, links: ${name}`, () => {
        assert.deepEqual(linkSegments(segments, resolved), expected);
    });
}

// Strings as the page would load them (keys from STRING_KEYS → lang/en).
const STR = {
    'toolarg:query': 'Search for', 'toolarg:k': 'Max. results', 'toolarg:path': 'Location', 'toolarg:since': 'From',
    'toolarg:until': 'Until', 'toolarg:course_ids': 'Courses', 'toolarg:pattern': 'Pattern', 'toolarg:order': 'Order',
    'toolarg:offset': 'Start at character', 'toolarg:limit': 'Length', 'toolarg:page': 'Page', 'toolarg:options': 'Options',
    'toolarg:max_matches': 'Max. matches', 'toolarg:context_chars': 'Context', 'toolarg:detail': 'Detail',
    'toolarg:question': 'Question', 'toolval:modified_desc': 'Newest first', 'toolval:modified_asc': 'Oldest first',
    'toolval:daysago': 'last {$a} days', 'toolval:yes': 'Yes', 'toolval:no': 'No', 'toolval:root': 'All courses',
};
const opts = {strings: STR, locale: 'de-DE'};

test('every label the parser can use is a requested string', () => {
    for (const key of Object.keys(STR)) {
        assert.ok(STRING_KEYS.includes(key), key);
    }
});

test('known arguments get friendly labels in a stable order', () => {
    const call = describeCall({k: 8, query: 'Abgabe Subnetting-Projekt'}, opts);
    assert.equal(call.hasparams, true);
    assert.deepEqual(call.params.map((p) => p.label), ['Search for', 'Max. results']);
    assert.equal(call.params[0].istext, true);
    assert.equal(call.params[0].text, 'Abgabe Subnetting-Projekt');
    assert.equal(call.params[1].text, '8');
});

test('a path becomes breadcrumb segments with the last one marked', () => {
    const [param] = describeCall({path: '/LF7/Subnetting-Projekt/Material Projekt/Bewertungsraster.txt'}, opts).params;
    assert.equal(param.ispath, true);
    assert.deepEqual(param.segments.map(({name, last}) => ({name, last})), [
        {name: 'LF7', last: false}, {name: 'Subnetting-Projekt', last: false},
        {name: 'Material Projekt', last: false}, {name: 'Bewertungsraster.txt', last: true},
    ]);
});

test('the root path is named, not shown as an empty breadcrumb', () => {
    const [param] = describeCall({path: '/'}, opts).params;
    assert.equal(param.istext, true);
    assert.equal(param.text, 'All courses');
});

test('ISO dates are formatted and relative ranges are put into words', () => {
    const params = describeCall({since: '-7d', until: '2026-11-20'}, opts).params;
    assert.equal(params[0].text, 'last 7 days');
    assert.equal(params[1].text, new Date('2026-11-20T00:00:00').toLocaleDateString('de-DE'));
});

test('regex patterns are shown as code, lists as chips, enums translated', () => {
    const params = describeCall({pattern: '\\b/2[4-9]\\b', 'course_ids': [529, 530], order: 'modified_desc'}, opts).params;
    const byLabel = Object.fromEntries(params.map((p) => [p.label, p]));
    assert.equal(byLabel.Pattern.iscode, true);
    assert.equal(byLabel.Pattern.text, '\\b/2[4-9]\\b');
    assert.equal(byLabel.Courses.ischips, true);
    assert.deepEqual(byLabel.Courses.chips, [{text: '529'}, {text: '530'}]);
    assert.equal(byLabel.Order.text, 'Newest first');
});

test('read windows are shown as a character range', () => {
    const params = describeCall({path: '/LF7/a.pdf', offset: 6000, limit: 6000}, opts).params;
    assert.deepEqual(params.map((p) => p.label), ['Location', 'Start at character', 'Length']);
    assert.equal(params[1].text, (6000).toLocaleString('de-DE'));
});

test('unknown arguments are humanised and complex values shown as compact JSON', () => {
    const params = describeCall({'max_depth': 3, filters: {kind: 'pdf'}, flag: true, empty: null}, opts).params;
    assert.deepEqual(params.map((p) => p.label), ['Max depth', 'Filters', 'Flag']);
    assert.equal(params[1].iscode, true);
    assert.equal(params[1].text, '{"kind":"pdf"}');
    assert.equal(params[2].text, 'Yes');
});

test('no arguments means no parameter list, raw JSON is always available', () => {
    const call = describeCall({}, opts);
    assert.equal(call.hasparams, false);
    assert.equal(describeCall({query: 'x'}, opts).raw, '{\n  "query": "x"\n}');
});

test('a result lists its summary and the citations it found', () => {
    const result = describeResult({
        summary: '4 Treffer in LF7',
        citations: [
            {index: 1, title: 'Abgabe Subnetting-Projekt', 'course_name': 'LF7', url: 'https://m/mod/assign/view.php?id=3', page: 2},
            {index: 2, title: 'Bewertungsraster.txt'},
        ],
    }, {strings: {page: 'Page'}, locale: 'de-DE'});
    assert.equal(result.summary, '4 Treffer in LF7');
    assert.equal(result.hascitations, true);
    assert.deepEqual(result.citations[0], {
        index: 1, title: 'Abgabe Subnetting-Projekt', meta: 'LF7 · Page 2', url: 'https://m/mod/assign/view.php?id=3', hasurl: true,
    });
    assert.equal(result.citations[1].hasurl, false);
});

test('link attachments are listed like citations, other kinds are ignored', () => {
    const result = describeResult({attachments: [{type: 'link', title: 'YouTube', url: 'https://youtube.com/x'}, {type: 'table'}]},
        {strings: {page: 'Page'}, locale: 'de-DE'});
    assert.equal(result.hascitations, true);
    assert.deepEqual(result.citations.map((c) => c.title), ['YouTube']);
    assert.equal(result.citations[0].index, '');
});

test('an empty result has nothing to show', () => {
    const result = describeResult({}, {strings: {page: 'Page'}, locale: 'de-DE'});
    assert.equal(result.hasresult, false);
});

test('a call lists the paths it shows so the page can link them', () => {
    const call = describeCall({path: '/LF7/Subnetting-Projekt/Material Projekt', query: 'x'}, opts);
    assert.deepEqual(call.paths, ['/LF7/Subnetting-Projekt/Material Projekt']);
    const [, param] = call.params;
    assert.equal(param.path, '/LF7/Subnetting-Projekt/Material Projekt');
    assert.ok(param.segments.every((s) => s.hasurl === false && s.url === ''));
    assert.deepEqual(describeCall({path: '/'}, opts).paths, []);
    assert.deepEqual(describeCall({query: 'x'}, opts).paths, []);
});

test('resolved links are attached per segment; empty urls stay plain text', () => {
    const segments = describeCall({path: '/LF7/Geheim/Lösung'}, opts).params[0].segments;
    const linked = linkSegments(segments, [
        {name: 'LF7', url: 'https://m/course/view.php?id=2'}, {name: 'Geheim', url: ''}, {name: 'Lösung', url: ''},
    ]);
    assert.deepEqual(linked.map((s) => [s.name, s.hasurl, s.url, s.last]), [
        ['LF7', true, 'https://m/course/view.php?id=2', false], ['Geheim', false, '', false], ['Lösung', false, '', true],
    ]);
});

test('links are only taken from http(s) urls and never from a mismatched answer', () => {
    const segments = describeCall({path: '/LF7/A'}, opts).params[0].segments;
    const linked = linkSegments(segments, [{name: 'LF7', url: 'javascript:alert(1)'}, {name: 'B', url: 'https://m/x'}]);
    assert.deepEqual(linked.map((s) => s.hasurl), [false, false]);
    assert.deepEqual(linkSegments(segments, undefined).map((s) => s.hasurl), [false, false]);
});
