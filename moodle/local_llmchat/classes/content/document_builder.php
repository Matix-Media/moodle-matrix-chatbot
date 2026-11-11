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
 * Per-modname rules deciding what becomes a document.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat\content;

use cm_info;

/**
 * Turns one course module into DocumentIn records.
 *
 * Rules: intro/description → inline html (part "intro"); label → its text; page → html
 * (part "page"); book → one html document per visible chapter ("chapter:{id}"); url →
 * external url ("url"); resource and folder → one file document per allowed file
 * ("file:{path}{name}"). Part ids are stable across edits so an update replaces in place.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class document_builder {
    /** Default file type allowlist (mirrors the API's extractors). */
    public const DEFAULT_FILETYPES = 'pdf,doc,docx,odt,rtf,txt,md,csv,html,htm,ppt,pptx,odp,xls,xlsx,ods,epub,json,xml';

    /** Default maximum file size in MB. */
    public const DEFAULT_MAXFILESIZE_MB = 25;

    /**
     * Build documents for one cm, sorted by doc id.
     *
     * @param cm_info $cm
     * @param \stdClass $course
     * @return document[]
     */
    public function build(cm_info $cm, \stdClass $course): array {
        global $DB;
        if (!empty($cm->deletioninprogress)) {
            return [];
        }
        $instance = $DB->get_record($cm->modname, ['id' => $cm->instance]);
        if (!$instance) {
            return [];
        }
        $context = \context_module::instance($cm->id);
        $base = $this->base($cm, $course, $instance);
        $docs = [];

        if ($cm->modname === 'label') {
            $text = $this->rewrite((string)($instance->intro ?? ''), $context, 'mod_label', 'intro');
            if ($this->has_content($text)) {
                $docs[] = $this->html($base, 'intro', $base['module_name'], $text, []);
            }
            return $docs;
        }

        if (property_exists($instance, 'intro') && plugin_supports('mod', $cm->modname, FEATURE_MOD_INTRO, true)) {
            $text = $this->rewrite((string)$instance->intro, $context, 'mod_' . $cm->modname, 'intro');
            if ($this->has_content($text)) {
                $docs[] = $this->html($base, 'intro', $base['module_name'], $text, []);
            }
        }

        switch ($cm->modname) {
            case 'page':
                $text = $this->rewrite((string)$instance->content, $context, 'mod_page', 'content');
                if ($this->has_content($text)) {
                    $docs[] = $this->html($base, 'page', $base['module_name'], $text, []);
                }
                break;
            case 'book':
                $chapters = $DB->get_records('book_chapters', ['bookid' => $instance->id, 'hidden' => 0], 'pagenum');
                foreach ($chapters as $chapter) {
                    $text = file_rewrite_pluginfile_urls(
                        $chapter->content,
                        'pluginfile.php',
                        $context->id,
                        'mod_book',
                        'chapter',
                        $chapter->id
                    );
                    $doc = $this->html($base, 'chapter:' . $chapter->id, $chapter->title, $text, [$chapter->title]);
                    $data = $doc->to_wire();
                    $data['module_url'] = (new \moodle_url(
                        '/mod/book/view.php',
                        ['id' => $cm->id, 'chapterid' => $chapter->id]
                    ))->out(false);
                    $data['timemodified'] = (int)$chapter->timemodified;
                    $docs[] = new document($data);
                }
                break;
            case 'url':
                $external = trim((string)$instance->externalurl);
                if ($external !== '') {
                    $docs[] = new document(array_merge($base, [
                        'doc_id' => $base['doc_id'] . 'url',
                        'title' => $base['module_name'],
                        'kind' => 'external',
                        'external_url' => $external,
                        'content_hash' => hash('sha256', $external),
                    ]));
                }
                break;
            case 'resource':
            case 'folder':
                foreach ($this->files($context, 'mod_' . $cm->modname) as $file) {
                    $docs[] = $this->file($base, $cm, $context, $file);
                }
                break;
        }

        usort($docs, fn(document $a, document $b) => strcmp($a->id(), $b->id()));
        return $docs;
    }

    /**
     * Fields shared by every document of the cm. doc_id holds the "{course}:{cmid}:" prefix.
     *
     * @param cm_info $cm
     * @param \stdClass $course
     * @param \stdClass $instance
     * @return array
     */
    private function base(cm_info $cm, \stdClass $course, \stdClass $instance): array {
        $section = $cm->get_section_info();
        $sectionname = trim(strip_tags(get_section_name($course, $section)));
        $modulename = trim(strip_tags($cm->name));
        $url = $cm->url ? $cm->url->out(false) : (new \moodle_url('/course/view.php', ['id' => $course->id]))->out(false);
        return [
            'doc_id' => "{$course->id}:{$cm->id}:",
            'course_id' => (int)$course->id,
            'section_id' => (int)$section->id,
            'section_name' => $sectionname,
            'section_order' => (int)$section->section,
            'cmid' => (int)$cm->id,
            'modname' => $cm->modname,
            'module_name' => $modulename,
            'header_path' => [trim(strip_tags($course->fullname)), $sectionname, $modulename],
            'module_url' => $url,
            'timemodified' => (int)max($cm->added, $instance->timemodified ?? 0),
        ];
    }

    /**
     * An inline html document.
     *
     * @param array $base
     * @param string $part
     * @param string $title
     * @param string $text
     * @param string[] $extrapath
     * @return document
     */
    private function html(array $base, string $part, string $title, string $text, array $extrapath): document {
        $data = $base;
        $data['doc_id'] .= $part;
        $data['title'] = trim(strip_tags($title));
        $data['header_path'] = array_merge($base['header_path'], $extrapath);
        $data['kind'] = 'html';
        $data['text'] = $text;
        $data['content_hash'] = hash('sha256', $text);
        return new document($data);
    }

    /**
     * A file document.
     *
     * @param array $base
     * @param cm_info $cm
     * @param \context_module $context
     * @param \stored_file $file
     * @return document
     */
    private function file(array $base, cm_info $cm, \context_module $context, \stored_file $file): document {
        $sha = file_hasher::sha256($file);
        $folders = array_values(array_filter(explode('/', $file->get_filepath()), fn($s) => $s !== ''));
        $data = $base;
        $data['doc_id'] .= 'file:' . $file->get_filepath() . $file->get_filename();
        $data['title'] = $file->get_filename();
        $data['header_path'] = array_merge($base['header_path'], $folders, [$file->get_filename()]);
        if ($cm->modname === 'folder') {
            $data['module_url'] = \moodle_url::make_pluginfile_url(
                $context->id,
                'mod_folder',
                'content',
                0,
                $file->get_filepath(),
                $file->get_filename()
            )->out(false);
        }
        $data['timemodified'] = (int)$file->get_timemodified();
        $data['kind'] = 'file';
        $data['file'] = [
            'sha256' => $sha,
            'filename' => $file->get_filename(),
            'mimetype' => $file->get_mimetype(),
            'filesize' => (int)$file->get_filesize(),
        ];
        $data['content_hash'] = $sha;
        return new document($data, $file);
    }

    /**
     * Allowed files of a module content area.
     *
     * @param \context_module $context
     * @param string $component
     * @return \stored_file[]
     */
    private function files(\context_module $context, string $component): array {
        $allowed = self::allowed_extensions();
        $maxbytes = self::max_bytes();
        $out = [];
        $files = get_file_storage()->get_area_files($context->id, $component, 'content', 0, 'filepath, filename', false);
        foreach ($files as $file) {
            $ext = strtolower(pathinfo($file->get_filename(), PATHINFO_EXTENSION));
            if (!in_array($ext, $allowed, true) || $file->get_filesize() > $maxbytes) {
                continue;
            }
            $out[] = $file;
        }
        return $out;
    }

    /**
     * Allowed lower-case extensions (setting filetypes).
     *
     * @return string[]
     */
    public static function allowed_extensions(): array {
        $raw = get_config('local_llmchat', 'filetypes');
        if ($raw === false || trim($raw) === '') {
            $raw = self::DEFAULT_FILETYPES;
        }
        return array_values(array_filter(array_map(
            fn($e) => ltrim(strtolower(trim($e)), '.'),
            preg_split('/[\s,;]+/', $raw)
        )));
    }

    /**
     * Maximum file size in bytes (setting maxfilesize, MB).
     *
     * @return int
     */
    public static function max_bytes(): int {
        $mb = (int)get_config('local_llmchat', 'maxfilesize');
        return ($mb > 0 ? $mb : self::DEFAULT_MAXFILESIZE_MB) * 1024 * 1024;
    }

    /**
     * Rewrite @@PLUGINFILE@@ placeholders to absolute URLs.
     *
     * @param string $text
     * @param \context_module $context
     * @param string $component
     * @param string $area
     * @return string
     */
    private function rewrite(string $text, \context_module $context, string $component, string $area): string {
        return file_rewrite_pluginfile_urls($text, 'pluginfile.php', $context->id, $component, $area, null);
    }

    /**
     * Whether html carries any text or media.
     *
     * @param string $html
     * @return bool
     */
    private function has_content(string $html): bool {
        if (trim(html_entity_decode(strip_tags($html), ENT_QUOTES | ENT_HTML5, 'UTF-8'), " \t\n\r\0\x0B\xC2\xA0") !== '') {
            return true;
        }
        return (bool)preg_match('/<(img|video|audio|iframe|object)\b/i', $html);
    }
}
