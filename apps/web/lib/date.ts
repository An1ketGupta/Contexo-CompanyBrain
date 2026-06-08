const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 60 * 60 * 24 * 365],
  ["month", 60 * 60 * 24 * 30],
  ["week", 60 * 60 * 24 * 7],
  ["day", 60 * 60 * 24],
  ["hour", 60 * 60],
  ["minute", 60],
  ["second", 1],
];

const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

/**
 * Relative time string ("3 hours ago", "yesterday"). Uses Intl.RelativeTimeFormat
 * so we don't need date-fns just for this one helper.
 */
export function formatDistanceToNow(input: string | Date): string {
  const date = typeof input === "string" ? new Date(input) : input;
  if (Number.isNaN(date.getTime())) return "—";

  const seconds = Math.round((date.getTime() - Date.now()) / 1000);

  for (const [unit, perUnit] of UNITS) {
    if (Math.abs(seconds) >= perUnit || unit === "second") {
      return rtf.format(Math.round(seconds / perUnit), unit);
    }
  }
  return rtf.format(0, "second");
}
