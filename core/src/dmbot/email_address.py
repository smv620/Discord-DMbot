"""Cleaning an email address typed by a person (#2547).

Shared by the website and the bot's forms.
"""

MAX_EMAIL = 254  # the longest an address can be


def clean_email(raw: object) -> str | None:
    """The address in a form to look up, or None when it can't be one. Deliberately loose: the
    only real check is whether it matches an account."""
    if not isinstance(raw, str):
        return None
    email = raw.strip().lower()
    if not email or len(email) > MAX_EMAIL or email.count("@") != 1 or " " in email:
        return None
    name, domain = email.split("@")
    if not name or "." not in domain or domain.startswith(".") or domain.endswith("."):
        return None
    return email
