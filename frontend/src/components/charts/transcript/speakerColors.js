const SPEAKER_PALETTE = ["#8eddf0", "#ffc247", "#f46a6f", "#63d084", "#8b9bff", "#d989ff", "#5fd5bb"];

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
