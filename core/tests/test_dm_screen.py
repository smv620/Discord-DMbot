"""DM screen: channel names, permission plans, exposure checks, wording, Peek on the notice."""

import re
import unittest

import discord

from dmbot.audio.segmenter import Segmenter
from dmbot.bot import DMBot, Table
from dmbot.campaigns import CampaignStore
from dmbot.config import Settings
from dmbot.consent import ConsentStore
from dmbot.dm_screen import HideButton, PeekButton, VisibilityButton, card_view
from dmbot.dm_screen import messages as m
from dmbot.dm_screen.rules import (
    FULL,
    HIDDEN,
    READ_ONLY,
    Exposure,
    Target,
    channel_name,
    everyone,
    find_exposure,
    is_peeker,
    is_screen_name,
    merge_overwrites,
    missing_required,
    overwrite_plan,
    restrict,
    unique_channel_name,
)
from dmbot.ears.protocol import Status

GUILD, BOT, DM, PLAYER, OTHER = 1, 2, 3, 4, 5
CID = "0123456789abcdef0123456789abcdef"


def member(user_id: int) -> Target:
    return Target("member", user_id)


# ---- channel names -------------------------------------------------------------


def test_channel_name_is_lowercase_and_dashed() -> None:
    assert channel_name("Rime of the Frostmaiden") == "dm-screen-rime-of-the-frostmaiden"


def test_channel_name_drops_punctuation_and_collapses_dashes() -> None:
    assert channel_name("  Curse of Strahd: Part 2!! ") == "dm-screen-curse-of-strahd-part-2"


def test_channel_name_without_usable_letters() -> None:
    assert channel_name("!!!") == "dm-screen"


def test_channel_name_fits_discord_limit() -> None:
    name = channel_name("x" * 200)
    assert len(name) == 100
    assert not name.endswith("-")


# ---- permission plans ------------------------------------------------------------


def plan(visibility: str, peekers: tuple[int, ...] = ()) -> dict[Target, object]:
    return dict(
        overwrite_plan(visibility, guild_id=GUILD, bot_id=BOT, dm_ids=[DM], peeker_ids=peekers)
    )


def test_private_hides_from_everyone_and_drops_peekers() -> None:
    p = plan("private", peekers=(PLAYER,))
    assert p[everyone(GUILD)] == HIDDEN
    assert p[member(DM)] == FULL
    assert p[member(BOT)] == FULL
    assert member(PLAYER) not in p


def test_peek_hides_from_everyone_but_keeps_peekers_read_only() -> None:
    p = plan("peek", peekers=(PLAYER,))
    assert p[everyone(GUILD)] == HIDDEN
    assert p[member(PLAYER)] == READ_ONLY


def test_open_lets_everyone_read_but_not_post() -> None:
    p = plan("open", peekers=(PLAYER,))
    assert p[everyone(GUILD)] == READ_ONLY
    assert member(PLAYER) not in p


def test_dm_and_bot_are_never_downgraded_to_peekers() -> None:
    p = plan("peek", peekers=(DM, BOT))
    assert p[member(DM)] == FULL
    assert p[member(BOT)] == FULL


def test_peeker_overwrite_is_recognisable() -> None:
    assert is_peeker(READ_ONLY)
    assert not is_peeker(FULL)
    assert not is_peeker(HIDDEN)


# ---- exposure ------------------------------------------------------------------


def exposure(visibility: str, overwrites: dict[Target, dict[str, bool]]) -> Exposure:
    return find_exposure(visibility, overwrites, guild_id=GUILD, bot_id=BOT, dm_ids=[DM])


def test_clean_private_screen_has_no_exposure() -> None:
    ows = {everyone(GUILD): dict(HIDDEN), member(DM): dict(FULL), member(BOT): dict(FULL)}
    assert not exposure("private", ows)


def test_role_allowed_to_view_is_reported() -> None:
    ows = {everyone(GUILD): dict(HIDDEN), Target("role", 77): {"view_channel": True}}
    assert exposure("private", ows) == Exposure(roles=(77,))


def test_missing_everyone_deny_means_everyone_can_see() -> None:
    assert exposure("private", {member(DM): dict(FULL)}).everyone


def test_peekers_are_expected_under_peek_but_not_private() -> None:
    ows = {everyone(GUILD): dict(HIDDEN), member(PLAYER): dict(READ_ONLY)}
    assert not exposure("peek", ows)
    assert exposure("private", ows) == Exposure(members=(PLAYER,))


def test_member_with_full_access_who_is_not_a_dm_is_reported_under_peek() -> None:
    ows = {everyone(GUILD): dict(HIDDEN), member(OTHER): dict(FULL)}
    assert exposure("peek", ows) == Exposure(members=(OTHER,))


def test_open_screen_never_reports_exposure() -> None:
    assert not exposure("open", {Target("role", 77): {"view_channel": True}})


# ---- wording -------------------------------------------------------------------


def test_exposure_warning_names_roles_and_members() -> None:
    text = m.exposure_warning(Exposure(everyone=True, roles=(77,), members=(5,)))
    assert text is not None
    assert "everyone in the server" in text
    assert "<@&77>" in text and "<@5>" in text
    assert "Edit Channel → Permissions" in text
    assert m.exposure_warning(Exposure()) is None


def test_help_card_explains_who_can_see_and_how_to_start() -> None:
    peek = m.help_card("Frostmaiden", "peek")
    assert "Frostmaiden" in peek and "peek" in peek and "/dmbot start" in peek
    assert "never makes rulings or story" in peek
    assert m.HIDE_LABEL in peek
    assert "change who can see this" in peek
    assert "admins can always see" in m.help_card("Frostmaiden", "private")
    assert "admins" not in m.help_card("Frostmaiden", "open")


def test_topic_matches_visibility() -> None:
    assert "Only the DM" in m.topic("X", "private")
    assert "peek" in m.topic("X", "peek")
    assert "Everyone" in m.topic("X", "open")


# ---- buttons -------------------------------------------------------------------


def test_peek_and_hide_custom_ids_round_trip() -> None:
    for cls, prefix in ((PeekButton, "dmbot:peek:"), (HideButton, "dmbot:hide:")):
        button = cls(CID)
        assert button.custom_id == prefix + CID
        template = cls.__discord_ui_compiled_template__
        match = template.fullmatch(button.custom_id)
        assert match is not None and match["campaign"] == CID
        assert re.fullmatch(template, prefix + "not-a-campaign") is None


# ---- Peek button on the players' "listening" notice -----------------------------


class FakeChannel:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id


class NoticePeekButtonTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.consent = ConsentStore(":memory:")
        self.bot = DMBot(Settings(discord_token="t", ears_secret="s"), self.consent)
        self.views: dict[int, discord.ui.View | None] = {}

        async def fake_post(
            channel_id: int, text: str, view: discord.ui.View | None = None
        ) -> bool:
            self.views[channel_id] = view
            return True

        self.bot.post = fake_post  # type: ignore[method-assign]

    async def asyncTearDown(self) -> None:
        self.consent.close()
        self.bot.campaigns.close()

    async def notice_view(self, visibility: str | None) -> discord.ui.View | None:
        campaign_id = None
        if visibility is not None:
            campaign = await self.bot.campaigns.create(GUILD, "Frostmaiden", DM)
            campaign = await self.bot.campaigns.set_dm_screen_visibility(
                GUILD, campaign.id, visibility
            )
            campaign_id = campaign.id
        table = Table(GUILD, 20, 30, dm_user_id=DM, segmenter=Segmenter(GUILD))
        table.campaign_id = campaign_id
        await self.bot._on_status(table, Status("joined", guild_id=GUILD))
        return self.views.get(20)

    async def test_peek_campaign_gets_a_peek_button(self) -> None:
        view = await self.notice_view("peek")
        assert view is not None
        (item,) = view.children
        assert isinstance(item, PeekButton)
        assert item.custom_id.startswith("dmbot:peek:")

    async def test_private_campaign_gets_no_button(self) -> None:
        assert await self.notice_view("private") is None

    async def test_open_campaign_gets_no_button(self) -> None:
        assert await self.notice_view("open") is None

    async def test_switching_a_live_table_to_peek_posts_a_peek_invite(self) -> None:
        campaign = await self.bot.campaigns.create(GUILD, "Frostmaiden", DM)
        campaign = await self.bot.campaigns.set_dm_screen_visibility(GUILD, campaign.id, "peek")
        table = Table(GUILD, 20, 30, dm_user_id=DM, segmenter=Segmenter(GUILD))
        table.campaign_id = campaign.id
        self.bot.tables[GUILD] = table
        channel = FakeChannel(31)
        await self.bot.after_screen_change(campaign, channel)  # type: ignore[arg-type]
        assert table.screen_channel_id == 31  # follows a re-created screen
        view = self.views.get(20)
        assert view is not None and isinstance(view.children[0], PeekButton)

    async def test_other_campaigns_tables_are_left_alone(self) -> None:
        campaign = await self.bot.campaigns.create(GUILD, "Frostmaiden", DM)
        table = Table(GUILD, 20, 30, dm_user_id=DM, segmenter=Segmenter(GUILD))
        self.bot.tables[GUILD] = table  # not linked to any campaign
        await self.bot.after_screen_change(campaign, FakeChannel(31))  # type: ignore[arg-type]
        assert table.screen_channel_id == 30
        assert 20 not in self.views

    async def test_no_campaign_means_no_button(self) -> None:
        assert await self.notice_view(None) is None


# ---- review fixes: permissions the bot holds, names, merging -----------------------


def test_read_only_closes_side_doors() -> None:
    for side_door in (
        "send_messages_in_threads",
        "create_public_threads",
        "create_private_threads",
        "add_reactions",
        "use_application_commands",
    ):
        assert READ_ONLY[side_door] is False
    assert is_peeker(READ_ONLY)


def test_restrict_keeps_only_permissions_the_bot_holds() -> None:
    held = {"view_channel", "send_messages"}
    assert restrict(READ_ONLY, held) == {"view_channel": True, "send_messages": False}


def test_missing_required_uses_discord_names() -> None:
    assert missing_required({"view_channel", "send_messages", "read_message_history"}) == [
        "Manage Channels",
        "Manage Roles",
    ]
    assert (
        missing_required(
            {
                "view_channel",
                "send_messages",
                "read_message_history",
                "manage_channels",
                "manage_roles",
            }
        )
        == []
    )


def test_needs_permissions_names_exactly_whats_missing() -> None:
    assert "**Manage Roles**" in m.needs_permissions(["Manage Roles"])
    assert "turn it on" in m.needs_permissions(["Manage Roles"])
    assert "turn them on" in m.needs_permissions(["Manage Channels", "Manage Roles"])


def test_unique_channel_name_adds_a_number() -> None:
    assert unique_channel_name("dm-screen-x", []) == "dm-screen-x"
    assert unique_channel_name("dm-screen-x", ["dm-screen-x"]) == "dm-screen-x-2"
    assert unique_channel_name("dm-screen-x", ["dm-screen-x", "dm-screen-x-2"]) == "dm-screen-x-3"
    assert len(unique_channel_name("x" * 100, ["x" * 100])) <= 100


def test_merge_keeps_server_roles_and_drops_stray_members() -> None:
    mods = Target("role", 77)
    current = {
        everyone(GUILD): {"view_channel": True},
        mods: {"view_channel": True},
        member(OTHER): dict(FULL),  # added by hand, or a removed DM
    }
    p = overwrite_plan("private", guild_id=GUILD, bot_id=BOT, dm_ids=[DM])
    merged = merge_overwrites(current, p, guild_id=GUILD)
    assert merged[mods] == {"view_channel": True}  # the server's role overwrite stays
    assert merged[everyone(GUILD)] == HIDDEN  # @everyone follows the plan
    assert member(OTHER) not in merged
    assert merged[member(DM)] == FULL


def test_peek_warning_says_what_peeking_means() -> None:
    assert "old ones too" in m.PEEK_WARNING
    assert "Your DM will see that you peeked" in m.PEEK_WARNING
    assert "hide it again" in m.PEEK_WARNING


def test_visibility_changed_says_who_can_see_now() -> None:
    assert m.WHO_CAN_SEE["open"] in m.visibility_changed("open", was="private")


def test_switching_peek_to_private_says_peekers_lost_access() -> None:
    assert "can't see it anymore" in m.visibility_changed("private", was="peek")
    assert "can't see it anymore" not in m.visibility_changed("private", was="open")


def test_only_dm_screen_names_count_as_screens() -> None:
    assert is_screen_name("dm-screen")
    assert is_screen_name("dm-screen-frostmaiden")
    assert not is_screen_name("general")
    assert not is_screen_name("dm-screenshots")


def test_visibility_button_custom_ids_round_trip() -> None:
    for visibility in ("private", "peek", "open"):
        button = VisibilityButton(CID, visibility)
        assert button.custom_id == f"dmbot:vis:{CID}:{visibility}"
        match = VisibilityButton.__discord_ui_compiled_template__.fullmatch(button.custom_id)
        assert match is not None and match["visibility"] == visibility
    template = VisibilityButton.__discord_ui_compiled_template__
    assert template.fullmatch(f"dmbot:vis:{CID}:everyone") is None


class CardViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_card_view_marks_current_setting_and_adds_hide_only_for_peek(self) -> None:
        await card_view_check()


async def card_view_check() -> None:
    store = CampaignStore(":memory:")
    try:
        campaign = await store.create(GUILD, "Frostmaiden", DM)  # default: peek
        view = card_view(campaign)
        buttons = [i for i in view.children if isinstance(i, VisibilityButton)]
        assert [b.visibility for b in buttons] == ["private", "peek", "open"]
        assert [b.item.disabled for b in buttons] == [False, True, False]
        assert any(isinstance(i, HideButton) for i in view.children)
        private = await store.set_dm_screen_visibility(GUILD, campaign.id, "private")
        assert not any(isinstance(i, HideButton) for i in card_view(private).children)
    finally:
        store.close()


def test_open_is_described_as_the_whole_server() -> None:
    assert "server" in m.WHO_CAN_SEE["open"]
    assert "server" in m.topic("X", "open")
