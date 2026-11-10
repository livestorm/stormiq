// The Transcript tab's "Speaking stats" card, from the record's measures
// (`m.speaking_pace`, `m.speaker_talk_time`, `m.longest_stretch`, docs/product.md
// §13) and the speakers' names from the Transcript route. Pure functions: they
// word what the server computed and never compute a pace, a share or a
// stretch themselves. A hidden number stays hidden and says why.

const HIDDEN = new Set(["insufficient", "unresolved", "unavailable"]);

const WHY = {
  pace_ok: "The transcript covers too little of the recording for a pace.",
  shares_ok: "The speakers couldn't be told apart reliably.",
  speaking_s: "There's less than a minute of speech.",
};

function hidden(measure) {
  return !measure || HIDDEN.has(measure.state);
}

/** Why a speaking measure is hidden, or "". */
export function whyHidden(measure) {
  if (!measure) return "Not computed for this session yet.";
  if (!HIDDEN.has(measure.state)) return "";
  if (measure.state === "unavailable") return "No transcript yet.";
  return WHY[measure.gate?.field] || "Not enough speech for a reliable number.";
}

/** "8 s", "2 min 5 s", "1 h 3 min" from seconds; "" when unknown. */
export function formatDuration(seconds) {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) return "";
  const total = Math.round(seconds);
  if (total < 60) return `${total} s`;
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hours) return `${hours} h${minutes ? ` ${minutes} min` : ""}`;
  return `${minutes} min${secs ? ` ${secs} s` : ""}`;
}

function percent(share) {
  if (typeof share !== "number" || !Number.isFinite(share)) return "";
  const value = share * 100;
  return value > 0 && value < 0.5 ? "under 1%" : `${Math.round(value)}%`;
}

function nameOf(names, key) {
  if (key === "unknown" || key == null) return "Unattributed speech";
  return names.get(key) || `Speaker ${Number(key) + 1}`;
}

function status(measure) {
  return measure?.state === "approximation" ? "estimated" : "measured";
}

/** The card: `{summary, chip, pace, stretch, speakers, speakersWhy, estimated}`.
 *
 * - `pace`: `{text, status}` ("135 words a minute"), or `{why}` when hidden;
 * - `stretch`: `{text, who, status}`, or `{why}`;
 * - `speakers`: rows `{key, name, time, share, bar, pace}` in the server's
 *   order (most talk first), empty when hidden (`speakersWhy` says why);
 * - `summary`: the collapsed card's line; `chip`: the speakers' count. */
export function speakingStats(measures = {}, speakerList = []) {
  const names = new Map(speakerList.map((speaker) => [speaker.key, speaker.name]));
  const pace = measures["m.speaking_pace"];
  const talk = measures["m.speaker_talk_time"];
  const stretch = measures["m.longest_stretch"];

  const paceView = hidden(pace)
    ? { why: whyHidden(pace) }
    : { text: `${Math.round(pace.value)} words a minute`, status: status(pace) };
  const stretchView = hidden(stretch)
    ? { why: whyHidden(stretch) }
    : {
        text: formatDuration(stretch.value),
        who: nameOf(names, stretch.coverage?.speaker),
        status: status(stretch),
      };
  const speakers = hidden(talk)
    ? []
    : (talk.rows || []).map((row) => ({
        key: row.key,
        name: nameOf(names, row.key),
        time: formatDuration(row.speakingS),
        share: percent(row.share),
        bar: typeof row.share === "number" ? Math.max(0, Math.min(100, row.share * 100)) : 0,
        pace: typeof row.wpm === "number" ? `${Math.round(row.wpm)} wpm` : "",
      }));

  const top = speakers[0];
  const parts = [];
  if (paceView.text) parts.push(paceView.text);
  if (top && speakers.length > 1) parts.push(`${top.name} spoke ${top.share} of the time`);
  const summary = parts.length ? parts.join(" · ") : paceView.why || whyHidden(talk);
  const count = hidden(talk) ? null : talk.den;

  return {
    summary,
    chip: count ? `${count} ${count === 1 ? "speaker" : "speakers"}` : "",
    pace: paceView,
    stretch: stretchView,
    speakers,
    speakersWhy: hidden(talk) ? whyHidden(talk) : "",
    estimated: [pace, talk, stretch].some((measure) => measure?.state === "approximation"),
  };
}
