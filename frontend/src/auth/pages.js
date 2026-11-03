/**
 * The sign-in and sign-up pages, shown in place of the map.
 *
 *   #/signin                    username or email and password, or a provider
 *   #/signup                    a new End User account, or a provider
 *   #/forgot                    email a password-reset link
 *   #/reset/<uid>/<token>       choose a new password from that link
 *   #/signin/complete/<ticket>  the return from Google, Microsoft or GitHub
 *
 * The provider buttons are the ones the server has set up
 * (accounts/social.py); with none, the pages say so. `?next=#/alerts` on
 * sign-in or sign-up returns there afterwards.
 */

import {
  completeSocialSignIn,
  confirmPasswordReset,
  fetchSocialProviders,
  register,
  requestPasswordReset,
  signIn,
  startSocialSignIn,
} from "../api.js";
import { codeBox } from "./code.js";
import { parseAuthSlug, passwordStrength } from "./format.js";
import "./auth.css";

let providers = null;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function svg(markup) {
  const wrap = document.createElement("span");
  wrap.className = "auth-provider__logo";
  wrap.setAttribute("aria-hidden", "true");
  wrap.innerHTML = markup;
  return wrap;
}

// The providers' own marks, as their brand guidelines ask sign-in buttons to show.
const LOGOS = {
  google:
    '<svg viewBox="0 0 48 48" width="18" height="18"><path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 13 4 4 13 4 24s9 20 20 20 20-9 20-20c0-1.3-.1-2.4-.4-3.5z"/><path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"/><path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z"/><path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z"/></svg>',
  microsoft:
    '<svg viewBox="0 0 21 21" width="18" height="18"><path fill="#f25022" d="M1 1h9v9H1z"/><path fill="#00a4ef" d="M1 11h9v9H1z"/><path fill="#7fba00" d="M11 1h9v9h-9z"/><path fill="#ffb900" d="M11 11h9v9h-9z"/></svg>',
  github:
    '<svg viewBox="0 0 16 16" width="18" height="18"><path fill="currentColor" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>',
};

function loadProviders() {
  providers ??= fetchSocialProviders().catch(() => {
    providers = null;
    return [];
  });
  return providers;
}

function field(label, input, extra) {
  const row = el("label", "auth-field");
  row.append(el("span", "auth-field__label", label), input);
  if (extra) row.append(extra);
  return row;
}

function input(type, attrs = {}) {
  const node = el("input", "auth-field__input");
  node.type = type;
  Object.assign(node, attrs);
  return node;
}

/** A password box with a Show/Hide toggle. */
function passwordInput(attrs) {
  const wrap = el("div", "auth-password");
  const box = input("password", attrs);
  const toggle = el("button", "auth-password__toggle", "Show");
  toggle.type = "button";
  toggle.setAttribute("aria-label", "Show password");
  toggle.addEventListener("click", () => {
    const hidden = box.type === "password";
    box.type = hidden ? "text" : "password";
    toggle.textContent = hidden ? "Hide" : "Show";
    toggle.setAttribute("aria-label", hidden ? "Hide password" : "Show password");
    box.focus();
  });
  wrap.append(box, toggle);
  return { wrap, box };
}

function strengthMeter(box) {
  const meter = el("div", "auth-strength");
  const bar = el("span", "auth-strength__bar");
  const text = el("span", "auth-strength__text");
  meter.append(bar, text);
  meter.setAttribute("aria-live", "polite");
  const update = () => {
    const { score, label, hint } = passwordStrength(box.value);
    meter.dataset.score = box.value ? String(score) : "";
    text.textContent = label ? `${label}${hint ? `. ${hint}` : ""}` : "";
  };
  box.addEventListener("input", update);
  update();
  return meter;
}

function message(kind = "error") {
  const node = el("p", `auth-message auth-message--${kind}`);
  node.setAttribute("role", kind === "error" ? "alert" : "status");
  return node;
}

function remember() {
  const label = el("label", "auth-check");
  const box = el("input");
  box.type = "checkbox";
  box.name = "remember";
  label.append(box, "Keep me signed in on this device");
  return { label, box };
}

/** The "Continue with ..." buttons, filled in once the server says which exist. */
function providerButtons(verb, { next, rememberBox, onError }) {
  const holder = el("div", "auth-providers");
  const or = el("p", "auth-or", "or");
  or.hidden = true;
  loadProviders().then((list) => {
    if (list.length === 0) {
      holder.append(
        el("p", "auth-providers__none", "Sign-in with Google, Microsoft or GitHub is not set up on this server yet."),
      );
      return;
    }
    or.hidden = false;
    for (const provider of list) {
      const button = el("button", `auth-provider auth-provider--${provider.key}`);
      button.type = "button";
      button.append(svg(LOGOS[provider.key] ?? ""), el("span", null, `${verb} with ${provider.name}`));
      button.addEventListener("click", async () => {
        for (const b of holder.querySelectorAll("button")) b.disabled = true;
        button.classList.add("auth-provider--busy");
        try {
          const url = await startSocialSignIn(provider.key, { next, remember: rememberBox?.checked });
          window.location.assign(url);
        } catch (error) {
          onError(error.message);
          for (const b of holder.querySelectorAll("button")) b.disabled = false;
          button.classList.remove("auth-provider--busy");
        }
      });
      holder.append(button);
    }
  });
  return [holder, or];
}

/** The page shell: a brand panel and a card for the form. */
function shell(root, { title, lede, body, footer }) {
  const page = el("div", "auth-page");
  const brand = el("aside", "auth-brand");
  const mark = el("a", "auth-brand__mark", "FloodScout");
  mark.href = "#/";
  brand.append(
    mark,
    el("p", "auth-brand__title", "Flood risk for every county and sub-county in Kenya"),
    (() => {
      const list = el("ul", "auth-brand__points");
      for (const point of [
        "Daily risk scores from a model trained on 26 years of floods",
        "A 7-day forecast, and why each place scores as it does",
        "Flood alerts by email for the places you choose",
      ]) {
        list.append(el("li", null, point));
      }
      return list;
    })(),
    el("p", "auth-brand__note", "A research project at Strathmore University. Official warnings come from the Kenya Meteorological Department."),
  );

  const main = el("main", "auth-main");
  const card = el("section", "auth-card");
  card.setAttribute("aria-labelledby", "auth-title");
  const heading = el("h1", "auth-card__title", title);
  heading.id = "auth-title";
  card.append(heading);
  if (lede) card.append(el("p", "auth-card__lede", lede));
  card.append(...body);
  main.append(card);
  if (footer) main.append(footer);
  const back = el("a", "auth-back", "← Back to the map");
  back.href = "#/";
  main.append(back);
  page.append(brand, main);
  root.replaceChildren(page);
  return card;
}

function footerLink(text, label, href) {
  const p = el("p", "auth-footer");
  const link = el("a", null, label);
  link.href = href;
  p.append(text, link);
  return p;
}

/**
 * After signing in or up: when the email is not confirmed and a code has
 * just been sent to it, ask for the code here before going on. The person
 * is already signed in; "Do this later" goes on without it.
 */
function afterSignIn(card, user, next, onSignedIn, { title, lede }) {
  if (!user.email || user.email_verified || !user.code_sent) {
    onSignedIn(user, next);
    return;
  }
  const heading = card.querySelector(".auth-card__title");
  heading.textContent = title;
  const intro = el("p", "auth-card__lede", lede);
  const box = codeBox({
    email: user.email,
    onConfirmed: () => onSignedIn({ ...user, email_verified: true }, next),
    onSkip: () => onSignedIn(user, next),
  });
  card.replaceChildren(heading, intro, box);
  box.querySelector("input").focus();
}

/* ---------------------------------------------------------------- pages */

function renderSignIn(root, { error, next }, { onSignedIn }) {
  const problem = message();
  if (error) problem.textContent = error;
  const keep = remember();
  const [buttons, or] = providerButtons("Continue", {
    next,
    rememberBox: keep.box,
    onError: (text) => (problem.textContent = text),
  });

  const form = el("form", "auth-form");
  form.noValidate = true;
  const name = input("text", { autocomplete: "username", required: true, name: "username" });
  const { wrap, box } = passwordInput({ autocomplete: "current-password", required: true, name: "password" });
  const forgot = el("a", "auth-field__aside", "Forgot your password?");
  forgot.href = "#/forgot";
  const submit = el("button", "auth-submit", "Sign in");
  submit.type = "submit";
  form.append(field("Username or email", name), field("Password", wrap, forgot), keep.label, submit);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    problem.textContent = "";
    if (!name.value.trim() || !box.value) {
      problem.textContent = "Enter your username or email, and your password.";
      return;
    }
    submit.disabled = true;
    submit.textContent = "Signing in…";
    try {
      const user = await signIn(name.value.trim(), box.value, keep.box.checked);
      afterSignIn(form.closest(".auth-card"), user, next || "#/", onSignedIn, {
        title: "Confirm your email",
        lede: "Your email is not confirmed yet, so flood warnings cannot be sent to it. One code, once.",
      });
    } catch (err) {
      box.value = "";
      problem.textContent =
        err.status === 400
          ? "That username or email and password do not match an account."
          : err.status === 429
            ? "Too many attempts. Wait a minute and try again."
            : err.message;
      submit.disabled = false;
      submit.textContent = "Sign in";
      box.focus();
    }
  });

  shell(root, {
    title: "Sign in",
    lede: "To manage your flood alerts and your profile. The map works without an account.",
    body: [problem, buttons, or, form],
    footer: footerLink("New to FloodScout? ", "Create an account", next ? `#/signup?next=${encodeURIComponent(next)}` : "#/signup"),
  });
  name.focus();
}

function renderSignUp(root, { next }, { onSignedIn }) {
  const problem = message();
  const keep = remember();
  const [buttons, or] = providerButtons("Sign up", {
    next,
    rememberBox: keep.box,
    onError: (text) => (problem.textContent = text),
  });

  const form = el("form", "auth-form");
  form.noValidate = true;
  const username = input("text", { autocomplete: "username", required: true, maxLength: 150 });
  const email = input("email", { autocomplete: "email" });
  const { wrap, box } = passwordInput({ autocomplete: "new-password", required: true });
  const again = input("password", { autocomplete: "new-password", required: true });
  const terms = el(
    "p",
    "auth-fine",
    "Your email is used only for alerts you ask for and to reset your password. You can delete your alerts at any time from your profile.",
  );
  const submit = el("button", "auth-submit", "Create account");
  submit.type = "submit";
  form.append(
    field("Username", username),
    field("Email", email, el("span", "auth-field__hint", "Optional, but needed to reset a forgotten password.")),
    field("Password", wrap, strengthMeter(box)),
    field("Password again", again),
    keep.label,
    terms,
    submit,
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    problem.textContent = "";
    if (!username.value.trim()) {
      problem.textContent = "Choose a username.";
      return;
    }
    if (box.value !== again.value) {
      problem.textContent = "The two passwords are not the same.";
      return;
    }
    if (passwordStrength(box.value).score === 0) {
      problem.textContent = passwordStrength(box.value).hint || "Choose a stronger password.";
      return;
    }
    submit.disabled = true;
    submit.textContent = "Creating your account…";
    try {
      const user = await register(
        { username: username.value.trim(), email: email.value.trim(), password: box.value },
        keep.box.checked,
      );
      afterSignIn(form.closest(".auth-card"), user, next || "#/profile", onSignedIn, {
        title: "Check your email",
        lede: "Your account is made. Confirm your email so flood warnings can reach it.",
      });
    } catch (err) {
      problem.textContent = err.message;
      submit.disabled = false;
      submit.textContent = "Create account";
    }
  });

  shell(root, {
    title: "Create your account",
    lede: "Free. Keep your flood alerts and details in one place.",
    body: [problem, buttons, or, form],
    footer: footerLink("Already have an account? ", "Sign in", next ? `#/signin?next=${encodeURIComponent(next)}` : "#/signin"),
  });
  username.focus();
}

function renderForgot(root) {
  const problem = message();
  const done = message("success");
  const form = el("form", "auth-form");
  form.noValidate = true;
  const email = input("email", { autocomplete: "email", required: true });
  const submit = el("button", "auth-submit", "Send the link");
  submit.type = "submit";
  form.append(field("Email", email), submit);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    problem.textContent = "";
    if (!email.value.includes("@")) {
      problem.textContent = "Enter the email address on your account.";
      return;
    }
    submit.disabled = true;
    try {
      const reply = await requestPasswordReset(email.value.trim());
      done.textContent = `${reply.detail} Check your inbox, and your spam folder.`;
      form.replaceWith(done);
    } catch (err) {
      problem.textContent = err.message;
      submit.disabled = false;
    }
  });
  shell(root, {
    title: "Forgot your password?",
    lede: "Enter the email on your account and we will send a link to choose a new one.",
    body: [problem, form],
    footer: footerLink("Remembered it? ", "Sign in", "#/signin"),
  });
  email.focus();
}

function renderReset(root, { uid, token }) {
  const problem = message();
  const done = message("success");
  const form = el("form", "auth-form");
  form.noValidate = true;
  const { wrap, box } = passwordInput({ autocomplete: "new-password", required: true });
  const again = input("password", { autocomplete: "new-password", required: true });
  const submit = el("button", "auth-submit", "Set the new password");
  submit.type = "submit";
  form.append(field("New password", wrap, strengthMeter(box)), field("New password again", again), submit);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    problem.textContent = "";
    if (box.value !== again.value) {
      problem.textContent = "The two passwords are not the same.";
      return;
    }
    submit.disabled = true;
    try {
      const reply = await confirmPasswordReset({ uid, token, new_password: box.value });
      done.textContent = reply.detail;
      const go = el("a", "auth-submit auth-submit--link", "Sign in");
      go.href = "#/signin";
      form.replaceWith(done, go);
    } catch (err) {
      problem.textContent = err.message;
      submit.disabled = false;
    }
  });
  shell(root, {
    title: "Choose a new password",
    lede: "Every device signed in to this account will be signed out.",
    body: [problem, form],
  });
  box.focus();
}

async function renderComplete(root, { ticket }, { onSignedIn }) {
  const status = message("success");
  status.textContent = "Finishing signing you in…";
  shell(root, { title: "Signing you in", body: [status] });
  try {
    const result = await completeSocialSignIn(ticket);
    const { next, created, linked, remember: _remember, ...user } = result;
    // Clean the ticket out of the address bar and the history.
    history.replaceState(null, "", `${location.pathname}${location.search}#/`);
    let landing = next || "#/";
    if (linked && !created) landing = next || "#/profile";
    if (created) landing = next || "#/profile";
    onSignedIn(user, landing);
  } catch (err) {
    location.hash = `#/signin?error=${encodeURIComponent(err.message)}`;
  }
}

/**
 * Draw the page for `slug` into `root`. `onSignedIn(user, next)` is called
 * once someone is signed in, with the hash to go to.
 */
export function showAuthPage(root, { slug, user, onSignedIn }) {
  const route = parseAuthSlug(slug);
  if (user && route.page !== "complete" && route.page !== "reset") {
    // Already signed in: nothing to do here.
    location.hash = route.next || "#/profile";
    return;
  }
  const hooks = { onSignedIn };
  if (route.page === "signup") renderSignUp(root, route, hooks);
  else if (route.page === "forgot") renderForgot(root);
  else if (route.page === "reset") renderReset(root, route);
  else if (route.page === "complete") renderComplete(root, route, hooks);
  else renderSignIn(root, route, hooks);
}
