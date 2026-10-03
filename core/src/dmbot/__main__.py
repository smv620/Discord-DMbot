"""Entry point: `python -m dmbot` (or the `dmbot` command after `pip install -e .`)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys

from dotenv import find_dotenv, load_dotenv

from dmbot.config import ConfigError, load_settings


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    load_dotenv(find_dotenv(usecwd=True))
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Configuration problem: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    from dmbot.bot import run  # imported late so config errors show before discord loads

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run(settings))


if __name__ == "__main__":
    main()
