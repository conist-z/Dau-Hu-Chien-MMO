"""Discord OAuth2 verification for the web client ("Đăng nhập bằng Discord").

The browser runs the OAuth2 authorization-code flow with PKCE against
Discord using the BOT's own application (client_id is public in the web
client; the secret stays in the panel env). It POSTs the authorization code
here; we exchange it for an access token and read /users/@me to learn the
user's real Discord id — the SAME id the Discord client uses, so one shared
Player/inventory serves both clients.

Uses aiohttp (already a dependency) — never blocking network I/O (rule 21).
"""
from __future__ import annotations

from typing import Optional

import aiohttp

from config import DISCORD_OAUTH_CLIENT_ID, DISCORD_OAUTH_CLIENT_SECRET

DISCORD_API = "https://discord.com/api/v10"
DISCORD_TOKEN_URL = f"{DISCORD_API}/oauth2/token"
DISCORD_ME_URL = f"{DISCORD_API}/users/@me"


class OAuthError(Exception):
    """Raised for any failed token exchange or profile fetch."""


async def exchange_code(code: str, redirect_uri: str, code_verifier: str = "") -> dict:
    """Exchange an OAuth2 authorization code for Discord's token payload.

    The browser authorizes with PKCE (code_challenge), so the exchange MUST
    echo the code_verifier — Discord rejects the grant with 400 otherwise.

    Raises OAuthError with a short machine-readable reason on failure.
    """
    if not DISCORD_OAUTH_CLIENT_ID or not DISCORD_OAUTH_CLIENT_SECRET:
        raise OAuthError("oauth_not_configured")
    data = {
        "client_id": DISCORD_OAUTH_CLIENT_ID,
        "client_secret": DISCORD_OAUTH_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    if code_verifier:
        data["code_verifier"] = code_verifier
    try:
        async with aiohttp.ClientSession() as http:
            async with http.post(DISCORD_TOKEN_URL, data=data) as resp:
                if resp.status != 200:
                    raise OAuthError(f"token_exchange_{resp.status}")
                return await resp.json()
    except aiohttp.ClientError as e:
        raise OAuthError(f"network_{type(e).__name__}") from e


async def fetch_discord_user(access_token: str) -> dict:
    """The verified Discord profile: {id, username, global_name, avatar, ...}."""
    try:
        async with aiohttp.ClientSession() as http:
            async with http.get(
                DISCORD_ME_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            ) as resp:
                if resp.status != 200:
                    raise OAuthError(f"profile_{resp.status}")
                return await resp.json()
    except aiohttp.ClientError as e:
        raise OAuthError(f"network_{type(e).__name__}") from e


async def verify_code(code: str, redirect_uri: str, code_verifier: str = "") -> Optional[dict]:
    """Full verification: code -> {user_id, username, display_name, avatar}.

    Returns None-shaped raises only; callers map OAuthError to a WS error.
    """
    token_payload = await exchange_code(code, redirect_uri, code_verifier)
    access_token = token_payload.get("access_token")
    if not access_token:
        raise OAuthError("no_access_token")
    profile = await fetch_discord_user(access_token)
    raw_id = profile.get("id")
    if raw_id is None or not str(raw_id).isdigit():
        raise OAuthError("bad_profile")
    return {
        "user_id": int(raw_id),
        "username": profile.get("username", ""),
        "display_name": profile.get("global_name") or profile.get("username", ""),
        "avatar_hash": profile.get("avatar"),
    }
