"""Chat activity rewards and the Kevin Bucks shop.

Activity rewards are limited to normal chat in the main server. Reward state
shares the same kevin_bucks.json data store as blackjack, mines, and slots.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands

import bot as bot_module
import blackjack as blackjack_feature


CHAT_REWARD = 10
CHAT_BOOST_REWARD = 20
CHAT_COOLDOWN_SECONDS = 60
CHAT_DAILY_CAP = 500
CHAT_BOOSTED_DAILY_CAP = 1_000
CHAT_BOOST_DURATION = timedelta(hours=1)
DATA_SYNC_DELAY_SECONDS = 120

SHOP_ITEMS = {
    "chatboost": {
        "name": "Chat Boost",
        "cost": 750,
        "description": "Double eligible chat rewards for 1 hour. The daily chat cap increases to 1,000 while active.",
    },
    "dailyboost": {
        "name": "Daily Boost",
        "cost": 400,
        "description": "Double your next daily claim from 500 to 1,000 Kevin Bucks.",
    },
}

_sync_dirty = False
_sync_task: Optional[asyncio.Task] = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_timestamp(raw) -> Optional[datetime]:
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(str(raw))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def get_activity_state(user_id: int) -> dict:
    """Return the user's activity/shop state, creating safe defaults."""
    all_states = blackjack_feature.data.setdefault("chat_activity", {})
    key = str(user_id)
    state = all_states.get(key)
    if not isinstance(state, dict):
        state = {}
        all_states[key] = state

    state.setdefault("last_reward_at", "")
    state.setdefault("day", "")
    try:
        state["earned_today"] = max(0, int(state.get("earned_today", 0)))
    except (TypeError, ValueError):
        state["earned_today"] = 0
    state.setdefault("boost_until", "")
    try:
        state["daily_multiplier"] = 2 if int(state.get("daily_multiplier", 1)) >= 2 else 1
    except (TypeError, ValueError):
        state["daily_multiplier"] = 1
    return state


def _mark_data_changed() -> None:
    """Persist activity locally now and sync the shared currency file in batches."""
    global _sync_dirty, _sync_task
    _sync_dirty = True

    try:
        blackjack_feature._save_local()
    except Exception:
        pass

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    if _sync_task is None or _sync_task.done():
        _sync_task = loop.create_task(_delayed_data_sync())


async def _delayed_data_sync() -> None:
    global _sync_dirty, _sync_task
    try:
        while _sync_dirty:
            await asyncio.sleep(DATA_SYNC_DELAY_SECONDS)
            if not _sync_dirty:
                continue
            _sync_dirty = False
            try:
                await blackjack_feature.save_data()
            except Exception:
                _sync_dirty = True
    finally:
        _sync_task = None
        if _sync_dirty:
            _mark_data_changed()


def _is_main_server(guild) -> bool:
    return (
        guild is not None
        and getattr(guild, "id", None) == getattr(bot_module, "MAIN_SERVER", None)
    )


def _is_transform_target(message: discord.Message) -> bool:
    """Do not reward messages that are consumed by an automatic text relay."""
    channel_id = getattr(message.channel, "id", None)
    author_id = getattr(message.author, "id", None)

    for module_name, mapping_name in (
        ("uwuify_feature", "uwu_targets"),
        ("hoodify_feature", "hood_targets"),
        ("safechat_feature", "safechat_targets"),
    ):
        feature = getattr(bot_module, module_name, None)
        mapping = getattr(feature, mapping_name, {})
        if author_id in mapping.get(channel_id, set()):
            return True
    return False


async def award_chat_activity(message: discord.Message) -> None:
    """Award a small amount for meaningful, non-command chat with anti-spam limits."""
    if not _is_main_server(getattr(message, "guild", None)):
        return
    if getattr(message.author, "bot", False) or getattr(message, "webhook_id", None):
        return
    if not isinstance(getattr(message, "channel", None), discord.TextChannel):
        return

    content = str(getattr(message, "content", "") or "").strip()
    if not content or content.startswith(","):
        return

    # Links and raw mentions alone should not qualify as meaningful chat.
    meaningful = re.sub(r"https?://\S+", " ", content, flags=re.IGNORECASE)
    meaningful = re.sub(r"<@!?[0-9]+>|<@&[0-9]+>|<#[0-9]+>", " ", meaningful)
    meaningful = re.sub(r"\s+", " ", meaningful).strip()
    if len(meaningful) < 12 or len(meaningful.split()) < 3:
        return

    if _is_transform_target(message):
        return

    now = _now()
    today = now.date().isoformat()
    state = get_activity_state(message.author.id)

    if state.get("day") != today:
        state["day"] = today
        state["earned_today"] = 0

    last_reward = _parse_timestamp(state.get("last_reward_at"))
    if last_reward is not None:
        if (now - last_reward).total_seconds() < CHAT_COOLDOWN_SECONDS:
            return

    boost_until = _parse_timestamp(state.get("boost_until"))
    boosted = boost_until is not None and boost_until > now
    daily_cap = CHAT_BOOSTED_DAILY_CAP if boosted else CHAT_DAILY_CAP
    remaining = daily_cap - int(state.get("earned_today", 0))
    reward = min(CHAT_BOOST_REWARD if boosted else CHAT_REWARD, remaining)
    if reward <= 0:
        return

    state["last_reward_at"] = now.isoformat()
    state["earned_today"] = int(state.get("earned_today", 0)) + reward
    blackjack_feature.add_balance(message.author.id, reward)
    _mark_data_changed()


async def chat_activity_listener(message: discord.Message) -> None:
    """Call from the bot's existing on_message event; never register a second listener."""
    try:
        await award_chat_activity(message)
    except Exception:
        # Economy rewards must never interfere with normal bot message handling.
        return


def _shop_embed() -> discord.Embed:
    embed = discord.Embed(
        title="Kevin Bucks Shop",
        description="Spend your Kevin Bucks on boosts for the shared server economy.",
        color=discord.Color.blurple(),
    )
    for key in ("chatboost", "dailyboost"):
        item = SHOP_ITEMS[key]
        embed.add_field(
            name=f"{item['name']} — {item['cost']:,} KB",
            value=item["description"],
            inline=False,
        )
    embed.set_footer(text="Use ,buy chatboost or ,buy dailyboost to purchase an item.")
    return embed


def _normalize_item(raw: str) -> Optional[str]:
    normalized = re.sub(r"[^a-z]", "", str(raw).casefold())
    aliases = {
        "chat": "chatboost",
        "activityboost": "chatboost",
        "chatboost": "chatboost",
        "daily": "dailyboost",
        "dailyboost": "dailyboost",
        "doubledaily": "dailyboost",
    }
    return aliases.get(normalized)


async def _purchase(user_id: int, raw_item: str) -> tuple[bool, str]:
    item_key = _normalize_item(raw_item)
    if item_key is None:
        return False, "That item does not exist. Use ,shop to see available items."

    item = SHOP_ITEMS[item_key]
    state = get_activity_state(user_id)
    now = _now()

    if item_key == "dailyboost" and int(state.get("daily_multiplier", 1)) >= 2:
        return False, "You already have a Daily Boost waiting for your next daily claim."

    if item_key == "chatboost":
        current_until = _parse_timestamp(state.get("boost_until"))
        if current_until is not None and current_until > now:
            base_time = current_until
        else:
            base_time = now
        new_until = base_time + CHAT_BOOST_DURATION

    balance = blackjack_feature.get_balance(user_id)
    if balance < item["cost"]:
        return False, (
            f"You need {item['cost']:,} Kevin Bucks, but you only have {balance:,}."
        )

    blackjack_feature.set_balance(user_id, balance - item["cost"])
    if item_key == "chatboost":
        state["boost_until"] = new_until.isoformat()
        until_text = new_until.strftime("%Y-%m-%d %H:%M UTC")
        response = (
            f"Purchased **Chat Boost** for {item['cost']:,} Kevin Bucks. "
            f"Your boost is active until **{until_text}**."
        )
    else:
        state["daily_multiplier"] = 2
        response = (
            f"Purchased **Daily Boost** for {item['cost']:,} Kevin Bucks. "
            "Your next daily claim will pay 1,000 Kevin Bucks."
        )

    await blackjack_feature.save_data()
    return True, f"{response}\nRemaining balance: **{blackjack_feature.get_balance(user_id):,} Kevin Bucks**."


def _inventory_text(user_id: int) -> str:
    state = get_activity_state(user_id)
    now = _now()
    boost_until = _parse_timestamp(state.get("boost_until"))

    if boost_until is not None and boost_until > now:
        remaining_seconds = int((boost_until - now).total_seconds())
        hours, remainder = divmod(remaining_seconds, 3600)
        minutes = remainder // 60
        chat_status = f"Active — **{hours}h {minutes}m** remaining"
    else:
        chat_status = "Not active"

    daily_status = (
        "Ready — your next daily claim pays **1,000 Kevin Bucks**"
        if int(state.get("daily_multiplier", 1)) >= 2
        else "None"
    )
    return (
        f"**Chat Boost:** {chat_status}\n"
        f"**Daily Boost:** {daily_status}"
    )


@bot_module.tree.command(name="shop", description="Browse the Kevin Bucks shop.")
async def shop_command(interaction: discord.Interaction):
    if not _is_main_server(interaction.guild):
        await interaction.response.send_message(
            "The Kevin Bucks shop is only available in the main server.",
            ephemeral=True,
        )
        return
    await interaction.response.send_message(embed=_shop_embed())


@bot_module.tree.command(name="buy", description="Buy a Kevin Bucks shop item.")
@app_commands.describe(item="The item you want to purchase.")
@app_commands.choices(
    item=[
        app_commands.Choice(name="Chat Boost — 750 KB", value="chatboost"),
        app_commands.Choice(name="Daily Boost — 400 KB", value="dailyboost"),
    ]
)
async def buy_command(
    interaction: discord.Interaction,
    item: app_commands.Choice[str],
):
    if not _is_main_server(interaction.guild):
        await interaction.response.send_message(
            "The Kevin Bucks shop is only available in the main server.",
            ephemeral=True,
        )
        return
    _, response = await _purchase(interaction.user.id, item.value)
    await interaction.response.send_message(response, ephemeral=True)


@bot_module.tree.command(name="inventory", description="Check your active Kevin Bucks shop items.")
async def inventory_command(interaction: discord.Interaction):
    if not _is_main_server(interaction.guild):
        await interaction.response.send_message(
            "Kevin Bucks inventory is only available in the main server.",
            ephemeral=True,
        )
        return
    await interaction.response.send_message(_inventory_text(interaction.user.id), ephemeral=True)


async def handle_prefix(message: discord.Message) -> bool:
    if not _is_main_server(message.guild):
        return False

    parts = message.content.strip().split()
    if not parts:
        return False

    command = parts[0].casefold()
    if command == ",shop":
        await message.reply(embed=_shop_embed(), mention_author=False)
        return True

    if command in {",inventory", ",inv"}:
        await message.reply(_inventory_text(message.author.id), mention_author=False)
        return True

    if command == ",buy":
        if len(parts) != 2:
            await message.reply(
                "Usage: ,buy chatboost or ,buy dailyboost",
                mention_author=False,
            )
            return True
        _, response = await _purchase(message.author.id, parts[1])
        await message.reply(response, mention_author=False)
        return True

    return False
