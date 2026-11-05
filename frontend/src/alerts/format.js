/**
 * Pure helpers for the alerts page and banner, testable under node.
 */

export const LEVEL_ORDER = ["warning", "watch", "advisory"];

export const LEVEL_LABELS = {
  warning: "Flood warning",
  watch: "Flood watch",
  advisory: "Flood advisory",
};

export const LEVEL_PLURALS = {
  warning: "Flood warnings",
  watch: "Flood watches",
  advisory: "Flood advisories",
};

/** "Nyando, Kisumu County" for a sub-county, "Kisumu County" for a county. */
export function alertPlace(alert) {
  return alert.region_level === "sub_county"
    ? `${alert.region_name}, ${alert.county_name} County`
    : `${alert.region_name} County`;
}

// Plural nouns for the banner: "3 warnings".
const LEVEL_NOUNS = { warning: "warning", watch: "watch", advisory: "advisory" };
const PLURALS = { warning: "warnings", watch: "watches", advisory: "advisories" };

export const LEVEL_MEANING = {
  warning: "Act now: flooding is likely to be dangerous here soon.",
  watch: "Be ready: the chance of a large flood is well above usual.",
  advisory: "Stay informed: risk is rising or heavy rain is forecast.",
};

/** Count the alerts at each level. */
export function countByLevel(alerts) {
  const counts = { warning: 0, watch: 0, advisory: 0 };
  for (const alert of alerts) {
    if (alert.level in counts) counts[alert.level] += 1;
  }
  return counts;
}

/** "3 flood warnings, 12 watches and 1 advisory", or "" when there are none. */
export function bannerText(counts) {
  const parts = LEVEL_ORDER.filter((level) => counts[level] > 0).map(
    (level) => `${counts[level]} ${counts[level] === 1 ? LEVEL_NOUNS[level] : PLURALS[level]}`,
  );
  if (parts.length === 0) return "";
  const list = parts.length === 1 ? parts[0] : `${parts.slice(0, -1).join(", ")} and ${parts.at(-1)}`;
  return `${list.replace(/^(\d+ )/, "$1flood ")} in force`;
}

/** Alerts grouped by level, most severe first: [[level, alerts]], empty levels left out. */
export function groupByLevel(alerts) {
  return LEVEL_ORDER.map((level) => [level, alerts.filter((a) => a.level === level)]).filter(
    ([, list]) => list.length > 0,
  );
}

/** Alerts whose place or county name contains `query`, ignoring case and accents. */
export function filterAlerts(alerts, query) {
  const fold = (text) =>
    String(text ?? "")
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "")
      .toLowerCase();
  const wanted = fold(query).trim();
  if (!wanted) return alerts;
  return alerts.filter(
    (a) => fold(a.region_name).includes(wanted) || fold(a.county_name).includes(wanted),
  );
}

/**
 * Places for the sign-up picker: counties first, each followed by its
 * sub-counties, as [{ id, label, county }].
 */
export function placeOptions(names) {
  const counties = names.filter((n) => n.level === "county");
  const options = [];
  for (const county of counties) {
    options.push({ id: county.id, label: `${county.name} County`, county: county.name });
    for (const sub of names.filter((n) => n.level === "sub_county" && n.county_name === county.name)) {
      options.push({ id: sub.id, label: `${sub.name}, ${county.name}`, county: county.name });
    }
  }
  return options;
}

/**
 * The message to show for a refused request: DRF's `detail`, or the first
 * message of a form's field errors ({ email: ["..."] }, { non_field_errors:
 * [...] }, and nested ones like { regions: { regions: [...] } }).
 */
export function errorMessage(data) {
  if (!data || typeof data !== "object") return null;
  if (typeof data.detail === "string") return data.detail;
  for (const value of Object.values(data)) {
    if (typeof value === "string") return value;
    if (Array.isArray(value) && value.length) {
      return typeof value[0] === "string" ? value[0] : errorMessage(value[0]);
    }
    if (value && typeof value === "object") {
      const nested = errorMessage(value);
      if (nested) return nested;
    }
  }
  return null;
}
