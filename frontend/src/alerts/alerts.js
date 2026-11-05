/**
 * The flood alerts page (#/alerts), shown in place of the map.
 *
 * Open to everyone: the alerts in force, most severe first, each with what
 * triggered it and what to do; a form to get alerts by email for chosen
 * places; and the open CAP feed other systems can read.
 *
 * A System Administrator also sees the controls the API gives them: run the
 * rules now, issue or cancel an alert by hand, send a test message, and the
 * subscriber and delivery counts.
 *
 *   #/alerts                    the page
 *   #/alerts/<id>               the page, scrolled to one alert
 *   #/alerts/subscribe/<id>     the page, with the sign-up form set to a place
 */

import {
  ALERT_FEED_URL,
  alertCapUrl,
  cancelAlert,
  confirmAlerts,
  fetchAlertStats,
  fetchAlerts,
  fetchRegionNames,
  issueAlert,
  runAlertRules,
  sendTestAlert,
  subscribeToAlerts,
} from "../api.js";
import {
  LEVEL_LABELS,
  LEVEL_MEANING,
  LEVEL_PLURALS,
  alertPlace,
  LEVEL_ORDER,
  bannerText,
  countByLevel,
  filterAlerts,
  groupByLevel,
  placeOptions,
} from "./format.js";
import { channelsText, quickAlertControl } from "./quick.js";
import "./alerts.css";

let names = null;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function button(label, onClick, className = "auth__button") {
  const node = el("button", className, label);
  node.type = "button";
  node.addEventListener("click", onClick);
  return node;
}

function when(iso) {
  const date = new Date(iso);
  return Number.isNaN(date.valueOf())
    ? iso
    : date.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

function day(iso) {
  const date = new Date(`${iso}T00:00:00`);
  return date.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
}

function loadNames() {
  names ??= fetchRegionNames().catch((error) => {
    names = null;
    throw error;
  });
  return names;
}

function placeSelect(options, selected) {
  const select = el("select", "alerts-form__input");
  select.append(new Option("Choose a county or sub-county", ""));
  let group = null;
  for (const option of options) {
    if (option.label.endsWith(" County")) {
      group = el("optgroup");
      group.label = option.county;
      select.append(group);
    }
    group.append(new Option(option.label, option.id, false, option.id === selected));
  }
  return select;
}

/* ------------------------------------------------------------- sign-up */

function renderSignUp(card, { preselect }) {
  const form = el("form", "alerts-form");
  form.noValidate = true;
  const places = el("div", "alerts-form__places");
  const chosen = new Map();

  const placeRow = el("label", "alerts-form__field");
  placeRow.append(el("span", "alerts-form__label", "Places"));
  const pickerWrap = el("div", "alerts-form__picker");
  pickerWrap.append(el("span", "alerts-form__hint", "Loading places…"));
  placeRow.append(pickerWrap, places);

  const email = el("input", "alerts-form__input");
  email.type = "email";
  email.autocomplete = "email";
  email.placeholder = "you@example.com";
  const emailRow = el("label", "alerts-form__field");
  emailRow.append(el("span", "alerts-form__label", "Email"), email);

  const levels = el("fieldset", "alerts-form__levels");
  levels.append(el("legend", "alerts-form__label", "Tell me about"));
  const levelChoices = [
    ["advisory", "Advisories, watches and warnings"],
    ["watch", "Watches and warnings"],
    ["warning", "Warnings only"],
  ];
  for (const [value, label] of levelChoices) {
    const option = el("label", "alerts-form__radio");
    const input = el("input");
    input.type = "radio";
    input.name = "min_level";
    input.value = value;
    input.checked = value === "watch";
    option.append(input, label);
    levels.append(option);
  }

  const message = el("p", "alerts-form__message");
  message.setAttribute("role", "status");
  const submit = el("button", "auth__button auth__button--primary", "Send me a code");
  submit.type = "submit";

  form.append(placeRow, emailRow, levels, submit, message);
  card.append(form);

  function renderChosen() {
    places.replaceChildren();
    for (const [id, label] of chosen) {
      const chip = el("span", "alerts-chip", label);
      chip.append(
        button("×", () => {
          chosen.delete(id);
          renderChosen();
        }, "alerts-chip__remove"),
      );
      chip.lastChild.setAttribute("aria-label", `Remove ${label}`);
      places.append(chip);
    }
  }

  loadNames()
    .then((rows) => {
      const options = placeOptions(rows);
      const select = placeSelect(options, null);
      select.setAttribute("aria-label", "Add a place");
      select.addEventListener("change", () => {
        const id = Number(select.value);
        const option = options.find((o) => o.id === id);
        if (option && chosen.size < 10) chosen.set(id, option.label);
        select.value = "";
        renderChosen();
      });
      pickerWrap.replaceChildren(select);
      const start = options.find((o) => o.id === preselect);
      if (start) chosen.set(start.id, start.label);
      renderChosen();
    })
    .catch(() => {
      pickerWrap.replaceChildren(el("span", "alerts-form__hint", "Could not load the places."));
    });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    message.className = "alerts-form__message";
    const body = {
      regions: [...chosen.keys()],
      min_level: form.querySelector("input[name=min_level]:checked").value,
    };
    if (email.value.trim()) body.email = email.value.trim();
    if (body.regions.length === 0) {
      message.textContent = "Choose at least one place.";
      message.classList.add("alerts-form__message--error");
      return;
    }
    if (!body.email) {
      message.textContent = "Give an email address.";
      message.classList.add("alerts-form__message--error");
      return;
    }
    submit.disabled = true;
    message.textContent = "Sending your code…";
    try {
      const result = await subscribeToAlerts(body);
      renderConfirm(card, form, result, body);
    } catch (error) {
      message.textContent = error.message;
      message.classList.add("alerts-form__message--error");
      submit.disabled = false;
    }
  });
}

function renderConfirm(card, form, result, body) {
  const by = `email to ${body.email}`;
  const box = el("form", "alerts-form");
  const code = el("input", "alerts-form__input alerts-form__input--code");
  code.inputMode = "numeric";
  code.autocomplete = "one-time-code";
  code.maxLength = 6;
  code.placeholder = "123456";
  const row = el("label", "alerts-form__field");
  row.append(el("span", "alerts-form__label", "Code"), code);
  const message = el("p", "alerts-form__message");
  message.setAttribute("role", "status");
  const submit = el("button", "auth__button auth__button--primary", "Start my alerts");
  submit.type = "submit";
  box.append(
    el("p", "alerts-form__lede", `We sent a six-digit code by ${by}. Enter it here to start your alerts. It works for an hour.`),
    row,
    submit,
    message,
  );
  form.replaceWith(box);
  code.focus();

  box.addEventListener("submit", async (event) => {
    event.preventDefault();
    submit.disabled = true;
    try {
      const done = await confirmAlerts({ subscription: result.subscription, code: code.value.trim() });
      const success = el("div", "alerts-success");
      success.append(
        el("p", "alerts-success__title", "Your alerts are on."),
        el(
          "p",
          null,
          `You will hear about ${done.min_level === "warning" ? "warnings" : done.min_level === "watch" ? "watches and warnings" : "advisories, watches and warnings"} for ${done.regions.join(", ")}. ` +
            "Anything already in force for these places has been sent. Every message has a link to stop them.",
        ),
      );
      box.replaceWith(success);
    } catch (error) {
      message.textContent = error.message;
      message.classList.add("alerts-form__message--error");
      submit.disabled = false;
    }
  });
}

/**
 * Signed in: choose places, press once. The alerts go to the profile's email
 * (quick.js); a different address uses the full form.
 */
function renderSignedInSignUp(card, { preselect, user }) {
  const box = el("div", "alerts-form");
  const contacts = channelsText({ email: user.email });
  box.append(
    el(
      "p",
      "alerts-form__lede",
      contacts
        ? `Signed in as ${user.username}: alerts go ${contacts}, as on your profile.`
        : `Signed in as ${user.username}. Add an email to your profile to get alerts.`,
    ),
  );
  const chosen = new Map();
  const chips = el("div", "alerts-form__places");
  const pickerWrap = el("div", "alerts-form__picker");
  pickerWrap.append(el("span", "alerts-form__hint", "Loading places…"));
  const action = el("div");
  const redraw = () => {
    chips.replaceChildren();
    for (const [id, label] of chosen) {
      const chip = el("span", "alerts-chip", label);
      chip.append(
        button("×", () => {
          chosen.delete(id);
          redraw();
        }, "alerts-chip__remove"),
      );
      chips.append(chip);
    }
    action.replaceChildren(
      chosen.size
        ? quickAlertControl([...chosen].map(([id, name]) => ({ id, name })), user, {
            label: `Get alerts for ${chosen.size === 1 ? "this place" : `these ${chosen.size} places`}`,
          })
        : el("p", "alerts-form__hint", "Choose at least one place."),
    );
  };
  loadNames()
    .then((rows) => {
      const options = placeOptions(rows);
      const select = placeSelect(options, null);
      select.setAttribute("aria-label", "Add a place");
      select.addEventListener("change", () => {
        const option = options.find((o) => o.id === Number(select.value));
        if (option && chosen.size < 10) chosen.set(option.id, option.label);
        select.value = "";
        redraw();
      });
      pickerWrap.replaceChildren(select);
      const start = options.find((o) => o.id === preselect);
      if (start) chosen.set(start.id, start.label);
      redraw();
    })
    .catch(() => pickerWrap.replaceChildren(el("span", "alerts-form__hint", "Could not load the places.")));

  const other = el("button", "link-button", "Use a different email");
  other.type = "button";
  other.addEventListener("click", () => {
    box.remove();
    renderSignUp(card, { preselect });
  });
  const field = el("div", "alerts-form__field");
  field.append(el("span", "alerts-form__label", "Places"), pickerWrap, chips);
  box.append(field, action, other);
  card.append(box);
}

/* --------------------------------------------------------------- alerts */

function alertCard(alert, { admin, user, highlight, onShowOnMap, onChanged }) {
  const card = el("article", `alert-card alert-card--${alert.level}`);
  card.id = `alert-${alert.id}`;
  if (highlight) card.classList.add("alert-card--highlight");
  const head = el("header", "alert-card__head");
  head.append(
    el("span", `alert-pill alert-pill--${alert.level}`, LEVEL_LABELS[alert.level]),
    el("h4", "alert-card__title", alertPlace(alert)),
  );
  const meta = el(
    "p",
    "alert-card__meta",
    `${alert.basis === "forecast" ? `From the forecast, from ${day(alert.onset)}` : alert.basis === "manual" ? `Issued by ${alert.issued_by ?? "an administrator"}` : "From today's score"}` +
      ` · issued ${when(alert.issued_at)}${alert.version > 1 ? ` · raised ${alert.version - 1} time${alert.version > 2 ? "s" : ""}` : ""}` +
      ` · until ${when(alert.expires)}`,
  );
  const reasons = el("ul", "alert-card__reasons");
  for (const reason of alert.reasons) reasons.append(el("li", null, reason.text));
  const todo = el("p", "alert-card__instruction");
  todo.append(el("strong", null, "What to do: "), alert.instruction);

  const actions = el("div", "alert-card__actions");
  actions.append(button("Show on the map", () => onShowOnMap(alert)));
  const capLink = el("a", "auth__button", "CAP message");
  capLink.href = alertCapUrl(alert.id);
  capLink.target = "_blank";
  capLink.rel = "noopener";
  actions.append(capLink);
  actions.append(
    quickAlertControl([{ id: alert.region, name: alertPlace(alert) }], user, {
      label: "Get alerts for this place",
    }),
  );
  if (admin) {
    const cancel = button("Cancel this alert", async () => {
      cancel.disabled = true;
      try {
        await cancelAlert(alert.id);
        onChanged();
      } catch (error) {
        cancel.textContent = error.message;
      }
    }, "auth__button alert-card__cancel");
    actions.append(cancel);
  }
  card.append(head, meta, reasons, todo, actions);
  return card;
}

/* ---------------------------------------------------------------- admin */

function renderAdmin(parent, { onChanged }) {
  const card = el("section", "admin-card admin-card--wide alerts-admin");
  const head = el("header", "admin-card__head");
  head.append(
    el("h3", "admin-card__title", "Administrator"),
    el("p", "admin-card__subtitle", "Run the rules, issue or cancel an alert, and check that messages go out."),
  );
  card.append(head);
  const stats = el("div", "alerts-admin__stats", "Loading the counts…");
  card.append(stats);

  fetchAlertStats()
    .then((s) => {
      const items = [
        ["Subscribers", `${s.subscribers.confirmed} confirmed, ${s.subscribers.pending} waiting for a code`],
        ["By email", `${s.subscribers.with_email}`],
        [
          "Messages, last 7 days",
          s.deliveries_7d.length
            ? s.deliveries_7d.map((d) => `${d.count} ${d.channel} ${d.outcome}`).join(", ")
            : "None",
        ],
        [
          "Last run",
          s.last_run
            ? `${when(s.last_run.started_at)}: ${s.last_run.issued} issued, ${s.last_run.upgraded} raised, ${s.last_run.ended} ended, ${s.last_run.messages_sent} messages`
            : "Never",
        ],
      ];
      const list = el("dl", "detail__rows");
      for (const [term, value] of items) list.append(el("dt", null, term), el("dd", null, value));
      stats.replaceChildren(list);
    })
    .catch((error) => {
      stats.textContent = `Could not load the counts: ${error.message}`;
    });

  const tools = el("div", "alerts-admin__tools");

  // Run the rules now.
  const runBox = el("div", "alerts-admin__tool");
  const runNote = el("p", "alerts-form__message");
  const run = button("Run the alert rules now", async () => {
    run.disabled = true;
    runNote.textContent = "Running…";
    try {
      const r = await runAlertRules();
      runNote.textContent = `${r.issued} issued, ${r.upgraded} raised, ${r.ended} ended; ${r.messages_sent} messages sent.`;
      onChanged();
    } catch (error) {
      runNote.textContent = error.message;
    } finally {
      run.disabled = false;
    }
  }, "auth__button auth__button--primary");
  runBox.append(el("p", "alerts-form__label", "Rules"), run, runNote);

  // A test message.
  const testBox = el("form", "alerts-admin__tool");
  const testTo = el("input", "alerts-form__input");
  testTo.type = "email";
  testTo.placeholder = "Your email";
  testTo.setAttribute("aria-label", "Email for the test");
  const testNote = el("p", "alerts-form__message");
  const testSend = el("button", "auth__button", "Send a test email");
  testSend.type = "submit";
  testBox.append(el("p", "alerts-form__label", "Test the email"), testTo, testSend, testNote);
  testBox.addEventListener("submit", async (event) => {
    event.preventDefault();
    const body = { email: testTo.value.trim() };
    try {
      const result = await sendTestAlert(body);
      testNote.textContent = Object.entries(result)
        .map(([channel, r]) => `${channel}: ${r.outcome}${r.detail ? ` (${r.detail})` : ""}`)
        .join("; ");
    } catch (error) {
      testNote.textContent = error.message;
    }
  });

  // Issue by hand.
  const issueBox = el("form", "alerts-admin__tool alerts-admin__tool--wide");
  const levelSelect = el("select", "alerts-form__input");
  for (const level of LEVEL_ORDER) levelSelect.append(new Option(LEVEL_LABELS[level], level));
  levelSelect.setAttribute("aria-label", "Level");
  const what = el("textarea", "alerts-form__input");
  what.rows = 2;
  what.placeholder = "What is happening, e.g. The Nyando river has burst its banks at Ahero.";
  what.setAttribute("aria-label", "What is happening");
  const issueNote = el("p", "alerts-form__message");
  const issue = el("button", "auth__button auth__button--primary", "Issue alert");
  issue.type = "submit";
  const pickerWrap = el("span", null, "Loading places…");
  issueBox.append(el("p", "alerts-form__label", "Issue an alert by hand"), pickerWrap, levelSelect, what, issue, issueNote);
  let placePicker = null;
  loadNames()
    .then((rows) => {
      placePicker = placeSelect(placeOptions(rows), null);
      placePicker.setAttribute("aria-label", "Place");
      pickerWrap.replaceWith(placePicker);
    })
    .catch(() => {
      pickerWrap.textContent = "Could not load the places.";
    });
  issueBox.addEventListener("submit", async (event) => {
    event.preventDefault();
    issue.disabled = true;
    try {
      const alert = await issueAlert({
        region: Number(placePicker?.value),
        level: levelSelect.value,
        description: what.value,
      });
      issueNote.textContent = `Issued: ${alert.headline}.`;
      what.value = "";
      onChanged();
    } catch (error) {
      issueNote.textContent = error.message;
    } finally {
      issue.disabled = false;
    }
  });

  tools.append(runBox, testBox, issueBox);
  card.append(tools);
  parent.append(card);
}

/* ----------------------------------------------------------------- page */

let lastRender = null;

/**
 * Draw the alerts page into `root` for the hash slug `alerts[/...]`.
 * `onShowOnMap(alert)` leaves the page for the map, on the alert's place.
 */
export async function showAlerts(root, { slug, user, onShowOnMap }) {
  lastRender = { root, slug, user, onShowOnMap };
  const parts = slug.split("/");
  const preselect = parts[1] === "subscribe" ? Number(parts[2]) : null;
  const focusId = parts[1] && parts[1] !== "subscribe" ? Number(parts[1]) : null;
  const admin = Boolean(user?.is_administrator);

  const page = el("div", "admin-page alerts-page");
  const head = el("header", "admin-head");
  const titles = el("div");
  titles.append(
    el("p", "admin-head__eyebrow", "Early warning"),
    el("h2", "admin-head__title", "Flood alerts"),
    el(
      "p",
      "admin-head__lede",
      "Issued automatically every morning from the day's risk scores and the 7-day forecast, and sent by email " +
        "to everyone who asks for a place. FloodScout is a university research system: official warnings come from the " +
        "Kenya Meteorological Department and the National Disaster Operations Centre.",
    ),
  );
  const back = el("a", "auth__button auth__button--primary", "Back to the map");
  back.href = "#/";
  const actions = el("div", "admin-head__actions");
  actions.append(back);
  head.append(titles, actions);
  page.append(head);
  const body = el("div", "admin-body alerts-body");
  page.append(body);
  root.replaceChildren(page);

  if (admin) {
    renderAdmin(body, { onChanged: () => showAlerts(root, lastRender) });
  }

  const columns = el("div", "alerts-columns");
  const listColumn = el("div", "alerts-list");
  const side = el("div", "alerts-side");
  columns.append(listColumn, side);
  body.append(columns);

  const signUp = el("section", "admin-card alerts-signup");
  signUp.id = "alerts-signup";
  const signHead = el("header", "admin-card__head");
  signHead.append(
    el("h3", "admin-card__title", "Get alerts"),
    el("p", "admin-card__subtitle", "By email, for up to ten places. We send a code first so no one can sign up someone else."),
  );
  signUp.append(signHead);
  if (user) renderSignedInSignUp(signUp, { preselect, user });
  else renderSignUp(signUp, { preselect });
  side.append(signUp);

  const levels = el("section", "admin-card");
  levels.append(el("h3", "admin-card__title", "What the levels mean"));
  const meaning = el("dl", "alerts-levels");
  for (const level of LEVEL_ORDER) {
    meaning.append(
      el("dt", `alert-pill alert-pill--${level}`, LEVEL_LABELS[level]),
      el("dd", null, LEVEL_MEANING[level]),
    );
  }
  levels.append(
    meaning,
    el(
      "p",
      "relocation__note",
      "Warning: Very High and rising, or forecast Very High within 3 days with 50 mm or more of rain in a day " +
        "(the level the Kenya Meteorological Department treats as a flood warning sign). Watch: Very High today or " +
        "forecast within 7 days. Advisory: High and rising, or a 50 mm day in the forecast.",
    ),
  );
  side.append(levels);

  const feeds = el("section", "admin-card");
  feeds.append(
    el("h3", "admin-card__title", "Open feed"),
    el(
      "p",
      "admin-card__subtitle",
      "Every alert is published in the Common Alerting Protocol (CAP 1.2), the international standard warning " +
        "services exchange alerts in, so a county website, radio station or app can carry them.",
    ),
  );
  const feedLink = el("a", "auth__button", "Atom feed of CAP alerts");
  feedLink.href = ALERT_FEED_URL;
  feedLink.target = "_blank";
  feedLink.rel = "noopener";
  feeds.append(feedLink);
  side.append(feeds);

  listColumn.append(el("p", "admin-card__subtitle", "Loading the alerts in force…"));
  let alerts;
  try {
    alerts = await fetchAlerts();
  } catch (error) {
    listColumn.replaceChildren(el("p", "status--error", `Could not load the alerts: ${error.message}`));
    return;
  }
  if (lastRender?.slug !== slug) return;

  const counts = countByLevel(alerts);
  const tiles = el("div", "alerts-tiles");
  for (const level of LEVEL_ORDER) {
    const tile = el("div", `alerts-tile alerts-tile--${level}`);
    tile.append(el("span", "alerts-tile__number", `${counts[level]}`), el("span", null, LEVEL_PLURALS[level]));
    tiles.append(tile);
  }
  const search = el("input", "alerts-form__input alerts-search");
  search.type = "search";
  search.placeholder = "Find a place in the alerts";
  search.setAttribute("aria-label", "Find a place in the alerts");
  const groups = el("div", "alerts-groups");

  const draw = () => {
    groups.replaceChildren();
    const shown = filterAlerts(alerts, search.value);
    if (alerts.length === 0) {
      groups.append(el("p", "detail__empty", "No flood alerts are in force."));
      return;
    }
    if (shown.length === 0) {
      groups.append(el("p", "detail__empty", `No alert in force for “${search.value}”.`));
      return;
    }
    for (const [level, list] of groupByLevel(shown)) {
      const section = el("section", "alerts-group");
      section.append(el("h3", "alerts-group__title", `${LEVEL_PLURALS[level]} (${list.length})`));
      for (const alert of list) {
        section.append(
          alertCard(alert, {
            admin,
            user,
            highlight: alert.id === focusId,
            onShowOnMap,
            onChanged: () => showAlerts(root, lastRender),
          }),
        );
      }
      groups.append(section);
    }
  };
  search.addEventListener("input", draw);
  listColumn.replaceChildren(
    el("p", "alerts-summary", bannerText(counts) || "No flood alerts are in force."),
    tiles,
    search,
    groups,
  );
  draw();

  if (focusId) document.getElementById(`alert-${focusId}`)?.scrollIntoView({ block: "center" });
  if (preselect) document.getElementById("alerts-signup")?.scrollIntoView({ block: "start" });
}

export function hideAlerts() {
  lastRender = null;
}
