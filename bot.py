import os
import asyncio
import sqlite3
import threading
import base64
import json
import re
import random
import logging
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone
import urllib.error
import urllib.parse
import urllib.request

import discord
from discord import app_commands
import uwuify
from discord.ext import tasks

# Keep the terminal quiet; the only intentional terminal output is "Bot is alive".
logging.disable(logging.CRITICAL)

# =========================
# CONFIG
# =========================

# Main server id
MAIN_SERVER = 551166126596554775

# First role to assign in the main server
TAG_ROLE_ID = 1534715148676370462

# Second role to assign in the main server
TAG_ROLE_ID_2 = 1551382001221632010

# Role ID allowed to use the second-role blacklist slash commands.
# The user must have this role in the MAIN_SERVER.
BLACKLIST_ALLOWED_ROLE_ID = 1306082718060384399  # <-- put the whitelisted role ID here

# File used to save the second-role blacklist.
# This survives bot restarts.
BLACKLIST_FILE = Path(__file__).with_name("second_role_blacklist.json")

# Combined persistent blacklist for both UWUIFY and HOODIFY targets.
# A member on this list cannot be selected for either mode.
TEXTIFY_BLACKLIST_FILE = Path(__file__).with_name("textify_blacklist.json")
# Backward-compatible aliases used by the existing mode logic.
UWU_USER_BLACKLIST_FILE = TEXTIFY_BLACKLIST_FILE
HOOD_USER_BLACKLIST_FILE = TEXTIFY_BLACKLIST_FILE
TEXTIFY_BAN_FILE = Path(__file__).with_name("textify_ban.json")

# GitHub persistence for the UWUIFY / HOODIFY user-ID blacklists.
# GITHUB_TOKEN is stored securely in Railway. The repository and branch can
# also be overridden with Railway variables, but default to this bot repo.
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO", "uhhreurheurh/john-the-bot")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")
GITHUB_API_BASE = "https://api.github.com"

# =========================
# REVIEW DATABASE CONFIG
# =========================
GITHUB_DB_PATH = os.getenv("GITHUB_DB_PATH", "reviews.db")
# Keep review database commits off Railway's deployment branch.
GITHUB_DB_BRANCH = os.getenv("GITHUB_DB_BRANCH", "database")
REVIEW_DB_SYNC_MINUTES = 30
REVIEW_DB_SAVE_ROLE_ID = 1306082718060384399
# Roles allowed to actually use /deletereview. The command remains visible to everyone.
DELETE_REVIEW_ALLOWED_ROLE_IDS = {
    1306082718060384399,
    1518416402141417472,
    1397677852056354948,
}
REVIEW_DB_FILE = Path(__file__).with_name("reviews.db")
REVIEW_APPROVAL_RATINGS = (4, 5)
REVIEW_UPDATE_APPROVAL_CHANNEL_ID = 1554700806220161164
REVIEW_LOG_CHANNEL_ID = 1554863194885988362
# User-ID blacklist for the review system. Blacklisted reviewers cannot submit
# new reviews or update existing reviews. Persisted locally and synced to GitHub.
REVIEW_BLACKLIST_FILE = Path(__file__).with_name("review_blacklist.json")

# Staff strike system.
# Only members with one of these roles may create or manage staff strikes.
STAFF_STRIKE_ALLOWED_ROLE_IDS = {
    1306082718060384399,
    1518416402141417472,
    1397677852056354948,
    1371738883401711656,
}
STAFF_STRIKES_FILE = Path(__file__).with_name("staff_strikes.json")
STAFF_STRIKE_EXPIRY_CHECK_SECONDS = 60
STAFF_STRIKE_TWO_ACTIVE_CHANNEL_ID = 1371890083833319554
STAFF_STRIKE_EXPIRED_CHANNEL_ID = 1371889867151114343
STAFF_STRIKE_ACTIVE_CHANNEL_ID = 1380990378605281290

# Staff rank order, highest to lowest.
STAFF_ROLE_HIERARCHY = [
    ("Co Owner", 1518416402141417472),
    ("Director", 1397677852056354948),
    ("Staff Manager", 1371738883401711656),
    ("Head Admin", 1371739870380425236),
    ("Admin", 1371738346900029510),
    ("Head Mod", 1371739816227504160),
    ("Mod", 1371738357897494589),
    ("Trial Mod", 1371738371441037343),
]
STAFF_ROLE_IDS = {role_id for _, role_id in STAFF_ROLE_HIERARCHY}

# Tag server ids
# Keep this list in the same order as tag_server_role_ids below.
tag_servers = [
    1369556559205630012,
    1369543909843275866,
    1369504281404641421,
    1369568362497048647,
    401823090004328459,
    1369566527547772978,
    1175479734239498341,
]

# Specific tag role id for each tag server above.
# tag_servers[0] -> tag_server_role_ids[0], etc.
tag_server_role_ids = [
    1492174542733443093,  # server 1369556559205630012
    1545511630060654592,  # server 1369543909843275866
    1545512468548288594,  # server 1369504281404641421
    1545513302363217952,  # server 1369568362497048647
    1545513671910883338,  # server 401823090004328459
    1545514277190762577,  # server 1369566527547772978
    1545514523924897892,  # server 1175479734239498341
]

# Second tag role to check in each tag server.
# Use the server ID as the key, so entries do NOT have to match
# the number/order of tag_servers.
# Set a role ID to 0, or leave a server out, to disable the second-role check.
tag_server_role_ids_2 = {
    # Independent second-role source server -> source role
    985391650861879337: 1471888850510155960,
}

# =========================
# PROTECTED ROLES
# =========================
# Anyone with ANY of these role IDs will NOT be kicked.
PROTECTED_ROLE_IDS = {
    1369757095553138768,
    1369843161102549062,
    1369550591713611856,
    1372370152242286702,
    1366518196315881616,
    1388941384437989431,
    1372712786810896456,
}

# How often to check for tag-based role assignment (seconds)
tag_check_time = 1

# How often to check for users to kick (minutes)
check_time = 10

# Webhook URLs.
# Set these in environment variables.
KICK_WEBHOOK_URL = os.getenv("KICK_WEBHOOK_URL")
ROLE_WEBHOOK_URL = os.getenv("ROLE_WEBHOOK_URL")
# Invite sent to kicked users
MAIN_SERVER_INVITE = "https://discord.gg/bbc"

# Bot token.
# Set BOT_TOKEN in the environment before starting the bot.
BOT_TOKEN = os.getenv("BOT_TOKEN")
# =========================
# AUTOMATIC ROLE REMOVAL
# =========================
# When a member has the trigger role in the MAIN_SERVER, the bot will
# automatically remove the role listed below. Set either value to 0
# to disable this feature.
AUTO_REMOVE_TRIGGER_ROLE_ID = 1378810715611336914  # Role that causes the removal
AUTO_REMOVE_ROLE_ID = 1372658714825457729          # Role to automatically remove


# =========================
# BLACKLIST HELPERS
# =========================

def load_blacklist() -> set[int]:
    """Load the second-role blacklist from disk."""
    if not BLACKLIST_FILE.exists():
        return set()

    try:
        with BLACKLIST_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, list):
            return set()

        return {int(user_id) for user_id in data}

    except (json.JSONDecodeError, ValueError, TypeError, OSError) as e:
        return set()


def save_blacklist(blacklist: set[int]) -> None:
    """Save the second-role blacklist to disk."""
    try:
        with BLACKLIST_FILE.open("w", encoding="utf-8") as file:
            json.dump(sorted(blacklist), file, indent=2)
    except OSError as e:
        pass


second_role_blacklist = load_blacklist()


def load_user_blacklist(path: Path) -> set[int]:
    """Load a persistent user-ID blacklist from local disk."""
    if not path.exists():
        return set()

    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, list):
            return set()

        return {int(user_id) for user_id in data}
    except (json.JSONDecodeError, ValueError, TypeError, OSError):
        return set()


def _save_user_blacklist_local(path: Path, blacklist: set[int]) -> None:
    """Write a user-ID blacklist to the local filesystem as a fallback cache."""
    try:
        with path.open("w", encoding="utf-8") as file:
            json.dump(sorted(blacklist), file, indent=2)
    except OSError:
        pass


# Initialize these before commands or background tasks can reference them.
# UWUIFY and HOODIFY intentionally share the same Textify blacklist.
textify_blacklist = load_user_blacklist(TEXTIFY_BLACKLIST_FILE)
uwu_user_blacklist = textify_blacklist
hood_user_blacklist = textify_blacklist
uwu_hoodify_ban = load_user_blacklist(TEXTIFY_BAN_FILE)
review_blacklist = load_user_blacklist(REVIEW_BLACKLIST_FILE)


def load_staff_strikes() -> list[dict]:
    """Load all staff strikes from local disk."""
    if not STAFF_STRIKES_FILE.exists():
        return []

    try:
        with STAFF_STRIKES_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (json.JSONDecodeError, OSError, TypeError):
        return []

    if not isinstance(data, list):
        return []

    cleaned = []
    for item in data:
        if not isinstance(item, dict):
            continue

        try:
            user_id = int(item["user_id"])
            strike_number = int(item["strike_number"])
            issued_by = int(item["issued_by"])
            reason = str(item["reason"]).strip()
            issued_at = str(item["issued_at"])
            expires_at = str(item["expires_at"])
            original_staff_role_id = (
                int(item["original_staff_role_id"])
                if item.get("original_staff_role_id") is not None
                else None
            )
        except (KeyError, TypeError, ValueError):
            continue

        if not reason or strike_number < 1:
            continue

        cleaned.append({
            "user_id": user_id,
            "strike_number": strike_number,
            "reason": reason,
            "issued_by": issued_by,
            "issued_at": issued_at,
            "expires_at": expires_at,
            "original_staff_role_id": original_staff_role_id,
        })

    return cleaned


def _save_staff_strikes_local(strikes: list[dict]) -> None:
    """Persist staff strikes to the local filesystem."""
    try:
        with STAFF_STRIKES_FILE.open("w", encoding="utf-8") as file:
            json.dump(strikes, file, indent=2)
            file.write("\n")
    except OSError:
        pass


staff_strikes = load_staff_strikes()


def _staff_strike_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def prune_expired_staff_strikes() -> list[dict]:
    """Remove expired strikes and return the records that expired."""
    global staff_strikes

    now = datetime.now(timezone.utc)
    expired = []
    active = []

    for strike in staff_strikes:
        expires_at = _staff_strike_datetime(strike.get("expires_at", ""))
        if expires_at is not None and expires_at > now:
            active.append(strike)
        else:
            expired.append(strike)

    staff_strikes = active
    return expired


def get_staff_role_for_member(member: discord.Member) -> tuple[str, discord.Role] | None:
    """Return the member's highest configured staff role."""
    role_by_id = {role.id: role for role in member.roles}
    for role_name, role_id in STAFF_ROLE_HIERARCHY:
        role = role_by_id.get(role_id)
        if role is not None:
            return role_name, role
    return None


def get_next_staff_role(role_id: int) -> tuple[str, int] | None:
    """Return the configured rank one level below the supplied role."""
    for index, (_, current_id) in enumerate(STAFF_ROLE_HIERARCHY):
        if current_id != role_id:
            continue
        if index + 1 >= len(STAFF_ROLE_HIERARCHY):
            return None
        return STAFF_ROLE_HIERARCHY[index + 1]
    return None


def get_original_staff_role_id(user_id: int) -> int | None:
    """Get the role the member had before strike consequences were applied."""
    for strike in staff_strikes:
        if int(strike.get("user_id", 0)) != int(user_id):
            continue
        original_role_id = strike.get("original_staff_role_id")
        if original_role_id:
            return int(original_role_id)
    return None


async def send_staff_strike_log(channel_id: int, content: str) -> bool:
    """Send a normal-text staff strike role-change message."""
    guild = bot.get_guild(MAIN_SERVER)
    if guild is None:
        return False

    channel = guild.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return False

    if not hasattr(channel, "send"):
        return False

    try:
        await channel.send(
            content,
            allowed_mentions=discord.AllowedMentions(
                everyone=False,
                roles=False,
                users=True,
                replied_user=False,
            ),
        )
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False


async def resolve_main_guild_member(user_id: int) -> discord.Member | None:
    guild = bot.get_guild(MAIN_SERVER)
    if guild is None:
        return None

    member = guild.get_member(int(user_id))
    if member is not None:
        return member

    try:
        return await guild.fetch_member(int(user_id))
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return None


async def apply_staff_strike_consequences(
    member: discord.Member,
    *,
    active_count: int | None = None,
    original_role_id_override: int | None = None,
    log_two_strikes: bool = False,
    log_three_strikes: bool = False,
) -> tuple[str, str] | None:
    """Apply the role consequence for the member's current active strike count."""
    active_strikes = (
        get_user_staff_strikes(member.id)
        if active_count is None
        else [
            strike
            for strike in staff_strikes
            if int(strike.get("user_id", 0)) == int(member.id)
        ]
    )
    active_count = len(active_strikes)

    original_role_id = (
        original_role_id_override
        if original_role_id_override is not None
        else get_original_staff_role_id(member.id)
    )
    current_info = get_staff_role_for_member(member)

    if original_role_id is None and current_info is not None and active_count > 0:
        original_role_id = current_info[1].id
        for strike in active_strikes:
            strike["original_staff_role_id"] = original_role_id

    original_role = member.guild.get_role(original_role_id) if original_role_id else None
    original_role_name = original_role.name if original_role is not None else "No Staff Role"

    if active_count < 2:
        desired_role = original_role
        desired_name = original_role_name if desired_role is not None else "No Staff Role"
    elif active_count == 2:
        next_role_info = get_next_staff_role(original_role_id) if original_role_id else None
        desired_role = (
            member.guild.get_role(next_role_info[1])
            if next_role_info is not None
            else None
        )
        desired_name = (
            next_role_info[0]
            if next_role_info is not None
            else "Suspended"
        )
    else:
        desired_role = None
        desired_name = "Suspended"

    managed_staff_roles = [
        member.guild.get_role(role_id)
        for _, role_id in STAFF_ROLE_HIERARCHY
    ]
    managed_staff_roles = [role for role in managed_staff_roles if role is not None]

    current_staff_role = current_info[1] if current_info is not None else None
    current_name = current_info[0] if current_info is not None else (
        "Suspended" if current_staff_role is None and active_count >= 3 else "No Staff Role"
    )

    roles_to_remove = [
        role
        for role in managed_staff_roles
        if desired_role is None or role.id != desired_role.id
    ]

    bot_member = member.guild.me
    can_manage = (
        bot_member is not None
        and bot_member.guild_permissions.manage_roles
        and member.guild.owner_id != member.id
    )

    if can_manage:
        try:
            removable = [
                role
                for role in roles_to_remove
                if not role.managed and not role.is_default()
                and bot_member.top_role > role
                and member.top_role < bot_member.top_role
            ]
            if removable:
                await member.remove_roles(
                    *removable,
                    reason=f"Staff strike consequence ({active_count} active strikes)",
                )

            if (
                desired_role is not None
                and desired_role not in member.roles
                and not desired_role.managed
                and not desired_role.is_default()
                and bot_member.top_role > desired_role
                and member.top_role < bot_member.top_role
            ):
                await member.add_roles(
                    desired_role,
                    reason=f"Staff strike consequence ({active_count} active strikes)",
                )
        except (discord.Forbidden, discord.HTTPException):
            pass

    refreshed = await resolve_main_guild_member(member.id)
    if refreshed is not None:
        member = refreshed
        refreshed_info = get_staff_role_for_member(member)
        actual_name = refreshed_info[0] if refreshed_info is not None else (
            "Suspended" if active_count >= 3 else "No Staff Role"
        )
        role_changed = (
            desired_role is not None
            and refreshed_info is not None
            and refreshed_info[1].id == desired_role.id
        ) or (
            desired_role is None and refreshed_info is None
        )
    else:
        actual_name = desired_name
        role_changed = current_name != desired_name

    if role_changed and current_name != actual_name:
        if log_two_strikes and active_count == 2:
            await send_staff_strike_log(
                STAFF_STRIKE_TWO_ACTIVE_CHANNEL_ID,
                f"<@{member.id}> {current_name} to {actual_name}",
            )
        elif log_three_strikes and active_count >= 3:
            await send_staff_strike_log(
                STAFF_STRIKE_TWO_ACTIVE_CHANNEL_ID,
                f"<@{member.id}> {current_name} to Suspended",
            )

        return current_name, actual_name

    return None


async def handle_expired_staff_strikes(
    expired_strikes: list[dict],
) -> None:
    """Restore/demote staff roles after strikes expire and announce promotions."""
    if not expired_strikes:
        return

    by_user: dict[int, list[dict]] = {}
    for strike in expired_strikes:
        by_user.setdefault(int(strike["user_id"]), []).append(strike)

    for user_id, user_expired in by_user.items():
        member = await resolve_main_guild_member(user_id)
        if member is None:
            continue

        active_count = len(get_user_staff_strikes(user_id))
        before_active_count = active_count + len(user_expired)

        before_info = get_staff_role_for_member(member)
        if before_info is not None:
            before_name = before_info[0]
        elif before_active_count >= 3:
            before_name = "Suspended"
        else:
            before_name = "No Staff Role"

        await apply_staff_strike_consequences(
            member,
            active_count=active_count,
        )

        refreshed = await resolve_main_guild_member(user_id) or member
        after_info = get_staff_role_for_member(refreshed)
        after_name = after_info[0] if after_info is not None else (
            "Suspended" if active_count >= 3 else "No Staff Role"
        )

        if before_name != after_name:
            await send_staff_strike_log(
                STAFF_STRIKE_EXPIRED_CHANNEL_ID,
                f"<@{user_id}> {before_name} to {after_name}",
            )


def get_user_staff_strikes(user_id: int) -> list[dict]:
    """Return currently active strikes for one user, sorted by strike number."""
    return sorted(
        (
            strike
            for strike in staff_strikes
            if int(strike.get("user_id", 0)) == int(user_id)
        ),
        key=lambda strike: int(strike["strike_number"]),
    )


def next_staff_strike_number(user_id: int) -> int:
    """Get the next sequential strike number for a user."""
    highest = 0
    for strike in staff_strikes:
        if int(strike.get("user_id", 0)) != int(user_id):
            continue
        try:
            highest = max(highest, int(strike["strike_number"]))
        except (KeyError, TypeError, ValueError):
            continue
    return highest + 1


def create_staff_strike(
    *,
    user_id: int,
    reason: str,
    days: int,
    issued_by: int,
    original_staff_role_id: int | None = None,
) -> dict:
    """Create a new time-limited staff strike."""
    reason = reason.strip()
    number = next_staff_strike_number(user_id)
    now = datetime.now(timezone.utc)

    strike = {
        "user_id": int(user_id),
        "strike_number": number,
        "reason": reason,
        "issued_by": int(issued_by),
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(days=int(days))).isoformat(),
        "original_staff_role_id": (
            int(original_staff_role_id)
            if original_staff_role_id is not None
            else None
        ),
    }
    staff_strikes.append(strike)
    return strike


STAFF_STRIKE_SYNC_LOCK = asyncio.Lock()
staff_strike_github_sync_error: str | None = None


def _github_get_staff_strikes() -> tuple[bool, list[dict], str | None]:
    """Fetch the staff strike file from GitHub.

    Returns (exists, strikes, blob_sha).
    """
    encoded_branch = urllib.parse.quote(GITHUB_BRANCH, safe="")
    url = f"{_github_contents_url(STAFF_STRIKES_FILE)}?ref={encoded_branch}"

    try:
        payload = _github_request_json(url)
    except RuntimeError as error:
        if str(error).startswith("GitHub API HTTP 404:"):
            return False, [], None
        raise

    encoded_content = payload.get("content", "")
    if not encoded_content:
        return True, [], payload.get("sha")

    try:
        decoded = base64.b64decode(
            "".join(str(encoded_content).split())
        ).decode("utf-8")
        data = json.loads(decoded)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f"GitHub strike file {STAFF_STRIKES_FILE.name!r} has invalid JSON."
        ) from error

    if not isinstance(data, list):
        raise RuntimeError(
            f"GitHub strike file {STAFF_STRIKES_FILE.name!r} must contain a JSON list."
        )

    cleaned = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            cleaned.append({
                "user_id": int(item["user_id"]),
                "strike_number": int(item["strike_number"]),
                "reason": str(item["reason"]).strip(),
                "issued_by": int(item["issued_by"]),
                "issued_at": str(item["issued_at"]),
                "expires_at": str(item["expires_at"]),
                "original_staff_role_id": (
                    int(item["original_staff_role_id"])
                    if item.get("original_staff_role_id") is not None
                    else None
                ),
            })
        except (KeyError, TypeError, ValueError):
            continue

    return True, cleaned, payload.get("sha")


def _github_save_staff_strikes(strikes: list[dict]) -> None:
    """Create or update the staff strike file in GitHub."""
    exists, _, blob_sha = _github_get_staff_strikes()
    serialized = json.dumps(strikes, indent=2) + "\n"

    payload = {
        "message": f"Update {STAFF_STRIKES_FILE.name}",
        "content": base64.b64encode(
            serialized.encode("utf-8")
        ).decode("ascii"),
        "branch": GITHUB_BRANCH,
    }

    if exists and blob_sha:
        payload["sha"] = blob_sha

    _github_request_json(
        _github_contents_url(STAFF_STRIKES_FILE),
        method="PUT",
        payload=payload,
    )


async def save_staff_strikes() -> bool:
    """Save locally and sync staff strikes to GitHub when configured."""
    global staff_strike_github_sync_error

    _save_staff_strikes_local(staff_strikes)

    if not GITHUB_TOKEN:
        staff_strike_github_sync_error = "GITHUB_TOKEN is not configured."
        return False

    async with STAFF_STRIKE_SYNC_LOCK:
        try:
            await asyncio.to_thread(
                _github_save_staff_strikes,
                list(staff_strikes),
            )
            staff_strike_github_sync_error = None
            return True
        except Exception as error:
            staff_strike_github_sync_error = str(error)
            return False


async def sync_staff_strikes_from_github() -> bool:
    """Load staff strike records from GitHub."""
    global staff_strikes, staff_strike_github_sync_error

    if not GITHUB_TOKEN:
        staff_strike_github_sync_error = "GITHUB_TOKEN is not configured."
        return False

    async with STAFF_STRIKE_SYNC_LOCK:
        try:
            exists, remote_strikes, _ = await asyncio.to_thread(
                _github_get_staff_strikes
            )

            if exists:
                staff_strikes = remote_strikes
                _save_staff_strikes_local(staff_strikes)
            else:
                await asyncio.to_thread(
                    _github_save_staff_strikes,
                    list(staff_strikes),
                )

            staff_strike_github_sync_error = None
            return True
        except Exception as error:
            staff_strike_github_sync_error = str(error)
            return False


def staff_strike_command_allowed(member: discord.Member) -> bool:
    return any(role.id in STAFF_STRIKE_ALLOWED_ROLE_IDS for role in member.roles)


async def staff_strike_command_check(interaction: discord.Interaction) -> bool:
    if interaction.guild_id != MAIN_SERVER:
        raise app_commands.CheckFailure(
            "This command can only be used in the main server."
        )

    if not isinstance(interaction.user, discord.Member):
        raise app_commands.CheckFailure(
            "Could not verify your server roles."
        )

    if not staff_strike_command_allowed(interaction.user):
        raise app_commands.CheckFailure(
            "You do not have permission to use the staff strike system."
        )

    return True


def format_staff_strike(target: discord.Member, strike: dict) -> str:
    return (
        f"{target.mention} / {target.id}\n\n"
        f"strike #{int(strike['strike_number'])}: {strike['reason']}\n\n"
        f"{int(round((
            _staff_strike_datetime(strike['expires_at'])
            - _staff_strike_datetime(strike['issued_at'])
        ).total_seconds() / 86400))}d"
    )


def parse_staff_strike_duration(
    value: str,
) -> int | None:
    value = value.strip().lower()
    if value.endswith("d"):
        value = value[:-1].strip()
    try:
        days = int(value)
    except ValueError:
        return None

    if not 7 <= days <= 40:
        return None

    return days


def _github_headers() -> dict[str, str]:
    """Build headers for GitHub's REST API."""
    if not GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN is not configured in Railway.")

    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "john-the-bot",
    }


def _github_blacklist_path(path: Path) -> str:
    return path.name


def _github_contents_url(path: Path) -> str:
    encoded_path = urllib.parse.quote(
        _github_blacklist_path(path),
        safe="",
    )
    return f"{GITHUB_API_BASE}/repos/{GITHUB_REPO}/contents/{encoded_path}"


def _github_request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
) -> dict:
    """Perform a GitHub JSON API request with useful error messages."""
    request = urllib.request.Request(
        url,
        headers={
            **_github_headers(),
            "Content-Type": "application/json",
        },
        method=method,
    )

    if payload is not None:
        request.data = json.dumps(payload).encode("utf-8")

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        try:
            details = json.loads(body)
            message = details.get("message", body)
        except (json.JSONDecodeError, AttributeError):
            message = body or str(error)

        raise RuntimeError(
            f"GitHub API HTTP {error.code}: {message}"
        ) from error
    except urllib.error.URLError as error:
        raise RuntimeError(
            f"Could not reach GitHub: {error.reason}"
        ) from error

    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError as error:
        raise RuntimeError("GitHub returned invalid JSON.") from error


def _github_get_user_blacklist(
    path: Path,
) -> tuple[bool, set[int], str | None]:
    """Fetch a blacklist from GitHub.

    Returns (exists, ids, blob_sha).
    """
    encoded_branch = urllib.parse.quote(GITHUB_BRANCH, safe="")
    url = f"{_github_contents_url(path)}?ref={encoded_branch}"

    try:
        payload = _github_request_json(url)
    except RuntimeError as error:
        if str(error).startswith("GitHub API HTTP 404:"):
            return False, set(), None
        raise

    encoded_content = payload.get("content", "")
    if not encoded_content:
        return True, set(), payload.get("sha")

    try:
        decoded = base64.b64decode(
            "".join(str(encoded_content).split())
        ).decode("utf-8")
        data = json.loads(decoded)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f"GitHub blacklist file {_github_blacklist_path(path)!r} has invalid JSON."
        ) from error

    if not isinstance(data, list):
        raise RuntimeError(
            f"GitHub blacklist file {_github_blacklist_path(path)!r} must contain a JSON list."
        )

    try:
        ids = {int(user_id) for user_id in data}
    except (ValueError, TypeError) as error:
        raise RuntimeError(
            f"GitHub blacklist file {_github_blacklist_path(path)!r} contains an invalid user ID."
        ) from error

    return True, ids, payload.get("sha")


def _github_save_user_blacklist(
    path: Path,
    blacklist: set[int],
) -> None:
    """Create or update one user-ID blacklist in GitHub."""
    exists, _, blob_sha = _github_get_user_blacklist(path)

    serialized = json.dumps(sorted(blacklist), indent=2) + "\n"
    payload = {
        "message": f"Update {_github_blacklist_path(path)}",
        "content": base64.b64encode(
            serialized.encode("utf-8")
        ).decode("ascii"),
        "branch": GITHUB_BRANCH,
    }

    if exists and blob_sha:
        payload["sha"] = blob_sha

    _github_request_json(
        _github_contents_url(path),
        method="PUT",
        payload=payload,
    )


GITHUB_USER_BLACKLIST_SYNC_LOCK = asyncio.Lock()
github_blacklist_sync_error: str | None = None


async def save_user_blacklist(
    path: Path,
    blacklist: set[int],
) -> bool:
    """Save locally and permanently sync the blacklist to GitHub."""
    global github_blacklist_sync_error

    _save_user_blacklist_local(path, blacklist)

    if not GITHUB_TOKEN:
        github_blacklist_sync_error = (
            "GITHUB_TOKEN is missing from Railway."
        )
        return False

    async with GITHUB_USER_BLACKLIST_SYNC_LOCK:
        try:
            await asyncio.to_thread(
                _github_save_user_blacklist,
                path,
                set(blacklist),
            )
            github_blacklist_sync_error = None
            return True
        except Exception as error:
            github_blacklist_sync_error = str(error)
            return False


async def sync_user_blacklists_from_github() -> bool:
    """Load the Textify blacklist and command-ban list from GitHub."""
    global textify_blacklist, uwu_user_blacklist, hood_user_blacklist, uwu_hoodify_ban, review_blacklist, github_blacklist_sync_error

    if not GITHUB_TOKEN:
        github_blacklist_sync_error = (
            "GITHUB_TOKEN is missing from Railway."
        )
        return False

    async with GITHUB_USER_BLACKLIST_SYNC_LOCK:
        try:
            for path, name in (
                (TEXTIFY_BLACKLIST_FILE, "textify"),
                (TEXTIFY_BAN_FILE, "ban"),
                (REVIEW_BLACKLIST_FILE, "review"),
            ):
                exists, github_ids, _ = await asyncio.to_thread(
                    _github_get_user_blacklist,
                    path,
                )

                if exists:
                    target = (
                        textify_blacklist
                        if name == "textify"
                        else uwu_hoodify_ban
                        if name == "ban"
                        else review_blacklist
                    )
                    target.clear()
                    target.update(github_ids)
                    _save_user_blacklist_local(path, target)
                else:
                    # First run: create the file using any local cached IDs.
                    target = (
                        textify_blacklist
                        if name == "textify"
                        else uwu_hoodify_ban
                        if name == "ban"
                        else review_blacklist
                    )
                    await asyncio.to_thread(
                        _github_save_user_blacklist,
                        path,
                        set(target),
                    )

            github_blacklist_sync_error = None
            return True

        except Exception as error:
            github_blacklist_sync_error = str(error)
            return False


async def ensure_user_blacklists_ready() -> None:
    """Refuse activation until GitHub-backed blacklist state is available."""
    global user_blacklists_synced

    if not GITHUB_TOKEN or user_blacklists_synced:
        return

    if await sync_user_blacklists_from_github():
        user_blacklists_synced = True
        return

    raise RuntimeError(
        github_blacklist_sync_error
        or "The GitHub blacklist could not be synchronized."
    )


class UwuUserBlacklisted(Exception):
    """Raised when a member is blocked from using UWUIFY."""


class HoodUserBlacklisted(Exception):
    """Raised when a member is blocked from using HOODIFY."""


class UserBlacklistStorageUnavailable(Exception):
    """Raised when GitHub-backed blacklist state cannot be verified."""


HOOD_WEBHOOK_CLEANUP_INTERVAL_SECONDS = 60

# =========================
# UWU WEBHOOKS
# =========================

UWU_WEBHOOK_NAME = "Uwuify Relay"
UWU_WEBHOOK_IDLE_SECONDS = 5 * 60

# Only members with one of these role IDs may enable/disable UWU mode.
UWU_ALLOWED_ROLE_IDS = {
    1518416402141417472,
    1378810715611336914,
    1377468541779050636,
}

# External proxy bots whose output should be checked for active UWU/HOODIFY targets.
# Use the bot name here so we do not rely on an unverified application ID.
PROXY_BOT_IDS = set()
PROXY_BOT_NAMES = {
    "bleed",
}
PROXY_REQUEST_TTL_SECONDS = 15

# Safety cleanup interval for leftover UWU webhooks.
UWU_WEBHOOK_CLEANUP_INTERVAL_SECONDS = 60

# Words/phrases that the UWU webhook is NEVER allowed to send.
# Matching is case-insensitive and uses word boundaries, so for example
# "badword" also matches "BADWORD" and "badword!" but not "badwording".
# Add whatever words/phrases you want blocked to this set.
UWU_WORD_BLACKLIST = {
    # "nagger",
    # "rape",
}

# Maximum number of unique people who can be actively UWUified at once.
MAX_ACTIVE_UWU_TARGETS = 5

# Use the package's optional flags so the transformation is more obvious
# than the minimal default behavior.
UWU_FLAGS = uwuify.SMILEY | uwuify.YU | uwuify.STUTTER

# =========================
# HOODIFY WEBHOOKS
# =========================
HOOD_WEBHOOK_NAME = "Hoodify Relay"
HOOD_WEBHOOK_IDLE_SECONDS = 5 * 60

# Members with one of these roles may enable/disable HOODIFY.
# Set to the same roles as UWU, or change them independently.
HOOD_ALLOWED_ROLE_IDS = {
    1518416402141417472,
    1378810715611336914,
    1377468541779050636,
}

# Separate blacklist for HOODIFY messages.
HOOD_WORD_BLACKLIST = {
    # "example",
}

# Maximum number of unique people who can be actively HOODIFIED at once.
MAX_ACTIVE_HOOD_TARGETS = 5

# channel_id -> {"webhook": discord.Webhook, "timer": asyncio.Task | None}
hood_webhooks: dict[int, dict] = {}

# channel_id -> set of target member IDs.
hood_targets: dict[int, set[int]] = {}

# Protect the global HOODIFY target cap from simultaneous commands.
hood_target_lock = asyncio.Lock()

# Local casual-slang transformer used only by HOODIFY.
# This intentionally does not imitate a racial/ethnic identity or dialect.
HOOD_REPLACEMENTS = [
    # Multi-word phrases first.
    (r"\bnot gonna lie\b", "ngl"),
    (r"\bto be honest\b", "tbh"),
    (r"\bin my opinion\b", "imo"),
    (r"\bas soon as possible\b", "asap"),
    (r"\bby the way\b", "btw"),
    (r"\bI do not know\b", "idk"),
    (r"\bI don't know\b", "idk"),
    (r"\bI do not care\b", "idc"),
    (r"\bI don't care\b", "idc"),
    (r"\bnever mind\b", "nvm"),
    (r"\bsee you\b", "cya"),
    (r"\bthank you\b", ("thx", "ty", "preciate it")),
    (r"\bwhat are you doing\b", ("what you doing", "what you on")),
    (r"\bwhat do you mean\b", ("what you mean", "what you talkin bout")),
    (r"\bwhat is up\b", ("what's good", "what's poppin", "what's the move")),
    (r"\bwhat's up\b", ("what's good", "what's poppin", "what's the move")),
    (r"\bhow are you\b", ("how you doing", "how you been", "you good")),
    (r"\bmy friends\b", ("the homies", "my homies", "the gang")),
    (r"\bmy friend\b", ("my homie", "my bro", "my guy")),
    (r"\bwait a minute\b", ("hold up", "wait a sec", "hold on")),
    (r"\bthat is crazy\b", ("that's wild", "that's crazy", "that's nuts")),
    (r"\bthat's crazy\b", ("that's wild", "that's crazy", "that's nuts")),
    (r"\bthat makes sense\b", ("that checks out", "makes sense", "I see it")),
    (r"\bI understand\b", ("I got you", "I hear you", "say less")),
    (r"\byou are right\b", ("you right", "facts", "real talk")),
    (r"\bfor real\b", ("fr", "deadass", "no cap")),
    (r"\bright now\b", ("rn", "right now fr", "as we speak")),
    (r"\bat the moment\b", "rn"),
    (r"\ba lot\b", ("mad", "hella", "a ton")),
    (r"\ba little bit\b", ("a lil", "a bit")),
    (r"\bright away\b", ("rn", "ASAP", "on the spot")),
    (r"\bcome here\b", ("slide thru", "pull up", "come thru")),
    (r"\bgo home\b", ("head home", "dip home", "bounce home")),

    # Contractions / casual phrasing.
    (r"\bgoing to\b", ("gonna", "boutta")),
    (r"\bwant to\b", ("wanna", "tryna")),
    (r"\btrying to\b", ("tryna", "boutta")),
    (r"\bgot to\b", ("gotta", "gonna have to")),
    (r"\bhave to\b", ("gotta", "hafta")),
    (r"\bsupposed to\b", ("sposed to", "supposedta")),
    (r"\bkind of\b", ("kinda", "sorta")),
    (r"\bsort of\b", ("sorta", "kinda")),
    (r"\bout of\b", "outta"),
    (r"\blet me\b", ("lemme", "lmk")),
    (r"\bgive me\b", "gimme"),
    (r"\bcome on\b", ("cmon", "bro cmon")),
    (r"\babout\b", ("bout", "ab")),
    (r"\bbecause\b", ("cause", "cuz", "bc")),
    (r"\bthem\b", "em"),
    (r"\byou all\b", "y'all"),
    (r"\byou guys\b", "y'all"),
    (r"\bdo not\b", "don't"),
    (r"\bdoes not\b", "doesn't"),
    (r"\bdid not\b", "didn't"),
    (r"\bcannot\b", "can't"),
    (r"\bcan not\b", "can't"),
    (r"\bwill not\b", "won't"),
    (r"\bit is\b", "it's"),
    (r"\bthat is\b", "that's"),
    (r"\bwhat is\b", "what's"),
    (r"\bthere is\b", "there's"),
    (r"\bwe are\b", "we're"),
    (r"\byou are\b", "you're"),
    (r"\bthey are\b", "they're"),
    (r"\bI am\b", "I'm"),
    (r"\bI have\b", "I've"),
    (r"\bI will\b", "I'll"),
    (r"\bI would\b", "I'd"),

    # Greetings / people.
    (r"\bhello\b", ("yo", "ayy", "what's good")),
    (r"\bhi\b", ("yo", "ayy")),
    (r"\bhey\b", ("ayy", "yo", "what's good")),
    (r"\bgood morning\b", ("morning", "gm y'all")),
    (r"\bgood night\b", ("night y'all", "gn gang", "night")),
    (r"\bfriends\b", ("homies", "the homies", "the gang")),
    (r"\bfriend\b", ("homie", "bro", "my guy")),
    (r"\bbrother\b", ("bro", "bruh")),
    (r"\bsister\b", ("sis", "girl")),
    (r"\bdude\b", ("bro", "bruh")),
    (r"\bman\b", ("bro", "my guy")),
    (r"\bdawg\b", ("bro", "my guy")),
    (r"\bguys\b", ("y'all", "everybody", "the gang")),
    (r"\bpeople\b", ("folks", "y'all", "everybody")),

    # Everyday vocabulary.
    (r"\byes\b", ("yeah", "yup", "bet", "yea")),
    (r"\bno\b", ("nah", "nope", "nuh uh")),
    (r"\bokay\b", ("aight", "bet", "cool")),
    (r"\bok\b", ("aight", "bet")),
    (r"\bplease\b", ("pls", "plz")),
    (r"\bthanks\b", ("ty", "thx", "preciate it")),
    (r"\bprobably\b", ("prob", "prolly", "most likely")),
    (r"\breally\b", ("rly", "fr", "deadass")),
    (r"\bvery\b", ("mad", "hella", "real")),
    (r"\blittle\b", ("lil", "tiny lil")),
    (r"\bsomething\b", ("sumn", "something fr")),
    (r"\bnothing\b", ("nun", "nothing")),
    (r"\beverything\b", ("all that", "everything fr")),
    (r"\bsomewhere\b", ("somewhere fr", "someplace")),
    (r"\bnowhere\b", ("nowhere fr", "not anywhere")),
    (r"\bhome\b", ("crib", "place")),
    (r"\bhouse\b", ("crib", "place")),
    (r"\bcar\b", ("whip", "ride")),
    (r"\bfood\b", ("grub", "eats")),
    (r"\bmoney\b", ("cash", "bread", "bag")),
    (r"\bwork\b", ("the grind", "job", "work")),
    (r"\bjob\b", ("work", "the grind")),
    (r"\bphone\b", ("cell", "phone")),
    (r"\bcomputer\b", ("PC", "rig")),
    (r"\bstore\b", ("spot", "store")),

    # Mood / reactions.
    (r"\bawesome\b", ("fire", "tuff", "hard")),
    (r"\bgreat\b", ("fire", "solid", "tuff")),
    (r"\bnice\b", ("clean", "solid", "valid")),
    (r"\bcool\b", ("valid", "clean", "chill")),
    (r"\bamazing\b", ("crazy", "insane", "fire")),
    (r"\bexcellent\b", ("fire", "solid")),
    (r"\bfunny\b", ("hilarious", "wild")),
    (r"\bfun\b", ("lit", "a vibe")),
    (r"\bperfect\b", ("tuff", "clean", "spot on")),
    (r"\bseriously\b", ("deadass", "fr", "no joke")),
    (r"\bI agree\b", ("facts", "real talk", "deadass")),
    (r"\bno way\b", ("nahhh", "ain't no way", "bro what")),
    (r"\bcalm down\b", ("chill", "relax bro", "take it easy")),
    (r"\brelax\b", ("chill", "take it easy")),
    (r"\bconfused\b", ("lost", "confused as hell")),
    (r"\btired\b", ("cooked", "drained", "finished")),
    (r"\bexhausted\b", ("cooked", "done for", "fried")),
    (r"\bangry\b", ("heated", "mad", "tight")),
    (r"\bhappy\b", ("hyped", "feeling good", "geeked")),
    (r"\bexcited\b", ("hyped", "geeked", "locked in")),
    (r"\bread y\b", ("locked in", "set", "good to go")),
    (r"\bread y\b", ("locked in", "set")),
    (r"\bserious\b", ("dead serious", "fr")),
    (r"\blie\b", ("cap", "front")),
    (r"\blies\b", ("caps", "fronting")),
    (r"\bly ing\b", ("cappin", "frontin")),
    (r"\blying\b", ("cappin", "frontin")),
    (r"\bjoking\b", ("trolling", "playing")),
    (r"\bmistake\b", ("fumble", "L")),
    (r"\bproblem\b", ("issue", "situation")),
    (r"\bproblems\b", ("issues", "situations")),

    # Gaming / chat slang.
    (r"\bwinning\b", ("cooking", "going crazy")),
    (r"\bwin\b", ("W", "dub")),
    (r"\bwon\b", ("cooked", "got the W")),
    (r"\bloss\b", ("L", "an L")),
    (r"\blosing\b", ("taking an L", "selling")),
    (r"\blost\b", ("sold", "took an L")),
    (r"\bfailed\b", ("sold", "fumbled")),
    (r"\bfail\b", ("sell", "fumble")),
    (r"\bignore\b", ("leave on read", "leave it")),
    (r"\bjoin\b", ("pull up", "slide in")),
    (r"\bwait for me\b", ("hold up for me", "wait on me")),
]

HOOD_OPENERS = [
    "yo",
    "ayy",
    "bro",
    "bruh",
    "nah",
    "ight",
    "look",
]

HOOD_MID_PHRASES = [
    "lowkey",
    "highkey",
    "deadass",
    "no cap",
    "on god",
    "real talk",
    "say less",
    "you feel me",
    "I'm sayin",
    "type shi",
]

HOOD_CLOSERS = [
    "fr",
    "ngl",
    "lowkey",
    "deadass",
    "no cap",
    "bet",
    "on god",
    "😭",
    "💀",
]

HOOD_EXTRAS = [
    "bro",
    "bruh",
    "gang",
    "homie",
    "fr",
    "ngl",
    "lowkey",
    "highkey",
    "deadass",
    "no cap",
    "bet",
]

def hoodify_text(content: str) -> str:
    """Convert ordinary text into varied casual internet slang.

    This is general casual/internet slang and does not imitate a racial or
    ethnic dialect. The output has controlled randomness so repeated messages
    do not always receive the exact same wording.
    """
    if not content:
        return content

    result = content
    protected = []

    def protect(match):
        protected.append(match.group(0))
        # ASCII placeholders can themselves be modified by slang replacements.
        # Private-use Unicode characters are left untouched.
        return f"\ue002{len(protected) - 1}\ue003"

    result = re.sub(
        r"https?://\S+|<@!?\d+>|<@&\d+>|<#\d+>|<a?:\w+:\d+>",
        protect,
        result,
    )

    for pattern, replacement in HOOD_REPLACEMENTS:
        if isinstance(replacement, (tuple, list)):
            replacement = random.choice(replacement)
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)

    result = re.sub(r"!{3,}", "!!", result)
    result = re.sub(r"\?{3,}", "??", result)
    result = re.sub(r"\.{4,}", "...", result)
    result = re.sub(r"[ \t]{2,}", " ", result).strip()

    words = result.split()
    if words:
        # Random seasoning. Each has a probability so not every message gets
        # every extra phrase.
        if random.random() < 0.42 and len(words) >= 3:
            opener = random.choice(HOOD_OPENERS)
            result = f"{opener}, {result}"

        if random.random() < 0.34 and len(words) >= 5:
            parts = result.split()
            phrase = random.choice(HOOD_MID_PHRASES)
            pos = random.randint(1, max(1, len(parts) - 1))
            parts.insert(pos, phrase)
            result = " ".join(parts)

        if random.random() < 0.50:
            closer = random.choice(HOOD_CLOSERS)
            if not re.search(
                r"(?:\bfr\b|\bngl\b|\blowkey\b|\bdeadass\b|no cap|bet|on god|😭|💀)$",
                result,
                flags=re.IGNORECASE,
            ):
                if result.endswith((".", "!", "?")):
                    result = result[:-1].rstrip() + f" {closer}" + result[-1]
                else:
                    result += f" {closer}"

        # Occasionally add one small standalone slang word, but never stack
        # more than one so the message stays readable.
        if random.random() < 0.22 and len(words) >= 4:
            extra = random.choice(HOOD_EXTRAS)
            if extra.lower() not in result.lower():
                result += f" {extra}"

    result = re.sub(r"[ \t]{2,}", " ", result).strip()

    for index, original in enumerate(protected):
        result = result.replace(f"\ue002{index}\ue003", original)

    # For a message where nothing matched and random seasoning didn't fire,
    # use one of several fallbacks instead of always adding "fr".
    if result == content.strip() and result:
        result = random.choice([
            f"yo, {result}",
            f"{result} ngl",
            f"{result} no cap",
            f"{result} fr",
            f"{result} lowkey",
        ])

    return result


def hood_user_is_whitelisted(member: discord.Member | discord.User) -> bool:
    """Return True when the member has at least one allowed HOODIFY role."""
    return any(
        role.id in HOOD_ALLOWED_ROLE_IDS
        for role in getattr(member, "roles", ())
    )

def get_active_hood_target_ids(exclude_channel_id: int | None = None) -> set[int]:
    active: set[int] = set()
    for channel_id, target_ids in hood_targets.items():
        if channel_id == exclude_channel_id:
            continue
        active.update(target_ids)
    return active

def get_active_hood_target_count() -> int:
    return len(get_active_hood_target_ids())

class HoodTargetLimitReached(Exception):
    """Raised when adding a HOODIFY target would exceed the global cap."""

class HoodMessageBlocked(Exception):
    """Raised when a HOODIFY message contains blocked content."""

def get_blacklisted_hood_word(content: str) -> str | None:
    if not content or not HOOD_WORD_BLACKLIST:
        return None
    for blocked in HOOD_WORD_BLACKLIST:
        blocked = str(blocked).strip()
        if not blocked:
            continue
        pattern = rf"(?<!\\w){re.escape(blocked)}(?!\\w)"
        if re.search(pattern, content, flags=re.IGNORECASE):
            return blocked
    return None

def ensure_hood_message_is_allowed(content: str) -> None:
    blocked = get_blacklisted_hood_word(content)
    if blocked is not None:
        raise HoodMessageBlocked(blocked)

async def _delete_hood_webhook_after_idle(
    channel_id: int,
    webhook: discord.Webhook,
) -> None:
    try:
        await asyncio.sleep(HOOD_WEBHOOK_IDLE_SECONDS)
        entry = hood_webhooks.get(channel_id)
        if entry is not None and entry.get("webhook") is webhook:
            try:
                await webhook.delete(reason="Hoodify webhook unused for 5 minutes")
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
            finally:
                hood_webhooks.pop(channel_id, None)
                hood_targets.pop(channel_id, None)
    except asyncio.CancelledError:
        return

def _reset_hood_webhook_timer(channel_id: int, webhook: discord.Webhook) -> None:
    entry = hood_webhooks.get(channel_id)
    if entry is None or entry.get("webhook") is not webhook:
        return

    old_timer = entry.get("timer")
    if old_timer is not None and not old_timer.done():
        old_timer.cancel()

    entry["timer"] = asyncio.create_task(
        _delete_hood_webhook_after_idle(channel_id, webhook)
    )

async def get_hood_webhook(channel: discord.TextChannel) -> discord.Webhook:
    channel_id = channel.id
    entry = hood_webhooks.get(channel_id)

    if entry is not None:
        webhook = entry.get("webhook")
        if webhook is not None:
            try:
                await webhook.fetch()
                _reset_hood_webhook_timer(channel_id, webhook)
                return webhook
            except (discord.NotFound, discord.HTTPException):
                hood_webhooks.pop(channel_id, None)

    webhook = await channel.create_webhook(
        name=HOOD_WEBHOOK_NAME,
        reason="Temporary webhook for the ,hoodify /hoodify command",
    )

    hood_webhooks[channel_id] = {
        "webhook": webhook,
        "timer": None,
    }
    _reset_hood_webhook_timer(channel_id, webhook)
    return webhook

async def set_hood_target(
    channel: discord.TextChannel,
    target: discord.Member,
) -> discord.Webhook:
    try:
        await ensure_user_blacklists_ready()
    except RuntimeError as error:
        raise UserBlacklistStorageUnavailable(str(error)) from error

    if target.id in hood_user_blacklist:
        raise HoodUserBlacklisted

    async with hood_target_lock:
        channel_targets = hood_targets.setdefault(channel.id, set())

        if target.id not in channel_targets:
            active_target_ids = get_active_hood_target_ids()
            if (
                target.id not in active_target_ids
                and len(active_target_ids) >= MAX_ACTIVE_HOOD_TARGETS
            ):
                if not channel_targets:
                    hood_targets.pop(channel.id, None)
                raise HoodTargetLimitReached(
                    f"The maximum of {MAX_ACTIVE_HOOD_TARGETS} active HOODIFY "
                    "targets has been reached."
                )
            channel_targets.add(target.id)

    webhook = await get_hood_webhook(channel)
    _reset_hood_webhook_timer(channel.id, webhook)
    return webhook

async def disable_hood_target(channel_id: int) -> bool:
    hood_targets.pop(channel_id, None)
    entry = hood_webhooks.pop(channel_id, None)
    if entry is None:
        return False

    timer = entry.get("timer")
    if timer is not None and not timer.done():
        timer.cancel()

    webhook = entry.get("webhook")
    if webhook is not None:
        try:
            await webhook.delete(reason="Hoodify mode disabled")
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    return True

async def disable_all_hood_targets() -> int:
    channel_ids = list(hood_targets.keys() | hood_webhooks.keys())
    disabled_count = 0
    for channel_id in channel_ids:
        if await disable_hood_target(channel_id):
            disabled_count += 1
    return disabled_count

async def send_hood_message(
    channel: discord.TextChannel,
    target: discord.Member,    content: str,
) -> list[discord.WebhookMessage]:
    if target.id in hood_user_blacklist:
        raise HoodUserBlacklisted

    ensure_hood_message_is_allowed(content)

    webhook = await get_hood_webhook(channel)
    hood_text = hoodify_text(content)

    if not hood_text:
        hood_text = "yo"

    ensure_hood_message_is_allowed(hood_text)

    sent_messages: list[discord.WebhookMessage] = []
    chunks = [
        hood_text[index:index + 2000]
        for index in range(0, len(hood_text), 2000)
    ] or ["yo"]

    for chunk in chunks:
        sent_messages.append(
            await webhook.send(
                chunk,
                username=target.display_name[:80],
                avatar_url=target.display_avatar.url,
                allowed_mentions=discord.AllowedMentions(
                    everyone=False,
                    roles=False,
                    users=True,
                    replied_user=False,
                ),
                wait=True,
            )
        )

    _reset_hood_webhook_timer(channel.id, webhook)
    return sent_messages

async def cleanup_stale_hood_webhooks() -> None:
    if bot.user is None:
        return

    bot_id = bot.user.id
    tracked_ids = {
        entry["webhook"].id
        for entry in hood_webhooks.values()
        if entry.get("webhook") is not None
    }

    for guild in bot.guilds:
        try:
            webhooks = await guild.webhooks()
        except (discord.Forbidden, discord.HTTPException):
            continue

        for webhook in webhooks:
            if webhook.id in tracked_ids:
                continue
            if webhook.name != HOOD_WEBHOOK_NAME:
                continue
            if webhook.user is None or webhook.user.id != bot_id:
                continue

            try:
                await webhook.delete(reason="Stale Hoodify webhook cleanup")
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

@tasks.loop(seconds=HOOD_WEBHOOK_CLEANUP_INTERVAL_SECONDS if "HOOD_WEBHOOK_CLEANUP_INTERVAL_SECONDS" in globals() else 60)
async def hood_webhook_cleanup_loop():
    await cleanup_stale_hood_webhooks()

@hood_webhook_cleanup_loop.before_loop
async def before_hood_webhook_cleanup():
    await bot.wait_until_ready()

# channel_id -> {"webhook": discord.Webhook, "timer": asyncio.Task | None}
uwu_webhooks: dict[int, dict] = {}

# channel_id -> set of target member IDs.
# Multiple people can be UWUified in the same channel at once.
uwu_targets: dict[int, set[int]] = {}

# Protect the global 5-person cap from simultaneous commands.
uwu_target_lock = asyncio.Lock()


def uwu_user_is_whitelisted(member: discord.Member | discord.User) -> bool:
    """Return True when the member has at least one allowed UWU role."""
    return any(
        role.id in UWU_ALLOWED_ROLE_IDS
        for role in getattr(member, "roles", ())
    )


def get_active_uwu_target_ids(exclude_channel_id: int | None = None) -> set[int]:
    """Return the unique member IDs currently using UWU mode."""
    active: set[int] = set()

    for channel_id, target_ids in uwu_targets.items():
        if channel_id == exclude_channel_id:
            continue
        active.update(target_ids)

    return active


def get_active_uwu_target_count() -> int:
    """Return the number of unique people currently being UWUified globally."""
    return len(get_active_uwu_target_ids())


class UwuTargetLimitReached(Exception):
    """Raised when adding a new target would exceed the global UWU limit."""


class UwuMessageBlocked(Exception):
    """Raised when a message contains text that the UWU webhook must not send."""


def get_blacklisted_uwu_word(content: str) -> str | None:
    """Return the first blocked word/phrase found in content, or None."""
    if not content or not UWU_WORD_BLACKLIST:
        return None

    for blocked in UWU_WORD_BLACKLIST:
        blocked = str(blocked).strip()
        if not blocked:
            continue

        pattern = rf"(?<!\w){re.escape(blocked)}(?!\w)"
        if re.search(pattern, content, flags=re.IGNORECASE):
            return blocked

    return None


def ensure_uwu_message_is_allowed(content: str) -> None:
    """Raise UwuMessageBlocked when the content must not be sent."""
    blocked = get_blacklisted_uwu_word(content)
    if blocked is not None:
        raise UwuMessageBlocked(blocked)


async def _delete_uwu_webhook_after_idle(channel_id: int, webhook: discord.Webhook) -> None:
    """Delete the temporary UWU webhook after 5 minutes without use."""
    try:
        await asyncio.sleep(UWU_WEBHOOK_IDLE_SECONDS)

        entry = uwu_webhooks.get(channel_id)
        if entry is not None and entry.get("webhook") is webhook:
            try:
                await webhook.delete(reason="Uwu webhook unused for 5 minutes")
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
            finally:
                uwu_webhooks.pop(channel_id, None)
                uwu_targets.pop(channel_id, None)
    except asyncio.CancelledError:
        return


def _reset_uwu_webhook_timer(channel_id: int, webhook: discord.Webhook) -> None:
    entry = uwu_webhooks.get(channel_id)
    if entry is None or entry.get("webhook") is not webhook:
        return

    old_timer = entry.get("timer")
    if old_timer is not None and not old_timer.done():
        old_timer.cancel()

    entry["timer"] = asyncio.create_task(
        _delete_uwu_webhook_after_idle(channel_id, webhook)
    )


async def get_uwu_webhook(channel: discord.TextChannel) -> discord.Webhook:
    """Get or create the temporary UWU webhook for a channel."""
    channel_id = channel.id
    entry = uwu_webhooks.get(channel_id)

    if entry is not None:
        webhook = entry.get("webhook")
        if webhook is not None:
            try:
                await webhook.fetch()
                _reset_uwu_webhook_timer(channel_id, webhook)
                return webhook
            except (discord.NotFound, discord.HTTPException):
                uwu_webhooks.pop(channel_id, None)

    webhook = await channel.create_webhook(
        name=UWU_WEBHOOK_NAME,
        reason="Temporary webhook for the ,uwuify /uwuify command",
    )

    uwu_webhooks[channel_id] = {
        "webhook": webhook,
        "timer": None,
    }
    _reset_uwu_webhook_timer(channel_id, webhook)
    return webhook


async def set_uwu_target(
    channel: discord.TextChannel,
    target: discord.Member,
) -> discord.Webhook:
    """Add a target to UWU mode while enforcing a global 5-person cap."""
    try:
        await ensure_user_blacklists_ready()
    except RuntimeError as error:
        raise UserBlacklistStorageUnavailable(str(error)) from error

    if target.id in uwu_user_blacklist:
        raise UwuUserBlacklisted

    async with uwu_target_lock:
        channel_targets = uwu_targets.setdefault(channel.id, set())

        # Already active in this channel: no additional slot is needed.
        if target.id not in channel_targets:
            active_target_ids = get_active_uwu_target_ids()

            # A person already active anywhere does not consume another slot.
            if (
                target.id not in active_target_ids
                and len(active_target_ids) >= MAX_ACTIVE_UWU_TARGETS
            ):
                # Don't leave an empty set behind when the command is rejected.
                if not channel_targets:
                    uwu_targets.pop(channel.id, None)

                raise UwuTargetLimitReached(
                    f"The maximum of {MAX_ACTIVE_UWU_TARGETS} active UWU targets has been reached."
                )

            channel_targets.add(target.id)

    # Create/reuse the webhook outside the cap lock so webhook API calls do not
    # block another target from being checked against the cap.
    webhook = await get_uwu_webhook(channel)
    _reset_uwu_webhook_timer(channel.id, webhook)
    return webhook


async def disable_uwu_target(channel_id: int) -> bool:
    """Disable UWU mode and delete its temporary webhook immediately."""
    uwu_targets.pop(channel_id, None)
    entry = uwu_webhooks.pop(channel_id, None)
    if entry is None:
        return False

    timer = entry.get("timer")
    if timer is not None and not timer.done():
        timer.cancel()

    webhook = entry.get("webhook")
    if webhook is not None:
        try:
            await webhook.delete(reason="Uwu mode disabled")
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    return True


async def disable_all_uwu_targets() -> int:
    """Disable UWU mode in every currently tracked channel and delete its webhooks."""
    channel_ids = list(uwu_targets.keys() | uwu_webhooks.keys())
    disabled_count = 0

    for channel_id in channel_ids:
        if await disable_uwu_target(channel_id):
            disabled_count += 1

    return disabled_count


async def send_uwu_message(
    channel: discord.TextChannel,
    target: discord.Member,
    content: str,
) -> list[discord.WebhookMessage]:
    if target.id in uwu_user_blacklist:
        raise UwuUserBlacklisted

    """Uwuify text and send it through the temporary webhook.

    The message is checked before and after uwuification so a blocked word/phrase
    can never be sent by this webhook. Role mentions and @everyone/@here are also
    disabled through AllowedMentions.
    """
    # Never send a blacklisted word/phrase.
    ensure_uwu_message_is_allowed(content)

    webhook = await get_uwu_webhook(channel)

    # Protect Discord mentions before uwuify transforms the text.
    # This keeps @users, @roles, and #channels intact and clickable.
    protected_mentions = []

    def protect_mention(match):
        protected_mentions.append(match.group(0))
        # Use Unicode private-use characters only. ASCII placeholder words such
        # as "__UWU_PROTECTED_0__" get transformed by uwuify itself.
        return f"\ue000{len(protected_mentions) - 1}\ue001"

    uwu_input = re.sub(
        r"<@!?\d+>|<@&\d+>|<#\d+>",
        protect_mention,
        content,
    )

    # PyPI uwuify exposes uwu(text, flags=...).
    uwu_text = uwuify.uwu(uwu_input, flags=UWU_FLAGS)
    if not uwu_text:
        uwu_text = "uwu"

    # Restore exact Discord mention tokens before sending.
    for index, original in enumerate(protected_mentions):
        uwu_text = uwu_text.replace(
            f"\ue000{index}\ue001",
            original,
        )

    # Also check the final transformed text so the webhook never sends a blocked
    # word even if the transformation itself somehow creates one.
    ensure_uwu_message_is_allowed(uwu_text)

    sent_messages: list[discord.WebhookMessage] = []
    chunks = [
        uwu_text[index:index + 2000]
        for index in range(0, len(uwu_text), 2000)
    ] or ["uwu"]

    for chunk in chunks:
        sent_messages.append(
            await webhook.send(
                chunk,
                username=target.display_name[:80],
                avatar_url=target.display_avatar.url,
                # Never allow the UWU webhook to ping roles, @everyone, or @here.
                # Normal @user mentions are still allowed.
                allowed_mentions=discord.AllowedMentions(
                    everyone=False,
                    roles=False,
                    users=True,
                    replied_user=False,
                ),
                wait=True,
            )
        )

    _reset_uwu_webhook_timer(channel.id, webhook)
    return sent_messages


async def cleanup_stale_uwu_webhooks() -> None:
    """Delete leftover UWU webhooks created by this bot.

    Active/registered UWU webhooks are skipped. This catches webhooks left
    behind when the normal 5-minute timer fails or the bot restarts.
    """
    if bot.user is None:
        return

    bot_id = bot.user.id
    tracked_ids = {        entry["webhook"].id
        for entry in uwu_webhooks.values()
        if entry.get("webhook") is not None
    }

    for guild in bot.guilds:
        try:
            webhooks = await guild.webhooks()
        except discord.Forbidden:
            continue
        except discord.HTTPException as e:
            continue
        except Exception as e:
            continue

        for webhook in webhooks:
            if webhook.id in tracked_ids:
                continue

            # Only delete webhooks with our exact name and created by this bot.
            if webhook.name != UWU_WEBHOOK_NAME:
                continue
            if webhook.user is None or webhook.user.id != bot_id:
                continue


            try:
                await webhook.delete(reason="Stale UWU webhook cleanup")
                pass
            except discord.NotFound:
                pass
            except discord.Forbidden as e:
                pass
            except discord.HTTPException as e:
                pass
            except Exception as e:
                pass


@tasks.loop(seconds=UWU_WEBHOOK_CLEANUP_INTERVAL_SECONDS)
async def uwu_webhook_cleanup_loop():
    await cleanup_stale_uwu_webhooks()


@uwu_webhook_cleanup_loop.before_loop
async def before_uwu_webhook_cleanup():
    await bot.wait_until_ready()


async def disable_uwu_for_user(user_id: int) -> int:
    """Remove one user from every active UWUIFY channel."""
    affected_channels = [
        channel_id
        for channel_id, target_ids in uwu_targets.items()
        if user_id in target_ids
    ]

    removed = 0
    for channel_id in affected_channels:
        target_ids = uwu_targets.get(channel_id)
        if target_ids is None or user_id not in target_ids:
            continue

        target_ids.discard(user_id)
        removed += 1

        if not target_ids:
            await disable_uwu_target(channel_id)

    return removed


async def disable_hood_for_user(user_id: int) -> int:
    """Remove one user from every active HOODIFY channel."""
    affected_channels = [
        channel_id
        for channel_id, target_ids in hood_targets.items()
        if user_id in target_ids
    ]

    removed = 0
    for channel_id in affected_channels:
        target_ids = hood_targets.get(channel_id)
        if target_ids is None or user_id not in target_ids:
            continue

        target_ids.discard(user_id)
        removed += 1

        if not target_ids:
            await disable_hood_target(channel_id)

    return removed



# =========================
# REVIEW DATABASE
# =========================

review_db = sqlite3.connect(
    REVIEW_DB_FILE,
    check_same_thread=False,
    timeout=15,
)
review_db.row_factory = sqlite3.Row
review_db.execute("PRAGMA busy_timeout = 15000")
review_db.execute("""
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id INTEGER NOT NULL,
    reviewer_id INTEGER NOT NULL,
    rating INTEGER NOT NULL,
    comment TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")
review_db.execute('''
CREATE TABLE IF NOT EXISTS review_update_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id INTEGER NOT NULL,
    target_id INTEGER NOT NULL,
    reviewer_id INTEGER NOT NULL,
    old_rating INTEGER NOT NULL,
    old_comment TEXT NOT NULL,
    new_rating INTEGER NOT NULL,
    new_comment TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    approval_message_id INTEGER,
    approval_channel_id INTEGER,
    reviewed_by INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reviewed_at TIMESTAMP
)
''')
review_db.commit()

review_db_thread_lock = threading.RLock()


def _ensure_review_db_schema() -> None:
    """Make sure the review table exists, even after a GitHub DB restore."""
    with review_db_thread_lock:
        connection = sqlite3.connect(
            REVIEW_DB_FILE,
            check_same_thread=False,
            timeout=30,
        )
        try:
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_id INTEGER NOT NULL,
                    reviewer_id INTEGER NOT NULL,
                    rating INTEGER NOT NULL,
                    comment TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            connection.execute('''
                CREATE TABLE IF NOT EXISTS review_update_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    review_id INTEGER NOT NULL,
                    target_id INTEGER NOT NULL,
                    reviewer_id INTEGER NOT NULL,
                    old_rating INTEGER NOT NULL,
                    old_comment TEXT NOT NULL,
                    new_rating INTEGER NOT NULL,
                    new_comment TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    approval_message_id INTEGER,
                    approval_channel_id INTEGER,
                    reviewed_by INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    reviewed_at TIMESTAMP
                )
            ''')
            connection.execute('''
                CREATE TABLE IF NOT EXISTS review_update_approval_context (
                    request_id INTEGER PRIMARY KEY,
                    approved INTEGER NOT NULL DEFAULT 1
                )
            ''')
            connection.execute('''
                CREATE TRIGGER IF NOT EXISTS prevent_unapproved_review_updates
                BEFORE UPDATE OF rating, comment ON reviews
                WHEN NOT EXISTS (
                    SELECT 1
                    FROM review_update_approval_context c
                    JOIN review_update_requests r ON r.id = c.request_id
                    WHERE c.approved = 1
                      AND r.review_id = OLD.id
                      AND r.status = 'pending'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'Review updates require moderator approval.');
                END
            ''')
            connection.execute('''
                DELETE FROM reviews
                WHERE id NOT IN (
                    SELECT MIN(id)
                    FROM reviews
                    GROUP BY target_id, reviewer_id
                )
            ''')
            connection.execute('''
                CREATE UNIQUE INDEX IF NOT EXISTS idx_reviews_target_reviewer
                ON reviews(target_id, reviewer_id)
            ''')
            connection.commit()
        finally:
            connection.close()


def _reopen_review_db() -> None:
    global review_db
    try:
        review_db.close()
    except Exception:
        pass
    review_db = sqlite3.connect(
        REVIEW_DB_FILE,
        check_same_thread=False,
        timeout=15,
    )
    review_db.row_factory = sqlite3.Row
    review_db.execute("PRAGMA busy_timeout = 15000")
    review_db.execute("""
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_id INTEGER NOT NULL,
            reviewer_id INTEGER NOT NULL,
            rating INTEGER NOT NULL,
            comment TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    review_db.execute('''
        CREATE TABLE IF NOT EXISTS review_update_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            review_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            reviewer_id INTEGER NOT NULL,
            old_rating INTEGER NOT NULL,
            old_comment TEXT NOT NULL,
            new_rating INTEGER NOT NULL,
            new_comment TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            approval_message_id INTEGER,
            approval_channel_id INTEGER,
            reviewed_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TIMESTAMP
        )
    ''')
    review_db.execute('''
        CREATE TABLE IF NOT EXISTS review_update_approval_context (
            request_id INTEGER PRIMARY KEY,
            approved INTEGER NOT NULL DEFAULT 1
        )
    ''')
    review_db.execute('''
        CREATE TRIGGER IF NOT EXISTS prevent_unapproved_review_updates
        BEFORE UPDATE OF rating, comment ON reviews
        WHEN NOT EXISTS (
            SELECT 1
            FROM review_update_approval_context c
            JOIN review_update_requests r ON r.id = c.request_id
            WHERE c.approved = 1
              AND r.review_id = OLD.id
              AND r.status = 'pending'
        )
        BEGIN
            SELECT RAISE(ABORT, 'Review updates require moderator approval.');
        END
    ''')
    review_db.execute('''
        DELETE FROM reviews
        WHERE id NOT IN (
            SELECT MIN(id)
            FROM reviews
            GROUP BY target_id, reviewer_id
        )
    ''')
    review_db.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_reviews_target_reviewer
        ON reviews(target_id, reviewer_id)
    ''')
    review_db.commit()


def _sqlite_file_is_valid(path: Path) -> bool:
    """Return True when the file has a valid SQLite header and integrity check."""
    try:
        if not path.exists() or path.stat().st_size < 16:
            return False
        with path.open("rb") as file:
            if file.read(16) != b"SQLite format 3\\x00":
                return False
        connection = sqlite3.connect(path, timeout=10)
        try:
            result = connection.execute("PRAGMA integrity_check").fetchone()
            return bool(result and result[0] == "ok")
        finally:
            connection.close()
    except Exception:
        return False


def _ensure_review_db_file_ready_sync() -> None:
    """Repair/restore the local review DB before a write if it is invalid."""
    global review_db

    if _sqlite_file_is_valid(REVIEW_DB_FILE):
        return

    with review_db_thread_lock:
        if _sqlite_file_is_valid(REVIEW_DB_FILE):
            return

        # First try the known-good GitHub database branch.
        if GITHUB_TOKEN:
            try:
                remote = _github_download_db(GITHUB_DB_PATH)
                if remote:
                    temp = REVIEW_DB_FILE.with_name("reviews.db.repair.tmp")
                    temp.write_bytes(remote)
                    if _sqlite_file_is_valid(temp):
                        try:
                            review_db.close()
                        except Exception:
                            pass
                        temp.replace(REVIEW_DB_FILE)
                        _reopen_review_db()
                        return
                    temp.unlink(missing_ok=True)
            except Exception:
                pass

        # Never leave an invalid file blocking new reviews.
        # Keep the bad file as a backup and create a clean database.
        if REVIEW_DB_FILE.exists():
            backup = REVIEW_DB_FILE.with_name("reviews.db.corrupt")
            try:
                backup.unlink(missing_ok=True)
            except Exception:
                pass
            REVIEW_DB_FILE.replace(backup)

        try:
            review_db.close()
        except Exception:
            pass
        _reopen_review_db()


class DuplicateReviewError(Exception):
    '''Raised when a reviewer already has an active review for a target.'''


class PendingReviewUpdateError(Exception):
    '''Raised when a review already has a pending update request.'''


class ReviewStoreConflict(Exception):
    """Raised when another bot instance changed the shared GitHub store."""


GITHUB_REVIEW_STORE_PATH = "review_store.json"
review_store_thread_lock = threading.RLock()


def _new_review_store():
    return {
        "version": 1,
        "reviews": [],
        "review_update_requests": [],
    }


def _row_to_dict(row):
    return dict(row) if row is not None else None


def _seed_review_store_from_sqlite():
    store = _new_review_store()
    with review_db_thread_lock:
        connection = sqlite3.connect(REVIEW_DB_FILE, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            store["reviews"] = [
                dict(row)
                for row in connection.execute(
                    "SELECT id, target_id, reviewer_id, rating, comment, created_at "
                    "FROM reviews ORDER BY id"
                ).fetchall()
            ]
            store["review_update_requests"] = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM review_update_requests ORDER BY id"
                ).fetchall()
            ]
        finally:
            connection.close()
    return store


def _github_review_store_request(method="GET", content=None, sha=None):
    url = _github_db_url(GITHUB_REVIEW_STORE_PATH)
    branch = urllib.parse.quote(GITHUB_DB_BRANCH, safe="")
    if method == "GET":
        url = f"{url}?ref={branch}"

    body = None
    headers = _github_headers()

    if method == "PUT":
        body = {
            "message": "Sync review store",
            "content": base64.b64encode(
                json.dumps(content, ensure_ascii=False, indent=2).encode("utf-8")
            ).decode("ascii"),
            "branch": GITHUB_DB_BRANCH,
            "sha": sha,
        }
        headers = {**headers, "Content-Type": "application/json"}

    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers=headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        if error.code == 409:
            raise ReviewStoreConflict from error
        body_text = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"GitHub review store request failed ({error.code}): {body_text}"
        ) from error


def _github_read_review_store():
    if not GITHUB_TOKEN:
        return _new_review_store(), None

    payload = _github_review_store_request("GET")
    if payload is None:
        store = _seed_review_store_from_sqlite()
        return store, None

    encoded = payload.get("content")
    if not encoded:
        raise RuntimeError("GitHub review store is empty.")

    raw = base64.b64decode("".join(str(encoded).split())).decode("utf-8")
    store = json.loads(raw)

    if not isinstance(store, dict):
        raise RuntimeError("GitHub review store has invalid JSON.")

    store.setdefault("version", 1)
    store.setdefault("reviews", [])
    store.setdefault("review_update_requests", [])
    for request in store["review_update_requests"]:
        request.setdefault("update_reason", "")
    return store, payload.get("sha")


def _github_write_review_store(store, sha=None):
    payload = _github_review_store_request(
        "PUT",
        content=store,
        sha=sha,
    )
    if payload is None:
        raise RuntimeError("GitHub did not return the saved review store.")
    return payload.get("content", {}).get("sha")


def _github_update_review_store(mutator):
    """Atomically update the shared review store with conflict retries."""
    with review_store_thread_lock:
        last_error = None

        for _ in range(6):
            store, sha = _github_read_review_store()

            result = mutator(store)

            try:
                _github_write_review_store(store, sha)
                return result, store
            except ReviewStoreConflict as error:
                last_error = error
                time.sleep(0.5)

        raise RuntimeError(
            "The shared review store was changed by another bot instance. "
            "Please try again."
        ) from last_error


def _load_shared_review_store():
    with review_store_thread_lock:
        store, sha = _github_read_review_store()

        # Create the shared store when this is the first deployment using it.
        if sha is None and GITHUB_TOKEN:
            try:
                _github_write_review_store(store, None)
            except ReviewStoreConflict:
                store, _ = _github_read_review_store()

        return store


def _mirror_review_store_to_sqlite(store):
    """Keep reviews.db as a local/exported mirror of the shared store."""
    with review_db_thread_lock:
        connection = sqlite3.connect(REVIEW_DB_FILE, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM review_update_approval_context")
            connection.execute("DELETE FROM review_update_requests")
            connection.execute("DELETE FROM reviews")

            for review in store.get("reviews", []):
                connection.execute(
                    """
                    INSERT INTO reviews
                    (id, target_id, reviewer_id, rating, comment, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        review["id"],
                        review["target_id"],
                        review["reviewer_id"],
                        review["rating"],
                        review["comment"],
                        review.get("created_at"),
                    ),
                )

            request_columns = [
                "id",
                "review_id",
                "target_id",
                "reviewer_id",
                "old_rating",
                "old_comment",
                "new_rating",
                "new_comment",
                "status",
                "approval_message_id",
                "approval_channel_id",
                "reviewed_by",
                "created_at",
                "reviewed_at",
            ]

            placeholders = ", ".join(["?"] * len(request_columns))
            column_sql = ", ".join(request_columns)

            for request in store.get("review_update_requests", []):
                connection.execute(
                    f"""
                    INSERT INTO review_update_requests
                    ({column_sql})
                    VALUES ({placeholders})
                    """,
                    [request.get(column) for column in request_columns],
                )

            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def _ensure_shared_review_store():
    store = _load_shared_review_store()
    _mirror_review_store_to_sqlite(store)
    return store


def _generate_shared_id(existing_ids):
    """Return the next sequential ID in the shared review store.
    
    GitHub's compare-and-swap update is used by the shared-store writer, so
    concurrent Railway instances cannot both commit the same next ID.
    Deleted IDs are not reused.
    """
    numeric_ids = {int(value) for value in existing_ids}
    return max(numeric_ids, default=0) + 1


def add_review(target_id: int, reviewer_id: int, rating: int, comment: str):
    '''Insert one review per reviewer and target into the shared store.'''
    def mutator(store):
        for review in store["reviews"]:
            if (
                int(review["target_id"]) == int(target_id)
                and int(review["reviewer_id"]) == int(reviewer_id)
            ):
                raise DuplicateReviewError

        review_id = _generate_shared_id(
            {int(review["id"]) for review in store["reviews"]}
        )
        store["reviews"].append(
            {
                "id": review_id,
                "target_id": target_id,
                "reviewer_id": reviewer_id,
                "rating": rating,
                "comment": comment,
                "created_at": time.strftime(
                    "%Y-%m-%d %H:%M:%S",
                    time.gmtime(),
                ),
            }
        )
        return review_id

    review_id, store = _github_update_review_store(mutator)
    _mirror_review_store_to_sqlite(store)
    return review_id


def get_user_review(target_id: int, reviewer_id: int):
    store = _load_shared_review_store()
    matches = [
        review for review in store["reviews"]
        if int(review["target_id"]) == int(target_id)
        and int(review["reviewer_id"]) == int(reviewer_id)
    ]
    if not matches:
        return None
    return max(matches, key=lambda review: int(review["id"]))


def create_review_update_request(
    review_id,
    target_id,
    reviewer_id,
    old_rating,
    old_comment,
    new_rating,
    new_comment,
    update_reason,
):
    def mutator(store):
        for request in store["review_update_requests"]:
            if (
                int(request["review_id"]) == int(review_id)
                and request["status"] == "pending"
            ):
                raise PendingReviewUpdateError

        request_id = _generate_shared_id(
            {
                int(request["id"])
                for request in store["review_update_requests"]
            }
        )

        store["review_update_requests"].append(
            {
                "id": request_id,
                "review_id": review_id,
                "target_id": target_id,
                "reviewer_id": reviewer_id,
                "old_rating": old_rating,
                "old_comment": old_comment,
                "new_rating": new_rating,
                "new_comment": new_comment,
                "update_reason": update_reason,
                "status": "pending",
                "approval_message_id": None,
                "approval_channel_id": None,
                "reviewed_by": None,
                "created_at": time.strftime(
                    "%Y-%m-%d %H:%M:%S",
                    time.gmtime(),
                ),
                "reviewed_at": None,
            }
        )
        return request_id

    request_id, store = _github_update_review_store(mutator)
    _mirror_review_store_to_sqlite(store)
    return request_id


def get_review_update_request(request_id):
    store = _load_shared_review_store()
    for request in store["review_update_requests"]:
        if int(request["id"]) == int(request_id):
            return request
    return None


def get_pending_review_update_requests():
    store = _load_shared_review_store()
    return [
        request
        for request in store["review_update_requests"]
        if request["status"] == "pending"
        and request.get("approval_message_id") is not None
        and int(request.get("approval_channel_id") or 0)
        == REVIEW_UPDATE_APPROVAL_CHANNEL_ID
    ]


def set_review_update_message(request_id, channel_id, message_id):
    def mutator(store):
        for request in store["review_update_requests"]:
            if int(request["id"]) == int(request_id):
                request["approval_channel_id"] = channel_id
                request["approval_message_id"] = message_id
                return True
        raise RuntimeError("Review update request was not found.")

    _, store = _github_update_review_store(mutator)
    _mirror_review_store_to_sqlite(store)


def complete_review_update(request_id, moderator_id, approve):
    def mutator(store):
        request = next(
            (
                item for item in store["review_update_requests"]
                if int(item["id"]) == int(request_id)
                and item["status"] == "pending"
            ),
            None,
        )

        if request is None:
            return None, "already_handled"

        review = next(
            (
                item for item in store["reviews"]
                if int(item["id"]) == int(request["review_id"])
                and int(item["target_id"]) == int(request["target_id"])
                and int(item["reviewer_id"]) == int(request["reviewer_id"])
            ),
            None,
        )

        if review is None:
            request["status"] = "cancelled"
            request["reviewed_by"] = moderator_id
            request["reviewed_at"] = time.strftime(
                "%Y-%m-%d %H:%M:%S",
                time.gmtime(),
            )
            return request, "cancelled"

        status = "approved" if approve else "rejected"

        if approve:
            review["rating"] = request["new_rating"]
            review["comment"] = request["new_comment"]

        request["status"] = status
        request["reviewed_by"] = moderator_id
        request["reviewed_at"] = time.strftime(
            "%Y-%m-%d %H:%M:%S",
            time.gmtime(),
        )
        return request, status

    result, store = _github_update_review_store(mutator)
    _mirror_review_store_to_sqlite(store)
    return result


def get_reviews(target_id: int):
    store = _load_shared_review_store()
    return sorted(
        [
            review
            for review in store["reviews"]
            if int(review["target_id"]) == int(target_id)
        ],
        key=lambda review: int(review["id"]),
        reverse=True,
    )


def get_review(review_id: int):
    store = _load_shared_review_store()
    for review in store["reviews"]:
        if int(review["id"]) == int(review_id):
            return review
    return None


def delete_review(review_id: int):
    def mutator(store):
        original_count = len(store["reviews"])
        store["reviews"] = [
            review
            for review in store["reviews"]
            if int(review["id"]) != int(review_id)
        ]
        if len(store["reviews"]) == original_count:
            raise RuntimeError("Review not found.")
        return True

    _, store = _github_update_review_store(mutator)
    _mirror_review_store_to_sqlite(store)


def _aggregate_target_stats(store, target_id):
    reviews = [
        review for review in store["reviews"]
        if int(review["target_id"]) == int(target_id)
    ]
    total = len(reviews)
    approved = sum(
        1 for review in reviews
        if int(review["rating"]) in (4, 5)
    )
    neutral = sum(
        1 for review in reviews
        if int(review["rating"]) == 3
    )
    negative = sum(
        1 for review in reviews
        if int(review["rating"]) in (1, 2)
    )
    average = (
        sum(int(review["rating"]) for review in reviews) / total
        if total else 0
    )
    return {
        "total": total,
        "average": average,
        "approved": approved,
        "neutral": neutral,
        "negative": negative,
        "approval": ((approved / total) * 100) if total else 0,
    }


def get_leaderboard_liked(limit: int = 5):
    store = _load_shared_review_store()
    counts = {}
    for review in store["reviews"]:
        if int(review["rating"]) in (4, 5):
            target_id = int(review["target_id"])
            counts[target_id] = counts.get(target_id, 0) + 1

    return [
        {"target_id": target_id, "approved": count}
        for target_id, count in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[:limit]
    ]


def get_leaderboard_reviewed(limit: int = 5):
    store = _load_shared_review_store()
    counts = {}
    ratings = {}

    for review in store["reviews"]:
        target_id = int(review["target_id"])
        counts[target_id] = counts.get(target_id, 0) + 1
        ratings.setdefault(target_id, []).append(int(review["rating"]))

    rows = [
        {
            "target_id": target_id,
            "review_count": counts[target_id],
            "average_rating": sum(ratings[target_id]) / counts[target_id],
        }
        for target_id in counts
    ]

    return sorted(
        rows,
        key=lambda row: (
            -row["review_count"],
            -row["average_rating"],
            row["target_id"],
        ),
    )[:limit]


def get_leaderboard_disliked(limit: int = 5):
    store = _load_shared_review_store()
    counts = {}
    for review in store["reviews"]:
        if int(review["rating"]) in (1, 2):
            target_id = int(review["target_id"])
            counts[target_id] = counts.get(target_id, 0) + 1

    return [
        {"target_id": target_id, "disliked": count}
        for target_id, count in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[:limit]
    ]


def get_user_stats(user_id: int):
    return _aggregate_target_stats(
        _load_shared_review_store(),
        user_id,
    )

def _review_db_snapshot() -> bytes:
    """Create a consistent SQLite snapshot while review writes are paused."""
    with review_db_thread_lock:
        temp = REVIEW_DB_FILE.with_name("reviews.db.sync.tmp")
        try:
            temp.unlink(missing_ok=True)
        except TypeError:
            if temp.exists():
                temp.unlink()

        source = sqlite3.connect(
            REVIEW_DB_FILE,
            check_same_thread=False,
            timeout=30,
        )
        snapshot = sqlite3.connect(temp, timeout=30)
        try:
            source.execute("PRAGMA busy_timeout = 30000")
            snapshot.execute("PRAGMA busy_timeout = 30000")
            source.backup(snapshot)
            snapshot.commit()
        finally:
            snapshot.close()
            source.close()

        try:
            return temp.read_bytes()
        finally:
            temp.unlink(missing_ok=True)

def _github_db_url(path: str) -> str:
    encoded = "/".join(urllib.parse.quote(part, safe="") for part in path.split("/"))
    return f"{GITHUB_API_BASE}/repos/{GITHUB_REPO}/contents/{encoded}"


def _github_download_db(path: str) -> bytes | None:
    if not GITHUB_TOKEN:
        return None
    branch = urllib.parse.quote(GITHUB_DB_BRANCH, safe="")
    request = urllib.request.Request(
        f"{_github_db_url(path)}?ref={branch}",
        headers=_github_headers(),
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub database download failed ({error.code}): {body}") from error
    encoded = payload.get("content")
    if not encoded:
        raise RuntimeError("GitHub returned an empty review database.")
    return base64.b64decode("".join(str(encoded).split()))


def _github_upload_db(path: str, content: bytes, message: str) -> None:
    if not GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN is not configured.")
    branch = urllib.parse.quote(GITHUB_DB_BRANCH, safe="")
    url = _github_db_url(path)
    existing_sha = None
    try:
        with urllib.request.urlopen(
            urllib.request.Request(
                f"{url}?ref={branch}",
                headers=_github_headers(),
                method="GET",
            ),
            timeout=30,
        ) as response:
            existing_sha = json.loads(response.read().decode("utf-8")).get("sha")
    except urllib.error.HTTPError as error:
        if error.code != 404:
            body = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GitHub database lookup failed ({error.code}): {body}") from error

    body = {
        "message": message,
        "content": base64.b64encode(content).decode("ascii"),
        "branch": GITHUB_DB_BRANCH,
    }
    if existing_sha:
        body["sha"] = existing_sha

    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={**_github_headers(), "Content-Type": "application/json"},
        method="PUT",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            if response.status not in (200, 201):
                raise RuntimeError(f"GitHub database upload returned HTTP {response.status}.")
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub database upload failed ({error.code}): {body}") from error


def _review_db_has_reviews() -> bool:
    try:
        return review_db.execute("SELECT 1 FROM reviews LIMIT 1").fetchone() is not None
    except Exception:
        return False


async def restore_review_db_from_github() -> bool:
    """Restore the latest review database before any review commands run."""
    if not GITHUB_TOKEN:
        return False
    if _review_db_has_reviews():
        return False

    try:
        remote = await asyncio.to_thread(_github_download_db, GITHUB_DB_PATH)
        if not remote:
            return False

        temp = REVIEW_DB_FILE.with_name("reviews.db.restore.tmp")
        temp.write_bytes(remote)
        try:
            review_db.close()
        except Exception:
            pass
        temp.replace(REVIEW_DB_FILE)
        _reopen_review_db()
        return True
    except Exception:
        return False

async def refresh_local_review_db_from_github() -> bool:
    """Load the latest shared review database before review operations."""
    if not GITHUB_TOKEN:
        return False

    temp = REVIEW_DB_FILE.with_name("reviews.db.latest.tmp")
    try:
        remote = await asyncio.to_thread(_github_download_db, GITHUB_DB_PATH)
        if not remote:
            return False

        await asyncio.to_thread(temp.write_bytes, remote)
        if not await asyncio.to_thread(_sqlite_file_is_valid, temp):
            return False

        with review_db_thread_lock:
            try:
                review_db.close()
            except Exception:
                pass
            temp.replace(REVIEW_DB_FILE)
            _reopen_review_db()

        return True
    except Exception as error:
        print(f"Review database refresh failed: {type(error).__name__}: {error}")
        return False
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except Exception:
                pass


async def sync_review_db_to_github() -> tuple[bool, str]:
    """Export the shared review store to reviews.db and then to GitHub."""
    if not GITHUB_TOKEN:
        return False, "GITHUB_TOKEN is not configured."

    try:
        store = await asyncio.to_thread(_load_shared_review_store)
        await asyncio.to_thread(_mirror_review_store_to_sqlite, store)
        snapshot = await asyncio.to_thread(_review_db_snapshot)
        await asyncio.to_thread(
            _github_upload_db,
            GITHUB_DB_PATH,
            snapshot,
            "Sync review database",
        )
        return True, ""
    except Exception as error:
        return False, str(error)


review_db_lock = asyncio.Lock()


async def sync_review_db_to_github_locked() -> tuple[bool, str]:
    """Serialize review database uploads so background syncs cannot overlap."""
    async with review_db_lock:
        return await sync_review_db_to_github()


# =========================
# BOT SETUP
# =========================

intents = discord.Intents.default()
intents.members = True
intents.guilds = True
intents.message_content = True

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)
commands_synced = False
user_blacklists_synced = False
review_update_views_registered = False
review_db_restore_checked = False


# =========================
# MESSAGE DELETION HELPER
# =========================

async def delete_original_message(message: discord.Message) -> bool:
    """Delete a user message, retrying once if Discord has not cached it."""
    guild = message.guild
    bot_member = guild.me if guild is not None else None
    channel_permissions = (
        message.channel.permissions_for(bot_member)
        if bot_member is not None
        else None
    )

    if bot_member is not None:
        pass

    try:
        await message.delete()
        return True
    except discord.NotFound as e:
        return True
    except discord.Forbidden as e:
        pass
    except discord.HTTPException as e:
        pass
    except Exception as e:
        pass


    try:
        fresh_message = await message.channel.fetch_message(message.id)
        pass
    except discord.NotFound as e:
        return True
    except discord.Forbidden as e:
        return False
    except discord.HTTPException as e:
        return False
    except Exception as e:
        return False

    try:
        await fresh_message.delete()
        return True
    except discord.NotFound as e:
        return True
    except discord.Forbidden as e:
        return False
    except discord.HTTPException as e:
        return False
    except Exception as e:
        return False


# =========================
# PROXY MESSAGE HELPERS
# =========================

proxy_requests: dict[int, dict] = {}


def is_proxy_bot_message(message: discord.Message) -> bool:
    """Return True when a message came from a configured proxy bot."""
    author = message.author
    if author.id in PROXY_BOT_IDS:
        return True

    author_name = (
        getattr(author, "name", "")
        or getattr(author, "display_name", "")
        or ""
    ).strip().lower()

    return bool(author.bot and author_name in PROXY_BOT_NAMES)


def remember_proxy_request(message: discord.Message, content: str) -> None:
    """Remember a target using a common proxy-bot command.

    We only remember requests from people already selected for UWUIFY/HOODIFY.
    The next matching proxy-bot response in this channel is then relayed
    through the appropriate transformer.
    """
    if message.author.bot or not content:
        return

    mode_match = re.match(
        r"^\s*[,!](uwu(?:ify)?|hood(?:ify)?)\b",
        content,
        flags=re.IGNORECASE,
    )
    if mode_match is None:
        return

    mode = mode_match.group(1).lower()
    if mode.startswith("uwu"):
        if message.author.id in uwu_user_blacklist:
            return
        if message.author.id not in uwu_targets.get(message.channel.id, set()):
            return
        mode = "uwu"
    else:
        if message.author.id in hood_user_blacklist:
            return
        if message.author.id not in hood_targets.get(message.channel.id, set()):
            return
        mode = "hood"

    proxy_requests[message.channel.id] = {
        "target_id": message.author.id,
        "mode": mode,
        "expires": asyncio.get_running_loop().time() + PROXY_REQUEST_TTL_SECONDS,
    }


async def handle_proxy_message(message: discord.Message) -> bool:
    """Relay the next configured proxy-bot response for an active target."""
    if not is_proxy_bot_message(message):
        return False

    request = proxy_requests.get(message.channel.id)
    if request is None:
        return False

    if request["expires"] < asyncio.get_running_loop().time():
        proxy_requests.pop(message.channel.id, None)
        return False

    target_id = request["target_id"]
    mode = request["mode"]

    target = message.guild.get_member(target_id) if message.guild else None
    if target is None:
        proxy_requests.pop(message.channel.id, None)
        return False

    try:
        if mode == "uwu":
            if target.id in uwu_user_blacklist:
                proxy_requests.pop(message.channel.id, None)
                return False
            if target.id not in uwu_targets.get(message.channel.id, set()):
                proxy_requests.pop(message.channel.id, None)
                return False

            await send_uwu_message(
                message.channel,
                target,
                message.content,
            )

        elif mode == "hood":
            if target.id in hood_user_blacklist:
                proxy_requests.pop(message.channel.id, None)
                return False
            if target.id not in hood_targets.get(message.channel.id, set()):
                proxy_requests.pop(message.channel.id, None)
                return False

            await send_hood_message(
                message.channel,
                target,
                message.content,
            )

        else:
            proxy_requests.pop(message.channel.id, None)
            return False

        proxy_requests.pop(message.channel.id, None)

        # Replace the proxy bot's original output only after the transformed
        # webhook message was successfully sent.
        await delete_original_message(message)
        return True

    except (UwuMessageBlocked, HoodMessageBlocked):
        # Leave the proxy output alone when the configured content blacklist
        # blocks the transformed message.
        proxy_requests.pop(message.channel.id, None)
        return False
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        return False
    except Exception:
        return False


# =========================
# PREFIX COMMANDS
# =========================

# =========================
# PREFIX BLACKLIST HELPERS
# =========================

def prefix_blacklist_allowed(message: discord.Message) -> bool:
    """Return True when a prefix blacklist command may be used."""
    if message.guild is None or message.guild.id != MAIN_SERVER:
        return False

    if BLACKLIST_ALLOWED_ROLE_ID == 0:
        return False

    return (
        isinstance(message.author, discord.Member)
        and BLACKLIST_ALLOWED_ROLE_ID in {role.id for role in message.author.roles}
    )


@bot.event
async def on_message(message: discord.Message):
    """Handle comma-prefix commands and automatic UWU replacement.

    Prefix command messages are intentionally kept instead of being deleted.
    Only normal messages from active UWU targets are replaced/deleted.
    """
    proxy_message = is_proxy_bot_message(message)

    # Ignore normal bot/webhook messages, but allow configured proxy-bot output
    # through the UWUIFY/HOODIFY enforcement path.
    if message.author.bot and not proxy_message:
        return

    if message.webhook_id is not None and not proxy_message:
        return

    if not isinstance(message.channel, discord.TextChannel):
        return

    content = message.content.strip()

    # Parse the readable spaced prefix syntax once so all handlers can use it.
    spaced_parts = content.split(maxsplit=2)
    prefix_command = content.lower().split(maxsplit=1)[0]
    if prefix_command in {
        ",uwuify", ",unuwuify", ",hoodify", ",unhoodify", ",uwu", ",hood"
    } and uwu_hoodify_user_is_banned(message.author):
        await message.reply(
            "❌ You are banned from using UWUIFY and HOODIFY.",
            mention_author=False,
        )
        return

    # Spaced moderation commands.
    # Combined Textify prefix commands.
    # ,textify blacklist @user
    # ,textify unblacklist @user
    # ,textify status @user
    # ,textify ban @user
    # ,textify unban @user
    if len(spaced_parts) >= 2 and spaced_parts[0].lower() == ",textify":
        textify_action = spaced_parts[1].lower()

        if textify_action in {"blacklist", "unblacklist", "status"}:
            if not prefix_blacklist_allowed(message):
                await message.reply("❌ You do not have permission to use this command.", mention_author=False)
                return
            if not message.mentions:
                await message.reply(
                    "Usage: ,textify blacklist @user | ,textify unblacklist @user | ,textify status @user",
                    mention_author=False,
                )
                return

            target = message.mentions[0]
            if textify_action == "blacklist":
                uwu_user_blacklist.add(target.id)
                hood_user_blacklist.add(target.id)
                await save_user_blacklist(TEXTIFY_BLACKLIST_FILE, uwu_user_blacklist)
                await save_user_blacklist(TEXTIFY_BLACKLIST_FILE, hood_user_blacklist)
                removed = (
                    await disable_uwu_for_user(target.id)
                    + await disable_hood_for_user(target.id)
                )
                response = (
                    f"✅ {target.mention} is now blacklisted from both UWUIFY and HOODIFY."
                    + (f" Removed them from **{removed}** active mode(s)." if removed else "")
                )
            elif textify_action == "unblacklist":
                if target.id not in uwu_user_blacklist and target.id not in hood_user_blacklist:
                    response = f"ℹ️ {target.mention} is not currently blacklisted from UWUIFY or HOODIFY."
                else:
                    uwu_user_blacklist.discard(target.id)
                    hood_user_blacklist.discard(target.id)
                    await save_user_blacklist(TEXTIFY_BLACKLIST_FILE, uwu_user_blacklist)
                    await save_user_blacklist(TEXTIFY_BLACKLIST_FILE, hood_user_blacklist)
                    response = f"✅ {target.mention} can use UWUIFY and HOODIFY again."
            else:
                uwu_blacklisted = target.id in uwu_user_blacklist
                hood_blacklisted = target.id in hood_user_blacklist
                if uwu_blacklisted and hood_blacklisted:
                    status = "blacklisted from both UWUIFY and HOODIFY"
                elif uwu_blacklisted:
                    status = "blacklisted from UWUIFY"
                elif hood_blacklisted:
                    status = "blacklisted from HOODIFY"
                else:
                    status = "not blacklisted from UWUIFY or HOODIFY"
                response = f"{target.mention} is **{status}**."

            await message.reply(response, mention_author=False)
            return

        if textify_action in {"ban", "unban"}:
            if not prefix_blacklist_allowed(message):
                await message.reply("❌ You do not have permission to use this command.", mention_author=False)
                return
            if not message.mentions:
                await message.reply(
                    "Usage: ,textify ban @user | ,textify unban @user",
                    mention_author=False,
                )
                return

            target = message.mentions[0]
            if textify_action == "ban":
                if target.id in uwu_hoodify_ban:
                    await message.reply(
                        f"ℹ️ {target.mention} is already banned from running UWUIFY and HOODIFY.",
                        mention_author=False,
                    )
                    return
                uwu_hoodify_ban.add(target.id)
                await save_user_blacklist(TEXTIFY_BAN_FILE, uwu_hoodify_ban)
                removed = (
                    await disable_uwu_for_user(target.id)
                    + await disable_hood_for_user(target.id)
                )
                response = f"✅ {target.mention} is now banned from running UWUIFY and HOODIFY."
                if removed:
                    response += f" Removed {removed} active mode(s)."
            else:
                if target.id not in uwu_hoodify_ban:
                    response = f"ℹ️ {target.mention} is not currently banned from running UWUIFY and HOODIFY."
                else:
                    uwu_hoodify_ban.remove(target.id)
                    await save_user_blacklist(TEXTIFY_BAN_FILE, uwu_hoodify_ban)
                    response = f"✅ {target.mention} can run UWUIFY and HOODIFY again."

            await message.reply(response, mention_author=False)
            return

    if spaced_parts and spaced_parts[0].lower() == ",strike":
        if not (
            message.guild is not None
            and message.guild.id == MAIN_SERVER
            and isinstance(message.author, discord.Member)
            and staff_strike_command_allowed(message.author)
        ):
            await message.reply(
                "❌ You do not have permission to use the staff strike system.",
                mention_author=False,
            )
            return

        strike_parts = content.split(maxsplit=3)
        if len(strike_parts) < 4:
            await message.reply(
                "Usage: ,strike @user 7 false mute",
                mention_author=False,
            )
            return

        target = message.mentions[0] if message.mentions else None
        target_token = strike_parts[1].strip("<@!>")

        if target is None:
            try:
                target_id = int(target_token)
            except ValueError:
                target_id = 0

            if target_id:
                target = message.guild.get_member(target_id)
                if target is None:
                    try:
                        target = await message.guild.fetch_member(target_id)
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        target = None

        if target is None:
            await message.reply(
                "❌ Mention a valid member or provide their user ID.",
                mention_author=False,
            )
            return

        days = parse_staff_strike_duration(strike_parts[2])
        if days is None:
            await message.reply(
                "❌ Strike duration must be between **7d and 40d**.",
                mention_author=False,
            )
            return

        reason = strike_parts[3].strip()
        if not reason:
            await message.reply(
                "❌ You must provide a reason for the strike.",
                mention_author=False,
            )
            return

        expired = prune_expired_staff_strikes()
        if expired:
            await handle_expired_staff_strikes(expired)
            await save_staff_strikes()

        current_staff_info = get_staff_role_for_member(target)
        if current_staff_info is None:
            await message.reply(
                "❌ The selected member does not have a configured staff role.",
                mention_author=False,
            )
            return

        original_role_id = get_original_staff_role_id(target.id) or current_staff_info[1].id

        strike = create_staff_strike(
            user_id=target.id,
            reason=reason,
            days=days,
            issued_by=message.author.id,
            original_staff_role_id=original_role_id,
        )
        active_count = len(get_user_staff_strikes(target.id))
        consequence = await apply_staff_strike_consequences(
            target,
            active_count=active_count,
            log_two_strikes=(active_count == 2),
            log_three_strikes=(active_count >= 3),
        )
        synced = await save_staff_strikes()

        await send_staff_strike_log(
            STAFF_STRIKE_ACTIVE_CHANNEL_ID,
            format_staff_strike(target, strike),
        )

        response = (
            format_staff_strike(target, strike)
            + f"\n\nActive strikes: **{active_count}**"
        )
        if consequence is not None:
            old_role, new_role = consequence
            response += f"\nRole action: **{old_role} → {new_role}**"
        if not synced:
            response += "\n⚠️ GitHub sync failed; the strike was saved locally."

        await message.reply(response, mention_author=False)
        return

    if spaced_parts and spaced_parts[0].lower() == ",removestrike":
        if not (
            message.guild is not None
            and message.guild.id == MAIN_SERVER
            and isinstance(message.author, discord.Member)
            and staff_strike_command_allowed(message.author)
        ):
            await message.reply(
                "❌ You do not have permission to use the staff strike system.",
                mention_author=False,
            )
            return

        strike_parts = content.split()
        if len(strike_parts) != 3:
            await message.reply(
                "Usage: ,removestrike @user 1",
                mention_author=False,
            )
            return

        target = message.mentions[0] if message.mentions else None
        target_token = strike_parts[1].strip("<@!>")

        if target is None:
            try:
                target_id = int(target_token)
            except ValueError:
                target_id = 0

            if target_id:
                target = message.guild.get_member(target_id)
                if target is None:
                    try:
                        target = await message.guild.fetch_member(target_id)
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        target = None

        if target is None:
            await message.reply(
                "❌ Mention a valid member or provide their user ID.",
                mention_author=False,
            )
            return

        try:
            strike_number = int(strike_parts[2])
        except ValueError:
            strike_number = 0

        if strike_number < 1:
            await message.reply(
                "❌ Strike number must be 1 or higher.",
                mention_author=False,
            )
            return

        expired = prune_expired_staff_strikes()
        if expired:
            await handle_expired_staff_strikes(expired)
            await save_staff_strikes()

        matching = next(
            (
                strike
                for strike in staff_strikes
                if int(strike.get("user_id", 0)) == target.id
                and int(strike.get("strike_number", 0)) == strike_number
            ),
            None,
        )

        if matching is None:
            await message.reply(
                f"❌ {target.mention} does not have an active strike #{strike_number}.",
                mention_author=False,
            )
            return

        original_role_id = (
            int(matching["original_staff_role_id"])
            if matching.get("original_staff_role_id") is not None
            else get_original_staff_role_id(target.id)
        )

        before_info = get_staff_role_for_member(target)
        before_role_name = before_info[0] if before_info is not None else (
            "Suspended" if len(get_user_staff_strikes(target.id)) >= 3 else "No Staff Role"
        )

        staff_strikes.remove(matching)

        active_count = len(get_user_staff_strikes(target.id))
        consequence = await apply_staff_strike_consequences(
            target,
            active_count=active_count,
            original_role_id_override=original_role_id,
        )
        synced = await save_staff_strikes()

        response = (
            f"✅ Removed strike #{strike_number} from "
            f"{target.mention} / {target.id}.\n\n"
            f"Active strikes: **{active_count}**"
        )

        if consequence is not None:
            old_role, new_role = consequence
            response += f"\nRole action: **{old_role} → {new_role}**"
        elif original_role_id is not None:
            refreshed = await resolve_main_guild_member(target.id) or target
            after_info = get_staff_role_for_member(refreshed)
            after_role_name = after_info[0] if after_info is not None else (
                "Suspended" if active_count >= 3 else "No Staff Role"
            )
            if before_role_name != after_role_name:
                response += f"\nRole action: **{before_role_name} → {after_role_name}**"

        if not synced:
            response += "\n⚠️ GitHub sync failed; the strike removal was saved locally."

        await message.reply(response, mention_author=False)
        return

    if len(spaced_parts) >= 2 and spaced_parts[0].lower() == ",reviewblacklist" and spaced_parts[1].lower() in {"add", "remove", "status"}:
        if not prefix_blacklist_allowed(message):
            await message.reply("❌ You do not have permission to use this review blacklist command.", mention_author=False)
            return
        if not message.mentions:
            await message.reply(
                "Usage: ,reviewblacklist add @user | ,reviewblacklist remove @user | ,reviewblacklist status @user",
                mention_author=False,
            )
            return

        target = message.mentions[0]
        action = spaced_parts[1].lower()

        if action == "add":
            if target.id in review_blacklist:
                response = f"ℹ️ {target.mention} is already blacklisted from reviews."
            else:
                review_blacklist.add(target.id)
                synced = await save_review_blacklist()
                response = f"✅ {target.mention} can no longer submit or update reviews."
                if not synced:
                    response += f"\n⚠️ GitHub sync failed: {github_blacklist_sync_error}"
                await log_review_event(
                    "Review Blacklist Updated",
                    f"{message.author.mention} blacklisted {target.mention} from the review system.",
                    fields=[
                        ("Member", f"{target.mention} / {target.id}", True),
                        ("Changed By", f"{message.author.mention} / {message.author.id}", True),
                    ],
                    color=discord.Color.red(),
                )
        elif action == "remove":
            if target.id not in review_blacklist:
                response = f"ℹ️ {target.mention} is not currently blacklisted from reviews."
            else:
                review_blacklist.remove(target.id)
                synced = await save_review_blacklist()
                response = f"✅ {target.mention} can submit and update reviews again."
                if not synced:
                    response += f"\n⚠️ GitHub sync failed: {github_blacklist_sync_error}"
                await log_review_event(
                    "Review Blacklist Updated",
                    f"{message.author.mention} removed {target.mention} from the review blacklist.",
                    fields=[
                        ("Member", f"{target.mention} / {target.id}", True),
                        ("Changed By", f"{message.author.mention} / {message.author.id}", True),
                    ],
                    color=discord.Color.green(),
                )
        else:
            status = target.id in review_blacklist
            response = f"ℹ️ {target.mention} is **{'blacklisted' if status else 'not blacklisted'}** from the review system."

        await message.reply(response, mention_author=False)
        return

    if len(spaced_parts) >= 2 and spaced_parts[0].lower() == ",blacklist" and spaced_parts[1].lower() in {"add", "remove", "status"}:
        if not prefix_blacklist_allowed(message):
            await message.reply("❌ You do not have permission to use this blacklist command.", mention_author=False)
            return
        if not message.mentions:
            await message.reply("Usage: ,blacklist add @user | ,blacklist remove @user | ,blacklist status @user", mention_author=False)
            return
        target = message.mentions[0]
        action = spaced_parts[1].lower()
        if action == "add":
            second_role_blacklist.add(target.id)
            save_blacklist(second_role_blacklist)
            await message.reply(f"✅ {target.mention} has been added to the second-role blacklist.", mention_author=False)
        elif action == "remove":
            if target.id not in second_role_blacklist:
                response = f"ℹ️ {target.mention} is not currently on the second-role blacklist."
            else:
                second_role_blacklist.remove(target.id)
                save_blacklist(second_role_blacklist)
                response = f"✅ {target.mention} has been removed from the second-role blacklist."
            await message.reply(response, mention_author=False)
        else:
            blacklisted = target.id in second_role_blacklist
            await message.reply(f"{target.mention} is **{'blacklisted' if blacklisted else 'not blacklisted'}** for the second main-server role.", mention_author=False)
        return

    if len(spaced_parts) >= 3 and spaced_parts[0].lower() == ",uwuify" and spaced_parts[1].lower() == "hoodify" and spaced_parts[2].lower() in {"ban", "unban"}:
        if not prefix_blacklist_allowed(message):
            await message.reply("❌ You do not have permission to use this command.", mention_author=False)
            return
        if not message.mentions:
            await message.reply("Usage: ,uwuify hoodify ban @user | ,uwuify hoodify unban @user", mention_author=False)
            return
        target = message.mentions[0]
        action = spaced_parts[2].lower()
        if action == "ban":
            if target.id in uwu_hoodify_ban:
                await message.reply(f"ℹ️ {target.mention} is already banned from UWUIFY and HOODIFY.", mention_author=False)
                return
            uwu_hoodify_ban.add(target.id)
            await save_user_blacklist(TEXTIFY_BAN_FILE, uwu_hoodify_ban)
            removed_uwu = await disable_uwu_for_user(target.id)
            removed_hood = await disable_hood_for_user(target.id)
            removed = removed_uwu + removed_hood
            response = f"✅ {target.mention} is now banned from running UWUIFY and HOODIFY."
            if removed:
                response += f" Removed {removed} active mode(s)."
            await message.reply(response, mention_author=False)
        else:
            if target.id not in uwu_hoodify_ban:
                await message.reply(f"ℹ️ {target.mention} is not currently banned from UWUIFY and HOODIFY.", mention_author=False)
                return
            uwu_hoodify_ban.remove(target.id)
            await save_user_blacklist(TEXTIFY_BAN_FILE, uwu_hoodify_ban)
            await message.reply(f"✅ {target.mention} can run UWUIFY and HOODIFY again.", mention_author=False)
        return
    # Textify is the combined UWUIFY + HOODIFY blacklist interface.

    if proxy_message:
        handled = await handle_proxy_message(message)
        if handled:
            return
        return

    # Enforce active transformations BEFORE parsing any prefix command.
    # This prevents an active target from bypassing UWU/HOODIFY by sending
    # command-looking text such as ",uwuify @user text".
    hood_target_ids = hood_targets.get(message.channel.id, set())
    uwu_target_ids = uwu_targets.get(message.channel.id, set())

    if message.author.id in hood_target_ids and content:
        if message.author.id in hood_user_blacklist:
            await disable_hood_for_user(message.author.id)
            return
        bot_member = message.guild.me if message.guild is not None else None
        if bot_member is None or not message.channel.permissions_for(bot_member).manage_messages:
            return
        try:
            await send_hood_message(message.channel, message.author, content)
            await delete_original_message(message)
        except HoodMessageBlocked:
            pass
        except Exception:
            pass
        return

    if message.author.id in uwu_target_ids and content:
        if message.author.id in uwu_user_blacklist:
            await disable_uwu_for_user(message.author.id)
            return
        bot_member = message.guild.me if message.guild is not None else None
        if bot_member is None or not message.channel.permissions_for(bot_member).manage_messages:
            return
        try:
            await send_uwu_message(message.channel, message.author, content)
            await delete_original_message(message)
        except UwuMessageBlocked:
            pass
        except Exception:
            pass
        return

    # Only non-target users continue into the command parser.
    remember_proxy_request(message, content)

    # Translate readable mode/count prefixes to the existing command parser.
    if len(spaced_parts) >= 2:
        spaced_root = spaced_parts[0].lower()
        spaced_action = spaced_parts[1].lower()
        if spaced_root == ",uwu" and spaced_action == "count":
            content = ",uwucount"
        elif spaced_root == ",uwu" and spaced_action in {"on", "off"}:
            rest = spaced_parts[2] if len(spaced_parts) >= 3 else ""
            content = ",uwuify " + spaced_action + (f" {rest}" if rest else "")
        elif spaced_root == ",hood" and spaced_action == "count":
            content = ",hoodcount"
        elif spaced_root == ",hood" and spaced_action in {"on", "off"}:
            rest = spaced_parts[2] if len(spaced_parts) >= 3 else ""
            content = ",hoodify " + spaced_action + (f" {rest}" if rest else "")

    # Prefix ping.
    if content.lower() == ",ping":
        latency_ms = round(bot.latency * 1000)
        await message.reply(f"🏓 Pong! `{latency_ms}ms`", mention_author=False)
        return

    # Readable spaced prefix aliases are parsed above.

    # Prefix command to show how many people are currently being UWUified.
    if content.lower() == ",uwucount":
        global_count = get_active_uwu_target_count()
        channel_count = len(uwu_targets.get(message.channel.id, set()))
        await message.reply(
            f"🩷 **UWU count**\n"
            f"Global: **{global_count}/{MAX_ACTIVE_UWU_TARGETS}** people\n"
            f"This channel: **{channel_count}** people",
            mention_author=False,
        )
        return

    # ,unuwuify @user disables UWUIFY for only that member.
    if content.lower().startswith(",unuwuify") and message.mentions:
        if not uwu_user_is_whitelisted(message.author):
            await message.reply(
                "❌ You need one of the allowed UWU roles to use this command.",
                mention_author=False,
            )
            return

        target = message.mentions[0]
        disabled_count = await disable_uwu_for_user(target.id)
        await message.reply(
            f"✅ UWU mode disabled for {target.mention}. "
            f"Removed them from **{disabled_count}** active channel(s).",
            mention_author=False,
        )
        return

    # ,unuwuify @user disables UWUIFY for that member across all active channels.
    if content.lower().startswith(",unuwuify"):
        if not uwu_user_is_whitelisted(message.author):
            await message.reply(
                "❌ You need one of the allowed UWU roles to use this command.",
                mention_author=False,
            )
            return

        if not message.mentions:
            await message.reply(
                "Usage: ,unuwuify @user",
                mention_author=False,
            )
            return

        target = message.mentions[0]
        disabled_count = await disable_uwu_for_user(target.id)
        await message.reply(
            f"✅ UWU mode disabled for {target.mention}. "
            f"Removed them from **{disabled_count}** active channel(s).",
            mention_author=False,
        )
        return

    # Prefix UWU setup/disable command.
    if content.lower().startswith(",uwuify"):
        if not uwu_user_is_whitelisted(message.author):
            try:
                await message.reply(
                    "❌ You need one of the allowed UWU roles to use this command.",
                    mention_author=False,
                )
            except (discord.Forbidden, discord.HTTPException):
                pass
            return

        parts = content.split(maxsplit=2)

        if len(parts) >= 2 and parts[1].lower() == "off":
            # ",uwuify off @user" removes only that target.
            if len(parts) >= 3 and message.mentions:
                target = message.mentions[0]
                channel_targets = uwu_targets.get(message.channel.id, set())

                if target.id in channel_targets:
                    channel_targets.remove(target.id)

                    # Keep the webhook alive for other targets in this channel.
                    if channel_targets:
                        response = f"✅ {target.mention} is no longer being UWUified in this channel."
                    else:
                        await disable_uwu_target(message.channel.id)
                        response = "✅ Uwu mode disabled for this channel."
                else:
                    response = f"ℹ️ {target.mention} was not being UWUified in this channel."

                await message.reply(response, mention_author=False)
                return

            # ",uwuify off" disables every target in this channel.
            disabled = await disable_uwu_target(message.channel.id)
            response = (
                "✅ Uwu mode disabled for this channel."
                if disabled
                else "ℹ️ Uwu mode is not active in this channel."
            )
            await message.reply(response, mention_author=False)
            return

        if len(parts) < 2 or not message.mentions:
            await message.reply(
                "Usage: `,uwuify @user`, `,uwuify off`, or `,uwuify off @user`",
                mention_author=False,
            )
            return

        target = message.mentions[0]

        bot_member = message.guild.me if message.guild is not None else None
        if bot_member is None or not message.channel.permissions_for(bot_member).manage_messages:
            await message.reply(
                "❌ I need **Manage Messages** permission in this channel to replace messages.",
                mention_author=False,
            )
            return

        try:
            await set_uwu_target(message.channel, target)

            if len(parts) >= 3:
                try:
                    await send_uwu_message(message.channel, target, parts[2])
                except UwuMessageBlocked as blocked_error:
                    await message.reply(
                        f"❌ That message was not sent through the UWU webhook because it contains a blacklisted word/phrase: `{blocked_error}`",
                        mention_author=False,
                    )

            await message.reply(
                f"✅ Uwu mode is active for {target.mention} in this channel. "
                f"Active people: **{get_active_uwu_target_count()}/{MAX_ACTIVE_UWU_TARGETS}**.",
                mention_author=False,
            )
        except UwuUserBlacklisted:
            await message.reply(
                f"❌ {target.mention} is blacklisted from using UWUIFY.",
                mention_author=False,
            )
        except UserBlacklistStorageUnavailable as error:
            await message.reply(
                "❌ I could not verify the UWUIFY blacklist from GitHub, "
                f"so I will not activate this target. Error: `{error}`",
                mention_author=False,
            )
        except UwuTargetLimitReached:
            await message.reply(
                f"❌ The global limit of {MAX_ACTIVE_UWU_TARGETS} UWUified people has been reached. "+
                "Use `,unuwuify @user` or `/unuwuify @user` to disable UWU for one member, or wait for a slot to expire.",
                mention_author=False,
            )
        except discord.Forbidden:
            await message.reply(
                "❌ I need **Manage Messages** and **Manage Webhooks** permission in this channel/server.",
                mention_author=False,
            )
        except discord.HTTPException as e:
            await message.reply(
                f"❌ Discord rejected the uwu webhook request: `{e}`",
                mention_author=False,
            )
        except Exception as e:
            await message.reply(
                f"❌ Uwu command failed: `{e}`",
                mention_author=False,
            )
        return

    # -------------------------
    # HOODIFY COMMANDS / AUTOMATIC MODE
    # -------------------------
    if content.lower() in {",hoodcount", ",hood count"}:
        global_count = get_active_hood_target_count()
        channel_count = len(hood_targets.get(message.channel.id, set()))
        await message.reply(
            f"🖤 **HOODIFY count**\n"
            f"Global: **{global_count}/{MAX_ACTIVE_HOOD_TARGETS}** people\n"
            f"This channel: **{channel_count}** people",
            mention_author=False,
        )
        return

    # ,unhoodify @user disables HOODIFY for only that member.
    if content.lower().startswith(",unhoodify") and message.mentions:
        if not hood_user_is_whitelisted(message.author):
            await message.reply(
                "❌ You need one of the allowed HOODIFY roles to use this command.",
                mention_author=False,
            )
            return

        target = message.mentions[0]
        disabled_count = await disable_hood_for_user(target.id)
        await message.reply(
            f"✅ HOODIFY disabled for {target.mention}. "
            f"Removed them from **{disabled_count}** active channel(s).",
            mention_author=False,
        )
        return

    # ,unhoodify @user disables HOODIFY for that member across all active channels.
    if content.lower().startswith(",unhoodify"):
        if not hood_user_is_whitelisted(message.author):
            await message.reply(
                "❌ You need one of the allowed HOODIFY roles to use this command.",
                mention_author=False,
            )
            return

        if not message.mentions:
            await message.reply(
                "Usage: ,unhoodify @user",
                mention_author=False,
            )
            return

        target = message.mentions[0]
        disabled_count = await disable_hood_for_user(target.id)
        await message.reply(
            f"✅ HOODIFY disabled for {target.mention}. "
            f"Removed them from **{disabled_count}** active channel(s).",
            mention_author=False,
        )
        return

    if content.lower().startswith(",hoodify"):
        if not hood_user_is_whitelisted(message.author):
            try:
                await message.reply(
                    "❌ You need one of the allowed HOODIFY roles to use this command.",
                    mention_author=False,
                )
            except (discord.Forbidden, discord.HTTPException):
                pass
            return

        parts = content.split(maxsplit=2)

        if len(parts) >= 2 and parts[1].lower() == "off":
            if len(parts) >= 3 and message.mentions:
                target = message.mentions[0]
                channel_targets = hood_targets.get(message.channel.id, set())

                if target.id in channel_targets:
                    channel_targets.remove(target.id)
                    if channel_targets:
                        response = (
                            f"✅ {target.mention} is no longer being HOODIFIED "
                            "in this channel."
                        )
                    else:
                        await disable_hood_target(message.channel.id)
                        response = "✅ Hoodify mode disabled for this channel."
                else:
                    response = (
                        f"ℹ️ {target.mention} was not being HOODIFIED "
                        "in this channel."
                    )

                await message.reply(response, mention_author=False)
                return

            disabled = await disable_hood_target(message.channel.id)
            response = (
                "✅ Hoodify mode disabled for this channel."
                if disabled
                else "ℹ️ Hoodify mode is not active in this channel."
            )
            await message.reply(response, mention_author=False)
            return

        if len(parts) < 2 or not message.mentions:
            await message.reply(
                "Usage: `,hoodify @user`, `,hoodify off`, or "
                "`,hoodify off @user`",
                mention_author=False,
            )
            return

        target = message.mentions[0]
        bot_member = message.guild.me if message.guild is not None else None
        if bot_member is None or not message.channel.permissions_for(bot_member).manage_messages:
            await message.reply(
                "❌ I need **Manage Messages** permission in this channel "
                "to replace messages.",
                mention_author=False,
            )
            return

        try:
            await set_hood_target(message.channel, target)

            if len(parts) >= 3:
                try:
                    await send_hood_message(message.channel, target, parts[2])
                except HoodMessageBlocked as blocked_error:
                    await message.reply(
                        f"❌ That message was not sent through the HOODIFY webhook "
                        f"because it contains a blacklisted word/phrase: "
                        f"`{blocked_error}`",
                        mention_author=False,
                    )
                    return

            await message.reply(
                f"✅ HOODIFY is active for {target.mention} in this channel. "
                f"Active people: **{get_active_hood_target_count()}/"
                f"{MAX_ACTIVE_HOOD_TARGETS}**.",
                mention_author=False,
            )
        except HoodUserBlacklisted:
            await message.reply(
                f"❌ {target.mention} is blacklisted from using HOODIFY.",
                mention_author=False,
            )
        except UserBlacklistStorageUnavailable as error:
            await message.reply(
                "❌ I could not verify the HOODIFY blacklist from GitHub, "
                f"so I will not activate this target. Error: `{error}`",
                mention_author=False,
            )
        except HoodTargetLimitReached:
            await message.reply(
                f"❌ The global limit of {MAX_ACTIVE_HOOD_TARGETS} HOODIFIED "
                "people has been reached. Use `,unhoodify` or `/unhoodify` "
                "to disable all HOODIFY modes.",
                mention_author=False,
            )
        except discord.Forbidden:
            await message.reply(
                "❌ I need **Manage Messages** and **Manage Webhooks** "
                "permission in this channel/server.",
                mention_author=False,
            )
        except discord.HTTPException as e:
            await message.reply(
                f"❌ Discord rejected the HOODIFY webhook request: `{e}`",
                mention_author=False,
            )
        except Exception as e:
            await message.reply(
                f"❌ HOODIFY command failed: `{e}`",
                mention_author=False,
            )
        return

# =========================
# WEBHOOK
# =========================

async def send_webhook(
    webhook_url,
    title,
    description,
    color=discord.Color.blurple(),
    fields=None,
):
    """Sends an embed to the specified Discord webhook."""

    if not webhook_url:
        return

    try:
        webhook = discord.Webhook.from_url(
            webhook_url,
            session=bot.http._HTTPClient__session,
        )

        embed = discord.Embed(
            title=title,
            description=description,
            color=color,
            timestamp=discord.utils.utcnow(),
        )

        if fields:
            for name, value, inline in fields:
                embed.add_field(
                    name=name,
                    value=value,
                    inline=inline,
                )

        embed.set_footer(text="Tag Server Bot")

        await webhook.send(
            embed=embed,
            username="",
            # Never let bot-created webhook messages ping roles, @everyone, or @here.
            allowed_mentions=discord.AllowedMentions(
                everyone=False,
                roles=False,
                users=True,
                replied_user=False,
            ),
            wait=False,
        )

    except Exception as e:
        pass


# =========================
# SLASH COMMAND PERMISSION
# =========================

async def blacklist_command_check(interaction: discord.Interaction) -> bool:
    """Only allow the configured role to use blacklist commands in the main server."""
    if interaction.guild_id != MAIN_SERVER:
        raise app_commands.CheckFailure("This command can only be used in the main server.")

    if BLACKLIST_ALLOWED_ROLE_ID == 0:
        raise app_commands.CheckFailure("BLACKLIST_ALLOWED_ROLE_ID is not configured.")

    if not isinstance(interaction.user, discord.Member):
        raise app_commands.CheckFailure("Could not verify your server roles.")

    if BLACKLIST_ALLOWED_ROLE_ID not in {role.id for role in interaction.user.roles}:
        raise app_commands.CheckFailure(
            "You do not have the role required to use this command."
        )

    return True


def uwu_hoodify_user_is_banned(member: discord.Member | discord.User) -> bool:
    return member.id in uwu_hoodify_ban


# =========================
# SLASH COMMANDS
# =========================

# Slash-command groups. Discord does not allow literal spaces in command names,
# so grouped commands provide the visible spaced layout.
# Legacy groups kept only for backwards-compatible internal handlers.
uwuify_group = app_commands.Group(name="legacy_uwuify", description="Legacy")
uwuify_hoodify_group = app_commands.Group(name="legacy_hoodify", description="Legacy")
uwu_group = app_commands.Group(name="legacy_uwu", description="Legacy")
hood_group = app_commands.Group(name="legacy_hood", description="Legacy")
blacklist_group = app_commands.Group(name="blacklist", description="Manage the second-role blacklist")
textify_group = app_commands.Group(name="textify", description="Manage UWUIFY and HOODIFY controls")
# ============================================================
# REVIEW BLACKLIST HELPERS
# ============================================================

def review_user_is_blacklisted(user_id: int) -> bool:
    return user_id in review_blacklist


async def save_review_blacklist() -> bool:
    return await save_user_blacklist(REVIEW_BLACKLIST_FILE, review_blacklist)


async def review_blacklist_command_check(interaction: discord.Interaction) -> bool:
    if interaction.guild_id != MAIN_SERVER:
        raise app_commands.CheckFailure("This command can only be used in the main server.")

    if not isinstance(interaction.user, discord.Member):
        raise app_commands.CheckFailure("Could not verify your server roles.")

    if not any(
        role.id in {
            1397677852056354948,
            1518416402141417472,
            1306082718060384399,
        }
        for role in interaction.user.roles
    ):
        raise app_commands.CheckFailure(
            "You do not have permission to manage the review blacklist."
        )

    return True



review_blacklist_group = app_commands.Group(
    name="reviewblacklist",
    description="Manage the review user blacklist",
)

@review_blacklist_group.command(
    name="add",
    description="Blacklist a member from submitting or updating reviews.",
)
@app_commands.describe(member="The member to blacklist from reviews.")
@app_commands.check(review_blacklist_command_check)
async def review_blacklist_add_command(
    interaction: discord.Interaction,
    member: discord.Member,
):
    await interaction.response.defer(ephemeral=True)

    if member.id in review_blacklist:
        await interaction.followup.send(
            f"ℹ️ {member.mention} is already blacklisted from reviews.",
            ephemeral=True,
        )
        return

    review_blacklist.add(member.id)
    synced = await save_review_blacklist()
    await log_review_event(
        "Review Blacklist Updated",
        f"{interaction.user.mention} blacklisted {member.mention} from the review system.",
        fields=[
            ("Member", f"{member.mention} / {member.id}", True),
            ("Changed By", f"{interaction.user.mention} / {interaction.user.id}", True),
            ("GitHub Sync", "Success" if synced else "Failed", True),
        ],
        color=discord.Color.red(),
    )

    message = f"✅ {member.mention} can no longer submit or update reviews."
    if not synced:
        message += f"\n⚠️ GitHub sync failed: {github_blacklist_sync_error}"
    await interaction.followup.send(message, ephemeral=True)


@review_blacklist_group.command(
    name="remove",
    description="Remove a member from the review blacklist.",
)
@app_commands.describe(member="The member to remove from the review blacklist.")
@app_commands.check(review_blacklist_command_check)
async def review_blacklist_remove_command(
    interaction: discord.Interaction,
    member: discord.Member,
):
    await interaction.response.defer(ephemeral=True)

    if member.id not in review_blacklist:
        await interaction.followup.send(
            f"ℹ️ {member.mention} is not currently blacklisted from reviews.",
            ephemeral=True,
        )
        return

    review_blacklist.remove(member.id)
    synced = await save_review_blacklist()
    await log_review_event(
        "Review Blacklist Updated",
        f"{interaction.user.mention} removed {member.mention} from the review blacklist.",
        fields=[
            ("Member", f"{member.mention} / {member.id}", True),
            ("Changed By", f"{interaction.user.mention} / {interaction.user.id}", True),
            ("GitHub Sync", "Success" if synced else "Failed", True),
        ],
        color=discord.Color.green(),
    )

    message = f"✅ {member.mention} can submit and update reviews again."
    if not synced:
        message += f"\n⚠️ GitHub sync failed: {github_blacklist_sync_error}"
    await interaction.followup.send(message, ephemeral=True)


@review_blacklist_group.command(
    name="status",
    description="Check whether a member is blacklisted from reviews.",
)
@app_commands.describe(member="The member to check.")
@app_commands.check(review_blacklist_command_check)
async def review_blacklist_status_command(
    interaction: discord.Interaction,
    member: discord.Member,
):
    status = member.id in review_blacklist
    await interaction.response.send_message(
        f"ℹ️ {member.mention} is **{'blacklisted' if status else 'not blacklisted'}** from the review system.",
        ephemeral=True,
    )


@textify_group.command(
    name="ban",
    description="Ban a member from running UWUIFY and HOODIFY.",
)
@app_commands.describe(member="The member to ban from UWUIFY and HOODIFY")
@app_commands.check(blacklist_command_check)
async def uwuify_hoodify_ban_command(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer(ephemeral=False)
    if member.id in uwu_hoodify_ban:
        await interaction.followup.send(
            f"ℹ️ {member.mention} is already banned from UWUIFY and HOODIFY.",
            ephemeral=False,
        )
        return

    uwu_hoodify_ban.add(member.id)
    await save_user_blacklist(TEXTIFY_BAN_FILE, uwu_hoodify_ban)
    removed_uwu = await disable_uwu_for_user(member.id)
    removed_hood = await disable_hood_for_user(member.id)

    removed = removed_uwu + removed_hood
    await interaction.followup.send(
        f"✅ {member.mention} is now banned from running UWUIFY and HOODIFY."
        + (f" Removed {removed} active mode(s)." if removed else ""),
        ephemeral=False,
    )


@textify_group.command(
    name="unban",
    description="Allow a member to run UWUIFY and HOODIFY again.",
)
@app_commands.describe(member="The member to unban from UWUIFY and HOODIFY")
@app_commands.check(blacklist_command_check)
async def uwuify_hoodify_unban_command(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer(ephemeral=False)
    if member.id not in uwu_hoodify_ban:
        await interaction.followup.send(
            f"ℹ️ {member.mention} is not currently banned from UWUIFY and HOODIFY.",
            ephemeral=False,
        )
        return

    uwu_hoodify_ban.remove(member.id)
    await save_user_blacklist(TEXTIFY_BAN_FILE, uwu_hoodify_ban)
    await interaction.followup.send(
        f"✅ {member.mention} can run UWUIFY and HOODIFY again.",
        ephemeral=False,
    )


@tree.command(
    name="ping",
    description="Show the bot's current Discord latency.",
)
async def ping_command(interaction: discord.Interaction):
    """Show the bot's current WebSocket latency in milliseconds."""
    latency_ms = round(bot.latency * 1000, 2)
    await interaction.response.send_message(
        f"🏓 Pong! **{latency_ms} ms**"
    )

@tree.command(
    name="unuwuify",
    description="Disable UWU mode for a selected member.",
)
@app_commands.describe(member="The member to stop UWUIFYING")
async def unuwuify_command(interaction: discord.Interaction, member: discord.Member):
    """Disable UWU mode for one selected member across all active channels."""
    if uwu_hoodify_user_is_banned(interaction.user):
        await interaction.response.send_message(
            "❌ You are banned from using UWUIFY and HOODIFY.",
            ephemeral=False,
        )
        return
    if not isinstance(interaction.user, discord.Member) or not uwu_user_is_whitelisted(interaction.user):
        await interaction.response.send_message(
            "❌ You need one of the allowed UWU roles to use this command.",
            ephemeral=False,
        )
        return

    disabled_count = await disable_uwu_for_user(member.id)

    await interaction.response.send_message(
        f"✅ UWU mode disabled for {member.mention}. "
        f"Removed them from **{disabled_count}** active channel(s).",
        ephemeral=False,
    )


@uwu_group.command(
    name="count",
    description="Show how many people are currently being UWUified.",
)
async def uwucount_command(interaction: discord.Interaction):
    """Show the global and current-channel UWU target counts."""
    global_count = get_active_uwu_target_count()

    channel_count = 0
    if isinstance(interaction.channel, discord.TextChannel):
        channel_count = len(uwu_targets.get(interaction.channel.id, set()))

    await interaction.response.send_message(
        f"🩷 **UWU count**\n"
        f"Global: **{global_count}/{MAX_ACTIVE_UWU_TARGETS}** people\n"
        f"This channel: **{channel_count}** people",
        ephemeral=False,
    )


@tree.command(
    name="unhoodify",
    description="Disable HOODIFY for a selected member.",
)
@app_commands.describe(member="The member to stop HOODIFYING")
async def unhoodify_command(interaction: discord.Interaction, member: discord.Member):
    if uwu_hoodify_user_is_banned(interaction.user):
        await interaction.response.send_message(
            "❌ You are banned from using UWUIFY and HOODIFY.",
            ephemeral=False,
        )
        return
    if not isinstance(interaction.user, discord.Member) or not hood_user_is_whitelisted(interaction.user):
        await interaction.response.send_message(
            "❌ You need one of the allowed HOODIFY roles to use this command.",
            ephemeral=False,
        )
        return

    disabled_count = await disable_hood_for_user(member.id)
    await interaction.response.send_message(
        f"✅ HOODIFY disabled for {member.mention}. "
        f"Removed them from **{disabled_count}** active channel(s).",
        ephemeral=False,
    )


@hood_group.command(
    name="count",
    description="Show how many people are currently being HOODIFIED.",
)
async def hoodcount_command(interaction: discord.Interaction):
    global_count = get_active_hood_target_count()
    channel_count = 0
    if isinstance(interaction.channel, discord.TextChannel):
        channel_count = len(hood_targets.get(interaction.channel.id, set()))

    await interaction.response.send_message(
        f"🖤 **HOODIFY count**\n"
        f"Global: **{global_count}/{MAX_ACTIVE_HOOD_TARGETS}** people\n"
        f"This channel: **{channel_count}** people",
        ephemeral=False,
    )


@tree.command(
    name="hoodify",
    description="Add a member to this channel's automatic HOODIFY mode.",
)
@app_commands.describe(
    member="The member whose messages should be automatically hoodified",
    message="Optional one-time message to send through the hoodify webhook",
)
async def hoodify_command(
    interaction: discord.Interaction,
    member: discord.Member,
    message: str | None = None,
):
    if uwu_hoodify_user_is_banned(interaction.user):
        await interaction.response.send_message(
            "❌ You are banned from using UWUIFY and HOODIFY.",
            ephemeral=False,
        )
        return

    if not isinstance(interaction.user, discord.Member) or not hood_user_is_whitelisted(interaction.user):
        await interaction.response.send_message(
            "❌ You need one of the allowed HOODIFY roles to use this command.",
            ephemeral=False,
        )
        return

    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message(
            "❌ This command can only be used in a normal text channel.",
            ephemeral=False,
        )
        return

    bot_member = interaction.guild.me if interaction.guild is not None else None
    if bot_member is None or not interaction.channel.permissions_for(bot_member).manage_messages:
        await interaction.response.send_message(
            "❌ I need **Manage Messages** permission in this channel to replace messages.",
            ephemeral=False,
        )
        return

    try:
        await set_hood_target(interaction.channel, member)

        if message:
            try:
                await send_hood_message(interaction.channel, member, message)
            except HoodMessageBlocked as blocked_error:
                await interaction.response.send_message(
                    f"❌ HOODIFY was enabled, but the one-time message was not sent "
                    f"because it contains a blacklisted word/phrase: `{blocked_error}`",
                    ephemeral=False,
                )
                return

        active_count = get_active_hood_target_count()
        await interaction.response.send_message(
            f"✅ HOODIFY is active for {member.mention} in this channel. "
            f"Active people: **{active_count}/{MAX_ACTIVE_HOOD_TARGETS}**.\n"
            "You can add more people with another `/hoodify` command. "
            "The temporary webhook will be deleted after 5 minutes without use.",
            ephemeral=False,
        )
    except HoodUserBlacklisted:
        await interaction.response.send_message(
            f"❌ {member.mention} is blacklisted from using HOODIFY.",
            ephemeral=False,
        )
    except UserBlacklistStorageUnavailable as error:
        await interaction.response.send_message(
            "❌ I could not verify the HOODIFY blacklist from GitHub, "
            f"so I will not activate this target. Error: `{error}`",
            ephemeral=False,
        )
    except HoodTargetLimitReached:
        await interaction.response.send_message(
            f"❌ The global limit of {MAX_ACTIVE_HOOD_TARGETS} HOODIFIED people has been reached. "
            "Use `,unhoodify @user` or `/unhoodify @user` to disable HOODIFY for one member.",
            ephemeral=False,
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ I need **Manage Messages** and **Manage Webhooks** permission in this channel/server.",
            ephemeral=False,
        )
    except discord.HTTPException as e:
        await interaction.response.send_message(
            f"❌ Discord rejected the HOODIFY webhook request: `{e}`",
            ephemeral=False,
        )
    except Exception:
        await interaction.response.send_message(
            "❌ The HOODIFY mode could not be enabled.",
            ephemeral=False,
        )


@tree.command(
    name="uwuify",
    description="Add a member to this channel's automatic UWU mode.",
)
@app_commands.describe(
    member="The member whose messages should be automatically uwuified",
    message="Optional one-time message to send through the uwu webhook",
)
async def uwu_command(
    interaction: discord.Interaction,
    member: discord.Member,
    message: str | None = None,
):
    """Enable automatic uwu replacement for a selected member in this channel."""
    if uwu_hoodify_user_is_banned(interaction.user):
        await interaction.response.send_message(
            "❌ You are banned from using UWUIFY and HOODIFY.",
            ephemeral=False,
        )
        return
    if not isinstance(interaction.user, discord.Member) or not uwu_user_is_whitelisted(interaction.user):
        await interaction.response.send_message(
            "❌ You need one of the allowed UWU roles to use this command.",
            ephemeral=False,
        )
        return

    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message(
            "❌ This command can only be used in a normal text channel.",
            ephemeral=False,
        )
        return

    bot_member = interaction.guild.me if interaction.guild is not None else None
    if bot_member is None or not interaction.channel.permissions_for(bot_member).manage_messages:
        await interaction.response.send_message(
            "❌ I need **Manage Messages** permission in this channel to replace messages.",
            ephemeral=False,
        )
        return

    try:
        await set_uwu_target(interaction.channel, member)

        if message:
            try:
                await send_uwu_message(interaction.channel, member, message)
            except UwuMessageBlocked as blocked_error:
                await interaction.response.send_message(
                    f"❌ The UWU mode was enabled, but the one-time message was not sent because it contains a blacklisted word/phrase: `{blocked_error}`",
                    ephemeral=False,
                )
                return

        active_count = get_active_uwu_target_count()
        await interaction.response.send_message(
            f"✅ Uwu mode is active for {member.mention} in this channel. "
            f"Active people: **{active_count}/{MAX_ACTIVE_UWU_TARGETS}**.\n"
            "You can add more people with another `/uwuify` command. "
            "The temporary webhook will be deleted after 5 minutes without use.",
            ephemeral=False,
        )
    except UwuUserBlacklisted:
        await interaction.response.send_message(            f"❌ {member.mention} is blacklisted from using UWUIFY.",
            ephemeral=False,
        )
    except UserBlacklistStorageUnavailable as error:
        await interaction.response.send_message(
            "❌ I could not verify the UWUIFY blacklist from GitHub, "
            f"so I will not activate this target. Error: `{error}`",
            ephemeral=False,
        )
    except UwuTargetLimitReached:
        await interaction.response.send_message(
            f"❌ The global limit of {MAX_ACTIVE_UWU_TARGETS} UWUified people has been reached. "
            "Use `,unuwuify @user` or `/unuwuify @user` to disable UWU for one member, or wait for a slot to expire.",
            ephemeral=False,
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ I need **Manage Messages** and **Manage Webhooks** permission in this channel/server.",
            ephemeral=False,
        )
    except discord.HTTPException as e:
        await interaction.response.send_message(
            f"❌ Discord rejected the uwu webhook request: `{e}`",
            ephemeral=False,
        )
    except Exception:
        await interaction.response.send_message(
            "❌ The uwu mode could not be enabled.",
            ephemeral=False,
        )


@textify_group.command(
    name="blacklist",
    description="Block a member from both UWUIFY and HOODIFY.",
)
@app_commands.describe(member="The member to block from UWUIFY and HOODIFY")
@app_commands.check(blacklist_command_check)
async def textify_blacklist_command(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer(ephemeral=False)

    uwu_user_blacklist.add(member.id)
    hood_user_blacklist.add(member.id)

    uwu_synced = await save_user_blacklist(TEXTIFY_BLACKLIST_FILE, uwu_user_blacklist)
    hood_synced = await save_user_blacklist(TEXTIFY_BLACKLIST_FILE, hood_user_blacklist)

    removed = (
        await disable_uwu_for_user(member.id)
        + await disable_hood_for_user(member.id)
    )

    response = (
        f"✅ {member.mention} is now blacklisted from both UWUIFY and HOODIFY."
        + (f" Removed them from **{removed}** active mode(s)." if removed else "")
    )
    if not uwu_synced or not hood_synced:
        response += (
            "\n⚠️ GitHub sync FAILED."
            f"\n`{github_blacklist_sync_error}`"
        )

    await interaction.followup.send(response, ephemeral=False)


@textify_group.command(
    name="unblacklist",
    description="Allow a member to use both UWUIFY and HOODIFY again.",
)
@app_commands.describe(member="The member to remove from the Textify blacklist")
@app_commands.check(blacklist_command_check)
async def textify_unblacklist_command(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer(ephemeral=False)

    if member.id not in uwu_user_blacklist and member.id not in hood_user_blacklist:
        await interaction.followup.send(
            f"ℹ️ {member.mention} is not currently blacklisted from UWUIFY or HOODIFY.",
            ephemeral=False,
        )
        return

    uwu_user_blacklist.discard(member.id)
    hood_user_blacklist.discard(member.id)

    uwu_synced = await save_user_blacklist(TEXTIFY_BLACKLIST_FILE, uwu_user_blacklist)
    hood_synced = await save_user_blacklist(TEXTIFY_BLACKLIST_FILE, hood_user_blacklist)

    response = f"✅ {member.mention} can use UWUIFY and HOODIFY again."
    if not uwu_synced or not hood_synced:
        response += (
            "\n⚠️ GitHub sync FAILED."
            f"\n`{github_blacklist_sync_error}`"
        )

    await interaction.followup.send(response, ephemeral=False)


@textify_group.command(
    name="status",
    description="Check the combined UWUIFY and HOODIFY blacklist.",
)
@app_commands.describe(member="The member to check")
@app_commands.check(blacklist_command_check)
async def textify_status_command(interaction: discord.Interaction, member: discord.Member):
    uwu_blacklisted = member.id in uwu_user_blacklist
    hood_blacklisted = member.id in hood_user_blacklist

    if uwu_blacklisted and hood_blacklisted:
        status = "blacklisted from both UWUIFY and HOODIFY"
    elif uwu_blacklisted:
        status = "blacklisted from UWUIFY"
    elif hood_blacklisted:
        status = "blacklisted from HOODIFY"
    else:
        status = "not blacklisted from UWUIFY or HOODIFY"

    await interaction.response.send_message(
        f"{member.mention} is **{status}**.",
        ephemeral=False,
    )


@uwu_group.command(
    name="blacklist",
    description="Block a member from being UWUified.",
)
@app_commands.describe(member="The member to block from UWUIFY")
@app_commands.check(blacklist_command_check)
async def uwu_blacklist_command(
    interaction: discord.Interaction,
    member: discord.Member,
):
    await interaction.response.defer(ephemeral=False)

    uwu_user_blacklist.add(member.id)
    github_synced = await save_user_blacklist(
        TEXTIFY_BLACKLIST_FILE,
        uwu_user_blacklist,
    )
    removed = await disable_uwu_for_user(member.id)

    response = (
        f"✅ {member.mention} is now blacklisted from UWUIFY."
        + (f" Removed them from **{removed}** active channel(s)." if removed else "")
    )
    if not github_synced:
        response += (
            "\n⚠️ GitHub sync FAILED."
            f"\n`{github_blacklist_sync_error}`"
        )

    await interaction.followup.send(
        response,
        ephemeral=False,
    )


@uwu_group.command(
    name="unblacklist",
    description="Allow a member to be UWUified again.",
)
@app_commands.describe(member="The member to remove from the UWUIFY blacklist")
@app_commands.check(blacklist_command_check)
async def uwu_unblacklist_command(
    interaction: discord.Interaction,
    member: discord.Member,
):
    if member.id not in uwu_user_blacklist:
        await interaction.response.send_message(
            f"ℹ️ {member.mention} is not currently blacklisted from UWUIFY.",
            ephemeral=False,
        )
        return

    await interaction.response.defer(ephemeral=False)

    uwu_user_blacklist.remove(member.id)
    github_synced = await save_user_blacklist(
        TEXTIFY_BLACKLIST_FILE,
        uwu_user_blacklist,
    )

    response = f"✅ {member.mention} can use UWUIFY again."
    if not github_synced:
        response += (
            "\n⚠️ GitHub sync FAILED."
            f"\n`{github_blacklist_sync_error}`"
        )

    await interaction.followup.send(
        response,
        ephemeral=False,
    )


@hood_group.command(
    name="blacklist",
    description="Block a member from being HOODIFIED.",
)
@app_commands.describe(member="The member to block from HOODIFY")
@app_commands.check(blacklist_command_check)
async def hood_blacklist_command(
    interaction: discord.Interaction,
    member: discord.Member,
):
    await interaction.response.defer(ephemeral=False)

    hood_user_blacklist.add(member.id)
    github_synced = await save_user_blacklist(
        TEXTIFY_BLACKLIST_FILE,
        hood_user_blacklist,
    )
    removed = await disable_hood_for_user(member.id)

    response = (
        f"✅ {member.mention} is now blacklisted from HOODIFY."
        + (f" Removed them from **{removed}** active channel(s)." if removed else "")
    )
    if not github_synced:
        response += (
            "\n⚠️ GitHub sync FAILED."
            f"\n`{github_blacklist_sync_error}`"
        )

    await interaction.followup.send(
        response,
        ephemeral=False,
    )


@hood_group.command(
    name="unblacklist",
    description="Allow a member to be HOODIFIED again.",
)
@app_commands.describe(member="The member to remove from the HOODIFY blacklist")
@app_commands.check(blacklist_command_check)
async def hood_unblacklist_command(
    interaction: discord.Interaction,
    member: discord.Member,
):
    if member.id not in hood_user_blacklist:
        await interaction.response.send_message(
            f"ℹ️ {member.mention} is not currently blacklisted from HOODIFY.",
            ephemeral=False,
        )
        return

    await interaction.response.defer(ephemeral=False)

    hood_user_blacklist.remove(member.id)
    github_synced = await save_user_blacklist(
        TEXTIFY_BLACKLIST_FILE,
        hood_user_blacklist,
    )

    response = f"✅ {member.mention} can use HOODIFY again."
    if not github_synced:
        response += (
            "\n⚠️ GitHub sync FAILED."
            f"\n`{github_blacklist_sync_error}`"
        )

    await interaction.followup.send(
        response,
        ephemeral=False,
    )


@blacklist_group.command(
    name="add",
    description="Blacklist a member from receiving the second main-server role.",
)
@app_commands.describe(member="The member to blacklist from the second role")
@app_commands.check(blacklist_command_check)
async def blacklist_command(
    interaction: discord.Interaction,
    member: discord.Member,
):
    """Add a member to the second-role blacklist."""
    second_role_blacklist.add(member.id)
    save_blacklist(second_role_blacklist)

    # If they already have the second role, remove it so the blacklist
    # takes effect immediately instead of waiting for the next assignment.
    main_guild = interaction.guild
    removed_role = False

    if main_guild is not None and TAG_ROLE_ID_2:
        second_role = main_guild.get_role(TAG_ROLE_ID_2)

        if second_role is not None and second_role in member.roles:
            bot_member = main_guild.me

            if (
                bot_member is not None
                and main_guild.owner_id != member.id
                and member.top_role < bot_member.top_role
                and bot_member.guild_permissions.manage_roles
                and not second_role.managed
                and not second_role.is_default()
                and bot_member.top_role > second_role
            ):
                try:
                    await member.remove_roles(
                        second_role,
                        reason="Member added to second-role blacklist",
                    )
                    removed_role = True
                except discord.HTTPException as e:
                    pass

    role_text = " The second role was also removed." if removed_role else ""

    await interaction.response.send_message(
        f"✅ {member.mention} has been added to the second-role blacklist.{role_text}",
        ephemeral=False,
    )

    await send_webhook(
        ROLE_WEBHOOK_URL,
        title="🚫 Second Role Blacklisted",
        description=f"{member.mention} was added to the second-role blacklist.",
        color=discord.Color.red(),
        fields=[
            ("User", f"{member} (`{member.id}`)", True),
            ("Added By", f"{interaction.user} (`{interaction.user.id}`)", True),
            ("Second Role", f"`{TAG_ROLE_ID_2}`", True),
            ("Role Removed", "Yes" if removed_role else "No", True),
        ],
    )


@blacklist_group.command(
    name="remove",
    description="Remove a member from the second main-server role blacklist.",
)
@app_commands.describe(member="The member to remove from the blacklist")
@app_commands.check(blacklist_command_check)
async def unblacklist_command(
    interaction: discord.Interaction,
    member: discord.Member,
):
    """Remove a member from the second-role blacklist."""
    if member.id not in second_role_blacklist:
        await interaction.response.send_message(
            f"ℹ️ {member.mention} is not currently on the second-role blacklist.",
            ephemeral=False,
        )
        return

    second_role_blacklist.remove(member.id)
    save_blacklist(second_role_blacklist)

    await interaction.response.send_message(
        f"✅ {member.mention} has been removed from the second-role blacklist.",
        ephemeral=False,
    )

    await send_webhook(
        ROLE_WEBHOOK_URL,
        title="✅ Second Role Blacklist Removed",
        description=f"{member.mention} was removed from the second-role blacklist.",
        color=discord.Color.green(),
        fields=[
            ("User", f"{member} (`{member.id}`)", True),
            ("Removed By", f"{interaction.user} (`{interaction.user.id}`)", True),
            ("Second Role", f"`{TAG_ROLE_ID_2}`", True),
        ],
    )


@blacklist_group.command(
    name="status",
    description="Check whether a member is blacklisted from the second role.",
)
@app_commands.describe(member="The member to check")
@app_commands.check(blacklist_command_check)
async def blacklist_status_command(
    interaction: discord.Interaction,
    member: discord.Member,
):
    """Check a member's second-role blacklist status."""
    blacklisted = member.id in second_role_blacklist

    await interaction.response.send_message(
        f"{member.mention} is **{'blacklisted' if blacklisted else 'not blacklisted'}** "
        "for the second main-server role.",
        ephemeral=False,
    )


@tree.command(
    name="strike",
    description="Give a staff strike that expires after 7 to 40 days.",
)
@app_commands.describe(
    member="The staff member receiving the strike",
    reason="Reason for the strike",
    days="How many days the strike should last (7-40)",
)
@app_commands.check(staff_strike_command_check)
async def strike_command(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str,
    days: app_commands.Range[int, 7, 40],
):
    """Issue a time-limited staff strike."""
    await interaction.response.defer(ephemeral=False)

    reason = reason.strip()
    if not reason:
        await interaction.followup.send(
            "❌ You must provide a reason for the strike.",
            ephemeral=True,
        )
        return

    expired = prune_expired_staff_strikes()
    if expired:
        await handle_expired_staff_strikes(expired)
        await save_staff_strikes()

    current_staff_info = get_staff_role_for_member(member)
    if current_staff_info is None:
        await interaction.followup.send(
            "❌ The selected member does not have a configured staff role.",
            ephemeral=True,
        )
        return

    original_role_id = get_original_staff_role_id(member.id) or current_staff_info[1].id

    strike = create_staff_strike(
        user_id=member.id,
        reason=reason,
        days=int(days),
        issued_by=interaction.user.id,
        original_staff_role_id=original_role_id,
    )
    active_count = len(get_user_staff_strikes(member.id))
    consequence = await apply_staff_strike_consequences(
        member,
        active_count=active_count,
        log_two_strikes=(active_count == 2),
        log_three_strikes=(active_count >= 3),
    )
    synced = await save_staff_strikes()

    await send_staff_strike_log(
        STAFF_STRIKE_ACTIVE_CHANNEL_ID,
        format_staff_strike(member, strike),
    )

    extra = ""
    if not synced:
        extra = "\n⚠️ GitHub sync failed; the strike was saved locally."
    elif consequence is not None:
        old_role, new_role = consequence
        extra = f"\nRole action: **{old_role} → {new_role}**"

    await interaction.followup.send(
        format_staff_strike(member, strike)
        + f"\n\nActive strikes: **{active_count}**"
        + extra,
        ephemeral=False,
    )


@tree.command(
    name="strikes",
    description="View a member's active staff strikes.",
)
@app_commands.describe(member="The member whose active strikes you want to view")
@app_commands.check(staff_strike_command_check)
async def strikes_command(
    interaction: discord.Interaction,
    member: discord.Member,
):
    """Show a member's active staff strikes."""
    expired = prune_expired_staff_strikes()
    if expired:
        await handle_expired_staff_strikes(expired)
        await save_staff_strikes()

    strikes = get_user_staff_strikes(member.id)
    if not strikes:
        await interaction.response.send_message(
            f"{member.mention} / {member.id}\n\nNo active strikes.",
            ephemeral=False,
        )
        return

    blocks = []
    for strike in strikes:
        blocks.append(
            f"strike #{int(strike['strike_number'])}: {strike['reason']}"
        )

    await interaction.response.send_message(
        f"{member.mention} / {member.id}\n\n" + "\n\n".join(blocks),
        ephemeral=False,
    )


@tree.command(
    name="removestrike",
    description="Remove a specific active staff strike from a member.",
)
@app_commands.describe(
    member="The staff member whose strike you want to remove",
    strike_number="The strike number to remove",
)
@app_commands.check(staff_strike_command_check)
async def removestrike_command(
    interaction: discord.Interaction,
    member: discord.Member,
    strike_number: app_commands.Range[int, 1, 9999],
):
    """Remove one active strike and immediately recalculate staff rank."""
    await interaction.response.defer(ephemeral=False)

    expired = prune_expired_staff_strikes()
    if expired:
        await handle_expired_staff_strikes(expired)
        await save_staff_strikes()

    matching = next(
        (
            strike
            for strike in staff_strikes
            if int(strike.get("user_id", 0)) == member.id
            and int(strike.get("strike_number", 0)) == int(strike_number)
        ),
        None,
    )

    if matching is None:
        await interaction.followup.send(
            f"❌ {member.mention} does not have an active strike #{int(strike_number)}.",
            ephemeral=False,
        )
        return

    original_role_id = (
        int(matching["original_staff_role_id"])
        if matching.get("original_staff_role_id") is not None
        else get_original_staff_role_id(member.id)
    )

    before_info = get_staff_role_for_member(member)
    before_role_name = before_info[0] if before_info is not None else (
        "Suspended" if len(get_user_staff_strikes(member.id)) >= 3 else "No Staff Role"
    )

    staff_strikes.remove(matching)

    active_count = len(get_user_staff_strikes(member.id))
    consequence = await apply_staff_strike_consequences(
        member,
        active_count=active_count,
        original_role_id_override=original_role_id,
    )
    synced = await save_staff_strikes()

    response = (
        f"✅ Removed strike #{int(strike_number)} from "
        f"{member.mention} / {member.id}.\n\n"
        f"Active strikes: **{active_count}**"
    )

    if consequence is not None:
        old_role, new_role = consequence
        response += f"\nRole action: **{old_role} → {new_role}**"
    elif original_role_id is not None:
        refreshed = await resolve_main_guild_member(member.id) or member
        after_info = get_staff_role_for_member(refreshed)
        after_role_name = after_info[0] if after_info is not None else (
            "Suspended" if active_count >= 3 else "No Staff Role"
        )
        if before_role_name != after_role_name:
            response += f"\nRole action: **{before_role_name} → {after_role_name}**"

    if not synced:
        response += "\n⚠️ GitHub sync failed; the strike removal was saved locally."

    await interaction.followup.send(response, ephemeral=False)


# Register grouped slash-command roots.
uwuify_group.add_command(uwuify_hoodify_group)
tree.add_command(textify_group)
tree.add_command(blacklist_group)
tree.add_command(review_blacklist_group)

@tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
):
    if isinstance(error, app_commands.CheckFailure):
        message = str(error) or "You are not allowed to use this command."

        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=False)
        else:
            await interaction.response.send_message(message, ephemeral=False)
        return


    if interaction.response.is_done():
        await interaction.followup.send(
            "An unexpected error occurred while running the command.",
            ephemeral=False,
        )
    else:
        await interaction.response.send_message(
            "An unexpected error occurred while running the command.",
            ephemeral=False,
        )


# =========================
# AUTOMATIC ROLE REMOVAL HELPER
# =========================

async def remove_auto_role_if_needed(member: discord.Member, reason: str) -> bool:
    """Remove the configured role when the member has the trigger role."""
    if not AUTO_REMOVE_TRIGGER_ROLE_ID or not AUTO_REMOVE_ROLE_ID:
        return False

    if member.bot or member.guild.id != MAIN_SERVER:
        return False

    trigger_role = member.guild.get_role(AUTO_REMOVE_TRIGGER_ROLE_ID)
    role_to_remove = member.guild.get_role(AUTO_REMOVE_ROLE_ID)

    if trigger_role is None or role_to_remove is None:
        return False

    if trigger_role not in member.roles or role_to_remove not in member.roles:
        return False

    if member.guild.owner_id == member.id:
        return False

    bot_member = member.guild.me

    if bot_member is None:
        return False

    if not bot_member.guild_permissions.manage_roles:
        return False

    if role_to_remove.is_default() or role_to_remove.managed:
        return False

    if bot_member.top_role <= role_to_remove:
        return False

    if member.top_role >= bot_member.top_role:
        return False

    try:
        await member.remove_roles(role_to_remove, reason=reason)
        return True

    except Exception:
        return False

    return False


# =========================
# MEMBER UPDATE EVENT
# =========================

@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    """Immediately remove the target role when the trigger role is newly added in the main server."""
    if after.guild.id != MAIN_SERVER:
        return

    if not AUTO_REMOVE_TRIGGER_ROLE_ID or not AUTO_REMOVE_ROLE_ID:
        return

    had_trigger = AUTO_REMOVE_TRIGGER_ROLE_ID in {role.id for role in before.roles}
    has_trigger = AUTO_REMOVE_TRIGGER_ROLE_ID in {role.id for role in after.roles}

    if not had_trigger and has_trigger:
        await remove_auto_role_if_needed(
            after,
            reason="Member received the configured automatic-removal trigger role",
        )


# =========================
# MEMBER CACHE HELPER
# =========================

async def ensure_guild_members_loaded(guild: discord.Guild) -> bool:
    """Make sure the guild member cache is populated before role checks."""
    try:
        if not guild.chunked:
            await guild.chunk(cache=True)
        return True
    except discord.HTTPException as e:
        return False
    except Exception as e:
        return False


# ============================================================
# HELPERS
# ============================================================

def review_stars(rating: int) -> str:
    return "⭐" * rating + "☆" * (5 - rating)


def review_approval_emoji(rating: int) -> str:
    if rating in (4, 5):
        return "🟢"
    if rating == 3:
        return "🟠"
    return "🔴"


async def get_review_member_or_user(
    guild: discord.Guild,
    user_id: int
):
    member = guild.get_member(user_id)

    if member:
        return member

    try:
        return await bot.fetch_user(user_id)
    except:
        return None


# ============================================================
# REVIEW LOGGING
# ============================================================

async def log_review_event(title, description, fields=None, color=None):
    """Send review activity to the configured log channel."""
    try:
        channel = bot.get_channel(REVIEW_LOG_CHANNEL_ID)
        if channel is None:
            channel = await bot.fetch_channel(REVIEW_LOG_CHANNEL_ID)

        embed = discord.Embed(
            title=title,
            description=description,
            color=color or discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )

        for name, value, inline in fields or []:
            embed.add_field(name=name, value=value, inline=inline)

        await channel.send(embed=embed)
    except Exception:
        # Logging must never break the review system.
        pass


# ============================================================
# REVIEW COMMENT MODAL
# ============================================================

class ReviewModal(discord.ui.Modal):

    def __init__(
        self,
        target: discord.Member,
        rating: int
    ):
        super().__init__(
            title=f"Leave a {rating}-star review"
        )

        self.target = target
        self.rating = rating

        self.comment = discord.ui.TextInput(
            label="Review",
            placeholder="Write your review...",
            style=discord.TextStyle.paragraph,
            required=True,
            min_length=1,
            max_length=1000
        )

        self.add_item(self.comment)

    async def on_submit(
        self,
        interaction: discord.Interaction
    ):

        # Acknowledge the modal immediately so Discord does not time out.
        await interaction.response.defer(ephemeral=True)

        if review_user_is_blacklisted(interaction.user.id):
            await interaction.followup.send(
                "❌ You are blacklisted from using the review system.",
                ephemeral=True
            )
            return

        if interaction.user.id == self.target.id:
            await interaction.followup.send(
                "❌ You can't review yourself.",
                ephemeral=True
            )
            return

        try:
            async with review_db_lock:
                await refresh_local_review_db_from_github()
                review_id = add_review(
                    target_id=self.target.id,
                    reviewer_id=interaction.user.id,
                    rating=self.rating,
                    comment=self.comment.value
                )
                sync_success, sync_error = await sync_review_db_to_github()
        except DuplicateReviewError:
            await interaction.followup.send(
                '❌ You already reviewed this user. Use `/updatereview` to change your vote.',
                ephemeral=True,
            )
            return
        except Exception as error:
            # Return the real database error instead of hiding it behind the
            # generic Discord modal failure message. This also makes Railway
            # logs useful if the database itself is unavailable.
            print(
                f"Review database error: {type(error).__name__}: {error}"
            )
            await interaction.followup.send(
                "❌ I couldn't save that review to the database.\n"
                f"`{type(error).__name__}: {error}`",
                ephemeral=True
            )
            return

        await interaction.followup.send(
            f"✅ Your review for {self.target.mention} was added.\n"
            f"**Rating:** {review_stars(self.rating)}\n"
            f"**Review ID:** `{review_id}`",
            ephemeral=True
        )
        if not sync_success:
            print(f"Review database save after /review failed: {sync_error}")

        await log_review_event(
            "Review Added",
            f"{interaction.user.mention} submitted a review for {self.target.mention}.",
            fields=[
                ("Review ID", f"Review #{review_id}", True),
                ("Rating", review_stars(self.rating), True),
                ("Reviewer", f"{interaction.user.mention} / {interaction.user.id}", False),
                ("Target", f"{self.target.mention} / {self.target.id}", False),
                ("Comment", str(self.comment.value)[:1024], False),
            ],
            color=discord.Color.green(),
        )

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception
    ):
        try:
            if interaction.response.is_done():
                await interaction.followup.send(
                    "❌ Something went wrong while saving the review. Please try again.",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "❌ Something went wrong while saving the review. Please try again.",
                    ephemeral=True
                )
        except Exception:
            pass


# ============================================================
# STAR DROPDOWN
# ============================================================

class StarSelect(discord.ui.Select):

    def __init__(self, target: discord.Member):

        self.target = target

        options = [
            discord.SelectOption(
                label="1 Star",
                value="1",
                emoji="⭐"
            ),
            discord.SelectOption(
                label="2 Stars",
                value="2",
                emoji="⭐"
            ),
            discord.SelectOption(
                label="3 Stars",
                value="3",
                emoji="⭐"
            ),
            discord.SelectOption(
                label="4 Stars",
                value="4",
                emoji="⭐"
            ),
            discord.SelectOption(
                label="5 Stars",
                value="5",
                emoji="⭐"
            )
        ]

        super().__init__(
            placeholder="select a star rating...",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(
        self,
        interaction: discord.Interaction
    ):

        rating = int(self.values[0])

        await interaction.response.send_modal(
            ReviewModal(
                target=self.target,
                rating=rating
            )
        )


class StarView(discord.ui.View):

    def __init__(
        self,
        target: discord.Member
    ):
        super().__init__(timeout=120)

        self.add_item(
            StarSelect(target)
        )


# ============================================================
# /review
# ============================================================

@tree.command(
    name="review",
    description="Leave a review for a member."
)
@app_commands.describe(
    user="The member you want to review."
)
async def review(
    interaction: discord.Interaction,
    user: discord.Member
):

    if review_user_is_blacklisted(interaction.user.id):
        await interaction.response.send_message(
            "❌ You are blacklisted from using the review system.",
            ephemeral=True
        )
        return

    if user.id == interaction.user.id:
        await interaction.response.send_message(
            "❌ You can't review yourself.",
            ephemeral=True
        )
        return

    # /review creates a review only. Existing reviews must be changed through
    # /updatereview so the moderator approval workflow is always used.
    existing = get_user_review(user.id, interaction.user.id)
    if existing:
        await interaction.response.send_message(
            "❌ You already reviewed this user. Use /updatereview to request a change. "
            "Your existing review will stay unchanged until a moderator approves it.",
            ephemeral=True
        )
        return

    await interaction.response.send_message(
        "**select your star rating:**",
        view=StarView(user),
        ephemeral=True
    )


# ============================================================
# REVIEW PAGINATION
# ============================================================

class ReviewPagination(discord.ui.View):

    def __init__(
        self,
        target: discord.Member,
        reviews: list
    ):
        super().__init__(timeout=180)

        self.target = target
        self.reviews = reviews
        self.page = 0

        self.per_page = 5

        self.previous.disabled = True

        if len(reviews) <= self.per_page:
            self.next.disabled = True

    def make_embed(self):

        start = self.page * self.per_page
        end = start + self.per_page

        page_reviews = self.reviews[start:end]

        stats = get_user_stats(self.target.id)

        embed = discord.Embed(
            title=f"reviews for {self.target.display_name}",
            color=discord.Color.dark_grey()
        )

        embed.set_thumbnail(
            url=self.target.display_avatar.url
        )

        embed.description = (
            f"🟢 **{stats['approved']}**  "
            f"🔴 **{stats['total'] - stats['approved']}**\n"
        )

        for review in page_reviews:

            reviewer = self.target.guild.get_member(
                review["reviewer_id"]
            )

            if reviewer:
                reviewer_name = reviewer.display_name
                reviewer_mention = reviewer.mention
            else:
                reviewer_name = "Unknown User"
                reviewer_mention = f"<@{review['reviewer_id']}>"

            embed.add_field(
                name=(
                    f"{review_stars(review['rating'])} — "
                    f"by {reviewer_name} · ID {review['id']}"
                ),
                value=(
                    f"{reviewer_mention}\n"
                    f"> {review['comment']}"
                ),
                inline=False
            )

        total_pages = max(
            1,
            (len(self.reviews) + self.per_page - 1)
            // self.per_page
        )

        embed.set_footer(
            text=f"Page {self.page + 1}/{total_pages}"
        )

        return embed

    @discord.ui.button(
        emoji="◀",
        style=discord.ButtonStyle.secondary
    )
    async def previous(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        if self.page > 0:
            self.page -= 1

        self.next.disabled = False
        self.previous.disabled = self.page == 0

        await interaction.response.edit_message(
            embed=self.make_embed(),
            view=self
        )

    @discord.ui.button(
        emoji="▶",
        style=discord.ButtonStyle.secondary
    )
    async def next(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        max_page = (
            len(self.reviews) - 1
        ) // self.per_page

        if self.page < max_page:
            self.page += 1

        self.previous.disabled = False
        self.next.disabled = self.page >= max_page

        await interaction.response.edit_message(
            embed=self.make_embed(),
            view=self
        )


# ============================================================
# REVIEW UPDATE / APPROVAL
# ============================================================

def review_update_embed(request, status=None, moderator_id=None):
    embed = discord.Embed(
        title=f'Review Update Request #{request["id"]}',
        description=f'<@{request["reviewer_id"]}> requested an update for <@{request["target_id"]}>.',
        color=discord.Color.orange() if status is None else (discord.Color.green() if status == 'approved' else discord.Color.red()),
    )
    embed.add_field(
        name='Current Vote',
        value=f'{review_stars(request["old_rating"])}\n> {request["old_comment"]}',
        inline=False,
    )
    embed.add_field(
        name='Proposed Vote',
        value=f'{review_stars(request["new_rating"])}\n> {request["new_comment"]}',
        inline=False,
    )
    embed.add_field(
        name='Reason for Update',
        value=str(request.get("update_reason") or "No reason provided."),
        inline=False,
    )
    if status:
        embed.add_field(
            name='Decision',
            value=f'{status.title()} by <@{moderator_id}>',
            inline=False,
        )
    embed.set_footer(text='Review update awaiting moderator approval. Use the buttons below.')
    return embed


async def post_review_update_request(request_id):
    request = get_review_update_request(request_id)
    if not request:
        raise RuntimeError('Review update request was not found.')
    channel = bot.get_channel(REVIEW_UPDATE_APPROVAL_CHANNEL_ID)
    if channel is None:
        channel = await bot.fetch_channel(REVIEW_UPDATE_APPROVAL_CHANNEL_ID)
    approval_message = await channel.send(
        embed=review_update_embed(request),
        view=ReviewUpdateApprovalView(request_id),
    )
    set_review_update_message(request_id, REVIEW_UPDATE_APPROVAL_CHANNEL_ID, approval_message.id)
    return approval_message


class ReviewUpdateApprovalView(discord.ui.View):
    """Persistent Approve/Reject buttons for moderator review of vote updates."""
    def __init__(self, request_id: int, disabled: bool = False):
        super().__init__(timeout=None)
        self.request_id = request_id

        approve_button = discord.ui.Button(
            label='Approve',
            style=discord.ButtonStyle.success,
            custom_id=f'review_update_approve:{request_id}',
            disabled=disabled,
        )
        reject_button = discord.ui.Button(
            label='Reject',
            style=discord.ButtonStyle.danger,
            custom_id=f'review_update_reject:{request_id}',
            disabled=disabled,
        )
        approve_button.callback = self.approve_callback
        reject_button.callback = self.reject_callback
        self.add_item(approve_button)
        self.add_item(reject_button)

    async def approve_callback(self, interaction: discord.Interaction):
        await handle_review_update_decision(interaction, self.request_id, True)

    async def reject_callback(self, interaction: discord.Interaction):
        await handle_review_update_decision(interaction, self.request_id, False)


class UpdateReviewModal(discord.ui.Modal):
    def __init__(self, target, current_review):
        super().__init__(title='Update your vote')
        self.target = target
        self.current_review = current_review
        self.rating = discord.ui.TextInput(
            label='New rating (1-5)',
            placeholder='Enter 1, 2, 3, 4, or 5',
            required=True,
            max_length=1,
            default=str(current_review['rating']),
        )
        self.comment = discord.ui.TextInput(
            label='Review text',
            placeholder='Leave blank to keep your current review text.',
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=1000,
            default=current_review['comment'],
        )
        self.update_reason = discord.ui.TextInput(
            label='Reason for updating this review',
            placeholder='Explain why you are changing your review...',
            style=discord.TextStyle.paragraph,
            required=True,
            min_length=10,
            max_length=500,
        )
        self.add_item(self.rating)
        self.add_item(self.comment)
        self.add_item(self.update_reason)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            new_rating = int(self.rating.value.strip())
            if new_rating < 1 or new_rating > 5:
                raise ValueError
        except ValueError:
            await interaction.followup.send('❌ Rating must be a number from 1 to 5.', ephemeral=True)
            return

        new_comment = self.comment.value.strip() or self.current_review['comment']
        update_reason = self.update_reason.value.strip()

        if len(update_reason) < 10:
            await interaction.followup.send(
                '❌ You must provide at least 10 characters explaining why you are updating the review.',
                ephemeral=True,
            )
            return

        try:
            request_id = create_review_update_request(
                self.current_review['id'],
                self.target.id,
                interaction.user.id,
                self.current_review['rating'],
                self.current_review['comment'],
                new_rating,
                new_comment,
                update_reason,
            )
        except PendingReviewUpdateError:
            await interaction.followup.send('❌ You already have an update waiting for approval for this vote.', ephemeral=True)
            return
        except Exception as error:
            await interaction.followup.send(f'❌ I could not create the update request: `{error}`', ephemeral=True)
            return

        try:
            await post_review_update_request(request_id)
        except Exception:
            connection = sqlite3.connect(REVIEW_DB_FILE, timeout=30)
            try:
                connection.execute('DELETE FROM review_update_requests WHERE id = ? AND status = ?', (request_id, 'pending'))
                connection.commit()
            finally:
                connection.close()
            await interaction.followup.send('❌ I could not send the update to the approval channel. Check the bot permissions there.', ephemeral=True)
            return

        asyncio.create_task(sync_review_db_to_github_locked())
        await interaction.followup.send(
            f'✅ Your updated vote was sent for approval. Request ID: `{request_id}`',
            ephemeral=True,
        )


@tree.command(
    name='updatereview',
    description='Submit an updated vote for a member for moderator approval.'
)
@app_commands.describe(user='The member whose review you want to update.')
async def updatereview(interaction: discord.Interaction, user: discord.Member):
    if review_user_is_blacklisted(interaction.user.id):
        await interaction.response.send_message(
            "❌ You are blacklisted from using the review system.",
            ephemeral=True,
        )
        return

    await refresh_local_review_db_from_github()
    if user.id == interaction.user.id:
        await interaction.response.send_message('❌ You cannot update a review for yourself.', ephemeral=True)
        return
    current = get_user_review(user.id, interaction.user.id)
    if not current:
        await interaction.response.send_message('❌ You have not reviewed this user yet. Use `/review` first.', ephemeral=True)
        return
    await interaction.response.send_modal(UpdateReviewModal(user, current))


async def review_approval_allowed(interaction: discord.Interaction) -> bool:
    return (
        isinstance(interaction.user, discord.Member)
        and any(role.id in DELETE_REVIEW_ALLOWED_ROLE_IDS for role in interaction.user.roles)
    )


async def handle_review_update_decision(interaction: discord.Interaction, request_id: int, approve: bool):
    if interaction.channel_id != REVIEW_UPDATE_APPROVAL_CHANNEL_ID:
        await interaction.response.send_message(
            f'❌ Review updates must be approved or rejected in <#{REVIEW_UPDATE_APPROVAL_CHANNEL_ID}>.',
            ephemeral=True,
        )
        return
    if not await review_approval_allowed(interaction):
        await interaction.response.send_message('❌ You do not have permission to approve or reject review updates.', ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    request, status = await asyncio.to_thread(complete_review_update, request_id, interaction.user.id, approve)
    if request is None:
        await interaction.followup.send('❌ That review update has already been handled or does not exist.', ephemeral=True)
        return
    try:
        channel = bot.get_channel(request['approval_channel_id']) if request['approval_channel_id'] else None
        if channel is None and request['approval_channel_id']:
            channel = await bot.fetch_channel(request['approval_channel_id'])
        if channel is not None and request['approval_message_id']:
            message = await channel.fetch_message(request['approval_message_id'])
            await message.edit(
                embed=review_update_embed(request, status, interaction.user.id),
                view=ReviewUpdateApprovalView(request_id, disabled=True),
            )
    except Exception:
        pass
    asyncio.create_task(sync_review_db_to_github_locked())
    if status == 'approved':
        text = f'✅ Review update #{request_id} approved. The vote has been updated.'
    elif status == 'rejected':
        text = f'✅ Review update #{request_id} rejected. The original vote remains unchanged.'
    else:
        text = f'⚠️ Review update #{request_id} was cancelled because the original review no longer exists.'
    await interaction.followup.send(text, ephemeral=True)


async def register_pending_review_update_views():
    """Re-register persistent buttons after a bot restart."""
    for request in get_pending_review_update_requests():
        try:
            bot.add_view(
                ReviewUpdateApprovalView(request['id']),
                message_id=request['approval_message_id'],
            )
        except Exception:
            pass

# ============================================================
# /reviews
# ============================================================

@tree.command(
    name="reviews",
    description="View a member's reviews."
)
@app_commands.describe(
    user="The member whose reviews you want to see."
)
async def reviews(
    interaction: discord.Interaction,
    user: discord.Member
):

    await refresh_local_review_db_from_github()
    review_list = get_reviews(user.id)

    if not review_list:
        await interaction.response.send_message(
            f"**{user.display_name}** has no reviews yet.",
            ephemeral=True
        )
        return

    view = ReviewPagination(
        target=user,
        reviews=review_list
    )

    await interaction.response.send_message(
        embed=view.make_embed(),
        view=view
    )



# ============================================================
# LEADERBOARD
# ============================================================

@tree.command(
    name="leaderboard",
    description="View the reputation leaderboard."
)
async def leaderboard(interaction: discord.Interaction):
    # Acknowledge the interaction immediately. GitHub/API work can take longer
    # than Discord's initial interaction response window.
    await interaction.response.defer()

    try:
        # The shared review store is the source used by the leaderboard.
        # Load it once instead of refreshing the SQLite DB and then making
        # three additional GitHub reads (one for each leaderboard section).
        store = await asyncio.to_thread(_load_shared_review_store)
        reviews = store.get("reviews", [])

        # Aggregate everything from the same snapshot so all three sections
        # are consistent with one another and do not repeatedly hit GitHub.
        stats_by_target = {}
        liked_counts = {}
        neutral_counts = {}
        disliked_counts = {}

        for review in reviews:
            target_id = int(review["target_id"])
            rating = int(review["rating"])

            stats = stats_by_target.setdefault(
                target_id,
                {"total": 0, "approved": 0, "rating_sum": 0},
            )
            stats["total"] += 1
            stats["rating_sum"] += rating

            if rating in (4, 5):
                stats["approved"] += 1
                liked_counts[target_id] = liked_counts.get(target_id, 0) + 1
            elif rating == 3:
                neutral_counts[target_id] = neutral_counts.get(target_id, 0) + 1
            elif rating in (1, 2):
                disliked_counts[target_id] = disliked_counts.get(target_id, 0) + 1

        liked = sorted(
            (
                {"target_id": target_id, "approved": count}
                for target_id, count in liked_counts.items()
            ),
            key=lambda row: (-row["approved"], row["target_id"]),
        )[:5]

        reviewed = sorted(
            (
                {
                    "target_id": target_id,
                    "review_count": values["total"],
                    "average_rating": (
                        values["rating_sum"] / values["total"]
                        if values["total"]
                        else 0
                    ),
                }
                for target_id, values in stats_by_target.items()
            ),
            key=lambda row: (
                -row["review_count"],
                -row["average_rating"],
                row["target_id"],
            ),
        )[:5]

        neutral = sorted(
            (
                {"target_id": target_id, "neutral": count}
                for target_id, count in neutral_counts.items()
            ),
            key=lambda row: (-row["neutral"], row["target_id"]),
        )[:5]

        disliked = sorted(
            (
                {"target_id": target_id, "disliked": count}
                for target_id, count in disliked_counts.items()
            ),
            key=lambda row: (-row["disliked"], row["target_id"]),
        )[:5]

        embed = discord.Embed(
            title="leaderboard",
            color=discord.Color.dark_grey()
        )

        # Resolve each leaderboard target only once.
        target_ids = []
        for rows in (liked, reviewed, neutral, disliked):
            for row in rows:
                target_id = row["target_id"]
                if target_id not in target_ids:
                    target_ids.append(target_id)

        members = {}
        if interaction.guild is not None:
            for target_id in target_ids:
                members[target_id] = await get_review_member_or_user(
                    interaction.guild,
                    target_id,
                )

        liked_text = "**top 5 most liked users**\n\n"
        if not liked:
            liked_text += "No reviews yet."
        else:
            for index, row in enumerate(liked, start=1):
                target_id = row["target_id"]
                member = members.get(target_id)
                name = member.mention if member else f"<@{target_id}>"
                username = member.name if member else "unknown"
                stats = stats_by_target.get(
                    target_id,
                    {"total": 0, "approved": 0},
                )
                approval = (
                    (stats["approved"] / stats["total"]) * 100
                    if stats["total"]
                    else 0
                )
                liked_text += (
                    f"{index} {name} ({username}) 🟢 **{row['approved']}** "
                    f"({approval:.2f}% approval)\n"
                )
        embed.add_field(
            name="Most Liked",
            value=liked_text,
            inline=False,
        )

        reviewed_text = "**top 5 most reviewed**\n\n"
        if not reviewed:
            reviewed_text += "No reviews yet."
        else:
            for index, row in enumerate(reviewed, start=1):
                target_id = row["target_id"]
                member = members.get(target_id)
                name = member.mention if member else f"<@{target_id}>"
                username = member.name if member else "unknown"
                reviewed_text += (
                    f"{index} {name} ({username}) 📝 **{row['review_count']} reviews** "
                    f"(⭐ {row['average_rating']:.1f} avg)\n"
                )
        embed.add_field(
            name="Most Reviewed",
            value=reviewed_text,
            inline=False,
        )

        disliked_text = "**top 5 most disliked users**\n\n"
        if not disliked:
            disliked_text += "No negative reviews yet."
        else:
            for index, row in enumerate(disliked, start=1):
                target_id = row["target_id"]
                member = members.get(target_id)
                name = member.mention if member else f"<@{target_id}>"
                username = member.name if member else "unknown"
                stats = stats_by_target.get(
                    target_id,
                    {"total": 0, "approved": 0},
                )
                approval = (
                    (stats["approved"] / stats["total"]) * 100
                    if stats["total"]
                    else 0
                )
                disliked_text += (
                    f"{index} {name} ({username}) 🔴 **{row['disliked']} negative reviews** "
                    f"({approval:.2f}% approval)\n"
                )
        embed.add_field(
            name="Most Disliked",
            value=disliked_text,
            inline=False,
        )

        await interaction.followup.send(embed=embed)

    except Exception as error:
        await interaction.followup.send(
            "❌ I couldn't load the leaderboard right now. "
            "Please try again in a moment.",
            ephemeral=True,
        )
        print(
            f"Leaderboard command failed: {type(error).__name__}: {error}"
        )


# ============================================================
# MODERATOR DELETE COMMAND
# ============================================================

def deletereview_role_allowed(interaction: discord.Interaction) -> bool:
    """Allow /deletereview only to members with one of the configured roles."""
    if not isinstance(interaction.user, discord.Member):
        return False
    return any(
        role.id in DELETE_REVIEW_ALLOWED_ROLE_IDS
        for role in interaction.user.roles
    )


@tree.command(
    name="deletereview",
    description="Delete a review by ID."
)
@app_commands.describe(
    review_id="The review ID to delete."
)
@app_commands.check(deletereview_role_allowed)
async def deletereview(
    interaction: discord.Interaction,
    review_id: int
):

    await refresh_local_review_db_from_github()
    review = get_review(review_id)

    if not review:
        await interaction.response.send_message(
            "❌ Review not found.",
            ephemeral=True
        )
        return

    delete_review(review_id)
    asyncio.create_task(sync_review_db_to_github_locked())

    await log_review_event(
        "Review Removed",
        f"{interaction.user.mention} removed review #{review_id}.",
        fields=[
            ("Review ID", f"Review #{review_id}", True),
            ("Removed By", f"{interaction.user.mention} / {interaction.user.id}", False),
            ("Reviewer", f"<@{review['reviewer_id']}> / {review['reviewer_id']}", False),
            ("Target", f"<@{review['target_id']}> / {review['target_id']}", False),
            ("Rating", review_stars(int(review["rating"])), True),
            ("Comment", str(review.get("comment") or "No comment")[:1024], False),
        ],
        color=discord.Color.red(),
    )

    await interaction.response.send_message(
        f"✅ Review `{review_id}` deleted.",
        ephemeral=True
    )


# ============================================================
# ERROR HANDLER
# ============================================================

@deletereview.error
async def deletereview_error(
    interaction: discord.Interaction,
    error
):

    if isinstance(error, app_commands.errors.CheckFailure):
        await interaction.response.send_message(
            "❌ You do not have one of the required roles to use `/deletereview`.",
            ephemeral=True
        )
    else:
        raise error


# =========================
# REVIEW DATABASE SYNC LOOP + MANUAL SAVE
# =========================

review_db_auto_sync_task: asyncio.Task | None = None
staff_strike_expiry_task: asyncio.Task | None = None


async def staff_strike_expiry_worker():
    """Expire staff strikes, restore/demote roles, and announce promotions."""
    await bot.wait_until_ready()

    while not bot.is_closed():
        try:
            expired = prune_expired_staff_strikes()
            if expired:
                await handle_expired_staff_strikes(expired)
                await save_staff_strikes()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

        await asyncio.sleep(STAFF_STRIKE_EXPIRY_CHECK_SECONDS)


async def review_db_auto_sync_worker():
    """Reliably sync the review database immediately and every 30 minutes."""
    await bot.wait_until_ready()

    while not bot.is_closed():
        try:
            success, error = await sync_review_db_to_github_locked()
            if not success:
                print(f"Review database auto-save failed: {error}")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            print(f"Review database auto-save crashed: {type(error).__name__}: {error}")

        await asyncio.sleep(REVIEW_DB_SYNC_MINUTES * 60)


@tree.command(
    name="savedb",
    description="Immediately save the review database to GitHub."
)
async def savedb_command(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(
            "❌ This command can only be used inside a server.",
            ephemeral=True,
        )
        return

    if REVIEW_DB_SAVE_ROLE_ID not in {role.id for role in interaction.user.roles}:
        await interaction.response.send_message(
            "❌ You do not have permission to use `/savedb`.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)
    async with review_db_lock:
        success, error = await sync_review_db_to_github()

    if success:
        await interaction.followup.send(
            "✅ The review database was saved to GitHub successfully.",
            ephemeral=True,
        )
    else:
        await interaction.followup.send(
            f"❌ I could not save the review database to GitHub.\n`{error}`",
            ephemeral=True,
        )


# =========================
# BOT READY
# =========================

# Tracks whether the one allowed terminal status line has been shown.
terminal_status_printed = False


@bot.event
async def on_ready():
    global commands_synced, terminal_status_printed, review_db_restore_checked, staff_strike_expiry_task

    if not terminal_status_printed:
        print("Bot is alive")
        terminal_status_printed = True

    if not commands_synced:
        try:
            main_guild_object = discord.Object(id=MAIN_SERVER)
            # Replace stale guild command definitions so /review and
            # /deletereview use the current visibility/permission metadata.
            tree.clear_commands(guild=main_guild_object)
            tree.copy_global_to(guild=main_guild_object)
            await tree.sync(guild=main_guild_object)
            commands_synced = True
            pass
        except Exception as e:
            pass

    if not review_db_restore_checked:
        restored = await restore_review_db_from_github()
        if restored or _review_db_has_reviews() or not GITHUB_TOKEN:
            review_db_restore_checked = True

    # Always ensure the restored database has the review schema.
    _ensure_review_db_schema()
    try:
        await asyncio.to_thread(_ensure_shared_review_store)
    except Exception as error:
        print(f"Review store initialization failed: {type(error).__name__}: {error}")

    global review_update_views_registered
    if not review_update_views_registered:
        await register_pending_review_update_views()
        review_update_views_registered = True

    global review_db_auto_sync_task
    if review_db_auto_sync_task is None or review_db_auto_sync_task.done():
        try:
            success, error = await sync_review_db_to_github_locked()
            if not success:
                print(f"Review database initial save failed: {error}")
        except Exception as error:
            print(f"Review database initial save crashed: {type(error).__name__}: {error}")

        review_db_auto_sync_task = asyncio.create_task(
            review_db_auto_sync_worker()
        )

    if GITHUB_TOKEN and staff_strike_github_sync_error is None:
        try:
            await sync_staff_strikes_from_github()
        except Exception:
            pass

    startup_expired = prune_expired_staff_strikes()
    if startup_expired:
        await handle_expired_staff_strikes(startup_expired)
        await save_staff_strikes()

    if staff_strike_expiry_task is None or staff_strike_expiry_task.done():
        staff_strike_expiry_task = asyncio.create_task(
            staff_strike_expiry_worker()
        )

    global user_blacklists_synced
    if not user_blacklists_synced:
        if await sync_user_blacklists_from_github():
            user_blacklists_synced = True

    if not kick_loop.is_running():
        kick_loop.start()

    if not tag_role_loop.is_running():
        tag_role_loop.start()

    if not uwu_webhook_cleanup_loop.is_running():
        uwu_webhook_cleanup_loop.start()

    if not hood_webhook_cleanup_loop.is_running():
        hood_webhook_cleanup_loop.start()


# =========================
# KICK LOOP
# =========================

@tasks.loop(minutes=check_time)
async def kick_loop():

    main_guild = bot.get_guild(MAIN_SERVER)

    if main_guild is None:
        return

    main_members = {
        member.id
        for member in main_guild.members
    }

    for server_id in tag_servers:
        guild = bot.get_guild(server_id)

        if guild is None:
            continue

        for member in guild.members:
            if member.bot:
                continue

            # Anyone with ANY protected role will never be kicked.
            if any(role.id in PROTECTED_ROLE_IDS for role in member.roles):
                protected_roles = [
                    role for role in member.roles
                    if role.id in PROTECTED_ROLE_IDS
                ]

                continue

            if member.id not in main_members:
                try:
                    await member.send(
                        f"Hey {member.mention}, you were kicked from **{guild.name}** "
                        f"because you are not in the main server.\n\n"
                        f"Please join the main server here: {MAIN_SERVER_INVITE}"
                    )
                except Exception:
                    pass

                try:
                    await guild.kick(
                        member,
                        reason="not in main server",
                    )


                    await send_webhook(
                        KICK_WEBHOOK_URL,
                        title="🚫 Member Kicked",
                        description=(
                            f"{member.mention} was kicked from a tag server "
                            "because they are not in the main server."
                        ),
                        color=discord.Color.red(),
                        fields=[
                            ("User", f"{member} (`{member.id}`)", True),
                            ("Tag Server", f"{guild.name}\n`{guild.id}`", True),
                            ("Reason", "Not a member of the main server", False),
                        ],
                    )

                except Exception as e:

                    await send_webhook(
                        KICK_WEBHOOK_URL,
                        title="⚠️ Kick Failed",
                        description=(
                            f"Failed to kick {member.mention} "
                            f"from **{guild.name}**."
                        ),
                        color=discord.Color.orange(),
                        fields=[
                            ("User", f"{member} (`{member.id}`)", True),
                            ("Server", f"{guild.name}\n`{guild.id}`", True),
                            ("Error", f"`{e}`", False),
                        ],
                    )

                await asyncio.sleep(1)


# =========================
# TAG ROLE LOOP
# =========================

@tasks.loop(seconds=tag_check_time)
async def tag_role_loop():

    main_guild = bot.get_guild(MAIN_SERVER)

    if main_guild is None:
        return

    # Periodic sweep catches members who already had the trigger role when
    # the bot started. The member-update event handles newly added roles.
    if AUTO_REMOVE_TRIGGER_ROLE_ID and AUTO_REMOVE_ROLE_ID:
        for member in main_guild.members:
            if member.bot:
                continue
            await remove_auto_role_if_needed(
                member,
                reason="Periodic automatic-role-removal check",
            )

    tag_role = main_guild.get_role(TAG_ROLE_ID)

    if tag_role is None:
        return

    # Second main-server role is optional. Set TAG_ROLE_ID_2 to 0 to disable it.
    tag_role_2 = None
    second_role_ready = False
    if TAG_ROLE_ID_2:
        tag_role_2 = main_guild.get_role(TAG_ROLE_ID_2)
        if tag_role_2 is None:
            pass
        else:
            second_role_ready = True
            pass

    if len(tag_servers) != len(tag_server_role_ids):
        return

    if not await ensure_guild_members_loaded(main_guild):
        return

    bot_member = main_guild.me

    if bot_member is None:
        return

    if not bot_member.guild_permissions.manage_roles:
        return

    if tag_role.is_default():
        return

    if tag_role.managed:
        return

    if bot_member.top_role <= tag_role:
        return

    if second_role_ready and tag_role_2:
        if tag_role_2.is_default():
            second_role_ready = False

        elif tag_role_2.managed:
            second_role_ready = False

        elif bot_member.top_role <= tag_role_2:
            second_role_ready = False

    tagged_users = set()
    tagged_users_2 = set()
    second_role_check_failed = False
    second_role_servers_configured = 0


    # -------------------------
    # CHECK ALL FIRST-ROLE TAG SERVERS
    # -------------------------

    for server_id, tag_server_role_id in zip(
        tag_servers,
        tag_server_role_ids,
    ):
        guild = bot.get_guild(server_id)

        if guild is None:
            continue

        if not await ensure_guild_members_loaded(guild):
            continue

        tag_server_role = guild.get_role(tag_server_role_id)

        if tag_server_role is None:
            continue

        for member in guild.members:
            if member.bot:
                continue

            if tag_server_role in member.roles:
                tagged_users.add(member.id)

    # -------------------------
    # CHECK SECOND-ROLE SOURCE SERVERS
    # -------------------------
    # This is intentionally separate from tag_servers. A second-role source
    # server does NOT need to be in the first-role tag_servers list.

    if second_role_ready and tag_role_2:

        for source_server_id, source_role_id in tag_server_role_ids_2.items():
            if not source_role_id:
                continue

            second_role_servers_configured += 1

            source_guild = bot.get_guild(source_server_id)

            if source_guild is None:
                second_role_check_failed = True
                continue


            if not await ensure_guild_members_loaded(source_guild):
                second_role_check_failed = True
                continue

            source_role = source_guild.get_role(source_role_id)

            if source_role is None:
                try:
                    fetched_roles = await source_guild.fetch_roles()
                    source_role = next(
                        (role for role in fetched_roles if role.id == source_role_id),
                        None,
                    )
                except discord.HTTPException as e:
                    second_role_check_failed = True
                    continue

            if source_role is None:
                second_role_check_failed = True
                continue


            found_count = 0
            for source_member in source_guild.members:
                if source_member.bot:
                    continue

                if source_role in source_member.roles:
                    tagged_users_2.add(source_member.id)
                    found_count += 1

            pass

    # -------------------------
    # FIRST MAIN ROLE
    # -------------------------

    for member in main_guild.members:
        if member.bot:
            continue

        has_tag = member.id in tagged_users
        has_role = tag_role in member.roles

        if has_tag and not has_role:
            if main_guild.owner_id == member.id:
                continue

            if member.top_role >= bot_member.top_role:
                continue

            try:
                await member.add_roles(
                    tag_role,
                    reason="User has a configured first tag role in a tag server",
                )

                await send_webhook(
                    ROLE_WEBHOOK_URL,
                    title="🏷️ Tag Role Added",
                    description=f"{member.mention} was given the main tag role.",
                    color=discord.Color.green(),
                    fields=[
                        ("User", f"{member} (`{member.id}`)", True),
                        ("Role", f"{tag_role.mention}\n`{tag_role.id}`", True),
                        (
                            "Reason",
                            "User has a configured tag role in a tag server.",
                            False,
                        ),
                    ],
                )
            except discord.Forbidden as e:
                pass
            except discord.HTTPException as e:
                pass
            except Exception as e:
                pass

            await asyncio.sleep(0.5)

        elif not has_tag and has_role:
            try:
                await member.remove_roles(
                    tag_role,
                    reason="User no longer has the configured first tag role",
                )

                await send_webhook(
                    ROLE_WEBHOOK_URL,
                    title="🏷️ Tag Role Removed",
                    description=(
                        f"{member.mention} no longer has the configured "
                        "first tag role in any tag server."
                    ),
                    color=discord.Color.orange(),
                    fields=[
                        ("User", f"{member} (`{member.id}`)", True),
                        ("Role", f"{tag_role.mention}\n`{tag_role.id}`", True),
                        (
                            "Reason",
                            "User no longer has a configured tag role.",
                            False,
                        ),
                    ],
                )
            except discord.Forbidden as e:
                pass
            except discord.HTTPException as e:
                pass
            except Exception as e:
                pass

            await asyncio.sleep(0.5)

    # -------------------------
    # SECOND MAIN ROLE
    # -------------------------

    if second_role_ready and tag_role_2 and second_role_servers_configured > 0 and not second_role_check_failed:
        for member in main_guild.members:
            if member.bot:
                continue

            has_tag_2 = member.id in tagged_users_2
            has_role_2 = tag_role_2 in member.roles
            is_blacklisted = member.id in second_role_blacklist

            # Blacklisted members must never receive the second role.
            # If they already have it, remove it here as well.
            if is_blacklisted:
                if has_role_2:
                    if (
                        main_guild.owner_id == member.id
                        or member.top_role >= bot_member.top_role
                    ):
                        continue

                    try:
                        await member.remove_roles(
                            tag_role_2,
                            reason="User is on the second-role blacklist",
                        )

                        await send_webhook(
                            ROLE_WEBHOOK_URL,
                            title="🚫 Second Role Removed (Blacklisted)",
                            description=(
                                f"{member.mention} was prevented from keeping "
                                "the second main tag role because they are blacklisted."
                            ),
                            color=discord.Color.red(),
                            fields=[
                                ("User", f"{member} (`{member.id}`)", True),
                                ("Role", f"{tag_role_2.mention}\n`{tag_role_2.id}`", True),
                                ("Reason", "Member is on the second-role blacklist.", False),
                            ],
                        )
                    except discord.Forbidden as e:
                        pass
                    except discord.HTTPException as e:
                        pass
                    except Exception as e:
                        pass

                    await asyncio.sleep(0.5)

                continue

            if has_tag_2 and not has_role_2:
                if main_guild.owner_id == member.id:
                    continue

                if member.top_role >= bot_member.top_role:
                    continue

                try:
                    await member.add_roles(
                        tag_role_2,
                        reason="User has the configured second tag role in a tag server",
                    )

                    await send_webhook(
                        ROLE_WEBHOOK_URL,
                        title="🏷️ Second Tag Role Added",
                        description=(
                            f"{member.mention} was given the second main tag role."
                        ),
                        color=discord.Color.green(),
                        fields=[
                            ("User", f"{member} (`{member.id}`)", True),
                            ("Role", f"{tag_role_2.mention}\n`{tag_role_2.id}`", True),
                            (
                                "Reason",
                                "User has the configured second tag role in a tag server.",
                                False,
                            ),
                        ],
                    )
                except discord.Forbidden as e:
                    pass
                except discord.HTTPException as e:
                    pass
                except Exception as e:
                    pass

                await asyncio.sleep(0.5)

            elif not has_tag_2 and has_role_2:
                try:
                    await member.remove_roles(
                        tag_role_2,
                        reason="User no longer has the configured second tag role",
                    )

                    await send_webhook(
                        ROLE_WEBHOOK_URL,
                        title="🏷️ Second Tag Role Removed",
                        description=(
                            f"{member.mention} no longer has the configured "
                            "second tag role in any tag server."
                        ),
                        color=discord.Color.orange(),
                        fields=[
                            ("User", f"{member} (`{member.id}`)", True),
                            ("Role", f"{tag_role_2.mention}\n`{tag_role_2.id}`", True),
                            (
                                "Reason",
                                "User no longer has a configured second tag role.",
                                False,
                            ),
                        ],
                    )
                except discord.Forbidden as e:
                    pass
                except discord.HTTPException as e:
                    pass
                except Exception as e:
                    pass

                await asyncio.sleep(0.5)


    elif TAG_ROLE_ID_2:
        if second_role_servers_configured == 0:
            pass
        elif second_role_check_failed:
            pass
        elif not second_role_ready:
            pass

# =========================
# LOOP ERROR HANDLERS
# =========================

@kick_loop.error
async def kick_loop_error(error):
    # A single unexpected exception should not permanently stop the kick loop.
    await asyncio.sleep(5)

    if not bot.is_closed() and not kick_loop.is_running():
        kick_loop.restart()


@tag_role_loop.error
async def tag_role_loop_error(error):
    # A single unexpected exception should not permanently stop the role loop.
    await asyncio.sleep(5)

    if not bot.is_closed() and not tag_role_loop.is_running():
        tag_role_loop.restart()


# =========================
# LOOP STARTUP
# =========================

@tag_role_loop.before_loop
async def before_tag_role():
    await bot.wait_until_ready()


@kick_loop.before_loop
async def before_kick():
    await bot.wait_until_ready()


# =========================
# START BOT
# =========================

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN is not configured. "
        "Set the BOT_TOKEN environment variable before starting the bot."
    )

bot.run(BOT_TOKEN)