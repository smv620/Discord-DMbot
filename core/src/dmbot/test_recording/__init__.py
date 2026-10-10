"""Test recordings (#1019; docs/PLAN.md, "Test recordings"): the one place DMbot keeps audio.

On a test server only (`DMBOT_TEST_RECORDING_GUILDS`), and only for people who pressed a
separate "Save my voice for tests" button, each utterance DMbot heard is kept as a FLAC file
with a `session.json` that says when each was said, by a made-up speaker, and what DMbot
made of it. A successful session can then be replayed to re-check a new feature without a
table. The files live in one folder (`DMBOT_TEST_RECORDINGS_DIR`), never in the database,
a backup or the repository, and are deleted after 7 days unless someone marks one to keep.
"""
