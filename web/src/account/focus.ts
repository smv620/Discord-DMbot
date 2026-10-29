// Keeping focus where a keyboard or screen-reader user left it (#1466).

/** A button that was pressed is disabled while its request runs, and a browser drops the focus of a
 * disabled button to the page, so a keyboard or screen-reader user loses their place. When the
 * request is done, focus goes back to `back` (the same button, or a heading if the button is gone)
 * unless the person has already moved it somewhere else (#1466). */
export function keepFocus(back: () => HTMLElement | null | undefined): void {
  setTimeout(() => {
    const now = document.activeElement;
    if (now && now !== document.body) return;
    back()?.focus();
  }, 0);
}
