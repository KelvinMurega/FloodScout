/**
 * Risk categories and their colours.
 *
 * A traffic-light scheme: green (low), orange (moderate), light red (high),
 * dark red (very high). The dark-theme set keeps the same hues but lifts
 * green and orange slightly, and keeps "very high" a saturated dark red
 * that still clears 3:1 against the dark surface (#1a1a19).
 *
 * Colour is never the only channel: the legend, the detail panel and the
 * county table all name the category in text, which matters here because
 * green/red is the hardest pair for red-green colour-blind readers.
 */

export const CATEGORY_ORDER = ["low", "moderate", "high", "very_high"];

export const CATEGORY_LABELS = {
  low: "Low",
  moderate: "Moderate",
  high: "High",
  very_high: "Very high",
};

const RAMP_LIGHT = {
  low: "#2e9e4f",
  moderate: "#f08c00",
  high: "#f26d6d",
  very_high: "#a50f15",
};

const RAMP_DARK = {
  low: "#3fb760",
  moderate: "#f59f25",
  high: "#ff8a80",
  very_high: "#c62828",
};

// Regions the API returned no score for. Deliberately a desaturated grey in
// both themes so "unknown" can never be mistaken for a low reading.
const NO_DATA_LIGHT = "#d8d7d2";
const NO_DATA_DARK = "#4a4a47";

export function prefersDark() {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-color-scheme: dark)").matches
  );
}

export function colourFor(category, dark = prefersDark()) {
  const ramp = dark ? RAMP_DARK : RAMP_LIGHT;
  const noData = dark ? NO_DATA_DARK : NO_DATA_LIGHT;
  return ramp[category] ?? noData;
}

export function labelFor(category) {
  return CATEGORY_LABELS[category] ?? "No score";
}

/**
 * Relocation advisories, keyed by the API's `relocation_advisory` value.
 * The rule that assigns them lives server-side in riskengine/advisory.py;
 * this is only how they read on screen.
 */
export const ADVISORY_LABELS = {
  relocate: "Relocate now",
  prepare: "Prepare to move",
  none: "No action",
};

export function advisoryFor(advisory) {
  return ADVISORY_LABELS[advisory] ?? "No score";
}
