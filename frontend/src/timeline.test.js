import assert from "node:assert/strict";
import { test } from "node:test";

import { currentStep, stateForStep, timelineSteps } from "./timeline.js";

const SCORED = ["2026-10-20", "2026-10-19", "2026-10-18"]; // newest first, as the API lists
const FORECAST = ["2026-10-21", "2026-10-22"];

test("observed days oldest first, then today, then the forecast", () => {
  assert.deepEqual(timelineSteps(SCORED, FORECAST), [
    { date: "2026-10-18", kind: "observed" },
    { date: "2026-10-19", kind: "observed" },
    { date: "2026-10-20", kind: "today" },
    { date: "2026-10-21", kind: "forecast" },
    { date: "2026-10-22", kind: "forecast" },
  ]);
});

test("a stale forecast never stands in for a scored day", () => {
  const steps = timelineSteps(SCORED, ["2026-10-19", "2026-10-20", "2026-10-21"]);
  assert.deepEqual(
    steps.filter((s) => s.kind === "forecast").map((s) => s.date),
    ["2026-10-21"],
  );
});

test("only the most recent past days are kept", () => {
  const steps = timelineSteps(SCORED, [], 2);
  assert.deepEqual(
    steps.map((s) => s.date),
    ["2026-10-19", "2026-10-20"],
  );
});

test("a forecast with no scores yet is all forecast", () => {
  assert.deepEqual(
    timelineSteps([], FORECAST).map((s) => s.kind),
    ["forecast", "forecast"],
  );
});

test("currentStep finds the view on the timeline", () => {
  const steps = timelineSteps(SCORED, FORECAST);
  assert.equal(currentStep(steps), 2);
  assert.equal(currentStep(steps, { scoreDate: "2026-10-18" }), 0);
  assert.equal(currentStep(steps, { forecastDay: "2026-10-22" }), 4);
  assert.equal(currentStep(steps, { outlook: true }), -1);
});

test("stateForStep round-trips with currentStep", () => {
  const steps = timelineSteps(SCORED, FORECAST);
  steps.forEach((step, index) => {
    assert.equal(currentStep(steps, stateForStep(step)), index);
  });
  assert.deepEqual(stateForStep(undefined), { scoreDate: null, forecastDay: null });
});
