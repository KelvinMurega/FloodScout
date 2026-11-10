/**
 * Installing FloodScout and opening it offline (public/sw.js), and saying so.
 *
 * The service worker serves the last copy of the map's data when there is no
 * connection, marked with X-FloodScout-Cached. api.js tells this module
 * about each fresh or cached answer; the banner under the header then says
 * the person is offline and how old what they see is.
 */

const LAST_KEY = "floodscout.lastFresh";

/** "Offline. Showing what FloodScout last loaded, 3 hours ago." */
export function offlineText(lastFreshMs, nowMs = Date.now()) {
  if (!lastFreshMs) return "Offline. Showing what FloodScout last loaded on this device.";
  const minutes = Math.max(0, Math.round((nowMs - lastFreshMs) / 60000));
  let ago;
  if (minutes < 1) ago = "just now";
  else if (minutes < 60) ago = `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  else if (minutes < 60 * 48) {
    const hours = Math.round(minutes / 60);
    ago = `${hours} hour${hours === 1 ? "" : "s"} ago`;
  } else ago = `${Math.round(minutes / 1440)} days ago`;
  return `Offline. Showing what FloodScout last loaded, ${ago}. Warnings and scores may have changed since.`;
}

function readLast() {
  try {
    return Number(localStorage.getItem(LAST_KEY)) || 0;
  } catch {
    return 0;
  }
}

let banner = null;
let servedFromCache = false;

function update() {
  if (!banner) return;
  const offline = typeof navigator !== "undefined" && navigator.onLine === false;
  banner.hidden = !(offline || servedFromCache);
  if (!banner.hidden) banner.textContent = offlineText(readLast());
}

/** api.js: an answer came from the network (fresh) or from the device's copy. */
export function noteAnswer(response) {
  if (response.headers.get("X-FloodScout-Cached")) {
    servedFromCache = true;
  } else {
    servedFromCache = false;
    try {
      localStorage.setItem(LAST_KEY, String(Date.now()));
    } catch {
      // Storage blocked: the banner just cannot say how old the copy is.
    }
  }
  update();
}

/** Register the service worker (built site only) and put the banner under `anchor`. */
export function startOffline(anchor) {
  banner = document.createElement("p");
  banner.className = "offline-banner";
  banner.setAttribute("role", "status");
  banner.hidden = true;
  anchor.after(banner);
  window.addEventListener("online", () => {
    servedFromCache = false;
    update();
  });
  window.addEventListener("offline", update);
  update();

  if ("serviceWorker" in navigator && import.meta.env.PROD) {
    navigator.serviceWorker.register(`${import.meta.env.BASE_URL}sw.js`).catch((error) => console.error(error));
  }
}
