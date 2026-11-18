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

// Tests for local_llmchat/markdown against tests/fixtures/markdown_vectors.json. Run: node --test moodle/local_llmchat/tests/js/
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {registerHooks} from 'node:module';

// Resolve AMD names like RequireJS does in Moodle: local_llmchat/x is amd/src/x.js.
const amdSrc = new URL('../../amd/src/', import.meta.url);
registerHooks({
    resolve(specifier, context, next) {
        const amd = specifier.match(/^local_llmchat\/(.+)$/);
        return amd ? {url: new URL(`${amd[1]}.js`, amdSrc).href, shortCircuit: true} : next(specifier, context);
    },
});

// The module resolves URLs against the page.
globalThis.window = {location: {href: 'https://moodle.example/local/llmchat/chat.php'}};
const {render} = await import('../../amd/src/markdown.js');

const {vectors} = JSON.parse(readFileSync(new URL('../fixtures/markdown_vectors.json', import.meta.url), 'utf8'));

for (const {name, markdown, citations, html} of vectors) {
    test(`markdown: ${name}`, () => {
        assert.equal(render(markdown, citations), html);
    });
}
