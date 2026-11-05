/*
 * FloodScout's service worker: the app installs, and still opens without a
 * connection, showing the map as it was last loaded.
 *
 *   pages        network first; offline, the cached page
 *   built files  (assets/, hashed names) cache first: a name never changes
 *   map layers   (layers/) cached, refreshed in the background
 *   public API   GET with no sign-in: network first; offline, the last copy,
 *                marked with X-FloodScout-Cached so the page can say so
 *
 * A request carrying a sign-in token is never cached: a person's profile,
 * alerts and places stay off the device. Map tiles are not cached either;
 * offline, the regions are drawn on a blank background.
 *
 * BUILD is stamped by vite.config.js at each build, so a new deploy installs
 * a new worker and drops the old page cache.
 */

const BUILD = "__BUILD__";
const SHELL = `floodscout-shell-${BUILD}`;
const ASSETS = "floodscout-assets";
const DATA = "floodscout-data";
const DATA_LIMIT = 400;
const SHELL_FILES = ["./", "./index.html", "./manifest.webmanifest", "./icons/icon-192.png", "./icons/favicon-32.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((cache) => cache.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith("floodscout-shell-") && k !== SHELL).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

async function trim(cache) {
  const keys = await cache.keys();
  for (const key of keys.slice(0, Math.max(0, keys.length - DATA_LIMIT))) await cache.delete(key);
}

async function networkFirst(request, cacheName, { mark = false } = {}) {
  const cache = await caches.open(cacheName);
  try {
    const response = await fetch(request);
    if (response.ok) {
      await cache.put(request, response.clone());
      if (cacheName === DATA) trim(cache);
    }
    return response;
  } catch (error) {
    const cached = await cache.match(request);
    if (!cached) throw error;
    if (!mark) return cached;
    const headers = new Headers(cached.headers);
    headers.set("X-FloodScout-Cached", cached.headers.get("Date") || "1");
    return new Response(await cached.blob(), { status: cached.status, statusText: cached.statusText, headers });
  }
}

async function cacheFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok) await cache.put(request, response.clone());
  return response;
}

async function refreshInBackground(request, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request);
  const fresh = fetch(request)
    .then((response) => {
      if (response.ok) cache.put(request, response.clone());
      return response;
    })
    .catch(() => cached);
  return cached || fresh;
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET" || request.headers.has("Authorization")) return;
  const url = new URL(request.url);
  const sameOrigin = url.origin === self.location.origin;

  if (request.mode === "navigate") {
    event.respondWith(networkFirst(request, SHELL).catch(() => caches.match("./index.html")));
  } else if (sameOrigin && url.pathname.includes("/assets/")) {
    event.respondWith(cacheFirst(request, ASSETS));
  } else if (sameOrigin && url.pathname.includes("/layers/")) {
    event.respondWith(refreshInBackground(request, DATA));
  } else if (url.pathname.includes("/api/") && !url.pathname.includes("/api/auth/")) {
    event.respondWith(networkFirst(request, DATA, { mark: true }));
  }
});
