"""Discord sign-in for the website (OAuth2 code flow, #435).

Scopes are `identify email guilds` and nothing more (least privilege, CLAUDE.md). The
access token is used once, during sign-in, to read who the person is and which servers
they're in; then it's revoked. It is never stored, logged or sent to the browser.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlencode

import aiohttp

SCOPES = "identify email guilds"
# Adding DMbot to a server through the website: the bot and its commands, plus who did it.
INSTALL_SCOPES = "bot applications.commands identify"
API = "https://discord.com/api/v10"
AUTHORIZE_URL = "https://discord.com/oauth2/authorize"

# Discord permission bits that let someone add a bot to a server.
_MANAGE_GUILD = 1 << 5
_ADMINISTRATOR = 1 << 3


class DiscordError(RuntimeError):
    """Discord refused or didn't answer. The message has no token or personal data."""


@dataclass(frozen=True)
class DiscordUser:
    id: int
    name: str
    email: str | None


@dataclass(frozen=True)
class DiscordGuild:
    id: int
    name: str
    manage: bool  # can add DMbot (owner, Manage Server or Administrator)


def guild_from_json(raw: dict[str, Any]) -> DiscordGuild:
    permissions = int(raw.get("permissions", "0"))
    manage = bool(raw.get("owner")) or bool(permissions & (_MANAGE_GUILD | _ADMINISTRATOR))
    return DiscordGuild(id=int(raw["id"]), name=str(raw.get("name", "")), manage=manage)


def user_from_json(raw: dict[str, Any]) -> DiscordUser:
    name = raw.get("global_name") or raw.get("username") or ""
    email = raw.get("email") if raw.get("verified") else None
    return DiscordUser(id=int(raw["id"]), name=str(name), email=email)


class DiscordOAuth(Protocol):
    def authorize_url(self, state: str, redirect_uri: str) -> str: ...

    async def exchange(self, code: str, redirect_uri: str) -> str:
        """The access token for this sign-in code."""
        ...

    async def user(self, token: str) -> DiscordUser: ...

    def install_url(self, state: str, redirect_uri: str, guild_id: int, permissions: int) -> str:
        """Discord's page to add DMbot to this one server."""
        ...

    async def exchange_install(self, code: str, redirect_uri: str) -> tuple[str, int | None]:
        """The access token and the server Discord says DMbot was added to."""
        ...

    async def guilds(self, token: str) -> list[DiscordGuild]: ...

    async def revoke(self, token: str) -> None: ...


class HttpDiscord:
    """The real Discord, over HTTPS."""

    def __init__(self, client_id: str, client_secret: str, *, api: str = API) -> None:
        self._client_id = client_id
        # The app's own sign-in for Discord's token calls (client id and secret).
        self._app_auth = {"Authorization": aiohttp.encode_basic_auth(client_id, client_secret)}
        self._api = api.rstrip("/")
        self._http: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._http is not None:
            await self._http.close()
            self._http = None

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        query = urlencode(
            {
                "client_id": self._client_id,
                "response_type": "code",
                "scope": SCOPES,
                "redirect_uri": redirect_uri,
                "state": state,
                "prompt": "none",
            }
        )
        return f"{AUTHORIZE_URL}?{query}"

    def install_url(self, state: str, redirect_uri: str, guild_id: int, permissions: int) -> str:
        query = urlencode(
            {
                "client_id": self._client_id,
                "response_type": "code",
                "scope": INSTALL_SCOPES,
                "permissions": str(permissions),
                "guild_id": str(guild_id),
                "disable_guild_select": "true",
                "redirect_uri": redirect_uri,
                "state": state,
            }
        )
        return f"{AUTHORIZE_URL}?{query}"

    async def exchange_install(self, code: str, redirect_uri: str) -> tuple[str, int | None]:
        data = await self._request(
            "POST",
            f"{self._api}/oauth2/token",
            data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
            headers=self._app_auth,
        )
        if not isinstance(data, dict) or not isinstance(data.get("access_token"), str):
            raise DiscordError("Discord's token answer had no access token")
        guild = data.get("guild")
        guild_id = int(guild["id"]) if isinstance(guild, dict) and "id" in guild else None
        return str(data["access_token"]), guild_id

    async def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        if self._http is None:  # one connection pool for every sign-in
            self._http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        try:
            async with self._http.request(method, url, **kwargs) as response:
                if response.status >= 400:
                    raise DiscordError(f"Discord answered {response.status} to {url}")
                if response.content_type == "application/json":
                    return await response.json()
                return None
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise DiscordError(f"Couldn't reach Discord ({type(exc).__name__})") from exc

    async def exchange(self, code: str, redirect_uri: str) -> str:
        data = await self._request(
            "POST",
            f"{self._api}/oauth2/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers=self._app_auth,
        )
        if not isinstance(data, dict) or not isinstance(data.get("access_token"), str):
            raise DiscordError("Discord's token answer had no access token")
        granted = set(str(data.get("scope", "")).split())
        if not set(SCOPES.split()) <= granted:
            raise DiscordError("Discord granted fewer scopes than DMbot needs")
        return str(data["access_token"])

    async def user(self, token: str) -> DiscordUser:
        raw = await self._request(
            "GET", f"{self._api}/users/@me", headers={"Authorization": f"Bearer {token}"}
        )
        return user_from_json(raw)

    async def guilds(self, token: str) -> list[DiscordGuild]:
        raw = await self._request(
            "GET", f"{self._api}/users/@me/guilds", headers={"Authorization": f"Bearer {token}"}
        )
        return [guild_from_json(g) for g in raw or []]

    async def revoke(self, token: str) -> None:
        await self._request(
            "POST",
            f"{self._api}/oauth2/token/revoke",
            data={"token": token, "token_type_hint": "access_token"},
            headers=self._app_auth,
        )
