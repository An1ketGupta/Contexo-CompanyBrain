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

/**
 * Compact relative time ("5m", "3h", "2d", "1w"), then switches to absolute
 * "Mar 14" past 30 days. Use in table/list cells where horizontal space is
 * scarce — pair with `formatAbsolute` in a `title` for the full timestamp.
 */
export function formatRelativeShort(input: string | Date): string {
  const date = typeof input === "string" ? new Date(input) : input;
  if (Number.isNaN(date.getTime())) return "—";

  const seconds = Math.floor((Date.now() - date.getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
  if (seconds < 604800) return `${Math.floor(seconds / 86400)}d`;
  if (seconds < 2592000) return `${Math.floor(seconds / 604800)}w`;

  const sameYear = date.getFullYear() === new Date().getFullYear();
  return date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    ...(sameYear ? {} : { year: "numeric" }),
  });
}

/**
 * Full, human-readable timestamp ("Jun 15, 2026, 4:32 PM"). Use as the hover
 * tooltip companion to `formatRelativeShort`.
 */
export function formatAbsolute(input: string | Date): string {
  const date = typeof input === "string" ? new Date(input) : input;
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}
