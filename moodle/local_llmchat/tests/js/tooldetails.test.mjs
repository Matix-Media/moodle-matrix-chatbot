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
import {citationMeta, describeCall, describeResult, linkSegments, STRING_KEYS} from '../../amd/src/tooldetails.js';

// Vectors in tests/fixtures/tool_display_vectors.json.
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

const RESULT_STRINGS = {
    'page': 'Page',
    'toolresult:entries': '{$a} entries',
    'toolresult:pages': '{$a} pages',
    'toolresult:chars': '{$a} characters',
    'toolresult:range': 'Characters {$a->start}–{$a->end} of {$a->total}',
    'toolentry:folder': 'Folder',
    'toolentry:file': 'File',
    'toolfield:modified': 'Modified',
    'toolfield:course': 'Course',
    'toolnotice:no_hits': 'No results.',
    'toolnotice:path_not_found': 'Path not found.',
};
const resultOpts = {strings: RESULT_STRINGS, locale: 'en-GB'};
const plan = {index: 1, title: 'Plan', 'course_name': 'LF7', url: 'https://m/mod/page/view.php?id=11', pages: [2]};
// 09.11.2026 12:00 UTC
const NOV9 = Date.UTC(2026, 10, 9, 12) / 1000;

test('search hits show title, location, page, date and snippet, linked to the source', () => {
    const result = describeResult({
        citations: [plan],
        display: [{kind: 'hits', items: [{cite: 1, path: '/LF7/Woche 2/Plan', page: 2, modified: NOV9, snippet: 'Raum B204'}]}],
    }, resultOpts);
    assert.equal(result.hasresult, true);
    const [block] = result.blocks;
    assert.equal(block.ishits, true);
    assert.deepEqual(block.items[0], {
        index: 1, title: 'Plan', url: 'https://m/mod/page/view.php?id=11', hasurl: true,
        meta: 'LF7 › Woche 2 · Page 2 · 09/11/2026', snippet: 'Raum B204',
    });
    // Shown as a hit, so not listed a second time as a source.
    assert.equal(result.hascitations, false);
});

test('a hit without a known citation falls back to the last path segment', () => {
    const {blocks: [block]} = describeResult({display: [{kind: 'hits', items: [{cite: 4, path: '/A/B/Skript'}]}]}, resultOpts);
    assert.equal(block.items[0].title, 'Skript');
    assert.equal(block.items[0].hasurl, false);
});

test('entries show folders and files with translated sizes and their paths for linking', () => {
    const result = describeResult({display: [{kind: 'entries', path: '/LF7', items: [
        {name: 'Woche 1', type: 'folder', path: '/LF7/Woche 1', count: 3},
        {name: 'Skript', type: 'file', path: '/LF7/Woche 1/Skript', pages: 12, modified: NOV9},
        {name: 'Neu', type: 'quiz', path: '/LF7/Neu', chars: 1200},
    ]}]}, resultOpts);
    const [block] = result.blocks;
    assert.equal(block.isentries, true);
    assert.deepEqual(block.items.map((e) => [e.name, e.isfolder, e.meta]), [
        ['Woche 1', true, 'Folder · 3 entries'],
        ['Skript', false, 'File · 12 pages · 09/11/2026'],
        ['Neu', false, 'Quiz · 1,200 characters'],
    ]);
    assert.deepEqual(result.paths, ['/LF7/Woche 1', '/LF7/Woche 1/Skript', '/LF7/Neu']);
});

test('fields translate their labels and format dates and links', () => {
    const {blocks: [block]} = describeResult({display: [{kind: 'fields', items: [
        {key: 'modified', label: 'Geändert', value: '2026-11-09', format: 'date'},
        {key: 'link', label: 'Moodle-Link', value: 'https://m/x', format: 'link'},
        {key: 'secret', label: 'Bad link', value: 'javascript:alert(1)', format: 'link'},
        {key: 'unknown_key', label: 'Eigenes Feld', value: 'x'},
    ]}]}, resultOpts);
    assert.deepEqual(block.items.map((f) => [f.label, f.text, f.islink]), [
        ['Modified', '09/11/2026', false],
        ['Moodle-Link', 'https://m/x', true],
        ['Bad link', 'javascript:alert(1)', false],
        ['Eigenes Feld', 'x', false],
    ]);
});

test('a text excerpt shows its range and source', () => {
    const {blocks: [block], hascitations} = describeResult({citations: [plan],
        display: [{kind: 'text', text: 'Hallo', title: '/LF7/Woche 2/Plan', cite: 1, start: 0, end: 40, total: 1200}]}, resultOpts);
    assert.equal(block.istext, true);
    assert.equal(block.text, 'Hallo');
    assert.equal(block.title, 'Plan');
    assert.equal(block.range, 'Characters 0–40 of 1,200');
    assert.equal(block.hasurl, true);
    assert.equal(hascitations, false);
});

test('tables translate column labels', () => {
    const {blocks: [block]} = describeResult({display: [{kind: 'table',
        columns: [{key: 'course', label: 'Kurs'}, {key: 'x', label: 'Rollen'}], rows: [['LF7', 'Student']]}]}, resultOpts);
    assert.equal(block.istable, true);
    assert.deepEqual(block.columns.map((c) => c.label), ['Course', 'Rollen']);
    assert.deepEqual(block.rows[0].cells.map((c) => c.text), ['LF7', 'Student']);
});

test('notices are translated by code; errors keep the details', () => {
    const {blocks} = describeResult({display: [
        {kind: 'notice', level: 'info', code: 'no_hits', text: 'Keine Treffer'},
        {kind: 'notice', level: 'error', code: 'path_not_found', text: 'Pfad nicht gefunden: LF8'},
        {kind: 'notice', level: 'warning', code: 'new_code', text: 'Eigener Hinweis'},
    ]}, resultOpts);
    assert.deepEqual(blocks.map((b) => [b.isnotice, b.text, b.detail, b.level]), [
        [true, 'No results.', '', 'info'],
        [true, 'Path not found.', 'Pfad nicht gefunden: LF8', 'error'],
        [true, 'Eigener Hinweis', '', 'warning'],
    ]);
});

test('unknown block kinds are shown generically, never dropped', () => {
    const {blocks} = describeResult({display: [{kind: 'chart', text: 'Diagramm'}, {kind: 'map', lat: 1}]}, resultOpts);
    assert.deepEqual(blocks.map((b) => [b.isgeneric, b.text]), [
        [true, 'Diagramm'],
        [true, '{\n  "lat": 1\n}'],
    ]);
});

test('citations not shown in a block are still listed as sources', () => {
    const result = describeResult({citations: [{index: 1, title: 'Abgabe', 'course_name': 'LF7', url: 'https://m/a', page: 2},
        {index: 2, title: 'Raster.txt'}]}, resultOpts);
    assert.equal(result.hasresult, true);
    assert.deepEqual(result.citations[0], {index: 1, title: 'Abgabe', meta: 'LF7 · Page 2', url: 'https://m/a', hasurl: true});
    assert.equal(result.citations[1].hasurl, false);
});

test('an empty result has nothing to show', () => {
    const result = describeResult({}, resultOpts);
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

test('a cited document lists every page it was found on', () => {
    assert.equal(citationMeta({'course_name': 'LF7', pages: [2, 5], page: 2}, 'Page'), 'LF7 · Page 2, 5');
});

test('citations saved before pages existed fall back to their single page', () => {
    assert.equal(citationMeta({'course_name': 'LF7', page: 3}, 'Page'), 'LF7 · Page 3');
    assert.equal(citationMeta({pages: []}, 'Page'), '');
});
