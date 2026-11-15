"""One account, two Discord logins (#2533, slice 2b): each person's lines stay under the Discord
ID of the login that said them, so "delete my lines" for one login leaves the other login's lines
and the friends' lines. Needs Postgres."""

from __future__ import annotations

from dmbot.campaigns import CampaignStore
from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from tests.pg import DatabaseTest
from tests.test_account_discord_ids import add_user, link

GUILD = 1
# ACCOUNT is the account's first login too: add_user and link treat the account id that way.
ACCOUNT, SECOND, FRIEND = 7001, 7002, 7003
START = 1_700_000_000


def line(seconds: int, user: int, text: str) -> Line:
    return Line(START * 1000 + seconds * 1000, user, text, text)


class SpeakersPerLogin(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await add_user(self.db, ACCOUNT)
        self.assertEqual(await link(self.db, ACCOUNT, SECOND), "ok")
        self.campaigns = CampaignStore(self.db)
        self.store = TranscriptStore(self.db)
        campaign = await self.campaigns.create(GUILD, "Frostmaiden", ACCOUNT)
        self.session = await self.store.open_session(GUILD, campaign.id, START)
        await self.store.add_lines(
            GUILD,
            self.session,
            [
                line(1, ACCOUNT, "First login speaks"),
                line(2, SECOND, "Second login speaks"),
                line(3, FRIEND, "A friend speaks"),
                line(4, SECOND, "Second again"),
            ],
        )

    async def test_each_line_keeps_the_discord_id_of_who_said_it(self) -> None:
        users = [x.user_id for x in await self.store.lines(GUILD, self.session)]
        self.assertEqual(users, [ACCOUNT, SECOND, FRIEND, SECOND])

    async def test_the_session_lists_both_logins_as_speakers(self) -> None:
        session = await self.store.session(GUILD, self.session)
        assert session is not None
        self.assertEqual(set(session.speakers), {ACCOUNT, SECOND, FRIEND})

    async def test_deleting_one_logins_words_leaves_the_other_logins_and_the_friends(self) -> None:
        gone = await self.store.delete_person(GUILD, SECOND)
        self.assertEqual(gone.lines, 2)
        users = [x.user_id for x in await self.store.lines(GUILD, self.session)]
        self.assertEqual(users, [ACCOUNT, FRIEND])
        again = await self.store.delete_person(GUILD, SECOND)  # idempotent: nothing left to go
        self.assertEqual(again.lines, 0)

    async def test_deleting_the_first_login_leaves_the_added_logins_lines(self) -> None:
        await self.store.delete_person(GUILD, ACCOUNT)
        users = [x.user_id for x in await self.store.lines(GUILD, self.session)]
        self.assertEqual(users, [SECOND, FRIEND, SECOND])
