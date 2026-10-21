/**
 * The account control in the header: sign in, and once signed in a menu.
 *
 * The map needs no account. Signing in adds the profile page for everyone,
 * and for the System Administrator the analytics, the users, the alert
 * controls and the Rescore action from the Use Case Diagram. What the menu
 * offers is a convenience; the API checks the role on every request.
 */

import { rescore, signIn, signOut } from "./api.js";
import { ROLE_LABELS, displayName, initials } from "./profile/format.js";

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function button(label, onClick, className = "auth__button") {
  const node = el("button", className, label);
  node.type = "button";
  node.addEventListener("click", onClick);
  return node;
}

function signInMessage(error) {
  if (error.status === 400) return "Wrong username or password.";
  if (error.status === 429) return "Too many attempts. Try again in a minute.";
  return error.message;
}

/**
 * @param {HTMLElement} root  where the control is drawn
 * @param {{ onRescored: () => Promise<void>, onUserChange?: (user) => void }} hooks
 *        `onRescored` runs after a rescore, so the map can load the new
 *        scores; `onUserChange` whenever someone signs in or out.
 * @returns {{ setUser(user): void, refresh(user): void }} for a page that
 *          signs someone in itself (creating an account) or changes their name.
 */
export function initAuth(root, { onRescored, onUserChange = () => {} }) {
  let user = null;
  let closeMenu = () => {};

  function setUser(next) {
    user = next;
    if (user) renderUser();
    else renderSignedOut();
    onUserChange(user);
  }

  function renderSignedOut() {
    closeMenu();
    root.replaceChildren(button("Sign in", renderForm));
  }

  function renderForm() {
    const form = el("form", "auth__form");
    form.setAttribute("aria-label", "Sign in");

    const username = el("input", "auth__input");
    username.name = "username";
    username.autocomplete = "username";
    username.placeholder = "Username";
    username.setAttribute("aria-label", "Username");
    username.required = true;

    const password = el("input", "auth__input");
    password.type = "password";
    password.name = "password";
    password.autocomplete = "current-password";
    password.placeholder = "Password";
    password.setAttribute("aria-label", "Password");
    password.required = true;

    const submit = el("button", "auth__button auth__button--primary", "Sign in");
    submit.type = "submit";
    const create = el("a", "auth__link", "Create an account");
    create.href = "#/profile";
    create.addEventListener("click", renderSignedOut);
    const message = el("p", "auth__message");
    message.setAttribute("role", "alert");

    form.append(username, password, submit, button("Cancel", renderSignedOut), create, message);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      submit.disabled = true;
      message.textContent = "";
      try {
        setUser(await signIn(username.value, password.value));
      } catch (error) {
        password.value = "";
        message.textContent = signInMessage(error);
        submit.disabled = false;
      }
    });

    root.replaceChildren(form);
    username.focus();
  }

  function menuLink(label, href) {
    const link = el("a", "auth__menu-item", label);
    link.href = href;
    link.setAttribute("role", "menuitem");
    link.addEventListener("click", () => closeMenu());
    return link;
  }

  function renderUser(note = "") {
    const wrap = el("div", "auth__menu");
    const toggle = el("button", "auth__button auth__menu-button");
    toggle.type = "button";
    toggle.setAttribute("aria-haspopup", "menu");
    toggle.setAttribute("aria-expanded", "false");
    toggle.append(
      el("span", "profile-avatar profile-avatar--small", initials(user)),
      el("span", "auth__who", displayName(user)),
      el("span", null, "▾"),
    );

    const list = el("div", "auth__menu-list");
    list.setAttribute("role", "menu");
    list.hidden = true;
    list.append(
      el("p", "auth__menu-who", `${user.username} · ${ROLE_LABELS[user.role] ?? (user.is_administrator ? ROLE_LABELS.admin : ROLE_LABELS.user)}`),
      menuLink("My profile", "#/profile"),
      menuLink("Flood alerts", "#/alerts"),
    );

    const message = el("span", "auth__message", note);
    message.setAttribute("role", "status");

    if (user.is_administrator) {
      list.append(
        menuLink("Analytics", "#/admin"),
        menuLink("Users", "#/profile/users"),
        menuLink("Activity log", "#/profile/activity"),
      );
      const run = button(
        "Rescore now",
        async () => {
          closeMenu();
          run.disabled = true;
          message.textContent = "Scoring every region. This takes about a minute…";
          try {
            const result = await rescore();
            await onRescored();
            message.textContent = `Scored ${result.regions} regions for ${result.date}.`;
          } catch (error) {
            if (error.status === 401) {
              // The token was deleted on the server, e.g. signed out elsewhere.
              setUser(null);
              return;
            }
            message.textContent = `Rescore failed: ${error.message}`;
          } finally {
            run.disabled = false;
          }
        },
        "auth__menu-item",
      );
      run.title = "Score every region again from the rainfall already stored";
      run.setAttribute("role", "menuitem");
      list.append(run);
    }
    list.append(
      button(
        "Sign out",
        async () => {
          await signOut().catch(() => {});
          setUser(null);
        },
        "auth__menu-item",
      ),
    );

    const onDocument = (event) => {
      if (!wrap.contains(event.target)) closeMenu();
    };
    const onKey = (event) => {
      if (event.key === "Escape") {
        closeMenu();
        toggle.focus();
      }
    };
    closeMenu = () => {
      list.hidden = true;
      toggle.setAttribute("aria-expanded", "false");
      document.removeEventListener("click", onDocument);
      document.removeEventListener("keydown", onKey);
    };
    toggle.addEventListener("click", () => {
      if (!list.hidden) {
        closeMenu();
        return;
      }
      list.hidden = false;
      toggle.setAttribute("aria-expanded", "true");
      // Next tick, so this click does not close it straight away.
      setTimeout(() => {
        document.addEventListener("click", onDocument);
        document.addEventListener("keydown", onKey);
      });
      list.querySelector("a, button")?.focus();
    });

    wrap.append(toggle, list);
    root.replaceChildren(wrap, message);
  }

  renderSignedOut();
  return {
    setUser,
    /** Show changed details (a new name, say) without counting as a sign-in. */
    refresh(next) {
      user = { ...user, ...next };
      renderUser();
    },
  };
}
