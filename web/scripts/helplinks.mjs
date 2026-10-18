// The "Copy link" buttons on the Help page (#1946). Everything runs in the reader's browser: the
// address is built from the page's own address and put on the clipboard, and nothing is sent.

export const copiedText = "Link copied";
export const couldNotCopy = "Couldn't copy. The link is now in the address bar.";

/** The full address of a section or step on this page. */
export function linkTo(loc, id) {
  return `${loc.origin}${loc.pathname}#${id}`;
}

/**
 * Show the buttons (they start hidden: without JavaScript the heading's own link works) and make
 * each copy its section's address. The result is spoken once through the polite status.
 */
export function wireCopy(root, status, env, wait = 2000) {
  for (const button of root.querySelectorAll("[data-copy-link]")) {
    button.hidden = false;
    const label = button.textContent;
    let timer;
    button.addEventListener("click", async () => {
      const id = button.getAttribute("data-copy-link");
      let said;
      try {
        await env.clipboard.writeText(linkTo(env.location, id));
        said = copiedText;
      } catch {
        env.location.hash = id; // no clipboard here: put it where the reader can copy it
        said = couldNotCopy;
      }
      status.textContent = said;
      button.textContent = said === copiedText ? copiedText : label;
      clearTimeout(timer);
      timer = setTimeout(() => {
        button.textContent = label;
      }, wait);
    });
  }
}
