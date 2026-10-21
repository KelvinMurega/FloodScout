import assert from "node:assert/strict";
import { test } from "node:test";

import { floodRadius, gibsDate } from "./layerdates.js";

const TODAY = new Date(2026, 9, 20); // 20 October 2026, local time

test("gibsDate keeps a day old enough to be published", () => {
  assert.equal(gibsDate("2026-10-01", 2, TODAY), "2026-10-01");
});

test("gibsDate holds a recent day back to the newest published one", () => {
  assert.equal(gibsDate("2026-10-20", 2, TODAY), "2026-10-18");
  assert.equal(gibsDate("2026-10-20", 1, TODAY), "2026-10-19");
  assert.equal(gibsDate(null, 2, TODAY), "2026-10-18");
  assert.equal(gibsDate("not a date", 2, TODAY), "2026-10-18");
});

test("floodRadius grows with displacement and stays bounded", () => {
  assert.equal(floodRadius(0), 5);
  assert.equal(floodRadius(undefined), 5);
  assert.ok(floodRadius(10_000) > floodRadius(100));
  assert.equal(floodRadius(10_000_000), 16);
});
