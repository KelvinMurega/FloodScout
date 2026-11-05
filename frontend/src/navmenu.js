/**
 * The header's menu button on a narrow screen.
 *
 * Under NARROW the page actions (alerts, view, print, CSV) and the account
 * no longer fit beside the logo, so style.css folds them into a panel under
 * the header and shows a menu button instead. This opens and closes it:
 * the button toggles it, and it closes on Escape, on a tap outside it, on
 * moving to another page, after choosing an action, and when the screen
 * grows wide enough to show the actions again.
 *
 * Signed in, the account (its avatar and menu, auth.js) stays in the header
 * next to the menu button instead of going into the panel. Signed out, its
 * Sign in and Create account are too wide for that row and stay in the panel.
 */

// Matches the breakpoint in style.css ("The header's menu").
const NARROW = "(max-width: 720px)";

export function initNavMenu(header, toggle, panel, account) {
  const narrow = window.matchMedia(NARROW);

  function setOpen(open) {
    header.classList.toggle("app__header--open", open);
    toggle.setAttribute("aria-expanded", String(open));
    toggle.setAttribute("aria-label", open ? "Close the menu" : "Menu");
  }
  const close = () => setOpen(false);

  // The account beside the menu button when narrow and signed in, else last
  // in the panel. auth.js re-renders inside `account`, so moving it is safe.
  function placeAccount() {
    const inHeader = narrow.matches && account.querySelector(".auth__menu") !== null;
    if (inHeader && account.parentElement !== header) header.insertBefore(account, toggle);
    else if (!inHeader && account.parentElement !== panel) panel.append(account);
  }
  new MutationObserver(placeAccount).observe(account, { childList: true });
  placeAccount();

  toggle.addEventListener("click", () => setOpen(!header.classList.contains("app__header--open")));
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !header.classList.contains("app__header--open")) return;
    close();
    toggle.focus();
  });
  // Anywhere but the panel and its button closes it, the avatar included.
  document.addEventListener("click", (event) => {
    if (!panel.contains(event.target) && !toggle.contains(event.target)) close();
  });
  window.addEventListener("hashchange", close);
  narrow.addEventListener("change", () => {
    close();
    placeAccount();
  });

  // A page action closes the menu; the account button only opens its own
  // list inside the panel, so it leaves the menu open.
  panel.addEventListener("click", (event) => {
    const chosen = event.target.closest("a, button");
    if (chosen && !chosen.matches("[aria-haspopup]")) close();
  });
}
