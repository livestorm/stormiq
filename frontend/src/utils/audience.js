// Audience-only chat metrics and question answers.
//
// Chat rows carry `from_team_member` and `from_guest_speaker` (from the
// Livestorm chat API). Engagement metrics count only the audience; the full
// message list still shows every message. Rows without the flags (sessions
// cached before they were kept) count as audience.

function isTrue(value) {
  return value === true || String(value ?? "").trim().toLowerCase() === "true";
}

export function isTeamMessage(row) {
  return isTrue(row?.from_team_member) || isTrue(row?.from_guest_speaker);
}

export function messageSender(row) {
  if (isTrue(row?.from_team_member)) return "Team";
  if (isTrue(row?.from_guest_speaker)) return "Guest speaker";
  return "Attendee";
}

// `excludedTeam` counts the team's and guest speakers' messages;
// `excludedTeamAuthors` counts the distinct people who wrote them.
export function splitAudienceChat(rows) {
  const audience = [];
  const team = [];
  for (const row of rows || []) {
    if (isTeamMessage(row)) team.push(row);
    else audience.push(row);
  }
  return { audience, excludedTeam: team.length, excludedTeamAuthors: uniqueAuthorCount(team) };
}

export function uniqueAuthorCount(rows) {
  return new Set(
    (rows || []).map((row) => String(row?.author_id || "").trim()).filter(Boolean),
  ).size;
}

export function excludedTeamLabel(count) {
  return count > 0 ? `Excludes your team (${count})` : "";
}

// A question is answered when someone responded to it, or when it was
// answered orally (live).
export function isQuestionAnswered(row) {
  const responder = String(row?.answered_by || row?.responded_by || row?.response_author_id || "").trim();
  return Boolean(responder) || isTrue(row?.answered_orally);
}
