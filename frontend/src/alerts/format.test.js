import assert from "node:assert/strict";
import { test } from "node:test";

import {
  alertPlace,
  bannerText,
  countByLevel,
  errorMessage,
  filterAlerts,
  groupByLevel,
  normalisePhone,
  placeOptions,
} from "./format.js";

const ALERTS = [
  { level: "watch", region_name: "Nyando", county_name: "Kisumu" },
  { level: "warning", region_name: "Galole", county_name: "Tana River" },
  { level: "watch", region_name: "Garissa", county_name: "Garissa" },
];

test("counts and the banner read naturally", () => {
  const counts = countByLevel(ALERTS);
  assert.deepEqual(counts, { warning: 1, watch: 2, advisory: 0 });
  assert.equal(bannerText(counts), "1 flood warning and 2 watches in force");
  assert.equal(bannerText({ warning: 0, watch: 0, advisory: 3 }), "3 flood advisories in force");
  assert.equal(
    bannerText({ warning: 2, watch: 1, advisory: 1 }),
    "2 flood warnings, 1 watch and 1 advisory in force",
  );
  assert.equal(bannerText({ warning: 0, watch: 0, advisory: 0 }), "");
});

test("groups come most severe first and skip empty levels", () => {
  assert.deepEqual(
    groupByLevel(ALERTS).map(([level, list]) => [level, list.length]),
    [
      ["warning", 1],
      ["watch", 2],
    ],
  );
});

test("filtering matches the place or its county", () => {
  assert.equal(filterAlerts(ALERTS, "kisumu").length, 1);
  assert.equal(filterAlerts(ALERTS, "GAR").length, 1);
  assert.equal(filterAlerts(ALERTS, "  ").length, 3);
});

test("phone numbers are read the way Kenyans write them", () => {
  for (const raw of ["0712 345 678", "712345678", "254712345678", "+254 712-345-678"]) {
    assert.equal(normalisePhone(raw), "+254712345678");
  }
  assert.equal(normalisePhone("0110123456"), "+254110123456");
  assert.equal(normalisePhone("0812345678"), null);
  assert.equal(normalisePhone(""), null);
});

test("each county is followed by its sub-counties", () => {
  const options = placeOptions([
    { id: 1, name: "Kisumu", county_name: "Kisumu", level: "county" },
    { id: 9, name: "Nyando", county_name: "Kisumu", level: "sub_county" },
    { id: 2, name: "Garissa", county_name: "Garissa", level: "county" },
  ]);
  assert.deepEqual(
    options.map((o) => o.label),
    ["Kisumu County", "Nyando, Kisumu", "Garissa County"],
  );
});

test("errorMessage finds the message in any of DRF's error shapes", () => {
  assert.equal(errorMessage({ detail: "No." }), "No.");
  assert.equal(errorMessage({ email: ["Enter a valid email address."] }), "Enter a valid email address.");
  assert.equal(errorMessage({ non_field_errors: ["Give an email."] }), "Give an email.");
  assert.equal(errorMessage({ regions: { regions: "One does not exist." } }), "One does not exist.");
  assert.equal(errorMessage(null), null);
});

test("a place reads with its county", () => {
  assert.equal(
    alertPlace({ region_level: "sub_county", region_name: "Tharaka", county_name: "Tharaka" }),
    "Tharaka, Tharaka County",
  );
  assert.equal(alertPlace({ region_level: "county", region_name: "Garissa" }), "Garissa County");
});
