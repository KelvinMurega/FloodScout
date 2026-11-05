/**
 * "My places", on the profile page: warnings for places besides home.
 *
 * A child's school, a farm, a parent's house. Each is a name and a spot,
 * found by an address search, the device's location, or a pin dropped on
 * the map (right-click, or a long press on a phone). Every alert for the
 * spot's sub-county or its county then reaches the person by email, as for
 * home (accounts/places.py).
 */

import { addPlace, fetchPlaces, locateHome, removePlace, searchHome, updatePlace } from "../api.js";
import { PLACE_LEVELS as LEVELS, placeStatus } from "./format.js";

const LIMIT = 5;

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

export async function renderPlacesCard(parent, me) {
  const box = el("section", "admin-card places-card");
  const head = el("header", "admin-card__head");
  head.append(
    el("h3", "admin-card__title", "My places"),
    el(
      "p",
      "admin-card__subtitle",
      `Warnings for up to ${LIMIT} more places: a school, a farm, family. ` +
        "You can also right-click the map (or press and hold on a phone) to pin a spot.",
    ),
  );
  const body = el("div", "places-card__body");
  body.append(el("p", "alerts-form__hint", "Loading…"));
  box.append(head, body);
  parent.append(box);

  let places;
  try {
    places = await fetchPlaces();
  } catch (error) {
    body.replaceChildren(el("p", "alerts-form__hint", `Could not load them: ${error.message}`));
    return;
  }
  draw();

  function draw() {
    body.replaceChildren();
    if (places.length === 0) body.append(el("p", "alerts-form__hint", "No places saved yet."));
    const list = el("ul", "places-list");
    for (const place of places) list.append(row(place));
    if (places.length) body.append(list);
    if (places.length < LIMIT) body.append(addForm());
  }

  function replace(updated) {
    places = places.map((p) => (p.id === updated.id ? updated : p));
    draw();
  }

  function row(place) {
    const item = el("li", "places-list__item");
    const title = el("p", "places-list__title");
    title.append(el("strong", null, place.label), ` · ${place.region_name}, ${place.county_name} County`);
    const status = el("p", "alerts-form__hint", placeStatus(place, me));
    const level = el("select", "alerts-form__input places-list__level");
    level.setAttribute("aria-label", `Which warnings for ${place.label}`);
    for (const [value, label] of LEVELS) level.append(new Option(label, value, false, value === place.alert_level));
    level.disabled = !place.alerts_on;
    level.addEventListener("change", async () => replace(await updatePlace(place.id, { alert_level: level.value })));
    const toggle = button(place.alerts_on ? "Turn off" : "Turn on", "link-button");
    toggle.addEventListener("click", async () => replace(await updatePlace(place.id, { alerts_on: !place.alerts_on })));
    const remove = button("Remove", "link-button");
    remove.addEventListener("click", async () => {
      await removePlace(place.id);
      places = places.filter((p) => p.id !== place.id);
      draw();
    });
    const controls = el("div", "places-list__controls");
    controls.append(level, toggle, remove);
    item.append(title, status, controls);
    return item;
  }

  function addForm() {
    const form = el("form", "alerts-form places-form");
    form.noValidate = true;
    let spot = null;

    const name = el("input", "alerts-form__input");
    name.maxLength = 40;
    name.placeholder = "Name, e.g. School or Farm";
    const nameField = el("label", "alerts-form__field");
    nameField.append(el("span", "alerts-form__label", "Add a place"), name);

    const address = el("input", "alerts-form__input");
    address.type = "search";
    address.placeholder = "Estate, village, road or town";
    const find = button("Find");
    const searchRow = el("div", "home-form__row");
    searchRow.append(address, find);
    const here = button("Use my current location", "link-button");
    const results = el("div", "home-form__results");
    const message = el("p", "alerts-form__message");
    message.setAttribute("role", "status");
    const save = el("button", "auth__button auth__button--primary", "Save and turn on warnings");
    save.type = "submit";

    const pick = (place) => {
      spot = place.region ? place : null;
      if (place.label) address.value = place.label;
      results.replaceChildren(
        el(
          "p",
          place.region ? "home-form__found" : "alerts-form__message alerts-form__message--error",
          place.region
            ? `That is in ${place.region_name}, ${place.county_name} County.`
            : "That spot is outside the sub-counties FloodScout covers.",
        ),
      );
    };

    find.addEventListener("click", async () => {
      find.disabled = true;
      results.replaceChildren(el("p", "alerts-form__hint", "Searching…"));
      try {
        const found = await searchHome(address.value);
        results.replaceChildren();
        if (found.length === 0) results.append(el("p", "alerts-form__hint", "Nothing found. Try the nearest town."));
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
      navigator.geolocation.getCurrentPosition(
        async (position) => {
          const { latitude: lat, longitude: lon } = position.coords;
          try {
            pick({ label: "", lat, lon, ...(await locateHome(lat, lon)) });
          } catch (error) {
            results.replaceChildren(el("p", "alerts-form__message alerts-form__message--error", error.message));
          }
          here.disabled = false;
        },
        () => {
          results.replaceChildren(el("p", "alerts-form__hint", "Location was not shared. Search for the address instead."));
          here.disabled = false;
        },
        { enableHighAccuracy: false, timeout: 15000, maximumAge: 600000 },
      );
    });

    form.append(nameField, searchRow, here, results, save, message);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      message.classList.add("alerts-form__message--error");
      if (!name.value.trim()) {
        message.textContent = "Give the place a name.";
        return;
      }
      if (!spot) {
        message.textContent = "Find the place by its address or use your location first.";
        return;
      }
      save.disabled = true;
      message.classList.remove("alerts-form__message--error");
      message.textContent = "Saving…";
      try {
        const saved = await addPlace({ label: name.value.trim(), address: address.value.trim(), lat: spot.lat, lon: spot.lon });
        places = [...places, saved];
        draw();
      } catch (error) {
        message.textContent = error.message;
        message.classList.add("alerts-form__message--error");
        save.disabled = false;
      }
    });
    return form;
  }
}
