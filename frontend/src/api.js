/**
 * The only place that knows where the API lives.
 *
 * Resolution order, first match wins:
 *   1. ?api=<url> on the page URL      — for poking at another backend without a rebuild
 *   2. VITE_API_BASE_URL               — build/dev-time config, see .env.example
 *   3. http://localhost:8000/api       — the docker-compose default
 */

import { errorMessage } from "./alerts/format.js";

const FALLBACK_BASE_URL = "http://localhost:8000/api";

export function resolveBaseUrl(search = window.location.search) {
  const fromQuery = new URLSearchParams(search).get("api");
  const configured =
    fromQuery || import.meta.env.VITE_API_BASE_URL || FALLBACK_BASE_URL;
  return configured.replace(/\/+$/, "");
}

export const API_BASE_URL = resolveBaseUrl();

async function getJson(url, headers = {}) {
  let response;
  try {
    response = await fetch(url, { headers });
  } catch (cause) {
    // fetch() rejects on DNS/connection/CORS failures, where the status code
    // would tell us nothing. Name the URL — the usual cause is the API not
    // running, or running somewhere other than API_BASE_URL.
    throw new Error(`Could not reach ${url}. Is the API running?`, { cause });
  }
  if (!response.ok) {
    const error = new Error(`${url} returned ${response.status} ${response.statusText}`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

// The administrator's token. Held in memory only, never in localStorage or a
// cookie, so it is gone when the tab closes and a script injected into the
// page later cannot read it from storage.
let authToken = null;

async function postJson(path, body = {}) {
  const url = `${API_BASE_URL}/${path}`;
  const headers = { "Content-Type": "application/json" };
  if (authToken) headers.Authorization = `Token ${authToken}`;

  let response;
  try {
    response = await fetch(url, { method: "POST", headers, body: JSON.stringify(body) });
  } catch (cause) {
    throw new Error(`Could not reach ${url}. Is the API running?`, { cause });
  }
  const data = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    const error = new Error(errorMessage(data) || `${url} returned ${response.status}`);
    error.status = response.status;
    error.data = data;
    throw error;
  }
  return data;
}

/** Sign in. Resolves to { username, role, is_administrator }; keeps the token here. */
export async function signIn(username, password) {
  const { token, ...user } = await postJson("auth/token/", { username, password });
  authToken = token;
  return user;
}

/** Sign out: forget the token, and ask the API to delete it. */
export async function signOut() {
  try {
    if (authToken) await postJson("auth/logout/");
  } finally {
    authToken = null;
  }
}

/** Administrator only: score every region again for today. */
export function rescore() {
  return postJson("risk-scores/rescore/");
}

/**
 * Administrator only: the model's evaluation, its satellite check and the
 * pipeline's health, for the analytics page. Rejects with `status` 401 when
 * nobody is signed in.
 */
export function fetchAnalytics() {
  if (!authToken) {
    const error = new Error("Sign in as an administrator to see the analytics.");
    error.status = 401;
    return Promise.reject(error);
  }
  return getJson(`${API_BASE_URL}/admin/analytics/`, { Authorization: `Token ${authToken}` });
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
 * same params as fetchRegions. `date_after` and `date_before` set to one day
 * give the scores for that day instead.
 *
 * The per-region endpoint (`risk-scores/latest/?region=<id>`) would cost one
 * request per region — 47 for one map render.
 */
export function fetchLatestScores(params) {
  return getJson(withParams("risk-scores/latest-by-region/", params));
}

/** The days scores exist for, newest first, as YYYY-MM-DD strings. */
export function fetchScoreDates() {
  return getJson(withParams("risk-scores/dates/", { level: "county" }));
}

/** One region's scores from `after` to `before` (YYYY-MM-DD, inclusive), newest first. */
export function fetchScoreHistory(regionId, after, before) {
  return getJson(
    withParams("risk-scores/", { region: regionId, date_after: after, date_before: before }),
  );
}

/** What the model was given for one stored score, each input against its usual value. */
export function fetchScoreInputs(scoreId) {
  return getJson(withParams(`risk-scores/${scoreId}/inputs/`));
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

/**
 * Flood risk per grid square for one day: { date, cell_deg, model_version,
 * cells: [{ lon, lat, county, county_name, risk_category, times_usual,
 * rain_30d, rain_30d_ratio, ... }] }. With no date, the latest day.
 */
export function fetchRiskGrid(date) {
  return getJson(withParams("risk-grid/", { date }));
}

/** The days grid scores exist for, newest first, as YYYY-MM-DD strings. */
export function fetchGridDates() {
  return getJson(withParams("risk-grid/dates/"));
}

/**
 * What the map's themes colour by, for one day: { date, change_days,
 * regions: [{ region, times_usual, change_7d, rain_30d, rain_30d_normal,
 * rain_30d_ratio }] }. Same region params as fetchLatestScores, plus
 * `date` (YYYY-MM-DD); the newest day without it.
 */
export function fetchMapContext(params) {
  return getJson(withParams("risk-scores/context/", params));
}

/**
 * Large floods recorded in Kenya, as GeoJSON points. `{ region: id }` keeps
 * the ones whose recorded footprint reaches that region.
 */
export function fetchFloodEvents(params) {
  return getJson(withParams("flood-events/", params));
}

/** The newest forecast issue and the days it covers: { issued_on, days: [YYYY-MM-DD] }. */
export function fetchForecastDays() {
  return getJson(withParams("risk-forecast/days/"));
}

/**
 * Forecast rows, shaped like the latest scores plus `target_date`,
 * `rain_forecast_mm` and `rain_forecast_max_24h`. `{ target_date }` for one
 * day of the map, `{ region }` for one region's week; region params apply.
 */
export function fetchForecast(params) {
  return getJson(withParams("risk-forecast/", params));
}

/** Why the model gave one stored score: its parts, largest first, and a summary. */
export function fetchExplanation(scoreId) {
  return getJson(withParams(`risk-scores/${scoreId}/explain/`));
}

/** The situation in words, and the places that stand out, for a view. */
export function fetchBriefing(params) {
  return getJson(withParams("briefing/", params));
}

/* ---------------------------------------------------------------- alerts */

/** Every county and sub-county as { id, name, county_name, level }, without shapes. */
export function fetchRegionNames() {
  return getJson(withParams("regions/names/"));
}

/** Alerts, most severe first. With no params, the ones in force. */
export function fetchAlerts(params) {
  return getJson(withParams("alerts/", params));
}

/** The URL of one alert as a CAP 1.2 message, and of the Atom feed. */
export function alertCapUrl(alertId) {
  return `${API_BASE_URL}/alerts/${alertId}/cap/`;
}

export const ALERT_FEED_URL = `${API_BASE_URL}/alerts/feed/`;

/**
 * Ask for alerts: { email?, phone?, regions: [id], min_level }. Resolves to
 * { subscription, code_sent_by }; the code sent must be confirmed next.
 */
export function subscribeToAlerts(body) {
  return postJson("alerts/subscribe/", body);
}

/** Confirm a sign-up with the code sent: { subscription, code }. */
export function confirmAlerts(body) {
  return postJson("alerts/confirm/", body);
}

function adminGet(path) {
  if (!authToken) {
    const error = new Error("Sign in as an administrator.");
    error.status = 401;
    return Promise.reject(error);
  }
  return getJson(`${API_BASE_URL}/${path}`, { Authorization: `Token ${authToken}` });
}

/** Administrator only: subscriber and delivery counts, and the last run. */
export function fetchAlertStats() {
  return adminGet("alerts/stats/");
}

/** Administrator only: run the alert rules now. */
export function runAlertRules() {
  return postJson("alerts/run/");
}

/** Administrator only: issue an alert by hand: { region, level, description, instruction? }. */
export function issueAlert(body) {
  return postJson("alerts/issue/", body);
}

/** Administrator only: cancel an alert, telling those who were told. */
export function cancelAlert(alertId) {
  return postJson(`alerts/${alertId}/cancel/`);
}

/** Administrator only: send a test message: { email?, phone? }. */
export function sendTestAlert(body) {
  return postJson("alerts/test/", body);
}
