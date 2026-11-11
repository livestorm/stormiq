// A chat message that is only a GIF link, from GIPHY's or Tenor's own media
// hosts, is shown as the GIF (the owner's decision, November 2026). Anything
// else stays text: another host, another file type, a message with other
// words, a link with credentials or a port. Attendees wrote these messages,
// so the check is strict and returns the URL the browser parsed, never the
// raw text.

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
