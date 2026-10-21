/**
 * The map under and over the risk colours: basemaps and context layers.
 *
 * The risk polygons used to be the whole map, floating on a blank page.
 * A flood map is read against the ground it covers, so the regions now sit
 * on a choice of basemaps, with layers a reader can turn on over them:
 *
 *   Basemaps   Map (Esri Light Gray Canvas, or Dark Gray in dark mode), a
 *              basemap made for thematic maps, with place names drawn
 *              above the risk colours
 *              Satellite (Esri World Imagery), with place names
 *              Satellite today (NASA VIIRS true colour, the day on screen)
 *              Terrain (OpenTopoMap)
 *   Overlays   Rivers (OpenStreetMap, the long named ones)
 *              100-year flood zone (Copernicus GloFAS)
 *              Past floods (Dartmouth Flood Observatory, 1988 to 2021)
 *              Satellite rain (NASA GPM IMERG, the day on screen)
 *
 * All are free and need no key. The rivers and the flood zone are static
 * files built by `manage.py build_map_layers` into public/layers/; the past
 * floods come from the API. The choice is remembered in this browser.
 */

import L from "leaflet";

import { floodRadius, gibsDate } from "./layerdates.js";

const STORAGE_KEY = "floodscout.layers";

// NASA GIBS publishes each day's layer once processed. IMERG's early run
// lags about two days; the VIIRS image is usually out the next day.
const GIBS = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best";
const IMERG_LAG_DAYS = 2;

// The basemaps' own place names start at this zoom. Further out, the
// dashboard's county labels already name the places, and the two sets of
// names would print over each other.
const PLACE_NAMES_FROM_ZOOM = 8;
const VIIRS_LAG_DAYS = 1;

const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services";
const CANVAS_ATTRIBUTION = "Basemap &copy; Esri, HERE, Garmin, &copy; OpenStreetMap contributors";
const ESRI_ATTRIBUTION =
  "Imagery &copy; Esri, Maxar, Earthstar Geographics, and the GIS User Community";
const GIBS_ATTRIBUTION =
  'Satellite: <a href="https://earthdata.nasa.gov/gibs">NASA GIBS</a>';

function readChoice() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null");
  } catch {
    return null;
  }
}

function saveChoice(choice) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(choice));
  } catch {
    // Private windows and blocked storage: the choice lasts this visit only.
  }
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function formatDay(iso) {
  return new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function floodPopup(feature) {
  const p = feature.properties;
  const box = el("div", "flood-popup");
  const began = formatDay(p.began);
  const ended = formatDay(p.ended);
  box.append(
    el("strong", null, `Recorded flood, ${new Date(p.began).getFullYear()}`),
    el("div", null, began === ended ? began : `${began} to ${ended}`),
    el("div", null, p.main_cause ? `Cause: ${p.main_cause}` : "Cause not recorded"),
  );
  const toll = [];
  if (p.dead) toll.push(`${p.dead.toLocaleString()} dead`);
  if (p.displaced) toll.push(`${p.displaced.toLocaleString()} displaced`);
  if (toll.length) box.append(el("div", "flood-popup__toll", toll.join(", ")));
  box.append(
    el(
      "div",
      "flood-popup__note",
      `About ${p.area_km2.toLocaleString()} km² affected. Dartmouth Flood Observatory event ${feature.id}; ` +
        "the point is the centre DFO recorded, not where the water was deepest.",
    ),
  );
  return box;
}

/**
 * Add the basemaps, the overlays and their control to `map`.
 *
 * @param {L.Map} map
 * @param {{ dark: boolean, loadFloodEvents: () => Promise<object>,
 *           onChange?: (active: Set<string>) => void }} options
 * @returns {{ setDate(iso: string|null): void, active: Set<string> }}
 */
export function initLayers(map, { dark, loadFloodEvents, onChange = () => {} }) {
  // Panes above the risk fills (overlayPane, 400) so a layer turned on is
  // seen through the colours instead of under them.
  map.createPane("basemapLabels").style.zIndex = 430;
  map.createPane("water").style.zIndex = 415;
  map.createPane("satelliteRain").style.zIndex = 412;
  for (const pane of ["basemapLabels", "water", "satelliteRain"]) {
    map.getPane(pane).style.pointerEvents = "none";
  }

  // CARTO's basemaps were the first choice, but they now answer a browser
  // with an "API key required" tile. Esri's canvases need no key.
  const canvas = dark ? "World_Dark_Gray" : "World_Light_Gray";
  const streets = L.layerGroup([
    L.tileLayer(`${ESRI}/Canvas/${canvas}_Base/MapServer/tile/{z}/{y}/{x}`, {
      attribution: CANVAS_ATTRIBUTION,
      maxZoom: 16,
    }),
    L.tileLayer(`${ESRI}/Canvas/${canvas}_Reference/MapServer/tile/{z}/{y}/{x}`, {
      maxZoom: 16,
      minZoom: PLACE_NAMES_FROM_ZOOM,
      pane: "basemapLabels",
    }),
  ]);
  const satellite = L.layerGroup([
    L.tileLayer(`${ESRI}/World_Imagery/MapServer/tile/{z}/{y}/{x}`, {
      attribution: ESRI_ATTRIBUTION,
      maxZoom: 18,
    }),
    L.tileLayer(`${ESRI}/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}`, {
      maxZoom: 18,
      minZoom: PLACE_NAMES_FROM_ZOOM,
      pane: "basemapLabels",
    }),
  ]);
  const viirsUrl = (iso) =>
    `${GIBS}/VIIRS_SNPP_CorrectedReflectance_TrueColor/default/${gibsDate(iso, VIIRS_LAG_DAYS)}/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg`;
  const viirs = L.tileLayer(viirsUrl(null), {
    attribution: `${GIBS_ATTRIBUTION} (VIIRS true colour)`,
    maxNativeZoom: 9,
    maxZoom: 13,
  });
  const terrain = L.tileLayer("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", {
    attribution:
      'Map data &copy; OpenStreetMap contributors, SRTM | Style &copy; <a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA)',
    maxZoom: 17,
  });

  const imergUrl = (iso) =>
    `${GIBS}/IMERG_Precipitation_Rate/default/${gibsDate(iso, IMERG_LAG_DAYS)}/GoogleMapsCompatible_Level6/{z}/{y}/{x}.png`;
  const rain = L.tileLayer(imergUrl(null), {
    attribution: `${GIBS_ATTRIBUTION} (GPM IMERG)`,
    pane: "satelliteRain",
    maxNativeZoom: 6,
    maxZoom: 13,
    opacity: 0.85,
  });

  const rivers = L.layerGroup();
  const floodZone = L.layerGroup();
  const pastFloods = L.layerGroup();

  const baseLayers = {
    Map: streets,
    Satellite: satellite,
    "Satellite today (NASA)": viirs,
    Terrain: terrain,
  };
  const overlays = {
    Rivers: rivers,
    "100-year flood zone": floodZone,
    "Past floods (1988–2021)": pastFloods,
    "Satellite rain (NASA)": rain,
  };

  // Static files, loaded once when first turned on.
  let manifest = null;
  const loadManifest = () => {
    manifest ??= fetch(`${import.meta.env.BASE_URL}layers/layers.json`).then((r) => {
      if (!r.ok) throw new Error("Map layers not built — run `manage.py build_map_layers`.");
      return r.json();
    });
    return manifest;
  };
  const loaders = new Map([
    [
      rivers,
      async () => {
        const meta = await loadManifest();
        const data = await fetch(`${import.meta.env.BASE_URL}layers/${meta.rivers.file}`).then((r) =>
          r.json(),
        );
        rivers.addLayer(
          L.geoJSON(data, {
            pane: "water",
            interactive: false,
            style: { color: dark ? "#6cb6ff" : "#1f78d1", weight: 1.6, opacity: 0.9 },
          }),
        );
      },
    ],
    [
      floodZone,
      async () => {
        const meta = await loadManifest();
        const zone = meta.flood_zone_100yr;
        floodZone.addLayer(
          L.imageOverlay(`${import.meta.env.BASE_URL}layers/${zone.file}`, zone.bounds, {
            pane: "water",
            opacity: 0.9,
            attribution: "Flood zone: Copernicus GloFAS (CC BY 4.0)",
          }),
        );
      },
    ],
    [
      pastFloods,
      async () => {
        const events = await loadFloodEvents();
        pastFloods.addLayer(
          L.geoJSON(events, {
            pointToLayer: (feature, latlng) =>
              L.circleMarker(latlng, {
                radius: floodRadius(feature.properties.displaced),
                color: dark ? "#0d0d0d" : "#ffffff",
                weight: 1.5,
                fillColor: "#5b2c83",
                fillOpacity: 0.75,
              }),
            onEachFeature: (feature, layer) => layer.bindPopup(() => floodPopup(feature)),
          }),
        );
        pastFloods.getLayers()[0]?.bringToFront?.();
      },
    ],
  ]);
  const loaded = new Set();

  async function ensureLoaded(layer) {
    const load = loaders.get(layer);
    if (!load || loaded.has(layer)) return;
    loaded.add(layer);
    try {
      await load();
    } catch (error) {
      loaded.delete(layer);
      console.error(error);
    }
  }

  const active = new Set();
  const nameOf = (layer) =>
    Object.entries({ ...baseLayers, ...overlays }).find(([, l]) => l === layer)?.[0];

  // What was chosen last time, or the plain map with the rivers on.
  const saved = readChoice();
  const base = baseLayers[saved?.base] ?? streets;
  base.addTo(map);
  for (const name of saved?.overlays ?? ["Rivers"]) {
    const layer = overlays[name];
    if (!layer) continue;
    layer.addTo(map);
    active.add(name);
    ensureLoaded(layer);
  }
  let currentBase = nameOf(base);

  const remember = () => saveChoice({ base: currentBase, overlays: [...active] });
  map.on("baselayerchange", (event) => {
    currentBase = event.name;
    remember();
  });
  map.on("overlayadd", (event) => {
    active.add(event.name);
    ensureLoaded(event.layer);
    remember();
    onChange(active);
  });
  map.on("overlayremove", (event) => {
    active.delete(event.name);
    remember();
    onChange(active);
  });

  L.control.layers(baseLayers, overlays, { position: "topright", collapsed: true }).addTo(map);
  L.control.scale({ position: "bottomleft", imperial: false }).addTo(map);

  return {
    active,
    /** Show the NASA layers for `iso` (the day on screen), or the latest. */
    setDate(iso) {
      viirs.setUrl(viirsUrl(iso));
      rain.setUrl(imergUrl(iso));
    },
  };
}

/** Legend lines for the overlays that are on. */
export function overlayLegend(active) {
  const lines = [];
  if (active.has("Rivers")) lines.push({ kind: "line", colour: "#1f78d1", label: "River" });
  if (active.has("100-year flood zone")) {
    lines.push({
      kind: "swatch",
      colour: "#3787d7",
      label: "100-year flood zone (darker is deeper; includes lakes)",
    });
  }
  if (active.has("Past floods (1988–2021)")) {
    lines.push({
      kind: "dot",
      colour: "#5b2c83",
      label: "Recorded flood; larger displaced more people",
    });
  }
  if (active.has("Satellite rain (NASA)")) {
    lines.push({
      kind: "swatch",
      colour: "linear-gradient(90deg, #2e9e3e, #b6e33a, #f7e83a)",
      label: "Satellite rain rate, NASA IMERG (about 2 days behind)",
    });
  }
  return lines;
}
