import os
import asyncio
import json
import re
import random
import logging
from pathlib import Path

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
            pass
            return set()

        return {int(user_id) for user_id in data}

    except (json.JSONDecodeError, ValueError, TypeError, OSError) as e:
        pass
        return set()


def save_blacklist(blacklist: set[int]) -> None:
    """Save the second-role blacklist to disk."""
    try:
        with BLACKLIST_FILE.open("w", encoding="utf-8") as file:
            json.dump(sorted(blacklist), file, indent=2)
    except OSError as e:
        pass


second_role_blacklist = load_blacklist()


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
}

# External proxy bots whose output should be checked for active UWU/HOODIFY targets.
# Bleed's current application ID is included; the name match also covers older
# Discord username/discriminator formats.
PROXY_BOT_IDS = {
    1006548568234008627,
}
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
        return f"__HOOD_PROTECTED_{len(protected) - 1}__"

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
        result = result.replace(f"__HOOD_PROTECTED_{index}__", original)

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
    target: discord.Member,
    content: str,
) -> list[discord.WebhookMessage]:
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
        return f"__UWU_PROTECTED_{len(protected_mentions) - 1}__"

    uwu_input = re.sub(
        r"<@!?>?\\d+>|<@&\\d+>|<#\\d+>",
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
            f"__UWU_PROTECTED_{index}__",
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
            pass
            continue
        except Exception as e:
            pass
            continue

        for webhook in webhooks:
            if webhook.id in tracked_ids:
                continue

            # Only delete webhooks with our exact name and created by this bot.
            if webhook.name != UWU_WEBHOOK_NAME:
                continue
            if webhook.user is None or webhook.user.id != bot_id:
                continue

            pass

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


# =========================
# MESSAGE DELETION HELPER
# =========================

async def delete_original_message(message: discord.Message) -> bool:
    """Delete a user's original message with detailed terminal debugging."""
    guild = message.guild
    bot_member = guild.me if guild is not None else None
    channel_permissions = (
        message.channel.permissions_for(bot_member)
        if bot_member is not None
        else None
    )

    pass
    pass
    pass
    pass
    pass
    pass
    if bot_member is not None:
        pass

    try:
        pass
        await message.delete()
        pass
        pass
        return True
    except discord.NotFound as e:
        pass
        pass
        return True
    except discord.Forbidden as e:
        pass
    except discord.HTTPException as e:
        pass
    except Exception as e:
        pass

    pass

    try:
        fresh_message = await message.channel.fetch_message(message.id)
        pass
    except discord.NotFound as e:
        pass
        pass
        return True
    except discord.Forbidden as e:
        pass
        return False
    except discord.HTTPException as e:
        pass
        return False
    except Exception as e:
        pass
        return False

    try:
        pass
        await fresh_message.delete()
        pass
        pass
        return True
    except discord.NotFound as e:
        pass
        pass
        return True
    except discord.Forbidden as e:
        pass
        pass
        return False
    except discord.HTTPException as e:
        pass
        pass
        return False
    except Exception as e:
        pass
        pass
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
        r"^\\s*[,!](uwu(?:ify)?|hood(?:ify)?)\\b",
        content,
        flags=re.IGNORECASE,
    )
    if mode_match is None:
        return

    mode = mode_match.group(1).lower()
    if mode.startswith("uwu"):
        if message.author.id not in uwu_targets.get(message.channel.id, set()):
            return
        mode = "uwu"
    else:
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
            if target.id not in uwu_targets.get(message.channel.id, set()):
                proxy_requests.pop(message.channel.id, None)
                return False

            await send_uwu_message(
                message.channel,
                target,
                message.content,
            )

        elif mode == "hood":
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

    if proxy_message:
        handled = await handle_proxy_message(message)
        if handled:
            return
        return

    # Remember commands such as ,uwu / ,uwuify / ,hood / ,hoodify so the
    # subsequent configured proxy-bot response can be transformed for that user.
    remember_proxy_request(message, content)

    # Prefix ping.
    if content.lower() == ",ping":
        latency_ms = round(bot.latency * 1000)
        await message.reply(f"🏓 Pong! `{latency_ms}ms`", mention_author=False)
        return

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

    # Prefix command to disable UWU mode everywhere.
    if content.lower() == ",unuwuify":
        if not uwu_user_is_whitelisted(message.author):
            pass
            try:
                await message.reply(
                    "❌ You need one of the allowed UWU roles to use this command.",
                    mention_author=False,
                )
            except (discord.Forbidden, discord.HTTPException):
                pass
            return

        disabled_count = await disable_all_uwu_targets()

        await message.reply(
            f"✅ UWU mode disabled everywhere. "
            f"Removed **{disabled_count}** active channel(s).",
            mention_author=False,
        )
        return

    # Prefix UWU setup/disable command.
    if content.lower().startswith(",uwuify"):
        if not uwu_user_is_whitelisted(message.author):
            pass
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
            pass

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
        except UwuTargetLimitReached:
            await message.reply(
                f"❌ The global limit of {MAX_ACTIVE_UWU_TARGETS} UWUified people has been reached. "+
                "Use `,unuwuify` or `/unuwuify` to disable all UWU modes, or wait for a slot to expire.",
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

    if content.lower() == ",unhoodify":
        if not hood_user_is_whitelisted(message.author):
            try:
                await message.reply(
                    "❌ You need one of the allowed HOODIFY roles to use this command.",
                    mention_author=False,
                )
            except (discord.Forbidden, discord.HTTPException):
                pass
            return

        disabled_count = await disable_all_hood_targets()
        await message.reply(
            f"✅ HOODIFY disabled everywhere. "
            f"Removed **{disabled_count}** active channel(s).",
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

    hood_target_ids = hood_targets.get(message.channel.id, set())
    if message.author.id in hood_target_ids and content:
        bot_member = message.guild.me if message.guild is not None else None
        channel_permissions = (
            message.channel.permissions_for(bot_member)
            if bot_member is not None
            else None
        )

        if bot_member is None or not getattr(channel_permissions, "manage_messages", False):
            return

        try:
            hood_text = hoodify_text(content)
            if not hood_text:
                return

            # Send the transformed message first. Only remove the original if
            # the webhook relay succeeded.
            await send_hood_message(
                message.channel,
                message.author,
                content,
            )
            await delete_original_message(message)
        except HoodMessageBlocked:
            # Leave the original message untouched when it is blocked.
            pass
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            try:
                await message.channel.send(
                    "❌ HOODIFY could not relay that message. Check that the bot "
                    "has **Manage Messages** and **Manage Webhooks** permissions.",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception:
                pass
        except Exception:
            try:
                await message.channel.send(
                    "❌ HOODIFY hit an error while relaying that message. "
                    "The original message was left in place.",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception:
                pass
        return

    # Automatic mode: replace messages from any selected target in this channel.
    target_ids = uwu_targets.get(message.channel.id, set())
    if message.author.id in target_ids and content:
        pass
        bot_member = message.guild.me if message.guild is not None else None
        channel_permissions = (
            message.channel.permissions_for(bot_member)
            if bot_member is not None
            else None
        )
        pass
        if bot_member is None or not getattr(channel_permissions, 'manage_messages', False):
            pass
            return

        try:
            pass
            await send_uwu_message(
                message.channel,
                message.author,
                content,
            )
            pass

            deleted = await delete_original_message(message)
            pass
        except UwuMessageBlocked as blocked_error:
            # Leave the original message untouched. The blocked content must never
            # be relayed through the UWU webhook.
            pass
        except (discord.Forbidden, discord.NotFound, discord.HTTPException) as e:
            pass
        except Exception as e:
            pass
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
        pass
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


# =========================
# SLASH COMMANDS
# =========================

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
    description="Disable UWU mode for everyone and delete active UWU webhooks.",
)
async def unuwuify_command(interaction: discord.Interaction):
    """Disable all active UWU modes across the bot."""
    if not isinstance(interaction.user, discord.Member) or not uwu_user_is_whitelisted(interaction.user):
        await interaction.response.send_message(
            "❌ You need one of the allowed UWU roles to use this command.",
            ephemeral=True,
        )
        return

    disabled_count = await disable_all_uwu_targets()

    await interaction.response.send_message(
        f"✅ UWU mode disabled everywhere. "
        f"Removed **{disabled_count}** active channel(s).",
        ephemeral=True,
    )


@tree.command(
    name="uwucount",
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
        ephemeral=True,
    )


@tree.command(
    name="unhoodify",
    description="Disable HOODIFY mode for everyone and delete active HOODIFY webhooks.",
)
async def unhoodify_command(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member) or not hood_user_is_whitelisted(interaction.user):
        await interaction.response.send_message(
            "❌ You need one of the allowed HOODIFY roles to use this command.",
            ephemeral=True,
        )
        return

    disabled_count = await disable_all_hood_targets()
    await interaction.response.send_message(
        f"✅ HOODIFY disabled everywhere. "
        f"Removed **{disabled_count}** active channel(s).",
        ephemeral=True,
    )


@tree.command(
    name="hoodcount",
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
        ephemeral=True,
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
    if not isinstance(interaction.user, discord.Member) or not hood_user_is_whitelisted(interaction.user):
        await interaction.response.send_message(
            "❌ You need one of the allowed HOODIFY roles to use this command.",
            ephemeral=True,
        )
        return

    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message(
            "❌ This command can only be used in a normal text channel.",
            ephemeral=True,
        )
        return

    bot_member = interaction.guild.me if interaction.guild is not None else None
    if bot_member is None or not interaction.channel.permissions_for(bot_member).manage_messages:
        await interaction.response.send_message(
            "❌ I need **Manage Messages** permission in this channel to replace messages.",
            ephemeral=True,
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
                    ephemeral=True,
                )
                return

        active_count = get_active_hood_target_count()
        await interaction.response.send_message(
            f"✅ HOODIFY is active for {member.mention} in this channel. "
            f"Active people: **{active_count}/{MAX_ACTIVE_HOOD_TARGETS}**.\n"
            "You can add more people with another `/hoodify` command. "
            "The temporary webhook will be deleted after 5 minutes without use.",
            ephemeral=True,
        )
    except HoodTargetLimitReached:
        await interaction.response.send_message(
            f"❌ The global limit of {MAX_ACTIVE_HOOD_TARGETS} HOODIFIED people has been reached. "
            "Use `,unhoodify` or `/unhoodify` to disable all HOODIFY modes.",
            ephemeral=True,
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ I need **Manage Messages** and **Manage Webhooks** permission in this channel/server.",
            ephemeral=True,
        )
    except discord.HTTPException as e:
        await interaction.response.send_message(
            f"❌ Discord rejected the HOODIFY webhook request: `{e}`",
            ephemeral=True,
        )
    except Exception:
        await interaction.response.send_message(
            "❌ The HOODIFY mode could not be enabled.",
            ephemeral=True,
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
    if not isinstance(interaction.user, discord.Member) or not uwu_user_is_whitelisted(interaction.user):
        await interaction.response.send_message(
            "❌ You need one of the allowed UWU roles to use this command.",
            ephemeral=True,
        )
        return

    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message(
            "❌ This command can only be used in a normal text channel.",
            ephemeral=True,
        )
        return

    bot_member = interaction.guild.me if interaction.guild is not None else None
    if bot_member is None or not interaction.channel.permissions_for(bot_member).manage_messages:
        await interaction.response.send_message(
            "❌ I need **Manage Messages** permission in this channel to replace messages.",
            ephemeral=True,
        )
        return

    try:
        await set_uwu_target(interaction.channel, member)
        pass

        if message:
            try:
                await send_uwu_message(interaction.channel, member, message)
            except UwuMessageBlocked as blocked_error:
                await interaction.response.send_message(
                    f"❌ The UWU mode was enabled, but the one-time message was not sent because it contains a blacklisted word/phrase: `{blocked_error}`",
                    ephemeral=True,
                )
                return

        active_count = get_active_uwu_target_count()
        await interaction.response.send_message(
            f"✅ Uwu mode is active for {member.mention} in this channel. "
            f"Active people: **{active_count}/{MAX_ACTIVE_UWU_TARGETS}**.\n"
            "You can add more people with another `/uwuify` command. "
            "The temporary webhook will be deleted after 5 minutes without use.",
            ephemeral=True,
        )
    except UwuTargetLimitReached:
        await interaction.response.send_message(
            f"❌ The global limit of {MAX_ACTIVE_UWU_TARGETS} UWUified people has been reached. "
            "Use `,unuwuify` or `/unuwuify` to disable all UWU modes, or wait for a slot to expire.",
            ephemeral=True,
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ I need **Manage Messages** and **Manage Webhooks** permission in this channel/server.",
            ephemeral=True,
        )
    except discord.HTTPException as e:
        await interaction.response.send_message(
            f"❌ Discord rejected the uwu webhook request: `{e}`",
            ephemeral=True,
        )
    except Exception:
        await interaction.response.send_message(
            "❌ The uwu mode could not be enabled.",
            ephemeral=True,
        )


@tree.command(
    name="blacklist",
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
        ephemeral=True,
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


@tree.command(
    name="unblacklist",
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
            ephemeral=True,
        )
        return

    second_role_blacklist.remove(member.id)
    save_blacklist(second_role_blacklist)

    await interaction.response.send_message(
        f"✅ {member.mention} has been removed from the second-role blacklist.",
        ephemeral=True,
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


@tree.command(
    name="blacklist_status",
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
        ephemeral=True,
    )


@tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
):
    if isinstance(error, app_commands.CheckFailure):
        message = str(error) or "You are not allowed to use this command."

        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
        return

    pass

    if interaction.response.is_done():
        await interaction.followup.send(
            "An unexpected error occurred while running the command.",
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            "An unexpected error occurred while running the command.",
            ephemeral=True,
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
            pass
            await guild.chunk(cache=True)
        return True
    except discord.HTTPException as e:
        pass
        return False
    except Exception as e:
        pass
        return False


# =========================
# BOT READY
# =========================

# Tracks whether the one allowed terminal status line has been shown.
terminal_status_printed = False


@bot.event
async def on_ready():
    global commands_synced, terminal_status_printed

    if not terminal_status_printed:
        print("Bot is alive")
        terminal_status_printed = True

    if not commands_synced:
        try:
            main_guild_object = discord.Object(id=MAIN_SERVER)
            tree.copy_global_to(guild=main_guild_object)
            await tree.sync(guild=main_guild_object)
            commands_synced = True
            pass
        except Exception as e:
            pass

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
    pass

    main_guild = bot.get_guild(MAIN_SERVER)

    if main_guild is None:
        pass
        return

    main_members = {
        member.id
        for member in main_guild.members
    }

    for server_id in tag_servers:
        guild = bot.get_guild(server_id)

        if guild is None:
            pass
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

                pass
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

                    pass

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
                    pass

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
    pass

    main_guild = bot.get_guild(MAIN_SERVER)

    if main_guild is None:
        pass
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
        pass
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
        pass
        return

    if not await ensure_guild_members_loaded(main_guild):
        return

    bot_member = main_guild.me

    if bot_member is None:
        pass
        return

    if not bot_member.guild_permissions.manage_roles:
        pass
        return

    if tag_role.is_default():
        pass
        return

    if tag_role.managed:
        pass
        return

    if bot_member.top_role <= tag_role:
        pass
        return

    if second_role_ready and tag_role_2:
        if tag_role_2.is_default():
            pass
            second_role_ready = False

        elif tag_role_2.managed:
            pass
            second_role_ready = False

        elif bot_member.top_role <= tag_role_2:
            pass
            second_role_ready = False

    tagged_users = set()
    tagged_users_2 = set()
    second_role_check_failed = False
    second_role_servers_configured = 0

    pass

    # -------------------------
    # CHECK ALL FIRST-ROLE TAG SERVERS
    # -------------------------

    for server_id, tag_server_role_id in zip(
        tag_servers,
        tag_server_role_ids,
    ):
        guild = bot.get_guild(server_id)

        if guild is None:
            pass
            continue

        if not await ensure_guild_members_loaded(guild):
            continue

        tag_server_role = guild.get_role(tag_server_role_id)

        if tag_server_role is None:
            pass
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
        pass

        for source_server_id, source_role_id in tag_server_role_ids_2.items():
            if not source_role_id:
                pass
                continue

            second_role_servers_configured += 1

            source_guild = bot.get_guild(source_server_id)

            if source_guild is None:
                pass
                second_role_check_failed = True
                continue

            pass

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
                    pass
                    second_role_check_failed = True
                    continue

            if source_role is None:
                pass
                second_role_check_failed = True
                continue

            pass

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
                pass
                continue

            if member.top_role >= bot_member.top_role:
                pass
                continue

            try:
                await member.add_roles(
                    tag_role,
                    reason="User has a configured first tag role in a tag server",
                )
                pass

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
                pass

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
        pass
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
                        pass
                        continue

                    try:
                        await member.remove_roles(
                            tag_role_2,
                            reason="User is on the second-role blacklist",
                        )
                        pass

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
                    pass
                    continue

                if member.top_role >= bot_member.top_role:
                    pass
                    continue

                try:
                    await member.add_roles(
                        tag_role_2,
                        reason="User has the configured second tag role in a tag server",
                    )
                    pass

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
                    pass

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
    pass
    await asyncio.sleep(5)

    if not bot.is_closed() and not kick_loop.is_running():
        kick_loop.restart()


@tag_role_loop.error
async def tag_role_loop_error(error):
    # A single unexpected exception should not permanently stop the role loop.
    pass
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