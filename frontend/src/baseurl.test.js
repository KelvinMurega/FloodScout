import assert from "node:assert/strict";
import { test } from "node:test";

import { chooseBaseUrl } from "./baseurl.js";

const FALLBACK = "http://localhost:8000/api";

test("a built dashboard ignores ?api= on the page URL", () => {
  assert.equal(
    chooseBaseUrl({
      search: "?api=https://evil.example/api",
      configured: "https://floodscout.example/api",
      fallback: FALLBACK,
    }),
    "https://floodscout.example/api",
  );
});

test("the dev server follows ?api=", () => {
  assert.equal(
    chooseBaseUrl({ search: "?api=http://localhost:8010/api/", fallback: FALLBACK, dev: true }),
    "http://localhost:8010/api",
  );
});

test("the configured URL beats the fallback, without its trailing slash", () => {
  assert.equal(chooseBaseUrl({ configured: "http://x/api//", fallback: FALLBACK }), "http://x/api");
  assert.equal(chooseBaseUrl({ fallback: FALLBACK }), FALLBACK);
});
