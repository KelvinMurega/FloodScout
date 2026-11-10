import assert from "node:assert/strict";
import { test } from "node:test";

import { daysBefore, historySeries, sparkline } from "./history.js";

test("daysBefore counts back across a month end", () => {
  assert.equal(daysBefore("2026-10-08", 29), "2026-09-09");
  assert.equal(daysBefore("2026-03-01", 1), "2026-02-28");
  assert.equal(daysBefore("2026-10-08", 0), "2026-10-08");
});

test("historySeries sorts oldest first and keeps the local date", () => {
  const series = historySeries([
    { prediction_date: "2026-10-08T00:00:00+03:00", times_usual: 2.1 },
    { prediction_date: "2026-10-06T00:00:00+03:00", times_usual: 0.8 },
    { prediction_date: "2026-10-07T00:00:00+03:00", times_usual: 1.4 },
  ]);

  assert.deepEqual(series, [
    { date: "2026-10-06", value: 0.8 },
    { date: "2026-10-07", value: 1.4 },
    { date: "2026-10-08", value: 2.1 },
  ]);
});

test("historySeries drops placeholder scores, which have no multiple", () => {
  const series = historySeries([
    { prediction_date: "2026-10-07T00:00:00+03:00", times_usual: null },
    { prediction_date: "2026-10-08T00:00:00+03:00", times_usual: 1.2 },
  ]);

  assert.deepEqual(series, [{ date: "2026-10-08", value: 1.2 }]);
});

test("sparkline spreads days across the box and puts higher values higher", () => {
  const series = [
    { date: "2026-10-01", value: 0.5 },
    { date: "2026-10-06", value: 3 },
    { date: "2026-10-11", value: 1 },
  ];
  const chart = sparkline(series, "2026-10-01", "2026-10-11", { width: 110, height: 60, pad: 5 });

  assert.deepEqual(
    chart.points.map((point) => point.x),
    [5, 55, 105],
  );
  // SVG y grows downwards: the lowest value sits on the bottom padding line.
  assert.deepEqual(
    chart.points.map((point) => point.y),
    [55, 5, 45],
  );
  assert.equal(chart.usualY, 45);
  assert.equal(chart.low, 0.5);
  assert.equal(chart.high, 3);
});

test("sparkline leaves a gap for a missing day instead of closing it up", () => {
  const chart = sparkline(
    [
      { date: "2026-10-01", value: 1 },
      { date: "2026-10-02", value: 1 },
      { date: "2026-10-05", value: 1 },
    ],
    "2026-10-01",
    "2026-10-05",
    { width: 100, height: 40, pad: 10 },
  );

  assert.deepEqual(
    chart.points.map((point) => point.x),
    [10, 30, 90],
  );
});

test("sparkline always keeps the usual chance in range", () => {
  const above = sparkline([{ date: "2026-10-01", value: 4 }], "2026-10-01", "2026-10-02", {
    height: 50,
    pad: 5,
  });
  const below = sparkline([{ date: "2026-10-01", value: 0.2 }], "2026-10-01", "2026-10-02", {
    height: 50,
    pad: 5,
  });

  assert.equal(above.usualY, 45); // 1 is the bottom of the range
  assert.equal(below.usualY, 5); // 1 is the top of the range
});

test("sparkline copes with one flat value and with nothing", () => {
  const flat = sparkline([{ date: "2026-10-01", value: 1 }], "2026-10-01", "2026-10-01");
  assert.ok(Number.isFinite(flat.points[0].x) && Number.isFinite(flat.points[0].y));

  const empty = sparkline([], "2026-10-01", "2026-10-08");
  assert.deepEqual(empty.points, []);
  assert.equal(empty.low, null);
});
