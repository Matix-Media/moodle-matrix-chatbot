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
 * Reconnecting websocket with auth frame, heartbeat and exponential backoff (1 s → 30 s).
 *
 * The token is never put in the URL: the first frame is auth{token}. After every (re)connect
 * the owner gets onReady() and is expected to send resume{conversation_id, last_seq}.
 *
 * @module     local_llmchat/socket
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

export const PROTOCOL_VERSION = 1;
const HEARTBEAT_MS = 30000;
const MAX_MISSED_PONGS = 2;
const MIN_BACKOFF_MS = 1000;
const MAX_BACKOFF_MS = 30000;

export default class ReconnectingSocket {
    /**
     * Constructor.
     *
     * @param {Object} options
     * @param {Function} options.credentials async () => {wsurl, token}
     * @param {Function} options.onFrame (frame) => void, for every server frame except pong
     * @param {Function} options.onReady (readyFrame) => void, after auth succeeded
     * @param {Function} options.onStatus (online: boolean) => void
     * @param {Function} [options.WebSocketImpl] injectable for tests
     */
    constructor({credentials, onFrame, onReady, onStatus, WebSocketImpl}) {
        this.credentials = credentials;
        this.onFrame = onFrame;
        this.onReady = onReady;
        this.onStatus = onStatus;
        this.WebSocketImpl = WebSocketImpl || window.WebSocket;
        this.ws = null;
        this.ready = false;
        this.closed = false;
        this.backoff = MIN_BACKOFF_MS;
        this.missedPongs = 0;
        this.heartbeat = null;
        this.reconnectTimer = null;
    }

    /**
     * Open (or re-open) the connection.
     */
    async connect() {
        if (this.closed) {
            return;
        }
        let creds;
        try {
            creds = await this.credentials();
        } catch (e) {
            this.scheduleReconnect();
            return;
        }
        const ws = new this.WebSocketImpl(creds.wsurl);
        this.ws = ws;
        ws.onopen = () => {
            ws.send(JSON.stringify({v: PROTOCOL_VERSION, type: 'auth', token: creds.token}));
        };
        ws.onmessage = (event) => this.handleMessage(ws, event);
        ws.onclose = () => this.handleClose(ws);
        ws.onerror = () => {
            // A close event always follows; reconnect happens there.
        };
    }

    /**
     * Handle a raw message.
     *
     * @param {WebSocket} ws
     * @param {MessageEvent} event
     */
    handleMessage(ws, event) {
        if (ws !== this.ws) {
            return;
        }
        let frame;
        try {
            frame = JSON.parse(event.data);
        } catch (e) {
            return;
        }
        if (!frame || typeof frame.type !== 'string') {
            return;
        }
        if (frame.type === 'pong') {
            this.missedPongs = 0;
            return;
        }
        if (frame.type === 'ping') {
            this.sendRaw({type: 'pong'});
            return;
        }
        if (frame.type === 'ready' && !this.ready) {
            this.ready = true;
            this.backoff = MIN_BACKOFF_MS;
            this.startHeartbeat();
            this.onStatus(true);
            this.onReady(frame);
            return;
        }
        this.onFrame(frame);
    }

    /**
     * Handle a close: mark offline and reconnect with backoff.
     *
     * @param {WebSocket} ws
     */
    handleClose(ws) {
        if (ws !== this.ws) {
            return;
        }
        this.stopHeartbeat();
        this.ready = false;
        this.ws = null;
        this.onStatus(false);
        this.scheduleReconnect();
    }

    /**
     * Schedule the next connect attempt.
     */
    scheduleReconnect() {
        if (this.closed || this.reconnectTimer) {
            return;
        }
        const delay = this.backoff;
        this.backoff = Math.min(this.backoff * 2, MAX_BACKOFF_MS);
        this.reconnectTimer = setTimeout(() => {
            this.reconnectTimer = null;
            this.connect();
        }, delay);
    }

    /**
     * Ping every 30 s; two missed pongs = dead connection.
     */
    startHeartbeat() {
        this.stopHeartbeat();
        this.missedPongs = 0;
        this.heartbeat = setInterval(() => {
            if (this.missedPongs >= MAX_MISSED_PONGS) {
                this.drop();
                return;
            }
            this.missedPongs++;
            this.sendRaw({type: 'ping'});
        }, HEARTBEAT_MS);
    }

    /**
     * Stop the heartbeat.
     */
    stopHeartbeat() {
        if (this.heartbeat) {
            clearInterval(this.heartbeat);
            this.heartbeat = null;
        }
    }

    /**
     * Force-close the current socket (reconnect follows).
     */
    drop() {
        const ws = this.ws;
        if (ws) {
            try {
                ws.close();
            } catch (e) {
                // Ignore.
            }
            this.handleClose(ws);
        }
    }

    /**
     * Whether frames can be sent now.
     *
     * @returns {boolean}
     */
    isReady() {
        return this.ready && this.ws !== null;
    }

    /**
     * Send a protocol frame (adds v).
     *
     * @param {Object} frame
     * @returns {boolean} false when not connected
     */
    send(frame) {
        if (!this.isReady()) {
            return false;
        }
        return this.sendRaw(frame);
    }

    /**
     * Send without readiness check.
     *
     * @param {Object} frame
     * @returns {boolean}
     */
    sendRaw(frame) {
        if (!this.ws) {
            return false;
        }
        try {
            this.ws.send(JSON.stringify(Object.assign({v: PROTOCOL_VERSION}, frame)));
            return true;
        } catch (e) {
            return false;
        }
    }

    /**
     * Close for good.
     */
    close() {
        this.closed = true;
        this.stopHeartbeat();
        if (this.reconnectTimer) {
            clearTimeout(this.reconnectTimer);
        }
        if (this.ws) {
            this.ws.close();
        }
    }
}
