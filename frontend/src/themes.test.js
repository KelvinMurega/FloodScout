import assert from "node:assert/strict";
import { test } from "node:test";

import {
  THEMES,
  classIndex,
  classLabel,
  describeChange,
  describeRain,
  legendEntries,
  themeAvailable,
  themeColour,
  themeValue,
} from "./themes.js";

test("classIndex puts a value on a break into the class above it", () => {
  const breaks = [1, 2, 3];
  assert.equal(classIndex(0.5, breaks), 0);
  assert.equal(classIndex(1, breaks), 1);
  assert.equal(classIndex(2.9, breaks), 2);
  assert.equal(classIndex(3, breaks), 3);
  assert.equal(classIndex(99, breaks), 3);
});

test("every scale has one more colour than it has breaks", () => {
  for (const [name, theme] of Object.entries(THEMES)) {
    if (!theme.breaks) continue;
    assert.equal(theme.colours.length, theme.breaks.length + 1, name);
  }
});

test("themeValue prefers the context row, then the score's own fields", () => {
  assert.equal(themeValue("usual", { times_usual: 1.5 }, { times_usual: 2.1 }), 2.1);
  assert.equal(themeValue("usual", { times_usual: 1.5 }, null), 1.5);
  // A grid cell carries its own rain ratio; an outlook row its forecast one.
  assert.equal(themeValue("rain", { rain_30d_ratio: 1.4 }, null), 1.4);
  assert.equal(themeValue("rain", { rain_vs_normal: 0.7 }, null), 0.7);
  assert.equal(themeValue("change", {}, { change_7d: 0 }), 0);
  assert.equal(themeValue("change", {}, null), null);
});

test("themeColour separates scores that share a category", () => {
  const low = themeColour("usual", { risk_category: "high", times_usual: 1.2 });
  const high = themeColour("usual", { risk_category: "high", times_usual: 3.1 });
  assert.notEqual(low, high);
  assert.equal(
    themeColour("risk", { risk_category: "high", times_usual: 1.2 }),
    themeColour("risk", { risk_category: "high", times_usual: 3.1 }),
  );
});

test("themeColour greys out a missing value rather than calling it low", () => {
  const missing = themeColour("rain", {}, null);
  assert.ok(!THEMES.rain.colours.includes(missing));
});

test("classLabel names the open ends and the ranges", () => {
  assert.equal(classLabel("usual", 0), "Under 1×");
  assert.equal(classLabel("usual", 1), "1× to 1.25×");
  assert.equal(classLabel("usual", 6), "2.5× and over");
  assert.equal(classLabel("rain", 3), "80% to 120%");
  assert.equal(classLabel("change", 6), "+0.5 and over");
});

test("legendEntries counts the rows in each class, highest first", () => {
  const rows = [
    { score: { times_usual: 1.1 } },
    { score: { times_usual: 1.1 } },
    { score: { times_usual: 3 } },
    { score: {} },
  ];
  const entries = legendEntries("usual", rows);
  assert.equal(entries[0].label, "2.5× and over");
  assert.equal(entries[0].count, 1);
  assert.equal(entries.find((e) => e.label === "1× to 1.25×").count, 2);
  assert.deepEqual(entries.at(-1), { colour: entries.at(-1).colour, label: "Not known", count: 1 });
});

test("legendEntries for the risk level counts categories", () => {
  const entries = legendEntries("risk", [
    { score: { risk_category: "high" } },
    { score: { risk_category: "very_high" } },
    { score: { risk_category: "high" } },
  ]);
  assert.deepEqual(
    entries.map((e) => [e.label, e.count]),
    [
      ["Low", 0],
      ["Moderate", 0],
      ["High", 2],
      ["Very high", 1],
    ],
  );
});

test("the 7-day change has no meaning for an outlook or the area grid", () => {
  assert.equal(themeAvailable("change"), true);
  assert.equal(themeAvailable("change", { outlook: true }), false);
  assert.equal(themeAvailable("change", { grid: true }), false);
  assert.equal(themeAvailable("rain", { outlook: true }), true);
  assert.equal(themeAvailable("rain", { forecast: true }), false);
  assert.equal(themeAvailable("change", { forecast: true }), false);
  assert.equal(themeAvailable("usual", { forecast: true }), true);
});

test("describeChange and describeRain read as plain English", () => {
  assert.equal(describeChange(0.02), "→ steady over the last 7 days");
  assert.equal(describeChange(0.4), "↑ up 0.4 times usual in 7 days");
  assert.equal(describeChange(-0.25), "↓ down 0.3 times usual in 7 days");
  assert.equal(describeChange(null), null);
  assert.equal(
    describeRain({ rain_30d: 211.6, rain_30d_ratio: 1.8 }),
    "212 mm in 30 days, 180% of normal",
  );
  assert.equal(describeRain({ rain_30d: 40 }), "40 mm in 30 days");
  assert.equal(describeRain(null), null);
});
