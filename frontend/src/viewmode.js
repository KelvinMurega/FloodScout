/**
 * Simple view or detailed map.
 *
 * The simple view is the front page for everyone; the detailed map is one
 * tap away, and the choice is remembered on this device. Administrators and
 * Analysts start on the detailed map, the one their work is in.
 */

const KEY = "floodscout.view";
export const VIEWS = ["simple", "detailed"];

export function defaultView(user) {
  return user?.is_administrator || user?.can_see_analytics ? "detailed" : "simple";
}

function stored() {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

/** The view to show: the one chosen on this device, else the default for `user`. */
export function viewFor(user, chosen = stored()) {
  return VIEWS.includes(chosen) ? chosen : defaultView(user);
}

export function chooseView(view) {
  try {
    localStorage.setItem(KEY, view);
  } catch {
    // Blocked storage: the choice lasts until the page is left.
  }
}
