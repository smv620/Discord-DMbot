import discord

from dmbot.channel_access import (
    PostProblem,
    join_blocked_message,
    missing_post_permissions,
    notice_failed_message,
    post_problems,
)

CAN_POST = discord.Permissions(view_channel=True, send_messages=True)


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


def check(
    screen: discord.Permissions | None = CAN_POST,
    voice: discord.Permissions = CAN_POST,
    *,
    in_thread: bool = False,
) -> list[PostProblem]:
    return post_problems(
        screen_id=1,
        screen_perms=screen,
        screen_in_thread=in_thread,
        voice_id=2,
        voice_perms=voice,
    )


def test_no_problems_when_both_channels_are_postable() -> None:
    assert check() == []


def test_reports_both_channels_together() -> None:
    problems = check(screen=perms(), voice=perms(view_channel=True))
    assert [(p.channel_id, p.missing) for p in problems] == [
        (1, ("View Channel", "Send Messages")),
        (2, ("Send Messages",)),
    ]


def test_screen_thread_is_checked_as_a_thread() -> None:
    (problem,) = check(screen=CAN_POST, in_thread=True)
    assert problem.missing == ("Send Messages in Threads",)
    assert problem.in_thread


def test_unseen_screen_channel_is_its_own_problem() -> None:
    (problem,) = check(screen=None)
    assert problem.unseen
    assert problem.missing == ()


def test_blocked_message_names_each_channel_and_permission() -> None:
    text = join_blocked_message(
        [
            PostProblem(1, ("View Channel", "Send Messages"), "updates go here"),
            PostProblem(2, ("Send Messages",), "notice goes here"),
        ]
    )
    assert text.startswith("⚠️ Not joining")
    assert "<#1> needs **View Channel** and **Send Messages** (updates go here)" in text
    assert "<#2> needs **Send Messages** (notice goes here)" in text
    assert "Add them via Edit Channel → Permissions" in text
    assert "run `/dmbot start` again" in text


def test_blocked_message_says_it_for_a_single_permission() -> None:
    text = join_blocked_message([PostProblem(2, ("Send Messages",), "notice goes here")])
    assert "Add it via Edit Channel → Permissions" in text


def test_thread_problem_points_to_the_parent_channel() -> None:
    text = join_blocked_message(
        [PostProblem(3, ("Send Messages in Threads",), "updates go here", in_thread=True)]
    )
    assert "set on its parent channel" in text


def test_unseen_channel_suggests_view_or_another_channel() -> None:
    text = join_blocked_message([PostProblem(4, (), "", unseen=True)])
    assert "I can't see <#4>" in text
    assert "another private channel" in text
    assert "Edit Channel" not in text  # nothing listed to add


def test_notice_failed_message_says_tell_the_table_first() -> None:
    text = notice_failed_message(42)
    assert "<#42>" in text
    assert text.index("Tell the table now") < text.index("Send Messages")
    assert "retry on the next `/dmbot start`" in text
