import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { monthOf, outcomeLines, recordLabel, splitOutlookMonths } from "./outcome.js";

const label = (c) => ({ very_high: "Very High", high: "High", moderate: "Moderate", low: "Low" })[c];

describe("splitOutlookMonths", () => {
  it("keeps three months ahead and lists the past newest first", () => {
    const months = ["2026-08-01", "2026-09-01", "2026-10-01", "2026-11-01", "2026-12-01", "2027-01-01", "2027-02-01"].map(
      (month) => ({ month, issued_on: "2026-11-06" }),
    );
    const { upcoming, past } = splitOutlookMonths(months, new Date(2026, 10, 15));
    assert.deepEqual(upcoming.map((m) => m.month), ["2026-11-01", "2026-12-01", "2027-01-01"]);
    assert.deepEqual(past.map((m) => m.month), ["2026-10-01", "2026-09-01", "2026-08-01"]);
  });

  it("follows the server's kind over the browser's clock", () => {
    const months = [
      { month: "2026-11-01", kind: "past" },
      { month: "2026-12-01", kind: "upcoming" },
    ];
    const { upcoming, past } = splitOutlookMonths(months, new Date(2026, 9, 1));
    assert.deepEqual(upcoming.map((m) => m.month), ["2026-12-01"]);
    assert.deepEqual(past.map((m) => m.month), ["2026-11-01"]);
  });

  it("on the 1st of December the window moves on to February", () => {
    const months = ["2026-11-01", "2026-12-01", "2027-01-01", "2027-02-01"].map((month) => ({ month }));
    const { upcoming, past } = splitOutlookMonths(months, new Date(2026, 11, 1));
    assert.deepEqual(upcoming.map((m) => m.month), ["2026-12-01", "2027-01-01", "2027-02-01"]);
    assert.deepEqual(past.map((m) => m.month), ["2026-11-01"]);
  });
});

describe("monthOf", () => {
  it("is the first of the month", () => {
    assert.equal(monthOf(new Date(2026, 0, 31)), "2026-01-01");
  });
});

describe("outcomeLines", () => {
  const outcome = {
    has_outlook: true,
    complete: true,
    summary: {
      counties: 47,
      flooded: 4,
      by_category: {
        very_high: { rated: 25, flooded: 4 },
        high: { rated: 22, flooded: 0 },
        moderate: { rated: 0, flooded: 0 },
        low: { rated: 0, flooded: 0 },
      },
      flooded_ranks: [3, 6, 11, 22],
    },
  };

  it("says where floods were reported and how the outlook rated them", () => {
    assert.deepEqual(outcomeLines(outcome, label), [
      "Floods were reported in 4 counties.",
      "Of those, the outlook had put 4 of the 25 rated Very High.",
      "Their places on the outlook's list, highest chance first: 3, 6, 11, 22 of 47.",
    ]);
  });

  it("a month still under way says so far", () => {
    const none = { ...outcome, complete: false, summary: { ...outcome.summary, flooded: 0, flooded_ranks: [] } };
    assert.deepEqual(outcomeLines(none, label), ["No flood found reported in any county so far this month."]);
  });

  it("no outlook stored is said plainly", () => {
    assert.deepEqual(outcomeLines({ ...outcome, has_outlook: false }, label), [
      "No county outlook was stored for this month.",
    ]);
  });
});

describe("recordLabel", () => {
  const sources = [{ key: "news", name: "News reports" }, { key: "gdacs", name: "GDACS" }];
  it("names the outlet and the source", () => {
    assert.equal(
      recordLabel({ source: "news", publisher: "People Daily", date: "2026-10-30", title: "T", url: "u" }, sources).by,
      "People Daily, News reports",
    );
    assert.equal(recordLabel({ source: "gdacs", publisher: "GDACS", date: "2026-10-30", title: "T", url: "u" }, sources).by, "GDACS");
  });
});
