"""The bot's answer to the DM's talk about an issue (#2123): the waiting message is claimed, one
call is made, the answer is written as a second row with its proposals, and nothing is saved to the
campaign. The AI is a stand-in; the memory store is the real one."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

from dmbot.memory.models import CONFIRMED
from dmbot.memory.store import MemoryStore
from dmbot.recaps.plot_talk_run import TalkRunner, TalkStore
from tests.test_recap_plot import PlotBase
from tests.test_web_api import THURSDAY


class Ai:
    def __init__(self, reply: str, *, refused: bool = False) -> None:
        self.reply, self.refused, self.calls, self.sent = reply, refused, 0, ""

    async def complete(self, system: str, text: str, **kw: Any) -> Any:
        self.calls += 1
        self.sent = text
        return SimpleNamespace(text=self.reply, refused=self.refused, cut=False)


def good(*proposals: Any, reply: str = "That would be a change in the story.") -> str:
    return json.dumps({"reply": reply, "proposals": list(proposals)})


class TalkBase(PlotBase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self.checked()
        self.issue_id = await self.issue("a1")
        self.talk = TalkStore(self.db)
        self.memory = MemoryStore(self.db)
        self.ulfgar = await self.memory.add_entity(
            THURSDAY.id, self.one.id, type="character", name="Ulfgar", source="dm", status=CONFIRMED
        )
        await self.memory.set_condition(
            THURSDAY.id, self.one.id, self.ulfgar.value.id, "dead", source="dm"
        )

    def runner(self, ai: Ai | None) -> TalkRunner:
        return TalkRunner(self.talk, self.memory, ai)

    async def say(self, text: str = "Ulfgar came back as a ghost.") -> None:
        sent = await self.client.post(self.url(), json={"text": text})
        self.assertEqual(sent.status_code, 200, sent.text)

    def url(self, tail: str = "chat") -> str:
        return f"/me/recaps/{self.ref}/plot/{self.issue_id}/{tail}"

    async def messages(self) -> list[dict[str, Any]]:
        return list((await self.client.get(self.url())).json()["messages"])


class Answering(TalkBase):
    async def test_the_message_is_answered_and_the_page_reads_it(self) -> None:
        await self.say()
        ai = Ai(good({"entry": 1, "condition": "alive"}))
        self.assertEqual(await self.runner(ai).round(THURSDAY.id), 1)
        dm, bot = await self.messages()
        self.assertEqual((dm["role"], dm["state"]), ("dm", "done"))
        self.assertEqual((bot["role"], bot["state"]), ("bot", "done"))
        [p] = bot["proposals"]
        self.assertEqual((p["label"], p["state"]), ("Ulfgar: alive", "open"))
        self.assertIn("Press Save", bot["text"])

    async def test_the_call_is_given_the_issue_the_recap_and_the_entry_it_is_about(self) -> None:
        await self.say()
        ai = Ai(good())
        await self.runner(ai).round(THURSDAY.id)
        self.assertIn("Ulfgar spoke.", ai.sent)  # the issue's sentence
        self.assertIn("Ulfgar (character), now dead", ai.sent)  # the entry and what it is now
        self.assertIn("Ulfgar came back as a ghost.", ai.sent)  # the DM's words

    async def test_nothing_is_saved_to_the_campaign_by_a_proposal(self) -> None:
        await self.say()
        await self.runner(Ai(good({"entry": 1, "condition": "alive"}))).round(THURSDAY.id)
        held = await self.memory.states(THURSDAY.id, self.one.id)
        self.assertEqual(held[self.ulfgar.value.id].condition, "dead")  # unchanged until Save

    async def test_a_request_to_invent_story_gets_the_plain_no_and_no_call(self) -> None:
        await self.say("Invent a reason Ulfgar is alive.")
        ai = Ai(good())
        await self.runner(ai).round(THURSDAY.id)
        self.assertEqual(ai.calls, 0)
        bot = (await self.messages())[-1]
        self.assertIn("only retells what happened at your table", bot["text"])

    async def test_a_quotation_from_anywhere_else_fails_the_answer(self) -> None:
        await self.say()
        ai = Ai(good(reply='A player said "Gorrak swung the axe at dawn" earlier.'))
        await self.runner(ai).round(THURSDAY.id)
        [dm] = await self.messages()
        self.assertEqual((dm["state"], dm["error"]), ("failed", "bad_answer"))

    async def test_an_answer_that_cannot_be_used_or_a_refusal_fails_the_message(self) -> None:
        for ai, code in ((Ai("not json"), "bad_answer"), (Ai(good(), refused=True), "no_answer")):
            await self.say()
            await self.runner(ai).round(THURSDAY.id)
            [dm] = await self.messages()
            self.assertEqual((dm["state"], dm["error"]), ("failed", code))
            async with self.db.guild(THURSDAY.id) as conn:
                await conn.execute("DELETE FROM plot_chat")
        self.assertEqual((await self.client.get(self.url())).status_code, 200)

    async def test_with_no_ai_the_message_fails_and_the_dm_can_go_on(self) -> None:
        await self.say()
        await self.runner(None).round(THURSDAY.id)
        [dm] = await self.messages()
        self.assertEqual((dm["state"], dm["error"]), ("failed", "unavailable"))
        await self.say("Are you there?")  # not stuck behind the failed one

    async def test_a_call_that_hangs_fails_the_message(self) -> None:
        from unittest.mock import patch

        class Hangs(Ai):
            async def complete(self, system: str, text: str, **kw: Any) -> Any:
                await asyncio.sleep(60)

        await self.say()
        with patch("dmbot.recaps.plot_talk_run.CALL_TIMEOUT_S", 0.05):
            await self.runner(Hangs("")).round(THURSDAY.id)
        [dm] = await self.messages()
        self.assertEqual((dm["state"], dm["error"]), ("failed", "failed"))

    async def test_a_changed_recap_fails_the_message_as_stale(self) -> None:
        await self.say()
        await self.store.update(THURSDAY.id, self.one.id, 100, summary="A different recap.")
        ai = Ai(good())
        await self.runner(ai).round(THURSDAY.id)
        self.assertEqual(ai.calls, 0)
        [dm] = await self.messages()
        self.assertEqual((dm["state"], dm["error"]), ("failed", "stale"))

    async def created_at(self) -> int:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute("SELECT max(created_at) AS t FROM plot_chat")
            return int((await cur.fetchone() or {})["t"])

    async def test_a_claim_taken_back_by_another_worker_writes_nothing(self) -> None:
        await self.say()
        first = await self.talk.claim_next(THURSDAY.id)
        assert first is not None
        after = float(await self.created_at() + 200)  # past the claim's 120 s, inside 10 minutes
        late = TalkStore(self.db, clock=lambda: after)
        second = await late.claim_next(THURSDAY.id)
        assert second is not None
        from dmbot.memory.plot_talk import Answer

        self.assertFalse(await self.talk.finish(THURSDAY.id, first, Answer("late")))
        self.assertTrue(await late.finish(THURSDAY.id, second, Answer("on time")))
        self.assertEqual([m["role"] for m in await self.messages()], ["dm", "bot"])

    async def test_only_the_talk_of_this_dm_and_issue_is_shown_to_the_call(self) -> None:
        await self.say("First point.")
        await self.runner(Ai(good())).round(THURSDAY.id)
        await self.say("Second point.")
        ai = Ai(good())
        await self.runner(ai).round(THURSDAY.id)
        self.assertIn("First point.", ai.sent)  # the earlier turn is history
        self.assertIn("That would be a change in the story.", ai.sent)

    async def test_nothing_else_waiting_means_nothing_is_done(self) -> None:
        self.assertEqual(await self.runner(Ai(good())).round(THURSDAY.id), 0)

    async def test_an_entry_is_shown_only_when_its_name_is_a_whole_word_in_the_talk(self) -> None:
        await self.memory.add_entity(
            THURSDAY.id, self.one.id, type="character", name="Ann", source="dm", status=CONFIRMED
        )
        await self.say("The channel was quiet.")  # "Ann" is inside "channel": not a mention
        ai = Ai(good())
        await self.runner(ai).round(THURSDAY.id)
        self.assertNotIn("Ann (character)", ai.sent)
        self.assertIn("Ulfgar (character)", ai.sent)  # (named in the issue's own sentence)

    async def test_a_message_that_keeps_crashing_the_worker_is_given_up_on(self) -> None:
        await self.say()
        self.assertIsNotNone(await self.talk.claim_next(THURSDAY.id))
        t = await self.created_at()
        soon = TalkStore(self.db, clock=lambda: float(t + 200))
        self.assertIsNotNone(await soon.claim_next(THURSDAY.id))  # a stalled claim is retaken
        late = TalkStore(self.db, clock=lambda: float(t + 700))
        self.assertIsNone(await late.claim_next(THURSDAY.id))  # but not forever

    async def test_the_sweep_deletes_old_talks_in_its_own_server_only(self) -> None:
        from dmbot.campaigns.store import CampaignStore
        from dmbot.recaps.plot_talk_run import KEEP_S

        other_guild = THURSDAY.id + 1
        elsewhere = await CampaignStore(self.db).create(other_guild, "Else", 1)
        await self.say("First.")
        await self.runner(Ai(good())).round(THURSDAY.id)  # (one message waits at a time)
        await self.say("Second.")
        now = await self.created_at()
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE plot_chat SET created_at = %s WHERE body = 'First.' AND role = 'dm'",
                (now - KEEP_S - 86400,),  # 31 days old
            )
            await conn.execute(
                "UPDATE plot_chat SET created_at = %s WHERE body = 'Second.'",
                (now - KEEP_S + 86400,),  # 29 days old
            )
        async with self.db.guild(other_guild) as conn:
            await conn.execute(
                "INSERT INTO plot_chat (guild_id, campaign_id, id, issue_id, session_at, user_id,"
                " role, body, state, created_at) VALUES (%s, %s, %s, 'x', 100, 1, 'dm', 'Old.',"
                " 'done', %s)",
                (other_guild, elsewhere.id, "a" * 32, now - KEEP_S - 86400),
            )
        sweeper = TalkStore(self.db, clock=lambda: float(now))
        self.assertEqual(await sweeper.sweep(THURSDAY.id), 1)
        self.assertEqual(
            [m["text"] for m in await self.messages() if m["role"] == "dm"], ["Second."]
        )
        async with self.db.guild(other_guild) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM plot_chat")
            self.assertEqual((await cur.fetchone() or {})["n"], 1)  # the other server's stays

    async def test_a_timed_out_message_gets_no_answer_row(self) -> None:
        await self.say()
        m = await self.talk.claim_next(THURSDAY.id)
        assert m is not None
        async with self.db.guild(THURSDAY.id) as conn:  # the send door's ten minutes ran out
            await conn.execute("UPDATE plot_chat SET state = 'failed', error = 'timed_out'")
        from dmbot.memory.plot_talk import Answer

        self.assertFalse(await self.talk.finish(THURSDAY.id, m, Answer("too late")))
        self.assertEqual([x["role"] for x in await self.messages()], ["dm"])

    async def test_another_campaigns_talk_and_entries_are_never_shown(self) -> None:
        await self.memory.add_entity(
            THURSDAY.id, self.two.id, type="character", name="Zed", source="dm", status=CONFIRMED
        )
        await self.say("Zed is mentioned here, and Ulfgar.")
        ai = Ai(good())
        await self.runner(ai).round(THURSDAY.id)
        self.assertNotIn("Zed (character)", ai.sent)  # campaign two's entry
        self.assertIn("Ulfgar (character)", ai.sent)


class Saving(TalkBase):
    """Save: the bot makes the change as the DM's own fact, then answers the issue "changed"."""

    async def reply_with_proposal(self, condition: str = "alive") -> dict[str, Any]:
        await self.say()
        await self.runner(Ai(good({"entry": 1, "condition": condition}))).round(THURSDAY.id)
        [p] = (await self.messages())[-1]["proposals"]
        return dict(p)

    async def press(self, proposal: dict[str, Any], action: str) -> Any:
        return await self.client.post(
            self.url(f"proposals/{proposal['id']}"), json={"action": action}
        )

    async def condition(self) -> str:
        held = await self.memory.states(THURSDAY.id, self.one.id)
        return str(held[self.ulfgar.value.id].condition)

    async def test_nothing_is_saved_until_save_is_pressed(self) -> None:
        await self.reply_with_proposal()
        self.assertEqual(await self.runner(Ai(good())).apply_round(THURSDAY.id), 0)
        self.assertEqual(await self.condition(), "dead")

    async def test_not_that_saves_nothing_and_the_bot_has_nothing_to_do(self) -> None:
        p = await self.reply_with_proposal()
        self.assertEqual((await self.press(p, "decline")).status_code, 200)
        self.assertEqual(await self.runner(Ai(good())).apply_round(THURSDAY.id), 0)
        self.assertEqual(await self.condition(), "dead")
        [issue] = (await self.client.get(f"/me/recaps/{self.ref}/plot")).json()["issues"]
        self.assertIsNone(issue["answer"])

    async def test_save_makes_the_change_as_the_dms_own_fact_and_answers_the_issue(self) -> None:
        p = await self.reply_with_proposal()
        self.assertEqual((await self.press(p, "save")).json()["state"], "saving")
        self.assertEqual(await self.runner(Ai(good())).apply_round(THURSDAY.id), 1)
        self.assertEqual(await self.condition(), "alive")
        history = await self.memory.state_history(THURSDAY.id, self.one.id, self.ulfgar.value.id)
        # (history is kept; two changes in one second have no fixed order, so look by condition)
        self.assertEqual({h.condition for h in history}, {"dead", "alive"})
        [now] = [h for h in history if h.condition == "alive"]
        self.assertEqual(now.source, "dm")
        self.assertEqual(now.from_session_at, 100)  # the session the DM said it from
        [saved] = (await self.messages())[-1]["proposals"]
        self.assertEqual((saved["state"], saved["error"]), ("saved", ""))
        [issue] = (await self.client.get(f"/me/recaps/{self.ref}/plot")).json()["issues"]
        self.assertEqual(issue["answer"]["kind"], "changed")
        self.assertIn("Ulfgar: alive", issue["answer"]["note"])

    async def test_a_repeat_changes_nothing_more(self) -> None:
        p = await self.reply_with_proposal()
        await self.press(p, "save")
        runner = self.runner(Ai(good()))
        await runner.apply_round(THURSDAY.id)
        self.assertEqual(await runner.apply_round(THURSDAY.id), 0)  # nothing is saving any more
        history = await self.memory.state_history(THURSDAY.id, self.one.id, self.ulfgar.value.id)
        self.assertEqual(len(history), 2)

    async def test_a_change_the_rules_refuse_fails_that_proposal_in_plain_words(self) -> None:
        p = await self.reply_with_proposal("missing")  # dead -> missing: the table does not allow
        await self.press(p, "save")
        await self.runner(Ai(good())).apply_round(THURSDAY.id)
        self.assertEqual(await self.condition(), "dead")
        [failed] = (await self.messages())[-1]["proposals"]
        self.assertEqual(failed["state"], "failed")
        self.assertTrue(failed["error"])  # plain words for the DM
        [issue] = (await self.client.get(f"/me/recaps/{self.ref}/plot")).json()["issues"]
        self.assertIsNone(issue["answer"])  # a refused change answers nothing

    async def test_an_entry_that_is_gone_fails_the_proposal(self) -> None:
        p = await self.reply_with_proposal()
        await self.press(p, "save")
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("UPDATE memory_entities SET status = 'rejected'")
        await self.runner(Ai(good())).apply_round(THURSDAY.id)
        [failed] = (await self.messages())[-1]["proposals"]
        self.assertEqual(failed["state"], "failed")

    async def test_someone_who_stopped_being_a_dm_cannot_have_it_saved(self) -> None:
        p = await self.reply_with_proposal()
        await self.press(p, "save")
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("DELETE FROM campaign_dms WHERE campaign_id = %s", (self.one.id,))
        await self.runner(Ai(good())).apply_round(THURSDAY.id)
        self.assertEqual(await self.condition(), "dead")

    async def test_a_recap_that_changed_since_fails_the_save(self) -> None:
        p = await self.reply_with_proposal()
        await self.press(p, "save")
        await self.store.update(THURSDAY.id, self.one.id, 100, summary="A different recap.")
        await self.runner(Ai(good())).apply_round(THURSDAY.id)
        self.assertEqual(await self.condition(), "dead")
        [failed] = (await self.messages())[-1]["proposals"]
        self.assertEqual(failed["state"], "failed")

    async def test_save_leaves_a_dms_earlier_answer_alone(self) -> None:
        p = await self.reply_with_proposal()
        await self.client.post(self.url("answer"), json={"kind": "leave", "note": ""})
        await self.press(p, "save")
        await self.runner(Ai(good())).apply_round(THURSDAY.id)
        [issue] = (await self.client.get(f"/me/recaps/{self.ref}/plot")).json()["issues"]
        self.assertEqual(issue["answer"]["kind"], "leave")
        self.assertEqual(await self.condition(), "alive")  # the change was still made

    async def test_a_retry_after_a_crash_does_not_make_the_change_twice(self) -> None:
        """The change was made but the proposal still reads `saving` (the bot stopped): the retry
        finds it already so, and just closes the proposal."""
        p = await self.reply_with_proposal()
        await self.press(p, "save")
        await self.memory.set_condition(
            THURSDAY.id, self.one.id, self.ulfgar.value.id, "alive", source="dm"
        )  # (the change was made)
        await self.runner(Ai(good())).apply_round(THURSDAY.id)
        history = await self.memory.state_history(THURSDAY.id, self.one.id, self.ulfgar.value.id)
        self.assertEqual(len(history), 2)  # dead, then alive: no third row
        self.assertEqual((await self.messages())[-1]["proposals"][0]["state"], "saved")

    async def test_garbage_in_a_proposals_data_fails_that_proposal_only(self) -> None:
        await self.say()
        await self.runner(Ai(good({"entry": 1, "condition": "alive"}))).round(THURSDAY.id)
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE plot_chat SET proposals = jsonb_build_array(jsonb_build_object("
                "'id', 'p1', 'kind', 'condition', 'label', 5, 'data', '[]'::jsonb,"
                " 'state', 'saving'), jsonb_build_object('id', 'p2', 'state', 'open'))"
                " WHERE role = 'bot'"
            )
        await self.runner(Ai(good())).apply_round(THURSDAY.id)
        first, second = (await self.messages())[-1]["proposals"]
        self.assertEqual((first["state"], second["state"]), ("failed", "open"))

    async def test_a_sibling_proposal_in_the_same_message_is_untouched(self) -> None:
        first = await self.reply_with_proposal()
        second = {**first, "id": "f" * 32}
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE plot_chat SET proposals = %s::jsonb WHERE role = 'bot'",
                (json.dumps([first, second]),),
            )
        await self.press(second, "save")
        await self.runner(Ai(good())).apply_round(THURSDAY.id)
        states = [p["state"] for p in (await self.messages())[-1]["proposals"]]
        self.assertEqual(states, ["open", "saved"])

    async def test_a_stopped_dm_fails_the_proposal_with_its_reason(self) -> None:
        p = await self.reply_with_proposal()
        await self.press(p, "save")
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute("DELETE FROM campaign_dms WHERE campaign_id = %s", (self.one.id,))
        await self.runner(Ai(good())).apply_round(THURSDAY.id)
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute("SELECT proposals FROM plot_chat WHERE role = 'bot'")
            [row] = await cur.fetchall()
        self.assertEqual(row["proposals"][0]["state"], "failed")
        self.assertIn("DM", row["proposals"][0]["error"])

    async def test_a_failure_part_way_leaves_nothing_changed_and_a_retry_works(self) -> None:
        from unittest.mock import patch

        p = await self.reply_with_proposal()
        await self.press(p, "save")
        runner = self.runner(Ai(good()))
        with patch.object(TalkStore, "mark_in", side_effect=RuntimeError("boom")):
            await runner.apply_round(THURSDAY.id)
        self.assertEqual(await self.condition(), "dead")  # the condition rolled back with it
        history = await self.memory.state_history(THURSDAY.id, self.one.id, self.ulfgar.value.id)
        self.assertEqual(len(history), 1)
        # (the patch breaks closing it too: it stays `saving`, and the next round does it all)
        [waiting] = (await self.messages())[-1]["proposals"]
        self.assertEqual(waiting["state"], "saving")
        await runner.apply_round(THURSDAY.id)
        self.assertEqual(await self.condition(), "alive")
        [saved] = (await self.messages())[-1]["proposals"]
        self.assertEqual(saved["state"], "saved")

    async def test_the_saved_proposal_keeps_the_id_of_the_state_it_became(self) -> None:
        p = await self.reply_with_proposal()
        await self.press(p, "save")
        await self.runner(Ai(good())).apply_round(THURSDAY.id)
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute("SELECT proposals FROM plot_chat WHERE role = 'bot'")
            [row] = await cur.fetchall()
        held = await self.memory.states(THURSDAY.id, self.one.id)
        self.assertEqual(row["proposals"][0]["state_id"], held[self.ulfgar.value.id].id)

    async def test_an_entry_of_another_campaign_is_never_changed(self) -> None:
        zed = await self.memory.add_entity(
            THURSDAY.id, self.two.id, type="character", name="Zed", source="dm", status=CONFIRMED
        )
        await self.memory.set_condition(THURSDAY.id, self.two.id, zed.value.id, "dead", source="dm")
        p = await self.reply_with_proposal()
        await self.press(p, "save")
        async with self.db.guild(THURSDAY.id) as conn:  # the proposal's data is only data
            await conn.execute(
                "UPDATE plot_chat SET proposals = jsonb_set(proposals, '{0,data,entity_id}',"
                " to_jsonb(%s::text)) WHERE role = 'bot'",
                (zed.value.id,),
            )
        await self.runner(Ai(good())).apply_round(THURSDAY.id)
        [failed] = (await self.messages())[-1]["proposals"]
        self.assertEqual(failed["state"], "failed")
        held = await self.memory.states(THURSDAY.id, self.two.id)
        self.assertEqual(held[zed.value.id].condition, "dead")
        history = await self.memory.state_history(THURSDAY.id, self.two.id, zed.value.id)
        self.assertEqual(len(history), 1)

    async def test_a_condition_the_kind_cannot_have_fails_in_plain_words(self) -> None:
        p = await self.reply_with_proposal()
        await self.press(p, "save")
        async with self.db.guild(THURSDAY.id) as conn:  # (a character is never "destroyed")
            await conn.execute(
                "UPDATE plot_chat SET proposals = jsonb_set(proposals, '{0,data,condition}',"
                " '\"destroyed\"') WHERE role = 'bot'"
            )
        await self.runner(Ai(good())).apply_round(THURSDAY.id)
        [failed] = (await self.messages())[-1]["proposals"]
        self.assertEqual(failed["state"], "failed")
        self.assertTrue(failed["error"])
        self.assertEqual(await self.condition(), "dead")

    async def test_already_so_is_said_as_already_so(self) -> None:
        p = await self.reply_with_proposal()
        await self.press(p, "save")
        await self.memory.set_condition(
            THURSDAY.id, self.one.id, self.ulfgar.value.id, "alive", source="dm"
        )
        await self.runner(Ai(good())).apply_round(THURSDAY.id)
        [issue] = (await self.client.get(f"/me/recaps/{self.ref}/plot")).json()["issues"]
        self.assertIn("Already so", issue["answer"]["note"])

    async def test_a_change_the_dm_made_by_hand_after_the_proposal_stands(self) -> None:
        p = await self.reply_with_proposal()  # PlotBot saw "dead" and proposes "alive"
        await self.press(p, "save")
        for later in ("unknown", "transformed", "captured"):
            try:  # the DM changes it by hand before the bot applies the Save
                await self.memory.set_condition(
                    THURSDAY.id, self.one.id, self.ulfgar.value.id, later, source="dm"
                )
                break
            except Exception:  # the table does not allow that step from "dead": try the next
                continue
        else:
            self.fail("no condition could be set by hand")
        await self.runner(Ai(good())).apply_round(THURSDAY.id)
        self.assertEqual(await self.condition(), later)  # the DM's word stands
        [failed] = (await self.messages())[-1]["proposals"]
        self.assertEqual(failed["state"], "failed")
        self.assertIn("changed since", failed["error"])
