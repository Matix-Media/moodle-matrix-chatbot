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
 * Site secret storage and chat-token key derivation.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

namespace local_llmchat;

/**
 * The per-site secret shared with the chat API.
 *
 * The secret is stored encrypted with {@see \core\encryption} (the same format the
 * admin_setting_encryptedpassword setting writes) and is never displayed again. Browser chat
 * tokens are signed with a key derived from it via HKDF so the raw secret is never a MAC key.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
final class secret {
    /** HKDF info string for the chat-token key; shared with the API. */
    public const CHAT_TOKEN_INFO = 'chat-token';

    /**
     * The decrypted secret, or null when not configured.
     *
     * @return string|null
     */
    public static function get(): ?string {
        $stored = get_config('local_llmchat', 'secret');
        if ($stored === false || $stored === '' || $stored === null) {
            return null;
        }
        try {
            $plain = trim(\core\encryption::decrypt($stored));
        } catch (\Throwable $e) {
            debugging('local_llmchat: stored secret cannot be decrypted', DEBUG_DEVELOPER);
            return null;
        }
        return $plain === '' ? null : $plain;
    }

    /**
     * Store a new secret (encrypted).
     *
     * @param string $secret
     */
    public static function set(string $secret): void {
        $secret = trim($secret);
        set_config('secret', $secret === '' ? '' : \core\encryption::encrypt($secret), 'local_llmchat');
    }

    /**
     * Whether a secret is configured.
     *
     * @return bool
     */
    public static function is_configured(): bool {
        return self::get() !== null;
    }

    /**
     * Derive the chat-token signing key from a secret.
     *
     * HKDF-SHA256 with empty salt, info "chat-token", 32 bytes (RFC 5869).
     *
     * @param string $secret
     * @return string 32 raw bytes
     */
    public static function derive_chat_key(string $secret): string {
        return hash_hkdf('sha256', $secret, 32, self::CHAT_TOKEN_INFO, '');
    }

    /**
     * The chat-token key for the configured secret.
     *
     * @return string 32 raw bytes
     * @throws \moodle_exception when no secret is configured
     */
    public static function chat_key(): string {
        $secret = self::get();
        if ($secret === null) {
            throw new \moodle_exception('error:notconfigured', 'local_llmchat');
        }
        return self::derive_chat_key($secret);
    }

    /**
     * Constant-time comparison of a presented secret with the configured one.
     *
     * @param string $presented
     * @return bool
     */
    public static function matches(string $presented): bool {
        $secret = self::get();
        if ($secret === null || $presented === '') {
            return false;
        }
        return hash_equals($secret, $presented);
    }
}
