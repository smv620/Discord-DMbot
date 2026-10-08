"""Hash the admin password for .env (#772): python -m dmbot.web.hash_admin_password.

Reads the password from standard input (scripts/set-admin-password sends it, after
asking twice without echo) and prints the base64 form of its argon2id hash for
ADMIN_PASSWORD_HASH. Never prints or logs the password.
"""

import sys

from dmbot.web.admin import MIN_PASSWORD, hash_password


def main() -> int:
    password = sys.stdin.readline().rstrip("\n")
    if len(password) < MIN_PASSWORD:
        print(f"The password needs {MIN_PASSWORD} characters or more.", file=sys.stderr)
        return 2
    print(hash_password(password))
    return 0


if __name__ == "__main__":
    sys.exit(main())
