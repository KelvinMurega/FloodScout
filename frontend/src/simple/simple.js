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
  fetchLatestScores,
  fetchRegions,
  fetchScoreHistory,
} from "../api.js";
import { quickAlertControl } from "../alerts/quick.js";
import { searchRegions } from "../search.js";
import {
  alertFor,
  everydayAdvice,
  groupAreas,
  mapBucket,
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
  const mapHint = el("p", "simple__muted simple__map-hint", "Tap a county to see it up close.");
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
  try {
    countyScores = rowsOf(await fetchLatestScores({ level: "county" }));
  } catch (error) {
    if (mine === render) card.replaceChildren(el("p", "simple__muted", `Could not load today's readings: ${error.message}`));
    return;
  }
  if (mine !== render) return;
  const byCounty = new Map(countyScores.map((s) => [s.region, s]));
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

  const kenyaMap = drawKenya(mapDiv, counties, byCounty, (entry) => choose(entry));
  const shapesCache = new Map();
  // Each pick or "back" is numbered; a slower earlier one never draws over a later one.
  let pick = 0;
  const current = (n) => n === pick && mine === render;

  function bringIntoView() {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    body.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" });
  }

  function backToKenya() {
    pick += 1;
    keepPlace(null);
    kenyaMap.showKenya();
    mapTitle.textContent = "All of Kenya";
    mapBack.hidden = true;
    mapHint.textContent = "Tap a county to see it up close.";
    showKenya(card, countyScores);
    bringIntoView();
  }

  async function choose(place) {
    const n = ++pick;
    keepPlace(place);
    input.value = "";
    matches.hidden = true;
    bringIntoView();
    card.replaceChildren(el("p", "simple__muted", `Loading ${place.name}…`));

    const score = await scoreFor(place, countyScores);
    const countyName = place.level === "county" ? place.name : score?.county_name ?? place.county;
    const countyId = place.level === "county" ? place.id : countyByName.get(countyName);
    const [areas, shapes] = countyId
      ? await Promise.all([
          fetchLatestScores({ level: "sub_county", parent: countyId }).then(rowsOf).catch(() => []),
          shapesCache.get(countyId) ??
            fetchRegions({ level: "sub_county", parent: countyId })
              .then((geo) => {
                shapesCache.set(countyId, geo.features ?? []);
                return geo.features ?? [];
              })
              .catch(() => []),
        ])
      : [[], []];
    if (!current(n)) return;

    if (countyId) {
      kenyaMap.showCounty(countyId, shapes, new Map(areas.map((s) => [s.region, s])), place.level === "sub_county" ? place.id : null, (area) =>
        choose({ ...area, level: "sub_county", county: countyName }),
      );
      mapTitle.textContent = `${countyName} County`;
      mapBack.hidden = false;
      mapHint.textContent = "Tap an area for its details, or another county to move there.";
    }
    await showPlace(card, { ...place, county: countyName }, score, {
      countyId,
      areas: place.level === "county" ? areas : [],
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
  const [forecast, floods] = await Promise.all([
    fetchForecast({ region: place.id }).catch(() => []),
    fetchFloodEvents({ region: place.id }).catch(() => null),
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

/**
 * The map: all Kenya by county, or one county by sub-county with the rest
 * faint (still a tap away). Returns { showKenya, showCounty }.
 */
function drawKenya(div, counties, byCounty, onPickCounty) {
  map = L.map(div, {
    zoomControl: true,
    scrollWheelZoom: false,
    attributionControl: false,
    zoomSnap: 0.25,
  });
  const colour = (score) => cssVar(`--simple-${mapBucket(verdictOf(score).key)}`) || "#cccccc";
  const stroke = () => cssVar("--polygon-stroke") || "#ffffff";
  const focusStroke = () => cssVar("--polygon-stroke-focus") || "#000000";
  let focused = null;
  let areaLayer = null;

  const countyStyle = (feature) => {
    const faint = focused !== null;
    return {
      fillColor: colour(byCounty.get(idOf(feature))),
      fillOpacity: faint ? 0.25 : 0.85,
      color: stroke(),
      weight: 1,
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
  const kenyaBounds = countyLayer.getBounds();
  map.fitBounds(kenyaBounds, { padding: [8, 8] });

  return {
    showKenya() {
      focused = null;
      areaLayer?.remove();
      areaLayer = null;
      countyLayer.setStyle(countyStyle);
      map.flyToBounds(kenyaBounds, { padding: [8, 8], duration: 0.6 });
    },
    showCounty(countyId, shapes, byArea, selectedId, onPickArea) {
      focused = countyId;
      countyLayer.setStyle(countyStyle);
      areaLayer?.remove();
      areaLayer = null;
      if (shapes.length) {
        areaLayer = L.geoJSON(
          { type: "FeatureCollection", features: shapes },
          {
            style: (feature) => {
              const selected = idOf(feature) === selectedId;
              return {
                fillColor: colour(byArea.get(idOf(feature))),
                fillOpacity: 0.9,
                color: selected ? focusStroke() : stroke(),
                weight: selected ? 3 : 1,
              };
            },
            onEachFeature: (feature, shape) => {
              const id = idOf(feature);
              const name = nameOf(feature);
              shape.bindTooltip(`${name}: ${verdictOf(byArea.get(id)).word}`, { sticky: true });
              shape.on("click", () => onPickArea({ id, name }));
              if (id === selectedId) shape.bringToFront();
            },
          },
        ).addTo(map);
      }
      const target = areaLayer ?? countyLayer.getLayers().find((shape) => idOf(shape.feature) === countyId);
      if (target) map.flyToBounds(target.getBounds(), { padding: [12, 12], duration: 0.6 });
    },
  };
}
