/**
 * The simple view (#/ for everyone, unless they chose the detailed map).
 *
 * One place at a time, in plain words: is flooding more likely than usual
 * here for the time of year, is heavy rain coming this week, is an alert in
 * force, and what to do. Beside it a map in three colours: all Kenya by
 * county, or, once a county is picked, that county by sub-county, with a
 * plain way back to Kenya to pick another. The words come from plain.js;
 * every fact is the API's.
 */

import L from "leaflet";

import {
  fetchFloodEvents,
  fetchForecast,
  fetchKmdWarnings,
  fetchLatestScores,
  fetchRegions,
  fetchScoreHistory,
} from "../api.js";
import { quickAlertControl } from "../alerts/quick.js";
import { prefersDark } from "../risk.js";
import { searchRegions } from "../search.js";
import {
  alertFor,
  everydayAdvice,
  groupAreas,
  kmdLine,
  mapBucket,
  pointInGeometry,
  seasonVerdict,
  subCountyAlerts,
  timeOfYear,
  weekRain,
} from "./plain.js";
import "./simple.css";

const PLACE_KEY = "floodscout.simple.place";

let map = null;
let render = 0;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function storedPlace() {
  try {
    return JSON.parse(localStorage.getItem(PLACE_KEY) ?? "null");
  } catch {
    return null;
  }
}

function keepPlace(place) {
  try {
    if (place) localStorage.setItem(PLACE_KEY, JSON.stringify(place));
    else localStorage.removeItem(PLACE_KEY);
  } catch {
    // Blocked storage: the place is forgotten when the page is left.
  }
}

function isoDaysAgo(days) {
  const date = new Date(Date.now() - days * 86_400_000);
  return date.toISOString().slice(0, 10);
}

function rowsOf(result) {
  return Array.isArray(result) ? result : result?.results ?? [];
}

function idOf(feature) {
  return feature.id ?? feature.properties.id;
}

function nameOf(feature) {
  return feature.properties.name ?? feature.properties.county_name;
}

function verdictOf(score) {
  return seasonVerdict(score?.times_season, score?.risk_category);
}

/** The newest score for one place: a county's from the list, a sub-county's from its history. */
async function scoreFor(place, countyScores) {
  if (place.level === "county") return countyScores.find((s) => s.region === place.id) ?? null;
  const history = await fetchScoreHistory(place.id, isoDaysAgo(7), isoDaysAgo(-1)).catch(() => []);
  return rowsOf(history)[0] ?? null;
}

export function hideSimple() {
  render += 1;
  map?.remove();
  map = null;
}

function button(className, text, onClick) {
  const node = el("button", className, text);
  node.type = "button";
  node.addEventListener("click", onClick);
  return node;
}

/**
 * Draw the simple view into `root`. `counties` are the county shapes the
 * dashboard loaded, `alerts` the alerts in force, `user` who is signed in.
 * `onDetailed(regionId?)` switches to the detailed map.
 */
export async function showSimple(root, { user, counties, alerts, onDetailed }) {
  hideSimple();
  const mine = render;
  const page = el("div", "simple");

  const head = el("header", "simple__head");
  const titles = el("div");
  titles.append(
    el("h1", "simple__title", "Floods where you are"),
    el("p", "simple__lede", "Plain answers for your area: is flooding more likely than usual, is heavy rain coming, and what to do."),
  );
  head.append(titles, button("auth__button", "See the detailed map", () => onDetailed()));

  const find = el("div", "simple__find");
  const label = el("label", "simple__label", "Find your area");
  const input = el("input", "alerts-form__input simple__input");
  input.type = "search";
  input.id = "simple-find";
  input.placeholder = "A county, for example Kisumu";
  input.autocomplete = "off";
  label.htmlFor = input.id;
  const matches = el("ul", "simple__matches");
  matches.hidden = true;
  find.append(label, input, matches);
  if (user?.home_region) {
    find.append(
      button("link-button simple__home", `Use where I live: ${user.home_region_name}`, () =>
        choose({ id: user.home_region, name: user.home_region_name, level: "sub_county" }),
      ),
    );
  }

  // The map and the details side by side on a wide screen, the map first on a phone.
  const body = el("div", "simple__body");
  const mapBox = el("section", "simple__map-box");
  const mapHead = el("div", "simple__map-head");
  const mapTitle = el("h2", "simple__map-title", "All of Kenya");
  const mapBack = button("auth__button simple__back", "← Back to the Kenya map", () => backToKenya());
  mapBack.hidden = true;
  mapHead.append(mapTitle, mapBack);
  const mapDiv = el("div", "simple__map");
  mapDiv.setAttribute("aria-label", "Map coloured by the chance of flooding against the usual for the time of year");
  const mapHint = el("p", "simple__muted simple__map-hint", "Tap a county, or zoom in, to see its areas.");
  mapBox.append(mapHead, mapDiv, legend(), mapHint);
  const card = el("section", "simple__card");
  card.setAttribute("aria-live", "polite");
  body.append(mapBox, card);

  const note = el(
    "p",
    "simple__note",
    "FloodScout is a university research project, not an official warning service. Follow the Kenya Meteorological Department and your county government. In an emergency call 999 or 112, or the Kenya Red Cross on 1199.",
  );
  page.append(head, find, body, note);
  root.replaceChildren(page);
  card.append(el("p", "simple__muted", "Loading today's readings…"));

  let countyScores = [];
  let areaScores = [];
  try {
    [countyScores, areaScores] = await Promise.all([
      fetchLatestScores({ level: "county" }).then(rowsOf),
      // Every sub-county's reading is light (a row each); their shapes load per county as needed.
      fetchLatestScores({ level: "sub_county" }).then(rowsOf).catch(() => []),
    ]);
  } catch (error) {
    if (mine === render) card.replaceChildren(el("p", "simple__muted", `Could not load today's readings: ${error.message}`));
    return;
  }
  if (mine !== render) return;
  const byCounty = new Map(countyScores.map((s) => [s.region, s]));
  const byArea = new Map(areaScores.map((s) => [s.region, s]));
  const countyByName = new Map(countyScores.map((s) => [s.region_name, s.region]));

  const entries = counties.map((f) => ({ id: idOf(f), name: nameOf(f), level: "county" }));
  input.addEventListener("input", () => {
    const found = searchRegions(entries, input.value, 6);
    matches.replaceChildren(
      ...found.map((entry) => {
        const item = el("li");
        item.append(button("simple__match", entry.name, () => choose(entry)));
        return item;
      }),
    );
    matches.hidden = found.length === 0;
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      const [first] = searchRegions(entries, input.value, 1);
      if (first) choose(first);
    }
  });

  const shapesCache = new Map();
  const loadShapes = (countyId) => {
    if (!shapesCache.has(countyId)) {
      shapesCache.set(
        countyId,
        fetchRegions({ level: "sub_county", parent: countyId })
          .then((geo) => geo.features ?? [])
          .catch(() => {
            shapesCache.delete(countyId); // let a later look retry
            return [];
          }),
      );
    }
    return shapesCache.get(countyId);
  };
  // Each pick or "back" is numbered; a slower earlier one never draws over a later one.
  let pick = 0;
  let shownCounty = null;
  const current = (n) => n === pick && mine === render;

  const kenyaMap = drawMap(mapDiv, {
    counties,
    byCounty,
    byArea,
    loadShapes,
    onPickCounty: (entry) => choose(entry),
    onPickArea: (area) => choose({ ...area, level: "sub_county" }, { fly: false }),
    // Zooming or dragging into another county brings its details alongside.
    onCentreCounty: (entry) => {
      if (shownCounty !== entry.id) choose(entry, { fly: false, scroll: false });
    },
  });

  function bringIntoView() {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    body.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" });
  }

  function backToKenya() {
    pick += 1;
    shownCounty = null;
    keepPlace(null);
    kenyaMap.showKenya();
    mapTitle.textContent = "All of Kenya";
    mapBack.hidden = true;
    mapHint.textContent = "Tap a county, or zoom in, to see its areas.";
    showKenya(card, countyScores);
    bringIntoView();
  }

  async function choose(place, { fly = true, scroll = true } = {}) {
    const n = ++pick;
    keepPlace(place);
    input.value = "";
    matches.hidden = true;
    if (scroll) bringIntoView();
    card.replaceChildren(el("p", "simple__muted", `Loading ${place.name}…`));

    const known = place.level === "county" ? byCounty.get(place.id) : byArea.get(place.id);
    const score = known ?? (await scoreFor(place, countyScores));
    if (!current(n)) return;
    const countyName = place.level === "county" ? place.name : score?.county_name ?? place.county;
    const countyId = place.level === "county" ? place.id : countyByName.get(countyName);
    shownCounty = countyId ?? null;

    if (countyId) {
      kenyaMap.focus(countyId, place.level === "sub_county" ? place.id : null, { fly });
      mapTitle.textContent = `${countyName} County`;
      mapBack.hidden = false;
      mapHint.textContent = "Tap an area for its details. Zoom out or drag to move to another county.";
    }
    const areas = place.level === "county" ? areaScores.filter((row) => row.county_name === countyName) : [];
    await showPlace(card, { ...place, county: countyName }, score, {
      countyId,
      areas,
      alerts,
      user,
      onDetailed,
      onPick: (next) => choose(next),
      onKenya: backToKenya,
      isCurrent: () => current(n),
    });
  }

  const start = storedPlace() ?? (user?.home_region ? { id: user.home_region, name: user.home_region_name, level: "sub_county" } : null);
  if (start) await choose(start);
  else showKenya(card, countyScores);
}

function showKenya(card, countyScores) {
  const counts = { higher: 0, usual: 0, lower: 0 };
  for (const score of countyScores) {
    const bucket = mapBucket(verdictOf(score).key);
    if (bucket in counts) counts[bucket] += 1;
  }
  const when = countyScores[0]?.prediction_date ? timeOfYear(countyScores[0].prediction_date) : "this time of year";
  card.replaceChildren(
    el("h2", "simple__place-name", "Choose your area"),
    el("p", null, "Type your county above, or tap it on the map, to see what to expect there."),
    el(
      "p",
      "simple__muted",
      `Across Kenya today, against an ordinary ${when}: ${counts.higher} counties have a higher chance of flooding than usual, ${counts.usual} about the usual, and ${counts.lower} a lower chance.`,
    ),
  );
}

async function showPlace(card, place, score, { countyId, areas, alerts, user, onDetailed, onPick, onKenya, isCurrent }) {
  const [forecast, floods, kmd] = await Promise.all([
    fetchForecast({ region: place.id }).catch(() => []),
    fetchFloodEvents({ region: place.id }).catch(() => null),
    fetchKmdWarnings({ region: place.id }).catch(() => null),
  ]);
  if (!isCurrent()) return;

  const verdict = verdictOf(score);
  const rain = weekRain(rowsOf(forecast));
  const alert = alertFor(place, alerts);
  const nearby = subCountyAlerts(place, alerts);
  const when = score?.prediction_date ? timeOfYear(score.prediction_date) : "this time of year";

  // The way back, first and plain: to the county from one of its areas, to Kenya from a county.
  const back = el("div", "simple__card-back");
  if (place.level === "sub_county" && countyId) {
    back.append(
      button("auth__button simple__back", `← Back to ${place.county}`, () =>
        onPick({ id: countyId, name: place.county, level: "county" }),
      ),
    );
  }
  back.append(button("auth__button simple__back", "← Back to the Kenya map", onKenya));

  const name = el("h2", "simple__place-name", place.name);
  if (place.level === "sub_county" && place.county) name.append(el("span", "simple__county", ` ${place.county} County`));
  const badge = el("p", `simple__verdict simple__verdict--${mapBucket(verdict.key)}`);
  badge.append(el("strong", null, verdict.word), ` for ${when}`);
  const nodes = [back, name, badge, el("p", null, verdict.text)];

  if (alert) {
    const box = el("div", `simple__alert simple__alert--${alert.level}`);
    box.append(el("strong", null, `${alert.level_label} in force`), el("p", null, alert.headline));
    nodes.push(box);
  } else if (nearby) {
    nodes.push(el("p", "simple__alert simple__alert--advisory", `Flood alerts are in force in ${nearby} area${nearby === 1 ? "" : "s"} of this county.`));
  }

  // The national weather service's own warnings, beside FloodScout's.
  for (const { properties } of kmd?.features ?? []) {
    const box = el("div", "simple__alert simple__alert--kmd");
    const link = el("a", null, "Read KMD's warning");
    link.href = properties.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    box.append(el("strong", null, kmdLine(properties)), link);
    nodes.push(box);
  }

  if (areas.length) nodes.push(...areaList(place, areas, alerts, onPick));

  nodes.push(el("h3", "simple__subtitle", "This week"), el("p", null, rain.text));

  nodes.push(el("h3", "simple__subtitle", "What to do"));
  const advice = el("ul", "simple__advice");
  const lines = alert?.instruction ? alert.instruction.split(/(?<=\.)\s+/) : everydayAdvice(verdict.key, rain.level, nearby);
  for (const line of lines) advice.append(el("li", null, line));
  nodes.push(advice);

  nodes.push(quickAlertControl([{ id: place.id, name: place.name }], user));

  const features = floods?.features ?? [];
  if (features.length) {
    const latest = features
      .map((f) => f.properties.began)
      .filter(Boolean)
      .sort()
      .at(-1);
    const year = latest ? new Date(latest).toLocaleDateString(undefined, { month: "long", year: "numeric" }) : null;
    nodes.push(
      el(
        "p",
        "simple__muted",
        `Large floods recorded here since 1988: ${features.length}${year ? `, most recently in ${year}` : ""}.`,
      ),
    );
  }

  nodes.push(
    button("link-button", "See the numbers for this area on the detailed map →", () =>
      onDetailed(place.level === "county" ? place.id : null),
    ),
  );
  card.replaceChildren(...nodes);
}

/** "Areas in Kisumu": its sub-counties in the map's three groups, each one tap from its own details. */
function areaList(county, rows, alerts, onPick) {
  const groups = groupAreas(rows);
  const alerted = new Map(alerts.filter((a) => a.region_level === "sub_county").map((a) => [a.region, a]));
  const nodes = [
    el("h3", "simple__subtitle", `Areas in ${county.name}`),
    el(
      "p",
      "simple__muted",
      "Each area is read from the rain that fell on it. Tell them apart with care: the model learned from floods recorded for whole counties.",
    ),
  ];
  for (const [key, title] of [
    ["higher", "Higher chance than usual"],
    ["usual", "About usual"],
    ["lower", "Lower chance than usual"],
    ["unknown", "No reading today"],
  ]) {
    const list = groups[key];
    if (!list.length) continue;
    const group = el("div", "simple__areas");
    const head = el("p", "simple__areas-head");
    head.append(el("span", `simple__swatch simple__swatch--${key === "unknown" ? "usual" : key}`), `${title} (${list.length})`);
    const items = el("ul", "simple__area-list");
    for (const row of list) {
      const item = el("li");
      const pick = button("simple__area", row.region_name, () =>
        onPick({ id: row.region, name: row.region_name, level: "sub_county", county: county.name }),
      );
      const alert = alerted.get(row.region);
      if (alert) pick.append(el("span", `simple__area-alert simple__area-alert--${alert.level}`, alert.level_label));
      item.append(pick);
      items.append(item);
    }
    group.append(head, items);
    nodes.push(group);
  }
  return nodes;
}

function legend() {
  const box = el("div", "simple__legend");
  for (const [key, text] of [
    ["higher", "Higher chance than usual"],
    ["usual", "About usual"],
    ["lower", "Lower chance than usual"],
  ]) {
    const item = el("span", "simple__legend-item");
    item.append(el("span", `simple__swatch simple__swatch--${key}`), text);
    box.append(item);
  }
  return box;
}

// From this zoom the map shows sub-counties instead of counties, and the
// basemap's towns and roads; from the next, the sub-counties' names too.
const AREAS_FROM_ZOOM = 8;
const NAMES_FROM_ZOOM = 9;

const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services";
const CANVAS_ATTRIBUTION = "Basemap &copy; Esri, HERE, Garmin, &copy; OpenStreetMap contributors";

/**
 * The map: all Kenya by county; zoomed in, every county in view by
 * sub-county with names and the basemap's places; one county picked, that
 * county's areas whatever the zoom, the rest faint and a tap away.
 * Returns { showKenya, focus }.
 */
function drawMap(div, { counties, byCounty, byArea, loadShapes, onPickCounty, onPickArea, onCentreCounty }) {
  map = L.map(div, {
    zoomControl: true,
    scrollWheelZoom: true,
    zoomSnap: 0.25,
    minZoom: 5,
  });
  map.attributionControl.setPrefix(false);
  map.createPane("simpleNames").style.zIndex = 450;
  map.getPane("simpleNames").style.pointerEvents = "none";

  const canvas = prefersDark() ? "World_Dark_Gray" : "World_Light_Gray";
  L.tileLayer(`${ESRI}/Canvas/${canvas}_Base/MapServer/tile/{z}/{y}/{x}`, {
    attribution: CANVAS_ATTRIBUTION,
    maxZoom: 16,
  }).addTo(map);
  L.tileLayer(`${ESRI}/Canvas/${canvas}_Reference/MapServer/tile/{z}/{y}/{x}`, {
    maxZoom: 16,
    minZoom: AREAS_FROM_ZOOM,
    pane: "simpleNames",
  }).addTo(map);

  const colour = (score) => cssVar(`--simple-${mapBucket(verdictOf(score).key)}`) || "#cccccc";
  const stroke = () => cssVar("--polygon-stroke") || "#ffffff";
  const focusStroke = () => cssVar("--polygon-stroke-focus") || "#000000";
  const countyLine = () => cssVar("--county-line") || "#52514e";
  let focused = null;
  let focusedName = null;
  let selected = null;
  let flying = false;
  const zoomedIn = () => map.getZoom() >= AREAS_FROM_ZOOM;
  // A county's areas show when zoomed in, or when it is the one picked.
  const areasShown = (countyName) => zoomedIn() || countyName === focusedName;

  const countyStyle = (feature) => {
    const id = idOf(feature);
    const own = id === focused;
    const covered = zoomedIn() || own;
    return {
      fillColor: colour(byCounty.get(id)),
      fillOpacity: covered ? 0 : focused !== null ? 0.25 : 0.75,
      color: own ? focusStroke() : covered ? countyLine() : stroke(),
      weight: own ? 3 : covered ? 2 : 1,
    };
  };
  const countyLayer = L.geoJSON(
    { type: "FeatureCollection", features: counties },
    {
      style: countyStyle,
      onEachFeature: (feature, shape) => {
        const id = idOf(feature);
        const name = nameOf(feature);
        shape.bindTooltip(() => `${name}: ${verdictOf(byCounty.get(id)).word}`, { sticky: true });
        shape.on("click", () => onPickCounty({ id, name, level: "county" }));
      },
    },
  ).addTo(map);

  const areaStyle = (feature) => {
    const shown = areasShown(feature.properties.county_name);
    const isSelected = idOf(feature) === selected;
    return {
      fillColor: colour(byArea.get(idOf(feature))),
      fillOpacity: shown ? 0.7 : 0,
      opacity: shown ? 1 : 0,
      color: isSelected ? focusStroke() : stroke(),
      weight: isSelected ? 3 : 1,
    };
  };
  // A hidden area must not catch the tap meant for the county under it: at
  // the Kenya level a tap picks a county, which then zooms in.
  function styleAreas() {
    areaLayer.setStyle(areaStyle);
    areaLayer.eachLayer((shape) => {
      const element = shape.getElement();
      if (element) element.style.pointerEvents = areasShown(shape.feature.properties.county_name) ? "" : "none";
    });
  }
  const names = L.layerGroup().addTo(map);
  const areaLayer = L.geoJSON(null, {
    style: areaStyle,
    onEachFeature: (feature, shape) => {
      const id = idOf(feature);
      const name = nameOf(feature);
      const county = feature.properties.county_name;
      shape.bindTooltip(() => `${name}, ${county}: ${verdictOf(byArea.get(id)).word}`, { sticky: true });
      shape.on("click", (event) => {
        L.DomEvent.stopPropagation(event);
        onPickArea({ id, name, county });
      });
      names.addLayer(
        L.marker(shape.getBounds().getCenter(), {
          pane: "simpleNames",
          interactive: false,
          keyboard: false,
          icon: L.divIcon({ className: "simple__area-name", html: el("span", null, name), iconSize: null }),
        }),
      );
    },
  }).addTo(map);
  const loaded = new Set();

  async function ensureAreas(countyIds) {
    const wanted = countyIds.filter((id) => !loaded.has(id));
    wanted.forEach((id) => loaded.add(id));
    if (!wanted.length) return;
    const lists = await Promise.all(wanted.map((id) => loadShapes(id)));
    if (!map) return;
    lists.forEach((shapes, i) => {
      if (shapes.length) areaLayer.addData(shapes);
      else loaded.delete(wanted[i]);
    });
    styleAreas();
  }

  function countiesInView() {
    const view = map.getBounds();
    return countyLayer
      .getLayers()
      .filter((shape) => view.intersects(shape.getBounds()))
      .map((shape) => idOf(shape.feature));
  }

  function countyAtCentre() {
    const { lng, lat } = map.getCenter();
    const shape = countyLayer.getLayers().find((s) => pointInGeometry([lng, lat], s.feature.geometry));
    return shape ? { id: idOf(shape.feature), name: nameOf(shape.feature), level: "county" } : null;
  }

  function refresh() {
    if (zoomedIn()) ensureAreas(countiesInView());
    else if (focused !== null) ensureAreas([focused]);
    countyLayer.setStyle(countyStyle);
    styleAreas();
    // Names only where areas show: zoomed in, or in the picked county.
    div.classList.toggle("simple__map--names", map.getZoom() >= NAMES_FROM_ZOOM);
  }

  map.on("moveend", () => {
    refresh();
    if (flying) {
      flying = false;
      return;
    }
    if (zoomedIn()) {
      const county = countyAtCentre();
      if (county) onCentreCounty(county);
    }
  });

  const kenyaBounds = countyLayer.getBounds();
  map.fitBounds(kenyaBounds, { padding: [8, 8] });

  function flyTo(bounds) {
    flying = true;
    map.flyToBounds(bounds, { padding: [12, 12], duration: 0.6 });
  }

  return {
    showKenya() {
      focused = null;
      focusedName = null;
      selected = null;
      flyTo(kenyaBounds);
      refresh();
    },
    focus(countyId, selectedAreaId, { fly = true } = {}) {
      const shape = countyLayer.getLayers().find((s) => idOf(s.feature) === countyId);
      focused = countyId;
      focusedName = shape ? nameOf(shape.feature) : null;
      selected = selectedAreaId;
      ensureAreas([countyId]);
      if (fly && shape) flyTo(shape.getBounds());
      refresh();
    },
  };
}
