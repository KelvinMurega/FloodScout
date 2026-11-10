import assert from "node:assert/strict";
import { test } from "node:test";

import {
  accountChecks,
  activityText,
  ago,
  byMonth,
  displayName,
  groupDeliveries,
  initials,
  placeStatus,
} from "./format.js";

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
  assert.equal(
    activityText({ action: "alerts_stopped_for", detail: { username: "", contact: "s***@example.com" } }),
    "Stopped alerts for s***@example.com at their request",
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

test("deliveries group by alert, level and kind, keeping each channel once", () => {
  const alert = { id: 7, headline: "Flood watch" };
  const groups = groupDeliveries([
    { alert, level: "warning", kind: "alert", channel: "sms", outcome: "failed", sent_at: "2026-11-02T09:00:00Z" },
    { alert, level: "warning", kind: "alert", channel: "email", outcome: "sent", sent_at: "2026-11-02T09:00:00Z" },
    { alert, level: "watch", kind: "alert", channel: "email", outcome: "sent", sent_at: "2026-11-01T09:00:00Z" },
  ]);
  assert.deepEqual(
    groups.map((g) => [g.level, g.channels.map((c) => `${c.channel}:${c.outcome}`)]),
    [
      ["warning", ["sms:failed", "email:sent"]],
      ["watch", ["email:sent"]],
    ],
  );
  assert.equal(groups[0].at, "2026-11-02T09:00:00Z");
});

test("entries split by month in order", () => {
  const months = byMonth([{ at: "2026-11-02T09:00:00Z" }, { at: "2026-11-01T09:00:00Z" }, { at: "2026-10-20T09:00:00Z" }]);
  assert.equal(months.length, 2);
  assert.equal(months[0][1].length, 2);
});

test("account checks say what is left", () => {
  const me = { email: "w@example.com", email_verified: true, phone: "" };
  const checks = accountChecks(me, { region: 3, alerts_on: true }, { has_password: true, providers: [] });
  assert.deepEqual(
    checks.map((c) => [c.id, c.done]),
    [
      ["email", true],
      ["home", true],
      ["ways", false],
    ],
  );
  assert.equal(accountChecks(me, null, null).length, 2);
});

test("a saved place says whether its warnings are reaching the person", () => {
  const place = { alerts_on: true, receiving: true, alert_level: "warning" };
  assert.equal(placeStatus(place, { email: "a@example.com" }), "Warnings on: warnings only");
  assert.equal(placeStatus({ ...place, receiving: false }, { email: "a@example.com" }), "Waiting for your email to be confirmed");
  assert.equal(placeStatus({ ...place, receiving: false }, { email: "" }), "Add an email to your profile to get these");
  assert.equal(placeStatus({ ...place, alerts_on: false }, {}), "Warnings off");
});
