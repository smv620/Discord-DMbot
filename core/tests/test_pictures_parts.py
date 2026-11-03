"""The picture files of a backup (#3274): how pictures are grouped into parts, that a part reads
back exactly, and that a cut, padded, repeated or forged part is refused. Pure; no database."""

from __future__ import annotations

import unittest

from dmbot.campaigns.models import CampaignError
from dmbot.pictures import parts
from dmbot.pictures.parts import PartItem

ID = "a" * 32
R1, R2 = "1" * 64, "2" * 64


class Grouping(unittest.TestCase):
    def test_pictures_fill_a_part_in_order_and_are_never_split(self) -> None:
        self.assertEqual(parts.groups([5, 5, 5, 5], 10), [[0, 1], [2, 3]])
        self.assertEqual(parts.groups([6, 6, 6], 10), [[0], [1], [2]])

    def test_a_picture_bigger_than_a_part_gets_one_to_itself(self) -> None:
        self.assertEqual(parts.groups([1, 50, 1], 10), [[0], [1], [2]])

    def test_nothing_makes_no_parts(self) -> None:
        self.assertEqual(parts.groups([]), [])


class RoundTrip(unittest.TestCase):
    def test_a_part_reads_back_exactly(self) -> None:
        items = [PartItem(R1, b"first"), PartItem(R2, b"\x00\x01second\xff")]
        got = parts.decode_part(parts.encode_part(ID, 3, items))
        self.assertEqual((got.backup, got.number), (ID, 3))
        self.assertEqual(got.items, items)

    def test_an_empty_part_is_fine(self) -> None:
        self.assertEqual(parts.decode_part(parts.encode_part(ID, 1, [])).items, [])


class Refusing(unittest.TestCase):
    def refused(self, raw: bytes) -> None:
        with self.assertRaises(CampaignError):
            parts.decode_part(raw)

    def good(self) -> bytes:
        return parts.encode_part(ID, 1, [PartItem(R1, b"abc"), PartItem(R2, b"defg")])

    def test_not_a_part(self) -> None:
        self.refused(b"")
        self.refused(b"hello")
        self.refused(b"\x1f\x8b" + b"0" * 50)  # (a transcripts file is gzip: not this)

    def test_cut_short(self) -> None:
        raw = self.good()
        for cut in (1, 3, len(raw) - 1, len(raw) - 5):
            self.refused(raw[:-cut])

    def test_extra_bytes_after_the_last_picture(self) -> None:
        self.refused(self.good() + b"x")

    def test_a_repeated_place_is_refused(self) -> None:
        self.refused(parts.encode_part(ID, 1, [PartItem(R1, b"a"), PartItem(R1, b"b")]))

    def test_a_reference_that_is_not_a_hash_is_refused(self) -> None:
        for ref in ("0", "g" * 64, "1" * 63, "A" * 64):
            self.refused(parts.encode_part(ID, 1, [PartItem(ref, b"a")]))

    def test_a_bad_header_is_refused(self) -> None:
        raw = self.good()
        for old, new in (
            (b'"dmbot-pictures"', b'"other"'),
            (b'"part": 1', b'"part": 0'),
            (b'"version": 1', b'"version": 2'),
            (b'"count": 2', b'"count": 9'),
        ):
            self.refused(raw.replace(old, new, 1))

    def test_a_size_that_lies_is_refused(self) -> None:
        self.refused(self.good().replace(b'"size": 3', b'"size": 9', 1))

    def test_too_big_is_refused_before_reading(self) -> None:
        with self.assertRaises(CampaignError) as raised:
            parts.decode_part(b"x" * (parts.PART_READ_BYTES + 1))
        self.assertEqual(str(raised.exception), parts.TOO_BIG)
