// "Next time": one piece of event craft per found finding, from a curated
// library (never written by a model), in English and French. Advice is about
// running the session, never about the business. The line shown is picked
// from a few variants by a stable hash of the session and the situation, so
// a page reads the same each time and two sessions don't all read alike.

const LIBRARY = {
  "chapters.high": {
    en: [
      "Open with the part that drew the room in, or tease it early: it is what this audience came for.",
      "Turn that part into a clip or a follow-up post: it is the one the room reacted to on its own.",
      "Give that part more room next time, and bring its format (a demo, a story, real numbers) to the quieter parts.",
    ],
    fr: [
      "Ouvrez avec la partie qui a fait réagir la salle, ou annoncez-la tôt : c'est ce que ce public est venu chercher.",
      "Faites de cette partie un extrait ou un post de suivi : c'est celle à laquelle la salle a réagi d'elle-même.",
      "Donnez plus de place à cette partie la prochaine fois, et reprenez son format (une démo, une histoire, des chiffres concrets) dans les passages plus calmes.",
    ],
  },
  "chapters.low": {
    en: [
      "Shorten the quiet part, or put a question to the room in its middle.",
      "Move the quiet part earlier, while attention is high, or turn it into a short handout.",
      "Break the quiet part with something to look at: a live demo, a poll, a customer's words.",
    ],
    fr: [
      "Raccourcissez la partie calme, ou posez une question à la salle en son milieu.",
      "Avancez la partie calme, quand l'attention est encore forte, ou faites-en un court document à partager.",
      "Coupez la partie calme avec quelque chose à regarder : une démo en direct, un sondage, la parole d'un client.",
    ],
  },
  "friction.incident": {
    en: [
      "Run a sound check with every speaker ten minutes before going live, with the same setup they will use.",
      "Have a moderator watch the chat for sound complaints and tell the speaker at once.",
      "Keep a backup ready (a second microphone, a wired connection, a co-host who can take over).",
    ],
    fr: [
      "Faites un test de son avec chaque intervenant dix minutes avant le direct, avec le même matériel.",
      "Demandez à un modérateur de surveiller le chat pour les problèmes de son et de prévenir l'intervenant tout de suite.",
      "Prévoyez une solution de secours (un second micro, une connexion filaire, un co-animateur prêt à prendre le relais).",
    ],
  },
  "friction.late": {
    en: [
      "Start on time, even with fewer people in: latecomers catch up, early arrivals leave.",
      "If you must wait, say so in the chat with the new start time.",
      "Put a slide up in the waiting room with the start time and what's coming.",
    ],
    fr: [
      "Commencez à l'heure, même avec moins de monde : les retardataires rattrapent, les premiers arrivés partent.",
      "Si vous devez attendre, dites-le dans le chat avec la nouvelle heure de début.",
      "Affichez en salle d'attente une diapositive avec l'heure de début et le programme.",
    ],
  },
  monologue: {
    en: [
      "Bring the room in every six to eight minutes: a question, a poll, a show of hands in the chat.",
      "Split a long stretch between two voices, or cut to a demo or a guest halfway.",
      "Read a few chat messages aloud in the middle of a long part: it brings the room back.",
    ],
    fr: [
      "Faites participer la salle toutes les six à huit minutes : une question, un sondage, un vote dans le chat.",
      "Partagez un long passage entre deux voix, ou passez à une démo ou à un invité à mi-parcours.",
      "Lisez quelques messages du chat à voix haute au milieu d'un long passage : la salle revient.",
    ],
  },
  prompts: {
    en: [
      "After asking the room something, wait ten seconds in silence: chat replies take that long to arrive.",
      "Ask, then read the first answers aloud before moving on.",
      "Have a co-host read the replies that come in once you've moved on, and come back to them.",
    ],
    fr: [
      "Après une question à la salle, attendez dix secondes en silence : c'est le temps qu'il faut aux réponses pour arriver.",
      "Posez la question, puis lisez les premières réponses à voix haute avant de continuer.",
      "Demandez à un co-animateur de relever les réponses qui arrivent après, et revenez-y.",
    ],
  },
  demand: {
    en: [
      "Answer the topic the room kept asking about in the follow-up email: several people are waiting for it.",
      "Put the topic the room kept asking about on the agenda next time, early.",
      "Prepare a short answer to the most asked topic before going live, and give it during the session.",
    ],
    fr: [
      "Répondez dans l'e-mail de suivi au sujet sur lequel la salle revenait : plusieurs personnes attendent la réponse.",
      "Mettez au programme, et tôt, le sujet sur lequel la salle revenait la prochaine fois.",
      "Préparez une réponse courte au sujet le plus demandé avant le direct, et donnez-la pendant la session.",
    ],
  },
  moments: {
    en: [
      "Clip the moments the room leaned in: they make the best excerpts to share.",
      "Look at what was said at those moments and do more of it next time.",
      "Use the busiest moment as the hook of the follow-up email.",
    ],
    fr: [
      "Découpez les moments où la salle s'est animée : ce sont les meilleurs extraits à partager.",
      "Regardez ce qui se disait à ces moments-là et faites-en davantage la prochaine fois.",
      "Utilisez le moment le plus animé comme accroche de l'e-mail de suivi.",
    ],
  },
};

/** The situation a found finding is in, for the library: `chapters.high`,
 * `friction.incident` and so on, or the finding's id. */
export function situation(finding) {
  const values = finding?.values || {};
  if (finding?.id === "chapters") return values.high ? "chapters.high" : "chapters.low";
  if (finding?.id === "friction") return (values.incidents || []).length ? "friction.incident" : "friction.late";
  return finding?.id || "";
}

// A small stable hash (FNV-1a) of a string.
function hash(text) {
  let value = 2166136261;
  for (const char of text) {
    value ^= char.codePointAt(0);
    value = Math.imul(value, 16777619) >>> 0;
  }
  return value;
}

/** The "Next time" line for a found finding, in `lang` ("en" or "fr"),
 * picked by `seed` (the session's id); "" when the library has none. */
export function nextTime(finding, lang = "en", seed = "") {
  const key = situation(finding);
  const lines = LIBRARY[key]?.[lang] || LIBRARY[key]?.en || [];
  return lines.length ? lines[hash(`${seed}:${key}`) % lines.length] : "";
}

/** Every situation, for tests: each has as many lines in French as in English. */
export const SITUATIONS = Object.keys(LIBRARY);
export const LINES = LIBRARY;
