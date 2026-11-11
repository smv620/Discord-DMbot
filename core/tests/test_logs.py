import asyncio
import io
import json
import logging
import unittest

from dmbot.logs import configure_logging, log_context, set_log_context
from dmbot.sharding import ShardSettings

GUILD = 81384788765712384  # shard 3 of 7
ONE_OF_SEVEN = ShardSettings(7, (3,))


class LogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = logging.getLogger()
        self.saved = (list(self.root.handlers), self.root.level)

    def tearDown(self) -> None:
        handlers, level = self.saved
        for h in list(self.root.handlers):
            self.root.removeHandler(h)
        for h in handlers:
            self.root.addHandler(h)
        self.root.setLevel(level)

    def capture(self, fmt: str, shards: ShardSettings = ONE_OF_SEVEN) -> io.StringIO:
        configure_logging(fmt, "INFO", shards)
        stream = io.StringIO()
        handler = self.root.handlers[0]
        assert isinstance(handler, logging.StreamHandler)
        handler.setStream(stream)
        return stream

    def lines(self, stream: io.StringIO) -> list[dict[str, object]]:
        return [json.loads(line) for line in stream.getvalue().splitlines()]

    def test_json_carries_ids(self) -> None:
        stream = self.capture("json")
        with log_context(guild_id=GUILD, campaign_id="c1"):
            logging.getLogger("dmbot.test").info("started %s", "ok")
        logging.getLogger("dmbot.test").warning("outside")
        inside, outside = self.lines(stream)
        self.assertEqual(inside["msg"], "started ok")
        self.assertEqual(inside["guild_id"], str(GUILD))  # text: too big for JS numbers
        self.assertEqual(inside["campaign_id"], "c1")
        self.assertEqual(inside["shard_id"], 3)
        self.assertEqual(inside["level"], "INFO")
        self.assertEqual(outside["shard_id"], 3)  # the only shard this process serves
        self.assertNotIn("guild_id", outside)

    def test_no_shard_guess_with_several_shards(self) -> None:
        stream = self.capture("json", ShardSettings(2, (0, 1)))
        logging.getLogger("x").info("hi")
        self.assertNotIn("shard_id", self.lines(stream)[0])

    def test_extra_overrides_context(self) -> None:
        stream = self.capture("json")
        with log_context(guild_id=1):
            logging.getLogger("x").info("hi", extra={"guild_id": GUILD})
        self.assertEqual(self.lines(stream)[0]["guild_id"], str(GUILD))

    def test_exceptions_are_included(self) -> None:
        stream = self.capture("json")
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            logging.getLogger("x").exception("failed")
        self.assertIn("boom", str(self.lines(stream)[0]["exc"]))

    def test_text_format(self) -> None:
        stream = self.capture("text")
        with log_context(guild_id=GUILD):
            logging.getLogger("dmbot.x").info("hello")
        self.assertRegex(stream.getvalue(), rf"INFO dmbot\.x \[shard=3 guild={GUILD}\]: hello")

    def test_context_is_per_task(self) -> None:
        stream = self.capture("json")

        async def work(guild: int) -> None:
            set_log_context(guild_id=guild)
            await asyncio.sleep(0)
            logging.getLogger("x").info("tick")

        async def both() -> None:
            await asyncio.gather(work(111 << 22), work(222 << 22))

        asyncio.run(both())
        guilds = sorted(str(e["guild_id"]) for e in self.lines(stream))
        self.assertEqual(guilds, sorted([str(111 << 22), str(222 << 22)]))


class LibraryLevels(unittest.TestCase):
    def setUp(self) -> None:
        self.root = logging.getLogger()
        self.saved = (list(self.root.handlers), self.root.level)
        self.discord_level = logging.getLogger("discord").level

    def tearDown(self) -> None:
        handlers, level = self.saved
        for h in list(self.root.handlers):
            self.root.removeHandler(h)
        for h in handlers:
            self.root.addHandler(h)
        self.root.setLevel(level)
        logging.getLogger("discord").setLevel(self.discord_level)

    def test_discord_never_logs_debug(self) -> None:
        # discord.py's DEBUG logs raw events: usernames and what people type.
        configure_logging("json", "DEBUG", ONE_OF_SEVEN)
        self.assertEqual(logging.getLogger("discord").level, logging.INFO)
        self.assertFalse(logging.getLogger("discord.gateway").isEnabledFor(logging.DEBUG))
        configure_logging("json", "WARNING", ONE_OF_SEVEN)
        self.assertEqual(logging.getLogger("discord").level, logging.WARNING)

    def test_configuring_twice_does_not_duplicate_lines(self) -> None:
        configure_logging("json", "INFO", ONE_OF_SEVEN)
        configure_logging("json", "INFO", ONE_OF_SEVEN)
        self.assertEqual(len(self.root.handlers), 1)

    def test_log_context_does_not_outlive_its_block(self) -> None:
        from dmbot.logs import _campaign, _guild

        with log_context(guild_id=GUILD, campaign_id="c1"):
            pass
        self.assertIsNone(_guild.get())
        self.assertIsNone(_campaign.get())


class RefusalWords(unittest.TestCase):
    """Why a rule refused something, for the log, with the entries' names left out (#4161)."""

    def test_the_rules_sentence_stays_and_the_names_go(self) -> None:
        from dmbot.logs import refusal_words

        exc = ValueError(
            "Only Cerric Vanya'elen travels with Okthandom Korran when cerric vanya'elen is a PC."
        )
        got = refusal_words(exc, "Cerric Vanya'elen", "Okthandom Korran")
        self.assertEqual(got, "Only [name] travels with [name] when [name] is a PC.")

    def test_it_is_one_short_line(self) -> None:
        from dmbot.logs import refusal_words

        got = refusal_words(ValueError("a\n  b\t" + "x" * 500), "")
        self.assertNotIn("\n", got)
        self.assertEqual(len(got), 120)
        self.assertTrue(got.startswith("a b xxx"))

    def test_regex_characters_overlapping_and_short_names_are_safe(self) -> None:
        from dmbot.logs import refusal_words

        exc = ValueError("A.B (x)+ and A.B (x)+ Prime meet; e is here")
        got = refusal_words(exc, "A.B (x)+", "A.B (x)+ Prime", "e")
        # (the longest name wins where one holds another, and "e" never reaches into "[name]")
        self.assertEqual(got, "[name] and [name] m[name][name]t; [name] is h[name]r[name]")

    def test_names_match_whatever_their_spacing_and_an_empty_message_is_empty(self) -> None:
        from dmbot.logs import refusal_words

        got = refusal_words(ValueError("Maera  Voss is one"), "Maera Voss ")
        self.assertEqual(got, "[name] is one")
        self.assertEqual(refusal_words(ValueError(""), "x"), "")
