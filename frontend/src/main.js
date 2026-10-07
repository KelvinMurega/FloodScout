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
 * Either view can show today's scores or the seasonal outlook for a month
 * ahead; the period bar switches between them.
 *
 * The view lives in the URL hash (#/coast), so it survives a reload and the
 * browser's back button leaves a region. Nothing here knows how a score was
 * produced, only what the API says produced it.
 */

import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "./style.css";

import {
  API_BASE_URL,
  fetchFocusRegions,
  fetchLatestScores,
  fetchLowerRiskNeighbours,
  fetchOutlook,
  fetchOutlookMonths,
  fetchRegions,
} from "./api.js";
import { initAuth } from "./auth.js";
import {
  CATEGORY_ORDER,
  advisoryFor,
  colourFor,
  labelFor,
  prefersDark,
} from "./risk.js";

const KENYA_CENTRE = [0.25, 37.9];
const KENYA_ZOOM = 6;

const DARK = prefersDark();

// No basemap. The region polygons *are* the map: nothing outside Kenya is
// drawn, and the view is locked to whatever is on screen once it loads.
const FILL_OPACITY = 1;
const HOVER_WEIGHT = 2.5;

// Approximate label width per character at the label font size (px). Used to
// decide whether a region is wide enough on screen to carry its name.
const LABEL_CHAR_PX = 6.2;

// Outline for sub-counties where relocation is advised: a second, non-colour
// cue alongside the fill, so the areas stand out to colour-blind readers too.
const RELOCATE_DASH = "5 4";

// Scores older than this are flagged on screen. The scheduler refreshes
// them daily, so two days means a refresh was missed.
const STALE_AFTER_DAYS = 2;

const els = {
  status: document.getElementById("status"),
  provenance: document.getElementById("provenance"),
  regionBar: document.getElementById("region-bar"),
  periodBar: document.getElementById("period-bar"),
  regionIntro: document.getElementById("region-intro"),
  relocation: document.getElementById("relocation"),
  detailHeading: document.getElementById("detail-heading"),
  detail: document.getElementById("detail"),
  legend: document.getElementById("legend"),
  tableHeading: document.getElementById("table-heading"),
  tableHead: document.getElementById("table-head"),
  tableBody: document.getElementById("table-body"),
};

const state = {
  // Loaded once.
  counties: null,
  focusRegions: [],
  // Months a seasonal outlook exists for: [{ month, issued_on }].
  outlookMonths: [],
  // null is today's scores; otherwise the outlook month, as the API names it.
  period: null,
  // County scores per period, fetched on first use.
  countyCache: new Map(),
  focusByCountyId: new Map(),
  // Per region, fetched on first open.
  regionCache: new Map(),
  // The current view.
  focus: null,
  scoresByRegion: new Map(),
  layersByRegion: new Map(),
  labelsByRegion: new Map(),
  rowsByRegion: new Map(),
  selectedRegionId: null,
  viewLayers: [],
};

/* ---------------------------------------------------------------- utils */

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

function isOutlook() {
  return state.period !== null;
}

/** Scores for the current period: today's, or the outlook for its month. */
function fetchScores(params) {
  return isOutlook()
    ? fetchOutlook({ ...params, month: state.period })
    : fetchLatestScores(params);
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
});

map.attributionControl.addAttribution(
  'Boundaries: <a href="https://www.geoboundaries.org/">geoBoundaries</a>',
);

// Panes, bottom to top. The outline stroke is wide, but the opaque fills
// above it cover the inner half of every shared border, so only the half
// outside the shape's edge survives — a clean outer outline without having
// to dissolve the polygons into one.
map.createPane("context").style.zIndex = 340;
map.createPane("outline").style.zIndex = 350;
map.createPane("countyLines").style.zIndex = 420;
map.createPane("labels").style.zIndex = 450;
map.getPane("countyLines").style.pointerEvents = "none";
map.getPane("labels").style.pointerEvents = "none";

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
  map.setMinZoom(0);
  refit = () => {
    map.setMaxBounds(null);
    map.setMinZoom(map.getBoundsZoom(bounds, false, L.point(16, 16)));
    map.fitBounds(bounds, { padding: [16, 16], animate: false });
    // The limit is measured from the fitted view, not from `bounds`: a limit
    // smaller than the view pins the map in place and it cannot be dragged.
    map.setMaxBounds(map.getBounds().pad(PAN_MARGIN));
  };
  refit();
  map.options.maxBoundsViscosity = 1;
}

map.on("resize", () => refit?.());
map.on("zoomend", updateLabelVisibility);

function clearView() {
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
  const score = state.scoresByRegion.get(regionId);
  return {
    fillColor: colourFor(score?.risk_category, DARK),
    fillOpacity: FILL_OPACITY,
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
  if (isRegionView() && advisoryOf(regionId) === "relocate") {
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

        const score = state.scoresByRegion.get(regionId);
        featureLayer.bindTooltip(`${name} — ${labelFor(score?.risk_category)}`, {
          sticky: true,
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

function drawOutline(features) {
  addToView(
    L.geoJSON(
      { type: "FeatureCollection", features },
      {
        pane: "outline",
        interactive: false,
        style: { color: cssVar("--country-outline"), weight: 5, fill: false },
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
  } else {
    els.provenance.append(`Scores for ${formatDate(latest)}, produced by `, label, ".");
    const age = daysSince(latest);
    if (age > STALE_AFTER_DAYS) {
      els.provenance.append(` These are ${age} days old: the daily refresh has not run.`);
    }
  }
}

/** Whole days from an ISO date to now; 0 when the date is missing or ahead. */
function daysSince(isoString) {
  const then = new Date(isoString).valueOf();
  if (Number.isNaN(then)) return 0;
  return Math.max(0, Math.floor((Date.now() - then) / 86_400_000));
}

function renderRegionBar() {
  els.regionBar.replaceChildren();
  const current = state.focus?.slug ?? "";

  const options = [{ slug: "", name: "All Kenya" }, ...state.focusRegions];
  for (const option of options) {
    const button = el("button", "region-bar__button", option.name);
    button.type = "button";
    button.setAttribute("aria-pressed", String(option.slug === current));
    button.addEventListener("click", () => {
      location.hash = option.slug ? `#/${option.slug}` : "";
    });
    els.regionBar.append(button);
  }
}

function renderPeriodBar() {
  els.periodBar.hidden = state.outlookMonths.length === 0;
  els.periodBar.replaceChildren();

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
    button.setAttribute("aria-pressed", String(option.period === state.period));
    button.addEventListener("click", () => {
      state.period = option.period;
      route();
    });
    els.periodBar.append(button);
  }
}

function renderLegend(scores) {
  const counts = new Map();
  for (const score of scores) {
    counts.set(score.risk_category, (counts.get(score.risk_category) ?? 0) + 1);
  }

  els.legend.replaceChildren();
  for (const category of CATEGORY_ORDER) {
    const item = el("li", "legend__item");
    item.append(
      swatch(category),
      el("span", "legend__label", labelFor(category)),
      el("span", "legend__count", `${counts.get(category) ?? 0}`),
    );
    els.legend.append(item);
  }

  if (isRegionView() && !isOutlook()) {
    const item = el("li", "legend__item");
    item.append(
      el("span", "legend__swatch legend__swatch--dashed"),
      el("span", "legend__label", "Dashed outline: relocation advised"),
    );
    els.legend.append(item);
  }
}

function renderRegionIntro() {
  if (!isRegionView()) {
    els.regionIntro.hidden = true;
    return;
  }
  const focus = state.focus;
  els.regionIntro.hidden = false;
  els.regionIntro.replaceChildren();

  const back = el("button", "link-button", "← All Kenya");
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
  // No relocation advice from an outlook: it is a chance, months ahead.
  if (!isRegionView() || isOutlook()) {
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
  } else {
    entries = [
        ...(isRegionView() ? [["County", score.county_name]] : []),
        ...(isRegionView() ? [["Advisory", advisoryFor(score.relocation_advisory)]] : []),
        // A placeholder score has no usual chance to compare with.
        ...(typeof score.times_usual === "number"
          ? [["Chance of a large flood", formatTimesUsual(score.times_usual)]]
          : []),
        ["Model output", formatValue(score.predicted_value)],
        ["Prediction date", formatDate(score.prediction_date)],
        ["Model version", score.model_version ?? "—"],
    ];
  }

  for (const [term, value] of entries) {
    rows.append(el("dt", null, term), el("dd", null, value));
  }
  els.detail.append(rows);

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
  } else if (score && !isOutlook() && score.relocation_advisory !== "none") {
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
        : "Select a county on the map, or pick a region above to see its sub-counties.",
    ),
  );
}

function renderTable(features) {
  const region = isRegionView();
  els.tableBody.replaceChildren();
  state.rowsByRegion.clear();

  els.tableHeading.firstChild.textContent = region ? "All sub-counties " : "All counties ";
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

    row.addEventListener("click", () => select(regionId, { pan: true }));
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        select(regionId, { pan: true });
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

/* ---------------------------------------------------------------- views */

function loadCountyScores() {
  const period = state.period;
  if (!state.countyCache.has(period)) {
    state.countyCache.set(
      period,
      fetchScores({ level: "county" }).catch((error) => {
        state.countyCache.delete(period); // let a retry refetch
        throw error;
      }),
    );
  }
  return state.countyCache.get(period);
}

async function showOverview() {
  const slug = currentSlug();
  const period = state.period;
  const scores = await loadCountyScores();
  // Another view may have been chosen while this one loaded.
  if (currentSlug() !== slug || state.period !== period) return;

  state.focus = null;
  clearView();
  state.scoresByRegion = new Map(scores.map((score) => [score.region, score]));

  const features = state.counties.features;
  drawOutline(features);
  const layer = drawChoropleth(features);
  if (layer.getLayers().length > 0) lockTo(layer.getBounds());
  updateLabelVisibility();

  renderProvenance(scores);
  renderRegionBar();
  renderPeriodBar();
  renderRegionIntro();
  renderRelocation(scores);
  els.detailHeading.textContent = "County";
  renderEmptyDetail();
  renderLegend(scores);
  renderTable(features);
  setStatus(null);
}

async function loadRegion(focus) {
  const key = `${focus.slug}|${state.period}`;
  if (!state.regionCache.has(key)) {
    const params = { level: "sub_county", focus_region: focus.slug };
    state.regionCache.set(
      key,
      Promise.all([fetchRegions(params), fetchScores(params)]).catch((error) => {
        state.regionCache.delete(key); // let a retry refetch
        throw error;
      }),
    );
  }
  return state.regionCache.get(key);
}

async function showRegion(focus) {
  setStatus(`Loading ${focus.name} sub-counties…`);
  const period = state.period;
  const [subCounties, scores] = await loadRegion(focus);
  // Another view may have been chosen while this one loaded.
  if (currentSlug() !== focus.slug || state.period !== period) return;

  state.focus = focus;
  clearView();
  state.scoresByRegion = new Map(scores.map((score) => [score.region, score]));

  const focusCountyIds = new Set(focus.counties.map((c) => c.id));
  const focusCounties = state.counties.features.filter((f) =>
    focusCountyIds.has(regionIdOf(f)),
  );
  const otherCounties = state.counties.features.filter(
    (f) => !focusCountyIds.has(regionIdOf(f)),
  );

  // The rest of Kenya, faint and inert, so the region keeps its place.
  addToView(
    L.geoJSON(
      { type: "FeatureCollection", features: otherCounties },
      {
        pane: "context",
        interactive: false,
        style: {
          fillColor: cssVar("--context-fill"),
          fillOpacity: 1,
          color: cssVar("--polygon-stroke"),
          weight: 1,
        },
      },
    ),
  );
  // Outline and bounds come from the sub-counties themselves, not the
  // county shapes: the two boundary sets were drawn by different agencies
  // (RCMRD and IEBC) and do not line up exactly, so a county outline would
  // leave slivers of gap and overhang along the region's edge.
  drawOutline(subCounties.features);
  const choropleth = drawChoropleth(subCounties.features);
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
  renderRegionIntro();
  renderRelocation(scores);
  els.detailHeading.textContent = "Sub-county";
  renderEmptyDetail();
  renderLegend(scores);
  renderTable(subCounties.features);
  setStatus(null);

  if (subCounties.features.length === 0) {
    setStatus(
      "No sub-counties loaded for this region — run `manage.py seed_regions --level sub_county`.",
      true,
    );
  }
}

function currentSlug() {
  return location.hash.replace(/^#\/?/, "");
}

async function route() {
  const slug = currentSlug();
  const focus = state.focusRegions.find((f) => f.slug === slug);
  try {
    if (focus) {
      await showRegion(focus);
    } else {
      await showOverview();
    }
  } catch (error) {
    console.error(error);
    setStatus(error.message, true);
  }
}

async function load() {
  // Before the data, so an administrator can still sign in and rescore when
  // there are no scores to show yet.
  initAuth(document.getElementById("auth"), {
    // Scores are cached per view; drop them so the redraw fetches the new ones.
    onRescored: () => {
      state.countyCache.clear();
      state.regionCache.clear();
      return route();
    },
  });

  try {
    setStatus("Loading counties and risk scores…");

    const [counties, focusRegions, outlookMonths] = await Promise.all([
      fetchRegions(),
      fetchFocusRegions(),
      // The outlook is an extra: without it the dashboard still shows today.
      fetchOutlookMonths().catch(() => []),
    ]);

    state.counties = counties;
    state.focusRegions = focusRegions;
    state.outlookMonths = outlookMonths;
    for (const focus of focusRegions) {
      for (const county of focus.counties) state.focusByCountyId.set(county.id, focus);
    }

    console.info(
      `FloodScout: ${counties.features.length} counties, ${focusRegions.length} focus regions, from ${API_BASE_URL}`,
    );

    window.addEventListener("hashchange", route);
    await route();
  } catch (error) {
    console.error(error);
    setStatus(error.message, true);
  }
}

load();
