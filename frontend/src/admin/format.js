/**
 * Pure helpers for the administrator's analytics page: labels, number
 * formats and the small reshapes the charts and tables need. No DOM here,
 * so `node --test` can check them.
 */

export const FEATURE_LABELS = {
  rain_30d: "Rain in the last 30 days",
  rain_7d_max: "Wettest 7 days in a row",
  rain_prev_30d: "Rain in the 30 days before",
  rain_30d_ratio: "Rain against the usual for the month",
  oni: "El Niño index (ONI)",
  dmi: "Indian Ocean Dipole (DMI)",
  oni_short_rains: "ONI in the short rains",
  dmi_short_rains: "DMI in the short rains",
  month_sin: "Season (sine of month)",
  month_cos: "Season (cosine of month)",
  elevation: "Elevation",
  slope: "Slope",
  log_area: "County area (log)",
};

export function featureLabel(key) {
  return FEATURE_LABELS[key] ?? key;
}

export const BAND_LABELS = {
  low: "Low",
  moderate: "Moderate",
  high: "High",
  very_high: "Very high",
};

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function monthName(month) {
  return MONTHS[month - 1] ?? String(month);
}

export const STATUS_LABELS = { ok: "Healthy", warning: "Needs attention", degraded: "Degraded" };

/** 0.2535 -> "0.254"; null -> "–". */
export function fixed(value, digits = 3) {
  return value === null || value === undefined ? "–" : Number(value).toFixed(digits);
}

/** 0.0968 -> "9.7%". */
export function percent(value, digits = 1) {
  return value === null || value === undefined ? "–" : `${(value * 100).toFixed(digits)}%`;
}

/** 14523 -> "14,523". */
export function count(value) {
  return value === null || value === undefined ? "–" : Number(value).toLocaleString("en-GB");
}

/** [1996, 2002, 2008] -> "1996, 2002, 2008". */
export function yearList(years) {
  return years.join(", ");
}

/** A fold's years as a short axis label: "1996 +4". */
export function foldLabel(years) {
  return years.length > 1 ? `${years[0]} +${years.length - 1}` : String(years[0]);
}

/** Seconds as "7 min 24 s" or "4.3 s". */
export function duration(seconds) {
  if (seconds === null || seconds === undefined) return "–";
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes} min ${Math.round(seconds - minutes * 60)} s`;
}

/** "2026-10-19" -> "19 Oct 2026", read as a calendar day, not a UTC instant. */
export function day(isoDate) {
  if (!isoDate) return "–";
  const [year, month, date] = isoDate.split("-").map(Number);
  return `${date} ${monthName(month)} ${year}`;
}

/**
 * The 2×2 confusion matrix at one threshold, with each cell's share of its
 * row, so "caught" reads as the share of floods caught.
 */
export function confusion(row) {
  const floods = row.true_positives + row.false_negatives;
  const dry = row.false_positives + row.true_negatives;
  return {
    floods,
    dry,
    cells: [
      { key: "tp", label: "Flood, warned", value: row.true_positives, share: row.true_positives / floods },
      { key: "fn", label: "Flood, missed", value: row.false_negatives, share: row.false_negatives / floods },
      { key: "fp", label: "No flood, warned", value: row.false_positives, share: row.false_positives / dry },
      { key: "tn", label: "No flood, quiet", value: row.true_negatives, share: row.true_negatives / dry },
    ],
  };
}

/**
 * Per-county error rows with the share of floods caught, worst first.
 * Counties with no recorded flood have nothing to catch and go last.
 */
export function countyErrors(rows) {
  return rows
    .map((row) => ({ ...row, recall: row.floods ? row.caught / row.floods : null }))
    .sort((a, b) => {
      if (a.recall === null) return b.recall === null ? a.county.localeCompare(b.county) : 1;
      if (b.recall === null) return -1;
      return a.recall - b.recall || b.floods - a.floods;
    });
}

/** The worst status of a list of checks. */
export function worstStatus(checks) {
  const rank = { ok: 0, warning: 1, degraded: 2 };
  return checks.reduce(
    (worst, check) => (rank[check.status] > rank[worst] ? check.status : worst),
    "ok",
  );
}

/** The admin tab named in the hash `#/admin/<tab>`, or the first tab. */
export function tabFromSlug(slug, tabs) {
  const wanted = slug.split("/")[1];
  return tabs.find((tab) => tab.id === wanted)?.id ?? tabs[0].id;
}
