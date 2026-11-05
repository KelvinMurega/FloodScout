/**
 * "Flood warnings for where you live", on the profile page.
 *
 * The person says where they live, by searching an address (OpenStreetMap's
 * Nominatim, through the API), by their device's location, or by picking
 * their sub-county. From then on every alert for that sub-county or its
 * county reaches them by itself (accounts/home.py): no alerts page, no form.
 * The only other step is confirming their email once with a code.
 */

import {
  clearHome,
  fetchHome,
  fetchRegionNames,
  locateHome,
  saveHome,
  searchHome,
} from "../api.js";
import { codeBox } from "../auth/code.js";

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function button(label, className = "auth__button") {
  const node = el("button", className, label);
  node.type = "button";
  return node;
}

const LEVELS = [
  ["watch", "Watches and warnings (recommended)"],
  ["warning", "Warnings only"],
  ["advisory", "Advisories, watches and warnings"],
];

let subCounties = null;

function loadSubCounties() {
  subCounties ??= fetchRegionNames()
    .then((rows) => rows.filter((r) => r.level === "sub_county"))
    .catch((error) => {
      subCounties = null;
      throw error;
    });
  return subCounties;
}

/** The status line at the top of the card. */
function statusLine(home, me) {
  const line = el("div", "home-status");
  if (!home.region) {
    line.classList.add("home-status--none");
    line.append(el("strong", null, "Not set up yet. "), "Tell us where you live below.");
    return line;
  }
  const where = `${home.region_name}, ${home.county_name} County`;
  if (!home.alerts_on) {
    line.classList.add("home-status--off");
    line.append(el("strong", null, `Warnings are off for ${where}. `), "Tick the box below to turn them on.");
    return line;
  }
  if (home.receiving) {
    line.classList.add("home-status--on");
    const by = `email to ${home.by_email}`;
    line.append(
      el("strong", null, `Warnings on for ${where}. `),
      `When a flood is likely there, FloodScout tells you by ${by}, without you asking.`,
    );
    return line;
  }
  line.classList.add("home-status--waiting");
  line.append(
    el("strong", null, `Almost there: ${where} is saved. `),
    me.email ? "Confirm your email below to start the warnings." : "Add an email to your details below to start the warnings.",
  );
  return line;
}

/**
 * Draw the card into `parent`. `me` is the profile (auth/me); `onChange`
 * runs after the email is confirmed, so the rest of the page can update.
 */
export async function renderHomeCard(parent, me, { onChange } = {}) {
  const box = el("section", "admin-card home-card");
  const head = el("header", "admin-card__head");
  head.append(
    el("h3", "admin-card__title", "Flood warnings for where you live"),
    el(
      "p",
      "admin-card__subtitle",
      "Tell us where you live and FloodScout warns you by itself when a flood is likely there, " +
        "from today's risk or the 7-day forecast. Your location is kept to about 100 m and used for nothing else.",
    ),
  );
  const body = el("div", "home-card__body");
  body.append(el("p", "alerts-form__hint", "Loading…"));
  box.append(head, body);
  parent.append(box);

  let home;
  try {
    home = await fetchHome();
  } catch (error) {
    body.replaceChildren(el("p", "alerts-form__hint", `Could not load it: ${error.message}`));
    return;
  }
  draw(home);

  function draw(current) {
    home = current;
    body.replaceChildren(statusLine(home, me));

    // Confirming the email is the one other step.
    if (home.region && home.alerts_on && !home.receiving && me.email && !me.email_verified) {
      body.append(
        codeBox({
          email: me.email,
          sent: false,
          onConfirmed: (result) => {
            me.email_verified = true;
            draw(result.home);
            onChange?.(result);
          },
        }),
      );
    }
    body.append(form());
  }

  function form() {
    const wrap = el("form", "alerts-form home-form");
    wrap.noValidate = true;
    let chosen = {
      address: home.address,
      lat: home.lat,
      lon: home.lon,
      region: home.region,
    };

    // 1. Address search.
    const address = el("input", "alerts-form__input");
    address.type = "search";
    address.value = home.address ?? "";
    address.placeholder = "Estate, village, road or town, e.g. Ahero";
    address.autocomplete = "street-address";
    const find = button("Find");
    const searchRow = el("div", "home-form__row");
    searchRow.append(address, find);
    const results = el("div", "home-form__results");
    const addressField = el("label", "alerts-form__field");
    addressField.append(el("span", "alerts-form__label", "Where you live"), searchRow);

    // 2. Device location.
    const here = button("Use my current location", "link-button");

    // 3. The sub-county, detected or chosen.
    const region = el("select", "alerts-form__input");
    region.append(new Option("Choose your sub-county", ""));
    loadSubCounties()
      .then((rows) => {
        let group = null;
        let county = "";
        for (const row of rows) {
          if (row.county_name !== county) {
            county = row.county_name;
            group = el("optgroup");
            group.label = `${county} County`;
            region.append(group);
          }
          group.append(new Option(row.name, row.id, false, row.id === chosen.region));
        }
      })
      .catch(() => {});
    const regionField = el("label", "alerts-form__field");
    regionField.append(
      el("span", "alerts-form__label", "Your sub-county"),
      region,
      el("span", "alerts-form__hint", "Found from your address or location. Correct it if it is wrong."),
    );

    const pick = (place) => {
      chosen = { address: place.label ?? address.value, lat: place.lat, lon: place.lon, region: place.region };
      if (place.label) address.value = place.label;
      if (place.region) region.value = String(place.region);
      results.replaceChildren(
        el(
          "p",
          place.region ? "home-form__found" : "alerts-form__message alerts-form__message--error",
          place.region
            ? `That is in ${place.region_name}, ${place.county_name} County.`
            : "That place is outside the sub-counties FloodScout covers. Choose your sub-county from the list.",
        ),
      );
    };

    find.addEventListener("click", async () => {
      find.disabled = true;
      results.replaceChildren(el("p", "alerts-form__hint", "Searching…"));
      try {
        const found = await searchHome(address.value);
        results.replaceChildren();
        if (found.length === 0) {
          results.append(el("p", "alerts-form__hint", "Nothing found. Try the nearest town, or choose your sub-county below."));
        }
        for (const place of found) {
          const option = button("", "home-form__result");
          option.append(
            el("span", null, place.label),
            el("span", "alerts-form__hint", place.region ? `${place.region_name}, ${place.county_name} County` : "Outside the covered sub-counties"),
          );
          option.addEventListener("click", () => pick(place));
          results.append(option);
        }
      } catch (error) {
        results.replaceChildren(el("p", "alerts-form__message alerts-form__message--error", error.message));
      } finally {
        find.disabled = false;
      }
    });
    address.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        find.click();
      }
    });

    here.addEventListener("click", () => {
      if (!navigator.geolocation) {
        results.replaceChildren(el("p", "alerts-form__hint", "This browser cannot share its location."));
        return;
      }
      here.disabled = true;
      results.replaceChildren(el("p", "alerts-form__hint", "Finding where you are…"));
      navigator.geolocation.getCurrentPosition(
        async (position) => {
          const { latitude: lat, longitude: lon } = position.coords;
          try {
            const where = await locateHome(lat, lon);
            pick({ label: "", lat, lon, ...where });
          } catch (error) {
            results.replaceChildren(el("p", "alerts-form__message alerts-form__message--error", error.message));
          }
          here.disabled = false;
        },
        () => {
          results.replaceChildren(el("p", "alerts-form__hint", "Location was not shared. Search for your address instead."));
          here.disabled = false;
        },
        { enableHighAccuracy: false, timeout: 15000, maximumAge: 600000 },
      );
    });

    // 4. Which warnings, and whether at all.
    const level = el("select", "alerts-form__input");
    for (const [value, label] of LEVELS) level.append(new Option(label, value, false, value === home.alert_level));
    const levelField = el("label", "alerts-form__field");
    levelField.append(el("span", "alerts-form__label", "Warn me about"), level);
    const on = el("input");
    on.type = "checkbox";
    on.checked = home.alerts_on;
    const onField = el("label", "alerts-form__radio");
    onField.append(on, "Send me flood warnings for this area automatically");

    const save = el("button", "auth__button auth__button--primary", home.region ? "Save" : "Save and turn on warnings");
    save.type = "submit";
    const message = el("p", "alerts-form__message");
    message.setAttribute("role", "status");
    const actions = el("div", "home-form__actions");
    actions.append(save);
    if (home.region) {
      const remove = button("Remove where I live", "link-button");
      remove.addEventListener("click", async () => {
        try {
          draw(await clearHome());
        } catch (error) {
          message.textContent = error.message;
        }
      });
      actions.append(remove);
    }

    wrap.append(addressField, results, here, regionField, levelField, onField, actions, message);
    wrap.addEventListener("submit", async (event) => {
      event.preventDefault();
      const regionId = region.value ? Number(region.value) : null;
      if (!regionId && !(chosen.lat && chosen.lon)) {
        message.textContent = "Search for your address, use your location, or choose your sub-county.";
        message.classList.add("alerts-form__message--error");
        return;
      }
      save.disabled = true;
      message.classList.remove("alerts-form__message--error");
      message.textContent = "Saving…";
      const bodyOut = { alert_level: level.value, alerts_on: on.checked, address: address.value.trim() };
      // The chosen sub-county wins over the point when they disagree.
      if (chosen.lat && chosen.lon) Object.assign(bodyOut, { lat: chosen.lat, lon: chosen.lon });
      if (regionId) bodyOut.region = regionId;
      try {
        draw(await saveHome(bodyOut));
      } catch (error) {
        message.textContent = error.message;
        message.classList.add("alerts-form__message--error");
        save.disabled = false;
      }
    });
    return wrap;
  }
}
