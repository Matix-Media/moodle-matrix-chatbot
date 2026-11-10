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
 * Shared push helpers for full and incremental sync.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat\sync;

use local_llmchat\api_client;
use local_llmchat\content\document;

/**
 * Pushes documents in batches and uploads the blobs the API asks for.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class pusher {
    /** Documents per POST /ingest/documents request. */
    public const BATCH = 50;

    /**
     * Constructor.
     *
     * @param api_client $client
     */
    public function __construct(
        /** @var api_client */
        protected api_client $client,
    ) {
    }

    /**
     * Push documents, then upload missing blobs.
     *
     * @param document[] $documents
     * @param string[] $extrablobs sha256s the API already reported missing (manifest need_blobs)
     * @return array{documents: int, blobs: int}
     */
    public function push_documents(array $documents, array $extrablobs = []): array {
        $needed = array_fill_keys($extrablobs, true);
        foreach (array_chunk($documents, self::BATCH) as $batch) {
            $result = $this->client->push_documents(array_map(fn(document $d) => $d->to_wire(), $batch));
            foreach ($result['needs_blob'] ?? [] as $sha) {
                $needed[$sha] = true;
            }
        }
        return ['documents' => count($documents), 'blobs' => $this->upload_blobs(array_keys($needed), $documents)];
    }

    /**
     * Upload the requested blobs from the matching documents' stored files.
     *
     * @param string[] $shas
     * @param document[] $documents
     * @return int uploaded
     */
    public function upload_blobs(array $shas, array $documents): int {
        $files = [];
        foreach ($documents as $doc) {
            if ($doc->file && $doc->sha256()) {
                $files[$doc->sha256()] = $doc->file;
            }
        }
        $count = 0;
        foreach ($shas as $sha) {
            if (isset($files[$sha])) {
                $this->client->put_blob($sha, $files[$sha]->get_content());
                $count++;
            }
        }
        return $count;
    }
}
