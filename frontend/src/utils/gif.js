// A chat message with a GIF link from GIPHY's or Tenor's own media hosts
// shows the GIF (the owner's decision, November 2026), under the message's
// other words when it has some ("this is me https://…/giphy.gif"). Anything
// else stays text: another host, another file type, a link with credentials
// or a port. Attendees wrote these messages, so the check is strict and
// returns the URL the browser parsed, never the raw text.

const HOSTS = [/^media\d*\.giphy\.com$/, /^i\.giphy\.com$/, /^media\d*\.tenor\.com$/, /^c\.tenor\.com$/];
const FILES = /\.(gif|webp)$/i;

/** The GIF's URL when `text` is exactly one https link to a GIPHY or Tenor
 * media file, else "". */
export function gifUrl(text) {
  const value = String(text ?? "").trim();
  if (!value || /\s/.test(value)) return "";
  let url;
  try {
    url = new URL(value);
  } catch {
    return "";
  }
  if (url.protocol !== "https:" || url.username || url.password || url.port) return "";
  if (!HOSTS.some((host) => host.test(url.hostname)) || !FILES.test(url.pathname)) return "";
  return url.href;
}

/** A message split for showing: `{text, gif}`, `gif` the first GIF link's
 * URL (`gifUrl`) and `text` the message's other words, or `{text, gif: ""}`
 * unchanged when it has no GIF link. */
export function gifParts(text) {
  const value = String(text ?? "");
  const tokens = value.split(/(\s+)/);
  const index = tokens.findIndex((token) => token.trim() && gifUrl(token));
  if (index < 0) return { text: value, gif: "" };
  const rest = [...tokens.slice(0, index), ...tokens.slice(index + 1)].join("").replace(/\s+/g, " ").trim();
  return { text: rest, gif: gifUrl(tokens[index]) };
}
