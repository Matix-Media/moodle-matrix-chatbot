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
 * One DocumentIn record.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat\content;

/**
 * One document as pushed to POST /ingest/documents, plus the stored file for blob upload.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
final class document {
    /** Fields that say where a document sits; placement_hash covers exactly these. */
    private const PLACEMENT_FIELDS = [
        'section_id', 'section_order', 'section_name', 'module_name', 'title', 'header_path', 'module_url',
    ];

    /**
     * Constructor.
     *
     * @param array $data DocumentIn fields
     * @param \stored_file|null $file the file behind a kind=file document
     */
    public function __construct(
        /** @var array DocumentIn fields */
        private array $data,
        /** @var \stored_file|null file behind a kind=file document */
        public readonly ?\stored_file $file = null,
    ) {
        // Always recomputed: callers copy one document's data into another and edit it.
        $this->data['placement_hash'] = self::placement_hash($this->data);
    }

    /**
     * Hash of a document's placement, so the API notices a moved or renamed section
     * even when the content (and so content_hash) is unchanged.
     *
     * @param array $data DocumentIn fields
     * @return string sha256 hex
     */
    public static function placement_hash(array $data): string {
        $placement = [];
        foreach (self::PLACEMENT_FIELDS as $field) {
            $placement[$field] = $data[$field] ?? null;
        }
        return hash('sha256', json_encode($placement, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES));
    }

    /**
     * The doc id "{course_id}:{cmid}:{part}".
     *
     * @return string
     */
    public function id(): string {
        return $this->data['doc_id'];
    }

    /**
     * Wire representation.
     *
     * @return array
     */
    public function to_wire(): array {
        return $this->data;
    }

    /**
     * Blob sha256 for file documents.
     *
     * @return string|null
     */
    public function sha256(): ?string {
        return $this->data['file']['sha256'] ?? null;
    }

    /**
     * Manifest entry for POST /ingest/manifest.
     *
     * @return array
     */
    public function manifest_entry(): array {
        return [
            'doc_id' => $this->data['doc_id'],
            'timemodified' => $this->data['timemodified'],
            'content_hash' => $this->data['content_hash'],
            'placement_hash' => $this->data['placement_hash'],
        ];
    }
}
