/**
 * The printable report and the CSV export (Chapter 4, wireframe 4.10).
 *
 * Pure functions over the score rows the API returned, so what is exported
 * is what is on screen. Tested in report.test.js.
 */

import { labelFor } from "./risk.js";

/** Highest model output first; names break ties so the order is stable. */
export function sortByRisk(scores) {
  return [...scores].sort(
    (a, b) =>
      (b.predicted_value ?? -1) - (a.predicted_value ?? -1) ||
      String(a.region_name).localeCompare(String(b.region_name)),
  );
}

/** One CSV field, quoted when it holds a comma, a quote or a line break (RFC 4180). */
export function csvCell(value) {
  if (value === null || value === undefined) return "";
  const text = String(value);
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

export const CSV_COLUMNS = [
  "region",
  "county",
  "level",
  "risk_category",
  "times_usual_chance",
  "model_output",
  "date",
  "model_version",
];

/**
 * Score rows as CSV text, highest risk first.
 *
 * `date` is the day a score is for, or the month an outlook row is for.
 * `model_output` is the model's raw value; the evaluation found it runs
 * high as a probability, which is why the dashboard shows the multiple of
 * the usual chance instead (Chapter 5, Section 5.7.5).
 */
export function scoresToCsv(scores) {
  const lines = [CSV_COLUMNS.join(",")];
  for (const score of sortByRisk(scores)) {
    lines.push(
      [
        score.region_name,
        score.county_name,
        score.region_level,
        labelFor(score.risk_category),
        score.times_usual,
        score.predicted_value,
        (score.prediction_date ?? score.month ?? "").slice(0, 10),
        score.model_version,
      ]
        .map(csvCell)
        .join(","),
    );
  }
  return `${lines.join("\r\n")}\r\n`;
}

/** A file name that says what the export holds: floodscout-lake-basin-2026-10-08.csv */
export function reportFileName(view, date) {
  const slug = String(view || "kenya")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
  return `floodscout-${slug || "kenya"}${date ? `-${date}` : ""}.csv`;
}
