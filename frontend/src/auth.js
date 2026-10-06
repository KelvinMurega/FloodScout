/**
 * The sign-in control in the header, and the administrator's Rescore button.
 *
 * The map needs no account. Signing in only adds the one action the Use Case
 * Diagram gives the System Administrator here: scoring every region again.
 * What the buttons show is a convenience; the API checks the role itself on
 * every request.
 */

import { rescore, signIn, signOut } from "./api.js";

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
 * @param {{ onRescored: () => Promise<void> }} hooks  called after a rescore,
 *        so the map can load the new scores
 */
export function initAuth(root, { onRescored }) {
  let user = null;

  function renderSignedOut() {
    root.replaceChildren(button("Administrator sign in", renderForm));
  }

  function renderForm() {
    const form = el("form", "auth__form");
    form.setAttribute("aria-label", "Administrator sign in");

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
    const message = el("p", "auth__message");
    message.setAttribute("role", "alert");

    form.append(username, password, submit, button("Cancel", renderSignedOut), message);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      submit.disabled = true;
      message.textContent = "";
      try {
        user = await signIn(username.value, password.value);
        renderSignedIn();
      } catch (error) {
        password.value = "";
        message.textContent = signInMessage(error);
        submit.disabled = false;
      }
    });

    root.replaceChildren(form);
    username.focus();
  }

  function renderSignedIn(note = "") {
    const who = el(
      "span",
      "auth__who",
      `${user.username} · ${user.is_administrator ? "System Administrator" : "End User"}`,
    );
    const message = el("span", "auth__message", note);
    message.setAttribute("role", "status");
    const nodes = [who];

    if (user.is_administrator) {
      const run = button(
        "Rescore now",
        async () => {
          run.disabled = true;
          message.textContent = "Scoring every region. This takes about a minute…";
          try {
            const result = await rescore();
            await onRescored();
            message.textContent = `Scored ${result.regions} regions for ${result.date}.`;
          } catch (error) {
            if (error.status === 401) {
              // The token was deleted on the server, e.g. signed out elsewhere.
              user = null;
              renderSignedOut();
              return;
            }
            message.textContent = `Rescore failed: ${error.message}`;
          } finally {
            run.disabled = false;
          }
        },
        "auth__button auth__button--primary",
      );
      run.title = "Score every region again from the rainfall already stored";
      nodes.push(run);
    }

    nodes.push(
      button("Sign out", async () => {
        await signOut().catch(() => {});
        user = null;
        renderSignedOut();
      }),
      message,
    );
    root.replaceChildren(...nodes);
  }

  renderSignedOut();
}
