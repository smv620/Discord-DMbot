"""Test setup shared by every test module."""

import asyncio
import sys

# psycopg's async mode needs a selector event loop; Windows defaults to another one.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined,unused-ignore]
