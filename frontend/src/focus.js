/**
 * The focus-region overview: what the dashboard opens on.
 *
 * The model is trained on all of Kenya, because the flood archive is
 * national and fewer counties would mean fewer flood months to learn from.
 * The proposal promised the selected flood-prone regions, so the front door
 * shows those (geodata/focus_regions.py) and All Kenya is one click away.
 *
 *   #/         the focus overview: the focus counties coloured, the rest faint
 *   #/kenya    All Kenya: every county coloured
 *   #/<slug>   one focus region, at sub-county level
 */

import { CATEGORY_ORDER } from "./risk.js";

export const KENYA_SLUG = "kenya";

export const LEVEL_ORDER = ["warning", "watch", "advisory"];

/** "focus", "kenya" or null (a region or a page) for the slug in the hash. */
export function overviewScope(slug) {
  if (slug === "") return "focus";
  if (slug === KENYA_SLUG) return "kenya";
  return null;
}

/** The overview a county belongs on: the focus overview if it is in a focus region. */
export function countySlug(countyId, focusByCountyId) {
  return focusByCountyId.has(countyId) ? "" : KENYA_SLUG;
}

/**
 * One line per focus region: its worst county category on the scores shown,
 * and the alerts in force anywhere in it (its counties and their sub-counties).
 */
export function regionSummaries(focusRegions, scoresByRegion, alerts) {
  return focusRegions.map((focus) => {
    const names = new Set(focus.counties.map((c) => c.name));
    const worst = focus.counties
      .map((c) => scoresByRegion.get(c.id)?.risk_category)
      .filter((category) => CATEGORY_ORDER.includes(category))
      .sort((a, b) => CATEGORY_ORDER.indexOf(b) - CATEGORY_ORDER.indexOf(a))[0] ?? null;
    const counts = Object.fromEntries(LEVEL_ORDER.map((level) => [level, 0]));
    for (const alert of alerts) {
      if (names.has(alert.county_name) && alert.level in counts) counts[alert.level] += 1;
    }
    return { slug: focus.slug, name: focus.name, worst, alerts: counts };
  });
}

/** "2 warnings, 5 watches" or "No alerts". */
export function alertSummary(counts) {
  const plural = { warning: "warnings", watch: "watches", advisory: "advisories" };
  const parts = LEVEL_ORDER.filter((level) => counts[level] > 0).map(
    (level) => `${counts[level]} ${counts[level] === 1 ? level : plural[level]}`,
  );
  return parts.length ? parts.join(", ") : "No alerts";
}
