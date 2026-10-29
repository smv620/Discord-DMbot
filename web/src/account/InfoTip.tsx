/** @jsxImportSource preact */
// A small (i) button and the short hint it opens (#2985). A press-to-open hint, not a hover one, so it
// works on a phone and with a keyboard. The two parts are separate so the button can sit beside a field
// while the hint opens under it; the page keeps the open/closed state and gives both the same `id`.

export function InfoButton({
  id,
  open,
  label,
  closeLabel,
  onToggle,
}: {
  id: string;
  open: boolean;
  label: string;
  closeLabel: string;
  onToggle: () => void;
}) {
  return (
    <button type="button" class="info-tip-button" aria-label={open ? closeLabel : label} aria-expanded={open} aria-controls={id} onClick={onToggle}>
      <span aria-hidden="true" class="info-tip-mark">
        i
      </span>
    </button>
  );
}

export function InfoText({ id, open, children }: { id: string; open: boolean; children: string }) {
  return (
    <span id={id} class="info-tip-text small muted" role="note" hidden={!open}>
      {children}
    </span>
  );
}
