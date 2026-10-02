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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
# How often to refresh source-server member/role data (seconds).
# The role assignment loop remains fast, but source data is not fetched every second.
TAG_SOURCE_REFRESH_SECONDS = 30

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
# Remove AUTO_REMOVE_ROLE_ID from anyone in MAIN_SERVER who has either:
# - the explicit trigger role below, or
# - any role listed in STAFF_ROLE_HIERARCHY.
AUTO_REMOVE_TRIGGER_ROLE_ID = 1378810715611336914
AUTO_REMOVE_TRIGGER_ROLE_IDS = {
    AUTO_REMOVE_TRIGGER_ROLE_ID,
    *STAFF_ROLE_IDS,
}
AUTO_REMOVE_ROLE_ID = 1372658714825457729


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
# MESSAGE CONTENT HELPER
# =========================

async def get_target_message_content(message: discord.Message) -> str:
    """Return message text, falling back to a REST fetch when Gateway content is empty."""
    content = (message.content or "").strip()
    if content:
        return content

    try:
        fresh_message = await message.channel.fetch_message(message.id)
        return (fresh_message.content or "").strip()
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return ""


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

# Discord itself is the authoritative source for target-specific webhooks.
# These small per-channel caches let overlapping Railway instances recover the
# active target immediately without scanning webhooks for every message.
uwu_webhook_recovery_cache: dict[int, float] = {}
hood_webhook_recovery_cache: dict[int, float] = {}
WEBHOOK_RECOVERY_CACHE_SECONDS = 3.0


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

    request = proxy_requests.pop(message.channel.id, None)
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

    # The proxy message must be deleted after transformation. Do not create a
    # duplicate transformed message when the bot cannot delete the original.
    bot_member = message.guild.me if message.guild is not None else None
    if (
        bot_member is None
        or not message.channel.permissions_for(bot_member).manage_messages
    ):
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
    prefix_command_parts = content.lower().split(maxsplit=1)
    prefix_command = prefix_command_parts[0] if prefix_command_parts else ""

    if prefix_command in {
        ",uwuify", ",unuwuify", ",hoodify", ",unhoodify", ",uwu", ",hood"
    } and uwu_hoodify_user_is_banned(message.author):
        await message.reply(
            "❌ You are banned from using UWUIFY and HOODIFY.",
            mention_author=False,
        )
        return

    # Register proxy requests after the operator-ban check. An active target's
    # proxy command is still recorded, while the command message itself
    # continues through the normal UWUIFY/HOODIFY transformation path.
    remember_proxy_request(message, content)

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
                await save_user_blacklist(TEXTIFY_BLACKLIST_FILE, textify_blacklist)
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
                    await save_user_blacklist(TEXTIFY_BLACKLIST_FILE, textify_blacklist)
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
    # Recover target state from Discord's target-specific webhooks so another
    # overlapping Railway instance cannot lose the active target state.
    now = time.monotonic()
    if (
        now - uwu_webhook_recovery_cache.get(message.channel.id, 0.0)
        >= WEBHOOK_RECOVERY_CACHE_SECONDS
    ):
        await recover_uwu_targets_from_channel(message.channel)
        uwu_webhook_recovery_cache[message.channel.id] = now

    if (
        now - hood_webhook_recovery_cache.get(message.channel.id, 0.0)
        >= WEBHOOK_RECOVERY_CACHE_SECONDS
    ):
        await recover_hood_targets_from_channel(message.channel)
        hood_webhook_recovery_cache[message.channel.id] = now

    hood_target_ids = hood_targets.get(message.channel.id, set())
    uwu_target_ids = uwu_targets.get(message.channel.id, set())

    if message.author.id in hood_target_ids:
        target_content = await get_target_message_content(message)

        # Ignore empty/attachment-only messages.
        if not target_content:
            return

        if message.author.id in hood_user_blacklist:
            await disable_hood_for_user(message.author.id)
            return

        bot_member = message.guild.me if message.guild is not None else None
        permissions = (
            message.channel.permissions_for(bot_member)
            if bot_member is not None
            else None
        )
        if permissions is None or not permissions.manage_messages or not permissions.manage_webhooks:
            await disable_hood_for_user(message.author.id)
            try:
                await message.reply(
                    "❌ HOODIFY was disabled because I need **Manage Messages** "
                    "and **Manage Webhooks** permissions in this channel.",
                    mention_author=False,
                )
            except (discord.Forbidden, discord.HTTPException):
                pass
            return

        try:
            sent_messages = await send_hood_message(message.channel, message.author, target_content)
            deleted = await delete_original_message(message)
            if not deleted:
                try:
                    await message.reply(
                        "⚠️ HOODIFY transformed your message, but I could not delete "
                        "the original. Check that I have **Manage Messages** in this channel.",
                        mention_author=False,
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass
        except HoodUserBlacklisted:
            await disable_hood_for_user(message.author.id)
        except HoodMessageBlocked:
            return
        except (discord.Forbidden, discord.HTTPException):
            await disable_hood_for_user(message.author.id)
        except Exception as error:
            print(
                "HOODIFY automatic message error: "
                f"{type(error).__name__}: {error}"
            )
            return
        return

    if message.author.id in uwu_target_ids:
        target_content = await get_target_message_content(message)

        # Ignore empty/attachment-only messages.
        if not target_content:
            return

        if message.author.id in uwu_user_blacklist:
            await disable_uwu_for_user(message.author.id)
            return

        bot_member = message.guild.me if message.guild is not None else None
        permissions = (
            message.channel.permissions_for(bot_member)
            if bot_member is not None
            else None
        )
        if permissions is None or not permissions.manage_messages or not permissions.manage_webhooks:
            await disable_uwu_for_user(message.author.id)
            try:
                await message.reply(
                    "❌ UWUIFY was disabled because I need **Manage Messages** "
                    "and **Manage Webhooks** permissions in this channel.",
                    mention_author=False,
                )
            except (discord.Forbidden, discord.HTTPException):
                pass
            return

        try:
            sent_messages = await send_uwu_message(message.channel, message.author, target_content)
            deleted = await delete_original_message(message)
            if not deleted:
                try:
                    await message.reply(
                        "⚠️ UWUIFY transformed your message, but I could not delete "
                        "the original. Check that I have **Manage Messages** in this channel.",
                        mention_author=False,
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass
        except UwuUserBlacklisted:
            await disable_uwu_for_user(message.author.id)
        except UwuMessageBlocked:
            return
        except (discord.Forbidden, discord.HTTPException):
            await disable_uwu_for_user(message.author.id)
        except Exception as error:
            print(
                "UWUIFY automatic message error: "
                f"{type(error).__name__}: {error}"
            )
            return
        return

    # Only non-target users continue into the command parser.

    # Translate readable mode/count prefixes to the existing command parser.
    if len(spaced_parts) >= 2:
        spaced_root = spaced_parts[0].lower()
        spaced_action = spaced_parts[1].lower()
        if spaced_root == ",uwu" and spaced_action == "count":
            content = ",uwucount"
        if spaced_root == ",uwu" and spaced_action == "count":
            content = ",uwucount"
        elif spaced_root == ",uwu" and message.mentions:
            rest = spaced_parts[2] if len(spaced_parts) >= 3 else ""
            content = ",uwuify " + spaced_action + (f" {rest}" if rest else "")
        elif spaced_root == ",hood" and spaced_action == "count":
            content = ",hoodcount"
        elif spaced_root == ",hood" and message.mentions:
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

    # Prefix UWUIFY enable command.
    # Use ,unuwuify @user to disable a member. The old ,uwuify on/off forms
    # are intentionally removed.
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
        if len(parts) < 2 or not message.mentions:
            await message.reply(
                "Usage: `,uwuify @user`",
                mention_author=False,
            )
            return

        target = message.mentions[0]

        bot_member = message.guild.me if message.guild is not None else None
        if bot_member is None or not (
            message.channel.permissions_for(bot_member).manage_messages
            and message.channel.permissions_for(bot_member).manage_webhooks
        ):
            await message.reply(
                "❌ I need **Manage Messages** and **Manage Webhooks** permission in this channel to replace messages.",
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
                    return

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
                f"❌ The global limit of {MAX_ACTIVE_UWU_TARGETS} UWUified people has been reached. "
                "Use `,unuwuify @user` or `/unuwuify @user` to disable UWU for one member.",
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

    # Prefix HOODIFY enable command.
    # Use ,unhoodify @user to disable a member. The old ,hoodify on/off forms
    # are intentionally removed.
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
        if len(parts) < 2 or not message.mentions:
            await message.reply(
                "Usage: `,hoodify @user`",
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
            await set_hood_target(message.channel, target)

            if len(parts) >= 3:
                try:
                    await send_hood_message(message.channel, target, parts[2])
                except HoodMessageBlocked as blocked_error:
                    await message.reply(
                        f"❌ That message was not sent through the HOODIFY webhook because it contains a blacklisted word/phrase: `{blocked_error}`",
                        mention_author=False,
                    )
                    return

            await message.reply(
                f"✅ HOODIFY is active for {target.mention} in this channel. "
                f"Active people: **{get_active_hood_target_count()}/{MAX_ACTIVE_HOOD_TARGETS}**.",
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
                f"❌ The global limit of {MAX_ACTIVE_HOOD_TARGETS} HOODIFIED people has been reached. "
                "Use `,unhoodify @user` or `/unhoodify @user` to disable HOODIFY for one member.",
                mention_author=False,
            )
        except discord.Forbidden:
            await message.reply(
                "❌ I need **Manage Messages** and **Manage Webhooks** permission in this channel/server.",
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
    # Review, leaderboard, and remaining staff prefix commands.
    try:
        if await handle_prefix_review_command(message, content):
            return
    except (discord.Forbidden, discord.HTTPException):
        return
    except Exception as error:
        print(
            f"Prefix command failed: {type(error).__name__}: {error}"
        )
        try:
            await message.reply(
                "❌ That prefix command could not be completed.",
                mention_author=False,
            )
        except (discord.Forbidden, discord.HTTPException):
            pass
        return


# =========================
# REVIEW / LEADERBOARD / STRIKE PREFIX COMMANDS
# =========================

async def interaction_from_prefix_update_modal(message, target, current_review):
    await message.reply(
        "Click **Update Review** to open the update form.",
        view=PrefixUpdateView(target, current_review),
        mention_author=False,
    )


class PrefixUpdateView(discord.ui.View):
    def __init__(self, target, current_review):
        super().__init__(timeout=120)
        self.target = target
        self.current_review = current_review

    @discord.ui.button(label="Update Review", style=discord.ButtonStyle.primary)
    async def open_modal(self, interaction, button):
        if interaction.user.id != self.current_review["reviewer_id"]:
            await interaction.response.send_message(
                "Only the person who wrote this review can update it.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            UpdateReviewModal(self.target, self.current_review)
        )


async def send_prefix_leaderboard(message):
    try:
        store = await asyncio.to_thread(_load_shared_review_store)
        reviews = store.get("reviews", [])
        stats = {}
        liked = {}
        neutral = {}
        disliked = {}

        for review in reviews:
            target_id = int(review["target_id"])
            rating = int(review["rating"])
            info = stats.setdefault(target_id, {"total": 0, "approved": 0, "sum": 0})
            info["total"] += 1
            info["sum"] += rating
            if rating in (4, 5):
                info["approved"] += 1
                liked[target_id] = liked.get(target_id, 0) + 1
            elif rating == 3:
                neutral[target_id] = neutral.get(target_id, 0) + 1
            else:
                disliked[target_id] = disliked.get(target_id, 0) + 1

        liked_rows = sorted(liked.items(), key=lambda item: (-item[1], item[0]))[:5]
        reviewed_rows = sorted(stats.items(), key=lambda item: (-item[1]["total"], -(item[1]["sum"] / item[1]["total"]), item[0]))[:5]
        neutral_rows = sorted(neutral.items(), key=lambda item: (-item[1], item[0]))[:5]
        disliked_rows = sorted(disliked.items(), key=lambda item: (-item[1], item[0]))[:5]

        target_ids = []
        for target_id, _ in liked_rows + reviewed_rows + neutral_rows + disliked_rows:
            if target_id not in target_ids:
                target_ids.append(target_id)

        members = {}
        if message.guild is not None:
            for target_id in target_ids:
                members[target_id] = await get_review_member_or_user(message.guild, target_id)

        def label(target_id):
            member = members.get(target_id)
            return (member.mention if member else f"<@{target_id}>", member.name if member else "unknown")

        embed = discord.Embed(title="leaderboard", color=discord.Color.dark_grey())

        text = "**top 5 most liked users**\\n\\n"
        if liked_rows:
            for index, (target_id, count) in enumerate(liked_rows, 1):
                mention, username = label(target_id)
                info = stats[target_id]
                approval = info["approved"] / info["total"] * 100
                text += f"{index} {mention} ({username}) 🟢 **{count}** ({approval:.2f}% approval)\\n"
        else:
            text += "No reviews yet."
        embed.add_field(name="Most Liked", value=text, inline=False)

        text = "**top 5 most reviewed**\\n\\n"
        if reviewed_rows:
            for index, (target_id, info) in enumerate(reviewed_rows, 1):
                mention, username = label(target_id)
                average = info["sum"] / info["total"] if info["total"] else 0
                text += f"{index} {mention} ({username}) 📝 **{info['total']} reviews** (⭐ {average:.1f} avg)\\n"
        else:
            text += "No reviews yet."
        embed.add_field(name="Most Reviewed", value=text, inline=False)

        text = "**top 5 neutral users**\\n\\n"
        if neutral_rows:
            for index, (target_id, count) in enumerate(neutral_rows, 1):
                mention, username = label(target_id)
                text += f"{index} {mention} ({username}) 🟠 **{count} neutral reviews**\\n"
        else:
            text += "No 3-star reviews yet."
        embed.add_field(name="Neutral (3 Stars)", value=text, inline=False)

        text = "**top 5 most disliked users**\\n\\n"
        if disliked_rows:
            for index, (target_id, count) in enumerate(disliked_rows, 1):
                mention, username = label(target_id)
                info = stats[target_id]
                approval = info["approved"] / info["total"] * 100
                text += f"{index} {mention} ({username}) 🔴 **{count} negative reviews** ({approval:.2f}% approval)\\n"
        else:
            text += "No negative reviews yet."
        embed.add_field(name="Most Disliked", value=text, inline=False)

        await message.reply(embed=embed, mention_author=False)
    except Exception as error:
        print(f"Prefix leaderboard failed: {type(error).__name__}: {error}")
        await message.reply(
            "I couldn't load the leaderboard right now.",
            mention_author=False,
        )


async def handle_prefix_review_command(message, content):
    parts = content.split(maxsplit=2)
    command = parts[0].lower() if parts else ""

    if command == ",review":
        if not message.mentions:
            await message.reply("Usage: ,review @user", mention_author=False)
            return True
        if review_user_is_blacklisted(message.author.id):
            await message.reply("You are blacklisted from the review system.", mention_author=False)
            return True
        target = message.mentions[0]
        if target.id == message.author.id:
            await message.reply("You can't review yourself.", mention_author=False)
            return True
        if get_user_review(target.id, message.author.id):
            await message.reply("You already reviewed this user. Use ,updatereview @user to request a change.", mention_author=False)
            return True
        await message.reply("**select your star rating:**", view=StarView(target), mention_author=False)
        return True

    if command == ",updatereview":
        if not message.mentions:
            await message.reply("Usage: ,updatereview @user", mention_author=False)
            return True
        if review_user_is_blacklisted(message.author.id):
            await message.reply("You are blacklisted from the review system.", mention_author=False)
            return True
        target = message.mentions[0]
        if target.id == message.author.id:
            await message.reply("You cannot update a review for yourself.", mention_author=False)
            return True
        await refresh_local_review_db_from_github()
        current = get_user_review(target.id, message.author.id)
        if not current:
            await message.reply("You have not reviewed this user yet. Use ,review @user first.", mention_author=False)
            return True
        await interaction_from_prefix_update_modal(message, target, current)
        return True

    if command == ",reviews":
        if not message.mentions:
            await message.reply("Usage: ,reviews @user", mention_author=False)
            return True
        target = message.mentions[0]
        await refresh_local_review_db_from_github()
        review_list = get_reviews(target.id)
        if not review_list:
            await message.reply(f"{target.display_name} has no reviews yet.", mention_author=False)
            return True
        view = ReviewPagination(target=target, reviews=review_list)
        await message.reply(embed=view.make_embed(), view=view, mention_author=False)
        return True

    if command == ",deletereview":
        if len(parts) < 2:
            await message.reply("Usage: ,deletereview 123", mention_author=False)
            return True
        if not isinstance(message.author, discord.Member) or not any(role.id in DELETE_REVIEW_ALLOWED_ROLE_IDS for role in message.author.roles):
            await message.reply("You do not have permission to delete reviews.", mention_author=False)
            return True
        try:
            review_id = int(parts[1])
        except ValueError:
            await message.reply("Review ID must be a number.", mention_author=False)
            return True
        await refresh_local_review_db_from_github()
        review = get_review(review_id)
        if not review:
            await message.reply("Review not found.", mention_author=False)
            return True
        delete_review(review_id)
        asyncio.create_task(sync_review_db_to_github_locked())
        await log_review_event(
            "Review Removed",
            f"{message.author.mention} removed review #{review_id}.",
            fields=[
                ("Review ID", f"Review #{review_id}", True),
                ("Removed By", f"{message.author.mention} / {message.author.id}", False),
                ("Reviewer", f"<@{review['reviewer_id']}> / {review['reviewer_id']}", False),
                ("Target", f"<@{review['target_id']}> / {review['target_id']}", False),
                ("Rating", review_stars(int(review["rating"])), True),
                ("Comment", str(review.get("comment") or "No comment")[:1024], False),
            ],
            color=discord.Color.red(),
        )
        await message.reply(f"Review {review_id} deleted.", mention_author=False)
        return True

    if command == ",leaderboard":
        await send_prefix_leaderboard(message)
        return True

    if command == ",savedb":
        if not isinstance(message.author, discord.Member) or REVIEW_DB_SAVE_ROLE_ID not in {role.id for role in message.author.roles}:
            await message.reply("You do not have permission to use ,savedb.", mention_author=False)
            return True
        success, error = await sync_review_db_to_github_locked()
        if success:
            await message.reply("The review database was saved to GitHub successfully.", mention_author=False)
        else:
            await message.reply(f"Could not save the review database to GitHub: {error}", mention_author=False)
        return True

    if command == ",strikes":
        if not (message.guild and message.guild.id == MAIN_SERVER and isinstance(message.author, discord.Member) and staff_strike_command_allowed(message.author)):
            await message.reply("You do not have permission to use the staff strike system.", mention_author=False)
            return True
        if not message.mentions:
            await message.reply("Usage: ,strikes @user", mention_author=False)
            return True
        target = message.mentions[0]
        expired = prune_expired_staff_strikes()
        if expired:
            await handle_expired_staff_strikes(expired)
            await save_staff_strikes()
        strikes = get_user_staff_strikes(target.id)
        if not strikes:
            await message.reply(f"{target.mention} / {target.id} has no active strikes.", mention_author=False)
            return True
        await message.reply(
            f"{target.mention} / {target.id}\\n\\n" + "\\n\\n".join(f"strike #{int(s['strike_number'])}: {s['reason']}" for s in strikes),
            mention_author=False,
        )
        return True

    return False

# =========================
# WEBHOOK
# =========================

WEBHOOK_DEBUG = os.getenv("WEBHOOK_DEBUG", "1").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

last_webhook_error: str | None = None


def _report_webhook_error(title, error):
    """Print webhook failures when WEBHOOK_DEBUG is enabled."""
    global last_webhook_error
    last_webhook_error = f"{type(error).__name__}: {error}"

    if not WEBHOOK_DEBUG:
        return

    print(
        f"Webhook failed [{title}]: "
        f"{last_webhook_error}"
    )


async def send_webhook(
    webhook_url,
    title,
    description,
    color=discord.Color.blurple(),
    fields=None,
    webhook_name="WEBHOOK_URL",
) -> bool:
    """Send an embed through a Discord webhook and report whether it succeeded."""
    if not webhook_url:
        _report_webhook_error(
            title,
            RuntimeError(f"{webhook_name} is not configured."),
        )
        return False

    webhook_url = webhook_url.strip()

    # Discord can issue webhook URLs on several official Discord hosts,
    # including ptb.discord.com. Validate the webhook path instead of
    # requiring the stable discord.com hostname.
    parsed_webhook_url = urllib.parse.urlparse(webhook_url)
    webhook_host = (parsed_webhook_url.hostname or "").lower()
    valid_discord_host = (
        webhook_host == "discord.com"
        or webhook_host.endswith(".discord.com")
        or webhook_host == "discordapp.com"
        or webhook_host.endswith(".discordapp.com")
    )

    if (
        parsed_webhook_url.scheme != "https"
        or not valid_discord_host
        or not parsed_webhook_url.path.startswith("/api/webhooks/")
    ):
        _report_webhook_error(
            title,
            RuntimeError(
                f"{webhook_name} does not look like a Discord webhook URL."
            ),
        )
        return False

    try:
        # Use the bot client rather than Discord.py's private HTTP session.
        webhook = discord.Webhook.from_url(
            webhook_url.strip(),
            client=bot,
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

        # Do not send an empty username override. Discord can reject the
        # request when the override is present but blank.
        await webhook.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions(
                everyone=False,
                roles=False,
                users=True,
                replied_user=False,
            ),
            wait=True,
        )
        return True

    except (discord.Forbidden, discord.NotFound, discord.HTTPException) as error:
        _report_webhook_error(title, error)
        return False
    except Exception as error:
        _report_webhook_error(title, error)
        return False

async def send_role_webhook(
    title,
    description,
    color=discord.Color.blurple(),
    fields=None,
) -> bool:
    """Send a role-system event through ROLE_WEBHOOK_URL."""
    return await send_webhook(
        ROLE_WEBHOOK_URL,
        title=title,
        description=description,
        color=color,
        fields=fields,
        webhook_name="ROLE_WEBHOOK_URL",
    )


async def send_kick_webhook(
    title,
    description,
    color=discord.Color.blurple(),
    fields=None,
) -> bool:
    """Send a kick-system event through KICK_WEBHOOK_URL."""
    return await send_webhook(
        KICK_WEBHOOK_URL,
        title=title,
        description=description,
        color=color,
        fields=fields,
        webhook_name="KICK_WEBHOOK_URL",
    )



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

@textify_group.command(
    name="ban",
    description="Prevent a member from running UWUIFY or HOODIFY.",
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

    github_synced = await save_user_blacklist(TEXTIFY_BLACKLIST_FILE, textify_blacklist)

    removed = (
        await disable_uwu_for_user(member.id)
        + await disable_hood_for_user(member.id)
    )

    response = (
        f"✅ {member.mention} is now blacklisted from both UWUIFY and HOODIFY."
        + (f" Removed them from **{removed}** active mode(s)." if removed else "")
    )
    if not github_synced:
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

    github_synced = await save_user_blacklist(TEXTIFY_BLACKLIST_FILE, textify_blacklist)

    response = f"✅ {member.mention} can use UWUIFY and HOODIFY again."
    if not github_synced:
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

    await send_role_webhook(
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

    await send_role_webhook(
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


# Register grouped slash-command roots.
uwuify_group.add_command(uwuify_hoodify_group)
tree.add_command(textify_group)
tree.add_command(blacklist_group)

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


    original_error = getattr(error, "original", error)
    print(
        f"Slash command failed: {type(original_error).__name__}: "
        f"{original_error}"
    )

    if interaction.response.is_done():
        await interaction.followup.send(
            "An unexpected error occurred while running the command. "
            f"Error: {original_error}",
            ephemeral=False,
        )
    else:
        await interaction.response.send_message(
            "An unexpected error occurred while running the command. "
            f"Error: {original_error}",
            ephemeral=False,
        )


# =========================
# AUTOMATIC ROLE REMOVAL HELPER
# =========================

async def remove_auto_role_if_needed(member: discord.Member, reason: str) -> bool:
    """Remove the configured role whenever a member has any trigger role."""
    if not AUTO_REMOVE_TRIGGER_ROLE_IDS or not AUTO_REMOVE_ROLE_ID:
        return False

    if member.bot or member.guild.id != MAIN_SERVER:
        return False

    role_ids = {role.id for role in member.roles}

    # Any staff hierarchy role OR the explicit trigger role qualifies.
    if not role_ids.intersection(AUTO_REMOVE_TRIGGER_ROLE_IDS):
        return False

    role_to_remove = member.guild.get_role(AUTO_REMOVE_ROLE_ID)
    if role_to_remove is None or role_to_remove not in member.roles:
        return False

    if member.guild.owner_id == member.id:
        return False

    bot_member = member.guild.me
    if bot_member is None or not bot_member.guild_permissions.manage_roles:
        return False

    # The bot only needs its highest role to be above the role being removed.
    # The member's own highest role does not block removing this specific role.
    if role_to_remove.is_default() or role_to_remove.managed:
        return False

    if bot_member.top_role <= role_to_remove:
        return False

    try:
        await member.remove_roles(
            role_to_remove,
            reason=reason,
        )
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False
    except Exception:
        return False



# =========================
# MEMBER UPDATE EVENT
# =========================

@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    """Handle automatic removal and source-server tag-role changes."""
    if after.guild.id == MAIN_SERVER:
        # Enforce the rule whenever a qualifying trigger/staff role exists.
        # This catches both newly-added roles and cases where the removable
        # role was added while the trigger was already present.
        if AUTO_REMOVE_TRIGGER_ROLE_IDS and AUTO_REMOVE_ROLE_ID:
            await remove_auto_role_if_needed(
                after,
                reason="Member has a configured automatic-removal trigger or staff role",
            )
        return

    source_pairs = dict(zip(tag_servers, tag_server_role_ids))
    relevant_role_ids = set()
    if after.guild.id in source_pairs:
        relevant_role_ids.add(source_pairs[after.guild.id])
    if after.guild.id in tag_server_role_ids_2:
        relevant_role_ids.add(tag_server_role_ids_2[after.guild.id])

    if relevant_role_ids:
        before_ids = {role.id for role in before.roles}
        after_ids = {role.id for role in after.roles}
        if any(role_id in before_ids or role_id in after_ids for role_id in relevant_role_ids):
            await sync_tag_roles_for_source_member(after)

# =========================
# MEMBER CACHE HELPER
# =========================

async def ensure_guild_members_loaded(guild: discord.Guild) -> bool:
    """Make sure the guild member cache is populated before role checks."""
    try:
        if not guild.chunked:
            await guild.chunk(cache=True)
        return True
    except discord.HTTPException:
        return False
    except Exception:
        return False


# Source-server role/member refresh cache.
# key = guild ID, value = {"expires": monotonic_time, "members": list[discord.Member]}
tag_source_member_cache: dict[int, dict] = {}


async def get_source_members_for_role_check(
    guild: discord.Guild,
) -> list[discord.Member] | None:
    """Return a current-enough member list without hammering Discord's API."""
    now = time.monotonic()
    cached = tag_source_member_cache.get(guild.id)

    if cached is not None and float(cached.get("expires", 0)) > now:
        return list(cached.get("members", []))

    # With the Members intent enabled, use the complete gateway cache when
    # discord.py reports that the guild has finished chunking.
    cached_members = [
        member for member in guild.members
        if not member.bot
    ]
    if guild.chunked:
        tag_source_member_cache[guild.id] = {
            "expires": now + TAG_SOURCE_REFRESH_SECONDS,
            "members": cached_members,
        }
        return cached_members

    # A partial cache must not be treated as the complete member list: doing
    # so can make the kick system incorrectly think somebody is absent from
    # the main server. Ask Discord for the remaining chunks first.
    try:
        await ensure_guild_members_loaded(guild)
    except Exception:
        pass

    cached_members = [
        member for member in guild.members
        if not member.bot
    ]
    if guild.chunked:
        tag_source_member_cache[guild.id] = {
            "expires": now + TAG_SOURCE_REFRESH_SECONDS,
            "members": cached_members,
        }
        return cached_members

    # Last resort for a cold cache: use Discord's REST member endpoint.
    try:
        members = [
            member
            async for member in guild.fetch_members(limit=None)
            if not member.bot
        ]
        tag_source_member_cache[guild.id] = {
            "expires": now + TAG_SOURCE_REFRESH_SECONDS,
            "members": members,
        }
        return members
    except (discord.Forbidden, discord.HTTPException):
        return None
    except Exception:
        return None


async def get_source_role_for_check(
    guild: discord.Guild,
    role_id: int,
) -> discord.Role | None:
    """Get a source role from cache, falling back to Discord when necessary."""
    role = guild.get_role(role_id)
    if role is not None:
        return role

    try:
        roles = await guild.fetch_roles()
        return next((candidate for candidate in roles if candidate.id == role_id), None)
    except (discord.Forbidden, discord.HTTPException):
        return None
    except Exception:
        return None


# ============================================================
# TAG-SERVER ROLE EVENT SYNC
# ============================================================

async def sync_tag_roles_for_source_member(source_member: discord.Member) -> None:
    """Immediately reconcile main-server tag roles for a source-server member."""
    if source_member.bot:
        return

    main_guild = bot.get_guild(MAIN_SERVER)
    if main_guild is None:
        return

    # Fetch the bot's member record fresh so Discord's current role
    # positions are used for hierarchy checks.
    try:
        bot_member = await main_guild.fetch_member(bot.user.id)
    except (discord.NotFound, discord.HTTPException):
        bot_member = main_guild.me

    if bot_member is None or not bot_member.guild_permissions.manage_roles:
        print("Role sync skipped: bot does not currently have Manage Roles.")
        return

    main_member = main_guild.get_member(source_member.id)
    if main_member is None:
        try:
            main_member = await main_guild.fetch_member(source_member.id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return

    if main_member.guild.owner_id == main_member.id:
        return

    source_role_ids = {role.id for role in source_member.roles}
    source_pairs = dict(zip(tag_servers, tag_server_role_ids))

    current_source_role_id = source_pairs.get(source_member.guild.id)
    if current_source_role_id:
        first_main_role = main_guild.get_role(TAG_ROLE_ID)
        if (
            first_main_role is not None
            and not first_main_role.managed
            and not first_main_role.is_default()
            and bot_member.top_role > first_main_role
        ):
            has_current_source_role = current_source_role_id in source_role_ids

            if has_current_source_role and first_main_role not in main_member.roles:
                try:
                    await main_member.add_roles(
                        first_main_role,
                        reason="Configured tag-server source role detected",
                    )
                    await send_role_webhook(
                        title="🏷️ Tag Role Added",
                        description=f"{main_member.mention} was given the main tag role.",
                        color=discord.Color.green(),
                        fields=[
                            ("User", f"{main_member} ({main_member.id})", True),
                            ("Role", f"{first_main_role.mention} ({first_main_role.id})", True),
                            ("Reason", "Configured tag-server source role detected.", False),
                        ],
                    )
                except (discord.Forbidden, discord.HTTPException) as error:
                    print(
                        "Immediate first tag-role assignment failed for "
                        f"{main_member} ({main_member.id}) -> role {first_main_role.id}: "
                        f"{type(error).__name__}: {error}"
                    )

            elif not has_current_source_role and first_main_role in main_member.roles:
                still_qualified = False
                for server_id, role_id in source_pairs.items():
                    if server_id == source_member.guild.id:
                        continue
                    other_guild = bot.get_guild(server_id)
                    if other_guild is None:
                        continue
                    other_member = other_guild.get_member(source_member.id)
                    if other_member is None:
                        try:
                            other_member = await other_guild.fetch_member(source_member.id)
                        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                            other_member = None

                    if (
                        other_member is not None
                        and role_id in {r.id for r in other_member.roles}
                    ):
                        still_qualified = True
                        break

                if not still_qualified:
                    try:
                        await main_member.remove_roles(
                            first_main_role,
                            reason="Configured source role was removed",
                        )
                        await send_role_webhook(
                            title="🏷️ Tag Role Removed",
                            description=f"{main_member.mention} lost the main tag role because the configured source role was removed.",
                            color=discord.Color.orange(),
                            fields=[
                                ("User", f"{main_member} ({main_member.id})", True),
                                ("Role", f"{first_main_role.mention} ({first_main_role.id})", True),
                                ("Reason", "Configured source role was removed.", False),
                            ],
                        )
                    except (discord.Forbidden, discord.HTTPException):
                        pass

    second_source_role_id = tag_server_role_ids_2.get(source_member.guild.id)
    if not second_source_role_id or not TAG_ROLE_ID_2:
        return

    second_main_role = main_guild.get_role(TAG_ROLE_ID_2)
    if (
        second_main_role is None
        or second_main_role.managed
        or second_main_role.is_default()
        or bot_member.top_role <= second_main_role
    ):
        return

    has_second_source_role = second_source_role_id in source_role_ids
    is_blacklisted = main_member.id in second_role_blacklist

    try:
        if is_blacklisted:
            if second_main_role in main_member.roles:
                await main_member.remove_roles(
                    second_main_role,
                    reason="Member is on the second-role blacklist",
                )
                await send_role_webhook(
                    title="🚫 Second Tag Role Removed",
                    description=f"{main_member.mention} lost the second main tag role because they are blacklisted.",
                    color=discord.Color.red(),
                    fields=[
                        ("User", f"{main_member} ({main_member.id})", True),
                        ("Role", f"{second_main_role.mention} ({second_main_role.id})", True),
                        ("Reason", "Member is on the second-role blacklist.", False),
                    ],
                )
            return

        if has_second_source_role and second_main_role not in main_member.roles:
            await main_member.add_roles(
                second_main_role,
                reason="Configured second tag-server source role detected",
            )
            await send_role_webhook(
                title="🏷️ Second Tag Role Added",
                description=f"{main_member.mention} was given the second main tag role.",
                color=discord.Color.green(),
                fields=[
                    ("User", f"{main_member} ({main_member.id})", True),
                    ("Role", f"{second_main_role.mention} ({second_main_role.id})", True),
                    ("Reason", "Configured second tag-server source role detected.", False),
                ],
            )
        elif not has_second_source_role and second_main_role in main_member.roles:
            await main_member.remove_roles(
                second_main_role,
                reason="Configured second tag-server source role was removed",
            )
            await send_role_webhook(
                title="🏷️ Second Tag Role Removed",
                description=f"{main_member.mention} lost the second main tag role because the configured source role was removed.",
                color=discord.Color.orange(),
                fields=[
                    ("User", f"{main_member} ({main_member.id})", True),
                    ("Role", f"{second_main_role.mention} ({second_main_role.id})", True),
                    ("Reason", "Configured second tag-server source role was removed.", False),
                ],
            )
    except (discord.Forbidden, discord.HTTPException) as error:
        print(
            "Immediate second tag-role sync failed for "
            f"{main_member} ({main_member.id}) -> role {second_main_role.id}: "
            f"{type(error).__name__}: {error}"
        )

# ============================================================
# HELPERS
# ============================================================

# =========================
# REVIEW DATABASE SYNC LOOP + MANUAL SAVE
# =========================

review_db_auto_sync_task: asyncio.Task | None = None
staff_strike_expiry_task: asyncio.Task | None = None


# =========================

# =========================
# FEATURE MODULES
# =========================
import hoodify_feature
import uwuify_feature
import reviews as reviews_feature
import leaderboard as leaderboard_feature
import staff_strikes as staff_strikes_feature
from hoodify_feature import *
from uwuify_feature import *
from reviews import *
from reviews import (
    _ensure_review_db_schema,
    _ensure_shared_review_store,
    _review_db_has_reviews,
    _load_shared_review_store,
)
from leaderboard import *
from staff_strikes import *

# =========================
# HOODIFY ROOT COMMANDS
# =========================
# Define stable root commands in bot.py and delegate to the tested feature
# callbacks. This prevents import-time command registration from disappearing.
tree.remove_command("hoodify")
tree.remove_command("unhoodify")
tree.remove_command("hoodcount")


@tree.command(
    name="hoodify",
    description="Add a member to this channel's automatic HOODIFY mode.",
)
@app_commands.describe(
    member="The member whose messages should be automatically hoodified",
    message="Optional one-time message to send through the hoodify webhook",
)
async def hoodify_root_command(
    interaction: discord.Interaction,
    member: discord.Member,
    message: str | None = None,
):
    await hoodify_feature.hoodify_command.callback(interaction, member, message)


@tree.command(
    name="unhoodify",
    description="Disable HOODIFY for a selected member.",
)
@app_commands.describe(member="The member to stop HOODIFYING")
async def unhoodify_root_command(
    interaction: discord.Interaction,
    member: discord.Member,
):
    await hoodify_feature.unhoodify_command.callback(interaction, member)


@tree.command(
    name="hoodcount",
    description="Show how many people are currently being HOODIFIED.",
)
async def hoodcount_root_command(
    interaction: discord.Interaction,
):
    await hoodify_feature.hoodcount_root_command.callback(interaction)


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
        main_guild_object = discord.Object(id=MAIN_SERVER)

        try:
            # Feature modules use decorators during import. Re-register their
            # command objects explicitly as a safety net in case a module was
            # imported while bot.py was still initializing and its decorator
            # did not land on the live CommandTree.
            feature_commands = (
                globals().get("uwu_command"),
                globals().get("unuwuify_command"),
                globals().get("uwucount_root_command"),
                globals().get("hoodify_root_command"),
                globals().get("unhoodify_root_command"),
                globals().get("hoodcount_root_command"),
            )
            registered_names = {command.name for command in tree.get_commands()}
            for command in feature_commands:
                if isinstance(command, app_commands.Command) and command.name not in registered_names:
                    tree.add_command(command)
                    registered_names.add(command.name)

            # Register the complete current CommandTree directly into the
            # main guild. This avoids guild copy state getting out of sync with
            # the decorators defined in the feature modules.
            global_commands = list(tree.get_commands())

            tree.clear_commands(guild=main_guild_object)
            for command in global_commands:
                tree.add_command(command, guild=main_guild_object)

            synced_commands = await tree.sync(guild=main_guild_object)

            synced_names = {command.name for command in synced_commands}
            defined_names = {command.name for command in global_commands}
            print(
                "Guild command sync: "
                f"{len(synced_names)} synced / {len(defined_names)} defined"
            )
            required_commands = {
                "uwuify",
                "unuwuify",
                "uwucount",
                "hoodify",
                "unhoodify",
                "hoodcount",
            }

            if required_commands.issubset(synced_names):
                commands_synced = True
                print(
                    "Slash commands synced: "
                    + ", ".join(sorted(synced_names))
                )
            else:
                commands_synced = False
                print(
                    "Slash command sync incomplete. Missing: "
                    + ", ".join(sorted(required_commands - synced_names))
                )
        except Exception as error:
            commands_synced = False
            print(
                "Slash command sync failed: "
                f"{type(error).__name__}: {error}"
            )

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

    if GITHUB_TOKEN and staff_strikes_feature.staff_strike_github_sync_error is None:
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

    # Refresh the main-server member list so the bot does not rely on a stale
    # cache when deciding whether someone is still in the main server.
    main_source_members = await get_source_members_for_role_check(main_guild)
    if main_source_members is None:
        print("Kick check skipped: could not refresh main-server members.")
        return

    main_members = {
        member.id
        for member in main_source_members
        if not member.bot
    }

    for server_id in tag_servers:
        guild = bot.get_guild(server_id)

        if guild is None:
            print(f"Kick check skipped: source server {server_id} is not available.")
            continue

        bot_member = guild.me
        if bot_member is None or not bot_member.guild_permissions.kick_members:
            print(
                f"Kick check skipped for {guild.name}: "
                "bot does not have Kick Members permission."
            )
            continue

        # Use a fresh member list for the tag server as well.
        source_members = await get_source_members_for_role_check(guild)
        if source_members is None:
            print(
                f"Kick check skipped for {guild.name}: "
                "could not refresh source-server members."
            )
            continue

        for member in source_members:
            if member.bot:
                continue

            # Anyone with ANY protected role will never be kicked.
            if any(role.id in PROTECTED_ROLE_IDS for role in member.roles):
                continue

            if member.id in main_members:
                continue

            # Discord hierarchy applies to kicks. The bot must be above the
            # member it is trying to kick, and the owner can never be kicked.
            if guild.owner_id == member.id:
                print(
                    f"Kick skipped for {member} ({member.id}) in {guild.name}: "
                    "member is the server owner."
                )
                continue

            if member.top_role >= bot_member.top_role:
                print(
                    f"Kick skipped for {member} ({member.id}) in {guild.name}: "
                    f"member top role {member.top_role.id}/{member.top_role.position} "
                    f"is not below bot top role "
                    f"{bot_member.top_role.id}/{bot_member.top_role.position}."
                )
                continue

            try:
                try:
                    await member.send(
                        f"Hey {member.mention}, you were kicked from **{guild.name}** "
                        f"because you are not in the main server.\n\n"
                        f"Please join the main server here: {MAIN_SERVER_INVITE}"
                    )
                except Exception:
                    pass

                await guild.kick(
                    member,
                    reason="not in main server",
                )

                await send_kick_webhook(
                    title="🚫 Member Kicked",
                    description=(
                        f"{member.mention} was kicked from a tag server "
                        "because they are not in the main server."
                    ),
                    color=discord.Color.red(),
                    fields=[
                        ("User", f"{member} ({member.id})", True),
                        ("Tag Server", f"{guild.name}\n{guild.id}", True),
                        ("Reason", "Not a member of the main server", False),
                    ],
                )

            except (discord.Forbidden, discord.HTTPException) as error:
                print(
                    f"Kick failed for {member} ({member.id}) in {guild.name}: "
                    f"{type(error).__name__}: {error}"
                )
                await send_kick_webhook(
                    title="⚠️ Kick Failed",
                    description=(
                        f"Failed to kick {member.mention} "
                        f"from **{guild.name}**."
                    ),
                    color=discord.Color.orange(),
                    fields=[
                        ("User", f"{member} ({member.id})", True),
                        ("Server", f"{guild.name}\n{guild.id}", True),
                        ("Reason", "Discord rejected the kick request.", False),
                        ("Error", f"{error}", False),
                    ],
                )
            except Exception as error:
                print(
                    f"Unexpected kick error for {member} ({member.id}) in {guild.name}: "
                    f"{type(error).__name__}: {error}"
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

    # Keep enforcing the automatic role-removal rule for the explicit
    # trigger role and every configured staff hierarchy role.
    if AUTO_REMOVE_TRIGGER_ROLE_IDS and AUTO_REMOVE_ROLE_ID:
        for member in main_guild.members:
            if member.bot:
                continue
            await remove_auto_role_if_needed(
                member,
                reason="Periodic automatic-role-removal check",
            )

    # Refresh the bot member so role-position checks use Discord's current data.
    try:
        bot_member = await main_guild.fetch_member(bot.user.id)
    except (discord.NotFound, discord.HTTPException):
        bot_member = main_guild.me

    if bot_member is None or not bot_member.guild_permissions.manage_roles:
        print("Role sync skipped: bot does not currently have Manage Roles.")
        return

    # First main-server tag role.
    tag_role = main_guild.get_role(TAG_ROLE_ID)
    first_role_ready = (
        tag_role is not None
        and not tag_role.is_default()
        and not tag_role.managed
        and bot_member.top_role > tag_role
    )

    # Second main-server tag role is completely independent from the first.
    tag_role_2 = main_guild.get_role(TAG_ROLE_ID_2) if TAG_ROLE_ID_2 else None
    second_role_ready = (
        tag_role_2 is not None
        and not tag_role_2.is_default()
        and not tag_role_2.managed
        and bot_member.top_role > tag_role_2
    )

    tagged_users: set[int] = set()
    tagged_users_2: set[int] = set()

    # -------------------------
    # CHECK ALL FIRST-ROLE TAG SERVERS
    # -------------------------
    if first_role_ready:
        first_role_check_failed = False
        tagged_users: set[int] = set()

        if len(tag_servers) != len(tag_server_role_ids):
            first_role_check_failed = True

        for server_id, tag_server_role_id in zip(tag_servers, tag_server_role_ids):
            guild = bot.get_guild(server_id)
            if guild is None:
                first_role_check_failed = True
                continue

            tag_server_role = await get_source_role_for_check(guild, tag_server_role_id)
            if tag_server_role is None:
                first_role_check_failed = True
                continue

            source_members = await get_source_members_for_role_check(guild)
            if source_members is None:
                first_role_check_failed = True
                continue

            for source_member in source_members:
                if tag_server_role.id in {role.id for role in source_member.roles}:
                    tagged_users.add(source_member.id)

        # Add immediately when at least one source server confirms the role.
        for member in main_guild.members:
            if member.bot or member.id not in tagged_users:
                continue
            if member.id == main_guild.owner_id:
                continue
            if tag_role not in member.roles:
                try:
                    await member.add_roles(
                        tag_role,
                        reason="User has a configured first tag role in a tag server",
                    )
                    await send_role_webhook(
                        title="🏷️ Tag Role Added",
                        description=f"{member.mention} was given the main tag role.",
                        color=discord.Color.green(),
                        fields=[
                            ("User", f"{member} (`{member.id}`)", True),
                            ("Role", f"{tag_role.mention}\n`{tag_role.id}`", True),
                            ("Reason", "User has a configured tag role in a tag server.", False),
                        ],
                    )
                except (discord.Forbidden, discord.HTTPException) as error:
                    print(
                        "First tag-role assignment failed for "
                        f"{member} ({member.id}) -> role {tag_role.id} "
                        f"(bot top role: {bot_member.top_role.id}/{bot_member.top_role.position}, "
                        f"member top role: {member.top_role.id}/{member.top_role.position}, "
                        f"target role: {tag_role.id}/{tag_role.position}): "
                        f"{type(error).__name__}: {error}"
                    )

        # Only remove the main role when every configured source was checked.
        if not first_role_check_failed:
            for member in main_guild.members:
                if member.bot or member.id in tagged_users:
                    continue
                if member.id == main_guild.owner_id:
                    continue
                if member.top_role >= bot_member.top_role:
                    continue

                if tag_role in member.roles:
                    try:
                        await member.remove_roles(
                            tag_role,
                            reason="User no longer has the configured first tag role",
                        )
                        await send_role_webhook(
                            title="🏷️ Tag Role Removed",
                            description=f"{member.mention} no longer has the configured first tag role in any tag server.",
                            color=discord.Color.orange(),
                            fields=[
                                ("User", f"{member} (`{member.id}`)", True),
                                ("Role", f"{tag_role.mention}\n`{tag_role.id}`", True),
                                ("Reason", "User no longer has a configured tag role.", False),
                            ],
                        )
                    except (discord.Forbidden, discord.HTTPException):
                        pass

    # -------------------------
    # CHECK SECOND-ROLE SOURCE SERVERS
    # -------------------------
    if second_role_ready:
        second_role_check_failed = False
        tagged_users_2: set[int] = set()

        for source_server_id, source_role_id in tag_server_role_ids_2.items():
            if not source_role_id:
                continue

            source_guild = bot.get_guild(source_server_id)
            if source_guild is None:
                second_role_check_failed = True
                continue

            source_role = await get_source_role_for_check(source_guild, source_role_id)
            if source_role is None:
                second_role_check_failed = True
                continue

            source_members = await get_source_members_for_role_check(source_guild)
            if source_members is None:
                second_role_check_failed = True
                continue

            for source_member in source_members:
                if source_role.id in {role.id for role in source_member.roles}:
                    tagged_users_2.add(source_member.id)

        # Add whenever the configured second source confirms the member.
        for member in main_guild.members:
            if member.bot:
                continue

            has_tag_2 = member.id in tagged_users_2
            has_role_2 = tag_role_2 in member.roles
            is_blacklisted = member.id in second_role_blacklist

            if is_blacklisted:
                if has_role_2 and member.id != main_guild.owner_id:
                    try:
                        await member.remove_roles(
                            tag_role_2,
                            reason="Member is on the second-role blacklist",
                        )
                    except (discord.Forbidden, discord.HTTPException):
                        pass
                continue

            if has_tag_2 and not has_role_2:
                if member.id == main_guild.owner_id:
                    continue
                try:
                    await member.add_roles(
                        tag_role_2,
                        reason="User has the configured second tag role in a tag server",
                    )
                    await send_role_webhook(
                        title="🏷️ Second Tag Role Added",
                        description=f"{member.mention} was given the second main tag role.",
                        color=discord.Color.green(),
                        fields=[
                            ("User", f"{member} (`{member.id}`)", True),
                            ("Role", f"{tag_role_2.mention}\n`{tag_role_2.id}`", True),
                            ("Reason", "User has the configured second tag role in a tag server.", False),
                        ],
                    )
                except (discord.Forbidden, discord.HTTPException) as error:
                    print(
                        "Second tag-role assignment failed for "
                        f"{member} ({member.id}) -> role {tag_role_2.id} "
                        f"(bot top role: {bot_member.top_role.id}/{bot_member.top_role.position}, "
                        f"member top role: {member.top_role.id}/{member.top_role.position}, "
                        f"target role: {tag_role_2.id}/{tag_role_2.position}): "
                        f"{type(error).__name__}: {error}"
                    )

        # Only remove when all configured second-role sources were checked.
        if not second_role_check_failed:
            for member in main_guild.members:
                if member.bot or member.id in tagged_users_2:
                    continue
                if member.id == main_guild.owner_id:
                    continue

                if tag_role_2 in member.roles and member.id not in second_role_blacklist:
                    try:
                        await member.remove_roles(
                            tag_role_2,
                            reason="User no longer has the configured second tag role",
                        )
                        await send_role_webhook(
                            title="🏷️ Second Tag Role Removed",
                            description=f"{member.mention} no longer has the configured second tag role in any tag server.",
                            color=discord.Color.orange(),
                            fields=[
                                ("User", f"{member} (`{member.id}`)", True),
                                ("Role", f"{tag_role_2.mention}\n`{tag_role_2.id}`", True),
                                ("Reason", "User no longer has a configured second tag role.", False),
                            ],
                        )
                    except (discord.Forbidden, discord.HTTPException):
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
# RAILWAY HEALTH SERVER
# =========================

class RailwayHealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in {"/", "/health"}:
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        # Keep Railway terminal output quiet.
        return


def start_railway_health_server() -> None:
    port_value = os.getenv("PORT")
    if not port_value:
        return

    try:
        port = int(port_value)
        server = ThreadingHTTPServer(("0.0.0.0", port), RailwayHealthHandler)
        thread = threading.Thread(
            target=server.serve_forever,
            name="railway-health-server",
            daemon=True,
        )
        thread.start()
    except Exception as error:
        print(
            "Railway health server failed to start: "
            f"{type(error).__name__}: {error}"
        )


# =========================
# START BOT
# =========================

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN is not configured. "
        "Set the BOT_TOKEN environment variable before starting the bot."
    )

start_railway_health_server()

# Keep the Discord worker alive if bot.run() ever returns cleanly.
# Railway only restarts crashed processes, so a clean return would otherwise
# leave the service stopped.
while True:
    try:
        bot.run(BOT_TOKEN, reconnect=True)
        print("Bot run returned; restarting Discord connection in 5 seconds.")
    except Exception as error:
        print(
            "Discord connection stopped: "
            f"{type(error).__name__}: {error}"
        )

    time.sleep(5)