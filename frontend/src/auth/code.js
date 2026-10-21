/**
 * The box for the six-digit code that confirms an email (or phone).
 *
 * Used straight after signing up or signing in with an email not yet
 * confirmed (auth/pages.js), and on the profile. Confirming it is what lets
 * flood warnings reach that email without anyone asking for them.
 */

import { confirmContactCode, sendContactCode } from "../api.js";

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

/**
 * @param {{ email: string, channel?: string, onConfirmed: (result) => void,
 *           onSkip?: () => void, sent?: boolean }} options
 *        `sent` false means no code has gone out yet, so the box offers to send one.
 */
export function codeBox({ email, channel = "email", onConfirmed, onSkip, sent = true }) {
  const box = el("form", "code-box");
  box.noValidate = true;
  const lede = el("p", "code-box__lede");
  const setLede = (wasSent) => {
    lede.replaceChildren(
      wasSent ? "We emailed a six-digit code to " : "Confirm ",
      el("strong", null, email),
      wasSent
        ? ". Enter it to confirm your email; flood warnings for where you live will then come to it."
        : " to get flood warnings by email. We will send it a six-digit code.",
    );
  };
  setLede(sent);

  const input = el("input", "auth-field__input code-box__input");
  input.inputMode = "numeric";
  input.autocomplete = "one-time-code";
  input.maxLength = 6;
  input.placeholder = "123456";
  input.setAttribute("aria-label", "Six-digit code");
  const confirm = el("button", "auth-submit", "Confirm");
  confirm.type = "submit";
  const resend = el("button", "link-button", sent ? "Send a new code" : "Send the code");
  resend.type = "button";
  const message = el("p", "auth-message auth-message--error");
  message.setAttribute("role", "alert");
  const row = el("div", "code-box__row");
  row.append(input, confirm);
  const links = el("div", "code-box__links");
  links.append(resend);
  if (onSkip) {
    const later = el("button", "link-button", "Do this later");
    later.type = "button";
    later.addEventListener("click", onSkip);
    links.append(later);
  }
  box.append(lede, message, row, links);

  resend.addEventListener("click", async () => {
    resend.disabled = true;
    message.textContent = "";
    try {
      await sendContactCode(channel);
      setLede(true);
      resend.textContent = "Sent. Send another";
      input.focus();
    } catch (error) {
      message.textContent = error.message;
    } finally {
      setTimeout(() => (resend.disabled = false), 60_000);
    }
  });

  box.addEventListener("submit", async (event) => {
    event.preventDefault();
    message.textContent = "";
    if (!/^\d{6}$/.test(input.value.trim())) {
      message.textContent = "Enter the six digits from the email.";
      return;
    }
    confirm.disabled = true;
    try {
      onConfirmed(await confirmContactCode(channel, input.value.trim()));
    } catch (error) {
      message.textContent = error.message;
      confirm.disabled = false;
      input.select();
    }
  });
  return box;
}
