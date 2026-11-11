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
 * German strings for local_llmchat.
 *
 * @package    local_llmchat
 * @copyright  2026 Matix-Media
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

defined('MOODLE_INTERNAL') || die();

$string['answer'] = 'Antworten';
$string['answered'] = 'Deine Antwort:';
$string['assistant'] = 'Assistent';
$string['budgetexhausted'] = 'Budget für diese Antwort aufgebraucht – die Antwort wurde vorzeitig abgeschlossen.';
$string['cancel'] = 'Abbrechen';
$string['cancelled'] = 'Abgebrochen.';
$string['chat'] = 'Chat';
$string['chattitle'] = 'Kurs-Assistent';
$string['choice:select'] = 'Auswählen';
$string['composerhint'] = 'Enter zum Senden · Umschalt+Enter für eine neue Zeile';
$string['confirm:no'] = 'Nein';
$string['confirm:yes'] = 'Ja';
$string['connecting'] = 'Verbinde…';
$string['conversations'] = 'Unterhaltungen';
$string['copied'] = 'Antwort kopiert';
$string['copy'] = 'Kopieren';
$string['coursecontext'] = 'Kurs: {$a}';
$string['emptyintro'] = 'Frag nach Material, Terminen und Aktivitäten deiner Kurse. Jede Antwort verweist auf ihre Quellen.';
$string['emptyintrocourse'] = 'Frag zu {$a}: Material, Termine und Aktivitäten. Jede Antwort verweist auf ihre Quellen.';
$string['emptytitle'] = 'Wie kann ich helfen?';
$string['error'] = 'Etwas ist schiefgelaufen: {$a}';
$string['error:apistatus'] = 'Die Chat-API antwortete mit HTTP {$a}.';
$string['error:apiunreachable'] = 'Die Chat-API ist nicht erreichbar.';
$string['error:httpsrequired'] = 'Die API-URL muss https:// verwenden (http:// nur mit Entwickler-Debugging).';
$string['error:invalidapiurl'] = 'Die API-URL ist keine gültige http(s)-URL.';
$string['error:invalidtoken'] = 'Ungültiges Chat-Token.';
$string['error:notanumber'] = 'Bitte eine ganze Zahl eingeben oder leer lassen für den Standardwert der Website.';
$string['error:notconfigured'] = 'Der Chat ist noch nicht eingerichtet (API-URL und Secret fehlen).';
$string['error:userunavailable'] = 'Die Person existiert nicht oder ist gesperrt.';
$string['llmchat:manage'] = 'Kurs-Chat verwalten (Synchronisation, Kontingente)';
$string['llmchat:unlimited'] = 'Kurs-Chat ohne persönliches Token-Limit nutzen';
$string['llmchat:use'] = 'Kurs-Chat nutzen';
$string['message'] = 'Nachricht';
$string['messages'] = 'Nachrichten';
$string['newconversation'] = 'Neue Unterhaltung';
$string['noconversations'] = 'Noch keine Unterhaltungen';
$string['notaccessible'] = 'nicht zugänglich';
$string['offline'] = 'Offline – Verbindung wird wiederhergestellt…';
$string['other'] = 'Eigene Antwort';
$string['placeholder'] = 'Stelle eine Frage zu deinen Kursen…';
$string['pluginname'] = 'Kurs-Chat (LLM)';
$string['privacy:metadata:api'] = 'Um Fragen zu beantworten, erhält die Chat-API ein kurzlebiges Token mit Angaben zur Person und speichert die Unterhaltung.';
$string['privacy:metadata:api:conversation'] = 'Fragen, Antworten und Werkzeugergebnisse der Unterhaltung.';
$string['privacy:metadata:api:courses'] = 'Kurse, Rollen und sichtbare Aktivitäten der Person.';
$string['privacy:metadata:api:fullname'] = 'Der vollständige Name.';
$string['privacy:metadata:api:lang'] = 'Die bevorzugte Sprache.';
$string['privacy:metadata:api:userid'] = 'Die Nutzer-ID.';
$string['privacy:metadata:override'] = 'Persönliche Token-Kontingente, von Administrator/innen festgelegt.';
$string['privacy:metadata:override:exempt'] = 'Ob die Person von persönlichen Limits ausgenommen ist.';
$string['privacy:metadata:override:note'] = 'Notiz der Administration.';
$string['privacy:metadata:override:timemodified'] = 'Zeitpunkt der letzten Änderung.';
$string['privacy:metadata:override:tokensperday'] = 'Tägliches Token-Limit.';
$string['privacy:metadata:override:tokenspermonth'] = 'Monatliches Token-Limit.';
$string['privacy:metadata:override:userid'] = 'Die Person, für die die Ausnahme gilt.';
$string['queuedmessage'] = 'Wird gesendet, sobald die Verbindung wieder steht.';
$string['quota:addoverride'] = 'Ausnahme hinzufügen oder bearbeiten';
$string['quota:deleted'] = 'Ausnahme entfernt.';
$string['quota:estimated'] = 'geschätzt';
$string['quota:exempt'] = 'Von persönlichen Limits ausgenommen';
$string['quota:exemptuser'] = 'Kein persönliches Token-Limit';
$string['quota:limit'] = 'Token-Limit';
$string['quota:limit_help'] = 'Leer lassen für den Standardwert der Website, 0 für unbegrenzt.';
$string['quota:nooverrides'] = 'Keine persönlichen Ausnahmen.';
$string['quota:note'] = 'Notiz';
$string['quota:open'] = 'Token-Budget';
$string['quota:overrides'] = 'Persönliche Ausnahmen';
$string['quota:remaining'] = 'Heute: {$a->used} von {$a->limit} Tokens verbraucht';
$string['quota:remainingmonth'] = 'Diesen Monat: {$a->used} von {$a->limit} Tokens verbraucht';
$string['quota:resets'] = 'Wird {$a} zurückgesetzt';
$string['quota:saved'] = 'Ausnahme gespeichert.';
$string['quota:sitedefault'] = 'Standard der Website';
$string['quota:sitelimits'] = 'Limits der Website';
$string['quota:sitetotal'] = 'Gesamt der Website: {$a}';
$string['quota:thismonth'] = 'Diesen Monat';
$string['quota:title'] = 'Token-Budget';
$string['quota:today'] = 'Heute';
$string['quota:tokens'] = 'Tokens';
$string['quota:tokensperday'] = 'Tokens pro Tag';
$string['quota:tokenspermonth'] = 'Tokens pro Monat';
$string['quota:unlimited'] = 'unbegrenzt';
$string['quota:usage'] = 'Verbrauch ({$a})';
$string['quota:usedof'] = '{$a->used} von {$a->limit}';
$string['quotas'] = 'Chat-Kontingente';
$string['ratelimited:burst'] = 'Zu viele Nachrichten in kurzer Zeit. Bitte {$a} erneut versuchen.';
$string['ratelimited:site'] = 'Das Chat-Budget dieser Website ist aufgebraucht. Es wird {$a} zurückgesetzt.';
$string['ratelimited:user'] = 'Dein persönliches Chat-Budget ist aufgebraucht. Es wird {$a} zurückgesetzt.';
$string['reconnected'] = 'Verbindung wiederhergestellt';
$string['reimport'] = 'Leeren und neu importieren';
$string['resume'] = 'Fortsetzen';
$string['runfullsync'] = 'Vollständigen Import jetzt starten';
$string['send'] = 'Senden';
$string['settings'] = 'Einstellungen';
$string['settings:apiurl'] = 'API-URL';
$string['settings:apiurl_desc'] = 'Basis-URL der Chat-API, z. B. https://chat-api.example.org';
$string['settings:connection'] = 'Verbindung';
$string['settings:connection_desc'] = 'Lege auf dem API-Host mit <code>moodle-llm-chat site add "&lt;Name&gt;"</code> eine Website an und füge das ausgegebene Secret unten ein. <a href="{$a}">Verbindung testen</a>';
$string['settings:count_reasoning'] = 'Denk-Tokens mitzählen';
$string['settings:count_reasoning_desc'] = 'Tokens für das „Nachdenken“ des Modells in die Kontingente einrechnen.';
$string['settings:exempt_users_bypass_site'] = 'Ausgenommene umgehen Website-Limits';
$string['settings:exempt_users_bypass_site_desc'] = 'Wenn aktiv, gelten für ausgenommene Personen auch die Website-Limits nicht.';
$string['settings:filetypes'] = 'Dateitypen';
$string['settings:filetypes_desc'] = 'Kommagetrennte Dateiendungen, die an die API gesendet werden.';
$string['settings:includedcourses'] = 'Einbezogene Kurse';
$string['settings:includedcourses_desc'] = 'Kommagetrennte Kurs-IDs. Leer = alle Kurse.';
$string['settings:limit_desc'] = 'Tokens; 0 = unbegrenzt.';
$string['settings:max_tokens_per_turn'] = 'Maximale Tokens pro Antwort';
$string['settings:maxattempts'] = 'Maximale Sendeversuche';
$string['settings:maxattempts_desc'] = 'Nach so vielen Fehlversuchen wird ein Eintrag nicht mehr wiederholt und auf der Statusseite angezeigt.';
$string['settings:maxfilesize'] = 'Maximale Dateigröße (MB)';
$string['settings:maxfilesize_desc'] = 'Größere Dateien werden nicht an die API gesendet.';
$string['settings:quota'] = 'Token-Kontingente';
$string['settings:quota_desc'] = 'Persönliche Ausnahmen werden auf der Seite „Chat-Kontingente“ verwaltet. Der API-Betreiber kann niedrigere Obergrenzen erzwingen.';
$string['settings:secret'] = 'Secret der Website';
$string['settings:secret_desc'] = 'Wird verschlüsselt gespeichert und nie wieder angezeigt.';
$string['settings:site_tokens_per_day'] = 'Tokens der Website pro Tag';
$string['settings:site_tokens_per_month'] = 'Tokens der Website pro Monat';
$string['settings:sync'] = 'Inhaltssynchronisation';
$string['settings:syncenabled'] = 'Synchronisation aktiv';
$string['settings:syncenabled_desc'] = 'Änderungen an Kursinhalten an die API senden.';
$string['settings:syncmaxruntime'] = 'Laufzeit je Vollimport (Sekunden)';
$string['settings:syncmaxruntime_desc'] = 'Maximale Laufzeit eines Vollimport-Durchlaufs; er wird an der Abbruchstelle fortgesetzt.';
$string['settings:user_tokens_per_day'] = 'Tokens pro Person und Tag';
$string['settings:user_tokens_per_month'] = 'Tokens pro Person und Monat';
$string['sources'] = 'Quellen';
$string['status:action'] = 'Aktion';
$string['status:api'] = 'API';
$string['status:attempts'] = 'Versuche';
$string['status:authfailed'] = 'Die API hat das Secret abgelehnt (401). Vermutlich wurde es rotiert oder widerrufen – bitte das neue Secret in den Einstellungen eintragen.';
$string['status:authfailedsince'] = 'Der Postausgang ist seit {$a} pausiert: die API hat das Secret abgelehnt.';
$string['status:blobs'] = 'Hochgeladene Dateien';
$string['status:connectionfailed'] = 'Verbindung fehlgeschlagen: {$a}';
$string['status:connectionok'] = 'Verbindung erfolgreich.';
$string['status:documents'] = 'Gesendete Dokumente';
$string['status:finished'] = 'Beendet';
$string['status:fullsync'] = 'Vollimport';
$string['status:lasterror'] = 'Letzter Fehler';
$string['status:nextattempt'] = 'Nächster Versuch';
$string['status:nosync'] = 'Bisher lief kein Vollimport.';
$string['status:outbox'] = 'Postausgang';
$string['status:outboxcounts'] = 'Ausstehend: {$a->pending} · wird wiederholt: {$a->failing} · aufgegeben: {$a->exhausted}';
$string['status:progress'] = 'Fertige Kurse';
$string['status:queued'] = 'Der Vollimport wurde eingeplant und läuft mit dem nächsten Cron.';
$string['status:retried'] = 'Fehlgeschlagene Einträge werden erneut versucht.';
$string['status:retryexhausted'] = 'Aufgegebene Einträge erneut versuchen';
$string['status:started'] = 'Gestartet';
$string['status:state'] = 'Status';
$string['status:syncid'] = 'Sync-ID';
$string['suggestion1'] = 'Was steht diese Woche an?';
$string['suggestion2'] = 'Fasse das neueste Material zusammen';
$string['suggestion3'] = 'Wo finde ich die Aufgabenstellung?';
$string['syncstatus'] = 'Synchronisationsstatus';
$string['task:drainoutbox'] = 'Ausstehende Inhaltsänderungen an die Chat-API senden';
$string['task:fullsync'] = 'Vollständige Inhaltssynchronisation mit der Chat-API';
$string['testconnection'] = 'Verbindung testen';
$string['thinking'] = 'Denkt nach…';
$string['thoughts'] = 'Gedankengang';
$string['tool:done'] = 'fertig';
$string['tool:parameters'] = 'Parameter';
$string['tool:raw'] = 'Rohdaten anzeigen';
$string['tool:result'] = 'Ergebnis';
$string['tool:running'] = 'läuft…';
$string['toolarg:context_chars'] = 'Kontext (Zeichen)';
$string['toolarg:course_ids'] = 'Kurse';
$string['toolarg:detail'] = 'Detail';
$string['toolarg:k'] = 'Max. Treffer';
$string['toolarg:limit'] = 'Länge (Zeichen)';
$string['toolarg:max_matches'] = 'Max. Fundstellen';
$string['toolarg:offset'] = 'Ab Zeichen';
$string['toolarg:options'] = 'Optionen';
$string['toolarg:order'] = 'Reihenfolge';
$string['toolarg:page'] = 'Seite';
$string['toolarg:path'] = 'Ort';
$string['toolarg:pattern'] = 'Muster';
$string['toolarg:query'] = 'Suche nach';
$string['toolarg:question'] = 'Frage';
$string['toolarg:since'] = 'Von';
$string['toolarg:until'] = 'Bis';
$string['toolval:daysago'] = 'letzte {$a} Tage';
$string['toolval:modified_asc'] = 'Älteste zuerst';
$string['toolval:modified_desc'] = 'Neueste zuerst';
$string['toolval:no'] = 'Nein';
$string['toolval:root'] = 'Alle Kurse';
$string['toolval:yes'] = 'Ja';
$string['turninterrupted'] = 'Die Antwort wurde unterbrochen.';
$string['typeanswer'] = 'Antwort eingeben';
$string['unknowninteraction'] = 'Bitte antworten:';
$string['untitled'] = 'Neue Unterhaltung';
$string['you'] = 'Du';
