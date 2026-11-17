"""The sample map step of a sample run (#4423): the owner's sample map, added to the kept sample
campaign as the DM's own map, read closely by the running bot, then scored with `map_key_check`.

The map is published art: it stays on the server (`--map PATH`, outside the repository) and goes
only into the sample campaign's own kept pictures, never into GitHub or a report. The DM's two yeses
are recorded as for any map the DM sends: the right to use it in this campaign (`add_map`'s
`right_to_use`, the DM's account) and, because the bot reads a map closely only for a campaign whose
DM has confirmed the right to use shared material (CLAUDE.md, IP), that confirmation too, with the
file's SHA-256 only, recorded after the map is kept (Supervisor's ruling asked on #4423). Both are
the owner's, in the owner's own test campaign (#4423).

It never fails the run: when pictures are off (no R2 settings), or the bot doesn't read the map in
time, it says so and skips the score.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

WAIT_S = 1500  # the bot's close reads run every 10 minutes (`bot.MAP_CLOSE_EVERY_S`): about 2.5
POLL_S = 20
TITLE = "Icewind Dale (sample)"


def skipped(say: Callable[[str], None], reason: str) -> None:
    """Say, and log, that the map step did not run and why (#4646): a step that silently doesn't
    run reads as "nothing found". The reason is a fixed phrase, never a path or a name."""
    say(f"map: skipped ({reason})")
    log.info("Sample map: skipped (%s)", reason)


async def run(
    db: Any,
    memory: Any,
    guild_id: int,
    campaign_id: str,
    dm: int,
    path: Path,
    folder: Path,
    *,
    say: Callable[[str], None],
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
    settings: Any = None,
) -> Mapping[str, int | str]:
    """Add, wait, back up and score; counts only. `settings`: the bot's own (read from the
    environment when None)."""
    from dmbot.campaigns.models import SHARED_STORY
    from dmbot.consent import ConsentStore
    from dmbot.devtools import accept_all, map_key_check
    from dmbot.devtools.sample_run import steps
    from dmbot.devtools.sample_run.plan import SampleError
    from dmbot.maps.store import CLOSE_READ_TRIES, MapError, MapStore
    from dmbot.pictures.bucket import Bucket
    from dmbot.pictures.keeper import Context, PictureKeeper
    from dmbot.pictures.store import PictureStore

    try:  # (the sample campaign only, before anything is written: never a real one)
        await accept_all.check_campaign(db, guild_id, campaign_id)
    except accept_all.WrongCampaign as exc:
        raise SampleError(str(exc)) from exc
    if settings is None:
        from dmbot.config import load_settings

        settings = load_settings()
    if not settings.pictures_ready:
        skipped(say, "kept pictures are off on this server")
        return {"skipped": 1}
    consent = ConsentStore(db)
    await consent.consenting(guild_id)  # (fills the cache `has_image_consent` reads)
    if not consent.has_image_consent(guild_id, dm):
        # (the bot reads a map only while its poster agrees to pictures being read)
        skipped(say, "the DM account hasn't agreed to its pictures being read")
        return {"skipped": 1}
    try:
        data = await asyncio.to_thread(path.read_bytes)
    except OSError as exc:  # (the kind only, never the path)
        skipped(say, f"the map file could not be read: {type(exc).__name__}")
        return {"skipped": 1}
    bucket = Bucket(
        settings.r2_account_id, settings.r2_bucket, settings.r2_access_key, settings.r2_secret_key
    )
    keeper = PictureKeeper(PictureStore(db), bucket, settings.picture_storage_mb)
    got = await keeper.keep(
        Context(
            guild_id=guild_id, campaign_id=campaign_id, poster_id=dm, session_id="",
            line_id=None, message_id=0, description=TITLE, consent_version=None,
            right_granted_at=None,
        ),
        data,
    )  # fmt: skip
    if got.picture is None:
        skipped(say, f"the picture was not kept: {got.why_not}")
        return {"skipped": 1}
    try:
        maps = MapStore(db, memory)
        made = await maps.add_map(
            guild_id, campaign_id, picture_id=got.picture.id, uploader_id=dm,
            uploader_is_dm=True, right_to_use=True, kind="region", came_from="website",
            title=TITLE, audience="dm", storage_mb=settings.picture_storage_mb,
        )  # fmt: skip
    except MapError as exc:  # (the store's own words)
        skipped(say, str(exc))
        return {"skipped": 1}
    # The owner's standing confirmation for the sample campaign (#4423): recorded only now that the
    # map is kept, as the file's own SHA-256 (it can be checked against the file on the server)
    await steps.campaign_store(db).record_confirmation(
        guild_id, campaign_id, dm, SHARED_STORY, hashlib.sha256(data).hexdigest()
    )
    began = clock()
    read, tries = False, 0
    while clock() - began < WAIT_S:
        read, tries = await _state(db, maps, guild_id, campaign_id, got.picture.id, made.value.id)
        if read or tries >= CLOSE_READ_TRIES:
            break
        await sleep(POLL_S)
    if read:  # (#4646: the success path says so too, never only the failures)
        say(f"map: read closely ({tries} tries, {int(clock() - began)} seconds)")
        log.info("Sample map: read closely after %d tries", tries)
    if not read:
        say(
            f"map: not read closely (the bot tried {tries} times)"
            if tries
            else "map: not read closely in time (is the bot running, and does the plan allow AI?)"
        )
    backup = await steps.backup(steps.campaign_store(db), guild_id, campaign_id, folder, "map")
    report = map_key_check.check(_decoded(backup), _key(map_key_check.DEFAULT_KEY))
    for line in map_key_check.render(report):
        say(f"map: {line}")
    return {
        "read closely": int(read),
        "tries": tries,
        "seconds": int(clock() - began),
        **{f"{part}: {status}": n for part, c in report.parts.items() for status, n in c.items()},
        "must-not hits": sum(report.must_not.values()),
    }


async def _state(
    db: Any, maps: Any, guild_id: int, campaign_id: str, picture_id: int, map_id: str
) -> tuple[bool, int]:
    """(read closely, the bot's tries so far) for the sample map; the tries through the map
    store, the one reader of the maps table (design rule 4)."""
    read = await _read_closely(db, guild_id, campaign_id, picture_id)
    return read, await maps.close_read_tries(guild_id, campaign_id, map_id)


async def _read_closely(db: Any, guild_id: int, campaign_id: str, picture_id: int) -> bool:
    async with db.guild(guild_id) as conn:
        cur = await conn.execute(
            "SELECT 1 FROM map_notes WHERE guild_id = %s AND campaign_id = %s"
            " AND picture_id = %s AND close_read IS NOT NULL",
            (guild_id, campaign_id, picture_id),
        )
        return await cur.fetchone() is not None


def _decoded(path: Path) -> dict[str, Any]:
    from dmbot.campaigns.store import decode_backup

    data = decode_backup(path.read_bytes())
    return data if isinstance(data, dict) else {}


def _key(path: Path) -> dict[str, Any]:
    import json

    key = json.loads(path.read_text(encoding="utf-8"))
    return key if isinstance(key, dict) else {}
