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
 * Course chat client: websocket streaming of tokens, thinking, tool cards and interactions,
 * reconnect with resume{last_seq}, "Fortsetzen" after an interrupted turn, quota and
 * rate-limit display. A fresh chat token is fetched before every send.
 *
 * @module     local_llmchat/chat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

import Ajax from 'core/ajax';
import {getStrings} from 'core/str';
import ReconnectingSocket from 'local_llmchat/socket';
import {render as renderMarkdown} from 'local_llmchat/markdown';
import {rendererFor} from 'local_llmchat/interactions/registry';
import {el} from 'local_llmchat/interactions/dom';

const STRING_KEYS = [
    'answer', 'assistant', 'budgetexhausted', 'cancelled', 'choice:select', 'confirm:no', 'confirm:yes', 'connecting',
    'error', 'notaccessible', 'other', 'queuedmessage', 'quota:exemptuser', 'quota:remaining', 'quota:remainingmonth',
    'ratelimited:burst', 'ratelimited:site', 'ratelimited:user', 'resume', 'sources', 'thinking', 'thoughts',
    'tool:running', 'turninterrupted', 'unknowninteraction', 'untitled', 'you',
];

const SEQ_STORAGE_KEY = 'local_llmchat_lastseq';

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
    'turn_interrupted': 'onInterrupted',
    'rate_limited': 'onRateLimited',
    'quota': 'onQuota',
    'error': 'onError',
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

class ChatController {
    /**
     * Constructor.
     *
     * @param {HTMLElement} root
     * @param {Object} config {courseid}
     * @param {Object} strings
     */
    constructor(root, config, strings) {
        this.root = root;
        this.courseid = Number(config.courseid) || 0;
        this.str = strings;
        this.regions = {};
        ['messages', 'conversations', 'offline', 'error', 'quota', 'input', 'composer'].forEach((name) => {
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
        this.apiurl = '';
        this.running = false;

        this.socket = new ReconnectingSocket({
            credentials: () => this.issueToken().then((t) => ({wsurl: t.wsurl, token: t.token})),
            onFrame: (frame) => this.handleFrame(frame, false),
            onReady: (frame) => this.handleReady(frame),
            onStatus: (online) => this.setOnline(online),
        });
        this.bind();
    }

    /**
     * Wire DOM events.
     */
    bind() {
        this.regions.composer.addEventListener('submit', (e) => {
            e.preventDefault();
            this.submit();
        });
        this.regions.input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.submit();
            }
        });
        this.cancelButton.addEventListener('click', () => {
            if (this.conversationId) {
                this.socket.send({type: 'cancel', 'conversation_id': this.conversationId});
            }
        });
        this.root.querySelector('[data-action="new-conversation"]').addEventListener('click', () => {
            this.openConversation(null);
        });
    }

    /**
     * Start.
     */
    start() {
        this.setOnline(false, true);
        this.socket.connect();
    }

    /**
     * Fresh chat token (sesskey-protected AJAX).
     *
     * @returns {Promise<Object>} {token, expires, apiurl, wsurl}
     */
    issueToken() {
        return Ajax.call([{methodname: 'local_llmchat_issue_token', args: {courseid: this.courseid}}])[0]
            .then((result) => {
                this.apiurl = result.apiurl;
                return result;
            });
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
     * Online/offline banner.
     *
     * @param {boolean} online
     * @param {boolean} [initial]
     */
    setOnline(online, initial = false) {
        this.regions.offline.classList.toggle('d-none', online || initial);
        if (initial) {
            this.regions.quota.textContent = this.str.connecting;
        }
    }

    /**
     * After (re)authentication: remember tools, resume the open conversation, flush the queue.
     *
     * @param {Object} frame ready{tools, server_time}
     */
    handleReady(frame) {
        (frame.tools || []).forEach((tool) => this.tools.set(tool.name, tool));
        if (this.regions.quota.textContent === this.str.connecting) {
            this.regions.quota.textContent = '';
        }
        if (this.conversationId) {
            this.socket.send({type: 'resume', 'conversation_id': this.conversationId,
                'last_seq': this.lastSeq[this.conversationId] || 0});
        }
        this.refreshConversations();
        if (this.queued) {
            const queued = this.queued;
            this.queued = null;
            if (queued.note) {
                queued.note.remove();
            }
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
        const clientMsgId = uuid();
        const bubble = this.addUserBubble(text);
        this.pendingUserBubbles.set(clientMsgId, bubble);
        if (!this.socket.isReady()) {
            // Offline: keep at most one unsent message.
            if (this.queued && this.queued.bubble) {
                this.queued.bubble.remove();
            }
            const note = el('div', {className: 'small text-muted', text: this.str.queuedmessage});
            bubble.appendChild(note);
            this.queued = {text, clientMsgId, bubble, note};
            return;
        }
        this.dispatchSend(text, clientMsgId);
    }

    /**
     * Send with a fresh token (scope refresh per turn).
     *
     * @param {string} text
     * @param {string} clientMsgId
     */
    dispatchSend(text, clientMsgId) {
        this.hideError();
        this.issueToken().then((t) => {
            const frame = {
                type: 'send',
                text,
                token: t.token,
                'client_msg_id': clientMsgId,
                ...(this.conversationId ? {'conversation_id': this.conversationId} : {}),
                ...(this.courseid ? {'course_id': this.courseid} : {}),
            };
            if (!this.socket.send(frame)) {
                const bubble = this.pendingUserBubbles.get(clientMsgId);
                this.queued = {text, clientMsgId, bubble, note: null};
            } else {
                this.setRunning(true);
            }
            return t;
        }).catch((e) => this.showError(e && e.message ? e.message : String(e)));
    }

    /**
     * Toggle running state (cancel button).
     *
     * @param {boolean} running
     */
    setRunning(running) {
        this.running = running;
        this.cancelButton.classList.toggle('d-none', !running);
    }

    /**
     * Handle one server frame.
     *
     * @param {Object} frame
     * @param {boolean} replay true when re-rendering stored history
     */
    handleFrame(frame, replay) {
        if (!this.acceptFrame(frame, replay)) {
            return;
        }
        const handler = FRAME_HANDLERS[frame.type];
        if (handler) {
            this[handler](frame, replay);
        }
        // Unknown types are ignored (forward compatibility).
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
            this.conversationId = String(frame.conversation_id);
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
     * Citation list sent ahead of the final answer.
     *
     * @param {Object} frame citations{citations}
     */
    onCitations(frame) {
        this.currentTurn(frame).citations = frame.citations || [];
    }

    /**
     * Error frame; a cancelled turn is shown inline instead of as an error.
     *
     * @param {Object} frame error{code, message}
     */
    onError(frame) {
        this.setRunning(false);
        if (frame.code === 'cancelled') {
            this.currentTurn(frame).body.appendChild(el('div', {className: 'small text-muted', text: this.str.cancelled}));
        } else {
            this.showError(frame.message || frame.code || '');
        }
    }

    /**
     * Scroll messages to the bottom.
     */
    scroll() {
        const m = this.regions.messages;
        m.scrollTop = m.scrollHeight;
    }

    /**
     * Add a user bubble.
     *
     * @param {string} text
     * @returns {HTMLElement}
     */
    addUserBubble(text) {
        const bubble = el('div', {className: 'local-llmchat-msg local-llmchat-user mb-3', 'data-role': 'user'}, [
            el('div', {className: 'small text-muted', text: this.str.you}),
            el('div', {className: 'local-llmchat-text', text}),
        ]);
        this.regions.messages.appendChild(bubble);
        this.turn = null;
        this.scroll();
        return bubble;
    }

    /**
     * Server echo of a user message.
     *
     * @param {Object} frame
     * @param {boolean} replay
     */
    onUserMessage(frame, replay) {
        const known = frame.client_msg_id && this.pendingUserBubbles.has(frame.client_msg_id);
        if (known) {
            this.pendingUserBubbles.delete(frame.client_msg_id);
        } else {
            this.addUserBubble(String(frame.text ?? (frame.message && frame.message.text) ?? ''));
        }
        if (!replay) {
            this.setRunning(true);
        }
        this.turn = null;
    }

    /**
     * The assistant bubble of the current turn (created on demand).
     *
     * @param {Object} frame
     * @returns {Object}
     */
    currentTurn(frame) {
        const turnId = frame.turn_id ?? null;
        if (this.turn && (turnId === null || this.turn.id === null || this.turn.id === turnId)) {
            if (this.turn.id === null) {
                this.turn.id = turnId;
            }
            return this.turn;
        }
        const tools = el('div', {className: 'local-llmchat-tools'});
        const text = el('div', {className: 'local-llmchat-text'});
        const body = el('div', {className: 'local-llmchat-body'}, [tools, text]);
        const bubble = el('div', {className: 'local-llmchat-msg local-llmchat-assistant mb-3', 'data-role': 'assistant'}, [
            el('div', {className: 'small text-muted', text: this.str.assistant}), body,
        ]);
        this.regions.messages.appendChild(bubble);
        this.turn = {id: turnId, bubble, body, tools, text, raw: '', thinking: null, cards: new Map(), citations: []};
        return this.turn;
    }

    /**
     * Reasoning delta (text only in show_thinking=full; otherwise an indicator).
     *
     * @param {Object} frame
     */
    onThinking(frame) {
        const turn = this.currentTurn(frame);
        if (!turn.thinking) {
            const content = el('div', {className: 'small text-muted local-llmchat-thinking-text'});
            const summary = el('summary', {className: 'small', text: this.str.thinking});
            const details = el('details', {className: 'local-llmchat-thinking mb-2', open: ''}, [summary, content]);
            turn.body.insertBefore(details, turn.tools);
            turn.thinking = {details, summary, content};
        }
        if (frame.delta) {
            turn.thinking.content.textContent += frame.delta;
        }
        this.scroll();
    }

    /**
     * Collapse the thinking block once the answer starts.
     *
     * @param {Object} turn
     */
    collapseThinking(turn) {
        if (turn.thinking && turn.thinking.details.open) {
            turn.thinking.details.open = false;
            turn.thinking.summary.textContent = this.str.thoughts;
        }
    }

    /**
     * Answer token.
     *
     * @param {Object} frame
     */
    onToken(frame) {
        const turn = this.currentTurn(frame);
        this.collapseThinking(turn);
        turn.raw += frame.delta || '';
        turn.text.innerHTML = renderMarkdown(turn.raw, turn.citations);
        this.scroll();
    }

    /**
     * Tool card.
     *
     * @param {Object} frame tool_call{id, name, args}
     */
    onToolCall(frame) {
        const turn = this.currentTurn(frame);
        const meta = this.tools.get(frame.name) || {};
        const args = frame.args || {};
        const firstArg = Object.values(args).find((v) => typeof v === 'string') || '';
        let label;
        if (meta.summary_template) {
            label = meta.summary_template.replace(/\{(\w+)\}/g, (m, k) => (args[k] !== undefined ? String(args[k]) : ''));
        } else {
            label = `${meta.label || frame.name} ${firstArg ? `"${firstArg}"` : ''}`;
        }
        label = `${meta.icon ? meta.icon + ' ' : '🔧 '}${label}`.trim();
        const status = el('span', {className: 'ml-2 ms-2 text-muted', text: this.str['tool:running']});
        const summary = el('summary', {className: 'small'}, [el('span', {text: label}), status]);
        const detail = el('div', {className: 'small local-llmchat-tool-detail'});
        detail.appendChild(el('pre', {className: 'mb-1', text: JSON.stringify(args, null, 2)}));
        const card = el('details', {className: 'local-llmchat-tool card card-body p-2 mb-2',
            'data-tool': String(frame.name || '')}, [summary, detail]);
        turn.tools.appendChild(card);
        turn.cards.set(frame.id, {card, status, detail});
        this.scroll();
    }

    /**
     * Intermediate tool progress.
     *
     * @param {Object} frame
     */
    onToolProgress(frame) {
        const entry = this.currentTurn(frame).cards.get(frame.id);
        if (entry) {
            const text = frame.message || frame.text || frame.summary || '';
            if (text) {
                entry.status.textContent = String(text);
            }
        }
    }

    /**
     * Tool result.
     *
     * @param {Object} frame tool_result{id, summary, citations?, attachments?}
     */
    onToolResult(frame) {
        const entry = this.currentTurn(frame).cards.get(frame.id);
        if (!entry) {
            return;
        }
        entry.status.textContent = frame.summary ? `→ ${frame.summary}` : '✓';
        if (frame.summary) {
            entry.detail.appendChild(el('div', {text: String(frame.summary)}));
        }
        (frame.citations || []).forEach((c) => {
            entry.detail.appendChild(el('div', {className: 'text-muted', text: `[${c.index ?? '?'}] ${c.title || ''}`}));
        });
    }

    /**
     * Interaction (ask_user etc.), rendered by kind.
     *
     * @param {Object} frame question{interrupt_id, kind, payload}
     * @param {boolean} replay
     */
    onQuestion(frame, replay) {
        const turn = this.currentTurn(frame);
        this.collapseThinking(turn);
        const existing = turn.body.querySelector(`[data-interrupt="${CSS.escape(String(frame.interrupt_id))}"]`);
        if (existing) {
            existing.remove();
        }
        const renderer = rendererFor(frame.kind);
        const strings = {
            answer: this.str.answer, other: this.str.other, yes: this.str['confirm:yes'], no: this.str['confirm:no'],
            select: this.str['choice:select'], unknowninteraction: this.str.unknowninteraction,
        };
        const node = renderer.render(frame.payload || {}, (response) => {
            this.issueToken().then(() => this.socket.send({
                type: 'answer', 'conversation_id': this.conversationId, 'interrupt_id': frame.interrupt_id, response,
            })).catch((e) => this.showError(String(e && e.message ? e.message : e)));
        }, strings);
        node.setAttribute('data-interrupt', String(frame.interrupt_id));
        if (frame.error) {
            node.insertBefore(el('div', {className: 'text-danger small', text: String(frame.error)}), node.firstChild);
        }
        turn.body.appendChild(node);
        // Text after the interaction (the resumed answer) goes below it, not above.
        turn.text = el('div', {className: 'local-llmchat-text'});
        turn.raw = '';
        turn.body.appendChild(turn.text);
        this.setRunning(false);
        if (!replay) {
            node.querySelector('input, button')?.focus();
        }
        this.scroll();
    }

    /**
     * Final answer with citations.
     *
     * @param {Object} frame final{text, citations}
     */
    onFinal(frame) {
        const turn = this.currentTurn(frame);
        this.collapseThinking(turn);
        turn.citations = frame.citations || turn.citations || [];
        turn.raw = frame.text ?? turn.raw;
        turn.text.innerHTML = renderMarkdown(turn.raw, turn.citations);
        if (turn.citations.length) {
            const list = el('ol', {className: 'small local-llmchat-sources mb-0'});
            turn.citations.forEach((c) => {
                const item = el('li', {value: String(c.index)});
                const label = [c.title, c.course_name, c.page ? `S. ${c.page}` : ''].filter(Boolean).join(' · ');
                if (c.url) {
                    item.appendChild(el('a', {href: c.url, target: '_blank', rel: 'noopener', text: label}));
                } else {
                    item.textContent = label;
                }
                list.appendChild(item);
            });
            turn.body.appendChild(el('details', {className: 'mt-2'}, [
                el('summary', {className: 'small', text: this.str.sources}), list,
            ]));
        }
        if (frame.budget_exhausted || frame.truncated_by_budget) {
            turn.body.appendChild(el('div', {className: 'small text-warning', text: this.str.budgetexhausted}));
        }
        this.setRunning(false);
        this.turn = null;
        this.refreshConversations();
        this.scroll();
    }

    /**
     * The API restarted mid-turn: offer "Fortsetzen".
     *
     * @param {Object} frame turn_interrupted{turn_id}
     * @param {boolean} replay
     */
    onInterrupted(frame, replay) {
        const turn = this.currentTurn(frame);
        this.setRunning(false);
        const button = el('button', {type: 'button', className: 'btn btn-sm btn-primary ml-2 ms-2', text: this.str.resume});
        const box = el('div', {className: 'alert alert-info py-1 px-2 mt-2 local-llmchat-interrupted'}, [
            el('span', {text: this.str.turninterrupted}), button,
        ]);
        button.addEventListener('click', () => {
            this.issueToken().then(() => {
                if (this.socket.send({type: 'continue', 'conversation_id': this.conversationId, 'turn_id': frame.turn_id})) {
                    box.remove();
                    this.setRunning(true);
                }
                return null;
            }).catch((e) => this.showError(String(e && e.message ? e.message : e)));
        });
        box.setAttribute('data-replayed', replay ? '1' : '0');
        turn.body.appendChild(box);
    }

    /**
     * Budget refusal.
     *
     * @param {Object} frame rate_limited{scope, used?, limit?, resets_at}
     */
    onRateLimited(frame) {
        this.setRunning(false);
        const key = `ratelimited:${['burst', 'user', 'site'].includes(frame.scope) ? frame.scope : 'user'}`;
        this.showError(fill(this.str[key], formatTime(frame.resets_at)), 'warning');
        if (frame.used !== undefined && frame.limit !== undefined) {
            this.regions.quota.textContent = fill(this.str['quota:remaining'],
                {used: formatNumber(frame.used), limit: formatNumber(frame.limit)});
        }
    }

    /**
     * Remaining budget header.
     *
     * @param {Object} frame quota{used_today, limit_today, used_month, limit_month, resets_at, exempt?}
     */
    onQuota(frame) {
        if (frame.exempt) {
            this.regions.quota.textContent = this.str['quota:exemptuser'];
            return;
        }
        const parts = [];
        if (frame.limit_today !== undefined && frame.limit_today !== null) {
            parts.push(fill(this.str['quota:remaining'],
                {used: formatNumber(frame.used_today), limit: formatNumber(frame.limit_today)}));
        }
        if (frame.limit_month !== undefined && frame.limit_month !== null) {
            parts.push(fill(this.str['quota:remainingmonth'],
                {used: formatNumber(frame.used_month), limit: formatNumber(frame.limit_month)}));
        }
        this.regions.quota.textContent = parts.join(' · ');
    }

    /**
     * Show an error banner.
     *
     * @param {string} message
     * @param {string} [level]
     */
    showError(message, level = 'danger') {
        const box = this.regions.error;
        box.className = `alert alert-${level}`;
        box.textContent = level === 'danger' ? fill(this.str.error, message) : message;
    }

    /**
     * Hide the error banner.
     */
    hideError() {
        this.regions.error.className = 'alert alert-danger d-none';
        this.regions.error.textContent = '';
    }

    /**
     * GET from the API with the chat token.
     *
     * @param {string} path
     * @returns {Promise<Object>}
     */
    apiGet(path) {
        return this.issueToken().then((t) => fetch(`${t.apiurl}${path}`, {
            headers: {Authorization: `Bearer ${t.token}`, Accept: 'application/json'},
            credentials: 'omit',
        })).then((response) => {
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            return response.json();
        });
    }

    /**
     * Reload the conversation list.
     */
    refreshConversations() {
        this.apiGet('/api/conversations').then((data) => {
            const items = Array.isArray(data) ? data : (data.conversations || data.items || []);
            const list = this.regions.conversations;
            list.innerHTML = '';
            items.forEach((c) => {
                const id = String(c.id);
                const button = el('button', {type: 'button',
                    className: `btn btn-link text-start w-100 p-1${id === this.conversationId ? ' fw-bold' : ''}`,
                    text: c.title || this.str.untitled});
                button.addEventListener('click', () => this.openConversation(id));
                list.appendChild(el('li', {}, [button]));
            });
            return data;
        }).catch(() => {
            // The list is a convenience; the chat works without it.
        });
    }

    /**
     * Open a stored conversation (null = new).
     *
     * @param {string|null} id
     */
    openConversation(id) {
        this.conversationId = id;
        this.turn = null;
        this.regions.messages.innerHTML = '';
        this.hideError();
        this.setRunning(false);
        if (!id) {
            this.refreshConversations();
            return;
        }
        this.apiGet(`/api/conversations/${encodeURIComponent(id)}`).then((data) => {
            if (this.conversationId !== id) {
                return data;
            }
            const events = data.events || [];
            if (events.length) {
                events.forEach((frame) => this.handleFrame(Object.assign({'conversation_id': id}, frame), true));
            } else {
                (data.messages || []).forEach((m) => {
                    if (m.role === 'user') {
                        this.addUserBubble(String(m.text ?? m.content ?? ''));
                    } else if (m.role === 'assistant') {
                        this.onFinal({text: String(m.text ?? m.content ?? ''), citations: m.citations || []});
                    }
                });
            }
            this.setRunning(false);
            // Anything newer than history (a running turn, a pending question) arrives via resume.
            this.socket.send({type: 'resume', 'conversation_id': id, 'last_seq': this.lastSeq[id] || 0});
            this.refreshConversations();
            return data;
        }).catch((e) => this.showError(String(e && e.message ? e.message : e)));
    }
}

/**
 * Initialise the chat page.
 *
 * @param {Object} config {courseid, rootid}
 */
export const init = async(config) => {
    const root = document.getElementById(config.rootid);
    if (!root) {
        return;
    }
    const values = await getStrings(STRING_KEYS.map((key) => ({key, component: 'local_llmchat'})));
    const strings = {};
    STRING_KEYS.forEach((key, i) => {
        strings[key] = values[i];
    });
    const controller = new ChatController(root, config, strings);
    controller.start();
};

export {ChatController, fill};
