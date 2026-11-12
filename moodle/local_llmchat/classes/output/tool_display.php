<?php
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
 * Server-side twin of amd/src/tooldetails.js plus the tool card label and icon of chat.js.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat\output;

/**
 * Turns tool-call arguments and results into the display data of the tool card templates,
 * exactly as amd/src/tooldetails.js does on the client (pinned by
 * tests/fixtures/tool_display_vectors.json; change both together).
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
final class tool_display {
    /** Arguments the agent's tools use, in display order. Others follow in their own order. */
    private const KNOWN_ARGS = [
        'question', 'query', 'pattern', 'path', 'options', 'course_ids', 'since', 'until', 'order', 'detail',
        'offset', 'limit', 'page', 'k', 'max_matches', 'context_chars',
    ];

    /** How a known argument's value is shown. */
    private const ARG_KINDS = [
        'path' => 'path', 'pattern' => 'code', 'since' => 'date', 'until' => 'date', 'order' => 'enum', 'detail' => 'enum',
    ];

    /** Enum values with a translation. */
    private const ENUM_VALUES = ['modified_desc', 'modified_asc'];

    /** Display-block labels with a translation; others use the label the API sent. */
    private const FIELD_KEYS = ['title', 'kind', 'size', 'modified', 'link', 'note', 'date', 'weekday', 'time', 'week',
        'timezone', 'name', 'lang', 'course', 'courses', 'teaching', 'roles', 'groups'];

    /** Entry types with a translation. */
    private const ENTRY_TYPES = ['folder', 'inline', 'html', 'file', 'external'];

    /** Notice codes with a translation. */
    private const NOTICE_CODES = ['no_hits', 'empty', 'no_access', 'no_text', 'no_documents', 'path_not_found',
        'moodle_unavailable', 'invalid_input', 'access_note'];

    /** Tool name => [pix key, component], used when the API's tool metadata names no pix icon. */
    private const TOOL_ICONS = [
        'search_content' => ['a/search', 'core'],
        'search_text' => ['a/search', 'core'],
        'search_regex' => ['a/search', 'core'],
        'list_dir' => ['i/folder', 'core'],
        'stat_file' => ['i/files', 'core'],
        'read_file' => ['i/files', 'core'],
        'list_documents' => ['i/calendar', 'core'],
        'current_datetime' => ['i/calendar', 'core'],
        'current_user' => ['i/user', 'core'],
        'ask_user' => ['i/question', 'core'],
    ];

    /**
     * Constructor.
     *
     * @param array $strings the same keys the client loads (toolarg:*, toolval:*, toolresult:*, toolfield:*, toolentry:*,
     *     toolnotice:*, page)
     */
    public function __construct(
        /** @var array */
        private array $strings,
    ) {
    }

    /**
     * With the strings of the current language.
     *
     * @return self
     */
    public static function from_lang(): self {
        $keys = array_merge(
            array_map(fn($k) => "toolarg:$k", self::KNOWN_ARGS),
            array_map(fn($v) => "toolval:$v", self::ENUM_VALUES),
            ['toolval:daysago', 'toolval:yes', 'toolval:no', 'toolval:root'],
            ['toolresult:entries', 'toolresult:pages', 'toolresult:chars', 'toolresult:range'],
            array_map(fn($k) => "toolfield:$k", self::FIELD_KEYS),
            array_map(fn($t) => "toolentry:$t", self::ENTRY_TYPES),
            array_map(fn($c) => "toolnotice:$c", self::NOTICE_CODES),
        );
        $strings = [];
        foreach ($keys as $key) {
            $strings[$key] = get_string($key, 'local_llmchat');
        }
        $strings['page'] = get_string('page');
        return new self($strings);
    }

    /**
     * Fill {$a}.
     *
     * @param string $template
     * @param mixed $a
     * @return string
     */
    private static function fill(string $template, $a): string {
        if (is_array($a)) {
            return preg_replace_callback('/\{\$a->(\w+)\}/', fn($m) => (string)($a[$m[1]] ?? ''), $template);
        }
        return str_replace('{$a}', (string)$a, $template);
    }

    /**
     * "max_depth" → "Max depth".
     *
     * @param string $key
     * @return string
     */
    private static function humanise(string $key): string {
        $words = preg_replace('/[_-]+/', ' ', $key);
        $words = \core_text::strtolower(trim(preg_replace('/([a-z])([A-Z])/', '$1 $2', $words)));
        return \core_text::strtoupper(\core_text::substr($words, 0, 1)) . \core_text::substr($words, 1);
    }

    /**
     * JSON as JavaScript's JSON.stringify writes it (2-space indent when pretty).
     *
     * @param mixed $value
     * @param bool $pretty
     * @return string
     */
    public static function json($value, bool $pretty = false): string {
        $flags = JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_SUBSTITUTE;
        if (!$pretty) {
            return (string)json_encode($value, $flags);
        }
        $json = (string)json_encode($value, $flags | JSON_PRETTY_PRINT);
        return preg_replace_callback('/^( +)/m', fn($m) => str_repeat(' ', intdiv(strlen($m[1]), 2)), $json);
    }

    /**
     * A number the way the user's language writes it.
     *
     * @param int|float $value
     * @return string
     */
    private static function number($value): string {
        if (is_int($value)) {
            return abs($value) < 1000 ? (string)$value
                : number_format($value, 0, '', get_string('thousandssep', 'langconfig'));
        }
        return format_float($value, -1);
    }

    /**
     * A date-ish argument in words: ISO date, ISO date-time, or a relative range such as "-7d".
     *
     * @param string $value
     * @return string
     */
    private function date(string $value): string {
        if (preg_match('/^-(\d+)d$/', $value, $m)) {
            return self::fill($this->strings['toolval:daysago'], (int)$m[1]);
        }
        $time = strtotime($value);
        if ($time === false) {
            return $value;
        }
        $format = preg_match('/^\d{4}-\d{2}-\d{2}$/', $value) ? 'strftimedate' : 'strftimedatetime';
        return userdate($time, get_string($format, 'langconfig'));
    }

    /**
     * One parameter row for the template.
     *
     * @param string $key
     * @param mixed $value
     * @return array|null null when there is nothing to show
     */
    private function param(string $key, $value): ?array {
        if ($value === null || $value === '') {
            return null;
        }
        $label = $this->strings["toolarg:$key"] ?? self::humanise($key);
        $row = fn(string $kind, array $extra): array => array_merge([
            'label' => $label, 'istext' => false, 'iscode' => false, 'ispath' => false, 'ischips' => false,
            'text' => '', 'segments' => [], 'chips' => [],
        ], ["is$kind" => true], $extra);
        $kind = self::ARG_KINDS[$key] ?? null;
        if (is_bool($value)) {
            return $row('text', ['text' => $value ? $this->strings['toolval:yes'] : $this->strings['toolval:no']]);
        }
        if (is_int($value) || is_float($value)) {
            return $row('text', ['text' => self::number($value)]);
        }
        if (is_array($value)) {
            if (array_is_list($value)) {
                $scalar = array_filter($value, fn($v) => is_string($v) || is_int($v) || is_float($v));
                if (count($scalar) === count($value)) {
                    return $row('chips', ['chips' => array_map(fn($v) => ['text' => (string)$v], $value)]);
                }
                return $row('code', ['text' => self::json($value)]);
            }
            return $row('code', ['text' => self::json((object)$value)]);
        }
        $text = (string)$value;
        if ($kind === 'path') {
            $names = array_values(array_filter(array_map('trim', explode('/', $text)), fn($s) => $s !== ''));
            if (!$names) {
                return $row('text', ['text' => $this->strings['toolval:root']]);
            }
            $last = count($names) - 1;
            $segments = [];
            foreach ($names as $i => $name) {
                $segments[] = ['name' => $name, 'last' => $i === $last, 'url' => '', 'hasurl' => false];
            }
            return $row('path', ['path' => $text, 'segments' => $segments]);
        }
        if ($kind === 'code') {
            return $row('code', ['text' => $text]);
        }
        if ($kind === 'date') {
            return $row('text', ['text' => $this->date($text)]);
        }
        if ($kind === 'enum') {
            return $row('text', ['text' => $this->strings["toolval:$text"] ?? self::humanise($text)]);
        }
        return $row('text', ['text' => $text]);
    }

    /**
     * Display data for a tool call's arguments (describeCall).
     *
     * @param array $args
     * @return array {hasparams, params, raw, paths}
     */
    public function describe_call(array $args): array {
        $keys = array_merge(
            array_values(array_filter(self::KNOWN_ARGS, fn($k) => array_key_exists($k, $args))),
            array_values(array_filter(array_map('strval', array_keys($args)), fn($k) => !in_array($k, self::KNOWN_ARGS, true))),
        );
        $params = [];
        foreach ($keys as $key) {
            $row = $this->param($key, $args[$key]);
            if ($row !== null) {
                $params[] = $row;
            }
        }
        $paths = [];
        foreach ($params as $p) {
            if ($p['ispath'] && !in_array($p['path'], $paths, true)) {
                $paths[] = $p['path'];
            }
        }
        return [
            'hasparams' => count($params) > 0,
            'params' => $params,
            'raw' => self::json((object)$args, true),
            'paths' => $paths,
        ];
    }

    /**
     * The pages of a citation, from "pages" or (conversations saved before it) "page".
     *
     * @param array $citation
     * @return int[]
     */
    public static function citation_pages(array $citation): array {
        if (!empty($citation['pages']) && is_array($citation['pages'])) {
            return array_values($citation['pages']);
        }
        return !empty($citation['page']) ? [$citation['page']] : [];
    }

    /**
     * Secondary line for a cited source: course and pages, e.g. "LF7 · Page 2, 5" (citationMeta).
     *
     * @param array $citation
     * @param string $pageword the word for "page"
     * @return string
     */
    public static function citation_meta(array $citation, string $pageword): string {
        $pages = self::citation_pages($citation);
        return implode(' · ', array_filter([
            (string)($citation['course_name'] ?? ''),
            $pages ? $pageword . ' ' . implode(', ', $pages) : '',
        ], fn($p) => $p !== ''));
    }

    /**
     * An http(s) URL, or '' for anything else.
     *
     * @param mixed $url
     * @return string
     */
    private static function http_url($url): string {
        $url = is_scalar($url) ? (string)$url : '';
        return preg_match('#^https?://#i', $url) ? $url : '';
    }

    /**
     * Path segments of a course path.
     *
     * @param mixed $path
     * @return string[]
     */
    private static function segments_of($path): array {
        $path = is_scalar($path) ? (string)$path : '';
        return array_values(array_filter(array_map('trim', explode('/', $path)), fn($s) => $s !== ''));
    }

    /**
     * Epoch seconds or an ISO date/date-time in the user's format ('' when unknown).
     *
     * @param mixed $value
     * @return string
     */
    private function when($value): string {
        if ($value === null || $value === '') {
            return '';
        }
        if (is_int($value) || is_float($value)) {
            return userdate((int)$value, get_string('strftimedate', 'langconfig'));
        }
        return $this->date((string)$value);
    }

    /**
     * A string from the loaded set, or null.
     *
     * @param string $key
     * @return string|null
     */
    private function str(string $key): ?string {
        $value = $this->strings[$key] ?? null;
        return $value === null || $value === '' ? null : (string)$value;
    }

    /**
     * A scalar as text ('' for null, arrays and objects).
     *
     * @param mixed $value
     * @return string
     */
    private static function text($value): string {
        if (is_bool($value)) {
            return $value ? 'true' : 'false';
        }
        return is_scalar($value) ? (string)$value : '';
    }

    /**
     * Template context for one display block, by kind; unknown kinds are shown generically (describeBlock).
     *
     * @param array $block
     * @param array $cites citation index => citation
     * @return array
     */
    private function block(array $block, array $cites): array {
        $items = isset($block['items']) && is_array($block['items']) ? array_values(array_filter($block['items'], 'is_array')) : [];
        switch ($block['kind'] ?? null) {
            case 'hits':
                return ['ishits' => true, 'items' => array_map(function (array $hit) use ($cites): array {
                    $cite = $cites[(int)($hit['cite'] ?? 0)] ?? null;
                    $names = self::segments_of($hit['path'] ?? '');
                    $url = self::http_url($cite['url'] ?? '');
                    $title = (string)($cite['title'] ?? '');
                    return [
                        'index' => $hit['cite'] ?? '',
                        'title' => $title !== '' ? $title : ($names ? end($names) : self::text($hit['path'] ?? '')),
                        'url' => $url,
                        'hasurl' => $url !== '',
                        'meta' => implode(' · ', array_filter([
                            implode(' › ', array_slice($names, 0, -1)),
                            !empty($hit['page']) ? $this->strings['page'] . ' ' . $hit['page'] : '',
                            $this->when($hit['modified'] ?? null),
                        ], fn($p) => $p !== '')),
                        'snippet' => self::text($hit['snippet'] ?? ''),
                    ];
                }, $items)];
            case 'entries':
                return ['isentries' => true, 'items' => array_map(function (array $entry): array {
                    $type = self::text($entry['type'] ?? '');
                    $count = $entry['count'] ?? null;
                    $size = [
                        $count !== null ? self::fill((string)$this->str('toolresult:entries'), self::number($count)) : '',
                        !empty($entry['pages'])
                            ? self::fill((string)$this->str('toolresult:pages'), self::number($entry['pages'])) : '',
                        empty($entry['pages']) && !empty($entry['chars'])
                            ? self::fill((string)$this->str('toolresult:chars'), self::number($entry['chars'])) : '',
                    ];
                    return [
                        'name' => self::text($entry['name'] ?? ''),
                        'path' => self::text($entry['path'] ?? ''),
                        'isfolder' => $type === 'folder',
                        'url' => '',
                        'hasurl' => false,
                        'detail' => self::text($entry['detail'] ?? ''),
                        'meta' => implode(' · ', array_filter(array_merge(
                            [$this->str("toolentry:$type") ?? self::humanise($type)],
                            $size,
                            [$this->when($entry['modified'] ?? null)],
                        ), fn($p) => $p !== '')),
                    ];
                }, $items)];
            case 'fields':
                return ['isfields' => true, 'items' => array_map(function (array $field): array {
                    $key = self::text($field['key'] ?? '');
                    $value = self::text($field['value'] ?? '');
                    $format = $field['format'] ?? 'text';
                    $url = $format === 'link' ? self::http_url($value) : '';
                    $label = self::text($field['label'] ?? '');
                    return [
                        'label' => $this->str("toolfield:$key") ?? ($label !== '' ? $label : self::humanise($key)),
                        'text' => $format === 'date' ? $this->when($value) : $value,
                        'islink' => $url !== '',
                        'url' => $url,
                        'iscode' => $format === 'code',
                    ];
                }, $items)];
            case 'text':
                $cite = $cites[(int)($block['cite'] ?? 0)] ?? null;
                $names = self::segments_of($block['title'] ?? '');
                $url = self::http_url($cite['url'] ?? '');
                $title = (string)($cite['title'] ?? '');
                [$start, $end, $total] = [$block['start'] ?? null, $block['end'] ?? null, $block['total'] ?? null];
                $ranged = is_int($start) && is_int($end) && is_int($total) && $total > $end - $start;
                return [
                    'istext' => true,
                    'text' => self::text($block['text'] ?? ''),
                    'index' => $block['cite'] ?? '',
                    'title' => $title !== '' ? $title : ($names ? end($names) : ''),
                    'url' => $url,
                    'hasurl' => $url !== '',
                    'range' => $ranged ? self::fill((string)$this->str('toolresult:range'), [
                        'start' => self::number($start), 'end' => self::number($end), 'total' => self::number($total),
                    ]) : '',
                ];
            case 'table':
                $columns = is_array($block['columns'] ?? null) ? array_values(array_filter($block['columns'], 'is_array')) : [];
                $rows = is_array($block['rows'] ?? null) ? array_values($block['rows']) : [];
                return [
                    'istable' => true,
                    'columns' => array_map(function (array $c): array {
                        $key = self::text($c['key'] ?? '');
                        $label = self::text($c['label'] ?? '');
                        return ['label' => $this->str("toolfield:$key") ?? ($label !== '' ? $label : $key)];
                    }, $columns),
                    'rows' => array_map(fn($row) => ['cells' => array_map(
                        fn($cell) => ['text' => self::text($cell)],
                        is_array($row) ? array_values($row) : []
                    )], $rows),
                ];
            case 'notice':
                $level = in_array($block['level'] ?? '', ['info', 'warning', 'error'], true) ? $block['level'] : 'info';
                $translated = $this->str('toolnotice:' . self::text($block['code'] ?? ''));
                $own = self::text($block['text'] ?? '');
                return [
                    'isnotice' => true,
                    'level' => $level,
                    'iserror' => $level === 'error',
                    'iswarning' => $level === 'warning',
                    'text' => $translated ?? $own,
                    'detail' => $translated !== null && $level === 'error' && $own !== $translated ? $own : '',
                ];
            default:
                $rest = $block;
                unset($rest['kind']);
                return ['isgeneric' => true, 'text' => is_string($block['text'] ?? null)
                    ? $block['text'] : self::json((object)$rest, true)];
        }
    }

    /**
     * Display data for a tool result: its display blocks, plus the sources it cited that no block shows
     * (describeResult).
     *
     * @param array $frame tool_result{citations?, display?}
     * @return array {hasresult, blocks, hascitations, citations, paths} - paths: entry paths, for linking
     */
    public function describe_result(array $frame): array {
        $all = array_values(array_filter(is_array($frame['citations'] ?? null) ? $frame['citations'] : [], 'is_array'));
        $cites = [];
        foreach ($all as $c) {
            $cites[(int)($c['index'] ?? 0)] = $c;
        }
        $display = array_values(array_filter(is_array($frame['display'] ?? null) ? $frame['display'] : [], 'is_array'));
        $blocks = array_map(fn($b) => $this->block($b, $cites), $display);
        $shown = [];
        foreach ($display as $b) {
            $candidates = [$b['cite'] ?? null];
            foreach (is_array($b['items'] ?? null) ? $b['items'] : [] as $item) {
                $candidates[] = is_array($item) ? ($item['cite'] ?? null) : null;
            }
            foreach ($candidates as $n) {
                if ($n !== null) {
                    $shown[(int)$n] = true;
                }
            }
        }
        $citations = [];
        foreach ($all as $c) {
            if (isset($shown[(int)($c['index'] ?? 0)])) {
                continue;
            }
            $citations[] = [
                'index' => $c['index'] ?? '',
                'title' => (string)(($c['title'] ?? '') ?: ($c['url'] ?? '')),
                'meta' => self::citation_meta($c, $this->strings['page']),
                'url' => (string)($c['url'] ?? ''),
                'hasurl' => !empty($c['url']),
            ];
        }
        $paths = [];
        foreach ($blocks as $b) {
            foreach (!empty($b['isentries']) ? $b['items'] : [] as $entry) {
                if ($entry['path'] !== '' && !in_array($entry['path'], $paths, true)) {
                    $paths[] = $entry['path'];
                }
            }
        }
        return [
            'hasresult' => count($blocks) > 0 || count($citations) > 0,
            'blocks' => $blocks,
            'hascitations' => count($citations) > 0,
            'citations' => $citations,
            'paths' => $paths,
        ];
    }

    /**
     * Link the listed entries of a described result to what the user can open (as the page's linkEntries).
     *
     * @param array $result describe_result() output
     * @param array $links path => [{name, url}]
     * @return array
     */
    public static function link_entries(array $result, array $links): array {
        foreach ($result['blocks'] as $b => $block) {
            if (empty($block['isentries'])) {
                continue;
            }
            foreach ($block['items'] as $i => $entry) {
                $segments = $links[$entry['path']] ?? null;
                $last = is_array($segments) && $segments ? end($segments) : null;
                $url = is_array($last) ? self::http_url($last['url'] ?? '') : '';
                $result['blocks'][$b]['items'][$i]['url'] = $url;
                $result['blocks'][$b]['items'][$i]['hasurl'] = $url !== '';
            }
        }
        return $result;
    }

    /**
     * Attach resolved links to breadcrumb segments (linkSegments).
     *
     * @param array $segments [{name, last}]
     * @param array|null $resolved [{name, url}] for the same path, url '' when not accessible
     * @return array [{name, last, url, hasurl}]
     */
    public static function link_segments(array $segments, ?array $resolved): array {
        $out = [];
        foreach (array_values($segments) as $i => $segment) {
            $answer = $resolved[$i] ?? null;
            $same = is_array($answer)
                && \core_text::strtolower((string)($answer['name'] ?? '')) === \core_text::strtolower((string)$segment['name']);
            $url = $same && preg_match('#^https?://#i', (string)($answer['url'] ?? '')) ? (string)$answer['url'] : '';
            $out[] = array_merge($segment, ['url' => $url, 'hasurl' => $url !== '']);
        }
        return $out;
    }

    /**
     * Card label: the tool's summary template, else its label and first string argument (as chat.js).
     *
     * @param string $name
     * @param array $args
     * @param array $meta tool metadata from the API's manifest
     * @return string
     */
    public static function label(string $name, array $args, array $meta): string {
        if (!empty($meta['summary_template'])) {
            return preg_replace_callback('/\{(\w+)\}/', function ($m) use ($args) {
                if (!array_key_exists($m[1], $args) || $args[$m[1]] === null) {
                    return '';
                }
                $v = $args[$m[1]];
                return is_bool($v) ? ($v ? 'true' : 'false') : (is_scalar($v) ? (string)$v : self::json($v));
            }, (string)$meta['summary_template']);
        }
        $first = '';
        foreach ($args as $v) {
            if (is_string($v)) {
                $first = $v;
                break;
            }
        }
        return (($meta['label'] ?? '') ?: $name) . ($first !== '' ? " „{$first}“" : '');
    }

    /**
     * Pix icon: an icon named by the API as "component:key" wins, then the name table.
     *
     * @param string $name
     * @param array $meta
     * @return array [key, component]
     */
    public static function icon(string $name, array $meta): array {
        if (preg_match('#^([a-z0-9_]+):([a-z0-9_/-]+)$#', (string)($meta['icon'] ?? ''), $m)) {
            return [$m[2], $m[1]];
        }
        return self::TOOL_ICONS[$name] ?? ['tool', 'local_llmchat'];
    }
}
