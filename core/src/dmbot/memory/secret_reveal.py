"""The DM said a secret name at the table (#2088, docs/PLAN.md, "When a secret is revealed"): find
which one, once for each name in a session, so DMbot can ask at once "Is it revealed now?".

This is the pure finding, with no database, no Discord and no AI. A secret name is looked for by
the one whole-name matcher (`dmbot.secrets_match`), so "the brotherhood" is not the secret
"Arcane Brotherhood". Names that share a spelling are one question (`spelling_key`), and each is
asked about once in a session: whether the DM said yes or no, or never answered.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field

from dmbot.memory.models import spelling_key
from dmbot.memory.secret_aloud import SecretName
from dmbot.secrets_match import names_a_secret

READ_EVERY_S = 60.0  # how long a session trusts the secret names it read (they change rarely)


@dataclass(slots=True)
class RevealSession:
    """What one session remembers: the spellings it has asked about, and the secret names last
    read. Kept in memory for the session and never stored (a restart starts it fresh)."""

    asked: set[str] = field(default_factory=set)
    told_waiting: bool = False  # the "question waiting" line went up already this session
    names: list[SecretName] | None = None
    read_at: float | None = None

    def fresh(self, now: float) -> bool:
        return (
            self.names is not None
            and self.read_at is not None
            and (now - self.read_at < READ_EVERY_S)
        )


@dataclass(frozen=True, slots=True)
class Revealed:
    key: str  # the spelling every entry shares
    alias_id: str  # the first entry with this spelling: what the buttons carry
    text: str
    entries: int  # how many entries share it (one answer covers them all, #1429)


def revealed_in(
    line: str,
    secrets: Sequence[SecretName],
    allowed_names: Collection[str],
    session: RevealSession,
) -> Revealed | None:
    """The first secret name this line says that the session has not asked about, or None. The
    name found is claimed at once (so two lines said together ask once)."""
    groups: dict[str, list[SecretName]] = {}
    for secret in secrets:
        groups.setdefault(spelling_key(secret.text), []).append(secret)
    for key, members in groups.items():
        if not key or key in session.asked:
            continue
        first = members[0]
        if names_a_secret(line, [first.text], allowed_names):
            session.asked.add(key)
            return Revealed(key, first.alias_id, first.text, len(members))
    return None
