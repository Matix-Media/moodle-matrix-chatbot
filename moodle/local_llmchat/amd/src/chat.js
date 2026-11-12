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
 * Course chat client. Streams tokens, thinking, tool calls and interactions from the API
 * websocket and renders them with local_llmchat templates (theme-overridable, theme icons).
 * Reconnects with resume{last_seq}, offers "Continue" after an interrupted turn and shows the
 * token budget. A fresh chat token is fetched before every send.
 *
 * Frames are handled strictly in order through a promise queue, because template rendering is
 * asynchronous and a stream must never be reordered.
 *
 * @module     local_llmchat/chat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

import Ajax from 'core/ajax';
import Log from 'core/log';
import {getStrings} from 'core/str';
import {add as addToast} from 'core/toast';
import ReconnectingSocket from 'local_llmchat/socket';
import * as activity from 'local_llmchat/activity';
import * as frames from 'local_llmchat/frames';
import {render as renderMarkdown, escapeHtml} from 'local_llmchat/markdown';
import {rendererFor} from 'local_llmchat/interactions/registry';
import {renderNode, renderInto, uniqid} from 'local_llmchat/render';
import {
    callTarget, citationMeta, describeCall, describeResult, linkSegments, RESULT_STRING_KEYS, STRING_KEYS as TOOL_STRING_KEYS,
} from 'local_llmchat/tooldetails';

const STRING_KEYS = [
    'cancelled', 'copied', 'error', 'newconversation', 'offline', 'queuedmessage', 'quota:exemptuser', 'quota:remaining',
    'quota:remainingmonth', 'quota:thismonth', 'quota:today', 'quota:usedof', 'ratelimited:burst', 'ratelimited:site',
    'ratelimited:user', 'reconnected', 'resume',
    'turninterrupted', 'unknowninteraction', 'untitled',
];

const SEQ_STORAGE_KEY = 'local_llmchat_lastseq';

/** Current conversation per course (0 = global chat), so the block and the full page agree. */
const CURRENT_STORAGE_KEY = 'local_llmchat_current';

/** Circumference of the token ring (r = 15 in a 36-unit viewBox). */
const RING_CIRCUMFERENCE = 2 * Math.PI * 15;

/** Space kept below the chat card. */
const BOTTOM_GAP = 16;

/** Moodle's floating page controls (help button, drawer toggles) that may sit over the card. */
const FLOATING_CONTROLS = [
    '.btn-footer-popover', '.drawer-toggles .drawer-toggler', '[data-region="footer-container-popover"] .btn',
].join(', ');

/** Server frame type => controller method (frame, replay). */
const FRAME_HANDLERS = {
    'user_message': 'onUserMessage',
    'thinking': 'onThinking',
    'token': 'onToken',
    'tool_call': 'onToolCall',
    'tool_progress': 'onToolProgress',
    'tool_result': 'onToolResult',
    'question': 'onQuestion',
    'citations': 'onCitations',
    'final': 'onFinal',
    'cancelled': 'onCancelled',
    'turn_interrupted': 'onInterrupted',
    'rate_limited': 'onRateLimited',
    'quota': 'onQuota',
    'error': 'onError',
};

/** Tool name => pix icon, used when the API's tool metadata names no pix icon. */
const TOOL_ICONS = {
    'search_content': ['a/search', 'core'],
    'search_text': ['a/search', 'core'],
    'search_regex': ['a/search', 'core'],
    'list_dir': ['i/folder', 'core'],
    'stat_file': ['i/files', 'core'],
    'read_file': ['i/files', 'core'],
    'list_documents': ['i/calendar', 'core'],
    'current_datetime': ['i/calendar', 'core'],
    'current_user': ['i/user', 'core'],
    'ask_user': ['i/question', 'core'],
};

/**
 * Fill a {$a} / {$a->x} placeholder string.
 *
 * @param {string} template
 * @param {*} a
 * @returns {string}
 */
const fill = (template, a) => String(template)
    .replace(/\{\$a->(\w+)\}/g, (m, k) => (a && a[k] !== undefined ? String(a[k]) : ''))
    .replace(/\{\$a\}/g, a === undefined || a === null ? '' : String(a));

/**
 * Random id for client_msg_id.
 *
 * @returns {string}
 */
const uuid = () => (window.crypto && window.crypto.randomUUID ? window.crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`);

/**
 * Format a timestamp (seconds or ISO string) for display.
 *
 * @param {number|string} value
 * @returns {string}
 */
const formatTime = (value) => {
    if (value === undefined || value === null || value === '') {
        return '';
    }
    const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value);
    return isNaN(date.getTime()) ? String(value) : date.toLocaleString();
};

const formatNumber = (n) => (typeof n === 'number' ? n.toLocaleString() : (n ?? '–'));

/**
 * Pix icon for a tool: an icon named by the API as "component:key" wins, then the name table.
 *
 * @param {string} name
 * @param {Object} meta tool metadata from ready{tools}
 * @returns {Array} [key, component]
 */
const toolIcon = (name, meta) => {
    const named = String(meta.icon || '').match(/^([a-z0-9_]+):([a-z0-9_/-]+)$/);
    if (named) {
        return [named[2], named[1]];
    }
    return TOOL_ICONS[name] || ['tool', 'local_llmchat'];
};

class ChatController {
    /**
     * Constructor.
     *
     * @param {HTMLElement} root
     * @param {Object} config {courseid, conversationid, preloaded}
     * @param {Object} strings
     */
    constructor(root, config, strings) {
        this.root = root;
        this.courseid = Number(config.courseid) || 0;
        // Compact: the chat block, one conversation, no list (the full page has the list).
        this.compact = root.dataset.mode === 'compact';
        this.initialConversation = config.conversationid ? String(config.conversationid) : '';
        // What the page already shows: the list, and a conversation up to a seq.
        this.preloaded = config.preloaded || {};
        this.str = strings;
        this.regions = {};
        ['messages', 'empty', 'conversations', 'status', 'quota', 'quota-button', 'quota-ring', 'quota-percent', 'quota-panel',
            'input', 'composer', 'panel', 'sidebar', 'title', 'expand'].forEach((name) => {
            this.regions[name] = root.querySelector(`[data-region="${name}"]`);
        });
        this.sendButton = root.querySelector('[data-action="send"]');
        this.cancelButton = root.querySelector('[data-action="cancel"]');
        this.conversationId = null;
        this.lastSeq = this.loadSeqs();
        this.tools = new Map();
        this.turn = null;
        this.queued = null;
        this.pendingUserBubbles = new Map();
        this.queue = Promise.resolve();
        this.offlineNotice = null;
        this.wasOffline = false;
        this.stickToBottom = true;
        this.linkCache = new Map();

        this.socket = new ReconnectingSocket({
            credentials: async() => {
                const t = await this.issueToken();
                return {wsurl: t.wsurl, token: t.token};
            },
            onFrame: (frame) => this.handleFrame(frame, false),
            onReady: (frame) => this.enqueue(() => this.handleReady(frame)),
            onStatus: (online) => this.enqueue(() => this.setOnline(online)),
        });
        this.bind();
    }

    /**
     * Run work after everything queued before it.
     *
     * @param {Function} task () => (Promise|void)
     * @returns {Promise}
     */
    enqueue(task) {
        this.queue = this.queue.then(task).catch((e) => Log.error(e));
        return this.queue;
    }

    /**
     * Wire DOM events.
     */
    bind() {
        const input = this.regions.input;
        this.regions.composer.addEventListener('submit', (e) => {
            e.preventDefault();
            this.submit();
        });
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
                e.preventDefault();
                this.submit();
            }
        });
        input.addEventListener('input', () => this.autosize());
        this.cancelButton.addEventListener('click', () => {
            if (this.conversationId) {
                this.socket.send(frames.cancel({conversationId: this.conversationId}));
            }
        });
        this.regions.messages.addEventListener('scroll', () => {
            const m = this.regions.messages;
            this.stickToBottom = m.scrollTop + m.clientHeight >= m.scrollHeight - 48;
        });
        this.root.addEventListener('click', (e) => this.onClick(e));
        // The token panel closes on Escape and on any click outside it.
        document.addEventListener('click', (e) => {
            if (!this.regions.quota.contains(e.target)) {
                this.toggleQuotaPanel(false);
            }
        });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this.regions['quota-panel'].classList.contains('show')) {
                this.toggleQuotaPanel(false);
                this.regions['quota-button'].focus();
            }
        });
    }

    /**
     * Delegated clicks: toggles, suggestions, conversation list, new conversation, copy.
     *
     * @param {MouseEvent} e
     */
    onClick(e) {
        const target = e.target.closest('[data-action]');
        if (!target || !this.root.contains(target)) {
            return;
        }
        const action = target.dataset.action;
        if (action === 'toggle') {
            const panel = document.getElementById(target.getAttribute('aria-controls'));
            if (panel) {
                const open = panel.classList.toggle('show');
                target.setAttribute('aria-expanded', open ? 'true' : 'false');
            }
        } else if (action === 'quota-toggle') {
            this.toggleQuotaPanel(!this.regions['quota-panel'].classList.contains('show'));
        } else if (action === 'suggest') {
            this.regions.input.value = target.dataset.text;
            this.submit();
        } else if (action === 'open-conversation') {
            this.closeSidebarOnMobile();
            this.openConversation(target.dataset.id);
        } else if (action === 'new-conversation') {
            this.closeSidebarOnMobile();
            this.openConversation(null);
        } else if (action === 'copy') {
            this.copy(target.dataset.copytext || '');
        }
    }

    /**
     * Put an answer on the clipboard.
     *
     * @param {string} text
     */
    async copy(text) {
        try {
            await navigator.clipboard.writeText(text);
            addToast(this.str.copied);
        } catch (e) {
            Log.debug(e);
        }
    }

    /**
     * Open or close the token budget panel behind the ring.
     *
     * @param {boolean} open
     */
    toggleQuotaPanel(open) {
        this.regions['quota-panel'].classList.toggle('show', open);
        this.regions['quota-button'].setAttribute('aria-expanded', open ? 'true' : 'false');
    }

    /**
     * Collapse the conversation list again after a pick on small screens.
     */
    closeSidebarOnMobile() {
        if (!this.regions.sidebar) {
            return;
        }
        this.regions.sidebar.classList.remove('show');
        this.root.querySelectorAll(`[aria-controls="${this.regions.sidebar.id}"]`)
            .forEach((b) => b.setAttribute('aria-expanded', 'false'));
    }

    /**
     * Start. The block waits until it is first on screen (the block drawer may stay closed), so
     * a page with the block costs no token, socket or API call until the chat is looked at.
     */
    start() {
        if (!this.compact) {
            // Boost-based themes are sized by styles.css from the first paint; other layouts
            // get the card fitted to the window here.
            if (!document.body.classList.contains('uses-drawers')) {
                this.fitHeight();
                window.addEventListener('resize', () => this.fitHeight());
            }
            this.run();
            return;
        }
        if (typeof window.IntersectionObserver !== 'function') {
            this.run();
            return;
        }
        const observer = new window.IntersectionObserver((entries) => {
            if (entries.some((entry) => entry.isIntersecting)) {
                observer.disconnect();
                this.run();
            }
        });
        observer.observe(this.root);
    }

    /**
     * Connect and open the conversation to start with: the one asked for in the URL, in the block
     * the one last open in this scope, otherwise a new one.
     */
    run() {
        this.socket.connect();
        const id = this.initialConversation || (this.compact ? this.loadCurrent() : '');
        if (id && this.preloaded.conversationid === id) {
            this.adoptConversation(id, Number(this.preloaded.lastseq) || 0);
        } else if (id) {
            this.openConversation(id, {focus: false, restore: !this.initialConversation});
        } else if (!this.preloaded.list) {
            this.refreshConversations();
        }
    }

    /**
     * Take over a conversation the page rendered up to lastSeq: nothing to fetch, the socket's
     * resume brings whatever came after (a running turn, a pending question).
     *
     * @param {string} id
     * @param {number} lastSeq
     */
    adoptConversation(id, lastSeq) {
        this.enqueue(() => {
            this.setConversation(id);
            // Exactly the page's state, even if this tab saw more before a reload.
            this.lastSeq[id] = lastSeq;
            this.saveSeqs();
            this.scroll(true);
        });
    }

    /**
     * The conversation last open in this scope (sessionStorage), or ''.
     *
     * @returns {string}
     */
    loadCurrent() {
        try {
            const current = JSON.parse(window.sessionStorage.getItem(CURRENT_STORAGE_KEY) || '{}') || {};
            return current[this.courseid] ? String(current[this.courseid]) : '';
        } catch (e) {
            return '';
        }
    }

    /**
     * Make a conversation the current one (null = new): remembered for this scope, and the
     * block's "open in full view" link opens it.
     *
     * @param {string|null} id
     */
    setConversation(id) {
        this.conversationId = id;
        try {
            const current = JSON.parse(window.sessionStorage.getItem(CURRENT_STORAGE_KEY) || '{}') || {};
            if (id) {
                current[this.courseid] = id;
            } else {
                delete current[this.courseid];
            }
            window.sessionStorage.setItem(CURRENT_STORAGE_KEY, JSON.stringify(current));
        } catch (e) {
            // Storage unavailable: the block starts with a new conversation next time.
        }
        if (this.regions.expand) {
            const url = new URL(this.regions.expand.href, window.location.href);
            if (id) {
                url.searchParams.set('conversation', id);
            } else {
                url.searchParams.delete('conversation');
            }
            this.regions.expand.href = url.toString();
        }
    }

    /**
     * Make the chat card fill the window below its top edge, with the composer always in view.
     * Only for themes without Boost's drawer layout, which styles.css sizes.
     */
    fitHeight() {
        const panel = this.regions.panel;
        const box = panel.getBoundingClientRect();
        const top = box.top + window.scrollY;
        // Keep the composer clear of floating controls that overlap the card horizontally.
        let gap = BOTTOM_GAP;
        document.querySelectorAll(FLOATING_CONTROLS).forEach((control) => {
            const r = control.getBoundingClientRect();
            const fixed = window.getComputedStyle(control).position === 'fixed' ||
                window.getComputedStyle(control.offsetParent || control).position === 'fixed';
            if (r.width && fixed && r.right > box.left && r.left < box.right) {
                gap = Math.max(gap, window.innerHeight - r.top + 8);
            }
        });
        const height = Math.max(440, window.innerHeight - top - gap);
        panel.style.height = `${height}px`;
        if (!this.regions.sidebar) {
            return;
        }
        if (window.matchMedia('(min-width: 992px)').matches) {
            this.regions.sidebar.style.height = `${height}px`;
        } else {
            this.regions.sidebar.style.height = '';
        }
    }

    /**
     * Grow the composer with its content, up to a limit.
     */
    autosize() {
        const input = this.regions.input;
        input.style.height = 'auto';
        input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
    }

    /**
     * Fresh chat token (sesskey-protected AJAX).
     *
     * @returns {Promise<Object>} {token, expires, apiurl, wsurl}
     */
    async issueToken() {
        const result = await Ajax.call([{methodname: 'local_llmchat_issue_token', args: {courseid: this.courseid}}])[0];
        this.apiurl = result.apiurl;
        return result;
    }

    /**
     * Last seq per conversation, kept across reloads in sessionStorage.
     *
     * @returns {Object}
     */
    loadSeqs() {
        try {
            return JSON.parse(window.sessionStorage.getItem(SEQ_STORAGE_KEY) || '{}') || {};
        } catch (e) {
            return {};
        }
    }

    /**
     * Persist last seqs.
     */
    saveSeqs() {
        try {
            window.sessionStorage.setItem(SEQ_STORAGE_KEY, JSON.stringify(this.lastSeq));
        } catch (e) {
            // Storage unavailable: resume still works within this page.
        }
    }

    /**
     * Online/offline notice in the status area; a toast once the connection is back.
     *
     * @param {boolean} online
     */
    async setOnline(online) {
        if (online) {
            if (this.offlineNotice) {
                this.offlineNotice.remove();
                this.offlineNotice = null;
            }
            if (this.wasOffline) {
                addToast(this.str.reconnected, {type: 'success'});
                this.wasOffline = false;
            }
            return;
        }
        this.wasOffline = true;
        if (!this.offlineNotice) {
            this.offlineNotice = await renderNode('notice', {
                type: 'warning', icon: 'offline', component: 'local_llmchat', message: this.str.offline, spinner: true,
            });
            this.offlineNotice.classList.add('mb-0', 'rounded-0', 'border-start-0', 'border-end-0');
            this.regions.status.prepend(this.offlineNotice);
        }
    }

    /**
     * After (re)authentication: remember tools, resume the open conversation, flush the queue.
     *
     * @param {Object} frame ready{tools, server_time}
     */
    handleReady(frame) {
        (frame.tools || []).forEach((tool) => this.tools.set(tool.name, tool));
        if (this.conversationId) {
            this.socket.send(frames.resume({conversationId: this.conversationId,
                lastSeq: this.lastSeq[this.conversationId]}));
        }
        if (this.queued) {
            const queued = this.queued;
            this.queued = null;
            queued.note.textContent = '';
            this.dispatchSend(queued.text, queued.clientMsgId);
        }
    }

    /**
     * Composer submit.
     */
    submit() {
        const text = this.regions.input.value.trim();
        if (!text) {
            return;
        }
        this.regions.input.value = '';
        this.autosize();
        const clientMsgId = uuid();
        this.enqueue(async() => {
            const bubble = await this.addUserBubble(text);
            this.pendingUserBubbles.set(clientMsgId, bubble);
            if (!this.socket.isReady()) {
                // Offline: keep at most one unsent message.
                if (this.queued) {
                    this.queued.bubble.remove();
                }
                const note = bubble.querySelector('[data-region="note"]');
                note.textContent = this.str.queuedmessage;
                this.queued = {text, clientMsgId, bubble, note};
                return;
            }
            this.dispatchSend(text, clientMsgId);
        });
    }

    /**
     * Send with a fresh token (scope refresh per turn).
     *
     * @param {string} text
     * @param {string} clientMsgId
     */
    async dispatchSend(text, clientMsgId) {
        this.clearStatusErrors();
        try {
            const t = await this.issueToken();
            const sent = this.socket.send(frames.send({text, token: t.token, clientMsgId,
                conversationId: this.conversationId, courseId: this.courseid}));
            if (sent) {
                this.setRunning(true);
            } else {
                const bubble = this.pendingUserBubbles.get(clientMsgId);
                const note = bubble.querySelector('[data-region="note"]');
                note.textContent = this.str.queuedmessage;
                this.queued = {text, clientMsgId, bubble, note};
            }
        } catch (e) {
            this.showStatus('error', e && e.message ? e.message : String(e));
        }
    }

    /**
     * Toggle running state: the send button becomes a stop button.
     *
     * @param {boolean} running
     */
    setRunning(running) {
        this.running = running;
        this.cancelButton.classList.toggle('d-none', !running);
        this.sendButton.classList.toggle('d-none', running);
    }

    /**
     * Handle one server frame (in order, after anything queued before).
     *
     * @param {Object} frame
     * @param {boolean} replay true when re-rendering stored history
     * @returns {Promise}
     */
    handleFrame(frame, replay) {
        return this.enqueue(async() => {
            if (!this.acceptFrame(frame, replay)) {
                return;
            }
            const handler = FRAME_HANDLERS[frame.type];
            if (handler) {
                await this[handler](frame, replay);
                this.scroll();
            }
            // Unknown types are ignored (forward compatibility).
        });
    }

    /**
     * Adopt the conversation id of a new conversation, drop frames of other conversations and
     * frames already seen (resume replays everything after last_seq; duplicates are skipped).
     *
     * @param {Object} frame
     * @param {boolean} replay
     * @returns {boolean} whether the frame should be rendered
     */
    acceptFrame(frame, replay) {
        if (frame.conversation_id && !this.conversationId && !replay) {
            this.setConversation(String(frame.conversation_id));
            this.refreshConversations();
        }
        const convId = frame.conversation_id ? String(frame.conversation_id) : this.conversationId;
        if (convId && convId !== this.conversationId) {
            return false;
        }
        if (typeof frame.seq === 'number' && convId) {
            const last = this.lastSeq[convId] || 0;
            if (!replay && frame.seq <= last) {
                return false;
            }
            this.lastSeq[convId] = Math.max(last, frame.seq);
            this.saveSeqs();
        }
        return true;
    }

    /**
     * Scroll to the newest message, unless the user scrolled up to read.
     *
     * @param {boolean} [force]
     */
    scroll(force = false) {
        const m = this.regions.messages;
        if (force || this.stickToBottom) {
            m.scrollTop = m.scrollHeight;
        }
    }

    /**
     * Append a node to the message list (hiding the empty state).
     *
     * @param {HTMLElement} node
     */
    appendMessage(node) {
        this.regions.empty.classList.add('d-none');
        this.regions.messages.appendChild(node);
    }

    /**
     * Add a user bubble.
     *
     * @param {string} text
     * @returns {Promise<HTMLElement>}
     */
    async addUserBubble(text) {
        const bubble = await renderNode('message_user', {text});
        this.appendMessage(bubble);
        this.turn = null;
        this.scroll(true);
        return bubble;
    }

    /**
     * Server echo of a user message.
     *
     * @param {Object} frame
     * @param {boolean} replay
     */
    async onUserMessage(frame, replay) {
        const known = frame.client_msg_id && this.pendingUserBubbles.has(frame.client_msg_id);
        if (known) {
            this.pendingUserBubbles.delete(frame.client_msg_id);
        } else {
            await this.addUserBubble(String(frame.text ?? (frame.message && frame.message.text) ?? ''));
        }
        this.turn = null;
        if (!replay) {
            // Open the assistant message right away so the typing indicator shows while waiting.
            this.setRunning(true);
            await this.currentTurn(frame);
        }
    }

    /**
     * The assistant message of the current turn (rendered on demand).
     *
     * @param {Object} frame
     * @returns {Promise<Object>}
     */
    async currentTurn(frame) {
        const turnId = frame.turn_id ?? null;
        if (this.turn && (turnId === null || this.turn.id === null || this.turn.id === turnId)) {
            if (this.turn.id === null) {
                this.turn.id = turnId;
            }
            return this.turn;
        }
        const node = await renderNode('message_assistant');
        this.appendMessage(node);
        const region = (name) => node.querySelector(`[data-region="${name}"]`);
        this.turn = {
            id: turnId, node, body: region('body'), text: region('text'), typing: region('typing'), raw: '',
            thinking: null, toolGroup: null, cards: new Map(), citations: [], activity: activity.initial(),
        };
        return this.turn;
    }

    /**
     * Track what the turn is doing (local_llmchat/activity): close the live thinking block when the
     * step moves on, and show the working indicator whenever the model works with nothing streaming
     * (e.g. after the last tool result, while it decides what to do next).
     *
     * @param {Object} turn
     * @param {string} type frame type, or 'user_answer' / 'continue' for what the client sent
     * @returns {Object} {before, after} activity states
     */
    advance(turn, type) {
        const before = turn.activity;
        const after = activity.step(before, type);
        turn.activity = after;
        if (activity.endsThinking(before, after)) {
            this.finishThinking(turn);
        }
        turn.typing.classList.toggle('d-none', !activity.showsIndicator(after));
        return {before, after};
    }

    /**
     * Insert a node at the end of the turn, above the working indicator (content stays in stream order).
     *
     * @param {Object} turn
     * @param {HTMLElement} node
     */
    place(turn, node) {
        turn.body.insertBefore(node, turn.typing);
    }

    /**
     * The text segment for answer text: the current one if nothing was placed after it, else a new one.
     *
     * @param {Object} turn
     * @returns {HTMLElement}
     */
    textSegment(turn) {
        if (turn.text.nextElementSibling !== turn.typing) {
            this.newTextSegment(turn);
        }
        return turn.text;
    }

    /**
     * Start a new text segment at the end of the turn (text after an interaction goes below it).
     *
     * @param {Object} turn
     */
    newTextSegment(turn) {
        const text = document.createElement('div');
        text.className = 'local-llmchat-text';
        this.place(turn, text);
        turn.text = text;
        turn.raw = '';
    }

    /**
     * Reasoning delta (text only when the API sends it; otherwise an indicator). Every model step
     * that thinks gets its own block, placed in stream order in the activity group with the tool cards.
     *
     * @param {Object} frame
     */
    async onThinking(frame) {
        const turn = await this.currentTurn(frame);
        const {before, after} = this.advance(turn, 'thinking');
        if (activity.startsThinking(before, after) || !turn.thinking) {
            const node = await renderNode('thinking', {uniqid: uniqid('llmchat-thinking')});
            this.toolGroup(turn).appendChild(node);
            const q = (sel) => node.querySelector(sel);
            turn.thinking = {
                node, text: q('[data-region="thinking-text"]'), spinner: q('[data-region="spinner"]'),
                active: q('[data-region="label-active"]'), done: q('[data-region="label-done"]'),
            };
        }
        if (frame.delta) {
            turn.thinking.text.textContent += frame.delta;
        }
    }

    /**
     * Finish the live thinking block once the step moves on (answer, tool call, question, end).
     * It stays open or closed as the reader left it (it starts collapsed). A block that streamed no
     * text is dropped, and with it a group it leaves empty.
     *
     * @param {Object} turn
     */
    finishThinking(turn) {
        const t = turn.thinking;
        if (t && !t.spinner.classList.contains('d-none')) {
            t.spinner.classList.add('d-none');
            t.active.classList.add('d-none');
            t.done.classList.remove('d-none');
            if (!t.text.textContent.trim()) {
                const group = t.node.parentElement;
                t.node.remove();
                if (group && !group.children.length) {
                    group.remove();
                }
            }
        }
    }

    /**
     * Answer token.
     *
     * @param {Object} frame
     */
    async onToken(frame) {
        const turn = await this.currentTurn(frame);
        this.advance(turn, 'token');
        const text = this.textSegment(turn);
        turn.raw += frame.delta || '';
        text.innerHTML = renderMarkdown(turn.raw, turn.citations);
    }

    /**
     * Strings and locale for local_llmchat/tooldetails.
     *
     * @returns {Object}
     */
    displayOptions() {
        return {strings: this.str, locale: document.documentElement.lang || undefined};
    }

    /**
     * Tool card.
     *
     * @param {Object} frame tool_call{id, name, args, locations?}
     */
    async onToolCall(frame) {
        const turn = await this.currentTurn(frame);
        const meta = this.tools.get(frame.name) || {};
        const args = frame.args || {};
        const firstArg = callTarget(args, frame.locations, this.str['toolval:root']);
        let label;
        if (meta.summary_template) {
            label = meta.summary_template.replace(/\{(\w+)\}/g, (m, k) => (args[k] !== undefined ? String(args[k]) : ''));
        } else {
            label = `${meta.label || frame.name}${firstArg ? ` „${firstArg}“` : ''}`;
        }
        const [icon, component] = toolIcon(frame.name, meta);
        const call = describeCall(args, Object.assign({locations: frame.locations}, this.displayOptions()));
        const node = await renderNode('tool_call', Object.assign({uniqid: uniqid('llmchat-tool'), icon, component, label}, call));
        this.advance(turn, 'tool_call');
        this.toolGroup(turn).appendChild(node);
        turn.cards.set(frame.id, node);
        // Not awaited: links arrive when ready and never hold up the stream.
        this.linkPaths(node, call);
    }

    /**
     * The activity group for thinking blocks and tool cards: the current one if nothing was placed
     * after it, else a new one.
     *
     * @param {Object} turn
     * @returns {HTMLElement}
     */
    toolGroup(turn) {
        if (!turn.toolGroup || turn.toolGroup.nextElementSibling !== turn.typing) {
            turn.toolGroup = document.createElement('div');
            turn.toolGroup.className = 'list-group local-llmchat-tools';
            turn.toolGroup.dataset.region = 'tools';
            this.place(turn, turn.toolGroup);
        }
        return turn.toolGroup;
    }

    /**
     * Turn the location breadcrumbs of a tool card into links to what the user can open.
     *
     * @param {HTMLElement} card
     * @param {Object} call result of describeCall()
     */
    async linkPaths(card, call) {
        if (!call.refs.length) {
            return;
        }
        try {
            const links = await this.resolveRefs(call.refs);
            for (const param of call.params.filter((p) => p.ispath)) {
                const holder = [...card.querySelectorAll('[data-region="path"]')].find((h) => h.dataset.arg === param.arg);
                const segments = linkSegments(param.segments, links);
                if (holder && segments.some((s) => s.hasurl)) {
                    await renderInto(holder, 'local_llmchat/path_breadcrumb', {segments});
                }
            }
        } catch (e) {
            Log.debug(e);
        }
    }

    /**
     * Links for Moodle objects by id, resolved on the server for the current user; cached per page.
     *
     * @param {Array} refs [{kind, id}]
     * @returns {Promise<Object>} "kind:id" => url ('' when the user cannot open it)
     */
    async resolveRefs(refs) {
        const key = (ref) => `${ref.kind}:${ref.id}`;
        const missing = refs.filter((ref) => !this.linkCache.has(key(ref)));
        if (missing.length) {
            const request = Ajax.call([{methodname: 'local_llmchat_resolve_refs', args: {refs: missing}}])[0];
            missing.forEach((ref) => {
                this.linkCache.set(key(ref), request.then((result) => (result.find((r) => key(r) === key(ref)) || {}).url || '')
                    .catch(() => {
                        this.linkCache.delete(key(ref));
                        return '';
                    }));
            });
        }
        const out = {};
        for (const ref of refs) {
            out[key(ref)] = await this.linkCache.get(key(ref));
        }
        return out;
    }

    /**
     * Intermediate tool progress.
     *
     * @param {Object} frame
     */
    async onToolProgress(frame) {
        const card = (await this.currentTurn(frame)).cards.get(frame.id);
        const text = frame.message || frame.text || frame.summary || '';
        if (card && text) {
            card.querySelector('[data-region="summary"]').textContent = String(text);
        }
    }

    /**
     * Tool result: spinner becomes a check mark, summary and citations go into the card.
     *
     * @param {Object} frame tool_result{id, summary, citations?, attachments?}
     */
    async onToolResult(frame) {
        const turn = await this.currentTurn(frame);
        const card = turn.cards.get(frame.id);
        if (!card) {
            return;
        }
        this.advance(turn, 'tool_result');
        card.querySelector('[data-region="spinner"]').classList.add('d-none');
        card.querySelector('[data-region="done"]').classList.remove('d-none');
        card.querySelector('[data-region="summary"]').textContent = frame.summary ? String(frame.summary) : '';
        const result = describeResult(frame, this.displayOptions());
        await renderInto(card.querySelector('[data-region="result"]'), 'local_llmchat/tool_result', result);
        // Not awaited: links arrive when ready and never hold up the stream.
        this.linkEntries(card, result.refs);
    }

    /**
     * Link the names of listed folders and files to what the user can open in Moodle.
     *
     * @param {HTMLElement} card
     * @param {Array} refs [{kind, id}] of the entries
     */
    async linkEntries(card, refs) {
        if (!refs.length) {
            return;
        }
        try {
            const links = await this.resolveRefs(refs);
            card.querySelectorAll('[data-region="entry"]').forEach((holder) => {
                if (holder.querySelector('a')) {
                    return;
                }
                const found = String(links[holder.dataset.ref] || '');
                const url = /^https?:\/\//i.test(found) ? found : '';
                if (url) {
                    const link = document.createElement('a');
                    link.href = url;
                    link.target = '_blank';
                    link.rel = 'noopener';
                    link.textContent = holder.textContent;
                    holder.replaceChildren(link);
                }
            });
        } catch (e) {
            Log.debug(e);
        }
    }

    /**
     * Interaction (ask_user etc.), rendered by kind.
     *
     * @param {Object} frame question{interrupt_id, kind, payload}
     * @param {boolean} replay
     */
    async onQuestion(frame, replay) {
        const turn = await this.currentTurn(frame);
        this.advance(turn, 'question');
        const interruptId = String(frame.interrupt_id);
        turn.body.querySelectorAll('[data-interrupt]').forEach((n) => {
            if (n.dataset.interrupt === interruptId) {
                n.remove();
            }
        });
        const renderer = rendererFor(frame.kind);
        const node = await renderer.render(frame.payload || {}, (response, summary) => {
            this.answerInteraction(turn, node, interruptId, response, summary);
        }, {unknowninteraction: this.str.unknowninteraction});
        node.dataset.interrupt = interruptId;
        if (frame.error) {
            const error = document.createElement('div');
            error.className = 'text-danger small mb-2';
            error.textContent = String(frame.error);
            node.querySelector('.card-body')?.prepend(error);
        }
        this.place(turn, node);
        this.newTextSegment(turn);
        this.setRunning(false);
        if (!replay) {
            (node.querySelector('input[type="text"]') || node.querySelector('button'))?.focus({preventScroll: true});
        }
    }

    /**
     * Send an interaction answer and replace the prompt with a compact summary.
     *
     * @param {Object} turn
     * @param {HTMLElement} node
     * @param {string} interruptId
     * @param {Object} response
     * @param {string} summary
     */
    async answerInteraction(turn, node, interruptId, response, summary) {
        try {
            const t = await this.issueToken();
            this.socket.send(frames.answer({conversationId: this.conversationId, interruptId, response, token: t.token}));
            const answered = await renderNode('interaction_answered', {answer: summary});
            answered.dataset.interrupt = interruptId;
            node.replaceWith(answered);
            this.setRunning(true);
            this.advance(turn, 'user_answer');
            this.scroll();
        } catch (e) {
            this.showStatus('error', String(e && e.message ? e.message : e));
        }
    }

    /**
     * Citation list sent ahead of the final answer.
     *
     * @param {Object} frame citations{citations}
     */
    async onCitations(frame) {
        (await this.currentTurn(frame)).citations = frame.citations || [];
    }

    /**
     * Final answer with sources and actions.
     *
     * @param {Object} frame final{text, citations}
     */
    async onFinal(frame) {
        const turn = await this.currentTurn(frame);
        this.advance(turn, 'final');
        turn.citations = frame.citations || turn.citations || [];
        const text = this.textSegment(turn);
        turn.raw = frame.text ?? turn.raw;
        text.innerHTML = renderMarkdown(turn.raw, turn.citations);
        const footer = await renderNode('answer_footer', {
            copytext: turn.raw,
            budgetexhausted: Boolean(frame.budget_exhausted || frame.truncated_by_budget),
            hascitations: turn.citations.length > 0,
            citations: turn.citations.map((c) => ({
                index: c.index,
                title: c.title || c.url || '',
                meta: citationMeta(c, this.str.page),
                url: c.url || '',
                hasurl: Boolean(c.url),
            })),
        });
        this.place(turn, footer);
        this.setRunning(false);
        this.turn = null;
        this.refreshConversations();
    }

    /**
     * The API restarted mid-turn: offer "Continue".
     *
     * @param {Object} frame turn_interrupted{turn_id}
     */
    async onInterrupted(frame) {
        const turn = await this.currentTurn(frame);
        this.advance(turn, 'turn_interrupted');
        this.setRunning(false);
        const notice = await renderNode('notice', {
            type: 'info', icon: 'i/info', component: 'core', message: this.str.turninterrupted,
            hasaction: true, actionlabel: this.str.resume,
        });
        notice.querySelector('[data-action="notice-action"]').addEventListener('click', async() => {
            const t = await this.issueToken();
            if (this.socket.send(frames.continueTurn({conversationId: this.conversationId, turnId: frame.turn_id,
                token: t.token}))) {
                notice.remove();
                this.setRunning(true);
                this.advance(turn, 'continue');
            }
        });
        this.place(turn, notice);
    }

    /**
     * Budget refusal.
     *
     * @param {Object} frame rate_limited{scope, used?, limit?, resets_at}
     */
    async onRateLimited(frame) {
        this.setRunning(false);
        if (this.turn) {
            this.advance(this.turn, 'rate_limited');
        }
        const scope = ['burst', 'user', 'site'].includes(frame.scope) ? frame.scope : 'user';
        await this.showStatus('warning', fill(this.str[`ratelimited:${scope}`], formatTime(frame.resets_at)));
        if (frame.used !== undefined && frame.limit !== undefined) {
            await this.onQuota({'used_today': frame.used, 'limit_today': frame.limit, 'resets_at': frame.resets_at});
        }
    }

    /**
     * Token budget: a small ring in the header (fullest window), a tooltip, and a details panel.
     *
     * @param {Object} frame quota{used_today, limit_today, used_month, limit_month, resets_at, exempt?}
     */
    async onQuota(frame) {
        const {'used_today': usedToday, 'limit_today': limitToday, 'used_month': usedMonth, 'limit_month': limitMonth,
            'resets_at': resetsAt} = frame;
        const windows = [
            [this.str['quota:today'], this.str['quota:remaining'], usedToday, limitToday],
            [this.str['quota:thismonth'], this.str['quota:remainingmonth'], usedMonth, limitMonth],
        ].filter(([, , , limit]) => limit !== undefined && limit !== null && limit > 0);
        const rows = windows.map(([label, , used, limit]) => {
            const percent = Math.min(100, Math.round(((used || 0) / limit) * 100));
            return {
                label, percent, warning: percent >= 80 && percent < 100, danger: percent >= 100,
                usedof: fill(this.str['quota:usedof'], {used: formatNumber(used || 0), limit: formatNumber(limit)}),
            };
        });
        const exempt = Boolean(frame.exempt);
        const percent = exempt ? 0 : Math.max(0, ...rows.map((r) => r.percent));
        const ring = this.regions['quota-ring'];
        ring.setAttribute('stroke-dasharray', `${(percent / 100) * RING_CIRCUMFERENCE} ${RING_CIRCUMFERENCE}`);
        // The number inside tells the ring apart from a loading spinner.
        const label = exempt ? '∞' : `${percent}%`;
        this.regions['quota-percent'].textContent = label;
        this.regions['quota-percent'].classList.toggle('local-llmchat-ringlabel-long', label.length > 3);
        let state = '';
        if (rows.some((r) => r.danger)) {
            state = 'danger';
        } else if (rows.some((r) => r.warning)) {
            state = 'warning';
        }
        this.regions.quota.dataset.state = state;
        this.regions['quota-button'].title = exempt ? this.str['quota:exemptuser']
            : windows.map(([, text, used, limit]) => fill(text, {used: formatNumber(used || 0), limit: formatNumber(limit)}))
                .join(' · ');
        await renderInto(this.regions['quota-panel'], 'local_llmchat/quota_panel', {
            exempt, rows, hasresets: Boolean(resetsAt) && !exempt, resets: formatTime(resetsAt),
        });
        this.regions.quota.classList.toggle('invisible', !exempt && rows.length === 0);
    }

    /**
     * Error frame; a cancelled turn is shown inline instead of as an error.
     *
     * @param {Object} frame error{code, message}
     */
    async onError(frame) {
        if (frame.code === 'cancelled') {
            await this.onCancelled(frame);
            return;
        }
        this.setRunning(false);
        if (this.turn) {
            this.advance(this.turn, 'error');
        }
        await this.showStatus('error', fill(this.str.error, frame.message || frame.code || ''));
    }

    /**
     * The user stopped the turn: say so inline.
     *
     * @param {Object} frame cancelled{turn_id}
     */
    async onCancelled(frame) {
        this.setRunning(false);
        const turn = await this.currentTurn(frame);
        this.advance(turn, 'cancelled');
        const note = document.createElement('div');
        note.className = 'small text-muted';
        note.textContent = this.str.cancelled;
        this.place(turn, note);
        this.turn = null;
    }

    /**
     * Show a dismissible core notification in the chat's status area.
     *
     * @param {string} type error|warning|info|success
     * @param {string} message plain text
     * @returns {Promise<void>}
     */
    async showStatus(type, message) {
        const holder = document.createElement('div');
        holder.dataset.statusType = type;
        this.regions.status.appendChild(holder);
        await renderInto(holder, `core/notification_${type}`, {
            message: escapeHtml(message), closebutton: true, announce: true,
            extraclasses: 'mb-0 rounded-0 border-start-0 border-end-0',
        });
    }

    /**
     * Remove error and warning notifications (before a new send).
     */
    clearStatusErrors() {
        this.regions.status.querySelectorAll('[data-status-type]').forEach((n) => n.remove());
    }

    /**
     * GET from the API with the chat token.
     *
     * @param {string} path
     * @returns {Promise<Object>}
     */
    async apiGet(path) {
        const t = await this.issueToken();
        const response = await fetch(`${t.apiurl}${path}`, {
            headers: {Authorization: `Bearer ${t.token}`, Accept: 'application/json'},
            credentials: 'omit',
        });
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        return response.json();
    }

    /**
     * Reload the conversation list.
     */
    async refreshConversations() {
        if (this.compact && !this.conversationId) {
            // The block lists nothing; a new conversation needs no request for its title.
            this.regions.title.textContent = this.str.newconversation;
            return;
        }
        try {
            const data = await this.apiGet('/api/conversations');
            const items = Array.isArray(data) ? data : (data.conversations || data.items || []);
            if (this.compact) {
                // The conversation may have changed while the list loaded.
                if (!this.conversationId) {
                    this.regions.title.textContent = this.str.newconversation;
                    return;
                }
                const current = items.find((c) => String(c.id) === this.conversationId);
                this.regions.title.textContent = (current && current.title) || this.str.untitled;
                return;
            }
            await renderInto(this.regions.conversations, 'local_llmchat/conversation_list', {
                hasconversations: items.length > 0,
                conversations: items.map((c) => ({
                    id: String(c.id), title: c.title || this.str.untitled, active: String(c.id) === this.conversationId,
                })),
            });
        } catch (e) {
            // The list is a convenience; the chat works without it.
            Log.debug(e);
        }
    }

    /**
     * Open a stored conversation (null = new).
     *
     * @param {string|null} id
     * @param {Object} [options]
     * @param {boolean} [options.focus=true] move the focus to the composer
     * @param {boolean} [options.restore=false] reopening the remembered conversation: if it is
     *     gone, quietly start a new one instead of showing an error
     */
    openConversation(id, {focus = true, restore = false} = {}) {
        this.enqueue(async() => {
            this.setConversation(id);
            this.turn = null;
            // Access may have changed since the links were resolved.
            this.linkCache.clear();
            this.regions.messages.replaceChildren(this.regions.empty);
            this.regions.empty.classList.toggle('d-none', Boolean(id));
            this.clearStatusErrors();
            this.setRunning(false);
            if (focus) {
                this.regions.input.focus();
            }
            // After the switch, so the list (or the block's title) marks this conversation.
            this.refreshConversations();
        });
        if (!id) {
            return;
        }
        this.enqueue(async() => {
            let data;
            try {
                // Stored history holds one event per streamed run, so one render per text run.
                data = await this.apiGet(`/api/conversations/${encodeURIComponent(id)}`);
            } catch (e) {
                if (restore && this.conversationId === id) {
                    Log.debug(e);
                    this.setConversation(null);
                    this.regions.empty.classList.remove('d-none');
                    this.refreshConversations();
                    return;
                }
                await this.showStatus('error', fill(this.str.error, e && e.message ? e.message : String(e)));
                return;
            }
            if (this.conversationId !== id) {
                return;
            }
            const events = data.events || [];
            events.forEach((frame) => this.handleFrame(Object.assign({'conversation_id': id}, frame), true));
            if (!events.length) {
                (data.messages || []).forEach((m) => {
                    const text = String(m.text ?? m.content ?? '');
                    this.handleFrame(m.role === 'user' ? {type: 'user_message', text} : {type: 'final', text,
                        citations: m.citations || []}, true);
                });
            }
            this.enqueue(() => {
                this.setRunning(false);
                // Anything newer than history (a running turn, a pending question) arrives via resume.
                this.socket.send(frames.resume({conversationId: id, lastSeq: this.lastSeq[id]}));
                this.scroll(true);
            });
        });
    }
}

/**
 * Initialise the chat page.
 *
 * @param {Object} config {courseid, rootid, conversationid, preloaded}
 */
export const init = async(config) => {
    const root = document.getElementById(config.rootid);
    if (!root) {
        return;
    }
    const requests = [...STRING_KEYS, ...TOOL_STRING_KEYS, ...RESULT_STRING_KEYS].map((key) => ({key, component: 'local_llmchat'}));
    requests.push({key: 'page', component: 'core'});
    const values = await getStrings(requests);
    const strings = {};
    requests.forEach(({key}, i) => {
        strings[key] = values[i];
    });
    const controller = new ChatController(root, config, strings);
    controller.start();
};

export {ChatController, fill};
