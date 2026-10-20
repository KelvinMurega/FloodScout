/**
 * Small pure helpers for the map layers (layers.js), kept apart from
 * Leaflet so they can be tested under node.
 */

/**
 * The day to ask NASA GIBS for: `isoDate` (the day on screen), but never
 * later than `lag` days before `today`, the newest day the layer is
 * published for. With no `isoDate`, that newest day.
 */
export function gibsDate(isoDate, lag, today = new Date()) {
  const latest = new Date(Date.UTC(today.getFullYear(), today.getMonth(), today.getDate() - lag));
  const wanted = isoDate ? new Date(`${isoDate}T00:00:00Z`) : latest;
  const day = Number.isNaN(wanted.valueOf()) || wanted > latest ? latest : wanted;
  return day.toISOString().slice(0, 10);
}

/** Marker radius in pixels for a past flood: grows with the people it displaced. */
export function floodRadius(displaced) {
  return 5 + Math.min(11, Math.sqrt(Math.max(0, displaced ?? 0)) / 60);
}
