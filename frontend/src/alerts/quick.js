/**
 * "Get flood alerts" in one click for someone signed in.
 *
 * The alerts go to the email and phone on their profile (POST
 * alerts/subscribe/me/). If those have never been confirmed, a six-digit
 * code is asked for once, right here; after that every press is instant.
 * Signed out, the button is a link to the form on the alerts page.
 */

import { confirmAlerts, subscribeMe } from "../api.js";

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

/** "by email to k…@gmail.com and SMS to +2547…678" from a subscription summary. */
export function channelsText(summary) {
  const parts = [];
  if (summary.email) parts.push(`email to ${summary.email}`);
  if (summary.phone) parts.push(`SMS to ${summary.phone}`);
  return parts.length ? `by ${parts.join(" and ")}` : "";
}

/**
 * A control that gets alerts for `places` ([{ id, name }]).
 * `user` is who is signed in, or null.
 */
export function quickAlertControl(places, user, { label } = {}) {
  const wrap = el("div", "quick-alert");
  const names = places.map((p) => p.name).join(", ");
  const text = label ?? `Get flood alerts for ${names}`;

  if (!user) {
    const link = el("a", "auth__button quick-alert__button", text);
    link.href = places.length === 1 ? `#/alerts/subscribe/${places[0].id}` : "#/alerts";
    const hint = el("p", "quick-alert__hint");
    const signIn = el("a", null, "Sign in");
    signIn.href = `#/signin?next=${encodeURIComponent(location.hash || "#/")}`;
    hint.append(signIn, " to get them in one click, with the email and phone on your account.");
    wrap.append(link, hint);
    return wrap;
  }

  const button = el("button", "auth__button auth__button--primary quick-alert__button", text);
  button.type = "button";
  const note = el("p", "quick-alert__hint");
  note.setAttribute("role", "status");
  wrap.append(button, note);

  const done = (summary) => {
    const ok = el("p", "quick-alert__done");
    ok.append(
      el("strong", null, `Alerts on for ${names}`),
      ` ${channelsText(summary)}.`,
      summary.alerts_sent_now ? ` ${summary.alerts_sent_now} alert in force was sent now.` : "",
    );
    const manage = el("a", null, "Manage your alerts");
    manage.href = "#/profile";
    ok.append(" ", manage);
    wrap.replaceChildren(ok);
  };

  button.addEventListener("click", async () => {
    button.disabled = true;
    note.textContent = "";
    try {
      const result = await subscribeMe({ regions: places.map((p) => p.id) });
      if (result.confirmed) {
        done(result);
        return;
      }
      askForCode(result);
    } catch (error) {
      note.textContent = error.message;
      if (/profile/i.test(error.message)) {
        const link = el("a", null, " Open your profile");
        link.href = "#/profile";
        note.append(link);
      }
      button.disabled = false;
    }
  });

  function askForCode(result) {
    const by = result.code_sent_by.map((c) => (c === "email" ? "email" : "text message")).join(" and ");
    const form = el("form", "quick-alert__code");
    const input = el("input", "alerts-form__input alerts-form__input--code");
    input.inputMode = "numeric";
    input.autocomplete = "one-time-code";
    input.maxLength = 6;
    input.placeholder = "123456";
    input.setAttribute("aria-label", "Six-digit code");
    const submit = el("button", "auth__button auth__button--primary", "Confirm");
    submit.type = "submit";
    const message = el("p", "quick-alert__hint");
    message.setAttribute("role", "status");
    form.append(
      el(
        "p",
        "quick-alert__hint",
        `First time only: we sent a six-digit code by ${by} to check it reaches you. Enter it here.`,
      ),
      input,
      submit,
      message,
    );
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      submit.disabled = true;
      try {
        done(await confirmAlerts({ subscription: result.subscription, code: input.value.trim() }));
      } catch (error) {
        message.textContent = error.message;
        submit.disabled = false;
      }
    });
    wrap.replaceChildren(form);
    input.focus();
  }

  return wrap;
}
