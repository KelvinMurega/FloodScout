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

// The signed-in user's token. Kept for this tab in sessionStorage, so a
// reload, or the trip to Google and back, does not sign anyone out; with
// "Keep me signed in" it goes in localStorage and outlives the browser. It
// was held in memory only until there was a sign-in page that leaves the
// app and comes back. Storage that is blocked just means signing in again.
const TOKEN_KEY = "floodscout.token";

function storedToken() {
  try {
    return sessionStorage.getItem(TOKEN_KEY) || localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

function remembered() {
  try {
    return localStorage.getItem(TOKEN_KEY) !== null;
  } catch {
    return false;
  }
}

/** Hold `token` (null to forget it), for this tab or, with `remember`, for good. */
function keepToken(token, remember = remembered()) {
  authToken = token;
  try {
    sessionStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(TOKEN_KEY);
    if (token) (remember ? localStorage : sessionStorage).setItem(TOKEN_KEY, token);
  } catch {
    // Blocked storage: the sign-in lasts until the page is left.
  }
}

let authToken = storedToken();

/** Whether a token from an earlier visit is waiting to be checked. */
export function hasStoredSession() {
  return authToken !== null;
}

/** Send JSON with `method`, signed in when there is a token; the reply's JSON. */
async function sendJson(method, path, body) {
  const url = `${API_BASE_URL}/${path}`;
  const headers = { "Content-Type": "application/json" };
  if (authToken) headers.Authorization = `Token ${authToken}`;

  let response;
  try {
    response = await fetch(url, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
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

function postJson(path, body = {}) {
  return sendJson("POST", path, body);
}

/**
 * Sign in with a username or an email. Resolves to { username, role,
 * is_administrator, ... }; keeps the token, for good with `remember`.
 */
export async function signIn(username, password, remember = false) {
  const { token, ...user } = await postJson("auth/token/", { username, password });
  keepToken(token, remember);
  return user;
}

/** Sign out: forget the token, and ask the API to delete it. */
export async function signOut() {
  try {
    if (authToken) await postJson("auth/logout/");
  } finally {
    keepToken(null);
  }
}

/**
 * Check a token from an earlier visit: the user it belongs to, or null after
 * forgetting it when the API no longer accepts it.
 */
export async function restoreSession() {
  if (!authToken) return null;
  try {
    return await fetchMe();
  } catch (error) {
    if (error.status === 401 || error.status === 403) keepToken(null);
    return null;
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

/* -------------------------------------------------------------- accounts */

function authedGet(path) {
  if (!authToken) {
    const error = new Error("Sign in first.");
    error.status = 401;
    return Promise.reject(error);
  }
  return getJson(`${API_BASE_URL}/${path}`, { Authorization: `Token ${authToken}` });
}

/** Create an End User account and sign in to it. Resolves like signIn. */
export async function register(body, remember = false) {
  const { token, ...user } = await postJson("auth/register/", body);
  keepToken(token, remember);
  return user;
}

/** The signed-in user's profile. */
export function fetchMe() {
  return authedGet("auth/me/");
}

/** Change the signed-in user's own details: { first_name, email, phone, ... }. */
export function updateMe(body) {
  return sendJson("PATCH", "auth/me/", body);
}

/**
 * Change the password: { current_password, new_password }. Every other
 * session is signed out; this one carries on with the new token.
 */
export async function changePassword(body) {
  const { token } = await postJson("auth/password/", body);
  keepToken(token);
}

/** What the signed-in user has done, newest first. */
export function fetchMyActivity() {
  return authedGet("auth/me/activity/");
}

/** The signed-in user's alert subscriptions. */
export function fetchMySubscriptions() {
  return authedGet("auth/me/subscriptions/");
}

/** Stop one of the signed-in user's alert subscriptions. */
export function removeMySubscription(id) {
  return sendJson("DELETE", `auth/me/subscriptions/${id}/`);
}

/** Administrator only: every account. */
export function fetchUsers() {
  return authedGet("admin/users/");
}

/** Administrator only: change an account's role or switch it off: { role?, is_active? }. */
export function updateUser(id, body) {
  return sendJson("PATCH", `admin/users/${id}/`, body);
}

/** Administrator only: what everyone has done, newest first. */
export function fetchActivity() {
  return authedGet("admin/activity/");
}

/* ------------------------------------------------- social sign-in, reset */

/** The sign-in providers set up on the server: [{ key, name }]. */
export function fetchSocialProviders() {
  return getJson(withParams("auth/social/providers/"));
}

/**
 * Where to send the browser to sign in with `provider` ("google",
 * "microsoft", "github"). Signed in, the provider is linked instead.
 * `next` is the dashboard page to come back to.
 */
export async function startSocialSignIn(provider, { next = "", remember = false } = {}) {
  const { url } = await postJson(`auth/social/${provider}/start/`, { next, remember });
  return url;
}

/** Trade the ticket the provider's return brought for a sign-in. */
export async function completeSocialSignIn(ticket) {
  const { token, ...rest } = await postJson("auth/social/complete/", { ticket });
  keepToken(token, rest.remember);
  return rest;
}

/** The signed-in user's linked providers: { has_password, providers: [...] }. */
export function fetchSocialAccounts() {
  return authedGet("auth/social/accounts/");
}

/** Unlink a provider from the signed-in user's account. */
export function unlinkSocialAccount(id) {
  return sendJson("DELETE", `auth/social/accounts/${id}/`);
}

/** Email a password-reset link, if an account uses `email`. */
export function requestPasswordReset(email) {
  return postJson("auth/password-reset/", { email });
}

/** Set a new password from a reset link: { uid, token, new_password }. */
export function confirmPasswordReset(body) {
  return postJson("auth/password-reset/confirm/", body);
}
