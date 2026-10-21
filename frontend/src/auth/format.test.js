import assert from "node:assert/strict";
import { test } from "node:test";

import { isAuthSlug, parseAuthSlug, passwordStrength } from "./format.js";

test("parseAuthSlug reads each auth page", () => {
  assert.deepEqual(parseAuthSlug("signin"), { page: "signin", error: "", next: "" });
  assert.equal(parseAuthSlug("signin?error=The%20sign-in%20was%20cancelled.").error, "The sign-in was cancelled.");
  assert.deepEqual(parseAuthSlug("signin/complete/abc123"), {
    page: "complete",
    ticket: "abc123",
    error: "",
    next: "",
  });
  assert.equal(parseAuthSlug("signup").page, "signup");
  const reset = parseAuthSlug("reset/MQ/c1x-abc");
  assert.deepEqual([reset.page, reset.uid, reset.token], ["reset", "MQ", "c1x-abc"]);
});

test("next is kept only when it is a dashboard page", () => {
  assert.equal(parseAuthSlug("signin?next=%23%2Falerts").next, "#/alerts");
  assert.equal(parseAuthSlug("signin?next=https%3A%2F%2Fevil.example").next, "");
});

test("isAuthSlug knows the auth pages and nothing else", () => {
  for (const slug of ["signin", "signin?error=x", "signin/complete/t", "signup", "forgot", "reset/a/b"]) {
    assert.equal(isAuthSlug(slug), true, slug);
  }
  for (const slug of ["", "alerts", "profile", "signinx", "coast"]) {
    assert.equal(isAuthSlug(slug), false, slug);
  }
});

test("passwordStrength guides without deciding", () => {
  assert.equal(passwordStrength("").score, 0);
  assert.equal(passwordStrength("short").label, "Too short");
  assert.equal(passwordStrength("1234567890").label, "Too simple");
  assert.equal(passwordStrength("Password").label, "Too common");
  assert.equal(passwordStrength("abcdefgh").label, "Fair");
  assert.ok(passwordStrength("river tana floods 2023!").score >= 3);
});
