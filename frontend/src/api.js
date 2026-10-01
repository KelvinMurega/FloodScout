/**
 * The only place that knows where the API lives.
 *
 * Resolution order, first match wins:
 *   1. ?api=<url> on the page URL      — for poking at another backend without a rebuild
 *   2. VITE_API_BASE_URL               — build/dev-time config, see .env.example
 *   3. http://localhost:8000/api       — the docker-compose default
 */

const FALLBACK_BASE_URL = "http://localhost:8000/api";

export function resolveBaseUrl(search = window.location.search) {
  const fromQuery = new URLSearchParams(search).get("api");
  const configured =
    fromQuery || import.meta.env.VITE_API_BASE_URL || FALLBACK_BASE_URL;
  return configured.replace(/\/+$/, "");
}

export const API_BASE_URL = resolveBaseUrl();

async function getJson(url) {
  let response;
  try {
    response = await fetch(url);
  } catch (cause) {
    // fetch() rejects on DNS/connection/CORS failures, where the status code
    // would tell us nothing. Name the URL — the usual cause is the API not
    // running, or running somewhere other than API_BASE_URL.
    throw new Error(`Could not reach ${url}. Is the API running?`, { cause });
  }
  if (!response.ok) {
    throw new Error(`${url} returned ${response.status} ${response.statusText}`);
  }
  return response.json();
}

function withParams(path, params = {}) {
  const query = new URLSearchParams(
    Object.entries(params).filter(([, value]) => value !== undefined && value !== null),
  ).toString();
  return `${API_BASE_URL}/${path}${query ? `?${query}` : ""}`;
}

/**
 * Regions as a GeoJSON FeatureCollection. With no params, the 47 counties;
 * `{ level: "sub_county", focus_region: "coast" }` gives the sub-counties
 * inside one focus region.
 */
export function fetchRegions(params) {
  return getJson(withParams("regions/", params));
}

/**
 * The latest score for every region, in a single request, narrowed by the
 * same params as fetchRegions.
 *
 * The per-region endpoint (`risk-scores/latest/?region=<id>`) would cost one
 * request per region — 47 for one map render.
 */
export function fetchLatestScores(params) {
  return getJson(withParams("risk-scores/latest-by-region/", params));
}

/** The months a seasonal outlook exists for: [{ month, issued_on }], soonest first. */
export function fetchOutlookMonths() {
  return getJson(withParams("risk-outlook/months/"));
}

/**
 * Outlook rows for one month (`params.month`, the first of the month),
 * narrowed by the same params as fetchRegions. Shaped like the latest scores.
 */
export function fetchOutlook(params) {
  return getJson(withParams("risk-outlook/", params));
}

/** The flood-prone regions the dashboard can drill into, with county ids. */
export function fetchFocusRegions() {
  return getJson(withParams("focus-regions/"));
}

/** Adjacent regions whose latest score is lower than this one's, safest first. */
export function fetchLowerRiskNeighbours(regionId) {
  return getJson(withParams("risk-scores/lower-risk-neighbours/", { region: regionId }));
}
