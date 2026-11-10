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
 * English strings for local_llmchat.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

defined('MOODLE_INTERNAL') || die();

$string['answer'] = 'Answer';
$string['assistant'] = 'Assistant';
$string['budgetexhausted'] = 'The token budget for this answer is used up; the answer was concluded early.';
$string['cancel'] = 'Stop';
$string['cancelled'] = 'Stopped.';
$string['chat'] = 'Chat';
$string['choice:select'] = 'Select';
$string['confirm:no'] = 'No';
$string['confirm:yes'] = 'Yes';
$string['connecting'] = 'Connecting…';
$string['conversations'] = 'Conversations';
$string['coursecontext'] = 'Course: {$a}';
$string['error'] = 'Something went wrong: {$a}';
$string['error:apistatus'] = 'The chat API answered with HTTP {$a}.';
$string['error:apiunreachable'] = 'The chat API cannot be reached.';
$string['error:httpsrequired'] = 'The API URL must use https:// (http:// is only allowed with developer debugging).';
$string['error:invalidapiurl'] = 'The API URL is not a valid http(s) URL.';
$string['error:invalidtoken'] = 'Invalid chat token.';
$string['error:notanumber'] = 'Enter a whole number, or leave empty for the site default.';
$string['error:notconfigured'] = 'The chat is not configured yet (API URL and secret are required).';
$string['error:userunavailable'] = 'The user does not exist or is suspended.';
$string['llmchat:manage'] = 'Manage the course chat (sync status, quotas)';
$string['llmchat:unlimited'] = 'Use the course chat without per-user token limits';
$string['llmchat:use'] = 'Use the course chat';
$string['message'] = 'Message';
$string['newconversation'] = 'New conversation';
$string['notaccessible'] = 'not accessible';
$string['offline'] = 'Offline — reconnecting…';
$string['other'] = 'Other answer';
$string['placeholder'] = 'Ask a question about your courses…';
$string['pluginname'] = 'Course chat (LLM)';
$string['privacy:metadata:api'] = 'To answer questions, the chat API receives a short-lived token describing the user and stores the conversation.';
$string['privacy:metadata:api:conversation'] = 'Questions, answers and tool results of the conversation.';
$string['privacy:metadata:api:courses'] = 'The courses, roles and visible activities of the user.';
$string['privacy:metadata:api:fullname'] = 'The full name of the user.';
$string['privacy:metadata:api:lang'] = 'The preferred language of the user.';
$string['privacy:metadata:api:userid'] = 'The user id.';
$string['privacy:metadata:override'] = 'Per-user token quota overrides set by administrators.';
$string['privacy:metadata:override:exempt'] = 'Whether the user is exempt from per-user limits.';
$string['privacy:metadata:override:note'] = 'Administrator note.';
$string['privacy:metadata:override:timemodified'] = 'When the override was last changed.';
$string['privacy:metadata:override:tokensperday'] = 'Daily token limit.';
$string['privacy:metadata:override:tokenspermonth'] = 'Monthly token limit.';
$string['privacy:metadata:override:userid'] = 'The user the override applies to.';
$string['queuedmessage'] = 'Will be sent once the connection is back.';
$string['quota:addoverride'] = 'Add or edit an override';
$string['quota:deleted'] = 'Override removed.';
$string['quota:estimated'] = 'estimated';
$string['quota:exempt'] = 'Exempt from per-user limits';
$string['quota:exemptuser'] = 'No personal token limit';
$string['quota:limit'] = 'Token limit';
$string['quota:limit_help'] = 'Leave empty to use the site default, 0 for unlimited.';
$string['quota:nooverrides'] = 'No per-user overrides.';
$string['quota:note'] = 'Note';
$string['quota:overrides'] = 'Per-user overrides';
$string['quota:remaining'] = 'Today: {$a->used} of {$a->limit} tokens used';
$string['quota:remainingmonth'] = 'This month: {$a->used} of {$a->limit} tokens used';
$string['quota:saved'] = 'Override saved.';
$string['quota:sitedefault'] = 'site default';
$string['quota:sitelimits'] = 'Site limits';
$string['quota:sitetotal'] = 'Site total: {$a}';
$string['quota:tokens'] = 'Tokens';
$string['quota:tokensperday'] = 'Tokens per day';
$string['quota:tokenspermonth'] = 'Tokens per month';
$string['quota:unlimited'] = 'unlimited';
$string['quota:usage'] = 'Usage ({$a})';
$string['quotas'] = 'Chat quotas';
$string['ratelimited:burst'] = 'Too many messages in a short time. Try again {$a}.';
$string['ratelimited:site'] = 'The chat budget of this site is used up. It resets {$a}.';
$string['ratelimited:user'] = 'Your personal chat budget is used up. It resets {$a}.';
$string['reimport'] = 'Clear and reimport';
$string['resume'] = 'Continue';
$string['runfullsync'] = 'Run full import now';
$string['send'] = 'Send';
$string['settings'] = 'Settings';
$string['settings:apiurl'] = 'API URL';
$string['settings:apiurl_desc'] = 'Base URL of the chat API, e.g. https://chat-api.example.org';
$string['settings:connection'] = 'Connection';
$string['settings:connection_desc'] = 'Create a site on the API host with <code>moodle-llm-chat site add "&lt;name&gt;"</code> and paste the printed secret below. <a href="{$a}">Test connection</a>';
$string['settings:count_reasoning'] = 'Count reasoning tokens';
$string['settings:count_reasoning_desc'] = 'Include model reasoning ("thinking") tokens in quotas.';
$string['settings:exempt_users_bypass_site'] = 'Exempt users bypass site limits';
$string['settings:exempt_users_bypass_site_desc'] = 'If enabled, exempt users are not limited by the site-wide budget either.';
$string['settings:filetypes'] = 'File types';
$string['settings:filetypes_desc'] = 'Comma-separated file extensions that are sent to the API.';
$string['settings:includedcourses'] = 'Included courses';
$string['settings:includedcourses_desc'] = 'Comma-separated course ids. Empty = all courses.';
$string['settings:limit_desc'] = 'Tokens; 0 = unlimited.';
$string['settings:max_tokens_per_turn'] = 'Maximum tokens per answer';
$string['settings:maxattempts'] = 'Maximum push attempts';
$string['settings:maxattempts_desc'] = 'After this many failed attempts an outbox entry stops retrying and is shown on the status page.';
$string['settings:maxfilesize'] = 'Maximum file size (MB)';
$string['settings:maxfilesize_desc'] = 'Larger files are not sent to the API.';
$string['settings:quota'] = 'Token quotas';
$string['settings:quota_desc'] = 'Per-user overrides and exemptions are managed on the "Chat quotas" page. The API operator may enforce lower ceilings.';
$string['settings:secret'] = 'Site secret';
$string['settings:secret_desc'] = 'Stored encrypted; never displayed again.';
$string['settings:site_tokens_per_day'] = 'Site tokens per day';
$string['settings:site_tokens_per_month'] = 'Site tokens per month';
$string['settings:sync'] = 'Content sync';
$string['settings:syncenabled'] = 'Sync enabled';
$string['settings:syncenabled_desc'] = 'Push course content changes to the API.';
$string['settings:syncmaxruntime'] = 'Full sync slice (seconds)';
$string['settings:syncmaxruntime_desc'] = 'Maximum runtime of one full sync run; it resumes where it stopped.';
$string['settings:user_tokens_per_day'] = 'Tokens per user per day';
$string['settings:user_tokens_per_month'] = 'Tokens per user per month';
$string['sources'] = 'Sources';
$string['status:action'] = 'Action';
$string['status:api'] = 'API';
$string['status:attempts'] = 'Attempts';
$string['status:authfailed'] = 'The API rejected the site secret (401). It was probably rotated or revoked — paste the new secret in the settings.';
$string['status:authfailedsince'] = 'The outbox is paused since {$a}: the API rejected the site secret.';
$string['status:blobs'] = 'Files uploaded';
$string['status:connectionfailed'] = 'Connection failed: {$a}';
$string['status:connectionok'] = 'Connection successful.';
$string['status:documents'] = 'Documents pushed';
$string['status:finished'] = 'Finished';
$string['status:fullsync'] = 'Full sync';
$string['status:lasterror'] = 'Last error';
$string['status:nextattempt'] = 'Next attempt';
$string['status:nosync'] = 'No full sync has run yet.';
$string['status:outbox'] = 'Outbox';
$string['status:outboxcounts'] = 'Pending: {$a->pending} · retrying: {$a->failing} · given up: {$a->exhausted}';
$string['status:progress'] = 'Courses done';
$string['status:queued'] = 'The full sync was queued and runs with the next cron.';
$string['status:retried'] = 'Failed entries will be retried.';
$string['status:retryexhausted'] = 'Retry given-up entries';
$string['status:started'] = 'Started';
$string['status:state'] = 'State';
$string['status:syncid'] = 'Sync id';
$string['syncstatus'] = 'Sync status';
$string['task:drainoutbox'] = 'Push pending content changes to the chat API';
$string['task:fullsync'] = 'Full content sync to the chat API';
$string['testconnection'] = 'Test connection';
$string['thinking'] = 'Thinking…';
$string['thoughts'] = 'Thoughts';
$string['tool:running'] = 'running…';
$string['turninterrupted'] = 'The answer was interrupted.';
$string['unknowninteraction'] = 'Please answer:';
$string['untitled'] = 'New conversation';
$string['you'] = 'You';
