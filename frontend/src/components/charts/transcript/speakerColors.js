const SPEAKER_PALETTE = [
  "var(--color-data-blue)",
  "var(--color-data-green)",
  "var(--color-data-peach)",
  "var(--color-data-grey)",
  "var(--color-data-red)",
  "var(--color-brand-100)",
  "var(--color-nimbus-400)",
];

export function buildSpeakerColorMap(speakers) {
  const orderedSpeakers = Array.from(
    new Set(
      (speakers || [])
        .map((speaker) => String(speaker || "Unknown").trim() || "Unknown")
        .filter(Boolean),
    ),
  ).sort((left, right) => left.localeCompare(right, undefined, { numeric: true, sensitivity: "base" }));

  return Object.fromEntries(
    orderedSpeakers.map((speaker, index) => [speaker, SPEAKER_PALETTE[index % SPEAKER_PALETTE.length]]),
  );
}
