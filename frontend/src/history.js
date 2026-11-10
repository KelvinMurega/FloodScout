/**
 * A region's recent scores as a small line chart (FR-05).
 *
 * Pure functions: dates in, coordinates out. main.js turns the result into
 * SVG. Tested in history.test.js.
 */

const DAY_MS = 86_400_000;

function toUtc(isoDate) {
  return Date.parse(`${isoDate}T00:00:00Z`);
}

/** The date `days` before `isoDate`, both as YYYY-MM-DD. */
export function daysBefore(isoDate, days) {
  return new Date(toUtc(isoDate) - days * DAY_MS).toISOString().slice(0, 10);
}

/**
 * API score rows as `[{ date, value }]`, oldest first.
 *
 * `value` is the score as a multiple of the usual chance. Rows without one
 * (placeholder scores) are dropped. The date is the first ten characters of
 * `prediction_date`, which the API sends in the project's own time zone.
 */
export function historySeries(scores) {
  return scores
    .filter((score) => typeof score.times_usual === "number" && score.prediction_date)
    .map((score) => ({ date: score.prediction_date.slice(0, 10), value: score.times_usual }))
    .sort((a, b) => a.date.localeCompare(b.date));
}

/**
 * Where to draw `series` in a `width` by `height` box.
 *
 * Days run left to right from `start` to `end`, so a day with no score
 * leaves a gap instead of being closed up. The vertical range always holds
 * 1, the usual chance, and `usualY` says where that line falls.
 */
export function sparkline(series, start, end, { width = 280, height = 64, pad = 6 } = {}) {
  const span = Math.max(1, (toUtc(end) - toUtc(start)) / DAY_MS);
  const values = series.map((point) => point.value);
  const low = Math.min(1, ...values);
  const high = Math.max(1, ...values);
  const range = high - low || 1;

  const xOf = (date) => pad + ((toUtc(date) - toUtc(start)) / DAY_MS / span) * (width - 2 * pad);
  const yOf = (value) => height - pad - ((value - low) / range) * (height - 2 * pad);
  const round = (n) => Math.round(n * 10) / 10;

  return {
    points: series.map((point) => ({ ...point, x: round(xOf(point.date)), y: round(yOf(point.value)) })),
    usualY: round(yOf(1)),
    low: values.length ? Math.min(...values) : null,
    high: values.length ? Math.max(...values) : null,
  };
}
