/**
 * Pure helpers for the profile page, testable under node.
 */

/** Two letters for the avatar: from the full name, else the username. */
export function initials({ first_name: first = "", last_name: last = "", username = "" } = {}) {
  const fromName = `${first.trim()[0] ?? ""}${last.trim()[0] ?? ""}`;
  if (fromName) return fromName.toUpperCase();
  return username.slice(0, 2).toUpperCase() || "?";
}

/** The name to greet someone by. */
export function displayName(user) {
  const full = `${user?.first_name ?? ""} ${user?.last_name ?? ""}`.trim();
  return full || user?.username || "";
}

export const ROLE_LABELS = {
  admin: "System Administrator",
  analyst: "Analyst",
  user: "End User",
};

/**
 * One line of the activity log in plain words. `entry` is an ActivityLog
 * row as the API sends it: { action, detail, at, username }.
 */
export function activityText(entry) {
  const d = entry.detail ?? {};
  switch (entry.action) {
    case "sign_in":
      return "Signed in";
    case "sign_out":
      return "Signed out";
    case "register":
      return "Created the account";
    case "profile_update":
      return `Changed their details${d.fields?.length ? ` (${d.fields.join(", ")})` : ""}`;
    case "password_change":
      return "Changed their password";
    case "rescore":
      return `Rescored ${d.regions ?? "every"} regions for ${d.date ?? "today"}`;
    case "alert_issue":
      return `Issued ${d.headline ?? "an alert"}`;
    case "alert_cancel":
      return `Cancelled ${d.headline ?? "an alert"}`;
    case "alert_run":
      return `Ran the alert rules (${d.issued ?? 0} issued, ${d.ended ?? 0} ended)`;
    case "alert_test":
      return `Sent a test message by ${(d.channels ?? []).join(" and ") || "email"}`;
    case "role_change":
      return `Made ${d.username} ${ROLE_LABELS[d.role] ?? d.role}`;
    case "account_switch":
      return `${d.is_active ? "Switched on" : "Switched off"} ${d.username}'s account`;
    case "subscription_remove":
      return `Stopped alerts for ${(d.regions ?? []).join(", ") || "some places"}`;
    default:
      return entry.action.replace(/_/g, " ");
  }
}

/** "Just now", "5 minutes ago", "3 hours ago", else the date. */
export function ago(iso, now = new Date()) {
  const then = new Date(iso);
  if (Number.isNaN(then.valueOf())) return "";
  const minutes = Math.round((now - then) / 60000);
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  return then.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}
