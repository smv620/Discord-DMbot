import discord

from dmbot.channel_access import (
    PostProblem,
    join_blocked_message,
    missing_post_permissions,
    notice_failed_message,
)


def perms(**flags: bool) -> discord.Permissions:
    return discord.Permissions(**flags)


def test_nothing_missing_when_bot_can_view_and_send() -> None:
    assert missing_post_permissions(perms(view_channel=True, send_messages=True)) == []


def test_reports_both_when_channel_is_hidden() -> None:
    assert missing_post_permissions(perms()) == ["View Channel", "Send Messages"]


def test_reports_send_only_when_bot_can_view() -> None:
    assert missing_post_permissions(perms(view_channel=True)) == ["Send Messages"]


def test_threads_need_send_messages_in_threads() -> None:
    p = perms(view_channel=True, send_messages=True)
    assert missing_post_permissions(p, in_thread=True) == ["Send Messages in Threads"]
    p = perms(view_channel=True, send_messages_in_threads=True)
    assert missing_post_permissions(p, in_thread=True) == []


def test_blocked_message_names_each_channel_and_permission() -> None:
    text = join_blocked_message(
        [
            PostProblem(1, ("View Channel", "Send Messages"), "your DM updates go here."),
            PostProblem(2, ("Send Messages",), "players need to see the recording notice."),
        ]
    )
    assert "<#1> needs **View Channel** and **Send Messages**" in text
    assert "<#2> needs **Send Messages**" in text
    assert "run `/table join` again" in text


def test_notice_failed_message_tells_dm_what_to_do() -> None:
    text = notice_failed_message(42)
    assert "<#42>" in text
    assert "Send Messages" in text
