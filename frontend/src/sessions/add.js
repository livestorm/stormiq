// The Add session page's wording: the organization's Livestorm events
// (`POST /api/workspace-events`), an event's finished sessions (`POST
// /api/event-sessions`) and the pasted-ID box. Pure functions: they word what
// the routes send and never compute a session measure.

import { formatDay } from "./list";

// Livestorm's event statuses, in the order a user looks for finished
// sessions: ended and on-demand events first.
export const EVENT_STATUSES = [
  { value: "", label: "All statuses" },
  { value: "ended", label: "Ended" },
  { value: "on_demand", label: "On demand" },
  { value: "live", label: "Live" },
  { value: "upcoming", label: "Upcoming" },
  { value: "not_started", label: "Not started" },
  { value: "not_scheduled", label: "Not scheduled" },
  { value: "draft", label: "Draft" },
];

const STATUS_LABELS = Object.fromEntries(EVENT_STATUSES.filter((s) => s.value).map((s) => [s.value, s.label]));

function text(value) {
  return String(value ?? "").trim();
}

function plural(count, noun) {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

/** An event status as words ("On demand"); an unknown one gets its own words
 * ("paused_x" → "Paused x"); none is "". */
export function statusLabel(value) {
  const key = text(value);
  if (!key) return "";
  if (STATUS_LABELS[key]) return STATUS_LABELS[key];
  const words = key.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** One event row: `{id, title, status, sessions}`. `sessions` is "" when
 * Livestorm's count is missing ("n/a"). */
/** The line over an open event's sessions: how many of its sessions have
 * finished (only those can be added), out of all it has. `total` is the
 * finished count (null when Livestorm doesn't say), `all` the event's
 * `sessions_count` (null when unknown). */
export function finishedLine(total, all) {
  if (total === null || total === undefined) return "";
  const finished = plural(total, "finished session");
  if (all === null || all === undefined || all <= total) return finished;
  return `${finished} of ${all}: the others are still to come, never started, cancelled or drafts, and can't be added.`;
}

export function eventRow(option) {
  const count = /^\d+$/.test(text(option?.sessions_count)) ? Number(option.sessions_count) : null;
  return {
    id: text(option?.id),
    title: text(option?.title) || "Untitled event",
    status: statusLabel(option?.scheduling_status),
    sessions: count == null ? "" : plural(count, "session"),
    count,
  };
}

/** One finished session of an event: `{id, title, day, attendees, added,
 * href}`. `added` when the organization already has it (it opens instead of
 * being fetched again). */
export function sessionChoice(option, addedIds = new Set(), options = {}) {
  const id = text(option?.id);
  const attendees = typeof option?.attendees === "number" && option.attendees >= 0 ? option.attendees : null;
  return {
    id,
    title: text(option?.name) || "Untitled session",
    day: formatDay(option?.startedAt, options),
    attendees: attendees == null ? "" : plural(attendees, "attendee"),
    added: addedIds.has(id),
    href: `/sessions/${encodeURIComponent(id)}`,
  };
}

/** The ids of the sessions the organization already has. */
export function addedSessionIds(workspaceSessions = []) {
  return new Set(workspaceSessions.map((session) => text(session?.sessionId)).filter(Boolean));
}

const UUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi;

/** The id in what the user pasted: an id, or a link holding one. `{id,
 * problem}`: `problem` is "empty", "many" (a link with more than one id: we
 * can't tell which is meant) or "" when `id` is set. A bare value with no
 * space or slash is taken as it is (Livestorm ids are UUIDs, but the server
 * is the judge). */
export function idFromInput(value) {
  const input = text(value);
  if (!input) return { id: "", problem: "empty" };
  const found = [...new Set((input.match(UUID) || []).map((id) => id.toLowerCase()))];
  if (found.length === 1) return { id: found[0], problem: "" };
  if (found.length > 1) return { id: "", problem: "many" };
  if (/[\s/]/.test(input)) return { id: "", problem: "empty" };
  return { id: input, problem: "" };
}
