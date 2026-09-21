/**
 * Risk categories and their colours.
 *
 * The four categories are *ordinal* — reordering them would change their
 * meaning — so the ramp is a single hue with monotone lightness steps rather
 * than four unrelated colours. Both sets below were checked mechanically
 * (monotone lightness, adjacent lightness gap >= 0.06, lightest step >= 2:1
 * against its own surface, single hue) rather than picked by eye:
 *
 *   light surface #fcfcfb : hue spread 11 deg, lightest step 2.16:1
 *   dark  surface #1a1a19 : hue spread  7 deg, darkest  step 2.65:1
 *
 * On the dark surface the ramp's anchor flips — severity runs toward the
 * brighter end, away from the background, so "very high" stays the loudest
 * thing on screen in either theme.
 *
 * Colour is never the only channel: the legend, the detail panel and the
 * county table all name the category in text.
 */

export const CATEGORY_ORDER = ["low", "moderate", "high", "very_high"];

export const CATEGORY_LABELS = {
  low: "Low",
  moderate: "Moderate",
  high: "High",
  very_high: "Very high",
};

const RAMP_LIGHT = {
  low: "#fb9272",
  moderate: "#fb6a4a",
  high: "#de2d26",
  very_high: "#a50f15",
};

const RAMP_DARK = {
  low: "#a33a28",
  moderate: "#d4553c",
  high: "#ef7f63",
  very_high: "#ffab90",
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
