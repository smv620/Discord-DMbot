"""The email chain is kept (#2192): every email to the person carries the conversation so far,
newest first and labelled; a reply sent back with no ">" is cut at the fixed line; the emails
thread through their Message-ID, In-Reply-To and References headers; a long history is cut to the
newest parts; and none of the words reach the log or a header. Invented words throughout."""

from __future__ import annotations

import re

from dmbot.web import support
from dmbot.web import support_ai as ai
from dmbot.web import support_mail as sm
from tests.test_support import MESSAGE, SUBJECT, TEAM
from tests.test_support_helper import ALICE_EMAIL, HelperBase, answer, new_words


def ids(header: str) -> list[str]:
    return re.findall(r"<[^<>\s]+>", header)


class TheHistory(HelperBase):
    async def talk(self) -> int:
        """A ticket, the helper's answer, the person's reply."""
        number = await self.ticket()
        self.fake.answers.append(answer("Open the menu."))
        await self.helper.run_once()
        to = str(self.mails(to_alice=True)[-1]["Reply-To"])
        await self.inbound(to, "Still red on my side.")
        return number

    async def test_the_thank_you_carries_no_words_and_no_history(self) -> None:
        await self.ticket()
        (ack,) = self.mails()
        self.assertNotIn(ai.HISTORY_HEADING, ack.get_content())
        self.assertEqual(str(ack["Subject"]), f"Re: {SUBJECT}")

    async def test_the_helpers_answer_carries_the_conversation_newest_first_with_labels(
        self,
    ) -> None:
        await self.talk()
        self.fake.answers.append(answer("Try the other button."))
        await self.helper.run_once()
        mail = self.mails(to_alice=True)[-1]
        body = mail.get_content()
        self.assertTrue(new_words(mail).startswith("Try the other button."))
        history = body.split(ai.HISTORY_HEADING, 1)[1]
        order = [
            history.index("You,"),
            history.index("DMbot's helper (a program),"),
            history.rindex("You,"),
        ]
        self.assertEqual(order, sorted(order))  # newest first: the reply, the answer, the question
        self.assertLess(history.index("Still red on my side."), history.index("Open the menu."))
        self.assertLess(history.index("Open the menu."), history.index(MESSAGE))
        self.assertRegex(history, r"You, \d{1,2} [A-Z][a-z]+ \d{4}:")

    async def test_the_teams_reply_and_the_hand_over_note_carry_it_too(self) -> None:
        number = await self.talk()
        await self.helper.team_reply(number, "I fixed it on our side.")
        reply = self.mails(to_alice=True)[-1]
        self.assertEqual(new_words(reply), "I fixed it on our side.")
        history = reply.get_content().split(ai.HISTORY_HEADING, 1)[1]
        self.assertIn("Still red on my side.", history)
        self.assertIn(MESSAGE, history)
        self.assertNotIn("DMbot team,", history)  # the new words are not repeated below
        # A hand-over note carries it as well.
        number = await self.ticket("I want a person")
        await self.helper.run_once()
        note = self.mails(to_alice=True)[-1]
        self.assertEqual(new_words(note), ai.ESCALATED_BODY)
        self.assertIn("I want a person", note.get_content())

    async def test_the_teams_own_email_has_the_same_format(self) -> None:
        number = await self.talk()
        for _ in range(ai.MAX_AI_ANSWERS):
            self.fake.answers.append(answer("More."))
            await self.helper.run_once()
            to = str(self.mails(to_alice=True)[-1]["Reply-To"])
            await self.inbound(to, "No.")
        await self.helper.run_once()
        alert = next(m for m in self.mails() if "needs you" in str(m["Subject"]))
        text = alert.get_content()
        self.assertIn(ai.HISTORY_HEADING, text)
        self.assertIn("The customer,", text)
        self.assertIn("DMbot's helper (a program),", text)
        self.assertEqual(await self.status(number), "escalated")

    async def test_a_reply_with_no_quote_marks_is_cut_at_the_fixed_line(self) -> None:
        await self.talk()
        mail = self.mails(to_alice=True)[-1]
        sent_back = (
            f"Thanks, that worked.\n\n{new_words(mail)}\n\n{ai.HISTORY_HEADING}\n\n"
            "You, 1 May 2027:\nan old line"
        )
        self.assertEqual(sm.fresh_words(sent_back), "Thanks, that worked.\n\nOpen the menu.")
        self.assertEqual(sm.fresh_words(f"Thanks.\n{ai.HISTORY_HEADING}\nold"), "Thanks.")

    async def test_a_long_history_keeps_the_newest_parts_and_says_where_the_rest_is(self) -> None:
        number = await self.ticket("first " * 10)
        first = await support.one(self.web, number)
        assert first is not None
        replies = [
            support.Reply(i, "customer" if i % 2 else "ai", f"part {i} " + "x" * 400, 1_800_000_000)
            for i in range(1, 30)
        ]
        text = ai.conversation(first, replies, limit=3000)
        self.assertTrue(text.startswith(ai.HISTORY_HEADING))
        self.assertTrue(text.endswith(ai.HISTORY_OLDER))
        self.assertIn("part 29", text)
        self.assertNotIn("part 1 ", text)
        self.assertLess(len(text), 3000 + 600)
        whole = ai.conversation(first, replies[:2])
        self.assertNotIn(ai.HISTORY_OLDER, whole)


class TheLimits(HelperBase):
    async def test_the_default_cut_holds_even_for_one_huge_message(self) -> None:
        number = await self.ticket("w" * 3000)
        first = await support.one(self.web, number)
        assert first is not None
        huge = [support.Reply(i, "ai", "y" * 7900, 1_800_000_000) for i in range(1, 6)]
        text = ai.conversation(first, huge)
        self.assertLessEqual(
            len(text), ai.HISTORY_MAX + len(ai.HISTORY_HEADING) + len(ai.HISTORY_OLDER) + 400
        )
        self.assertTrue(text.endswith(ai.HISTORY_OLDER))
        one = ai.conversation(first, [support.Reply(1, "ai", "z" * 30_000, 1_800_000_000)])
        self.assertLessEqual(len(one), ai.HISTORY_MAX + len(ai.HISTORY_HEADING) + 400)

    async def test_the_team_is_pointed_to_its_own_page_for_older_messages(self) -> None:
        number = await self.ticket("first")
        first = await support.one(self.web, number)
        assert first is not None
        many = [support.Reply(i, "customer", "q" * 900, 1_800_000_000) for i in range(1, 30)]
        text = ai.conversation(
            first, many, speakers=ai.TEAM_SPEAKERS, limit=3000, older=ai.HISTORY_OLDER_TEAM
        )
        self.assertTrue(text.endswith(ai.HISTORY_OLDER_TEAM))

    async def test_the_history_goes_only_to_the_tickets_person(self) -> None:
        await self.ticket()
        self.fake.answers.append(answer("Open the menu."))
        await self.helper.run_once()
        await self.helper.team_reply(1, "Hello.")
        for mail in self.mails():
            if str(mail["To"]) not in (ALICE_EMAIL, TEAM):
                self.fail(f"an email went to {mail['To']}")
            if ai.HISTORY_HEADING in mail.get_content():
                self.assertEqual(str(mail["To"]), ALICE_EMAIL)


class TheThreading(HelperBase):
    async def test_the_emails_chain_through_their_message_ids(self) -> None:
        number = await self.ticket()
        self.fake.answers.append(answer("Open the menu."))
        await self.helper.run_once()
        await self.helper.team_reply(number, "Hello from the team.")
        ack, answered, team = self.mails(to_alice=True)
        first, second, third = (ids(str(m["Message-ID"]))[0] for m in (ack, answered, team))
        self.assertEqual(len({first, second, third}), 3)
        self.assertIsNone(ack["In-Reply-To"])
        self.assertEqual(ids(str(answered["In-Reply-To"])), [first])
        self.assertEqual(ids(str(answered["References"])), [first])
        self.assertEqual(ids(str(team["In-Reply-To"])), [second])
        self.assertEqual(ids(str(team["References"])), [first, second])
        self.assertEqual(await support.mail_ids(self.web, number), [first, second, third])

    async def test_no_header_carries_the_tickets_words_or_the_address(self) -> None:
        await self.ticket()
        self.fake.answers.append(answer("Open the menu."))
        await self.helper.run_once()
        answered = self.mails(to_alice=True)[-1]
        for name in ("Message-ID", "In-Reply-To", "References"):
            value = str(answered[name])
            self.assertNotIn(MESSAGE[:10], value)
            self.assertNotIn(SUBJECT, value)
            self.assertNotIn(ALICE_EMAIL.split("@")[0], value)

    async def test_nothing_is_logged(self) -> None:
        with self.assertLogs(level="DEBUG") as logged:
            await self.ticket()
            self.fake.answers.append(answer("Open the menu."))
            await self.helper.run_once()
            await self.helper.team_reply(1, "A private word from the team.")
        text = " ".join(r.getMessage() for r in logged.records)
        for private in (MESSAGE, SUBJECT, ALICE_EMAIL, "Open the menu.", "private word"):
            self.assertNotIn(private, text)

    async def test_a_bad_id_is_refused_so_no_header_can_carry_anything_else(self) -> None:
        for bad in ("<a b@c>", "<x@y>\nBcc: me@example.org", "no-brackets@y", "<@y>"):
            with self.subTest(bad=bad):
                sent = await self.mailer.send(
                    user_id=1,
                    kind=f"thread-test:{abs(hash(bad))}",
                    to=ALICE_EMAIL,
                    subject="Re: x",
                    body="x",
                    in_reply_to=bad,
                )
                self.assertEqual(sent, "failed")

    async def test_the_ids_are_for_the_admin_door_only(self) -> None:
        number = await self.ticket()
        self.assertEqual(len(await support.mail_ids(self.web, number)), 1)
        async with self.web.user(1) as conn:  # an ordinary session sees none of it
            cur = await conn.execute("SELECT count(*) AS n FROM support_mail_ids")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)
