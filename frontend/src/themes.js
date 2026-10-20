/**
 * What the map's colour can mean, and the scale for each.
 *
 * The risk category alone colours almost all of Kenya the same in a wet
 * season: on 19 October 2026 every county was High or Very High. The
 * category is the official reading and stays, but the other themes are
 * continuous values drawn on graduated scales, so places that share a
 * category still look different:
 *
 *   usual   the score as a multiple of the usual chance of a large flood
 *   change  that multiple now minus a week before: where risk is rising
 *   rain    rain in the last 30 days over the county's normal for the month
 *
 * Breaks are fixed, not quantiles, so a colour means the same thing on
 * every day and in every region. The palettes are ColorBrewer's (YlOrRd,
 * RdBu reversed, BrBG), chosen for print and colour-blind safety.
 */

import { CATEGORY_ORDER, colourFor, labelFor } from "./risk.js";

export const THEME_ORDER = ["usual", "risk", "change", "rain"];

export const THEMES = {
  risk: {
    name: "Risk level",
    hint: "the four official categories",
  },
  usual: {
    name: "Risk vs usual",
    hint: "times the usual chance of a large flood",
    breaks: [1, 1.25, 1.5, 1.75, 2, 2.5],
    colours: ["#ffffcc", "#ffeda0", "#fed976", "#feb24c", "#fd8d3c", "#f03b20", "#bd0026"],
    format: (v) => `${v}×`,
  },
  change: {
    name: "Change in 7 days",
    hint: "times usual now, minus a week ago",
    breaks: [-0.5, -0.2, -0.05, 0.05, 0.2, 0.5],
    colours: ["#2166ac", "#67a9cf", "#d1e5f0", "#f2f2f0", "#fddbc7", "#ef8a62", "#b2182b"],
    format: (v) => (v > 0 ? `+${v}` : `${v}`),
  },
  rain: {
    name: "Rain vs normal",
    hint: "last 30 days against the usual for the month",
    breaks: [0.25, 0.5, 0.8, 1.2, 1.5, 2, 3],
    colours: [
      "#8c510a",
      "#bf812d",
      "#dfc27d",
      "#f2f2f0",
      "#c7eae5",
      "#80cdc1",
      "#35978f",
      "#01665e",
    ],
    format: (v) => `${Math.round(v * 100)}%`,
  },
};

// Never mistaken for a low reading: the same grey as an unscored region.
const NO_VALUE = "#c9c8c2";
const NO_VALUE_DARK = "#4a4a47";

/** Index of the class `value` falls in: below breaks[0] is 0. */
export function classIndex(value, breaks) {
  let index = 0;
  while (index < breaks.length && value >= breaks[index]) index += 1;
  return index;
}

/**
 * The number a theme colours by, from a score row and its context row.
 * Grid cells and outlook rows carry their own fields, read here too.
 */
export function themeValue(theme, score, context) {
  const pick = (...values) => values.find((v) => typeof v === "number") ?? null;
  switch (theme) {
    case "usual":
      return pick(context?.times_usual, score?.times_usual);
    case "change":
      return pick(context?.change_7d);
    case "rain":
      return pick(context?.rain_30d_ratio, score?.rain_30d_ratio, score?.rain_vs_normal);
    default:
      return null;
  }
}

export function themeColour(theme, score, context, dark = false) {
  if (theme === "risk") return colourFor(score?.risk_category, dark);
  const value = themeValue(theme, score, context);
  if (value === null) return dark ? NO_VALUE_DARK : NO_VALUE;
  const scale = THEMES[theme];
  return scale.colours[classIndex(value, scale.breaks)];
}

/** "1.25× to 1.5×", "under 1×", "2.5× and over". */
export function classLabel(theme, index) {
  const { breaks, format } = THEMES[theme];
  if (index === 0) return `Under ${format(breaks[0])}`;
  if (index === breaks.length) return `${format(breaks.at(-1))} and over`;
  return `${format(breaks[index - 1])} to ${format(breaks[index])}`;
}

/**
 * The legend for `theme`: one entry per class with how many of `rows`
 * ({ score, context }) fall in it, plus "no value" when any have none.
 */
export function legendEntries(theme, rows, dark = false) {
  if (theme === "risk") {
    return CATEGORY_ORDER.map((category) => ({
      colour: colourFor(category, dark),
      label: labelFor(category),
      count: rows.filter((r) => r.score?.risk_category === category).length,
    }));
  }
  const { breaks, colours } = THEMES[theme];
  const counts = new Array(colours.length).fill(0);
  let missing = 0;
  for (const { score, context } of rows) {
    const value = themeValue(theme, score, context);
    if (value === null) missing += 1;
    else counts[classIndex(value, breaks)] += 1;
  }
  // Highest first, the way a reader scans for danger.
  const entries = colours
    .map((colour, index) => ({ colour, label: classLabel(theme, index), count: counts[index] }))
    .reverse();
  if (missing > 0) {
    entries.push({ colour: dark ? NO_VALUE_DARK : NO_VALUE, label: "Not known", count: missing });
  }
  return entries;
}

/** Whether `theme` has anything to show for the view described. */
export function themeAvailable(theme, { outlook = false, grid = false } = {}) {
  if (theme === "change") return !outlook && !grid;
  return true;
}

/** An arrow and words for a 7-day change, for tooltips and the detail panel. */
export function describeChange(change) {
  if (typeof change !== "number") return null;
  if (Math.abs(change) < 0.05) return "→ steady over the last 7 days";
  const size = Math.abs(change).toFixed(1);
  return change > 0
    ? `↑ up ${size} times usual in 7 days`
    : `↓ down ${size} times usual in 7 days`;
}

/** "212 mm, 180% of normal", or what can be said without a normal. */
export function describeRain(context) {
  if (typeof context?.rain_30d !== "number") return null;
  const mm = `${Math.round(context.rain_30d)} mm in 30 days`;
  return typeof context.rain_30d_ratio === "number"
    ? `${mm}, ${Math.round(context.rain_30d_ratio * 100)}% of normal`
    : mm;
}
