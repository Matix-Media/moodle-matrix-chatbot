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
 * Server-side twin of amd/src/markdown.js.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat;

/**
 * Small, safe markdown renderer for answers, a line-by-line port of amd/src/markdown.js so a
 * history rendered by the page looks exactly like one streamed by the client. Everything is
 * escaped first, then a subset (code, bold, italics, headings, lists, links) is re-introduced,
 * and [n] markers become citation links. Both implementations are pinned by
 * tests/fixtures/markdown_vectors.json; change them together.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
final class markdown {
    /** Private-use character that parks code spans while the other inline rules run. */
    private const PARK = "\u{E000}";

    /**
     * Escape HTML exactly like the client (`'` becomes `&#39;`).
     *
     * @param string $text
     * @return string
     */
    public static function escape(string $text): string {
        return strtr($text, ['&' => '&amp;', '<' => '&lt;', '>' => '&gt;', '"' => '&quot;', "'" => '&#39;']);
    }

    /**
     * The URL when it is absolute http(s), otherwise null.
     *
     * @param string $url
     * @return string|null
     */
    public static function safe_url(string $url): ?string {
        $scheme = strtolower((string)parse_url($url, PHP_URL_SCHEME));
        $host = parse_url($url, PHP_URL_HOST);
        if (!in_array($scheme, ['http', 'https'], true) || !$host) {
            return null;
        }
        return $url;
    }

    /**
     * Inline formatting on escaped text.
     *
     * @param string $text escaped
     * @param array $cites index => citation
     * @return string
     */
    private static function inline(string $text, array $cites): string {
        $codes = [];
        $out = preg_replace_callback('/\x60([^\x60]+)\x60/u', function (array $m) use (&$codes): string {
            $codes[] = $m[1];
            return self::PARK . (count($codes) - 1) . self::PARK;
        }, $text);
        $out = preg_replace_callback('/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/u', function (array $m): string {
            $href = self::safe_url(str_replace('&amp;', '&', $m[2]));
            return $href !== null
                ? '<a href="' . self::escape($href) . '" target="_blank" rel="noopener">' . $m[1] . '</a>'
                : $m[0];
        }, $out);
        $out = preg_replace('/\*\*([^*]+)\*\*/u', '<strong>$1</strong>', $out);
        $out = preg_replace('/(^|[^*])\*([^*\s][^*]*)\*/u', '$1<em>$2</em>', $out);
        $out = preg_replace_callback('/\s*\[(\d{1,3})\]/u', function (array $m) use ($cites): string {
            $cite = $cites[(int)$m[1]] ?? null;
            $href = $cite && !empty($cite['url']) ? self::safe_url((string)$cite['url']) : null;
            if ($href === null) {
                return $m[0];
            }
            $parts = array_filter([
                (string)($cite['title'] ?? ''),
                !empty($cite['page']) ? 'S. ' . $cite['page'] : '',
            ], fn($p) => $p !== '');
            $title = self::escape(implode(' · ', $parts));
            $hidden = fn(string $t): string => '<span class="sr-only visually-hidden">' . $t . '</span>';
            return '&nbsp;<a class="local-llmchat-cite" href="' . self::escape($href) . '" target="_blank" rel="noopener"'
                . ' title="' . $title . '">' . $hidden('[') . $m[1] . $hidden(']') . '</a>';
        }, $out);
        return preg_replace_callback(
            '/' . self::PARK . '(\d+)' . self::PARK . '/u',
            fn(array $m): string => '<code>' . $codes[(int)$m[1]] . '</code>',
            $out
        );
    }

    /**
     * Render markdown to safe HTML.
     *
     * @param string $markdown
     * @param array $citations [{index, title, url, page, ...}]
     * @return string
     */
    public static function render(string $markdown, array $citations = []): string {
        $cites = [];
        foreach ($citations as $c) {
            if (is_array($c) && isset($c['index'])) {
                $cites[(int)$c['index']] = $c;
            }
        }
        $lines = explode("\n", self::escape($markdown));
        $html = [];
        $list = null;
        $para = [];
        $code = null;
        $flushpara = function () use (&$para, &$html, $cites): void {
            if ($para) {
                $html[] = '<p>' . self::inline(implode('<br>', $para), $cites) . '</p>';
                $para = [];
            }
        };
        $flushlist = function () use (&$list, &$html, $cites): void {
            if ($list) {
                $items = array_map(fn($i) => '<li>' . self::inline($i, $cites) . '</li>', $list['items']);
                $html[] = '<' . $list['tag'] . '>' . implode('', $items) . '</' . $list['tag'] . '>';
                $list = null;
            }
        };
        foreach ($lines as $line) {
            if ($code !== null) {
                if (preg_match('/^\x60{3}/', $line)) {
                    $html[] = '<pre><code>' . implode("\n", $code) . '</code></pre>';
                    $code = null;
                } else {
                    $code[] = $line;
                }
                continue;
            }
            if (preg_match('/^\x60{3}/', $line)) {
                $flushpara();
                $flushlist();
                $code = [];
                continue;
            }
            $isheading = preg_match('/^(#{1,4})\s+(.*)$/u', $line, $heading);
            $isbullet = preg_match('/^\s*[-*]\s+(.*)$/u', $line, $bullet);
            $isnumbered = preg_match('/^\s*\d+[.)]\s+(.*)$/u', $line, $numbered);
            if ($isheading) {
                $flushpara();
                $flushlist();
                $level = min(6, strlen($heading[1]) + 3);
                $html[] = "<h$level>" . self::inline($heading[2], $cites) . "</h$level>";
            } else if ($isbullet || $isnumbered) {
                $flushpara();
                $tag = $isbullet ? 'ul' : 'ol';
                if (!$list || $list['tag'] !== $tag) {
                    $flushlist();
                    $list = ['tag' => $tag, 'items' => []];
                }
                $list['items'][] = $isbullet ? $bullet[1] : $numbered[1];
            } else if (trim($line) === '') {
                $flushpara();
                $flushlist();
            } else {
                $flushlist();
                $para[] = $line;
            }
        }
        if ($code !== null) {
            $html[] = '<pre><code>' . implode("\n", $code) . '</code></pre>';
        }
        $flushpara();
        $flushlist();
        return implode('', $html);
    }
}
