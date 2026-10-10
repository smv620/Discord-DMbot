"""The few words of the consent question that other messages repeat (#1018). No imports, so
the help text and the consent messages can share them."""

MIN_AGE = 16  # DMbot's minimum age (owner, 2026-10-10)
# The button. Discord allows 80 characters.
CONSENT_LABEL = f"I'm {MIN_AGE} or older, record me"
AGE_LINE = f"You must be {MIN_AGE} or older to be recorded."
# For a younger player: nothing to press, no age asked, and they can still play.
UNDER_LINE = f"Younger than {MIN_AGE}? Press **No thanks**. You can still play."
