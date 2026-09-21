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

/** Kenya's counties as a GeoJSON FeatureCollection. */
export function fetchRegions() {
  return getJson(`${API_BASE_URL}/regions/`);
}

/**
 * The latest score for every region, in a single request.
 *
 * The per-region endpoint (`risk-scores/latest/?region=<id>`) would cost one
 * request per county — 47 for one map render.
 */
export function fetchLatestScores() {
  return getJson(`${API_BASE_URL}/risk-scores/latest-by-region/`);
}
