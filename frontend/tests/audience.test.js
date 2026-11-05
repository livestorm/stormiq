import { describe, expect, it } from "vitest";
import {
  excludedTeamLabel,
  isQuestionAnswered,
  messageSender,
  splitAudienceChat,
  uniqueAuthorCount,
} from "../src/utils/audience.js";

const chat = [
  { author_id: "host", text_content: "Link in the chat", from_team_member: true, from_guest_speaker: false },
  { author_id: "speaker", text_content: "Thanks all", from_team_member: false, from_guest_speaker: true },
  { author_id: "ana", text_content: "Great point", from_team_member: false, from_guest_speaker: false },
  { author_id: "ana", text_content: "And another", from_team_member: false, from_guest_speaker: false },
  { author_id: "ben", text_content: "Cached before the flags were kept" },
];

describe("audience chat", () => {
  it("leaves team and guest-speaker messages out of the audience", () => {
    const { audience, excludedTeam, excludedTeamAuthors } = splitAudienceChat(chat);
    expect(audience.map((row) => row.author_id)).toEqual(["ana", "ana", "ben"]);
    expect(excludedTeam).toBe(2);
    expect(excludedTeamAuthors).toBe(2);
    expect(uniqueAuthorCount(audience)).toBe(2);
  });

  it("counts the team's people apart from its messages", () => {
    const rows = [
      { author_id: "host", from_team_member: true },
      { author_id: "host", from_team_member: true },
      { author_id: "host", from_team_member: true },
      { author_id: "ana" },
    ];
    const { excludedTeam, excludedTeamAuthors } = splitAudienceChat(rows);
    expect(excludedTeam).toBe(3);
    expect(excludedTeamAuthors).toBe(1);
  });

  it("labels who sent each message", () => {
    expect(chat.map(messageSender)).toEqual(["Team", "Guest speaker", "Attendee", "Attendee", "Attendee"]);
  });

  it("says how many team messages were left out", () => {
    expect(excludedTeamLabel(2)).toBe("Excludes your team (2)");
    expect(excludedTeamLabel(0)).toBe("");
  });
});

describe("question answers", () => {
  it("counts an oral answer as answered", () => {
    expect(isQuestionAnswered({ answered_by: "Ana" })).toBe(true);
    expect(isQuestionAnswered({ answered_by: null, answered_orally: true })).toBe(true);
    expect(isQuestionAnswered({ answered_by: "", answered_orally: false })).toBe(false);
    expect(isQuestionAnswered({})).toBe(false);
  });
});
