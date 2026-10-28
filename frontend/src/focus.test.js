import assert from "node:assert/strict";
import { test } from "node:test";

import { KENYA_SLUG, alertSummary, countySlug, overviewScope, regionSummaries } from "./focus.js";

const COAST = {
  slug: "coast",
  name: "Coast",
  counties: [
    { id: 1, name: "Mombasa" },
    { id: 2, name: "Kilifi" },
  ],
};
const NAIROBI = { slug: "nairobi-metro", name: "Nairobi metro", counties: [{ id: 3, name: "Nairobi" }] };

test("the empty hash is the focus overview and #/kenya is All Kenya", () => {
  assert.equal(overviewScope(""), "focus");
  assert.equal(overviewScope(KENYA_SLUG), "kenya");
  assert.equal(overviewScope("coast"), null);
});

test("a county outside the focus regions belongs on All Kenya", () => {
  const focusByCountyId = new Map([[1, COAST]]);
  assert.equal(countySlug(1, focusByCountyId), "");
  assert.equal(countySlug(9, focusByCountyId), KENYA_SLUG);
});

test("each region gets its worst county and the alerts in its counties and sub-counties", () => {
  const scores = new Map([
    [1, { risk_category: "moderate" }],
    [2, { risk_category: "very_high" }],
  ]);
  const alerts = [
    { county_name: "Kilifi", level: "watch" },
    { county_name: "Kilifi", level: "warning" },
    { county_name: "Mombasa", level: "watch" },
    { county_name: "Garissa", level: "warning" },
  ];
  const [coast, nairobi] = regionSummaries([COAST, NAIROBI], scores, alerts);
  assert.deepEqual(coast, {
    slug: "coast",
    name: "Coast",
    worst: "very_high",
    alerts: { warning: 1, watch: 2, advisory: 0 },
  });
  assert.equal(nairobi.worst, null);
  assert.deepEqual(nairobi.alerts, { warning: 0, watch: 0, advisory: 0 });
});

test("alert counts read as words, strongest first", () => {
  assert.equal(alertSummary({ warning: 1, watch: 2, advisory: 0 }), "1 warning, 2 watches");
  assert.equal(alertSummary({ warning: 0, watch: 0, advisory: 3 }), "3 advisories");
  assert.equal(alertSummary({ warning: 0, watch: 0, advisory: 0 }), "No alerts");
});
