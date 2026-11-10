import assert from "node:assert/strict";
import { test } from "node:test";

import { offlineText } from "./offline.js";

test("the offline banner says how old the copy on screen is", () => {
  const now = Date.parse("2026-11-05T12:00:00Z");
  assert.equal(
    offlineText(now - 3 * 3600 * 1000, now),
    "Offline. Showing what FloodScout last loaded, 3 hours ago. Warnings and scores may have changed since.",
  );
  assert.match(offlineText(now - 1 * 60000, now), /1 minute ago/);
  assert.match(offlineText(now - 3 * 86400000, now), /3 days ago/);
  assert.equal(offlineText(0, now), "Offline. Showing what FloodScout last loaded on this device.");
});
