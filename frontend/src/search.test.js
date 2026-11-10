import assert from "node:assert/strict";
import { test } from "node:test";

import { normalise, searchRegions } from "./search.js";

const ENTRIES = [
  { id: 1, name: "Kisumu", level: "county" },
  { id: 2, name: "Kisii", level: "county" },
  { id: 3, name: "Kisumu Central", level: "sub_county" },
  { id: 4, name: "Murang'a", level: "county" },
  { id: 5, name: "Homa Bay", level: "county" },
  { id: 6, name: "Tana River", level: "county" },
  { id: 7, name: "Nyando", level: "sub_county" },
  { id: 8, name: "Garissa", level: "county" },
];

const names = (query, limit) => searchRegions(ENTRIES, query, limit).map((entry) => entry.name);

test("typing 'kisu' offers Kisumu, the county first", () => {
  assert.deepEqual(names("kisu"), ["Kisumu", "Kisumu Central"]);
});

test("case and surrounding spaces are ignored", () => {
  assert.deepEqual(names("  KISII "), ["Kisii"]);
});

test("accents and apostrophes are ignored", () => {
  assert.equal(normalise("Murang’a"), "murang a");
  assert.deepEqual(names("muranga"), ["Murang'a"]);
  assert.deepEqual(names("Murang'a"), ["Murang'a"]);
  assert.deepEqual(names("Kísumu"), ["Kisumu", "Kisumu Central"]);
});

test("a later word can start with the query", () => {
  assert.deepEqual(names("river"), ["Tana River"]);
  assert.deepEqual(names("bay"), ["Homa Bay"]);
});

test("spaces inside a name are optional", () => {
  assert.deepEqual(names("homabay"), ["Homa Bay"]);
});

test("a word that starts with the query beats a name that only contains it", () => {
  // Alphabetically Garissa would lead; the match at the start of "River" wins.
  assert.deepEqual(names("ri"), ["Tana River", "Garissa"]);
});

test("within one rank, counties come before sub-counties, then by name", () => {
  assert.deepEqual(names("an"), ["Murang'a", "Tana River", "Nyando"]);
});

test("an empty query or no match gives nothing", () => {
  assert.deepEqual(names(""), []);
  assert.deepEqual(names("   "), []);
  assert.deepEqual(names("mombasa"), []);
});

test("the limit caps the list", () => {
  assert.equal(searchRegions(ENTRIES, "a", 3).length, 3);
});

test("entries are passed through untouched", () => {
  assert.equal(searchRegions(ENTRIES, "garissa")[0], ENTRIES[7]);
});
