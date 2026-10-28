/**
 * The simple view (#/ for everyone, unless they chose the detailed map).
 *
 * One place at a time, in plain words: is flooding more likely than usual
 * here for the time of year, is heavy rain coming this week, is an alert in
 * force, and what to do. A small map in three colours, and the detailed map
 * one tap away. The words come from plain.js; every fact is the API's.
 */

import L from "leaflet";

import {
  fetchFloodEvents,
  fetchForecast,
  fetchLatestScores,
  fetchScoreHistory,
} from "../api.js";
import { quickAlertControl } from "../alerts/quick.js";
import { searchRegions } from "../search.js";
import {
  alertFor,
  everydayAdvice,
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
    localStorage.setItem(PLACE_KEY, JSON.stringify(place));
  } catch {
    // Blocked storage: the place is forgotten when the page is left.
  }
}

function isoDaysAgo(days) {
  const date = new Date(Date.now() - days * 86_400_000);
  return date.toISOString().slice(0, 10);
}

/** The newest score for one place: a county's from the list, a sub-county's from its history. */
async function scoreFor(place, countyScores) {
  if (place.level === "county") return countyScores.find((s) => s.region === place.id) ?? null;
  const history = await fetchScoreHistory(place.id, isoDaysAgo(7), isoDaysAgo(-1)).catch(() => []);
  const rows = Array.isArray(history) ? history : history.results ?? [];
  return rows[0] ?? null;
}

export function hideSimple() {
  render += 1;
  map?.remove();
  map = null;
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
  const detailed = el("button", "auth__button", "See the detailed map");
  detailed.type = "button";
  detailed.addEventListener("click", () => onDetailed());
  head.append(titles, detailed);

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
    const home = el("button", "link-button simple__home", `Use where I live: ${user.home_region_name}`);
    home.type = "button";
    home.addEventListener("click", () => choose({ id: user.home_region, name: user.home_region_name, level: "sub_county" }));
    find.append(home);
  }

  const card = el("section", "simple__card");
  card.setAttribute("aria-live", "polite");
  const mapBox = el("section", "simple__map-box");
  const mapDiv = el("div", "simple__map");
  mapDiv.setAttribute("aria-label", "Map of Kenya's counties, coloured by flood chance against the usual");
  mapBox.append(el("h2", "simple__subtitle", "Across Kenya"), mapDiv, legend());
  const note = el(
    "p",
    "simple__note",
    "FloodScout is a university research project, not an official warning service. Follow the Kenya Meteorological Department and your county government. In an emergency call 999 or 112, or the Kenya Red Cross on 1199.",
  );
  page.append(head, find, card, mapBox, note);
  root.replaceChildren(page);
  card.append(el("p", "simple__muted", "Loading today's readings…"));

  let countyScores = [];
  try {
    const result = await fetchLatestScores({ level: "county" });
    countyScores = Array.isArray(result) ? result : result.results ?? [];
  } catch (error) {
    if (mine === render) card.replaceChildren(el("p", "simple__muted", `Could not load today's readings: ${error.message}`));
    return;
  }
  if (mine !== render) return;
  const byRegion = new Map(countyScores.map((s) => [s.region, s]));

  const entries = counties.map((f) => ({
    id: f.id ?? f.properties.id,
    name: f.properties.name ?? f.properties.county_name,
    level: "county",
  }));
  input.addEventListener("input", () => {
    const found = searchRegions(entries, input.value, 6);
    matches.replaceChildren(
      ...found.map((entry) => {
        const item = el("li");
        const pick = el("button", "simple__match", entry.name);
        pick.type = "button";
        pick.addEventListener("click", () => choose(entry));
        item.append(pick);
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

  drawMap(mapDiv, counties, byRegion, (entry) => choose(entry));

  async function choose(place) {
    keepPlace(place);
    input.value = "";
    matches.hidden = true;
    await showPlace(card, place, { countyScores, alerts, user, onDetailed, mine });
  }

  const start = storedPlace() ?? (user?.home_region ? { id: user.home_region, name: user.home_region_name, level: "sub_county" } : null);
  if (start) await showPlace(card, start, { countyScores, alerts, user, onDetailed, mine });
  else showKenya(card, countyScores);
}

function verdictOf(score) {
  return seasonVerdict(score?.times_season, score?.risk_category);
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

async function showPlace(card, place, { countyScores, alerts, user, onDetailed, mine }) {
  card.replaceChildren(el("p", "simple__muted", `Loading ${place.name}…`));
  const [score, forecast, floods] = await Promise.all([
    scoreFor(place, countyScores),
    fetchForecast({ region: place.id }).catch(() => []),
    fetchFloodEvents({ region: place.id }).catch(() => null),
  ]);
  if (mine !== render) return;

  const full = { ...place, county: score?.county_name ?? place.county };
  const verdict = verdictOf(score);
  const rain = weekRain(Array.isArray(forecast) ? forecast : forecast.results ?? []);
  const alert = alertFor(full, alerts);
  const nearby = subCountyAlerts(full, alerts);
  const when = score?.prediction_date ? timeOfYear(score.prediction_date) : "this time of year";

  const name = el("h2", "simple__place-name", place.name);
  if (place.level === "sub_county" && full.county) name.append(el("span", "simple__county", ` ${full.county} County`));
  const badge = el("p", `simple__verdict simple__verdict--${mapBucket(verdict.key)}`);
  badge.append(el("strong", null, verdict.word), ` for ${when}`);
  const nodes = [name, badge, el("p", null, verdict.text)];

  if (alert) {
    const box = el("div", `simple__alert simple__alert--${alert.level}`);
    box.append(el("strong", null, `${alert.level_label} in force`), el("p", null, alert.headline));
    nodes.push(box);
  } else if (nearby) {
    nodes.push(el("p", "simple__alert simple__alert--advisory", `Flood alerts are in force in ${nearby} area${nearby === 1 ? "" : "s"} of this county.`));
  }

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

  const more = el("button", "link-button", "See the numbers for this area on the detailed map →");
  more.type = "button";
  more.addEventListener("click", () => onDetailed(place.level === "county" ? place.id : null));
  nodes.push(more);
  card.replaceChildren(...nodes);
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

function drawMap(div, counties, byRegion, onPick) {
  map = L.map(div, {
    zoomControl: true,
    scrollWheelZoom: false,
    attributionControl: false,
    zoomSnap: 0.25,
  });
  const colour = (key) => cssVar(`--simple-${key}`) || "#cccccc";
  const layer = L.geoJSON(
    { type: "FeatureCollection", features: counties },
    {
      style: (feature) => {
        const id = feature.id ?? feature.properties.id;
        const bucket = mapBucket(verdictOf(byRegion.get(id)).key);
        return {
          fillColor: colour(bucket),
          fillOpacity: 0.85,
          color: cssVar("--polygon-stroke") || "#ffffff",
          weight: 1,
        };
      },
      onEachFeature: (feature, shape) => {
        const id = feature.id ?? feature.properties.id;
        const name = feature.properties.name ?? feature.properties.county_name;
        shape.bindTooltip(`${name}: ${verdictOf(byRegion.get(id)).word}`, { sticky: true });
        shape.on("click", () => onPick({ id, name, level: "county" }));
      },
    },
  ).addTo(map);
  map.fitBounds(layer.getBounds(), { padding: [8, 8] });
}
