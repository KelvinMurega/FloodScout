/**
 * FloodScout dashboard — the Client Tier.
 *
 * Read-only. Two views:
 *
 *   All Kenya   the 47 counties, coloured by their latest county score.
 *   A region    one flood-prone focus region (Coast, Lake Basin, ...) drawn
 *               at sub-county level, each sub-county coloured by its own
 *               score, with the sub-counties where relocation is advised
 *               listed and outlined.
 *
 * Either view can show today's scores, the scores stored for an earlier
 * day, or the seasonal outlook for a month ahead; the period bar switches
 * between them.
 *
 * Today's and earlier days' scores can also be drawn by area: a grid of
 * squares, each scored from the rain that fell on it, so the map shows
 * where inside a county the risk is instead of one colour per county.
 *
 * The colour can show the risk category, the risk as a multiple of the usual
 * chance, its change over a week, or the rain against normal (themes.js).
 * Under the colours is a choice of basemaps and over them rivers, the
 * 100-year flood zone, past floods and satellite rain (layers.js).
 *
 * The view lives in the URL hash (#/coast), so it survives a reload and the
 * browser's back button leaves a region. #/admin is the System
 * Administrator's analytics page instead of the map (admin/admin.js). Nothing here knows how a score was
 * produced, only what the API says produced it.
 */

import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "./style.css";

import {
  API_BASE_URL,
  fetchAlerts,
  restoreSession,
  fetchBriefing,
  fetchExplanation,
  fetchFloodEvents,
  fetchSusceptibility,
  fetchKmdWarnings,
  fetchFocusRegions,
  fetchForecast,
  fetchForecastDays,
  fetchGridDates,
  fetchLatestScores,
  fetchLowerRiskNeighbours,
  fetchMapContext,
  fetchOutlook,
  fetchOutlookMonths,
  fetchRegions,
  fetchRiskGrid,
  fetchScoreDates,
  fetchScoreHistory,
  fetchScoreInputs,
} from "./api.js";
import { hideAdmin, resetAdmin, showAdmin } from "./admin/admin.js";
import { hideAlerts, showAlerts } from "./alerts/alerts.js";
import { LEVEL_LABELS, bannerText, countByLevel } from "./alerts/format.js";
import { quickAlertControl } from "./alerts/quick.js";
import { initAuth } from "./auth.js";
import { isAuthSlug } from "./auth/format.js";
import { showAuthPage } from "./auth/pages.js";
import { hideProfile, showProfile } from "./profile/profile.js";
import { daysBefore, historySeries, sparkline } from "./history.js";
import { reportFileName, scoresToCsv, sortByRisk } from "./report.js";
import {
  CATEGORY_ORDER,
  advisoryFor,
  colourFor,
  labelFor,
  prefersDark,
} from "./risk.js";
import { initLayers, overlayLegend } from "./layers.js";
import { searchRegions } from "./search.js";
import { KENYA_SLUG, alertSummary, countySlug, overviewScope, regionSummaries } from "./focus.js";
import { hideSimple, showSimple } from "./simple/simple.js";
import { chooseView, viewFor } from "./viewmode.js";
import { initNavMenu } from "./navmenu.js";
import { currentStep, stateForStep, timelineSteps } from "./timeline.js";
import {
  THEMES,
  THEME_ORDER,
  describeChange,
  describeRain,
  describeSeason,
  legendEntries,
  themeAvailable,
  themeColour,
} from "./themes.js";

const KENYA_CENTRE = [0.25, 37.9];
const KENYA_ZOOM = 6;

const DARK = prefersDark();

// The regions sit on a basemap (layers.js), so their fill lets it show
// through: rivers, towns and terrain stay readable under the colour. The
// "Colour" slider changes it, for reading a satellite image underneath.
// The view is still locked to whatever is on screen once it loads.
const FILL_OPACITY = 0.72;
const OPACITY_KEY = "floodscout.fillOpacity";
const HOVER_WEIGHT = 2.5;

// How many regions the "at a glance" panel names as the highest.
const GLANCE_TOP = 5;
// A rise in times-usual over 7 days at least this big counts as "rising".
const RISING = 0.2;

// Approximate label width per character at the label font size (px). Used to
// decide whether a region is wide enough on screen to carry its name.
const LABEL_CHAR_PX = 6.2;

// Outline for sub-counties where relocation is advised: a second, non-colour
// cue alongside the fill, so the areas stand out to colour-blind readers too.
const RELOCATE_DASH = "5 4";

// Scores older than this are flagged on screen. The scheduler refreshes
// them daily, so two days means a refresh was missed.
const STALE_AFTER_DAYS = 2;

// How far back the detail panel's history chart looks, ending on the day of
// the score on screen.
const HISTORY_DAYS = 30;
const SVG_NS = "http://www.w3.org/2000/svg";

const els = {
  status: document.getElementById("status"),
  provenance: document.getElementById("provenance"),
  regionBar: document.getElementById("region-bar"),
  periodBar: document.getElementById("period-bar"),
  timelineBar: document.getElementById("timeline-bar"),
  briefing: document.getElementById("briefing"),
  mapWrap: document.querySelector(".map-wrap"),
  alertBanner: document.getElementById("alert-banner"),
  layerBar: document.getElementById("layer-bar"),
  themeBar: document.getElementById("theme-bar"),
  glance: document.getElementById("glance"),
  legendHeading: document.getElementById("legend-heading"),
  regionIntro: document.getElementById("region-intro"),
  relocation: document.getElementById("relocation"),
  detailHeading: document.getElementById("detail-heading"),
  detail: document.getElementById("detail"),
  legend: document.getElementById("legend"),
  tableHeading: document.getElementById("table-heading"),
  tableHead: document.getElementById("table-head"),
  tableBody: document.getElementById("table-body"),
  searchInput: document.getElementById("search-input"),
  searchResults: document.getElementById("search-results"),
  searchEmpty: document.getElementById("search-empty"),
  printReport: document.getElementById("print-report"),
  downloadCsv: document.getElementById("download-csv"),
  viewToggle: document.getElementById("view-toggle"),
  report: document.getElementById("report"),
  admin: document.getElementById("admin"),
};

const state = {
  // Loaded once.
  counties: null,
  focusRegions: [],
  // Months a seasonal outlook exists for: [{ month, issued_on }].
  outlookMonths: [],
  // null is today's scores; otherwise the outlook month, as the API names it.
  period: null,
  // Days scores are stored for, newest first, as YYYY-MM-DD.
  scoreDates: [],
  // null is the latest scores; otherwise one of scoreDates. Never set
  // together with an outlook period.
  scoreDate: null,
  // A day of the newest 7-day forecast, or null. Never set with the above.
  forecastDay: null,
  // The newest forecast: the day it was issued and the days it covers.
  forecastIssued: null,
  forecastDays: [],
  // The Play button's timer while the timeline animates.
  playTimer: null,
  // The flood alerts in force, most severe first, and by region id.
  alerts: [],
  alertsByRegion: new Map(),
  // County scores per period and date, fetched on first use.
  countyCache: new Map(),
  // "region" colours each county or sub-county as one; "grid" colours the
  // squares of the area grid, each by its own score.
  mapBy: "region",
  // Days grid scores are stored for, newest first. Empty hides the choice.
  gridDates: [],
  // Grid scores per date ("" for the latest), fetched on first use.
  gridCache: new Map(),
  // Whether the view on screen is drawn by area.
  gridOn: false,
  // What the colour shows: one of THEME_ORDER (themes.js).
  theme: "season",
  // How strongly the colour covers the basemap, 0.15 to 1.
  fillOpacity: readOpacity(),
  // Context rows (change, rain against normal) per view, fetched on first use.
  contextCache: new Map(),
  // The context row for each region on screen.
  contextByRegion: new Map(),
  // What the legend counts: [{ score, context }], one per region or square.
  legendRows: [],
  focusByCountyId: new Map(),
  // Per region, fetched on first open.
  regionCache: new Map(),
  // Sub-county shapes per region. They do not change with the period, so
  // they are kept apart from the scores and shared with the search box.
  shapeCache: new Map(),
  // Sub-counties the search box can offer, loaded when it is first used.
  searchIndex: null,
  subCountyEntries: [],
  searchMatches: [],
  searchActive: -1,
  // A region chosen in the search box that the next view should select.
  pendingSelect: null,
  // The current view: a focus region, or else the overview's scope, "focus"
  // (the focus counties, the front door) or "kenya" (every county).
  focus: null,
  scope: "focus",
  // The score rows on screen, as the API returned them: what the printed
  // report and the CSV export hold.
  viewScores: [],
  scoresByRegion: new Map(),
  layersByRegion: new Map(),
  labelsByRegion: new Map(),
  rowsByRegion: new Map(),
  selectedRegionId: null,
  viewLayers: [],
  // Who is signed in, as the API described them; null when nobody is.
  user: null,
  // Whether the analytics page is on screen instead of the map.
  adminOpen: false,
};

/* ---------------------------------------------------------------- utils */

function readOpacity() {
  try {
    const saved = Number(localStorage.getItem(OPACITY_KEY));
    return saved >= 0.15 && saved <= 1 ? saved : FILL_OPACITY;
  } catch {
    return FILL_OPACITY;
  }
}

function setStatus(message, isError = false) {
  if (!message) {
    els.status.hidden = true;
    return;
  }
  els.status.hidden = false;
  els.status.textContent = message;
  els.status.classList.toggle("status--error", isError);
}

/** drf-gis puts the model pk on the Feature; fall back to properties. */
function regionIdOf(feature) {
  return feature.id ?? feature.properties?.id ?? null;
}

function nameOf(feature) {
  return feature.properties?.name ?? feature.properties?.county_name ?? "";
}

function formatValue(value) {
  return typeof value === "number" ? value.toFixed(3) : "—";
}

function formatDate(isoString) {
  if (!isoString) return "—";
  const parsed = new Date(isoString);
  if (Number.isNaN(parsed.valueOf())) return isoString;
  return parsed.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function swatch(category) {
  const span = el("span", "legend__swatch");
  span.style.background = colourFor(category, DARK);
  return span;
}

/**
 * A score as a multiple of the usual chance of a large flood. The model
 * ranks regions reliably but its probabilities run high, so the multiple is
 * shown and the percentage is not.
 */
function formatTimesUsual(times) {
  if (typeof times !== "number") return "—";
  if (times >= 0.9 && times <= 1.1) return "About the usual chance";
  return `About ${times.toFixed(1)} times the usual chance`;
}

function formatMonth(isoDate) {
  const parsed = new Date(`${isoDate}T00:00:00`);
  if (Number.isNaN(parsed.valueOf())) return isoDate;
  return parsed.toLocaleDateString(undefined, { year: "numeric", month: "long" });
}

function isRegionView() {
  return state.focus !== null;
}

function isFocusOverview() {
  return !isRegionView() && state.scope === "focus";
}

function isOutlook() {
  return state.period !== null;
}

function isPastDate() {
  return state.scoreDate !== null;
}

function isForecast() {
  return state.forecastDay !== null;
}

/**
 * Whether the scores on screen may carry relocation advice. Only the latest
 * ones do: an outlook is a chance months ahead, a forecast day is rain that
 * has not fallen, and an earlier day is over.
 */
function givesAdvice() {
  return !isOutlook() && !isPastDate() && !isForecast();
}

/** Names what is on screen, for caches and for spotting a view that changed. */
function viewKey() {
  return `${state.period ?? "today"}|${state.scoreDate ?? "latest"}|${state.forecastDay ?? "-"}`;
}

/**
 * Scores for the current period: the latest, one earlier day's, a forecast
 * day's, or an outlook month's. A forecast row is given the target day as
 * its `prediction_date`, so it is dated like a score everywhere it is drawn.
 */
function fetchScores(params) {
  if (isOutlook()) return fetchOutlook({ ...params, month: state.period });
  if (isForecast()) {
    return fetchForecast({ ...params, target_date: state.forecastDay }).then((rows) =>
      rows.map((row) => ({ ...row, prediction_date: `${row.target_date}T00:00:00` })),
    );
  }
  if (isPastDate()) {
    return fetchLatestScores({
      ...params,
      date_after: state.scoreDate,
      date_before: state.scoreDate,
    });
  }
  return fetchLatestScores(params);
}

/* ------------------------------------------------------------------ map */

const map = L.map("map", {
  center: KENYA_CENTRE,
  zoom: KENYA_ZOOM,
  zoomControl: true,
  // Fractional zoom lets fitBounds fill the panel instead of snapping a whole
  // level smaller and leaving the country floating in empty space.
  zoomSnap: 0.25,
  zoomDelta: 0.5,
  maxZoom: 13,
  // Basemap tiles appear at once instead of fading in. A fade is driven by
  // animation frames, which a print preview or a headless screenshot may
  // never run, leaving the tiles invisible.
  fadeAnimation: false,
});

map.attributionControl.addAttribution(
  'Boundaries: <a href="https://www.geoboundaries.org/">geoBoundaries</a>',
);

// Panes, bottom to top, all above the basemap tiles. The country outline
// sits under the region fills, which half cover it, so it reads as an edge
// around Kenya rather than a line through every border.
map.createPane("context").style.zIndex = 340;
map.createPane("outline").style.zIndex = 350;
// Above the region fills, so the squares take the pointer and their tooltips.
map.createPane("grid").style.zIndex = 410;
map.createPane("countyLines").style.zIndex = 420;
map.createPane("labels").style.zIndex = 450;
map.getPane("countyLines").style.pointerEvents = "none";
map.getPane("labels").style.pointerEvents = "none";

// Hundreds of squares draw far faster on one canvas than as SVG paths.
const GRID_RENDERER = L.canvas({ pane: "grid", padding: 0.2 });

// Basemaps and the context layers over the risk (layers.js). Past floods
// are fetched once, the first time the layer is turned on.
let floodEvents = null;
let kmdWarnings = null;
const mapLayers = initLayers(map, {
  dark: DARK,
  loadFloodEvents: () => {
    floodEvents ??= fetchFloodEvents().catch((error) => {
      floodEvents = null;
      throw error;
    });
    return floodEvents;
  },
  // KMD's warnings, fetched once per page load when the layer is turned on.
  loadKmdWarnings: () => {
    kmdWarnings ??= fetchKmdWarnings().catch((error) => {
      kmdWarnings = null;
      throw error;
    });
    return kmdWarnings;
  },
  onChange: () => renderLegend(),
});

/** The theme on screen: the one chosen, or "usual" where it means nothing. */
function currentTheme() {
  const view = { outlook: isOutlook(), grid: state.gridOn, forecast: isForecast() };
  return themeAvailable(state.theme, view) ? state.theme : "usual";
}

function regionColour(regionId) {
  return themeColour(
    currentTheme(),
    state.scoresByRegion.get(regionId),
    state.contextByRegion.get(regionId),
    DARK,
  );
}

/**
 * A point to hang a region's name on: the area-weighted centroid of its
 * largest polygon. Leaflet's own getCenter() uses the *first* part of a
 * MultiPolygon, which for Lamu is an offshore island.
 */
function labelPoint(geometry) {
  const polygons =
    geometry.type === "MultiPolygon" ? geometry.coordinates : [geometry.coordinates];

  let best = null;
  let bestArea = -1;
  for (const polygon of polygons) {
    const ring = polygon[0];
    let area = 0;
    let cx = 0;
    let cy = 0;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [x0, y0] = ring[j];
      const [x1, y1] = ring[i];
      const cross = x0 * y1 - x1 * y0;
      area += cross;
      cx += (x0 + x1) * cross;
      cy += (y0 + y1) * cross;
    }
    if (Math.abs(area) > bestArea) {
      bestArea = Math.abs(area);
      best = area === 0 ? ring[0] : [cx / (3 * area), cy / (3 * area)];
    }
  }
  return best ? L.latLng(best[1], best[0]) : null;
}

/** Only name a region when it is wide enough on screen to hold the name. */
function updateLabelVisibility() {
  for (const [regionId, marker] of state.labelsByRegion) {
    const layer = state.layersByRegion.get(regionId);
    const bounds = layer.getBounds();
    const west = map.latLngToContainerPoint(bounds.getSouthWest());
    const east = map.latLngToContainerPoint(bounds.getNorthEast());
    const widthPx = Math.abs(east.x - west.x);
    const fits = widthPx >= nameOf(layer.feature).length * LABEL_CHAR_PX + 10;
    marker.getElement()?.classList.toggle("county-label--hidden", !fits);
  }
}

let refit = null;

/** How far the map can be dragged past the fitted view, as a share of it. */
const PAN_MARGIN = 0.5;

/** Fit `bounds` to the panel and stop the view wandering far away from it. */
function lockTo(bounds) {
  refit = () => {
    map.setMaxBounds(null);
    // Fit first, then make the fitted zoom the furthest out the map can go.
    // Setting the floor first is wrong twice over: Leaflet starts an animated
    // zoom when the view is further out than a new floor, which the fit then
    // cuts off half-way, and a floor worked out separately can land one step
    // closer than the fit and crop the shape.
    map.setMinZoom(0);
    map.fitBounds(bounds, { padding: [16, 16], animate: false });
    map.setMinZoom(map.getZoom());
    // The limit is measured from the fitted view, not from `bounds`: a limit
    // smaller than the view pins the map in place and it cannot be dragged.
    map.setMaxBounds(map.getBounds().pad(PAN_MARGIN));
  };
  refit();
  map.options.maxBoundsViscosity = 1;
}

map.on("resize", () => refit?.());
// Leaflet only notices the window resizing. The bars above the map appear
// once the data is in and take height from it, which would leave the fitted
// view off-centre, so watch the map's own box as well.
new ResizeObserver(() => map.invalidateSize({ animate: false })).observe(map.getContainer());
map.on("zoomend", updateLabelVisibility);

function clearView() {
  clearHotspots();
  clearAlertBadges();
  for (const layer of state.viewLayers) layer.remove();
  state.viewLayers = [];
  state.layersByRegion.clear();
  state.labelsByRegion.clear();
  state.selectedRegionId = null;
}

function addToView(layer) {
  layer.addTo(map);
  state.viewLayers.push(layer);
  return layer;
}

function advisoryOf(regionId) {
  return state.scoresByRegion.get(regionId)?.relocation_advisory ?? "none";
}

function styleFor(feature) {
  const regionId = regionIdOf(feature);
  return {
    fillColor: regionColour(regionId),
    // Under the grid the regions keep their outlines but give up their fill.
    fillOpacity: state.gridOn ? 0 : state.fillOpacity,
    ...restingStroke(regionId),
  };
}

/**
 * The resting stroke for a region: focus ring if selected, a dashed ring if
 * relocation is advised there, otherwise a hairline in the surface colour
 * that keeps neighbouring fills from bleeding into one another.
 */
function restingStroke(regionId) {
  if (regionId === state.selectedRegionId) {
    return { weight: 3, color: cssVar("--polygon-stroke-focus"), dashArray: null };
  }
  if (isRegionView() && givesAdvice() && advisoryOf(regionId) === "relocate") {
    return { weight: 2, color: cssVar("--polygon-stroke-focus"), dashArray: RELOCATE_DASH };
  }
  return { weight: 1, color: cssVar("--polygon-stroke"), dashArray: null };
}

/** The interactive, coloured layer for one set of regions, with labels. */
function drawChoropleth(features) {
  const layer = L.geoJSON(
    { type: "FeatureCollection", features },
    {
      style: styleFor,
      onEachFeature: (feature, featureLayer) => {
        const regionId = regionIdOf(feature);
        const name = nameOf(feature);
        state.layersByRegion.set(regionId, featureLayer);

        // A function, so the card reads the theme and context on screen now.
        featureLayer.bindTooltip(() => regionTooltip(regionId, name), {
          sticky: true,
          className: "map-card",
        });
        featureLayer.on({
          click: () => select(regionId),
          mouseover: () => {
            featureLayer.setStyle({
              weight: HOVER_WEIGHT,
              color: cssVar("--polygon-stroke-focus"),
              dashArray: null,
            });
            featureLayer.bringToFront();
          },
          mouseout: () => {
            featureLayer.setStyle(restingStroke(regionId));
            state.layersByRegion.get(state.selectedRegionId)?.bringToFront();
          },
        });

        const point = labelPoint(feature.geometry);
        if (point && name) {
          const label = el("span", null, name);
          state.labelsByRegion.set(
            regionId,
            L.marker(point, {
              pane: "labels",
              interactive: false,
              keyboard: false,
              icon: L.divIcon({ className: "county-label", html: label, iconSize: null }),
            }),
          );
        }
      },
    },
  );
  addToView(layer);
  for (const marker of state.labelsByRegion.values()) addToView(marker);
  return layer;
}

/**
 * A region's hover card: its name, its risk in words, and the two things
 * that say where it is heading: the week's change and the rain behind it.
 */
function regionTooltip(regionId, name) {
  const score = state.scoresByRegion.get(regionId);
  const context = state.contextByRegion.get(regionId);
  const box = el("div", "grid-tooltip");
  const title = el("strong", null, name);
  const badge = el("span", "map-card__badge");
  badge.append(swatch(score?.risk_category), labelFor(score?.risk_category));
  box.append(title, badge);
  if (score) box.append(el("div", null, formatTimesUsual(context?.times_usual ?? score.times_usual)));
  const season = describeSeason(context?.times_season ?? score?.times_season, monthOf(score));
  if (season) box.append(el("div", null, season));
  const change = describeChange(context?.change_7d);
  if (change) box.append(el("div", "map-card__muted", change));
  const rain = describeRain(context);
  if (rain) box.append(el("div", "map-card__muted", rain));
  if (isOutlook() && typeof score?.rain_vs_normal === "number") {
    box.append(
      el("div", "map-card__muted", `Forecast rain ${score.rain_vs_normal.toFixed(1)} times normal`),
    );
  }
  box.append(el("div", "map-card__hint", "Click for details"));
  return box;
}

/** "October": the month a score or forecast row is for. */
function monthOf(score) {
  const iso = score?.target_date ?? score?.prediction_date;
  if (!iso) return null;
  const date = new Date(iso.length === 10 ? `${iso}T00:00:00` : iso);
  return Number.isNaN(date.valueOf()) ? null : date.toLocaleDateString(undefined, { month: "long" });
}

/** A grid square's tooltip: its county, its risk, and the rain behind it. */
function gridTooltip(cell, date) {
  const box = el("div", "grid-tooltip");
  box.append(
    el("strong", null, `${cell.county_name} area — ${labelFor(cell.risk_category)}`),
    el("div", null, formatTimesUsual(cell.times_usual)),
  );
  const season = describeSeason(cell.times_season, monthOf({ prediction_date: date }));
  if (season) box.append(el("div", null, season));
  if (typeof cell.rain_30d === "number") {
    const usual =
      typeof cell.rain_30d_ratio === "number"
        ? ` (${Math.round(cell.rain_30d_ratio * 100)}% of usual)`
        : "";
    box.append(el("div", null, `Rain in the last 30 days: ${Math.round(cell.rain_30d)} mm${usual}`));
  }
  return box;
}

/**
 * The area grid: one square per cell, coloured by its own score. Only the
 * cells of `countyIds` when given. Clicking a square opens its county when
 * the county is on the map as a region.
 */
function drawGrid(grid, countyIds = null) {
  const half = grid.cell_deg / 2;
  const cells = countyIds ? grid.cells.filter((c) => countyIds.has(c.county)) : grid.cells;
  const group = L.layerGroup();
  for (const cell of cells) {
    const square = L.rectangle(
      [
        [cell.lat - half, cell.lon - half],
        [cell.lat + half, cell.lon + half],
      ],
      {
        renderer: GRID_RENDERER,
        pane: "grid",
        stroke: false,
        fillColor: themeColour(currentTheme(), cell, null, DARK),
        fillOpacity: state.fillOpacity,
      },
    );
    square.bindTooltip(gridTooltip(cell, grid.date), { sticky: true });
    square.on("click", () => {
      if (state.layersByRegion.has(cell.county)) select(cell.county);
    });
    group.addLayer(square);
  }
  addToView(group);
  return cells;
}

/** Region borders drawn over the grid, so each square reads against its region. */
function drawRegionLines(features) {
  addToView(
    L.geoJSON(
      { type: "FeatureCollection", features },
      {
        pane: "countyLines",
        interactive: false,
        style: { color: cssVar("--county-line"), weight: 1, fill: false },
      },
    ),
  );
}

function drawOutline(features) {
  addToView(
    L.geoJSON(
      { type: "FeatureCollection", features },
      {
        pane: "outline",
        interactive: false,
        style: { color: cssVar("--country-outline"), weight: 2.5, fill: false },
      },
    ),
  );
}

/* ------------------------------------------------------------ rendering */

/**
 * Name the scorer on screen. Read from the data rather than hardcoded, so it
 * stays honest when the real model lands and starts stamping a new version.
 */
function renderProvenance(scores) {
  const versions = [...new Set(scores.map((s) => s.model_version).filter(Boolean))];
  if (versions.length === 0) {
    els.provenance.hidden = true;
    return;
  }

  const looksLikePlaceholder = versions.every((v) => /stub|heuristic|placeholder/i.test(v));
  // The newest date any score on screen is for: what "current" means here.
  const latest = scores.map((s) => s.prediction_date).filter(Boolean).sort().at(-1);
  els.provenance.hidden = false;
  els.provenance.replaceChildren();
  els.provenance.classList.toggle("provenance--placeholder", looksLikePlaceholder && !isOutlook());

  const label = el("strong", null, versions.join(", "));
  if (isOutlook()) {
    const issued = state.outlookMonths.find((m) => m.month === state.period)?.issued_on;
    els.provenance.append(
      `Outlook for ${formatMonth(state.period)} from `,
      label,
      `, driven by a seasonal rainfall forecast issued ${formatDate(issued)}. ` +
        "A chance of a large flood, not a prediction of one, and less certain than today's scores.",
    );
  } else if (looksLikePlaceholder) {
    els.provenance.append(
      "Scores produced by ",
      label,
      " — a documented placeholder, not a trained model. Not for operational use.",
    );
  } else if (isForecast()) {
    els.provenance.append(
      `Forecast for ${formatDate(latest)}, issued ${formatDate(state.forecastIssued)}: `,
      label,
      " run on the rain that has fallen and the rain forecast for the days between. " +
        "Less certain than today's scores, and never grounds for relocation advice.",
    );
  } else if (isPastDate()) {
    els.provenance.append(
      `Scores for ${formatDate(latest)}, produced by `,
      label,
      ". An earlier day, shown for comparison: relocation advice is given from the latest scores only.",
    );
  } else {
    els.provenance.append(`Scores for ${formatDate(latest)}, produced by `, label, ".");
    const age = daysSince(latest);
    if (age > STALE_AFTER_DAYS) {
      els.provenance.append(` These are ${age} days old: the daily refresh has not run.`);
    }
  }
  if (state.gridOn) {
    els.provenance.append(
      " Coloured by area: each square is scored from the rain that fell on it. The model " +
        "learned from floods recorded per county, so a square shows where the dangerous " +
        "rain fell, not the outline of a flood.",
    );
  }
}

/** Whole days from an ISO date to now; 0 when the date is missing or ahead. */
function daysSince(isoString) {
  const then = new Date(isoString).valueOf();
  if (Number.isNaN(then)) return 0;
  return Math.max(0, Math.floor((Date.now() - then) / 86_400_000));
}

function renderRegionBar() {
  const group = el("div", "segmented");
  els.regionBar.replaceChildren(el("span", "layer-bar__label", "Region"), group);
  const current = state.focus?.slug ?? (state.scope === "kenya" ? KENYA_SLUG : "");

  const options = [
    { slug: "", name: "Focus regions" },
    ...state.focusRegions,
    { slug: KENYA_SLUG, name: "All Kenya" },
  ];
  for (const option of options) {
    const button = el("button", "region-bar__button", option.name);
    button.type = "button";
    button.setAttribute("aria-pressed", String(option.slug === current));
    button.addEventListener("click", () => {
      location.hash = option.slug ? `#/${option.slug}` : "";
    });
    group.append(button);
  }
}

/** Show the scores stored for `isoDate`, or the latest when it is empty. */
function chooseScoreDate(isoDate, note) {
  if (isoDate && !state.scoreDates.includes(isoDate)) {
    note.textContent = `No scores are stored for ${formatDate(`${isoDate}T00:00:00`)}.`;
    return;
  }
  state.period = null;
  state.forecastDay = null;
  // The newest day is what "Today" already shows.
  state.scoreDate = isoDate && isoDate !== state.scoreDates[0] ? isoDate : null;
  route();
}

/** A date input for the days scores are stored for (FR-05). */
function scoreDatePicker() {
  const wrap = el("label", "period-bar__date", "Earlier day ");
  const input = el("input", "period-bar__input");
  const note = el("span", "period-bar__note");
  note.setAttribute("role", "status");
  input.type = "date";
  input.min = state.scoreDates.at(-1);
  input.max = state.scoreDates[0];
  input.value = state.scoreDate ?? "";
  input.addEventListener("change", () => chooseScoreDate(input.value, note));
  wrap.append(input);
  return [wrap, note];
}

function renderPeriodBar() {
  // A single stored day leaves nothing to pick, so the bar needs an outlook
  // or a second day of scores to be worth showing.
  const hasEarlierDays = state.scoreDates.length > 1;
  els.periodBar.hidden = state.outlookMonths.length === 0 && !hasEarlierDays;
  const group = el("div", "segmented");
  els.periodBar.replaceChildren(el("span", "layer-bar__label", "When"), group);

  const options = [
    { period: null, name: "Today" },
    ...state.outlookMonths.map((m) => ({
      period: m.month,
      name: `${formatMonth(m.month)} outlook`,
    })),
  ];
  for (const option of options) {
    const button = el("button", "region-bar__button", option.name);
    button.type = "button";
    button.setAttribute(
      "aria-pressed",
      String(option.period === state.period && !isPastDate() && !isForecast()),
    );
    button.addEventListener("click", () => {
      stopPlaying();
      state.period = option.period;
      state.scoreDate = null;
      state.forecastDay = null;
      route();
    });
    group.append(button);
    // The week ahead sits next to today, before the months ahead.
    if (option.period === null && state.forecastDays.length > 0) {
      const ahead = el("button", "region-bar__button", "Next 7 days");
      ahead.type = "button";
      ahead.title = "The flood risk forecast for each of the coming days";
      ahead.setAttribute("aria-pressed", String(isForecast()));
      ahead.addEventListener("click", () => {
        stopPlaying();
        state.period = null;
        state.scoreDate = null;
        state.forecastDay = state.forecastDays[0];
        route();
      });
      group.append(ahead);
    }
  }
  if (hasEarlierDays) els.periodBar.append(...scoreDatePicker());
}

/* ------------------------------------------------------------- timeline */

const PLAY_STEP_MS = 1400;

function steps() {
  return timelineSteps(state.scoreDates, state.forecastDays);
}

function goToStep(step) {
  const next = stateForStep(step);
  state.period = null;
  state.scoreDate = next.scoreDate;
  state.forecastDay = next.forecastDay;
  return route();
}

function stopPlaying() {
  if (state.playTimer) clearTimeout(state.playTimer);
  state.playTimer = null;
}

/** Play: step the map a day at a time to the end of the forecast. */
function play() {
  const all = steps();
  let index = currentStep(all, state);
  // From the end, start again at the beginning.
  if (index < 0 || index >= all.length - 1) index = -1;
  const advance = async () => {
    index += 1;
    await goToStep(all[index]);
    state.playTimer = index < all.length - 1 ? setTimeout(advance, PLAY_STEP_MS) : null;
    renderTimeline();
  };
  state.playTimer = setTimeout(advance, 0);
  renderTimeline();
}

function stepLabel(step) {
  if (!step) return "";
  const day = formatDate(`${step.date}T00:00:00`);
  if (step.kind === "today") return `${day} · latest scores`;
  if (step.kind === "forecast") return `${day} · forecast`;
  return `${day} · observed`;
}

/**
 * A slider over the stored days and the forecast days, with Play. Hidden
 * for an outlook, which is a month, not a day.
 */
function renderTimeline() {
  const all = steps();
  const index = currentStep(all, { ...state, outlook: isOutlook() });
  els.timelineBar.hidden = all.length < 2 || isOutlook();
  if (els.timelineBar.hidden) return;

  const playing = state.playTimer !== null;
  const playButton = el("button", "region-bar__button timeline__play", playing ? "Pause" : "Play");
  playButton.type = "button";
  playButton.setAttribute("aria-pressed", String(playing));
  playButton.title = "Show each day in turn, through the forecast";
  playButton.addEventListener("click", () => {
    if (playing) {
      stopPlaying();
      renderTimeline();
    } else {
      play();
    }
  });

  const label = el("span", "timeline__label", stepLabel(all[index]));
  label.classList.toggle("timeline__label--forecast", all[index]?.kind === "forecast");

  const slider = el("input", "timeline__range");
  slider.type = "range";
  slider.min = "0";
  slider.max = String(all.length - 1);
  slider.value = String(Math.max(index, 0));
  slider.setAttribute("aria-label", "Day shown on the map");
  slider.setAttribute("aria-valuetext", stepLabel(all[index]));
  const firstForecast = all.findIndex((step) => step.kind === "forecast");
  if (firstForecast > 0) {
    // Shade the forecast end of the track.
    slider.style.setProperty("--forecast-from", `${(firstForecast / (all.length - 1)) * 100}%`);
  }
  slider.addEventListener("input", () => {
    stopPlaying();
    label.textContent = stepLabel(all[Number(slider.value)]);
  });
  slider.addEventListener("change", () => goToStep(all[Number(slider.value)]));

  const ends = el("span", "timeline__ends");
  ends.append(
    el("span", null, formatDate(`${all[0].date}T00:00:00`)),
    el("span", null, formatDate(`${all.at(-1).date}T00:00:00`)),
  );
  const track = el("div", "timeline__track");
  track.append(slider, ends);
  els.timelineBar.replaceChildren(el("span", "layer-bar__label", "Day"), playButton, track, label);
}

function hasGrid() {
  return state.gridDates.length > 0 && !isOutlook() && !isForecast();
}

/** The choice between colouring by region and by area, when there is a grid. */
function renderLayerBar() {
  els.layerBar.hidden = !hasGrid();
  els.layerBar.replaceChildren();
  if (!hasGrid()) return;

  const group = el("div", "segmented");
  els.layerBar.append(el("span", "layer-bar__label", "Colour by"), group);
  const options = [
    { mapBy: "region", name: isRegionView() ? "Sub-county" : "County" },
    { mapBy: "grid", name: "Area" },
  ];
  for (const option of options) {
    const button = el("button", "region-bar__button", option.name);
    button.type = "button";
    button.setAttribute("aria-pressed", String(option.mapBy === state.mapBy));
    button.addEventListener("click", () => {
      state.mapBy = option.mapBy;
      route();
    });
    group.append(button);
  }
}

/**
 * The context rows (change, rain against normal) for the regions of a view,
 * keyed by region id. An outlook has none: its rows carry their own forecast
 * rain. A failure leaves the map coloured by what the scores themselves say.
 */
async function loadContext(params) {
  if (isOutlook() || isForecast()) return new Map();
  const key = `${viewKey()}|${JSON.stringify(params)}`;
  if (!state.contextCache.has(key)) {
    state.contextCache.set(
      key,
      fetchMapContext({ ...params, date: state.scoreDate ?? undefined })
        .then((body) => new Map(body.regions.map((row) => [row.region, row])))
        .catch((error) => {
          console.error(error);
          state.contextCache.delete(key);
          return new Map();
        }),
    );
  }
  return state.contextCache.get(key);
}

/** A "Forecast" badge on the map while a forecast day is on screen. */
function markForecast() {
  els.mapWrap.classList.toggle("map-wrap--forecast", isForecast());
  els.mapWrap.dataset.forecast = isForecast()
    ? `Forecast · ${formatDate(`${state.forecastDay}T00:00:00`)}`
    : "";
}

/** Point the NASA satellite layers at the day on screen. */
function syncLayerDate() {
  mapLayers.setDate(isOutlook() ? null : viewDate() || null);
}

/** Grid scores for the day on screen, or null when the view is not by area. */
function loadGrid() {
  if (state.mapBy !== "grid" || !hasGrid()) return Promise.resolve(null);
  const date = state.scoreDate ?? "";
  if (!state.gridCache.has(date)) {
    state.gridCache.set(
      date,
      fetchRiskGrid(date || undefined).catch((error) => {
        state.gridCache.delete(date); // let a retry refetch
        throw error;
      }),
    );
  }
  return state.gridCache.get(date);
}

/** "Show": what the colour on the map means (themes.js). */
function renderThemeBar() {
  const group = el("div", "segmented");
  els.themeBar.replaceChildren(el("span", "layer-bar__label", "Show"), group);
  const view = { outlook: isOutlook(), grid: state.gridOn, forecast: isForecast() };
  for (const theme of THEME_ORDER) {
    if (!themeAvailable(theme, view)) continue;
    const button = el("button", "region-bar__button", THEMES[theme].name);
    button.type = "button";
    button.title = THEMES[theme].hint;
    button.setAttribute("aria-pressed", String(theme === currentTheme()));
    button.addEventListener("click", () => {
      state.theme = theme;
      recolour();
    });
    group.append(button);
  }
  els.themeBar.append(opacitySlider());
}

/** The "Colour" slider: how much of the basemap shows through the risk. */
function opacitySlider() {
  const wrap = el("label", "theme-bar__opacity", "Colour ");
  const input = el("input", "theme-bar__range");
  input.type = "range";
  input.min = "15";
  input.max = "100";
  input.step = "5";
  input.value = String(Math.round(state.fillOpacity * 100));
  input.title = "How strongly the colour covers the map underneath";
  input.addEventListener("input", () => {
    state.fillOpacity = Number(input.value) / 100;
    try {
      localStorage.setItem(OPACITY_KEY, String(state.fillOpacity));
    } catch {
      // Blocked storage: the setting lasts this visit only.
    }
    for (const layer of state.viewLayers) {
      layer.eachLayer?.((child) => {
        if (child.options?.fillOpacity > 0 && child.options.pane !== "context") {
          child.setStyle({ fillOpacity: state.fillOpacity });
        }
      });
    }
  });
  wrap.append(input);
  return wrap;
}

/** Repaint the view on screen for a new theme, without fetching anything. */
function recolour() {
  for (const [regionId, layer] of state.layersByRegion) {
    layer.setStyle({ fillColor: regionColour(regionId) });
  }
  if (state.gridOn) route();
  renderThemeBar();
  renderLegend();
}

/** The rows the legend counts: the regions on screen, or the grid's squares. */
function setLegendRows(scores, cells) {
  state.legendRows = cells
    ? cells.map((cell) => ({ score: cell, context: null }))
    : scores.map((score) => ({ score, context: state.contextByRegion.get(score.region) }));
}

function renderLegend() {
  const theme = currentTheme();
  els.legendHeading.replaceChildren(
    THEMES[theme].name,
    el("span", "panel__hint", THEMES[theme].hint),
  );
  els.legend.replaceChildren();
  for (const entry of legendEntries(theme, state.legendRows, DARK)) {
    const item = el("li", "legend__item");
    const mark = el("span", "legend__swatch");
    mark.style.background = entry.colour;
    item.append(
      mark,
      el("span", "legend__label", entry.label),
      el("span", "legend__count", `${entry.count}`),
    );
    els.legend.append(item);
  }
  if (theme !== "risk") {
    els.legend.append(
      el(
        "li",
        "legend__note",
        "The risk level is still the official reading: it is in the hover card, the table and the details.",
      ),
    );
  }

  for (const line of overlayLegend(mapLayers.active)) {
    const item = el("li", "legend__item legend__item--overlay");
    const mark = el("span", `legend__swatch legend__swatch--${line.kind}`);
    mark.style.background = line.colour;
    item.append(mark, el("span", "legend__label", line.label));
    els.legend.append(item);
  }

  if (state.gridOn) {
    els.legend.append(el("li", "legend__note", "Counts are squares of the area grid."));
  }

  if (isRegionView() && givesAdvice()) {
    const item = el("li", "legend__item");
    item.append(
      el("span", "legend__swatch legend__swatch--dashed"),
      el("span", "legend__label", "Dashed outline: relocation advised"),
    );
    els.legend.append(item);
  }
}

/**
 * The view on screen in a few lines: how many places are at each level,
 * how many are rising, and the highest few, each one click from its details.
 */
function renderGlance(scores) {
  if (scores.length === 0) {
    els.glance.hidden = true;
    return;
  }
  els.glance.hidden = false;
  const where = viewName();
  const heading = el("h2", "panel__heading", "At a glance");
  heading.append(el("span", "panel__hint", where));

  const counts = el("div", "glance__counts");
  for (const category of [...CATEGORY_ORDER].reverse()) {
    const n = scores.filter((s) => s.risk_category === category).length;
    const item = el("div", "glance__count");
    item.append(el("span", "glance__number", `${n}`), swatch(category), labelFor(category));
    counts.append(item);
  }

  const lines = [];
  const contexts = scores.map((s) => state.contextByRegion.get(s.region)).filter(Boolean);
  const rising = contexts.filter((c) => (c.change_7d ?? 0) >= RISING).length;
  const falling = contexts.filter((c) => (c.change_7d ?? 0) <= -RISING).length;
  if (contexts.some((c) => typeof c.change_7d === "number")) {
    lines.push(`${rising} rising and ${falling} falling over the last 7 days.`);
  }
  const wet = contexts.filter((c) => (c.rain_30d_ratio ?? 0) >= 1.5).length;
  if (contexts.some((c) => typeof c.rain_30d_ratio === "number")) {
    lines.push(`${wet} had at least one and a half times their normal rain in the last 30 days.`);
  }

  const valueOf = (s) => state.contextByRegion.get(s.region)?.times_usual ?? s.times_usual ?? s.predicted_value;
  const top = [...scores].sort((a, b) => valueOf(b) - valueOf(a)).slice(0, GLANCE_TOP);
  const list = el("ol", "glance__top");
  for (const score of top) {
    const item = el("li");
    const button = el("button", "glance__place");
    button.type = "button";
    button.append(
      swatch(score.risk_category),
      el("span", "glance__name", score.region_name),
      el("span", "glance__value", formatTimesShort(valueOf(score))),
    );
    button.addEventListener("click", () => select(score.region, { pan: true }));
    item.append(button);
    list.append(item);
  }

  els.glance.replaceChildren(
    heading,
    counts,
    ...lines.map((line) => el("p", "glance__line", line)),
    el("p", "detail__subheading", "Highest against the usual chance"),
    list,
  );
}

/**
 * The situation in words (riskengine/briefing.py) and the places that stand
 * out, each one click from its details. Only for the latest scores: a
 * briefing is about now.
 */
async function renderBriefing(params) {
  if (!givesAdvice()) {
    els.briefing.hidden = true;
    clearHotspots();
    return;
  }
  const key = viewKey() + JSON.stringify(params);
  let body;
  try {
    body = await fetchBriefing(params);
  } catch (error) {
    console.error(error);
    els.briefing.hidden = true;
    return;
  }
  // The view may have changed while this loaded.
  if (viewKey() + JSON.stringify(params) !== key || !givesAdvice()) return;

  els.briefing.hidden = false;
  const heading = el("h2", "panel__heading", "Situation");
  heading.append(el("span", "panel__hint", `written from the data, ${formatDate(`${body.date}T00:00:00`)}`));
  const text = el("div", "briefing__text");
  text.append(...body.paragraphs.map((p) => el("p", null, p)));

  const nodes = [heading, el("p", "briefing__headline", body.headline), text];
  if (body.hotspots.length > 0) {
    nodes.push(el("p", "detail__subheading", "Watch these places"));
    const list = el("ul", "hotspots");
    for (const spot of body.hotspots.slice(0, 8)) {
      const item = el("li");
      const button = el("button", "hotspot");
      button.type = "button";
      const name = el("span", "hotspot__name");
      name.append(swatch(spot.category), spot.name);
      button.append(name, ...spot.reasons.map((r) => el("span", `hotspot__reason hotspot__reason--${r.kind}`, r.text)));
      button.addEventListener("click", () => selectOrOpenKenya(spot.region));
      item.append(button);
      list.append(item);
    }
    nodes.push(list);
  }
  els.briefing.replaceChildren(...nodes);
  drawHotspots(body.hotspots);
}

/* --------------------------------------------------------------- alerts */

const LEVEL_RANK = { advisory: 1, watch: 2, warning: 3 };

/** Load the alerts in force and show the banner. A failure shows nothing. */
async function loadAlerts() {
  try {
    state.alerts = await fetchAlerts();
  } catch (error) {
    console.error(error);
    state.alerts = [];
  }
  state.alertsByRegion = new Map(state.alerts.map((alert) => [alert.region, alert]));
  renderAlertBanner();
}

/** "1 flood warning and 70 watches in force", over the map, coloured by the worst. */
function renderAlertBanner() {
  const counts = countByLevel(state.alerts);
  const text = bannerText(counts);
  els.alertBanner.hidden = !text;
  if (!text) return;
  const worst = ["warning", "watch", "advisory"].find((level) => counts[level] > 0);
  els.alertBanner.className = `alert-banner alert-banner--${worst}`;
  const icon = el("span", "alert-banner__icon", "!");
  icon.setAttribute("aria-hidden", "true");
  const links = el("span", "alert-banner__links");
  const see = el("a", "auth__button auth__button--primary", "See the alerts");
  see.href = "#/alerts";
  const get = el("a", "auth__button", "Get alerts by email");
  get.href = "#/alerts/subscribe/";
  links.append(see, get);
  els.alertBanner.replaceChildren(
    icon,
    el("span", "alert-banner__text", text),
    el("span", "alert-banner__hint", "Issued from today's scores and the 7-day forecast."),
    links,
  );
}

let alertLayer = null;

function clearAlertBadges() {
  alertLayer?.remove();
  alertLayer = null;
}

/** A triangle on each place on the map with an alert in force, above its name. */
function drawAlertBadges() {
  clearAlertBadges();
  if (!givesAdvice()) return;
  alertLayer = L.layerGroup();
  for (const [regionId, layer] of state.layersByRegion) {
    const alert = state.alertsByRegion.get(regionId);
    const point = alert && labelPoint(layer.feature.geometry);
    if (!point) continue;
    const marker = L.marker(point, {
      icon: L.divIcon({
        className: "",
        html: `<span class="alert-badge alert-badge--${alert.level}">!</span>`,
        iconSize: [24, 22],
        // Above the place's name, which sits on the same point.
        iconAnchor: [12, 30],
      }),
      keyboard: false,
      title: alert.headline,
      zIndexOffset: 200 * LEVEL_RANK[alert.level],
    });
    marker.on("click", () => select(regionId, { pan: true }));
    alertLayer.addLayer(marker);
  }
  alertLayer.addTo(map);
}

/** The alert in force for a place, at the top of its details. */
function renderAlertBox(regionId, container) {
  const alert = givesAdvice() ? state.alertsByRegion.get(regionId) : null;
  if (!alert) return;
  const box = el("div", `detail-alert detail-alert--${alert.level}`);
  const link = el("a", null, "Full alert, and the CAP message");
  link.href = `#/alerts/${alert.id}`;
  const todo = el("p");
  todo.append(el("strong", null, "What to do: "), alert.instruction);
  box.append(
    el("span", `alert-pill alert-pill--${alert.level}`, LEVEL_LABELS[alert.level]),
    el("p", "detail-alert__title", alert.reasons.map((r) => r.text).join("; ")),
    todo,
    link,
  );
  container.append(box);
}

/** The map view that holds an alert's place, with the place selected. */
function showAlertOnMap(alert) {
  state.pendingSelect = alert.region;
  state.period = null;
  state.scoreDate = null;
  state.forecastDay = null;
  const county = state.counties?.features.find((f) => nameOf(f) === alert.county_name);
  // A county is on the focus overview if it is in a focus region, else on All Kenya.
  let slug = county ? countySlug(regionIdOf(county), state.focusByCountyId) : KENYA_SLUG;
  if (alert.region_level === "sub_county") {
    const focus = state.focusRegions.find((f) => f.counties.some((c) => c.name === alert.county_name));
    // A sub-county outside the focus regions: its county is on the national map.
    if (focus) slug = focus.slug;
    else state.pendingSelect = county ? regionIdOf(county) : null;
  }
  location.hash = slug ? `#/${slug}` : "#/";
  if (currentSlug() === slug) route();
}

let hotspotLayer = null;

function clearHotspots() {
  hotspotLayer?.remove();
  hotspotLayer = null;
}

/**
 * A pulsing ring on each place the briefing flags for a reason other than
 * being among the highest (those are already the darkest on the map).
 */
function drawHotspots(spots) {
  clearHotspots();
  hotspotLayer = L.layerGroup();
  for (const spot of spots) {
    if (spot.reasons.every((r) => r.kind === "top")) continue;
    if (state.alertsByRegion.has(spot.region)) continue;
    const layer = state.layersByRegion.get(spot.region);
    const point = layer && labelPoint(layer.feature.geometry);
    if (!point) continue;
    const urgent = spot.reasons.some((r) => r.kind === "heavy_rain");
    const marker = L.marker(point, {
      // The ring is an inner element: Leaflet places the icon itself with a
      // transform, which an animated transform on it would undo.
      icon: L.divIcon({
        className: `hotspot-pulse${urgent ? " hotspot-pulse--urgent" : ""}`,
        html: '<span class="hotspot-pulse__ring"></span>',
        iconSize: [22, 22],
      }),
      keyboard: false,
      title: `${spot.name}: ${spot.reasons.map((r) => r.text).join("; ")}`,
    });
    marker.on("click", () => select(spot.region, { pan: true }));
    hotspotLayer.addLayer(marker);
  }
  hotspotLayer.addTo(map);
}

function formatTimesShort(times) {
  return typeof times === "number" ? `${times.toFixed(1)}×` : "—";
}

/**
 * The focus overview's list of regions, each with its worst county today and
 * the alerts in force in it, and why the map shows these and not all Kenya.
 */
function renderFocusIntro() {
  els.regionIntro.hidden = false;
  const list = el("ul", "focus-list");
  for (const region of regionSummaries(state.focusRegions, state.scoresByRegion, state.alerts)) {
    const button = el("button", "focus-list__item");
    button.type = "button";
    button.append(
      region.worst ? swatch(region.worst) : el("span", "legend__swatch"),
      el("span", "focus-list__name", region.name),
      el("span", "focus-list__risk", region.worst ? `Up to ${labelFor(region.worst)}` : "No score"),
      el("span", "focus-list__alerts", givesAdvice() ? alertSummary(region.alerts) : ""),
    );
    button.addEventListener("click", () => {
      location.hash = `#/${region.slug}`;
    });
    const item = el("li");
    item.append(button);
    list.append(item);
  }

  const all = el("button", "link-button", "All Kenya →");
  all.type = "button";
  all.addEventListener("click", () => {
    location.hash = `#/${KENYA_SLUG}`;
  });

  els.regionIntro.replaceChildren(
    el("h2", "region-intro__name", "Flood-prone focus regions"),
    el(
      "p",
      "region-intro__hazards",
      "The model is trained on all 47 counties, because Kenya's flood records are national " +
        "and fewer counties would leave too few floods to learn from. The map opens on the " +
        `${state.focusRegions.length} regions named in national flood plans; pick one for its ` +
        "sub-counties.",
    ),
    list,
    all,
  );
}

function renderRegionIntro() {
  if (isFocusOverview()) {
    renderFocusIntro();
    return;
  }
  if (!isRegionView()) {
    els.regionIntro.hidden = true;
    return;
  }
  const focus = state.focus;
  els.regionIntro.hidden = false;
  els.regionIntro.replaceChildren();

  const back = el("button", "link-button", "← Focus regions");
  back.type = "button";
  back.addEventListener("click", () => {
    location.hash = "";
  });

  els.regionIntro.append(
    back,
    el("h2", "region-intro__name", `${focus.name} region`),
    el("p", "region-intro__hazards", focus.hazards),
    el("p", "region-intro__counties", focus.counties.map((c) => c.name).join(" · ")),
  );
}

function regionButton(score) {
  const button = el("button", "relocation__item");
  button.type = "button";
  button.append(
    swatch(score.risk_category),
    el("span", "relocation__name", score.region_name),
    el("span", "relocation__county", score.county_name),
  );
  button.addEventListener("click", () => select(score.region, { pan: true }));
  return button;
}

function renderRelocation(scores) {
  // No relocation advice from an outlook or from an earlier day's scores.
  if (!isRegionView() || !givesAdvice()) {
    els.relocation.hidden = true;
    return;
  }
  els.relocation.hidden = false;
  els.relocation.replaceChildren();

  const heading = el("h2", "panel__heading", "Relocation");
  heading.append(el("span", "panel__hint", "which sub-counties to move people from"));
  els.relocation.append(heading);

  const byValue = (a, b) => b.predicted_value - a.predicted_value;
  const groups = [
    ["relocate", "Relocate now — very high risk"],
    ["prepare", "Prepare to move — high risk"],
  ];

  let any = false;
  for (const [advisory, title] of groups) {
    const members = scores.filter((s) => s.relocation_advisory === advisory).sort(byValue);
    if (members.length === 0) continue;
    any = true;
    els.relocation.append(el("p", `relocation__group relocation__group--${advisory}`, title));
    const list = el("div", "relocation__list");
    for (const score of members) list.append(regionButton(score));
    els.relocation.append(list);
  }

  if (!any) {
    els.relocation.append(
      el(
        "p",
        "detail__empty",
        `No sub-county in the ${state.focus.name} region is at high or very high risk on the latest scores.`,
      ),
    );
  }

  els.relocation.append(
    el(
      "p",
      "relocation__note",
      "Read straight off the risk category: very high means relocate, high means prepare. " +
        "It is only as good as the scores behind it. In an emergency, follow instructions " +
        "from the National Disaster Operations Centre and county authorities.",
    ),
  );
}

async function renderNeighbours(regionId, container) {
  const heading = el("p", "detail__subheading", "Nearby lower-risk sub-counties");
  const body = el("p", "detail__empty", "Looking for safer areas nearby…");
  container.append(heading, body);

  let neighbours;
  try {
    neighbours = await fetchLowerRiskNeighbours(regionId);
  } catch (error) {
    console.error(error);
    body.textContent = "Could not load nearby areas.";
    return;
  }
  // The user may have moved on while this was loading.
  if (state.selectedRegionId !== regionId) return;

  if (neighbours.length === 0) {
    body.textContent = "No adjacent sub-county has a lower risk score.";
    return;
  }
  const list = el("div", "relocation__list");
  for (const score of neighbours) {
    const inView = state.layersByRegion.has(score.region);
    if (inView) {
      list.append(regionButton(score));
    } else {
      // Outside this region's map: named, but not clickable.
      const item = el("div", "relocation__item relocation__item--static");
      item.append(
        swatch(score.risk_category),
        el("span", "relocation__name", score.region_name),
        el("span", "relocation__county", score.county_name),
      );
      list.append(item);
    }
  }
  body.replaceWith(list);
}

function svg(tag, attributes = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
  return node;
}

/**
 * The region's scores over the HISTORY_DAYS ending on the day of `score`,
 * as a line of "times the usual chance" with the usual chance marked.
 */
async function renderHistory(regionId, score, container) {
  const heading = el("p", "detail__subheading", `Last ${HISTORY_DAYS} days`);
  const body = el("p", "detail__empty", "Loading earlier scores…");
  container.append(heading, body);

  const end = score.prediction_date.slice(0, 10);
  const start = daysBefore(end, HISTORY_DAYS - 1);
  let series;
  try {
    series = historySeries(await fetchScoreHistory(regionId, start, end));
  } catch (error) {
    console.error(error);
    body.textContent = "Could not load earlier scores.";
    return;
  }
  // The user may have moved on while this was loading.
  if (state.selectedRegionId !== regionId) return;

  if (series.length < 2) {
    body.textContent =
      series.length === 1
        ? "Only one day of scores is stored for this area so far."
        : "No earlier scores to compare with.";
    return;
  }

  const width = 280;
  const height = 64;
  const chart = sparkline(series, start, end, { width, height });
  const figure = el("figure", "history");
  const drawing = svg("svg", {
    class: "history__chart",
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    "aria-label":
      `Chance of a large flood on ${series.length} days from ${formatDate(series[0].date)} ` +
      `to ${formatDate(series.at(-1).date)}: between ${chart.low.toFixed(1)} and ` +
      `${chart.high.toFixed(1)} times the usual chance.`,
  });
  drawing.append(
    svg("line", { class: "history__usual", x1: 0, x2: width, y1: chart.usualY, y2: chart.usualY }),
    svg("polyline", {
      class: "history__line",
      points: chart.points.map((point) => `${point.x},${point.y}`).join(" "),
    }),
  );
  for (const point of chart.points) {
    const dot = svg("circle", { class: "history__dot", cx: point.x, cy: point.y, r: 2.2 });
    const title = svg("title");
    title.textContent = `${formatDate(point.date)}: ${point.value.toFixed(1)} times the usual chance`;
    dot.append(title);
    drawing.append(dot);
  }

  const first = series[0];
  const last = series.at(-1);
  const caption = el(
    "figcaption",
    "history__caption",
    `${formatDate(first.date)}: ${first.value.toFixed(1)} times usual. ` +
      `${formatDate(last.date)}: ${last.value.toFixed(1)} times usual. ` +
      `Lowest ${chart.low.toFixed(1)}, highest ${chart.high.toFixed(1)}, ` +
      `over ${series.length} days with a score. The dashed line is the usual chance.`,
  );
  figure.append(drawing, caption);
  body.replaceWith(figure);
}

const COMPARISON_LABELS = { above: "Above", below: "Below", near: "Near" };

function formatInput(value, unit) {
  if (typeof value !== "number") return "Not known";
  const digits = unit === "km²" || unit === "m" ? 0 : unit === "mm" ? 1 : 2;
  const number = value.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  return unit ? `${number} ${unit}` : number;
}

/**
 * What the model was given for this score (wireframe 4.8): each input, and
 * where there is a usual value, whether this one is above or below it.
 * Headed "inputs", never "causes", and with no confidence figure: the
 * evaluation found the probabilities are not calibrated on later years.
 */
async function renderInputs(regionId, score, container) {
  const heading = el("p", "detail__subheading", "Inputs behind this score");
  const body = el("p", "detail__empty", "Loading the inputs…");
  container.append(heading, body);

  let result;
  try {
    result = await fetchScoreInputs(score.id);
  } catch (error) {
    console.error(error);
    body.textContent = "Could not load the inputs.";
    return;
  }
  // The user may have moved on while this was loading.
  if (state.selectedRegionId !== regionId) return;

  if (result.inputs.length === 0) {
    body.textContent = "This score came from the placeholder rule, which has no model inputs.";
    return;
  }

  const list = el("dl", "inputs");
  for (const input of result.inputs) {
    const term = el("dt", null, input.label);
    const value = el("dd");
    if (input.value !== null || input.unit) {
      value.append(el("span", "inputs__value", formatInput(input.value, input.unit)));
    }
    const comparison = COMPARISON_LABELS[input.comparison];
    const against = comparison
      ? `${comparison} the usual ${formatInput(input.usual, input.unit)}`
      : input.note;
    if (against) value.append(el("span", "inputs__against", against));
    // The longer explanation, where the line above is the comparison.
    if (comparison && input.note) value.title = input.note;
    list.append(term, value);
  }

  const note = el(
    "p",
    "relocation__note",
    "What the model was given for this area and date, worked out again from the rainfall " +
      "stored now. These are inputs, not causes: no single one explains the category." +
      (result.same_model
        ? ""
        : ` This score was written by ${result.model_version}; the inputs are shown as ` +
          `${result.computed_with} reads them.`),
  );
  const wrap = el("div");
  wrap.append(list, note);
  body.replaceWith(wrap);
}

/** The detail panel's lines for the week's change and the rain behind the score. */
function contextEntries(context) {
  const entries = [];
  const change = describeChange(context?.change_7d);
  if (change) entries.push(["Trend", change]);
  const rain = describeRain(context);
  if (rain) entries.push(["Rain", rain]);
  return entries;
}

/**
 * The Kenya Meteorological Department's warnings covering this place, if
 * any. Shown first: an official warning outranks anything FloodScout works
 * out. A slot is held so the box lands where it belongs whenever it loads.
 */
async function renderKmd(regionId, container) {
  const slot = el("div", "kmd-notice-slot");
  container.append(slot);
  let warnings;
  try {
    warnings = (await fetchKmdWarnings({ region: regionId })).features;
  } catch (error) {
    console.error(error);
    slot.remove();
    return;
  }
  if (state.selectedRegionId !== regionId || warnings.length === 0) {
    slot.remove();
    return;
  }
  for (const { properties: p } of warnings) {
    const box = el("div", "kmd-notice");
    box.setAttribute("role", "note");
    const until = p.expires ? ` until ${formatDate(p.expires)}` : "";
    const from = new Date(p.onset) > new Date() ? ` from ${formatDate(p.onset)}` : " in force";
    box.append(
      el("p", "kmd-notice__title", `Kenya Met Department: ${p.event.toLowerCase()} ${p.severity === "Severe" || p.severity === "Extreme" ? "warning" : "advisory"}${from}${until}`),
      el("p", "kmd-notice__text", p.headline || p.description),
    );
    const link = el("a", "kmd-notice__link", "Read KMD's warning");
    link.href = p.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    box.append(link);
    slot.append(box);
  }
}

/** "1st", "2nd", "23rd". */
function ordinal(n) {
  const tens = n % 100;
  if (tens >= 11 && tens <= 13) return `${n}th`;
  return `${n}${{ 1: "st", 2: "nd", 3: "rd" }[n % 10] ?? "th"}`;
}

/**
 * Where inside this place the water goes, from the ground alone (the
 * susceptibility layer, riskengine/susceptibility.py). Says nothing until
 * the layer has been built. A slot keeps its place in the panel.
 */
async function renderSusceptibility(regionId, name, container) {
  const slot = el("div", "susceptibility");
  container.append(slot);
  let s;
  try {
    s = await fetchSusceptibility(regionId);
  } catch (error) {
    console.error(error);
    slot.remove();
    return;
  }
  if (state.selectedRegionId !== regionId || !s.available || s.high_share === null) {
    slot.remove();
    return;
  }
  const share = Math.round(s.high_share * 100);
  const fifth = 100 - s.high_percentile;
  slot.append(el("p", "detail__subheading", "Where the water goes"));
  let line = `${share}% of ${name}'s land is among Kenya's most flood-prone ${fifth === 20 ? "fifth" : `${fifth}%`}`;
  if (s.county_high_share !== null) line += ` (its county as a whole: ${Math.round(s.county_high_share * 100)}%)`;
  slot.append(el("p", "susceptibility__share", `${line}.`));
  const facts = [];
  if (s.rank_in_kenya) facts.push(`${ordinal(s.rank_in_kenya)} of ${s.of} in Kenya on that measure`);
  facts.push(
    `about ${Math.round(s.expected_km2).toLocaleString()} km² of its ${Math.round(s.land_km2).toLocaleString()} km² ` +
      "would be expected under water when a large flood reaches it",
  );
  slot.append(el("p", "susceptibility__facts", `${facts.join("; ")}.`.replace(/^./, (c) => c.toUpperCase())));
  const checked = s.checked
    ? ` Checked once against UNOSAT satellite flood maps (November 2023, April 2024): it ranked the sub-counties that flooded at ${s.checked.sub_county_roc_auc.toFixed(2)}, against ${s.checked.engine_roc_auc.toFixed(2)} for rain alone and 0.50 for chance.`
    : " Not yet checked against satellite flood maps.";
  slot.append(
    el(
      "p",
      "susceptibility__note",
      "From the ground alone (height above the nearest river, slope, upstream area and the 100-year flood zone); it does not change with the weather." +
        checked,
    ),
  );
}

/**
 * Large floods recorded in this place before (DFO, 1988 to 2021): how many,
 * the most recent, and the worst. A recorded flood's footprint is a circle
 * of its area, so "reached" is rough, and it is said so.
 */
async function renderPastFloods(regionId, container) {
  const heading = el("p", "detail__subheading", "Floods recorded here before");
  const body = el("p", "detail__empty", "Looking up past floods…");
  container.append(heading, body);

  let events;
  try {
    events = (await fetchFloodEvents({ region: regionId })).features;
  } catch (error) {
    console.error(error);
    body.textContent = "Could not load past floods.";
    return;
  }
  if (state.selectedRegionId !== regionId) return;

  if (events.length === 0) {
    body.textContent =
      "No large flood recorded by the Dartmouth Flood Observatory reached this area from 1988 to 2021.";
    return;
  }
  const year = (e) => new Date(e.properties.began).getFullYear();
  const latest = events[0];
  const worst = [...events].sort(
    (a, b) => b.properties.dead - a.properties.dead || b.properties.displaced - a.properties.displaced,
  )[0];
  const wrap = el("div", "past-floods");
  const years = [...new Set(events.map(year))].sort((a, b) => a - b);
  wrap.append(
    el(
      "p",
      "past-floods__count",
      `${events.length} large flood${events.length === 1 ? "" : "s"} reached this area, in ${years.length} ` +
        `different year${years.length === 1 ? "" : "s"} from ${years[0]} to ${years.at(-1)}.`,
    ),
  );
  const describe = (e) => {
    const p = e.properties;
    const toll = [p.dead ? `${p.dead.toLocaleString()} dead` : null, p.displaced ? `${p.displaced.toLocaleString()} displaced` : null]
      .filter(Boolean)
      .join(", ");
    return `${formatDate(p.began)}${p.main_cause ? `, ${p.main_cause.toLowerCase()}` : ""}${toll ? ` (${toll} in all)` : ""}`;
  };
  const list = el("dl", "detail__rows");
  list.append(el("dt", null, "Most recent"), el("dd", null, describe(latest)));
  if (worst !== latest && (worst.properties.dead || worst.properties.displaced)) {
    list.append(el("dt", null, "Deadliest"), el("dd", null, describe(worst)));
  }
  wrap.append(
    list,
    el(
      "p",
      "relocation__note",
      "Tolls are for the whole event, which often covered several counties. Turn on " +
        "“Past floods” in the map's layer menu to see them all.",
    ),
  );
  body.replaceWith(wrap);
}

/**
 * "Why this score": the model's own account of the score, as each group of
 * inputs' share of it in times the usual chance (riskengine/ml/explain.py).
 * Bars to the right raised the risk, to the left lowered it.
 */
async function renderWhy(regionId, score, container) {
  const heading = el("p", "detail__subheading", "Why this score");
  const body = el("p", "detail__empty", "Working out what drove this score…");
  container.append(heading, body);

  let why;
  try {
    why = await fetchExplanation(score.id);
  } catch (error) {
    // A placeholder score has nothing to explain; say nothing.
    if (error.status === 404) {
      heading.remove();
      body.remove();
    } else {
      console.error(error);
      body.textContent = "Could not work out what drove this score.";
    }
    return;
  }
  if (state.selectedRegionId !== regionId) return;

  const wrap = el("div", "why");
  const driver = el(
    "p",
    `why__driver why__driver--${why.driven_by}`,
    why.driven_by === "rain"
      ? "Driven by rain that has fallen"
      : "Driven by the season and the oceans, not by rain that has fallen",
  );
  wrap.append(driver, el("p", "why__summary", why.summary));

  const factors = why.factors.filter((f) => typeof f.times_usual === "number");
  const biggest = Math.max(0.1, ...factors.map((f) => Math.abs(f.times_usual)));
  const chart = el("div", "why__chart");
  chart.setAttribute("role", "list");
  chart.append(
    whyRow("The usual chance", why.baseline_times_usual, biggest, "why__row--base"),
  );
  for (const factor of factors) chart.append(whyRow(factor.label, factor.times_usual, biggest));
  chart.append(whyRow("This score", why.times_usual, biggest, "why__row--total"));
  wrap.append(
    chart,
    el(
      "p",
      "relocation__note",
      "How the model used each input for this score, split exactly from the trees of the " +
        "forest; the parts add up to the score. It shows what the model relied on, not what " +
        "causes floods: county size, for one, is how floods were recorded." +
        (why.same_model ? "" : ` Worked out with ${why.computed_with}.`),
    ),
  );
  body.replaceWith(wrap);
}

/** One bar of the "why" chart: centred on zero, or from zero for the base and total. */
function whyRow(label, times, biggest, modifier = "") {
  const row = el("div", `why__row ${modifier}`);
  row.setAttribute("role", "listitem");
  const total = modifier !== "";
  let value = `${times >= 0 ? "+" : "−"}${Math.abs(times).toFixed(1)}×`;
  if (total) value = `${times.toFixed(1)}×`;
  else if (Math.abs(times) < 0.05) value = "0.0×";
  const bar = el("span", "why__bar");
  const fill = el("span", `why__fill ${times >= 0 ? "why__fill--up" : "why__fill--down"}`);
  if (total) {
    const scale = Math.max(biggest * 2, times);
    fill.style.left = "0";
    fill.style.width = `${Math.min(100, (times / scale) * 100)}%`;
  } else {
    const half = Math.min(50, (Math.abs(times) / biggest) * 50);
    fill.style.width = `${half}%`;
    fill.style.left = times >= 0 ? "50%" : `${50 - half}%`;
  }
  bar.append(fill);
  row.append(el("span", "why__label", label), bar, el("span", "why__value", value));
  row.setAttribute("aria-label", `${label}: ${value}`);
  return row;
}

/** The week ahead for one region: a strip of the 7 forecast days. */
async function renderWeek(regionId, container) {
  if (state.forecastDays.length === 0) return;
  const heading = el("p", "detail__subheading", "Next 7 days (forecast)");
  const body = el("p", "detail__empty", "Loading the forecast…");
  container.append(heading, body);

  let week;
  try {
    week = await fetchForecast({ region: regionId });
  } catch (error) {
    console.error(error);
    body.textContent = "Could not load the forecast.";
    return;
  }
  if (state.selectedRegionId !== regionId) return;
  if (week.length === 0) {
    body.textContent = "No forecast for this area yet.";
    return;
  }

  const strip = el("div", "week");
  for (const day of week) {
    const date = new Date(`${day.target_date}T00:00:00`);
    const cell = el("button", "week__day");
    cell.type = "button";
    cell.title =
      `${formatDate(`${day.target_date}T00:00:00`)}: ${labelFor(day.risk_category)}, ` +
      `${formatTimesUsual(day.times_usual).toLowerCase()}` +
      (typeof day.rain_forecast_max_24h === "number"
        ? `; wettest forecast day so far ${Math.round(day.rain_forecast_max_24h)} mm`
        : "");
    const colour = el("span", "week__colour");
    colour.style.background = themeColour("usual", day, null, DARK);
    cell.append(
      el("span", "week__name", date.toLocaleDateString(undefined, { weekday: "short" })),
      colour,
      el("span", "week__value", formatTimesShort(day.times_usual)),
      el("span", "week__level", labelFor(day.risk_category)),
    );
    cell.addEventListener("click", () => {
      stopPlaying();
      state.pendingSelect = regionId;
      state.period = null;
      state.scoreDate = null;
      state.forecastDay = day.target_date;
      route();
    });
    strip.append(cell);
  }
  const peak = week.reduce((a, b) => (b.times_usual > a.times_usual ? b : a));
  const wettest = Math.max(...week.map((d) => d.rain_forecast_max_24h ?? 0));
  const note = el(
    "p",
    "relocation__note",
    `Highest on ${formatDate(`${peak.target_date}T00:00:00`)} at ${formatTimesShort(peak.times_usual)} ` +
      `the usual chance. Wettest forecast day: ${Math.round(wettest)} mm` +
      (wettest >= 50 ? ", above the 50 mm the Kenya Meteorological Department treats as a flood warning sign." : ".") +
      " Click a day to see the whole map for it.",
  );
  const wrap = el("div");
  wrap.append(strip, note);
  body.replaceWith(wrap);
}

function renderDetail(regionId) {
  const score = state.scoresByRegion.get(regionId);
  const layer = state.layersByRegion.get(regionId);
  const name = score?.region_name ?? (layer ? nameOf(layer.feature) : "Unknown area");

  els.detail.replaceChildren();
  els.detail.append(el("p", "detail__name", name));

  const badge = el("p", "detail__badge");
  const badgeSwatch = el("span", "detail__swatch");
  badgeSwatch.style.background = colourFor(score?.risk_category, DARK);
  badge.append(badgeSwatch, labelFor(score?.risk_category));
  els.detail.append(badge);
  renderAlertBox(regionId, els.detail);

  const rows = el("dl", "detail__rows");
  let entries;
  if (!score) {
    entries = [["Status", "No risk score recorded for this area."]];
  } else if (isOutlook()) {
    entries = [
      ...(isRegionView() ? [["County", score.county_name]] : []),
      ["Chance of a large flood", formatTimesUsual(score.times_usual)],
      [
        "Range across forecasts",
        typeof score.times_usual_low === "number" && typeof score.times_usual_high === "number"
          ? `${score.times_usual_low.toFixed(1)} to ${score.times_usual_high.toFixed(1)} times usual`
          : "—",
      ],
      [
        "Forecast rain",
        typeof score.rain_vs_normal === "number"
          ? `${score.rain_vs_normal.toFixed(1)} times normal`
          : "—",
      ],
      ["Outlook for", formatMonth(score.month)],
      ["Model version", score.model_version ?? "—"],
    ];
  } else if (isForecast()) {
    entries = [
      ...(isRegionView() ? [["County", score.county_name]] : []),
      ["Chance of a large flood", formatTimesUsual(score.times_usual)],
      [
        "Forecast rain",
        typeof score.rain_forecast_mm === "number"
          ? `${Math.round(score.rain_forecast_mm)} mm between now and this day`
          : "—",
      ],
      [
        "Wettest forecast day",
        typeof score.rain_forecast_max_24h === "number"
          ? `${Math.round(score.rain_forecast_max_24h)} mm`
          : "—",
      ],
      ["Forecast for", formatDate(score.prediction_date)],
      ["Issued", formatDate(state.forecastIssued)],
      ["Model version", score.model_version ?? "—"],
    ];
  } else {
    entries = [
        ...(isRegionView() ? [["County", score.county_name]] : []),
        ...(isRegionView() && givesAdvice()
          ? [["Advisory", advisoryFor(score.relocation_advisory)]]
          : []),
        // A placeholder score has no usual chance to compare with.
        ...(typeof score.times_usual === "number"
          ? [["Chance of a large flood", formatTimesUsual(score.times_usual)]]
          : []),
        ...contextEntries(state.contextByRegion.get(regionId)),
        ["Model output", formatValue(score.predicted_value)],
        ["Prediction date", formatDate(score.prediction_date)],
        ["Model version", score.model_version ?? "—"],
    ];
  }

  for (const [term, value] of entries) {
    rows.append(el("dt", null, term), el("dd", null, value));
  }
  els.detail.append(rows);
  renderKmd(regionId, els.detail);

  // Only a stored score has a model run to explain, a history and inputs:
  // an outlook or a forecast row is driven by rain that has not fallen.
  const stored = score && !isOutlook() && !isForecast();
  if (stored) renderWhy(regionId, score, els.detail);
  // The week ahead, from today's view.
  if (score && givesAdvice()) renderWeek(regionId, els.detail);
  // A placeholder score has no multiple of the usual chance to plot.
  if (stored && typeof score.times_usual === "number") {
    renderHistory(regionId, score, els.detail);
  }
  if (stored) renderInputs(regionId, score, els.detail);
  renderSusceptibility(regionId, name, els.detail);
  renderPastFloods(regionId, els.detail);

  // Signed in, one click with the profile's email and phone; else the form.
  const getAlerts = quickAlertControl([{ id: regionId, name }], state.user);
  getAlerts.classList.add("get-alerts");
  els.detail.append(getAlerts);

  if (!isRegionView()) {
    const focus = state.focusByCountyId.get(regionId);
    if (focus) {
      const open = el("button", "open-region", `Open the ${focus.name} region →`);
      open.type = "button";
      open.addEventListener("click", () => {
        location.hash = `#/${focus.slug}`;
      });
      els.detail.append(open);
    }
  } else if (score && givesAdvice() && score.relocation_advisory !== "none") {
    renderNeighbours(regionId, els.detail);
  }
}

function renderEmptyDetail() {
  els.detail.replaceChildren(
    el(
      "p",
      "detail__empty",
      isRegionView()
        ? "Select a sub-county on the map, the relocation list or the table."
        : isFocusOverview()
          ? "Select a county on the map, or pick a focus region to see its sub-counties."
          : "Select a county on the map, or pick a region above to see its sub-counties.",
    ),
  );
}

function renderTable(features) {
  const region = isRegionView();
  els.tableBody.replaceChildren();
  state.rowsByRegion.clear();

  els.tableHeading.firstChild.textContent = region
    ? "All sub-counties "
    : isFocusOverview()
      ? "Focus counties "
      : "All counties ";
  const headRow = el("tr");
  const headings = region
    ? ["Sub-county", "County", "Risk", "Value"]
    : ["County", "Risk", "Value"];
  for (const heading of headings) {
    const th = el("th", heading === "Value" ? "table__num" : null, heading);
    th.scope = "col";
    headRow.append(th);
  }
  els.tableHead.replaceChildren(headRow);

  // Counties read best alphabetically; in a region, the most at-risk first.
  const valueOf = (f) => state.scoresByRegion.get(regionIdOf(f))?.predicted_value ?? -1;
  const sorted = [...features].sort(
    region ? (a, b) => valueOf(b) - valueOf(a) : (a, b) => nameOf(a).localeCompare(nameOf(b)),
  );

  for (const feature of sorted) {
    const regionId = regionIdOf(feature);
    const score = state.scoresByRegion.get(regionId);

    const row = el("tr");
    row.tabIndex = 0;
    row.setAttribute("aria-selected", "false");

    const nameCell = el("td");
    const nameWrap = el("span", "table__county");
    nameWrap.append(swatch(score?.risk_category), nameOf(feature));
    nameCell.append(nameWrap);
    row.append(nameCell);

    if (region) row.append(el("td", null, feature.properties?.county_name ?? "—"));
    row.append(
      el("td", null, labelFor(score?.risk_category)),
      el("td", "table__num", formatValue(score?.predicted_value)),
    );

    row.addEventListener("click", () => {
      select(regionId, { pan: true });
      showDetail();
    });
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        select(regionId, { pan: true });
        showDetail();
      }
    });

    els.tableBody.append(row);
    state.rowsByRegion.set(regionId, row);
  }
}

/* ------------------------------------------------------------ selection */

function select(regionId, { pan = false } = {}) {
  state.selectedRegionId = regionId;

  for (const [id, layer] of state.layersByRegion) {
    layer.setStyle(restingStroke(id));
    if (id === regionId) layer.bringToFront();
  }

  for (const [id, row] of state.rowsByRegion) {
    row.setAttribute("aria-selected", String(id === regionId));
  }

  els.detailHeading.textContent = isRegionView() ? "Sub-county" : "County";
  renderDetail(regionId);

  const layer = state.layersByRegion.get(regionId);
  if (pan && layer) {
    map.fitBounds(layer.getBounds(), { padding: [24, 24], maxZoom: isRegionView() ? 11 : 8 });
  }
}

/**
 * Bring the details of the selected area into view. The table sits below
 * them, far down the page on a phone, so a pick from it would otherwise
 * change something the reader cannot see. Focus follows, for keyboard and
 * screen-reader users.
 */
function showDetail() {
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  els.detailHeading.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" });
  els.detailHeading.tabIndex = -1;
  els.detailHeading.focus({ preventScroll: true });
}

/* --------------------------------------------------------------- report */

// Printed under every report. A shorter form of the limits in Chapter 5,
// Section 5.10.3, for a reader who has only the page.
const RESPONSIBLE_USE_NOTE =
  "How to read this report. The categories rank areas by the chance that a large flood is " +
  "recorded; they are not a forecast that a flood will happen, and a Low category is not an " +
  "all-clear. The model was trained on county records of past large floods. It misses many " +
  "floods outside the two rainy seasons, its probabilities run high, and it does not tell " +
  "the sub-counties of one county apart reliably. This is a student research project and " +
  "not official guidance. In an emergency, follow the National Disaster Operations Centre " +
  "and the county authorities.";

function viewName() {
  if (isRegionView()) return `${state.focus.name} region`;
  return isFocusOverview() ? "Focus regions" : "Kenya";
}

/** The day or month the scores on screen are for, as the API wrote it. */
function viewDate() {
  if (isOutlook()) return state.period.slice(0, 7);
  const latest = state.viewScores.map((s) => s.prediction_date).filter(Boolean).sort().at(-1);
  return latest ? latest.slice(0, 10) : "";
}

/**
 * Fill the print-only section from the scores on screen: what they are for,
 * what produced them, every area sorted by risk, and how to read it.
 */
function renderReport(scores) {
  state.viewScores = scores;
  els.printReport.disabled = scores.length === 0;
  els.downloadCsv.disabled = scores.length === 0;

  const versions = [...new Set(scores.map((s) => s.model_version).filter(Boolean))];
  const what = isOutlook()
    ? `Seasonal outlook for ${formatMonth(state.period)}`
    : `Scores for ${formatDate(viewDate() && `${viewDate()}T00:00:00`)}`;

  const counts = CATEGORY_ORDER.map((category) => {
    const item = el("span", "report__count");
    item.append(
      swatch(category),
      `${labelFor(category)}: ${scores.filter((s) => s.risk_category === category).length}`,
    );
    return item;
  });
  const legend = el("p", "report__legend");
  legend.append(...counts);

  const region = isRegionView();
  const table = el("table", "report__table");
  const head = el("tr");
  const headings = ["", region ? "Sub-county" : "County", "Risk category", "Chance of a large flood"];
  if (region) headings.splice(2, 0, "County");
  for (const heading of headings) {
    const th = el("th", null, heading);
    th.scope = "col";
    head.append(th);
  }
  const thead = el("thead");
  thead.append(head);
  const tbody = el("tbody");
  sortByRisk(scores).forEach((score, index) => {
    const row = el("tr");
    const category = el("td");
    const wrap = el("span", "table__county");
    wrap.append(swatch(score.risk_category), labelFor(score.risk_category));
    category.append(wrap);
    row.append(el("td", "table__num", `${index + 1}`), el("td", null, score.region_name));
    if (region) row.append(el("td", null, score.county_name));
    row.append(category, el("td", null, formatTimesUsual(score.times_usual)));
    tbody.append(row);
  });
  table.append(thead, tbody);

  els.report.replaceChildren(
    el("h2", "report__title", `${viewName()}: flood risk report`),
    el(
      "p",
      "report__meta",
      `${what}. Model version ${versions.join(", ") || "not recorded"}. ` +
        `${scores.length} ${region ? "sub-counties" : "counties"}, highest risk first. ` +
        `Printed ${formatDate(new Date().toISOString())}.`,
    ),
    legend,
    table,
    el("p", "report__note", RESPONSIBLE_USE_NOTE),
  );
}

/** Save the scores on screen as a CSV file, built in the browser. */
function downloadCsv() {
  // The byte-order mark makes Excel read the file as UTF-8.
  const blob = new Blob(["\uFEFF", scoresToCsv(state.viewScores)], {
    type: "text/csv;charset=utf-8",
  });
  const link = el("a");
  link.href = URL.createObjectURL(blob);
  link.download = reportFileName(
    `${isRegionView() ? state.focus.name : isFocusOverview() ? "focus regions" : "kenya"}${isOutlook() ? " outlook" : ""}`,
    viewDate(),
  );
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 0);
}

/**
 * The map is drawn for the size it has on screen. For printing it is given
 * the size it will have on paper first and redrawn to fit, then put back.
 * Both events also fire when the browser's own Print command is used.
 */
function initReport() {
  window.addEventListener("beforeprint", () => {
    document.documentElement.classList.add("printing");
    map.invalidateSize({ animate: false });
    refit?.();
  });
  window.addEventListener("afterprint", () => {
    document.documentElement.classList.remove("printing");
    map.invalidateSize({ animate: false });
    refit?.();
  });
  els.printReport.addEventListener("click", () => window.print());
  els.downloadCsv.addEventListener("click", downloadCsv);
}

/** Select the region the search box asked for, if this view holds it. */
/**
 * Select a county from the national briefing. On the focus overview a county
 * outside the focus regions is not on the map, so All Kenya opens with it.
 */
function selectOrOpenKenya(regionId) {
  if (state.layersByRegion.has(regionId) || !isFocusOverview()) {
    select(regionId, { pan: true });
    return;
  }
  state.pendingSelect = regionId;
  location.hash = `#/${KENYA_SLUG}`;
}

function selectPending() {
  const regionId = state.pendingSelect;
  state.pendingSelect = null;
  if (regionId !== null && state.layersByRegion.has(regionId)) {
    select(regionId, { pan: true });
  }
}

/* --------------------------------------------------------------- search */

/** Every county, from the layer already loaded. */
function countyEntries() {
  return (state.counties?.features ?? []).map((feature) => ({
    id: regionIdOf(feature),
    name: nameOf(feature),
    level: "county",
    slug: countySlug(regionIdOf(feature), state.focusByCountyId),
    hint: "County",
  }));
}

/**
 * The sub-counties of every focus region, fetched the first time the search
 * box is used. These are the same requests the region views make, so opening
 * a region afterwards costs nothing extra. A region that fails to load is
 * left out, and the counties stay searchable.
 */
function loadSearchIndex() {
  if (!state.searchIndex) {
    state.searchIndex = Promise.allSettled(
      state.focusRegions.map(async (focus) => {
        const shapes = await loadRegionShapes(focus);
        return shapes.features.map((feature) => ({
          id: regionIdOf(feature),
          name: nameOf(feature),
          level: "sub_county",
          slug: focus.slug,
          hint: `${feature.properties?.county_name ?? "Sub-county"} · ${focus.name}`,
        }));
      }),
    ).then((results) => {
      const seen = new Set();
      state.subCountyEntries = results
        .flatMap((result) => (result.status === "fulfilled" ? result.value : []))
        .filter((entry) => !seen.has(entry.id) && seen.add(entry.id));
      renderSearch();
    });
  }
  return state.searchIndex;
}

function setActiveMatch(index) {
  state.searchActive = index;
  const options = els.searchResults.children;
  for (let i = 0; i < options.length; i += 1) {
    options[i].setAttribute("aria-selected", String(i === index));
  }
  if (index >= 0 && options[index]) {
    els.searchInput.setAttribute("aria-activedescendant", options[index].id);
    options[index].scrollIntoView({ block: "nearest" });
  } else {
    els.searchInput.removeAttribute("aria-activedescendant");
  }
}

function renderSearch() {
  const query = els.searchInput.value.trim();
  state.searchMatches = searchRegions([...countyEntries(), ...state.subCountyEntries], query);

  els.searchResults.replaceChildren();
  state.searchMatches.forEach((entry, index) => {
    const option = el("li", "search__option");
    option.id = `search-option-${index}`;
    option.setAttribute("role", "option");
    option.append(el("span", "search__name", entry.name), el("span", "search__hint", entry.hint));
    // mousedown, not click: it fires before the input loses focus.
    option.addEventListener("mousedown", (event) => {
      event.preventDefault();
      goTo(entry);
    });
    els.searchResults.append(option);
  });

  const open = state.searchMatches.length > 0;
  els.searchResults.hidden = !open;
  els.searchInput.setAttribute("aria-expanded", String(open));
  setActiveMatch(open ? 0 : -1);

  const nothing = query !== "" && !open;
  els.searchEmpty.hidden = !nothing;
  els.searchEmpty.textContent = nothing
    ? `No county or sub-county matches “${query}”. Sub-counties are listed for the focus regions only.`
    : "";
}

function clearSearch() {
  els.searchInput.value = "";
  renderSearch();
}

/** Show the view that holds `entry`, then select it and pan to it. */
function goTo(entry) {
  clearSearch();
  if (currentSlug() === entry.slug && state.layersByRegion.has(entry.id)) {
    select(entry.id, { pan: true });
    return;
  }
  state.pendingSelect = entry.id;
  if (currentSlug() === entry.slug) {
    route();
  } else {
    location.hash = entry.slug ? `#/${entry.slug}` : "";
  }
}

function initSearch() {
  els.searchInput.addEventListener("focus", loadSearchIndex, { once: true });
  els.searchInput.addEventListener("input", renderSearch);
  els.searchInput.addEventListener("keydown", (event) => {
    const count = state.searchMatches.length;
    if (event.key === "ArrowDown" && count > 0) {
      event.preventDefault();
      setActiveMatch((state.searchActive + 1) % count);
    } else if (event.key === "ArrowUp" && count > 0) {
      event.preventDefault();
      setActiveMatch((state.searchActive - 1 + count) % count);
    } else if (event.key === "Enter" && count > 0) {
      event.preventDefault();
      goTo(state.searchMatches[Math.max(state.searchActive, 0)]);
    } else if (event.key === "Escape") {
      clearSearch();
    }
  });
}

/* ---------------------------------------------------------------- views */

function loadCountyScores() {
  const key = viewKey();
  if (!state.countyCache.has(key)) {
    state.countyCache.set(
      key,
      fetchScores({ level: "county" }).catch((error) => {
        state.countyCache.delete(key); // let a retry refetch
        throw error;
      }),
    );
  }
  return state.countyCache.get(key);
}

/** The rest of Kenya, faint and inert, so the shapes on screen keep their place. */
function drawContext(features) {
  addToView(
    L.geoJSON(
      { type: "FeatureCollection", features },
      {
        pane: "context",
        interactive: false,
        // Named so the print stylesheet can lighten it (style.css).
        className: "context-shape",
        style: {
          fillColor: cssVar("--context-fill"),
          // Faint enough that the basemap still places the shapes in Kenya.
          fillOpacity: 0.5,
          color: cssVar("--polygon-stroke"),
          weight: 1,
        },
      },
    ),
  );
}

/** Every county ("kenya"), or the focus counties with the rest faint ("focus"). */
async function showOverview(scope) {
  const slug = currentSlug();
  const view = viewKey();
  const mapBy = state.mapBy;
  const [allScores, grid, context] = await Promise.all([
    loadCountyScores(),
    loadGrid(),
    loadContext({ level: "county" }),
  ]);
  // Another view may have been chosen while this one loaded.
  if (currentSlug() !== slug || viewKey() !== view || state.mapBy !== mapBy) return;

  state.focus = null;
  state.scope = scope;
  clearView();
  const inScope = (regionId) => scope === "kenya" || state.focusByCountyId.has(regionId);
  const scores = allScores.filter((score) => inScope(score.region));
  state.scoresByRegion = new Map(scores.map((score) => [score.region, score]));
  state.contextByRegion = context;
  state.gridOn = grid?.cells.length > 0;

  const features = state.counties.features.filter((f) => inScope(regionIdOf(f)));
  if (scope === "focus") {
    drawContext(state.counties.features.filter((f) => !inScope(regionIdOf(f))));
  }
  drawOutline(features);
  const layer = drawChoropleth(features);
  const cells = state.gridOn
    ? drawGrid(grid, scope === "focus" ? new Set(state.focusByCountyId.keys()) : null)
    : null;
  if (state.gridOn) drawRegionLines(features);
  if (layer.getLayers().length > 0) lockTo(layer.getBounds());
  updateLabelVisibility();

  renderProvenance(scores);
  renderRegionBar();
  renderPeriodBar();
  renderLayerBar();
  renderThemeBar();
  renderTimeline();
  renderGlance(scores);
  renderBriefing({ level: "county" });
  drawAlertBadges();
  markForecast();
  renderRegionIntro();
  renderRelocation(scores);
  els.detailHeading.textContent = "County";
  renderEmptyDetail();
  setLegendRows(scores, cells);
  renderLegend();
  renderTable(features);
  renderReport(scores);
  syncLayerDate();
  setStatus(grid && !state.gridOn ? "No area scores for this day; coloured by county." : null);
  selectPending();
}

function loadRegionShapes(focus) {
  if (!state.shapeCache.has(focus.slug)) {
    state.shapeCache.set(
      focus.slug,
      fetchRegions({ level: "sub_county", focus_region: focus.slug }).catch((error) => {
        state.shapeCache.delete(focus.slug); // let a retry refetch
        throw error;
      }),
    );
  }
  return state.shapeCache.get(focus.slug);
}

async function loadRegion(focus) {
  const key = `${focus.slug}|${viewKey()}`;
  if (!state.regionCache.has(key)) {
    const params = { level: "sub_county", focus_region: focus.slug };
    state.regionCache.set(
      key,
      Promise.all([loadRegionShapes(focus), fetchScores(params)]).catch((error) => {
        state.regionCache.delete(key); // let a retry refetch
        throw error;
      }),
    );
  }
  return state.regionCache.get(key);
}

async function showRegion(focus) {
  setStatus(`Loading ${focus.name} sub-counties…`);
  const view = viewKey();
  const mapBy = state.mapBy;
  const [[subCounties, scores], grid, context] = await Promise.all([
    loadRegion(focus),
    loadGrid(),
    loadContext({ level: "sub_county", focus_region: focus.slug }),
  ]);
  // Another view may have been chosen while this one loaded.
  if (currentSlug() !== focus.slug || viewKey() !== view || state.mapBy !== mapBy) return;

  state.focus = focus;
  clearView();
  state.scoresByRegion = new Map(scores.map((score) => [score.region, score]));
  state.contextByRegion = context;
  state.gridOn = grid?.cells.length > 0;

  const focusCountyIds = new Set(focus.counties.map((c) => c.id));
  const focusCounties = state.counties.features.filter((f) =>
    focusCountyIds.has(regionIdOf(f)),
  );
  const otherCounties = state.counties.features.filter(
    (f) => !focusCountyIds.has(regionIdOf(f)),
  );

  drawContext(otherCounties);
  // Outline and bounds come from the sub-counties themselves, not the
  // county shapes: the two boundary sets were drawn by different agencies
  // (RCMRD and IEBC) and do not line up exactly, so a county outline would
  // leave slivers of gap and overhang along the region's edge.
  drawOutline(subCounties.features);
  const choropleth = drawChoropleth(subCounties.features);
  const cells = state.gridOn ? drawGrid(grid, focusCountyIds) : null;
  if (state.gridOn) drawRegionLines(subCounties.features);
  // County borders over the sub-county fills, so each sub-county reads
  // against its county. Pointless for a one-county region like Nairobi,
  // where the only county border is the region's own edge.
  if (focusCounties.length > 1) {
    addToView(
      L.geoJSON(
        { type: "FeatureCollection", features: focusCounties },
        {
          pane: "countyLines",
          interactive: false,
          style: { color: cssVar("--county-line"), weight: 1.5, fill: false },
        },
      ),
    );
  }

  const bounds = choropleth.getBounds();
  if (bounds.isValid()) lockTo(bounds);
  updateLabelVisibility();

  renderProvenance(scores);
  renderRegionBar();
  renderPeriodBar();
  renderLayerBar();
  renderThemeBar();
  renderTimeline();
  renderGlance(scores);
  renderBriefing({ level: "sub_county", focus_region: focus.slug });
  drawAlertBadges();
  markForecast();
  renderRegionIntro();
  renderRelocation(scores);
  els.detailHeading.textContent = "Sub-county";
  renderEmptyDetail();
  setLegendRows(scores, cells);
  renderLegend();
  renderTable(subCounties.features);
  renderReport(scores);
  syncLayerDate();
  setStatus(null);

  if (subCounties.features.length === 0) {
    setStatus(
      "No sub-counties loaded for this region — run `manage.py seed_regions --level sub_county`.",
      true,
    );
  }
  selectPending();
}

function currentSlug() {
  return location.hash.replace(/^#\/?/, "");
}

function isAdminSlug(slug) {
  return slug === "admin" || slug.startsWith("admin/");
}

function isAlertsSlug(slug) {
  return slug === "alerts" || slug.startsWith("alerts/");
}

function isProfileSlug(slug) {
  return slug === "profile" || slug.startsWith("profile/");
}

function isPageSlug(slug) {
  return isAdminSlug(slug) || isAlertsSlug(slug) || isProfileSlug(slug) || isAuthSlug(slug);
}

let auth = null;

// Settles once a sign-in saved from an earlier visit has been checked with
// the API. route() waits for it, so a reload of #/profile does not find
// nobody signed in, send the reader to the sign-in page, and bounce back.
let sessionChecked = Promise.resolve();

/** A page in place of the map: the analytics (#/admin) or the alerts (#/alerts). */
function setAdminOpen(open) {
  state.adminOpen = open;
  document.body.classList.toggle("is-admin", open);
  if (!open) {
    hideAdmin();
    hideAlerts();
    hideProfile();
  }
}

/** Whether #/ shows the simple view for whoever is here (viewmode.js). */
function simpleHome() {
  return viewFor(state.user) === "simple";
}

/** The header's switch names the other view. */
function updateViewToggle() {
  const onSimple = currentSlug() === "" && simpleHome();
  const label = els.viewToggle.querySelector(".button-label");
  label.textContent = onSimple ? "Detailed map" : "Simple view";
  els.viewToggle.title = onSimple ? "The full map, with every number" : "Plain answers for your area";
}

/** Leave the simple view for the detailed map, with `regionId` selected if given. */
function openDetailed(regionId = null) {
  chooseView("detailed");
  state.pendingSelect = regionId;
  const slug = regionId !== null ? countySlug(regionId, state.focusByCountyId) : "";
  if (currentSlug() === slug) route();
  else location.hash = slug ? `#/${slug}` : "";
}

async function route() {
  await sessionChecked;
  const slug = currentSlug();
  hideSimple();
  document.body.classList.toggle("is-auth", isAuthSlug(slug));
  document.body.classList.toggle("is-simple", slug === "" && simpleHome());
  updateViewToggle();
  if (isAuthSlug(slug)) {
    hideAdmin();
    hideAlerts();
    hideProfile();
    stopPlaying();
    setAdminOpen(true);
    window.scrollTo(0, 0);
    showAuthPage(els.admin, {
      slug,
      user: state.user,
      onSignedIn: (user, next) => {
        // The page first, so the sign-in does not redraw the form it left.
        location.hash = next || "#/";
        auth.setUser(user);
      },
    });
    return;
  }
  if (isAdminSlug(slug)) {
    hideAlerts();
    hideProfile();
    setAdminOpen(true);
    window.scrollTo(0, 0);
    await showAdmin(els.admin, { slug, user: state.user });
    return;
  }
  if (isProfileSlug(slug)) {
    hideAdmin();
    hideAlerts();
    stopPlaying();
    setAdminOpen(true);
    window.scrollTo(0, 0);
    await showProfile(els.admin, {
      slug,
      user: state.user,
      onUserChange: (user) => {
        state.user = user;
        auth.refresh(user);
      },
    });
    return;
  }
  if (isAlertsSlug(slug)) {
    hideAdmin();
    hideProfile();
    stopPlaying();
    setAdminOpen(true);
    window.scrollTo(0, 0);
    await showAlerts(els.admin, { slug, user: state.user, onShowOnMap: showAlertOnMap });
    return;
  }
  // The front door in plain words, for everyone who has not chosen the map.
  if (slug === "" && simpleHome()) {
    hideAdmin();
    hideAlerts();
    hideProfile();
    stopPlaying();
    setAdminOpen(true);
    window.scrollTo(0, 0);
    // It needs the county shapes; load() routes again once they are in.
    if (!state.counties) return;
    await showSimple(els.admin, {
      user: state.user,
      counties: state.counties.features,
      alerts: state.alerts,
      onDetailed: openDetailed,
    });
    return;
  }
  if (state.adminOpen) {
    setAdminOpen(false);
    // An administrator may have issued or cancelled some on the alerts page.
    await loadAlerts();
  }
  // The map views need the counties; load() routes again once they are in.
  if (!state.counties) return;
  const focus = state.focusRegions.find((f) => f.slug === slug);
  // A link saved before a region was renamed: swap in its new slug, without
  // a second history entry. The hashchange routes again.
  const renamed = !focus && state.focusRegions.find((f) => f.former_slugs?.includes(slug));
  if (renamed) {
    location.replace(`#/${renamed.slug}`);
    return;
  }
  try {
    if (focus) {
      await showRegion(focus);
    } else {
      // An unknown slug opens the front door.
      await showOverview(overviewScope(slug) ?? "focus");
    }
  } catch (error) {
    console.error(error);
    setStatus(error.message, true);
  }
}

async function load() {
  // Before the data, so the analytics page works even if the map's data
  // does not load. route() leaves the map views until it has.
  window.addEventListener("hashchange", route);
  initNavMenu(
    document.querySelector(".app__header"),
    document.getElementById("nav-toggle"),
    document.getElementById("app-actions"),
    document.getElementById("auth"),
  );

  // Before the data, so an administrator can still sign in and rescore when
  // there are no scores to show yet.
  auth = initAuth(document.getElementById("auth"), {
    // Scores are cached per view; drop them so the redraw fetches the new ones.
    onRescored: async () => {
      resetAdmin();
      state.countyCache.clear();
      state.regionCache.clear();
      state.gridCache.clear();
      state.contextCache.clear();
      // A rescore may have added today to the days on offer.
      state.scoreDates = await fetchScoreDates().catch(() => state.scoreDates);
      return route();
    },
    onUserChange: (user) => {
      state.user = user;
      // The detail panel's "Get flood alerts" depends on who is signed in.
      if (state.selectedRegionId !== null && !state.adminOpen) renderDetail(state.selectedRegionId);
      if (!user && isProfileSlug(currentSlug())) location.hash = "#/";
      if (!user) resetAdmin();
      // The front door depends on who is here (viewmode.js).
      if (isPageSlug(currentSlug()) || currentSlug() === "") route();
    },
  });

  els.viewToggle.addEventListener("click", () => {
    const onSimple = currentSlug() === "" && simpleHome();
    if (onSimple) {
      openDetailed();
      return;
    }
    chooseView("simple");
    if (currentSlug() === "") route();
    else location.hash = "";
  });

  // A sign-in from an earlier visit, or from before the trip to Google and
  // back, is checked with the API before the header shows it.
  sessionChecked = restoreSession().then((user) => {
    if (user) auth.setUser(user);
  });

  // A page shown in place of the map (sign-in, alerts, profile, analytics)
  // needs none of the map's data, so it does not wait for the counties. A
  // restored sign-in has already routed it (onUserChange); otherwise route.
  if (isPageSlug(currentSlug())) {
    sessionChecked.then(() => {
      if (!state.user) route();
    });
  }

  try {
    setStatus("Loading counties and risk scores…");

    const [counties, focusRegions, outlookMonths, scoreDates, gridDates, forecast] =
      await Promise.all([
        fetchRegions(),
        fetchFocusRegions(),
        // The outlook, the earlier days and the forecast are extras: without
        // them the dashboard still shows today.
        fetchOutlookMonths().catch(() => []),
        fetchScoreDates().catch(() => []),
        fetchGridDates().catch(() => []),
        fetchForecastDays().catch(() => ({ issued_on: null, days: [] })),
      ]);
    state.forecastIssued = forecast.issued_on;
    state.forecastDays = forecast.days;
    // Before the first view, so its badges and detail boxes have them.
    await loadAlerts();

    state.counties = counties;
    state.focusRegions = focusRegions;
    state.outlookMonths = outlookMonths;
    state.scoreDates = scoreDates;
    state.gridDates = gridDates;
    for (const focus of focusRegions) {
      for (const county of focus.counties) state.focusByCountyId.set(county.id, focus);
    }

    console.info(
      `FloodScout: ${counties.features.length} counties, ${focusRegions.length} focus regions, from ${API_BASE_URL}`,
    );

    initSearch();
    initReport();
    // A page already on screen is left alone: redrawing a form would wipe
    // what has been typed into it. The map draws when the page is left.
    if (!isPageSlug(currentSlug())) await route();
  } catch (error) {
    console.error(error);
    setStatus(error.message, true);
  }
}

// The stylesheets above are in by now: swap the splash in index.html for the app.
document.documentElement.classList.add("is-ready");
load();
