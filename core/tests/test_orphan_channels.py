"""The sweep of leftover campaign channels (#4479): only DMbot's own channels (name AND topic) that
no live campaign of the server points to, seen twice a day apart, at most five a server. Stand-in
channels; no Discord, no database."""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field

from dmbot.dm_screen import messages, orphan_channels
from dmbot.dm_screen.orphan_channels import GRACE_S, MAX_DELETES, Sightings, sweep
from dmbot.dm_screen.transcript_channel import KIND as TRANSCRIPT_KIND

EPOCH = orphan_channels.DISCORD_EPOCH_MS
DAY = GRACE_S
NOW = 1_800_000_000.0  # a clock reading; channel ids are made to be this old


def snowflake(age_s: float, *, serial: int = 0) -> int:
    """A channel id that was made `age_s` seconds before NOW."""
    made_ms = int((NOW - age_s) * 1000) - EPOCH
    return (made_ms << 22) | serial


@dataclass
class Chan:
    id: int
    name: str
    topic: str | None
    deleted: bool = False
    fail: bool = False
    reasons: list[str | None] = field(default_factory=list)

    async def delete(self, *, reason: str | None = None) -> None:
        if self.fail:
            raise RuntimeError("Discord said no")
        self.deleted = True
        self.reasons.append(reason)


def dm_bot(age: float = 3 * DAY, serial: int = 0, name: str = "Frostmaiden") -> Chan:
    return Chan(
        snowflake(age, serial=serial),
        f"dmb-dm-bot-{name.lower()}",
        messages.dm_bot_topic(name),
    )


def transcript(age: float = 3 * DAY, serial: int = 0, name: str = "Frostmaiden") -> Chan:
    return Chan(
        snowflake(age, serial=serial),
        f"dmb-{TRANSCRIPT_KIND}-{name.lower()}",
        messages.transcript_topic(name),
    )


def screen(age: float = 3 * DAY, serial: int = 0, name: str = "Frostmaiden") -> Chan:
    return Chan(
        snowflake(age, serial=serial),
        f"dmb-dm-screen-{name.lower()}",
        messages.topic(name, "private"),
    )


class TheKinds(unittest.TestCase):
    def test_each_of_dmbots_channels_is_known_by_name_and_topic(self) -> None:
        self.assertEqual(orphan_channels.kind_of(dm_bot().name, dm_bot().topic), "dm-bot")
        self.assertEqual(
            orphan_channels.kind_of(transcript().name, transcript().topic), "transcript"
        )
        self.assertEqual(orphan_channels.kind_of(screen().name, screen().topic), "dm-screen")
        for visibility in ("private", "peek", "open"):
            self.assertEqual(
                orphan_channels.kind_of("dmb-dm-screen-x", messages.topic("X", visibility)),
                "dm-screen",
            )
        for everyone in (True, False):
            self.assertEqual(
                orphan_channels.kind_of(
                    "dmb-transcript-x", messages.transcript_topic("X", everyone=everyone)
                ),
                "transcript",
            )

    def test_a_channel_with_dmbots_name_but_another_topic_is_never_one(self) -> None:
        self.assertIsNone(orphan_channels.kind_of("dmb-dm-bot-mine", "my own notes"))
        self.assertIsNone(orphan_channels.kind_of("dmb-dm-bot-mine", None))
        self.assertIsNone(orphan_channels.kind_of("dmb-transcript-mine", "Live transcript for X."))

    def test_a_channel_with_dmbots_topic_but_another_name_is_never_one(self) -> None:
        self.assertIsNone(orphan_channels.kind_of("general", messages.dm_bot_topic("X")))
        self.assertIsNone(orphan_channels.kind_of("our-notes", messages.transcript_topic("X")))
        self.assertIsNone(orphan_channels.kind_of("table", messages.topic("X", "open")))


class TheSweep(unittest.IsolatedAsyncioTestCase):
    async def look(
        self,
        channels: list[Chan],
        live: set[int],
        seen: Sightings,
        at: float,
        *,
        can: bool = True,
        guild_id: int = 1,
    ) -> orphan_channels.Result:
        return await sweep(guild_id, channels, live, seen, can_manage=can, now=at)

    async def test_an_orphan_goes_only_at_the_second_sweep_a_day_after_the_first(self) -> None:
        gone = dm_bot()
        seen = Sightings()
        first = await self.look([gone], set(), seen, NOW)
        self.assertEqual((first.seen, first.deleted, gone.deleted), (1, 0, False))
        early = await self.look([gone], set(), seen, NOW + DAY - 60)
        self.assertEqual((early.deleted, gone.deleted), (0, False))
        later = await self.look([gone], set(), seen, NOW + DAY)
        self.assertEqual((later.deleted, gone.deleted), (1, True))
        self.assertEqual(gone.reasons, ["DMbot: its campaign no longer exists"])

    async def test_a_live_campaigns_channels_stay(self) -> None:
        mine = [dm_bot(), transcript(serial=1), screen(serial=2)]
        seen = Sightings()
        live = {c.id for c in mine}
        await self.look(mine, live, seen, NOW)
        done = await self.look(mine, live, seen, NOW + 3 * DAY)
        self.assertEqual((done.seen, done.deleted), (0, 0))
        self.assertFalse(any(c.deleted for c in mine))

    async def test_a_channel_made_by_a_person_is_never_touched(self) -> None:
        theirs = [
            Chan(snowflake(9 * DAY), "dmb-dm-bot-notes", "our own notes"),
            Chan(snowflake(9 * DAY, serial=1), "general", messages.dm_bot_topic("X")),
            Chan(snowflake(9 * DAY, serial=2), "dm-screen", None),
        ]
        seen = Sightings()
        await self.look(theirs, set(), seen, NOW)
        await self.look(theirs, set(), seen, NOW + 5 * DAY)
        self.assertFalse(any(c.deleted for c in theirs))

    async def test_a_channel_under_a_day_old_waits_even_when_seen_a_day_ago(self) -> None:
        fresh = dm_bot(age=60)  # made a minute before the first look
        seen = Sightings()
        await self.look([fresh], set(), seen, NOW)
        # a day after the first look it is a day and a minute old: now it may go
        self.assertEqual((await self.look([fresh], set(), seen, NOW + DAY - 61)).deleted, 0)
        self.assertEqual((await self.look([fresh], set(), seen, NOW + DAY)).deleted, 1)

    async def test_a_channel_that_becomes_live_again_starts_its_clock_afresh(self) -> None:
        back = dm_bot()
        seen = Sightings()
        await self.look([back], set(), seen, NOW)
        await self.look([back], {back.id}, seen, NOW + DAY // 2)  # a restore gave it back
        again = await self.look([back], set(), seen, NOW + DAY)
        self.assertEqual((again.deleted, back.deleted), (0, False))  # first seen again just now
        self.assertEqual((await self.look([back], set(), seen, NOW + 2 * DAY)).deleted, 1)

    async def test_at_most_five_a_sweep_and_the_rest_wait(self) -> None:
        many = [dm_bot(serial=n, name=f"C{n}") for n in range(MAX_DELETES + 3)]
        seen = Sightings()
        await self.look(many, set(), seen, NOW)
        first = await self.look(many, set(), seen, NOW + DAY)
        self.assertEqual(first.deleted, MAX_DELETES)
        left = [c for c in many if not c.deleted]
        second = await self.look(left, set(), seen, NOW + 2 * DAY)
        self.assertEqual(second.deleted, 3)

    async def test_without_the_permission_it_counts_and_deletes_nothing(self) -> None:
        gone = dm_bot()
        seen = Sightings()
        await self.look([gone], set(), seen, NOW, can=False)
        done = await self.look([gone], set(), seen, NOW + 3 * DAY, can=False)
        self.assertEqual((done.seen, done.deleted, done.skipped), (1, 0, "no permission"))
        self.assertFalse(gone.deleted)

    async def test_a_channel_that_will_not_delete_is_counted_and_the_rest_go(self) -> None:
        stuck, fine = dm_bot(serial=1), dm_bot(serial=2, name="Other")
        stuck.fail = True
        seen = Sightings()
        await self.look([stuck, fine], set(), seen, NOW)
        done = await self.look([stuck, fine], set(), seen, NOW + DAY)
        self.assertEqual((done.deleted, done.failed, fine.deleted), (1, 1, True))

    async def test_another_servers_live_channel_never_matches_or_is_caught(self) -> None:
        # Server A's sweep is given only A's channels and A's live ids. B's live campaign's
        # channel is not in A's list, so it is never looked at; and B's live id does not stop A's
        # own leftover (a different channel) from going.
        in_b = dm_bot(serial=7, name="Beta")
        a_gone = dm_bot(serial=8, name="Alpha")
        seen_a = Sightings()
        live_a: set[int] = set()  # (A's live campaigns: none point at a_gone)
        await self.look([a_gone], live_a, seen_a, NOW)
        done = await self.look([a_gone], live_a, seen_a, NOW + DAY)
        self.assertEqual((a_gone.deleted, in_b.deleted), (True, False))
        self.assertEqual(done.deleted, 1)
        # and the other way: B's sweep keeps B's live channel whatever A holds
        seen_b = Sightings()
        await self.look([in_b], {in_b.id}, seen_b, NOW)
        await self.look([in_b], {in_b.id}, seen_b, NOW + 3 * DAY)
        self.assertFalse(in_b.deleted)

    async def test_one_servers_look_never_forgets_anothers(self) -> None:
        # the bot shares one `Sightings` between servers: A, then B, then A again
        a_gone, b_gone = dm_bot(serial=1, name="Alpha"), dm_bot(serial=2, name="Beta")
        seen = Sightings()
        await self.look([a_gone], set(), seen, NOW, guild_id=1)
        await self.look([b_gone], set(), seen, NOW + 3600, guild_id=2)
        await self.look([], set(), seen, NOW + 7200, guild_id=3)  # (a server with none)
        done = await self.look([a_gone], set(), seen, NOW + DAY, guild_id=1)
        self.assertEqual((done.deleted, a_gone.deleted), (1, True))  # A's first sighting survived
        later = await self.look([b_gone], set(), seen, NOW + 3600 + DAY, guild_id=2)
        self.assertEqual((later.deleted, b_gone.deleted), (1, True))


class TheWords(unittest.TestCase):
    def test_the_owner_hears_a_count_and_nothing_else(self) -> None:
        self.assertEqual(
            orphan_channels.told_words(2),
            "DMbot removed 2 leftover channel(s) of campaigns that no longer exist.",
        )
