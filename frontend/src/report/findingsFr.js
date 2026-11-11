// The French words of the Report's findings: the same templates as
// `findings.js`, in French (decimal commas, « guillemets »), filled with the
// same server values. `findings.js` picks them with `findingsView(body, "fr")`.

import { clockLabel } from "./chartGeometry";

const number = (value) => String(value).replace(".", ",");
const plural = (count, one, many) => `${count} ${count === 1 || count === 0 ? one : many}`;
const fromPeople = (people) => (people > 0 ? ` de ${plural(people, "personne", "personnes")}` : "");
const minutes = (seconds) => Math.max(1, Math.round(seconds / 60));
const times = (index) => `${number((index / 100).toFixed(1).replace(/\.0$/, ""))}×`;
const quoted = (text) => `« ${text} »`;

function chapters(values) {
  const rows = values.chapters || [];
  const usual = values.usualPerMinute;
  const high = rows.find((row) => row.title === values.high);
  const low = rows.find((row) => row.title === values.low);
  const headline = high ? `${quoted(high.title)} a fait réagir la salle` : `La salle s'est tue pendant ${quoted(low.title)}`;
  const lines = [];
  if (high) {
    lines.push(
      `${number(high.perMinute)} messages par minute${fromPeople(high.chatters)}, ${times(high.index)} le chapitre habituel (${number(usual)} par minute).`,
    );
  }
  if (low) lines.push(`Pendant ${quoted(low.title)}, le chat est tombé à ${number(low.perMinute)} par minute, ${times(low.index)} l'habituel.`);
  lines.push(
    "Seul ce que la salle a écrit d'elle-même compte : ni les salutations, ni la logistique, ni les problèmes de son, ni les réponses à une question posée sur scène.",
  );
  const top = Math.max(usual * 2 || 0, ...rows.map((row) => row.perMinute || 0)) || 1;
  return {
    headline,
    lines,
    chart: {
      title: "Le chat spontané de la salle, en messages par minute, par chapitre",
      usual: usual === null || usual === undefined ? "" : `habituel : ${number(usual)} par minute`,
      usualLeft: usual ? (usual / top) * 100 : null,
      rows: rows.map((row) => ({
        title: row.title,
        when: `${clockLabel(row.t)} · ${minutes(row.seconds)} min`,
        width: row.perMinute === null ? 0 : Math.min((row.perMinute / top) * 100, 100),
        value:
          row.perMinute !== null
            ? `${number(row.perMinute)}/min`
            : row.qa
              ? `Q&R · ${plural(row.questions, "question", "questions")}`
              : "moins de 2 min",
        tone: row.title === values.high ? "high" : row.title === values.low ? "low" : "",
      })),
    },
    rows: [],
  };
}

function friction(values) {
  const found = values.incidents || [];
  const late = values.lateStart;
  const top = found.reduce((best, item) => (best === null || item.messages > best.messages ? item : best), null);
  let headline = `La session a commencé avec ${minutes(late?.lateS || 0)} minutes de retard`;
  if (top && top.people > 0) headline = `${plural(top.people, "personne a", "personnes ont")} signalé un problème de son ou de connexion à ${clockLabel(top.start)}`;
  else if (top) headline = `${plural(top.messages, "message", "messages")} sur le son ou la connexion à partir de ${clockLabel(top.start)}`;
  const rows = found.map((item) => {
    const parts = [`${plural(item.messages, "message", "messages")}${fromPeople(item.people)}`];
    if (values.speech) parts.push(item.deadAirS > 0 ? `${Math.round(item.deadAirS)} s de silence sur scène` : "aucun silence sur scène");
    return `${clockLabel(item.start)}–${clockLabel(item.end)} · ${parts.join(" · ")}`;
  });
  if (late && top) rows.push(`Début avec ${minutes(late.lateS)} min de retard · ${plural(late.waitingMessages, "message", "messages")} en salle d'attente`);
  const lines = top
    ? ["Les messages que l'analyse a classés comme problèmes de son, de vidéo ou de connexion, regroupés quand ils arrivent à moins de deux minutes d'intervalle."]
    : [`${plural(late.waitingMessages, "message", "messages")} du public en salle d'attente avant le début.`];
  return { headline, lines, chart: null, rows };
}

function monologue(values) {
  return {
    headline: `${minutes(values.seconds)} minutes sans faire participer la salle, et le chat a baissé`,
    lines: [
      `De ${clockLabel(values.start)} à ${clockLabel(values.end)}, une seule personne a parlé, sans question à la salle ni question du public reprise. Le chat spontané est passé de ${number(values.firstPerMinute)} à ${number(values.lastPerMinute)} messages par minute entre le premier et le dernier tiers.`,
      "Le chat sert ici d'indice d'attention : une salle silencieuse peut encore écouter.",
    ],
    chart: null,
    rows: [],
  };
}

function prompts(values) {
  const missed = new Set(values.missed || []);
  const rows = (values.prompts || []).filter((row) => missed.has(row.t));
  return {
    headline:
      rows.length === 1
        ? "La salle a répondu après que l'intervenant est passé à la suite"
        : `La salle a répondu à ${rows.length} questions après que l'intervenant est passé à la suite`,
    lines: [
      "Quand l'intervenant a posé une question à la salle, la plupart des réponses sont arrivées après qu'il a repris la parole. Quelques secondes de silence de plus auraient laissé la salle répondre.",
      "Les réponses ne sont pas identifiées une à une : les messages au-delà du rythme habituel de la session en tiennent lieu.",
    ],
    chart: null,
    rows: rows.map((row) => {
      const parts = [row.waitS === null ? "pas de parole ensuite" : `attente de ${Math.round(row.waitS)} s`];
      parts.push(`${plural(row.messages, "message", "messages")} dans les 2 min (environ ${Math.round(row.expected)} habituellement)`);
      if (row.waitS !== null) parts.push(`${row.late} après la reprise`);
      return `${clockLabel(row.t)} ${quoted(row.quote)} · ${parts.join(" · ")}`;
    }),
  };
}

function moments(values) {
  const list = values.moments || [];
  const usual = values.usualPerMinute;
  const lines = [
    `Les minutes où la salle a écrit bien plus que ses ${number(usual)} messages habituels par minute, d'elle-même : ni en réponse à une question posée sur scène, ni pour saluer.`,
  ];
  if (!values.quotes) lines.push("L'horloge de l'enregistrement n'est pas vérifiée à la seconde : les paroles prononcées ne sont pas affichées.");
  return {
    headline:
      list.length === 1
        ? `La salle s'est animée d'elle-même à ${clockLabel(list[0].start)}`
        : `La salle s'est animée d'elle-même ${list.length} fois`,
    lines,
    chart: null,
    rows: list.map((moment) => {
      const parts = [`${plural(moment.messages, "message", "messages")}${fromPeople(moment.people)}`];
      if (usual > 0) parts.push(`${Math.round(moment.peak / usual)}× la minute habituelle`);
      const head = `${moment.n}. ${clockLabel(moment.start)}–${clockLabel(moment.end)} · ${parts.join(" · ")}`;
      return moment.quote ? `${head} · pendant que l'intervenant disait ${quoted(moment.quote)}` : head;
    }),
  };
}

const NOTHING = {
  chapters: () => "Chapitres : le chat spontané a été régulier d'un chapitre à l'autre.",
  friction: () => "Production : pas de vague de plaintes sur le son ou la connexion, et pas de retard au démarrage.",
  monologue: (values) =>
    values?.firstPerMinute === undefined
      ? `Monologues : le plus long passage sans faire participer la salle a duré ${minutes(values?.seconds || 0)} min.`
      : `Monologues : le plus long passage a duré ${minutes(values.seconds)} min, et le chat a tenu.`,
  moments: (values) => {
    const others = [];
    if (values?.replies) others.push(`${plural(values.replies, "réponse", "réponses")} à une question posée sur scène`);
    if (values?.waves) others.push(`${plural(values.waves, "vague", "vagues")} de salutations ou de logistique`);
    return `Moments : aucun pic au-delà des ${number(values?.usualPerMinute ?? 0)} messages habituels par minute${others.length ? `, hormis ${others.join(" et ")}` : ""}.`;
  },
  prompts: (values) => {
    const count = (values?.prompts || []).length;
    return `Questions à la salle : elle a eu le temps de répondre ${count === 1 ? "à la seule question" : `aux ${count} questions`}.`;
  },
  demand: (values) =>
    (values?.topics || []).length
      ? "Ce que la salle a demandé : chaque sujet sur lequel plusieurs personnes ont posé des questions a eu ses réponses."
      : "Ce que la salle a demandé : aucun sujet n'a réuni 3 questions ou plus.",
};

const NOT_CHECKED = {
  chapters: {
    no_chapters: "Chapitres : aucun pour l'instant. Lancez « Analyse session » pour les trouver.",
    clock: "Chapitres : l'horloge de l'enregistrement ne correspond pas à celle de la session.",
    labels: "Chapitres : nécessite « Analyse session », pour distinguer le chat spontané des salutations et des réponses.",
    short: "Chapitres : la session dure moins de 10 minutes.",
    few_messages: "Chapitres : trop peu de chat spontané pour les comparer.",
    few_chapters: "Chapitres : moins de deux chapitres assez longs pour être comparés.",
  },
  friction: {
    labels: "Production : nécessite « Analyse session » pour lire les plaintes sur le son et la connexion. La session a commencé à l'heure.",
  },
  monologue: {
    clock: "Monologues : nécessite la transcription placée sur l'horloge de la session.",
    labels: "Monologues : nécessite « Analyse session », pour distinguer le chat spontané des salutations.",
    no_speech: "Monologues : la transcription n'a pas de parole horodatée.",
  },
  moments: {
    labels: "Moments : nécessite « Analyse session », pour distinguer les pics spontanés des salutations et des réponses.",
    clock: "Moments : la durée de la session est inconnue.",
    few_messages: "Moments : moins de 30 messages du public, trop peu pour trouver un pic.",
  },
  prompts: {
    clock: "Questions à la salle : nécessite l'horloge de l'enregistrement vérifiée à la seconde.",
    no_prompts: "Questions à la salle : l'analyse n'en a trouvé aucune dans l'intervention.",
  },
  demand: {
    no_reading: "Ce que la salle a demandé : nécessite « Analyse session » sur la session telle qu'elle est, pour regrouper les questions.",
  },
};

function talkLine(topic) {
  if (!topic.talkS) return "aucune réponse trouvée dans l'intervention";
  const where = topic.chapter ? ` dans ${quoted(topic.chapter)}` : "";
  return `${topic.talkS < 60 ? `${Math.round(topic.talkS)} s` : `${minutes(topic.talkS)} min`} de l'intervention y ont répondu${where}`;
}

function demand(values) {
  const topics = values.topics || [];
  const unmet = topics.filter((topic) => (values.unmet || []).includes(topic.label));
  const top = unmet[0];
  return {
    headline:
      unmet.length === 1
        ? top.people > 1
          ? `${top.people} personnes ont posé des questions sur ${quoted(top.label)}, la plupart sans réponse`
          : `${top.asked} questions portaient sur ${quoted(top.label)}, la plupart sans réponse`
        : `${unmet.length} sujets sur lesquels la salle revenait ont reçu peu de réponses`,
    lines: [
      "L'analyse a regroupé les questions qui demandent la même chose. Un sujet ressort quand la moitié de ses questions ou plus n'ont pas de réponse : ni écrite, ni marquée comme répondue, ni confirmée dans l'intervention.",
    ],
    chart: null,
    rows: topics.map(
      (topic) =>
        `${quoted(topic.label)} · ${plural(topic.asked, "question", "questions")}${fromPeople(topic.people)} · ${topic.unmet} sans réponse · ${talkLine(topic)}`,
    ),
  };
}

export const FR = {
  found: { chapters, friction, monologue, prompts, moments, demand },
  nothing: NOTHING,
  notChecked: NOT_CHECKED,
  labels: { title: "Ce qui ressort", quiet: "Rien ne ressort du rythme habituel de la session.", checked: "Vérifié, rien d'inhabituel", notChecked: "Non vérifié", notCheckedNote: (n) => `${n === 1 ? "1 vérification n'a" : `${n} vérifications n'ont`} pas pu être faite${n === 1 ? "" : "s"} : la raison est dans « Method and data quality ».`, next: "La prochaine fois", measured: "Mesuré", estimated: "Estimé" },
};
