import assert from "node:assert/strict";
import { test } from "node:test";

import { defaultView, viewFor } from "../viewmode.js";
import {
  alertFor,
  dailyRain,
  everydayAdvice,
  groupAreas,
  mapBucket,
  pointInGeometry,
  seasonVerdict,
  subCountyAlerts,
  timeOfYear,
  weekRain,
} from "./plain.js";

test("the season reading in words", () => {
  assert.equal(seasonVerdict(0.6, "high").key, "lower");
  assert.equal(seasonVerdict(1.1, "high").key, "usual");
  assert.equal(seasonVerdict(1.4, "high").key, "higher");
  assert.equal(seasonVerdict(2.2, "very_high").key, "much_higher");
  assert.equal(seasonVerdict(null, "high").key, "unknown");
});

test("a dry window is never 'higher than usual'", () => {
  assert.equal(seasonVerdict(1.8, "moderate").key, "usual");
  assert.equal(seasonVerdict(0.7, "moderate").key, "lower");
});

test("the map has three colours", () => {
  assert.equal(mapBucket("much_higher"), "higher");
  assert.equal(mapBucket("usual"), "usual");
});

const rows = (...totals) =>
  totals.map((mm, i) => ({ target_date: `2026-10-${String(29 + i).padStart(2, "0")}`, rain_forecast_mm: mm }));

test("each day's rain from the running totals", () => {
  assert.deepEqual(
    dailyRain(rows(3, 10, 40)).map((d) => d.mm),
    [3, 7, 30],
  );
});

test("the week's rain in a sentence", () => {
  assert.equal(weekRain(rows(0, 1, 1)).level, "dry");
  assert.match(weekRain(rows(3, 3, 3)).text, /^Light rain is forecast on /);
  assert.equal(weekRain(rows(3, 6, 9, 12, 15)).text, "Some rain is forecast on most days, none of it heavy.");
  const heavy = weekRain(rows(5, 30, 35));
  assert.equal(heavy.level, "heavy");
  assert.equal(weekRain(rows(5, 70)).level, "very_heavy");
  assert.equal(weekRain([]).level, "unknown");
});

test("the time of year as people say it", () => {
  assert.match(timeOfYear("2026-10-28"), /^late /);
  assert.match(timeOfYear("2026-10-15"), /^mid-/);
  assert.match(timeOfYear("2026-10-02"), /^early /);
});

test("advice is calm unless something points at floods", () => {
  assert.equal(everydayAdvice("usual", "dry")[0], "Nothing needs doing today.");
  assert.match(everydayAdvice("usual", "heavy")[0], /higher ground/);
  assert.match(everydayAdvice("much_higher", "dry")[0], /higher ground/);
  assert.match(everydayAdvice("usual", "some", 2)[0], /higher ground/);
});

const ALERTS = [
  { region: 10, region_level: "county", county_name: "Kisumu", level: "watch" },
  { region: 11, region_level: "sub_county", county_name: "Kisumu", level: "warning" },
  { region: 12, region_level: "sub_county", county_name: "Kisumu", level: "advisory" },
];

test("a place's alert is its own, else its county's", () => {
  assert.equal(alertFor({ id: 11, level: "sub_county", county: "Kisumu" }, ALERTS).level, "warning");
  assert.equal(alertFor({ id: 13, level: "sub_county", county: "Kisumu" }, ALERTS).level, "watch");
  assert.equal(alertFor({ id: 20, level: "county", name: "Siaya" }, ALERTS), null);
  assert.equal(subCountyAlerts({ id: 10, level: "county", name: "Kisumu" }, ALERTS), 2);
});

test("administrators and analysts start on the detailed map; a choice on this device wins", () => {
  assert.equal(defaultView(null), "simple");
  assert.equal(defaultView({ is_administrator: false, can_see_analytics: false }), "simple");
  assert.equal(defaultView({ is_administrator: false, can_see_analytics: true }), "detailed");
  assert.equal(viewFor({ is_administrator: true }, null), "detailed");
  assert.equal(viewFor({ is_administrator: true }, "simple"), "simple");
  assert.equal(viewFor(null, "nonsense"), "simple");
});

test("a county's areas grouped by colour, the most worrying first", () => {
  const groups = groupAreas([
    { region_name: "A", times_season: 1.3, risk_category: "high" },
    { region_name: "B", times_season: 1.9, risk_category: "very_high" },
    { region_name: "C", times_season: 1.0, risk_category: "high" },
    { region_name: "D", times_season: 0.7, risk_category: "high" },
    { region_name: "E", times_season: 1.6, risk_category: "moderate" },
    { region_name: "F", times_season: null, risk_category: null },
  ]);
  assert.deepEqual(groups.higher.map((s) => s.region_name), ["B", "A"]);
  // E is dry (Moderate), so never "higher".
  assert.deepEqual(groups.usual.map((s) => s.region_name), ["E", "C"]);
  assert.deepEqual(groups.lower.map((s) => s.region_name), ["D"]);
  assert.deepEqual(groups.unknown.map((s) => s.region_name), ["F"]);
});

test("which county a point is in, holes included", () => {
  const square = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]];
  const hole = [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]];
  const polygon = { type: "Polygon", coordinates: [square, hole] };
  assert.equal(pointInGeometry([2, 2], polygon), true);
  assert.equal(pointInGeometry([5, 5], polygon), false);
  assert.equal(pointInGeometry([12, 2], polygon), false);
  const multi = { type: "MultiPolygon", coordinates: [[square], [[[20, 0], [30, 0], [30, 10], [20, 10], [20, 0]]]] };
  assert.equal(pointInGeometry([25, 5], multi), true);
  assert.equal(pointInGeometry([15, 5], multi), false);
});
