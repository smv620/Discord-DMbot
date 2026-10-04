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
    from dmbot.db import DatabaseError
    from dmbot.transcription.base import TranscriberUnavailable

    try:
        with contextlib.suppress(KeyboardInterrupt):
            # psycopg's async mode needs a selector event loop; Windows defaults to another.
            loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
            asyncio.run(run(settings), loop_factory=loop_factory)
    except TranscriberUnavailable as exc:
        print(f"Transcription problem: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    except DatabaseError as exc:
        print(f"Database problem: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
