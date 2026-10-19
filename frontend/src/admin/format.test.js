import assert from "node:assert/strict";
import { test } from "node:test";

import {
  confusion,
  countyErrors,
  day,
  duration,
  featureLabel,
  fixed,
  foldLabel,
  percent,
  tabFromSlug,
  worstStatus,
} from "./format.js";

test("numbers format with a dash for a missing value", () => {
  assert.equal(fixed(0.25349), "0.253");
  assert.equal(fixed(null), "–");
  assert.equal(percent(0.0968), "9.7%");
  assert.equal(percent(undefined), "–");
});

test("a feature without a label keeps its key", () => {
  assert.equal(featureLabel("rain_30d"), "Rain in the last 30 days");
  assert.equal(featureLabel("something_new"), "something_new");
});

test("a fold is labelled by its first year and how many more", () => {
  assert.equal(foldLabel([1996, 2002, 2008, 2014, 2020]), "1996 +4");
  assert.equal(foldLabel([2017]), "2017");
});

test("durations read in seconds, then minutes", () => {
  assert.equal(duration(4.3), "4.3 s");
  assert.equal(duration(443.8), "7 min 24 s");
});

test("a date is read as a calendar day", () => {
  assert.equal(day("2026-10-19"), "19 Oct 2026");
  assert.equal(day(null), "–");
});

test("the confusion matrix shares are per row", () => {
  const { floods, dry, cells } = confusion({
    true_positives: 30,
    false_negatives: 10,
    false_positives: 20,
    true_negatives: 80,
  });
  assert.equal(floods, 40);
  assert.equal(dry, 100);
  assert.deepEqual(
    cells.map((cell) => [cell.key, cell.share]),
    [
      ["tp", 0.75],
      ["fn", 0.25],
      ["fp", 0.2],
      ["tn", 0.8],
    ],
  );
});

test("county errors put the lowest catch rate first and no-flood counties last", () => {
  const rows = countyErrors([
    { county: "A", floods: 10, caught: 9 },
    { county: "B", floods: 0, caught: 0 },
    { county: "C", floods: 10, caught: 2 },
    { county: "D", floods: 20, caught: 4 },
  ]);
  assert.deepEqual(
    rows.map((row) => row.county),
    ["D", "C", "A", "B"],
  );
  assert.equal(rows[3].recall, null);
});

test("the worst status wins", () => {
  assert.equal(worstStatus([{ status: "ok" }, { status: "warning" }]), "warning");
  assert.equal(worstStatus([{ status: "degraded" }, { status: "warning" }]), "degraded");
  assert.equal(worstStatus([]), "ok");
});

test("the tab comes from the hash, falling back to the first", () => {
  const tabs = [{ id: "overview" }, { id: "evaluation" }];
  assert.equal(tabFromSlug("admin/evaluation", tabs), "evaluation");
  assert.equal(tabFromSlug("admin", tabs), "overview");
  assert.equal(tabFromSlug("admin/nope", tabs), "overview");
});
