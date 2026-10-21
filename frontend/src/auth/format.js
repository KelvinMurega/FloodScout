/**
 * Pure helpers for the sign-in and sign-up pages, testable under node.
 */

/**
 * What an auth hash asks for:
 *   signin, signin?error=..., signin/complete/<ticket>, signup, forgot,
 *   reset/<uid>/<token>; `next` is a page to go to afterwards.
 */
export function parseAuthSlug(slug) {
  const [path, query = ""] = String(slug ?? "").split("?");
  const params = new URLSearchParams(query);
  const parts = path.split("/").filter(Boolean);
  const out = { page: parts[0] ?? "signin", error: params.get("error") ?? "", next: "" };
  const next = params.get("next") ?? "";
  if (/^#\/[\w/-]*$/.test(next)) out.next = next;
  if (out.page === "signin" && parts[1] === "complete") {
    out.page = "complete";
    out.ticket = parts[2] ?? "";
  }
  if (out.page === "reset") {
    out.uid = parts[1] ?? "";
    out.token = parts[2] ?? "";
  }
  return out;
}

/** Whether a hash slug is one of the auth pages. */
export function isAuthSlug(slug) {
  const page = String(slug ?? "").split(/[/?]/)[0];
  return ["signin", "signup", "forgot", "reset"].includes(page);
}

const COMMON = new Set([
  "password",
  "password1",
  "12345678",
  "123456789",
  "qwerty123",
  "iloveyou",
  "admin123",
  "letmein1",
  "floodscout",
]);

/**
 * A rough strength for a new password, to guide, not to decide: the server
 * applies Django's validators. { score: 0-4, label, hint }.
 */
export function passwordStrength(password) {
  const pw = String(password ?? "");
  if (!pw) return { score: 0, label: "", hint: "" };
  if (pw.length < 8) return { score: 0, label: "Too short", hint: "Use at least 8 characters." };
  if (/^\d+$/.test(pw)) return { score: 0, label: "Too simple", hint: "Not only numbers." };
  if (COMMON.has(pw.toLowerCase())) {
    return { score: 0, label: "Too common", hint: "This one is on every list of guesses." };
  }
  let score = 1;
  if (pw.length >= 12) score += 1;
  if (pw.length >= 16) score += 1;
  const kinds = [/[a-z]/, /[A-Z]/, /\d/, /[^\w\s]|\s/].filter((re) => re.test(pw)).length;
  if (kinds >= 3) score += 1;
  score = Math.min(4, score);
  const labels = ["Too weak", "Fair", "Good", "Strong", "Very strong"];
  return {
    score,
    label: labels[score],
    hint: score < 3 ? "Longer is stronger: a few unrelated words make a good password." : "",
  };
}
