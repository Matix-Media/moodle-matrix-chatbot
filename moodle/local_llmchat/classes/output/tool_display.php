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
     * @param array $strings the same keys the client loads (toolarg:*, toolval:*, page)
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
     * Display data for a tool result (describeResult).
     *
     * @param array $frame tool_result{summary, citations?, attachments?}
     * @return array {hasresult, summary, hascitations, citations}
     */
    public function describe_result(array $frame): array {
        $citations = [];
        foreach ($frame['citations'] ?? [] as $c) {
            $meta = array_filter([
                (string)($c['course_name'] ?? ''),
                !empty($c['page']) ? $this->strings['page'] . ' ' . $c['page'] : '',
            ], fn($p) => $p !== '');
            $citations[] = [
                'index' => $c['index'] ?? '',
                'title' => (string)(($c['title'] ?? '') ?: ($c['url'] ?? '')),
                'meta' => implode(' · ', $meta),
                'url' => (string)($c['url'] ?? ''),
                'hasurl' => !empty($c['url']),
            ];
        }
        foreach ($frame['attachments'] ?? [] as $a) {
            if (is_array($a) && ($a['type'] ?? '') === 'link' && !empty($a['url'])) {
                $citations[] = ['index' => '', 'title' => (string)(($a['title'] ?? '') ?: $a['url']), 'meta' => '',
                    'url' => (string)$a['url'], 'hasurl' => true];
            }
        }
        $summary = !empty($frame['summary']) ? (string)$frame['summary'] : '';
        return [
            'hasresult' => $summary !== '' || count($citations) > 0,
            'summary' => $summary,
            'hascitations' => count($citations) > 0,
            'citations' => $citations,
        ];
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
