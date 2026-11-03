"""A campaign's kept pictures in the extra files of its backup (#3274).

The backup's main file lists the pictures (rows, JSON). The picture files themselves are far too big
for it, so they travel in numbered files of about 20 MB each, next to the transcripts files. Each
file holds whole pictures and is read on its own, so a restore's memory stays bounded.

Pictures are already compressed, so a part is not gzipped. Layout: the magic line, one JSON header
line (`format`, `version`, `backup`, `part`, `count`), then for each picture a 4-byte length, its
JSON header (`ref`, `size`) and its bytes. `ref` is the picture's `sha256` as the main file lists
it (the hash of the bytes first posted, unique within a campaign), so a file never names a
database id and a part can be matched to its picture whenever it arrives.

A part carries only the picture file. The preview, the metadata stripping and every size check are
done again by `prepare` on restore, so a forged part can write nothing the bot would not accept from
a Discord post, and nothing in a part is trusted for its size or kind.

Pure: no database and no bucket here.
"""

from __future__ import annotations

import json
import re
import struct
from collections.abc import Sequence
from dataclasses import dataclass

from dmbot.campaigns.models import CampaignError

PART_FORMAT = "dmbot-pictures"
PART_VERSION = 1
MAGIC = b"DMBPICS1\n"
PART_TARGET_BYTES = (
    20 * 1024 * 1024
)  # a part is filled to about this, and may pass it by one picture
PART_READ_BYTES = 40 * 1024 * 1024  # the most a restore reads from one part
PICTURE_READ_BYTES = 26 * 1024 * 1024  # the most one picture may be (prepare allows 25 MB)
PICTURES_MAX = 5000  # pictures in one part
HEADER_MAX = 1024  # bytes of one picture's header

_REF = re.compile(r"[0-9a-f]{64}")
DAMAGED = "This pictures file is damaged. Use the files DMbot made, all of them."
TOO_BIG = "This pictures file is too big for DMbot to read."


@dataclass(frozen=True, slots=True)
class PartItem:
    ref: str  # the picture's `sha256` in the main file's list
    data: bytes


@dataclass(frozen=True, slots=True)
class Part:
    backup: str
    number: int
    items: list[PartItem]


def groups(sizes: Sequence[int], target: int = PART_TARGET_BYTES) -> list[list[int]]:
    """Which pictures (by place) go in which part, in order. A picture is never split; a part is
    closed once adding the next would pass `target`, and a lone big picture gets a part of its
    own."""
    out: list[list[int]] = []
    current: list[int] = []
    used = 0
    for place, size in enumerate(sizes):
        if current and used + size > target:
            out.append(current)
            current, used = [], 0
        current.append(place)
        used += size
    if current:
        out.append(current)
    return out


def encode_part(backup_id: str, number: int, items: Sequence[PartItem]) -> bytes:
    head = json.dumps(
        {
            "format": PART_FORMAT,
            "version": PART_VERSION,
            "backup": backup_id,
            "part": number,
            "count": len(items),
        }
    ).encode()
    out = bytearray(MAGIC)
    out += head + b"\n"
    for item in items:
        header = json.dumps({"ref": item.ref, "size": len(item.data)}).encode()
        out += struct.pack(">I", len(header)) + header + item.data
    return bytes(out)


def decode_part(raw: bytes) -> Part:
    """Read and check one part, refusing a big, cut or padded one. Pure and CPU-light, but run it
    off the event loop for a big file."""
    if len(raw) > PART_READ_BYTES:
        raise CampaignError(TOO_BIG)
    if not raw.startswith(MAGIC):
        raise CampaignError(DAMAGED)
    end = raw.find(b"\n", len(MAGIC))
    if end < 0 or end - len(MAGIC) > HEADER_MAX:
        raise CampaignError(DAMAGED)
    try:
        head = json.loads(raw[len(MAGIC) : end])
    except ValueError as exc:
        raise CampaignError(DAMAGED) from exc
    if not isinstance(head, dict) or head.get("format") != PART_FORMAT:
        raise CampaignError(DAMAGED)
    version, backup, number, count = (head.get(k) for k in ("version", "backup", "part", "count"))
    if (
        type(version) is not int
        or not 1 <= version <= PART_VERSION
        or not isinstance(backup, str)
        or not 8 <= len(backup) <= 64
        or type(number) is not int
        or number < 1
        or type(count) is not int
        or not 0 <= count <= PICTURES_MAX
    ):
        raise CampaignError(DAMAGED)
    at = end + 1
    items: list[PartItem] = []
    seen: set[str] = set()
    for _ in range(count):
        if at + 4 > len(raw):
            raise CampaignError(DAMAGED)
        (size,) = struct.unpack(">I", raw[at : at + 4])
        at += 4
        if size > HEADER_MAX or at + size > len(raw):
            raise CampaignError(DAMAGED)
        try:
            header = json.loads(raw[at : at + size])
        except ValueError as exc:
            raise CampaignError(DAMAGED) from exc
        at += size
        ref, length = (
            (header.get(k) for k in ("ref", "size")) if isinstance(header, dict) else (None, None)
        )
        if (
            not isinstance(ref, str)
            or not _REF.fullmatch(ref)
            or ref in seen
            or type(length) is not int
            or not 0 < length <= PICTURE_READ_BYTES
            or at + length > len(raw)
        ):
            raise CampaignError(DAMAGED)
        seen.add(ref)
        items.append(PartItem(ref, raw[at : at + length]))
        at += length
    if at != len(raw):  # more bytes after the last picture
        raise CampaignError(DAMAGED)
    return Part(backup, number, items)
