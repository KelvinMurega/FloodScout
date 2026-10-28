/**
 * The simple view's words: what the numbers mean, said without them.
 *
 * The detailed map speaks in times the usual chance, categories and model
 * versions. Most people want three things: is it worse than normal here, is
 * heavy rain coming, and what should I do. These turn the same data into
 * that, and nothing here invents a number the API did not give.
 */

/**
 * The flood chance against an ordinary year for the place and month
 * (`times_season`), as a word and a sentence. A place whose window was dry
 * (category Low or Moderate, riskengine/dry.py) is never "higher".
 */
export function seasonVerdict(timesSeason, category) {
  if (typeof timesSeason !== "number") {
    return { key: "unknown", word: "Not known", text: "There is no reading for this place today." };
  }
  const dry = category === "low" || category === "moderate";
  if (timesSeason < 0.91) {
    return { key: "lower", word: "Lower than usual", text: "A lower chance of flooding than usual for this time of year." };
  }
  if (timesSeason <= 1.25 || dry) {
    return { key: "usual", word: "About usual", text: "About the usual chance of flooding for this time of year." };
  }
  if (timesSeason <= 1.5) {
    return { key: "higher", word: "Higher than usual", text: "A higher chance of flooding than usual for this time of year." };
  }
  return { key: "much_higher", word: "Much higher than usual", text: "A much higher chance of flooding than usual for this time of year." };
}

/** The three colours of the simple map: much higher shares higher's. */
export function mapBucket(verdictKey) {
  if (verdictKey === "much_higher") return "higher";
  return verdictKey;
}

// The Kenya Meteorological Department's advisories start at 20 mm in a day;
// 50 mm in a day is its flood early-warning line (alerts/rules.py).
export const HEAVY_DAY_MM = 20;
export const VERY_HEAVY_DAY_MM = 50;
const SOME_RAIN_MM = 2;

/**
 * Each forecast day's own rain, from rows whose `rain_forecast_mm` runs from
 * the issue to that day: [{ date: "YYYY-MM-DD", mm }], in date order.
 */
export function dailyRain(rows) {
  const sorted = [...rows]
    .filter((r) => typeof r.rain_forecast_mm === "number")
    .sort((a, b) => a.target_date.localeCompare(b.target_date));
  let before = 0;
  return sorted.map((r) => {
    const mm = Math.max(0, r.rain_forecast_mm - before);
    before = r.rain_forecast_mm;
    return { date: r.target_date, mm };
  });
}

function dayName(iso) {
  return new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { weekday: "long" });
}

function listDays(days) {
  const names = days.map((d) => dayName(d.date));
  return names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names.at(-1)}` : names[0];
}

/** The coming week's rain in a sentence, and how strong it is: { level, text }. */
export function weekRain(rows) {
  const days = dailyRain(rows);
  if (days.length === 0) return { level: "unknown", text: "No rain forecast is available yet." };
  const veryHeavy = days.filter((d) => d.mm >= VERY_HEAVY_DAY_MM);
  const heavy = days.filter((d) => d.mm >= HEAVY_DAY_MM);
  const wet = days.filter((d) => d.mm >= SOME_RAIN_MM);
  if (veryHeavy.length) return { level: "very_heavy", text: `Very heavy rain is forecast on ${listDays(veryHeavy)}.` };
  if (heavy.length) return { level: "heavy", text: `Heavy rain is forecast on ${listDays(heavy)}.` };
  if (wet.length >= 4) return { level: "some", text: "Some rain is forecast on most days, none of it heavy." };
  if (wet.length) return { level: "some", text: `Light rain is forecast on ${listDays(wet)}.` };
  return { level: "dry", text: "Little or no rain is forecast this week." };
}

/** "early October", "mid-October" or "late October" for an ISO date. */
export function timeOfYear(iso) {
  const date = new Date(iso.length === 10 ? `${iso}T00:00:00` : iso);
  const month = date.toLocaleDateString(undefined, { month: "long" });
  const day = date.getDate();
  if (day <= 10) return `early ${month}`;
  if (day <= 20) return `mid-${month}`;
  return `late ${month}`;
}

/**
 * What to do, in a few lines, when no alert gives its own instruction.
 * Never calm while alerts are in force elsewhere in the county.
 */
export function everydayAdvice(verdictKey, rainLevel, alertsNearby = 0) {
  const worrying =
    alertsNearby > 0 ||
    verdictKey === "higher" ||
    verdictKey === "much_higher" ||
    rainLevel === "heavy" ||
    rainLevel === "very_heavy";
  if (worrying) {
    return [
      "Know the way to higher ground from home, school and work.",
      "Keep documents, medicines and a charged phone in a waterproof bag.",
      "Do not walk or drive through moving water: 15 cm can knock you down.",
      "Clear drains and gutters around your home.",
    ];
  }
  return [
    "Nothing needs doing today.",
    "It is still worth knowing the way to higher ground, and clearing drains before the rains.",
  ];
}

/** The strongest alert for a place: its own, else its county's, else none. */
export function alertFor(place, alerts) {
  const rank = { warning: 3, watch: 2, advisory: 1 };
  const own = alerts.filter((a) => a.region === place.id);
  const county = place.level === "sub_county" ? alerts.filter((a) => a.region_level === "county" && a.county_name === place.county) : [];
  const pick = (list) => [...list].sort((a, b) => (rank[b.level] ?? 0) - (rank[a.level] ?? 0))[0] ?? null;
  return pick(own) ?? pick(county);
}

/** For a county: how many of its sub-counties have an alert, by level. */
export function subCountyAlerts(place, alerts) {
  if (place.level !== "county") return 0;
  return alerts.filter((a) => a.region_level === "sub_county" && a.county_name === place.name).length;
}

/**
 * A county's sub-counties grouped as the simple map colours them, the most
 * worrying first within each group: { higher, usual, lower, unknown }.
 */
export function groupAreas(scores) {
  const groups = { higher: [], usual: [], lower: [], unknown: [] };
  for (const score of scores) {
    const bucket = mapBucket(seasonVerdict(score.times_season, score.risk_category).key);
    groups[bucket in groups ? bucket : "unknown"].push(score);
  }
  for (const list of Object.values(groups)) list.sort((a, b) => (b.times_season ?? 0) - (a.times_season ?? 0));
  return groups;
}

/** Whether [lon, lat] lies inside a GeoJSON Polygon or MultiPolygon (holes respected). */
export function pointInGeometry([x, y], geometry) {
  const inRing = (ring) => {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [xi, yi] = ring[i];
      const [xj, yj] = ring[j];
      if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  };
  const inPolygon = (rings) => inRing(rings[0]) && !rings.slice(1).some(inRing);
  if (geometry?.type === "Polygon") return inPolygon(geometry.coordinates);
  if (geometry?.type === "MultiPolygon") return geometry.coordinates.some(inPolygon);
  return false;
}
