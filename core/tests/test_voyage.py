"""Voyage AI's two calls (#2664): batching, the request shapes, replies that cannot be read, and
that no error ever holds the text sent. A fake transport; nothing connects."""

from __future__ import annotations

import json
import unittest
from collections.abc import Mapping
from typing import Any

from dmbot.memory import voyage as v


class Fake:
    """Answers each call from a list of (status, reply) pairs and keeps what was sent."""

    def __init__(self, *replies: tuple[int, Any]) -> None:
        self.replies = list(replies)
        self.sent: list[tuple[str, str, Mapping[str, Any] | None]] = []

    async def __call__(
        self, method: str, path: str, body: Mapping[str, Any] | None
    ) -> tuple[int, bytes]:
        self.sent.append((method, path, body))
        status, reply = self.replies.pop(0)
        return status, reply if isinstance(reply, bytes) else json.dumps(reply).encode()


def client(fake: Fake) -> v.Voyage:
    return v.Voyage(fake, embed_model="voyage-3.5", rerank_model="rerank-2.5")


def vectors(n: int) -> dict[str, Any]:
    return {
        "data": [{"index": i, "embedding": [float(i), 1.0]} for i in range(n)],
        "usage": {"total_tokens": 7 * n},
    }


class Embedding(unittest.IsolatedAsyncioTestCase):
    async def test_one_vector_for_each_text_in_order_with_the_model_and_kind(self) -> None:
        fake = Fake((200, vectors(2)))
        got = await client(fake).embed(["a", "b"])
        self.assertEqual(got, [[0.0, 1.0], [1.0, 1.0]])
        (_, path, body) = fake.sent[0]
        self.assertEqual(path, "/v1/embeddings")
        assert body is not None
        self.assertEqual(
            (body["input"], body["model"], body["input_type"]),
            (["a", "b"], "voyage-3.5", "document"),
        )

    async def test_a_search_text_is_sent_as_a_query(self) -> None:
        fake = Fake((200, vectors(1)))
        await client(fake).embed(["who betrayed us"], query=True)
        assert fake.sent[0][2] is not None
        self.assertEqual(fake.sent[0][2]["input_type"], "query")

    async def test_replies_out_of_order_are_put_back_in_order(self) -> None:
        reply = {"data": [{"index": 1, "embedding": [2.0]}, {"index": 0, "embedding": [1.0]}]}
        got = await client(Fake((200, reply))).embed(["a", "b"])
        self.assertEqual(got, [[1.0], [2.0]])

    async def test_nothing_is_sent_for_no_text(self) -> None:
        fake = Fake()
        self.assertEqual(await client(fake).embed([]), [])
        self.assertEqual(fake.sent, [])

    async def test_many_texts_go_in_batches_and_long_ones_are_cut(self) -> None:
        fake = Fake((200, vectors(v.BATCH)), (200, vectors(3)))
        c = client(fake)
        texts = ["x" * (v.MAX_CHARS + 50)] + ["y"] * (v.BATCH + 2)
        got = await c.embed(texts)
        self.assertEqual(len(got), v.BATCH + 3)
        assert fake.sent[0][2] is not None
        self.assertEqual(len(fake.sent[0][2]["input"][0]), v.MAX_CHARS)
        self.assertEqual([len(b[2]["input"]) for b in fake.sent if b[2]], [v.BATCH, 3])
        self.assertEqual(c.tokens, 7 * v.BATCH + 21)

    async def test_a_reply_that_cannot_be_read_is_a_reply_error(self) -> None:
        bad: list[Any] = [
            b"not json",
            [],
            {"data": "no"},
            {"data": [{"index": 0, "embedding": [1.0]}]},  # one short
            {"data": [{"index": 0, "embedding": [1.0]}, {"index": 0, "embedding": [1.0]}]},
            {"data": [{"index": 0, "embedding": []}, {"index": 1, "embedding": [1.0]}]},
            {"data": [{"index": 0, "embedding": ["x"]}, {"index": 1, "embedding": [1.0]}]},
            {"data": [{"index": 5, "embedding": [1.0]}, {"index": 1, "embedding": [1.0]}]},
            {"data": [1, 2]},
            {"data": [None, None]},
            {"data": [{"index": 0, "embedding": [True]}, {"index": 1, "embedding": [1.0]}]},
            b'{"data": [{"index": 0, "embedding": [NaN]}, {"index": 1, "embedding": [1.0]}]}',
        ]
        for reply in bad:
            with self.subTest(reply=reply), self.assertRaises(v.ProviderError) as caught:
                await client(Fake((200, reply))).embed(["a", "b"])
            self.assertEqual(caught.exception.kind, "reply")

    async def test_a_failed_call_says_how_and_never_what_was_sent(self) -> None:
        for status, kind, permanent in (
            (401, "auth", True),
            (402, "plan", True),
            (400, "refused", True),
            (429, "busy", False),
            (503, "busy", False),
        ):
            with self.subTest(status=status), self.assertRaises(v.ProviderError) as caught:
                await client(Fake((status, b"the dragon text"))).embed(["the dragon text"])
            self.assertEqual((caught.exception.kind, caught.exception.permanent), (kind, permanent))
            self.assertNotIn("dragon", caught.exception.describe() + str(caught.exception))
            self.assertIn(str(status), caught.exception.describe())


class Reranking(unittest.IsolatedAsyncioTestCase):
    async def test_the_best_matches_come_back_best_first(self) -> None:
        reply = {
            "data": [
                {"index": 2, "relevance_score": 0.4},
                {"index": 0, "relevance_score": 0.9},
            ]
        }
        fake = Fake((200, reply))
        got = await client(fake).rerank("q", ["a", "b", "c"], top_k=2)
        self.assertEqual(got, [v.Ranked(0, 0.9), v.Ranked(2, 0.4)])
        (_, path, body) = fake.sent[0]
        assert body is not None
        self.assertEqual(path, "/v1/rerank")
        self.assertEqual(
            (body["query"], body["documents"], body["model"], body["top_k"]),
            ("q", ["a", "b", "c"], "rerank-2.5", 2),
        )

    async def test_nothing_is_sent_for_no_documents_or_no_room(self) -> None:
        fake = Fake()
        self.assertEqual(await client(fake).rerank("q", [], top_k=3), [])
        self.assertEqual(await client(fake).rerank("q", ["a"], top_k=0), [])
        self.assertEqual(fake.sent, [])

    async def test_a_position_outside_what_was_sent_is_a_reply_error(self) -> None:
        reply = {"data": [{"index": 4, "relevance_score": 0.5}]}
        with self.assertRaises(v.ProviderError):
            await client(Fake((200, reply))).rerank("q", ["a", "b"], top_k=1)

    async def test_rows_and_scores_that_cannot_be_read_are_reply_errors(self) -> None:
        for reply in (
            {"data": [1]},
            {"data": [{"index": 0, "relevance_score": True}]},
            b'{"data": [{"index": 0, "relevance_score": Infinity}]}',
        ):
            with self.subTest(reply=reply), self.assertRaises(v.ProviderError):
                await client(Fake((200, reply))).rerank("q", ["a"], top_k=1)

    async def test_a_failed_call_is_a_provider_error(self) -> None:
        with self.assertRaises(v.ProviderError) as caught:
            await client(Fake((500, b""))).rerank("q", ["a"], top_k=1)
        self.assertEqual(caught.exception.kind, "busy")


class Closeness(unittest.TestCase):
    def test_cosine_of_same_unrelated_and_opposite_directions(self) -> None:
        self.assertAlmostEqual(v.cosine([1, 0], [2, 0]), 1.0)
        self.assertAlmostEqual(v.cosine([1, 0], [0, 3]), 0.0)
        self.assertAlmostEqual(v.cosine([1, 0], [-1, 0]), -1.0)
        self.assertEqual(v.cosine([0, 0], [1, 1]), 0.0)


if __name__ == "__main__":
    unittest.main()
