/**
 * The account page (#/profile), shown in place of the map.
 *
 *   #/profile              at a glance, what is left to set up, where you
 *                          live and your details
 *   #/profile/alerts       the places you get alerts for and every alert
 *                          sent to you (the account menu's "Flood alerts")
 *   #/profile/security     password, sign-in methods and recent activity
 *   #/profile/users        System Administrator: every account, role and on/off
 *   #/profile/subscribers  System Administrator: everyone signed up for alerts
 *   #/profile/activity     System Administrator: what everyone has done
 *
 * Signed out, it sends the person to the sign-in page and back. What it
 * shows is what the API returns; the API checks every change.
 */

import {
  changePassword,
  fetchHome,
  fetchSocialAccounts,
  fetchSubscribers,
  stopSubscriber,
  startSocialSignIn,
  unlinkSocialAccount,
  fetchActivity,
  fetchMe,
  fetchMyActivity,
  fetchMyAlerts,
  fetchMySubscriptions,
  fetchUsers,
  removeMySubscription,
  updateMe,
  updateUser,
} from "../api.js";
import { LEVEL_LABELS } from "../alerts/format.js";
import { codeBox } from "../auth/code.js";
import {
  ROLE_LABELS,
  accountChecks,
  activityText,
  ago,
  byMonth,
  displayName,
  groupDeliveries,
  initials,
} from "./format.js";
import { renderHomeCard } from "./home.js";
import { renderPlacesCard } from "./places.js";
import "./profile.css";

const TABS = [
  { id: "", label: "Overview" },
  { id: "alerts", label: "My flood alerts" },
  { id: "security", label: "Sign-in & security" },
  { id: "users", label: "Users", admin: true },
  { id: "subscribers", label: "Subscribers", admin: true },
  { id: "activity", label: "Activity log", admin: true },
];

const LEVELS = { advisory: "Advisories and up", watch: "Watches and warnings", warning: "Warnings only" };

let lastRender = null;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function field(label, input, hint) {
  const row = el("label", "alerts-form__field");
  row.append(el("span", "alerts-form__label", label), input);
  if (hint) row.append(el("span", "alerts-form__hint", hint));
  return row;
}

function input(type, value = "", attrs = {}) {
  const node = el("input", "alerts-form__input");
  node.type = type;
  node.value = value ?? "";
  Object.assign(node, attrs);
  return node;
}

function note() {
  const node = el("p", "alerts-form__message");
  node.setAttribute("role", "status");
  return node;
}

function say(node, text, error = false) {
  node.textContent = text;
  node.classList.toggle("alerts-form__message--error", error);
}

function card(parent, title, subtitle) {
  const node = el("section", "admin-card");
  const head = el("header", "admin-card__head");
  head.append(el("h3", "admin-card__title", title));
  if (subtitle) head.append(el("p", "admin-card__subtitle", subtitle));
  node.append(head);
  parent.append(node);
  return node;
}

/* ------------------------------------------------------------ my profile */

function renderDetails(parent, me, onSaved, onConfirmed) {
  const box = card(parent, "Your details", "Your email and phone are used for alerts you ask for, and are not shown to others.");
  box.id = "profile-details";
  // An email not yet confirmed gets no warnings; confirm it here, unless
  // the card for where they live already offers it (home.js).
  if (me.email && !me.email_verified && !me.home_region) {
    const confirm = el("div", "profile-confirm");
    confirm.append(
      el("p", "profile-confirm__title", "Your email is not confirmed yet"),
      codeBox({ email: me.email, sent: false, onConfirmed: () => onConfirmed?.() }),
    );
    box.append(confirm);
  }
  const form = el("form", "alerts-form profile-grid");
  const first = input("text", me.first_name, { autocomplete: "given-name" });
  const last = input("text", me.last_name, { autocomplete: "family-name" });
  const email = input("email", me.email, { autocomplete: "email" });
  const phone = input("tel", me.phone, { autocomplete: "tel", placeholder: "0712 345 678" });
  const organisation = input("text", me.organisation, { autocomplete: "organization", placeholder: "e.g. Kisumu County Disaster Management" });
  const message = note();
  const save = el("button", "auth__button auth__button--primary", "Save details");
  save.type = "submit";
  form.append(
    field("First name", first),
    field("Last name", last),
    field("Email", email),
    field("Phone", phone),
    field("Organisation", organisation),
    save,
    message,
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    save.disabled = true;
    try {
      const saved = await updateMe({
        first_name: first.value.trim(),
        last_name: last.value.trim(),
        email: email.value.trim(),
        phone: phone.value.trim(),
        organisation: organisation.value.trim(),
      });
      phone.value = saved.phone;
      say(message, "Saved.");
      onSaved(saved);
    } catch (error) {
      say(message, error.message, true);
    } finally {
      save.disabled = false;
    }
  });
  box.append(form);
}

function renderPassword(parent, methods) {
  const box = card(parent, "Password", "Changing it signs you out everywhere else.");
  const form = el("form", "alerts-form");
  const current = input("password", "", { autocomplete: "current-password" });
  // An account made through Google, Microsoft or GitHub has no password to
  // give: it sets one instead of changing it.
  methods.then((info) => {
    if (info && !info.has_password) {
      box.querySelector(".admin-card__title").textContent = "Set a password";
      box.querySelector(".admin-card__subtitle").textContent =
        "You signed up with another account. A password lets you sign in without it.";
      current.closest("label").remove();
      submit.textContent = "Set password";
    }
  });
  const next = input("password", "", { autocomplete: "new-password" });
  const again = input("password", "", { autocomplete: "new-password" });
  const message = note();
  const submit = el("button", "auth__button auth__button--primary", "Change password");
  submit.type = "submit";
  form.append(field("Current password", current), field("New password", next), field("New password again", again), submit, message);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (next.value !== again.value) {
      say(message, "The two new passwords are not the same.", true);
      return;
    }
    submit.disabled = true;
    try {
      await changePassword({ current_password: current.value, new_password: next.value });
      form.reset();
      say(message, "Password changed. Any other sign-ins have been signed out.");
    } catch (error) {
      say(message, error.message, true);
    } finally {
      submit.disabled = false;
    }
  });
  box.append(form);
}

/** Google, Microsoft and GitHub: which are linked, link or unlink them. */
async function renderSignInMethods(parent, methods) {
  const box = card(parent, "Sign-in methods", "Google, Microsoft and GitHub accounts you can sign in with.");
  const list = el("div", "profile-list");
  list.append(el("p", "alerts-form__hint", "Loading…"));
  box.append(list);
  const info = await methods;
  if (!info) {
    list.replaceChildren(el("p", "alerts-form__hint", "Could not load them."));
    return;
  }
  list.replaceChildren();
  const linkedCount = info.providers.filter((p) => p.linked).length;
  for (const provider of info.providers) {
    if (!provider.linked && !provider.available) continue;
    const item = el("div", "profile-list__item");
    const text = el("div");
    text.append(
      el("strong", null, provider.name),
      el(
        "span",
        "alerts-form__hint",
        provider.linked ? `Linked${provider.email ? ` as ${provider.email}` : ""}` : "Not linked",
      ),
    );
    const note = el("span", "alerts-form__hint");
    const action = el("button", "auth__button", provider.linked ? "Unlink" : "Link");
    action.type = "button";
    const lastWayIn = provider.linked && linkedCount === 1 && !info.has_password;
    if (lastWayIn) {
      action.disabled = true;
      action.title = "Your only way to sign in. Set a password first.";
    }
    action.addEventListener("click", async () => {
      action.disabled = true;
      try {
        if (provider.linked) {
          await unlinkSocialAccount(provider.id);
          item.remove();
        } else {
          window.location.assign(await startSocialSignIn(provider.key, { next: "#/profile" }));
        }
      } catch (error) {
        note.textContent = error.message;
        action.disabled = false;
      }
    });
    text.append(note);
    item.append(text, action);
    list.append(item);
  }
  if (!list.children.length) {
    list.append(
      el("p", "alerts-form__hint", "Sign-in with Google, Microsoft or GitHub is not set up on this server yet."),
    );
  }
}

/* ------------------------------------------------------ my flood alerts */

function pill(level) {
  return el("span", `alert-pill alert-pill--${level}`, LEVEL_LABELS[level]?.replace("Flood ", "") ?? level);
}

function when(node, iso) {
  node.dateTime = iso;
  node.title = new Date(iso).toLocaleString();
  return node;
}

/** One tile of figures; `href` makes the whole tile a link. */
function tile(parent, label, value, note, { href, tone } = {}) {
  const node = el(href ? "a" : "div", `admin-tile profile-tile${tone ? ` profile-tile--${tone}` : ""}`);
  if (href) node.href = href;
  node.append(el("p", "admin-tile__label", label), el("p", "admin-tile__value", value));
  if (note) node.append(el("p", "admin-tile__note", note));
  parent.append(node);
  return node;
}

function loading(parent) {
  const holder = el("div");
  holder.append(el("p", "alerts-form__hint", "Loading…"));
  parent.append(holder);
  return holder;
}

/** ago() mid-sentence: "just now", else as it is ("5 minutes ago", "3 Oct 2026"). */
function inline(text) {
  return text === "Just now" ? "just now" : text;
}

/** "By email to w@…" for a subscription. */
function channelText(row) {
  return row.email ? `By email to ${row.email}` : "No email";
}

function subscriptionItem(row, received, onStopped) {
  const item = el("article", "profile-sub");
  const head = el("div", "profile-sub__head");
  const title = el("div", "profile-sub__title");
  if (row.is_home) title.append(el("span", "profile-badge profile-badge--home", "Where you live"));
  title.append(el("strong", null, row.regions.join(", ") || "No places"));
  const state = row.confirmed
    ? el("span", "profile-badge profile-badge--on", "Active")
    : el("span", "profile-badge profile-badge--waiting", "Waiting for the code");
  head.append(title, state);

  const facts = el("dl", "profile-sub__facts");
  for (const [term, value] of [
    ["Alerts for", LEVELS[row.min_level] ?? row.min_level],
    ["Sent", channelText(row)],
    ["Received", received ? `${received} alert${received === 1 ? "" : "s"}` : "None yet"],
    ["Since", new Date(row.created_at).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })],
  ]) {
    const pair = el("div");
    pair.append(el("dt", null, term), el("dd", null, value));
    facts.append(pair);
  }

  const actions = el("div", "profile-sub__actions");
  if (row.is_home) {
    const change = el("a", "link-button", "Change where you live");
    change.href = "#/profile";
    actions.append(change);
  }
  const stop = el("button", "auth__button", row.is_home ? "Turn off" : "Stop these alerts");
  stop.type = "button";
  const message = el("span", "alerts-form__hint");
  stop.addEventListener("click", async () => {
    stop.disabled = true;
    try {
      await removeMySubscription(row.id);
      item.remove();
      onStopped?.();
    } catch (error) {
      message.textContent = error.message;
      stop.disabled = false;
    }
  });
  actions.append(stop, message);
  item.append(head, facts, actions);
  return item;
}

function deliveryItem(entry) {
  const item = el("li", `profile-sent profile-sent--${entry.kind === "cancel" ? "cancel" : entry.level}`);
  const marker = el("span", "profile-sent__marker");
  marker.setAttribute("aria-hidden", "true");
  const main = el("div", "profile-sent__main");
  const top = el("div", "profile-sent__top");
  top.append(entry.kind === "cancel" ? el("span", "profile-badge", "All clear") : pill(entry.level));
  const link = el("a", "profile-sent__headline", entry.kind === "cancel" ? `Cancelled: ${entry.alert.headline}` : entry.alert.headline);
  link.href = `#/alerts/${entry.alert.id}`;
  top.append(link);

  const meta = el("div", "profile-sent__meta");
  meta.append(el("span", null, entry.alert.region));
  for (const c of entry.channels) {
    const ok = c.outcome === "sent";
    const chip = el(
      "span",
      `profile-channel${ok ? "" : " profile-channel--failed"}`,
      `${c.channel === "sms" ? "SMS" : "Email"} ${ok ? "delivered" : "failed"}`,
    );
    chip.prepend(el("span", "profile-channel__mark", ok ? "✓ " : "✕ "));
    meta.append(chip);
  }
  if (entry.kind !== "cancel") {
    meta.append(
      el(
        "span",
        `profile-sent__status${entry.alert.status === "active" ? " profile-sent__status--active" : ""}`,
        entry.alert.status === "active" ? "Still in force" : entry.alert.status === "cancelled" ? "Since cancelled" : "Now over",
      ),
    );
  }

  main.append(top, meta);
  const at = when(el("time", "profile-sent__when", ago(entry.at)), entry.at);
  item.append(marker, main, at);
  return item;
}

/** Every alert sent to you, by month, a page at a time. */
function sentList(parent, entries) {
  if (entries.length === 0) {
    const empty = el("div", "profile-empty");
    empty.append(
      el("p", "profile-empty__title", "Nothing has been sent to you yet"),
      el(
        "p",
        "alerts-form__hint",
        "When a flood alert is issued for one of your places, it is listed here with how it reached you. No news is good news.",
      ),
    );
    parent.append(empty);
    return;
  }
  const PAGE_SIZE = 20;
  const list = el("div", "profile-sent-list");
  const more = el("button", "auth__button profile-more", "Show older alerts");
  more.type = "button";
  let shown = 0;
  const draw = () => {
    shown += PAGE_SIZE;
    list.replaceChildren();
    for (const [month, rows] of byMonth(entries.slice(0, shown))) {
      const group = el("section", "profile-month");
      group.append(el("h4", "profile-month__title", month));
      const ol = el("ol", "profile-sent-group");
      for (const row of rows) ol.append(deliveryItem(row));
      group.append(ol);
      list.append(group);
    }
    more.hidden = shown >= entries.length;
  };
  more.addEventListener("click", draw);
  draw();
  parent.append(list, more);
}

async function renderMyAlerts(body) {
  const tiles = el("div", "admin-tiles profile-glance");
  body.append(tiles);
  const now = el("div");
  body.append(now);
  const columns = el("div", "profile-columns profile-columns--alerts");
  const left = el("div", "profile-column");
  const right = el("div", "profile-column");
  columns.append(left, right);
  body.append(columns);

  const subsBox = card(left, "What you are subscribed to", "The places you get flood alerts for, and how they reach you.");
  const subsHolder = loading(subsBox);
  const sentBox = card(right, "Sent to you", "Every flood alert that went to you, newest first. Open one to read it in full.");
  const sentHolder = loading(sentBox);

  const [subs, alerts] = await Promise.all([
    fetchMySubscriptions().catch((error) => error),
    fetchMyAlerts().catch((error) => error),
  ]);

  // The tiles: the four things someone checks first.
  const groups = alerts instanceof Error ? [] : groupDeliveries(alerts.deliveries);
  const reached = groups.filter((g) => g.kind !== "cancel" && g.channels.some((c) => c.outcome === "sent"));
  const failed = alerts instanceof Error ? 0 : alerts.deliveries.filter((d) => d.outcome === "failed").length;
  const places = subs instanceof Error ? [] : [...new Set(subs.filter((s) => s.confirmed).flatMap((s) => s.regions))];
  const inForce = alerts instanceof Error ? [] : alerts.in_force;
  tile(tiles, "Places watched", `${places.length}`, places.length ? places.slice(0, 2).join(", ") + (places.length > 2 ? "…" : "") : "None yet");
  tile(tiles, "Alerts received", `${reached.length}`, reached.length ? `Latest: ${ago(reached[0].at)}` : "None yet");
  tile(tiles, "In force now", `${inForce.length}`, inForce.length ? "In your places" : "All quiet in your places", {
    tone: inForce.length ? "alert" : "good",
  });
  tile(tiles, "Failed to send", `${failed}`, failed ? "Check your email and phone" : "Every message got through", {
    tone: failed ? "warn" : undefined,
  });

  // Alerts in force for your places, above everything else.
  if (inForce.length) {
    const box = el("section", "admin-callout admin-callout--danger profile-now");
    box.append(el("p", "admin-callout__title", inForce.length === 1 ? "A flood alert is in force for one of your places" : `${inForce.length} flood alerts are in force for your places`));
    const list = el("ul", "profile-now__list");
    for (const a of inForce) {
      const li = el("li");
      const link = el("a", null, a.headline);
      link.href = `#/alerts/${a.id}`;
      li.append(pill(a.level), link);
      list.append(li);
    }
    box.append(list);
    now.append(box);
  }

  // Your subscriptions.
  if (subs instanceof Error) {
    subsHolder.replaceChildren(el("p", "alerts-form__hint", `Could not load them: ${subs.message}`));
  } else {
    subsHolder.replaceChildren();
    const list = el("div", "profile-subs");
    // Alerts that reached each subscription, by any channel.
    const counts = new Map();
    for (const d of alerts instanceof Error ? [] : alerts.deliveries) {
      if (d.outcome !== "sent" || d.kind === "cancel") continue;
      counts.set(d.subscription, (counts.get(d.subscription) ?? new Set()).add(`${d.alert.id}:${d.level}`));
    }
    // Where you live first: it is the one that comes by itself.
    const ordered = [...subs].sort((a, b) => Number(b.is_home) - Number(a.is_home));
    for (const row of ordered) {
      list.append(subscriptionItem(row, counts.get(row.id)?.size ?? 0, () => {
        if (!list.children.length) list.replaceWith(noSubscriptions());
      }));
    }
    subsHolder.append(ordered.length ? list : noSubscriptions());
    const add = el("a", "auth__button auth__button--primary", "Get alerts for another place");
    add.href = "#/alerts/subscribe/";
    const browse = el("a", "auth__button", "See all alerts in force");
    browse.href = "#/alerts";
    const actions = el("div", "profile-actions");
    actions.append(add, browse);
    subsBox.append(actions);
  }

  // What has been sent.
  if (alerts instanceof Error) {
    sentHolder.replaceChildren(el("p", "alerts-form__hint", `Could not load them: ${alerts.message}`));
  } else {
    sentHolder.replaceChildren();
    sentList(sentHolder, groups);
    if (alerts.stopped.length) {
      const stopped = card(left, "Alerts you stopped", "Kept only as a count; the messages themselves are no longer linked to you.");
      const ul = el("ul", "profile-stopped");
      for (const s of alerts.stopped) {
        const li = el("li");
        li.append(
          el("strong", null, s.places.join(", ") || "Some places"),
          el(
            "span",
            "alerts-form__hint",
            `${s.how} · ${s.alerts_received} alert${s.alerts_received === 1 ? "" : "s"} received · stopped ${inline(ago(s.stopped_at))}`,
          ),
        );
        ul.append(li);
      }
      stopped.append(ul);
    }
  }
}

function noSubscriptions() {
  const empty = el("div", "profile-empty");
  empty.append(
    el("p", "profile-empty__title", "You are not subscribed to any alerts"),
    el(
      "p",
      "alerts-form__hint",
      "Turn on warnings for where you live on the Overview, or pick any place to get its alerts by email.",
    ),
  );
  return empty;
}

/* ------------------------------------------------------------- overview */

/** The four tiles and the set-up checklist at the top of the Overview. */
async function renderGlance(body, me, ways) {
  const tiles = el("div", "admin-tiles profile-glance");
  const setup = el("div");
  body.append(tiles, setup);
  const [home, subs, alerts, methods] = await Promise.all([
    fetchHome().catch(() => null),
    fetchMySubscriptions().catch(() => null),
    fetchMyAlerts().catch(() => null),
    ways,
  ]);

  const homeOn = home?.region && home.alerts_on;
  tile(
    tiles,
    "Warnings where you live",
    !home?.region ? "Not set" : homeOn ? (home.receiving ? "On" : "Almost") : "Off",
    home?.region ? `${home.region_name}, ${home.county_name}` : "Tell us where you live below",
    { tone: homeOn && home.receiving ? "good" : "warn", href: "#/profile" },
  ).addEventListener("click", (event) => {
    event.preventDefault();
    document.getElementById("profile-home")?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
  const places = subs ? new Set(subs.filter((s) => s.confirmed).flatMap((s) => s.regions)).size : null;
  tile(tiles, "Places watched", places ?? "—", subs ? `${subs.length} subscription${subs.length === 1 ? "" : "s"}` : "Could not load", {
    href: "#/profile/alerts",
  });
  const groups = alerts ? groupDeliveries(alerts.deliveries).filter((g) => g.kind !== "cancel") : [];
  tile(tiles, "Alerts received", alerts ? `${groups.length}` : "—", groups.length ? `Latest: ${ago(groups[0].at)}` : "None yet", {
    href: "#/profile/alerts",
  });
  const inForce = alerts?.in_force.length ?? 0;
  tile(tiles, "In force now", alerts ? `${inForce}` : "—", inForce ? "In your places: open them" : "All quiet in your places", {
    href: "#/profile/alerts",
    tone: inForce ? "alert" : "good",
  });

  const checks = accountChecks(me, home, methods);
  const done = checks.filter((c) => c.done).length;
  if (done === checks.length) return;
  const box = el("section", "admin-card profile-setup");
  const head = el("header", "profile-setup__head");
  const text = el("div");
  text.append(
    el("h3", "admin-card__title", "Finish setting up your account"),
    el("p", "admin-card__subtitle", "Each step makes sure a flood warning can reach you when it matters."),
  );
  const meter = el("div", "profile-meter");
  meter.setAttribute("role", "img");
  meter.setAttribute("aria-label", `${done} of ${checks.length} done`);
  const bar = el("span", "profile-meter__bar");
  bar.style.width = `${(done / checks.length) * 100}%`;
  meter.append(bar);
  head.append(text, el("span", "profile-setup__count", `${done} of ${checks.length} done`));
  const where = { email: "profile-details", phone: "profile-details", home: "profile-home", ways: null };
  const list = el("ul", "profile-checks");
  for (const check of checks) {
    const li = el("li", `profile-check${check.done ? " profile-check--done" : ""}`);
    li.append(el("span", "profile-check__mark", check.done ? "✓" : ""), el("span", null, check.label));
    if (!check.done) {
      const go = el("a", "link-button profile-check__go", "Do it");
      go.href = where[check.id] ? "#/profile" : "#/profile/security";
      if (where[check.id]) {
        go.addEventListener("click", (event) => {
          event.preventDefault();
          document.getElementById(where[check.id])?.scrollIntoView({ behavior: "smooth", block: "start" });
        });
      }
      li.append(go);
    }
    list.append(li);
  }
  box.append(head, meter, list);
  setup.append(box);
}

/** A short "your alerts" card on the Overview, pointing to the full tab. */
async function renderAlertsPreview(parent) {
  const box = card(parent, "Your latest flood alerts", "The last few alerts sent to you.");
  const holder = loading(box);
  const all = el("a", "auth__button", "See all your alerts and subscriptions");
  all.href = "#/profile/alerts";
  box.append(all);
  let alerts;
  try {
    alerts = await fetchMyAlerts();
  } catch (error) {
    holder.replaceChildren(el("p", "alerts-form__hint", `Could not load them: ${error.message}`));
    return;
  }
  const latest = groupDeliveries(alerts.deliveries).slice(0, 3);
  if (!latest.length) {
    holder.replaceChildren(el("p", "alerts-form__hint", "None sent to you yet. No news is good news."));
    return;
  }
  const ol = el("ol", "profile-sent-group profile-sent-group--compact");
  for (const row of latest) ol.append(deliveryItem(row));
  holder.replaceChildren(ol);
}

function activityList(rows, { withUser = false } = {}) {
  const list = el("ol", "profile-activity");
  if (rows.length === 0) list.append(el("li", "alerts-form__hint", "Nothing yet."));
  for (const row of rows) {
    const item = el("li");
    item.append(
      el("span", "profile-activity__what", `${withUser ? `${row.username ?? "Someone"}: ` : ""}${activityText(row)}`),
      el("time", "profile-activity__when", ago(row.at)),
    );
    item.lastChild.dateTime = row.at;
    item.lastChild.title = new Date(row.at).toLocaleString();
    list.append(item);
  }
  return list;
}

async function renderMyActivity(parent) {
  const box = card(parent, "Recent activity", "Sign-ins and changes made with this account.");
  const holder = el("div");
  holder.append(el("p", "alerts-form__hint", "Loading…"));
  box.append(holder);
  try {
    holder.replaceChildren(activityList(await fetchMyActivity()));
  } catch (error) {
    holder.replaceChildren(el("p", "alerts-form__hint", `Could not load it: ${error.message}`));
  }
}

/* --------------------------------------------------------- administrator */

async function renderUsers(body, me) {
  const box = card(body, "Users", "Every account. Change a role or switch an account off; you cannot do either to your own.");
  const holder = el("div", "profile-table-wrap");
  holder.append(el("p", "alerts-form__hint", "Loading…"));
  box.append(holder);
  let users;
  try {
    users = await fetchUsers();
  } catch (error) {
    holder.replaceChildren(el("p", "alerts-form__hint", `Could not load the users: ${error.message}`));
    return;
  }
  const table = el("table", "table profile-table");
  const head = el("tr");
  const headings = ["User", "Email", "Role", "Account", "Subscriptions", "Alerts received", "Stopped", "Last signed in", "Joined"];
  for (const h of headings) {
    const th = el("th", null, h);
    th.scope = "col";
    head.append(th);
  }
  const thead = el("thead");
  thead.append(head);
  const tbody = el("tbody");
  for (const user of users) {
    const row = el("tr");
    const who = el("td");
    const name = el("div", "profile-who");
    name.append(el("span", "profile-avatar profile-avatar--small", initials(user)), el("span", null, displayName(user)));
    who.append(name);

    const self = user.id === me.id;
    const role = el("select", "alerts-form__input profile-table__select");
    for (const [value, label] of Object.entries(ROLE_LABELS)) role.append(new Option(label, value, false, value === user.role));
    role.disabled = self || user.is_superuser;
    role.title = user.is_superuser ? "A superuser is always an administrator" : self ? "You cannot change your own role" : "";
    const status = el("span", "profile-table__note");
    role.addEventListener("change", async () => {
      try {
        await updateUser(user.id, { role: role.value });
        status.textContent = "Saved";
      } catch (error) {
        status.textContent = error.message;
      }
    });
    const roleCell = el("td");
    if (user.is_superuser) {
      // Its role field says nothing: a superuser is an administrator anyway.
      roleCell.append(el("span", null, "Superuser (always an administrator)"));
    } else {
      roleCell.append(role, status);
    }

    const active = el("button", "auth__button", user.is_active ? "On" : "Off");
    active.type = "button";
    active.setAttribute("aria-pressed", String(user.is_active));
    active.disabled = self;
    active.title = self ? "You cannot switch off your own account" : `Switch ${user.is_active ? "off" : "on"}`;
    active.addEventListener("click", async () => {
      try {
        const saved = await updateUser(user.id, { is_active: !user.is_active });
        user.is_active = saved.is_active;
        active.textContent = user.is_active ? "On" : "Off";
        active.setAttribute("aria-pressed", String(user.is_active));
      } catch (error) {
        active.textContent = error.message;
      }
    });
    const activeCell = el("td");
    activeCell.append(active);

    row.append(
      who,
      el("td", null, user.email || "—"),
      roleCell,
      activeCell,
      el("td", "table__num", `${user.subscriptions}`),
      el("td", "table__num", `${user.alerts_received ?? 0}`),
      el("td", "table__num", user.unsubscribed ? `${user.unsubscribed}×` : "—"),
      el("td", null, user.last_login ? ago(user.last_login) : "Never"),
      el("td", null, new Date(user.date_joined).toLocaleDateString()),
    );
    // On a phone each row is a card, and each cell says what it is (profile.css).
    [...row.children].forEach((cell, i) => {
      cell.dataset.label = headings[i];
    });
    tbody.append(row);
  }
  table.append(thead, tbody);
  holder.replaceChildren(table);
}

/** A table whose cells say what they are, so it becomes cards on a phone (profile.css). */
function labelledTable(headings, rows) {
  const holder = el("div", "profile-table-wrap");
  const table = el("table", "table profile-table");
  const head = el("tr");
  for (const h of headings) {
    const th = el("th", null, h);
    th.scope = "col";
    head.append(th);
  }
  const thead = el("thead");
  thead.append(head);
  const tbody = el("tbody");
  for (const cells of rows) {
    const row = el("tr");
    cells.forEach((cell, i) => {
      const td = el("td");
      td.dataset.label = headings[i];
      if (cell instanceof Node) td.append(cell);
      else td.textContent = cell;
      row.append(td);
    });
    tbody.append(row);
  }
  table.append(thead, tbody);
  holder.append(table);
  return holder;
}

const LEVEL_SHORT = { advisory: "Advisories and up", watch: "Watches and warnings", warning: "Warnings only" };

function placesText(places) {
  if (places.length <= 2) return places.join(", ") || "—";
  return `${places.slice(0, 2).join(", ")} and ${places.length - 2} more`;
}

/**
 * "Stop alerts" for one subscriber, for when they asked an administrator.
 * The first press asks again on the button itself (no browser dialog); the
 * second stops them, the same as their own unsubscribe link.
 */
function stopButton(subscriber, onStopped) {
  const wrap = el("div", "profile-stop");
  const button = el("button", "auth__button", "Stop alerts");
  button.type = "button";
  const message = el("span", "profile-table__note");
  let armed = false;
  let timer;
  button.addEventListener("click", async () => {
    if (!armed) {
      armed = true;
      button.textContent = "Press again to stop";
      message.textContent = "They will get no more alerts.";
      timer = setTimeout(() => {
        armed = false;
        button.textContent = "Stop alerts";
        message.textContent = "";
      }, 5000);
      return;
    }
    clearTimeout(timer);
    button.disabled = true;
    try {
      await stopSubscriber(subscriber.id);
      onStopped();
    } catch (error) {
      message.textContent = error.message;
      button.disabled = false;
      armed = false;
      button.textContent = "Stop alerts";
    }
  });
  wrap.append(button, message);
  return wrap;
}

async function renderSubscribers(body) {
  const summary = card(body, "Alerts sent", "Everyone signed up for flood alerts, what reached them, and who stopped.");
  const holder = el("div");
  holder.append(el("p", "alerts-form__hint", "Loading…"));
  summary.append(holder);
  let data;
  try {
    data = await fetchSubscribers();
  } catch (error) {
    holder.replaceChildren(el("p", "alerts-form__hint", `Could not load them: ${error.message}`));
    return;
  }
  const t = data.totals;
  const tiles = el("div", "alerts-tiles profile-tiles");
  for (const [n, label] of [
    [t.subscribers, `subscribed (${t.with_account} with an account)`],
    [t.awaiting_code, "waiting to enter their code"],
    [t.alerts_sent, "alerts delivered"],
    [t.alerts_failed, "alerts that failed to send"],
    [t.unsubscribed, `stopped (${t.unsubscribed_30d} in the last 30 days)`],
  ]) {
    const tile = el("div", "alerts-tile");
    tile.append(el("span", "alerts-tile__number", `${n}`), el("span", null, label));
    tiles.append(tile);
  }
  holder.replaceChildren(tiles);

  const people = card(
    body,
    "Subscribers",
    "Newest first. A failed alert is one the email service refused. Use Stop alerts only when the person asked you to; it works like their own unsubscribe link.",
  );
  people.append(
    data.subscribers.length
      ? labelledTable(
          ["Contact", "Account", "Places", "Wants", "Delivered", "Failed", "Last alert", "Since", ""],
          data.subscribers.map((s) => [
            [s.email, s.phone].filter(Boolean).join(", ") + (s.confirmed ? "" : " (awaiting code)"),
            s.username ?? "No account",
            (s.is_home ? "Home: " : "") + placesText(s.places),
            LEVEL_SHORT[s.min_level] ?? s.min_level,
            `${s.alerts_sent}`,
            `${s.alerts_failed}`,
            s.last_alert_at ? ago(s.last_alert_at) : "None yet",
            new Date(s.subscribed_at).toLocaleDateString(),
            stopButton(s, () => {
              body.replaceChildren();
              renderSubscribers(body);
            }),
          ]),
        )
      : el("p", "alerts-form__hint", "Nobody has signed up for alerts yet."),
  );

  const gone = card(
    body,
    "Stopped their alerts",
    "Their subscription is deleted; only this is kept, with the contact mostly hidden, as the unsubscribe page tells them.",
  );
  gone.append(
    data.unsubscribed.length
      ? labelledTable(
          ["Who", "Places", "How", "Alerts received", "Subscribed", "Stopped"],
          data.unsubscribed.map((u) => [
            u.username ? `${u.username} (${u.contact})` : u.contact || "—",
            placesText(u.places),
            u.how,
            `${u.alerts_received}`,
            u.subscribed_at ? new Date(u.subscribed_at).toLocaleDateString() : "—",
            ago(u.stopped_at),
          ]),
        )
      : el("p", "alerts-form__hint", "Nobody has stopped their alerts since this was first recorded."),
  );
}

async function renderActivity(body) {
  const box = card(body, "Activity log", "Sign-ins, rescores, alerts issued and cancelled, and account changes, newest first.");
  const holder = el("div");
  holder.append(el("p", "alerts-form__hint", "Loading…"));
  box.append(holder);
  try {
    holder.replaceChildren(activityList(await fetchActivity(), { withUser: true }));
  } catch (error) {
    holder.replaceChildren(el("p", "alerts-form__hint", `Could not load it: ${error.message}`));
  }
}

/* ----------------------------------------------------------------- page */

/**
 * Draw the page into `root` for the hash slug `profile[/tab]`. `user` is who
 * is signed in, as auth.js holds them, or null. `onUserChange(user)` is
 * called after the details change, so the header can show the new name.
 */
export async function showProfile(root, { slug, user, onUserChange }) {
  lastRender = { root, slug, user };
  if (!user) {
    location.hash = `#/signin?next=${encodeURIComponent(`#/${slug}`)}`;
    return;
  }
  const tab = slug.split("/")[1] ?? "";
  const admin = Boolean(user.is_administrator);

  const page = el("div", "admin-page profile-page");
  const head = el("header", "admin-head profile-head");
  const avatar = el("span", "profile-avatar", initials(user));
  const titles = el("div", "profile-head__titles");
  const name = el("h2", "admin-head__title", displayName(user));
  const badges = el("div", "profile-head__badges");
  badges.append(el("span", "profile-badge profile-badge--role", ROLE_LABELS[user.role] ?? ""));
  const lede = el("p", "admin-head__lede");
  titles.append(el("p", "admin-head__eyebrow", "Your account"), name, badges, lede);
  const who = el("div", "profile-head__who");
  who.append(avatar, titles);
  const back = el("a", "auth__button auth__button--primary", "Back to the map");
  back.href = "#/";
  head.append(who, back);
  page.append(head);

  const visible = TABS.filter((t) => !t.admin || admin);
  if (visible.length > 1) {
    const nav = el("nav", "admin-tabs");
    nav.setAttribute("aria-label", "Account sections");
    for (const t of visible) {
      const link = el("a", "admin-tabs__link", t.label);
      link.href = t.id ? `#/profile/${t.id}` : "#/profile";
      if (t.id === tab) link.setAttribute("aria-current", "page");
      nav.append(link);
    }
    page.append(nav);
  }
  const body = el("div", "admin-body");
  page.append(body);
  root.replaceChildren(page);

  let me;
  try {
    me = await fetchMe();
  } catch (error) {
    body.append(el("p", "status--error", `Could not load your profile: ${error.message}`));
    return;
  }
  if (lastRender?.slug !== slug) return;
  if (me.organisation) badges.append(el("span", "profile-badge", me.organisation));
  if (me.home_region_name) badges.append(el("span", "profile-badge", `Lives in ${me.home_region_name}`));
  lede.textContent =
    `@${me.username} · member since ${new Date(me.date_joined).toLocaleDateString(undefined, { month: "long", year: "numeric" })}` +
    (me.last_login ? ` · last signed in ${inline(ago(me.last_login))}` : "");
  const again = () => showProfile(root, { slug, user, onUserChange });

  if (admin && tab === "users") {
    await renderUsers(body, me);
    return;
  }
  if (admin && tab === "activity") {
    await renderActivity(body);
    return;
  }
  if (admin && tab === "subscribers") {
    await renderSubscribers(body);
    return;
  }

  if (tab === "alerts") {
    await renderMyAlerts(body);
    return;
  }

  const columns = el("div", "profile-columns");
  const left = el("div", "profile-column");
  const right = el("div", "profile-column");
  columns.append(left, right);
  const methods = fetchSocialAccounts().catch(() => null);

  if (tab === "security") {
    body.append(columns);
    renderPassword(left, methods);
    renderSignInMethods(right, methods);
    renderMyActivity(right);
    return;
  }

  // The Overview: how things stand, what is left to do, then where the
  // person lives, which is what makes the warnings come by themselves.
  renderGlance(body, me, methods);
  const homeSlot = el("div", "profile-home-slot");
  homeSlot.id = "profile-home";
  body.append(homeSlot, columns);
  renderHomeCard(homeSlot, me, { onChange: again });
  // Other places the person wants warnings for, under the home.
  const placesSlot = el("div", "profile-places-slot");
  placesSlot.id = "profile-places";
  homeSlot.after(placesSlot);
  renderPlacesCard(placesSlot, me);
  renderDetails(
    left,
    me,
    (saved) => {
      name.textContent = displayName(saved);
      avatar.textContent = initials(saved);
      onUserChange?.({ ...user, ...saved });
    },
    again,
  );
  renderAlertsPreview(right);
}

export function hideProfile() {
  lastRender = null;
}
