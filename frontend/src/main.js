/**
 * FloodScout dashboard — the Client Tier.
 *
 * Read-only. Two requests on load: the counties as GeoJSON, and the latest
 * score for every county in one go. Nothing here knows how a score was
 * produced, only what the API says produced it.
 */

import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "./style.css";

import { API_BASE_URL, fetchLatestScores, fetchRegions } from "./api.js";
import {
  CATEGORY_ORDER,
  colourFor,
  labelFor,
  prefersDark,
} from "./risk.js";

const KENYA_CENTRE = [0.25, 37.9];
const KENYA_ZOOM = 6;

const DARK = prefersDark();

// Plain OpenStreetMap tiles, desaturated to grey by a CSS filter on
// .leaflet-tile-pane (see style.css). A neutral basemap is a choropleth
// requirement rather than a style choice — on full-colour tiles the county
// fills compete with roads, landcover and water for the reader's attention.
// OSM is used because it needs no API key; Carto's "light_all"/"dark_all"
// now watermark every tile with API KEY REQUIRED unless you register.
const BASEMAP = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";

const els = {
  status: document.getElementById("status"),
  provenance: document.getElementById("provenance"),
  detail: document.getElementById("detail"),
  legend: document.getElementById("legend"),
  tableBody: document.getElementById("table-body"),
};

const state = {
  scoresByRegion: new Map(),
  layersByRegion: new Map(),
  rowsByRegion: new Map(),
  selectedRegionId: null,
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

function swatch(category) {
  const span = document.createElement("span");
  span.className = "legend__swatch";
  span.style.background = colourFor(category, DARK);
  return span;
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
  els.provenance.hidden = false;
  els.provenance.replaceChildren();

  const label = document.createElement("strong");
  label.textContent = versions.join(", ");

  if (looksLikePlaceholder) {
    els.provenance.append(
      "Scores produced by ",
      label,
      " — a documented placeholder, not a trained model. Not for operational use.",
    );
  } else {
    els.provenance.append("Scores produced by ", label, ".");
  }
}

function renderLegend(scores) {
  const counts = new Map();
  for (const score of scores) {
    counts.set(score.risk_category, (counts.get(score.risk_category) ?? 0) + 1);
  }

  els.legend.replaceChildren();
  for (const category of CATEGORY_ORDER) {
    const item = document.createElement("li");
    item.className = "legend__item";

    const label = document.createElement("span");
    label.className = "legend__label";
    label.textContent = labelFor(category);

    const count = document.createElement("span");
    count.className = "legend__count";
    count.textContent = `${counts.get(category) ?? 0}`;

    item.append(swatch(category), label, count);
    els.legend.append(item);
  }
}

function renderDetail(regionId) {
  const score = state.scoresByRegion.get(regionId);
  const layer = state.layersByRegion.get(regionId);
  const countyName =
    score?.county_name ?? layer?.feature?.properties?.county_name ?? "Unknown county";

  els.detail.replaceChildren();

  const heading = document.createElement("p");
  heading.className = "detail__name";
  heading.textContent = countyName;
  els.detail.append(heading);

  const badge = document.createElement("p");
  badge.className = "detail__badge";
  const badgeSwatch = document.createElement("span");
  badgeSwatch.className = "detail__swatch";
  badgeSwatch.style.background = colourFor(score?.risk_category, DARK);
  badge.append(badgeSwatch, labelFor(score?.risk_category));
  els.detail.append(badge);

  const rows = document.createElement("dl");
  rows.className = "detail__rows";

  const entries = score
    ? [
        ["Predicted value", formatValue(score.predicted_value)],
        ["Prediction date", formatDate(score.prediction_date)],
        ["Model version", score.model_version ?? "—"],
      ]
    : [["Status", "No risk score recorded for this county."]];

  for (const [term, value] of entries) {
    const dt = document.createElement("dt");
    dt.textContent = term;
    const dd = document.createElement("dd");
    dd.textContent = value;
    rows.append(dt, dd);
  }

  els.detail.append(rows);
}

function renderTable(features) {
  els.tableBody.replaceChildren();
  state.rowsByRegion.clear();

  const sorted = [...features].sort((a, b) =>
    (a.properties?.county_name ?? "").localeCompare(b.properties?.county_name ?? ""),
  );

  for (const feature of sorted) {
    const regionId = regionIdOf(feature);
    const score = state.scoresByRegion.get(regionId);

    const row = document.createElement("tr");
    row.tabIndex = 0;
    row.setAttribute("aria-selected", "false");

    const nameCell = document.createElement("td");
    const nameWrap = document.createElement("span");
    nameWrap.className = "table__county";
    nameWrap.append(swatch(score?.risk_category));
    nameWrap.append(feature.properties?.county_name ?? "—");
    nameCell.append(nameWrap);

    const categoryCell = document.createElement("td");
    categoryCell.textContent = labelFor(score?.risk_category);

    const valueCell = document.createElement("td");
    valueCell.className = "table__num";
    valueCell.textContent = formatValue(score?.predicted_value);

    row.append(nameCell, categoryCell, valueCell);
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

function styleFor(feature) {
  const score = state.scoresByRegion.get(regionIdOf(feature));
  return {
    fillColor: colourFor(score?.risk_category, DARK),
    fillOpacity: 0.78,
    // A hairline in the surface colour keeps neighbouring fills from bleeding
    // into one another without drawing attention to the borders themselves.
    color: getComputedStyle(document.documentElement)
      .getPropertyValue("--polygon-stroke")
      .trim(),
    weight: 1,
  };
}

function select(regionId, { pan = false } = {}) {
  state.selectedRegionId = regionId;

  for (const [id, layer] of state.layersByRegion) {
    const selected = id === regionId;
    layer.setStyle({
      weight: selected ? 3 : 1,
      color: selected
        ? getComputedStyle(document.documentElement)
            .getPropertyValue("--polygon-stroke-focus")
            .trim()
        : getComputedStyle(document.documentElement)
            .getPropertyValue("--polygon-stroke")
            .trim(),
    });
    if (selected) layer.bringToFront();
  }

  for (const [id, row] of state.rowsByRegion) {
    row.setAttribute("aria-selected", String(id === regionId));
  }

  renderDetail(regionId);

  const layer = state.layersByRegion.get(regionId);
  if (pan && layer) {
    map.fitBounds(layer.getBounds(), { padding: [24, 24], maxZoom: 8 });
  }
}

/* ------------------------------------------------------------------ map */

const map = L.map("map", {
  center: KENYA_CENTRE,
  zoom: KENYA_ZOOM,
  zoomControl: true,
});

L.tileLayer(BASEMAP, {
  maxZoom: 19,
  attribution:
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors | Boundaries: geoBoundaries',
}).addTo(map);

async function load() {
  try {
    setStatus("Loading counties and risk scores…");

    const [regions, scores] = await Promise.all([fetchRegions(), fetchLatestScores()]);

    state.scoresByRegion = new Map(scores.map((score) => [score.region, score]));

    const layer = L.geoJSON(regions, {
      style: styleFor,
      onEachFeature: (feature, featureLayer) => {
        const regionId = regionIdOf(feature);
        state.layersByRegion.set(regionId, featureLayer);

        featureLayer.bindTooltip(feature.properties?.county_name ?? "", {
          sticky: true,
        });
        featureLayer.on({
          click: () => select(regionId),
          mouseover: () => featureLayer.setStyle({ fillOpacity: 0.95 }),
          mouseout: () => featureLayer.setStyle({ fillOpacity: 0.78 }),
        });
      },
    }).addTo(map);

    if (layer.getLayers().length > 0) {
      map.fitBounds(layer.getBounds(), { padding: [16, 16] });
    }

    renderProvenance(scores);
    renderLegend(scores);
    renderTable(regions.features ?? []);

    const scored = regions.features.filter((f) =>
      state.scoresByRegion.has(regionIdOf(f)),
    ).length;
    setStatus(null);
    console.info(
      `FloodScout: ${regions.features.length} counties, ${scored} with a score, from ${API_BASE_URL}`,
    );
  } catch (error) {
    console.error(error);
    setStatus(error.message, true);
  }
}

load();
