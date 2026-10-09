import assert from "node:assert/strict";
import { test } from "node:test";

import { CSV_COLUMNS, csvCell, reportFileName, scoresToCsv, sortByRisk } from "./report.js";

const SCORES = [
  {
    region_name: "Kisumu",
    county_name: "Kisumu",
    region_level: "county",
    risk_category: "high",
    times_usual: 1.5,
    predicted_value: 0.149,
    prediction_date: "2026-10-08T00:00:00+03:00",
    model_version: "flood-random-forest-20261007",
  },
  {
    region_name: "Garissa",
    county_name: "Garissa",
    region_level: "county",
    risk_category: "very_high",
    times_usual: 3.2,
    predicted_value: 0.309,
    prediction_date: "2026-10-08T00:00:00+03:00",
    model_version: "flood-random-forest-20261007",
  },
];

test("sortByRisk puts the highest model output first and does not mutate", () => {
  const sorted = sortByRisk(SCORES);

  assert.deepEqual(
    sorted.map((score) => score.region_name),
    ["Garissa", "Kisumu"],
  );
  assert.equal(SCORES[0].region_name, "Kisumu");
});

test("sortByRisk breaks ties by name and puts unscored rows last", () => {
  const sorted = sortByRisk([
    { region_name: "Wajir", predicted_value: 0.2 },
    { region_name: "Nowhere" },
    { region_name: "Mandera", predicted_value: 0.2 },
  ]);

  assert.deepEqual(
    sorted.map((score) => score.region_name),
    ["Mandera", "Wajir", "Nowhere"],
  );
});

test("csvCell quotes commas, quotes and line breaks, and leaves the rest alone", () => {
  assert.equal(csvCell("Kisumu"), "Kisumu");
  assert.equal(csvCell(0.149), "0.149");
  assert.equal(csvCell(null), "");
  assert.equal(csvCell(undefined), "");
  assert.equal(csvCell("Tana River, North"), '"Tana River, North"');
  assert.equal(csvCell('The "Lake" region'), '"The ""Lake"" region"');
  assert.equal(csvCell("two\nlines"), '"two\nlines"');
});

test("scoresToCsv writes a header and one line per score, highest risk first", () => {
  const lines = scoresToCsv(SCORES).split("\r\n");

  assert.equal(lines[0], CSV_COLUMNS.join(","));
  assert.equal(
    lines[1],
    "Garissa,Garissa,county,Very high,3.2,0.309,2026-10-08,flood-random-forest-20261007",
  );
  assert.equal(
    lines[2],
    "Kisumu,Kisumu,county,High,1.5,0.149,2026-10-08,flood-random-forest-20261007",
  );
  assert.equal(lines[3], ""); // the file ends with a line break
  assert.equal(lines.length, 4);
});

test("scoresToCsv uses the month for an outlook row and leaves a missing multiple empty", () => {
  const csv = scoresToCsv([
    {
      region_name: "Nyando",
      county_name: "Kisumu",
      region_level: "sub_county",
      risk_category: "moderate",
      times_usual: null,
      predicted_value: 0.07,
      month: "2026-11-01",
      model_version: "flood-random-forest-20261007",
    },
  ]);

  assert.equal(
    csv.split("\r\n")[1],
    "Nyando,Kisumu,sub_county,Moderate,,0.07,2026-11-01,flood-random-forest-20261007",
  );
});

test("scoresToCsv with no scores is the header alone", () => {
  assert.equal(scoresToCsv([]), `${CSV_COLUMNS.join(",")}\r\n`);
});

test("reportFileName names the view and the date", () => {
  assert.equal(reportFileName("Lake Basin", "2026-10-08"), "floodscout-lake-basin-2026-10-08.csv");
  assert.equal(reportFileName("", "2026-10-08"), "floodscout-kenya-2026-10-08.csv");
  assert.equal(reportFileName("North Eastern"), "floodscout-north-eastern.csv");
});
