/**
 * Where the API lives, from what the page knows. Kept apart from api.js,
 * which reads import.meta.env, so the tests can run under plain Node.
 *
 * `?api=` on the page URL is honoured only by the dev server. In a build it
 * would let a link like ?api=https://evil.example/api send the saved token,
 * and any password typed into the sign-in form, to someone else's server.
 */

export function chooseBaseUrl({ search = "", configured = "", fallback, dev = false }) {
  const fromQuery = dev ? new URLSearchParams(search).get("api") : null;
  return (fromQuery || configured || fallback).replace(/\/+$/, "");
}
