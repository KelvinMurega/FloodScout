import assert from "node:assert/strict";
import { test } from "node:test";

import { activityText, ago, displayName, initials } from "./format.js";

test("initials come from the name, else the username", () => {
  assert.equal(initials({ first_name: "Kelvin", last_name: "Murega", username: "km" }), "KM");
  assert.equal(initials({ username: "admin" }), "AD");
  assert.equal(initials({}), "?");
});

test("displayName prefers the full name", () => {
  assert.equal(displayName({ first_name: "Kelvin", last_name: "Murega", username: "km" }), "Kelvin Murega");
  assert.equal(displayName({ first_name: "", last_name: "", username: "admin" }), "admin");
});

test("activity reads as plain words", () => {
  assert.equal(activityText({ action: "sign_in" }), "Signed in");
  assert.equal(
    activityText({ action: "profile_update", detail: { fields: ["email", "phone"] } }),
    "Changed their details (email, phone)",
  );
  assert.equal(
    activityText({ action: "role_change", detail: { username: "wanjiku", role: "admin" } }),
    "Made wanjiku System Administrator",
  );
  assert.equal(
    activityText({ action: "alert_issue", detail: { headline: "Flood warning for Nyando" } }),
    "Issued Flood warning for Nyando",
  );
  assert.equal(activityText({ action: "something_new" }), "something new");
});

test("ago says how long ago", () => {
  const now = new Date("2026-10-21T12:00:00Z");
  assert.equal(ago("2026-10-21T11:59:40Z", now), "Just now");
  assert.equal(ago("2026-10-21T11:55:00Z", now), "5 minutes ago");
  assert.equal(ago("2026-10-21T09:00:00Z", now), "3 hours ago");
  assert.equal(ago("not a date", now), "");
});
