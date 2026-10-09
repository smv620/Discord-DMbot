import json
import unittest
from pathlib import Path

from dmbot.ears.protocol import (
    AUDIO_FRAME_KIND,
    AUDIO_HEADER_BYTES,
    Health,
    Hello,
    Speaking,
    Status,
    allowlist_command,
    decode_audio_frame,
    encode_audio_frame,
    join_command,
    leave_command,
    parse_ears_message,
)

FIXTURES = json.loads(
    (Path(__file__).resolve().parents[2] / "protocol" / "fixtures.json").read_text()
)


def pcm_from(samples: list[int]) -> bytes:
    return b"".join(s.to_bytes(2, "little", signed=True) for s in samples)


class SharedFixtures(unittest.TestCase):
    def test_constants_match(self) -> None:
        self.assertEqual(AUDIO_HEADER_BYTES, FIXTURES["audioFrameHeaderBytes"])
        self.assertEqual(AUDIO_FRAME_KIND, FIXTURES["audioFrameKind"])

    def test_decode_fixture_frames(self) -> None:
        for f in FIXTURES["audioFrames"]:
            frame = decode_audio_frame(bytes.fromhex(f["hex"]))
            assert frame is not None
            self.assertEqual(frame.guild_id, int(f["guildId"]))
            self.assertEqual(frame.user_id, int(f["userId"]))
            self.assertEqual(frame.timestamp_ms, f["timestampMs"])
            self.assertEqual(frame.pcm, pcm_from(f["samples"]))

    def test_encode_matches_fixture(self) -> None:
        for f in FIXTURES["audioFrames"]:
            encoded = encode_audio_frame(
                int(f["guildId"]), int(f["userId"]), f["timestampMs"], pcm_from(f["samples"])
            )
            self.assertEqual(encoded.hex(), f["hex"])


class AudioFrames(unittest.TestCase):
    def test_rejects_malformed(self) -> None:
        self.assertIsNone(decode_audio_frame(b"\x01\x02"))
        wrong_kind = bytes([9]) + bytes(AUDIO_HEADER_BYTES - 1) + b"\x00\x00"
        self.assertIsNone(decode_audio_frame(wrong_kind))
        odd = bytes([AUDIO_FRAME_KIND]) + bytes(AUDIO_HEADER_BYTES - 1) + b"\x00"
        self.assertIsNone(decode_audio_frame(odd))

    def test_duration(self) -> None:
        frame = decode_audio_frame(encode_audio_frame(1, 2, 3, bytes(640)))
        assert frame is not None
        self.assertAlmostEqual(frame.duration_ms, 20.0)


class ControlMessages(unittest.TestCase):
    def test_parses_each_type(self) -> None:
        self.assertEqual(
            parse_ears_message(
                '{"type":"hello","version":2,"secret":"s","shardCount":4,"shardIds":[3,1]}'
            ),
            Hello(2, "s", 4, (1, 3)),
        )
        # An older ears (no shard fields) still parses, so core can say why it's refused.
        self.assertEqual(
            parse_ears_message('{"type":"hello","version":1,"secret":"s"}'), Hello(1, "s")
        )
        self.assertEqual(
            parse_ears_message('{"type":"status","state":"joined","guildId":"5","channelId":"6"}'),
            Status("joined", 5, 6),
        )
        # #631: one speaker's audio kept failing; whose, by ID.
        self.assertEqual(
            parse_ears_message(
                '{"type":"status","state":"warning","guildId":"5","userId":"7","detail":"x"}'
            ),
            Status("warning", 5, None, "x", 7),
        )
        # A userId means nothing on the other states.
        self.assertEqual(
            parse_ears_message('{"type":"status","state":"left","guildId":"5","userId":"7"}'),
            Status("left", 5),
        )
        self.assertEqual(
            parse_ears_message(
                '{"type":"speaking","guildId":"1","userId":"2","event":"end","timestampMs":9}'
            ),
            Speaking(1, 2, "end", 9),
        )
        self.assertEqual(
            parse_ears_message(
                '{"type":"health","guildId":"1","userId":"2",'
                '"framesReceived":48,"framesExpected":50}'
            ),
            Health(1, 2, 48, 50),
        )
        self.assertEqual(
            parse_ears_message(
                '{"type":"health","guildId":"1","userId":"2","framesReceived":30,'
                '"framesExpected":50,"decryptFailures":18,"decodeErrors":1,"linkDropped":4}'
            ),
            Health(1, 2, 30, 50, decrypt_failures=18, decode_errors=1, link_dropped=4),
        )

    def test_rejects_invalid(self) -> None:
        for raw in [
            "nope",
            "[]",
            '{"type":"hello","version":"1","secret":"s"}',
            '{"type":"hello","version":true,"secret":"s"}',
            '{"type":"hello","version":2,"secret":"s"}',
            '{"type":"hello","version":2,"secret":"s","shardCount":0,"shardIds":[0]}',
            '{"type":"hello","version":2,"secret":"s","shardCount":2,"shardIds":[]}',
            '{"type":"hello","version":2,"secret":"s","shardCount":2,"shardIds":[2]}',
            '{"type":"hello","version":2,"secret":"s","shardCount":2,"shardIds":[1,1]}',
            '{"type":"hello","version":2,"secret":"s","shardCount":2,"shardIds":["1"]}',
            '{"type":"status","state":"dancing"}',
            '{"type":"status","state":"warning","guildId":"5"}',
            '{"type":"status","state":"warning","guildId":"5","userId":"x"}',
            '{"type":"speaking","guildId":"1","userId":"x","event":"end","timestampMs":1}',
            '{"type":"speaking","guildId":"1","userId":"2","event":"later","timestampMs":1}',
            '{"type":"health","guildId":"1","userId":"2","framesReceived":-1,"framesExpected":1}',
            '{"type":"health","guildId":"1","userId":"2","framesReceived":1,"framesExpected":1,'
            '"decryptFailures":-1}',
            '{"type":"health","guildId":"1","userId":"2","framesReceived":1,"framesExpected":1,'
            '"linkDropped":"4"}',
            '{"type":"mystery"}',
        ]:
            self.assertIsNone(parse_ears_message(raw), raw)

    def test_commands_use_string_ids(self) -> None:
        self.assertEqual(
            json.loads(join_command(18446744073709551615, 2)),
            {"type": "join", "guildId": "18446744073709551615", "channelId": "2"},
        )
        self.assertEqual(json.loads(leave_command(1)), {"type": "leave", "guildId": "1"})
        self.assertEqual(
            json.loads(allowlist_command(1, {30, 4})),
            {"type": "allowlist", "guildId": "1", "userIds": ["4", "30"]},
        )


class HelloSecretHidden(unittest.TestCase):
    def test_secret_not_in_repr(self) -> None:
        self.assertNotIn("topsecret", repr(Hello(2, "topsecret")))


class HealthFixture(unittest.TestCase):
    def test_parses_what_ears_sends_with_every_count(self) -> None:
        # The same JSON ears builds in ears/test/voice.test.ts.
        parsed = parse_ears_message(json.dumps(FIXTURES["health"]["json"]))
        self.assertEqual(
            parsed, Health(111, 1001, 5, 23, decrypt_failures=4, decode_errors=1, link_dropped=2)
        )

    def test_a_null_count_is_zero_and_a_missing_one_too(self) -> None:
        raw = {**FIXTURES["health"]["json"], "decryptFailures": None}
        del raw["linkDropped"]
        self.assertEqual(
            parse_ears_message(json.dumps(raw)),
            Health(111, 1001, 5, 23, decrypt_failures=0, decode_errors=1, link_dropped=0),
        )


class HelloFixture(unittest.TestCase):
    def test_parses_what_ears_sends(self) -> None:
        h = FIXTURES["hello"]
        parsed = parse_ears_message(json.dumps(h["json"]))
        self.assertEqual(parsed, Hello(2, h["secret"], h["shardCount"], tuple(h["shardIds"])))

    def test_other_versions_parse_without_guessing_fields(self) -> None:
        self.assertEqual(
            parse_ears_message('{"type":"hello","version":3,"secret":"s","new":1}'),
            Hello(3, "s"),
        )
