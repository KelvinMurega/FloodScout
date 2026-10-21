/**
 * The account page (#/profile), shown in place of the map.
 *
 *   #/profile            your details, password, alerts and recent activity
 *   #/profile/users      System Administrator: every account, role and on/off
 *   #/profile/activity   System Administrator: what everyone has done
 *
 * Signed out, it offers sign-in (the header button) and a form to create an
 * End User account, which is what lets someone keep their alerts in one
 * place. What it shows is what the API returns; the API checks every change.
 */

import {
  changePassword,
  fetchActivity,
  fetchMe,
  fetchMyActivity,
  fetchMySubscriptions,
  fetchRegionNames,
  fetchUsers,
  register,
  removeMySubscription,
  updateMe,
  updateUser,
} from "../api.js";
import { ROLE_LABELS, activityText, ago, displayName, initials } from "./format.js";
import "./profile.css";

const TABS = [
  { id: "", label: "My profile" },
  { id: "users", label: "Users", admin: true },
  { id: "activity", label: "Activity log", admin: true },
];

let lastRender = null;
let countyNames = null;

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

function loadCounties() {
  countyNames ??= fetchRegionNames()
    .then((rows) => rows.filter((r) => r.level === "county"))
    .catch((error) => {
      countyNames = null;
      throw error;
    });
  return countyNames;
}

/* ------------------------------------------------------------ signed out */

function renderSignedOut(root, { onSignedIn }) {
  const page = el("div", "admin-page profile-page");
  const head = el("header", "admin-head");
  const titles = el("div");
  titles.append(
    el("p", "admin-head__eyebrow", "Your account"),
    el("h2", "admin-head__title", "Profile"),
    el(
      "p",
      "admin-head__lede",
      "Sign in with the button at the top right, or create an account to keep your flood alerts and details in one place. " +
        "The map and the alerts work without one.",
    ),
  );
  const back = el("a", "auth__button auth__button--primary", "Back to the map");
  back.href = "#/";
  head.append(titles, back);
  page.append(head);

  const body = el("div", "admin-body profile-narrow");
  const box = card(body, "Create an account", "An End User account. A System Administrator can give it more.");
  const form = el("form", "alerts-form");
  const username = input("text", "", { autocomplete: "username", required: true });
  const email = input("email", "", { autocomplete: "email" });
  const password = input("password", "", { autocomplete: "new-password", required: true });
  const message = note();
  const submit = el("button", "auth__button auth__button--primary", "Create account");
  submit.type = "submit";
  form.append(
    field("Username", username),
    field("Email", email, "Optional. Used for alerts and nothing else."),
    field("Password", password, "At least 8 characters, not all numbers, not a common one."),
    submit,
    message,
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    submit.disabled = true;
    try {
      const user = await register({ username: username.value.trim(), email: email.value.trim(), password: password.value });
      onSignedIn(user);
    } catch (error) {
      password.value = "";
      say(message, error.message, true);
      submit.disabled = false;
    }
  });
  box.append(form);
  page.append(body);
  root.replaceChildren(page);
}

/* ------------------------------------------------------------ my profile */

function renderDetails(parent, me, onSaved) {
  const box = card(parent, "Your details", "Your email and phone are used for alerts you ask for, and are not shown to others.");
  const form = el("form", "alerts-form profile-grid");
  const first = input("text", me.first_name, { autocomplete: "given-name" });
  const last = input("text", me.last_name, { autocomplete: "family-name" });
  const email = input("email", me.email, { autocomplete: "email" });
  const phone = input("tel", me.phone, { autocomplete: "tel", placeholder: "0712 345 678" });
  const organisation = input("text", me.organisation, { autocomplete: "organization", placeholder: "e.g. Kisumu County Disaster Management" });
  const county = el("select", "alerts-form__input");
  county.append(new Option("Not set", ""));
  loadCounties()
    .then((rows) => {
      for (const row of rows) county.append(new Option(row.name, row.id, false, row.id === me.county));
    })
    .catch(() => {});
  const message = note();
  const save = el("button", "auth__button auth__button--primary", "Save details");
  save.type = "submit";
  form.append(
    field("First name", first),
    field("Last name", last),
    field("Email", email),
    field("Phone", phone),
    field("Organisation", organisation),
    field("Home county", county),
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
        county: county.value ? Number(county.value) : null,
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

function renderPassword(parent) {
  const box = card(parent, "Password", "Changing it signs you out everywhere else.");
  const form = el("form", "alerts-form");
  const current = input("password", "", { autocomplete: "current-password" });
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

async function renderSubscriptions(parent) {
  const box = card(parent, "Your flood alerts", "The places you get alerts for, by email or SMS.");
  const list = el("div", "profile-list");
  list.append(el("p", "alerts-form__hint", "Loading…"));
  const more = el("a", "auth__button", "Get alerts for another place");
  more.href = "#/alerts/subscribe/";
  box.append(list, more);
  let rows;
  try {
    rows = await fetchMySubscriptions();
  } catch (error) {
    list.replaceChildren(el("p", "alerts-form__hint", `Could not load them: ${error.message}`));
    return;
  }
  list.replaceChildren();
  if (rows.length === 0) {
    list.append(el("p", "alerts-form__hint", "None yet. Alerts you sign up for with this account's email appear here."));
    return;
  }
  const levels = { advisory: "Advisories and up", watch: "Watches and warnings", warning: "Warnings only" };
  for (const row of rows) {
    const item = el("div", "profile-list__item");
    const text = el("div");
    text.append(
      el("strong", null, row.regions.join(", ")),
      el(
        "span",
        "alerts-form__hint",
        `${levels[row.min_level]} · ${[row.email && "email", row.phone && "SMS"].filter(Boolean).join(" and ")}` +
          `${row.confirmed ? "" : " · waiting for the code"}`,
      ),
    );
    const stop = el("button", "auth__button", "Stop");
    stop.type = "button";
    stop.addEventListener("click", async () => {
      stop.disabled = true;
      try {
        await removeMySubscription(row.id);
        item.remove();
      } catch (error) {
        stop.textContent = error.message;
      }
    });
    item.append(text, stop);
    list.append(item);
  }
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
  for (const h of ["User", "Email", "Role", "Account", "Alerts", "Last signed in", "Joined"]) {
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
      el("td", null, user.last_login ? ago(user.last_login) : "Never"),
      el("td", null, new Date(user.date_joined).toLocaleDateString()),
    );
    tbody.append(row);
  }
  table.append(thead, tbody);
  holder.replaceChildren(table);
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
 * is signed in, as auth.js holds them, or null. `onSignedIn(user)` is called
 * after an account is created here; `onUserChange(user)` after the details
 * change, so the header can show the new name.
 */
export async function showProfile(root, { slug, user, onSignedIn, onUserChange }) {
  lastRender = { root, slug, user };
  if (!user) {
    renderSignedOut(root, { onSignedIn });
    return;
  }
  const tab = slug.split("/")[1] ?? "";
  const admin = Boolean(user.is_administrator);

  const page = el("div", "admin-page profile-page");
  const head = el("header", "admin-head profile-head");
  const avatar = el("span", "profile-avatar", initials(user));
  const titles = el("div");
  const name = el("h2", "admin-head__title", displayName(user));
  const lede = el("p", "admin-head__lede", ROLE_LABELS[user.role] ?? "");
  titles.append(el("p", "admin-head__eyebrow", "Your account"), name, lede);
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
  lede.textContent =
    `${ROLE_LABELS[me.role] ?? me.role}${me.organisation ? ` · ${me.organisation}` : ""}` +
    ` · member since ${new Date(me.date_joined).toLocaleDateString(undefined, { month: "long", year: "numeric" })}` +
    (me.last_login ? ` · last signed in ${ago(me.last_login)}` : "");

  if (admin && tab === "users") {
    await renderUsers(body, me);
    return;
  }
  if (admin && tab === "activity") {
    await renderActivity(body);
    return;
  }

  const columns = el("div", "profile-columns");
  const left = el("div", "profile-column");
  const right = el("div", "profile-column");
  columns.append(left, right);
  body.append(columns);
  renderDetails(left, me, (saved) => {
    name.textContent = displayName(saved);
    avatar.textContent = initials(saved);
    onUserChange?.({ ...user, ...saved });
  });
  renderPassword(left);
  renderSubscriptions(right);
  renderMyActivity(right);
}

export function hideProfile() {
  lastRender = null;
}
