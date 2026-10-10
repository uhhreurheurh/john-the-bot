"""
Kevin Bucks + Blackjack feature for John the Bot.

This module owns:
- Kevin Bucks balances
- /blackjack and ,blackjack
- /balance and ,balance
- /daily and ,daily
- Blackjack Hit / Stand / Double buttons
- Persistent JSON storage

The module intentionally keeps its state separate from reviews, Textify,
staff strikes, and the other existing bot systems.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import random
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands

import bot as bot_module


# =========================
# CONFIG
# =========================

CURRENCY_NAME = "Kevin Bucks"
CURRENCY_SYMBOL = "KB"

STARTING_BALANCE = 1_000
DAILY_AMOUNT = 500
DAILY_COOLDOWN = timedelta(hours=24)

# Perk roles affect regular Kevin Bucks rewards, not gambling winnings/refunds.
# If a member has both roles, the higher multiplier wins rather than stacking.
ROLE_EARNINGS_MULTIPLIERS = {
    1307463884596314257: 2,
    1377468541779050636: 4,
}


def get_role_earnings_multiplier(member) -> int:
    """Return the highest role earnings multiplier in the main server."""
    guild = getattr(member, "guild", None)
    if guild is None or getattr(guild, "id", None) != getattr(bot_module, "MAIN_SERVER", None):
        return 1

    role_ids = {
        getattr(role, "id", None)
        for role in getattr(member, "roles", ())
    }
    return max(
        (
            multiplier
            for role_id, multiplier in ROLE_EARNINGS_MULTIPLIERS.items()
            if role_id in role_ids
        ),
        default=1,
    )


MIN_BET = 10
MAX_BET = 100_000

BLACKJACK_PAYOUT_NUMERATOR = 3
BLACKJACK_PAYOUT_DENOMINATOR = 2

GAME_TIMEOUT_SECONDS = 300

DATA_FILE = Path(__file__).with_name("kevin_bucks.json")

# Use the same GitHub database branch as the bot's other persistent runtime
# data, but keep this currency file completely separate.
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO", "uhhreurheurh/john-the-bot")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "database")
GITHUB_API_BASE = "https://api.github.com"


# =========================
# PERSISTENT DATA
# =========================

data_lock = asyncio.Lock()


def _default_data() -> dict:
    return {
        "balances": {},
        "daily_claims": {},
        "chat_activity": {},
        "custom_role_access": {},
        "expired_custom_roles": {},
    }


def _normalize_chat_activity(raw) -> dict:
    if not isinstance(raw, dict):
        return {}

    normalized = {}
    for user_id, state in raw.items():
        if not isinstance(state, dict):
            continue
        try:
            earned_today = max(0, int(state.get("earned_today", 0)))
        except (TypeError, ValueError):
            earned_today = 0
        try:
            daily_multiplier = 2 if int(state.get("daily_multiplier", 1)) >= 2 else 1
        except (TypeError, ValueError):
            daily_multiplier = 1

        normalized[str(user_id)] = {
            "last_reward_at": str(state.get("last_reward_at", "") or ""),
            "day": str(state.get("day", "") or ""),
            "earned_today": earned_today,
            "boost_until": str(state.get("boost_until", "") or ""),
            "daily_multiplier": daily_multiplier,
        }
    return normalized


def load_data() -> dict:
    if not DATA_FILE.exists():
        return _default_data()

    try:
        with DATA_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            return _default_data()

        balances = data.get("balances", {})
        daily_claims = data.get("daily_claims", {})
        chat_activity = data.get("chat_activity", {})
        custom_role_access = data.get("custom_role_access", {})
        expired_custom_roles = data.get("expired_custom_roles", {})

        if not isinstance(balances, dict):
            balances = {}
        if not isinstance(daily_claims, dict):
            daily_claims = {}
        if not isinstance(custom_role_access, dict):
            custom_role_access = {}
        if not isinstance(expired_custom_roles, dict):
            expired_custom_roles = {}

        return {
            "balances": {
                str(user_id): max(0, int(balance))
                for user_id, balance in balances.items()
            },
            "daily_claims": {
                str(user_id): str(timestamp)
                for user_id, timestamp in daily_claims.items()
            },
            "chat_activity": _normalize_chat_activity(chat_activity),
            "custom_role_access": {
                str(user_id): str(expires_at)
                for user_id, expires_at in custom_role_access.items()
                if expires_at not in (None, "")
            },
            "expired_custom_roles": {
                str(user_id): snapshot
                for user_id, snapshot in expired_custom_roles.items()
                if isinstance(snapshot, dict)
            },
        }
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return _default_data()


data = load_data()


def _save_local() -> None:
    temporary = DATA_FILE.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, sort_keys=True)
        file.write("\n")
    temporary.replace(DATA_FILE)


def get_balance(user_id: int) -> int:
    key = str(user_id)
    if key not in data["balances"]:
        data["balances"][key] = STARTING_BALANCE
        _save_local()
    return int(data["balances"][key])


def set_balance(user_id: int, amount: int) -> None:
    data["balances"][str(user_id)] = max(0, int(amount))


def add_balance(user_id: int, amount: int) -> int:
    new_balance = get_balance(user_id) + int(amount)
    set_balance(user_id, new_balance)
    return new_balance


# =========================
# GITHUB PERSISTENCE
# =========================

def _github_headers() -> dict[str, str]:
    if not GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN is not configured.")

    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "john-the-bot-kevin-bucks",
        "Content-Type": "application/json",
    }


def _github_url() -> str:
    encoded_path = urllib.parse.quote(DATA_FILE.name, safe="")
    return f"{GITHUB_API_BASE}/repos/{GITHUB_REPO}/contents/{encoded_path}"


def _github_request(
    url: str,
    *,
    method: str = "GET",
    payload: Optional[dict] = None,
) -> dict:
    request = urllib.request.Request(
        url,
        headers=_github_headers(),
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
        raise RuntimeError(f"GitHub API HTTP {error.code}: {message}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Could not reach GitHub: {error.reason}") from error

    return json.loads(raw) if raw else {}


def _github_save_sync() -> None:
    if not GITHUB_TOKEN:
        return

    serialized = json.dumps(data, indent=2, sort_keys=True) + "\n"

    encoded_branch = urllib.parse.quote(GITHUB_BRANCH, safe="")
    try:
        response = _github_request(
            f"{_github_url()}?ref={encoded_branch}"
        )
    except RuntimeError as error:
        # Create the economy file automatically on its first save.
        if "GitHub API HTTP 404:" not in str(error):
            raise
        response = {}

    payload = {
        "message": f"Update {DATA_FILE.name}",
        "content": base64.b64encode(serialized.encode("utf-8")).decode("ascii"),
        "branch": GITHUB_BRANCH,
    }

    sha = response.get("sha")
    if sha:
        payload["sha"] = sha

    _github_request(_github_url(), method="PUT", payload=payload)


async def save_data() -> None:
    async with data_lock:
        _save_local()
        if GITHUB_TOKEN:
            try:
                await asyncio.to_thread(_github_save_sync)
            except Exception:
                # Local persistence remains authoritative if GitHub is
                # temporarily unavailable. The next save retries.
                pass


async def restore_data_from_github() -> bool:
    if not GITHUB_TOKEN:
        return False

    async with data_lock:
        try:
            encoded_branch = urllib.parse.quote(GITHUB_BRANCH, safe="")
            response = await asyncio.to_thread(
                _github_request,
                f"{_github_url()}?ref={encoded_branch}",
            )
            encoded_content = response.get("content", "")
            if not encoded_content:
                return False

            decoded = base64.b64decode(
                "".join(str(encoded_content).split())
            ).decode("utf-8")
            remote = json.loads(decoded)

            if not isinstance(remote, dict):
                return False

            balances = remote.get("balances", {})
            daily_claims = remote.get("daily_claims", {})
            chat_activity = remote.get("chat_activity", {})
            custom_role_access = remote.get("custom_role_access", {})
            expired_custom_roles = remote.get("expired_custom_roles", {})

            if not isinstance(balances, dict) or not isinstance(daily_claims, dict):
                return False
            if not isinstance(custom_role_access, dict):
                custom_role_access = {}
            if not isinstance(expired_custom_roles, dict):
                expired_custom_roles = {}

            data["balances"] = {
                str(user_id): max(0, int(balance))
                for user_id, balance in balances.items()
            }
            data["daily_claims"] = {
                str(user_id): str(timestamp)
                for user_id, timestamp in daily_claims.items()
            }
            data["chat_activity"] = _normalize_chat_activity(chat_activity)
            data["custom_role_access"] = {
                str(user_id): str(expires_at)
                for user_id, expires_at in custom_role_access.items()
                if expires_at not in (None, "")
            }
            data["expired_custom_roles"] = {
                str(user_id): snapshot
                for user_id, snapshot in expired_custom_roles.items()
                if isinstance(snapshot, dict)
            }
            _save_local()
            return True
        except Exception:
            return False


# =========================
# BLACKJACK
# =========================

@dataclass
class Card:
    rank: str
    suit: str

    @property
    def display(self) -> str:
        return f"{self.rank}{self.suit}"

    @property
    def value(self) -> int:
        if self.rank in {"J", "Q", "K"}:
            return 10
        if self.rank == "A":
            return 11
        return int(self.rank)


SUITS = ("♠", "♥", "♦", "♣")
RANKS = ("2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A")


def new_deck() -> list[Card]:
    deck = [Card(rank, suit) for suit in SUITS for rank in RANKS]
    random.shuffle(deck)
    return deck


def hand_value(hand: list[Card]) -> int:
    total = sum(card.value for card in hand)
    aces = sum(card.rank == "A" for card in hand)

    while total > 21 and aces:
        total -= 10
        aces -= 1

    return total


def is_blackjack(hand: list[Card]) -> bool:
    return len(hand) == 2 and hand_value(hand) == 21


def format_hand(hand: list[Card]) -> str:
    return " ".join(card.display for card in hand)


def format_dealer_hand(hand: list[Card], hide_first: bool = True) -> str:
    if not hand:
        return "—"
    if hide_first and len(hand) >= 2:
        return "🂠 " + " ".join(card.display for card in hand[1:])
    return format_hand(hand)


active_games: dict[int, "BlackjackGame"] = {}


class BlackjackGame:
    def __init__(
        self,
        user: discord.abc.User,
        channel: discord.abc.Messageable,
        bet: int,
    ):
        self.user = user
        self.channel = channel
        self.bet = bet
        self.deck = new_deck()
        self.player: list[Card] = []
        self.dealer: list[Card] = []
        self.message: Optional[discord.Message] = None
        self.finished = False
        self.created_at = datetime.now(timezone.utc)
        self.last_activity = self.created_at

        self.player.append(self.deck.pop())
        self.dealer.append(self.deck.pop())
        self.player.append(self.deck.pop())
        self.dealer.append(self.deck.pop())

    async def hit(self) -> str:
        if self.finished:
            return "finished"

        self.player.append(self.deck.pop())
        value = hand_value(self.player)

        if value > 21:
            await self.finish("bust")
            return "bust"

        if value == 21:
            await self.stand()
            return "21"

        return "continue"

    async def stand(self) -> None:
        if self.finished:
            return

        while hand_value(self.dealer) < 17:
            self.dealer.append(self.deck.pop())

        dealer_value = hand_value(self.dealer)
        player_value = hand_value(self.player)

        if dealer_value > 21:
            await self.finish("win")
        elif dealer_value > player_value:
            await self.finish("lose")
        elif dealer_value < player_value:
            await self.finish("win")
        else:
            await self.finish("push")

    async def double(self) -> str:
        if self.finished:
            return "finished"

        current_balance = get_balance(self.user.id)
        if current_balance < self.bet:
            return "insufficient"

        # Take the second half of the bet, then draw exactly one card.
        set_balance(self.user.id, current_balance - self.bet)
        self.bet *= 2
        self.player.append(self.deck.pop())

        value = hand_value(self.player)
        if value > 21:
            await self.finish("bust")
        else:
            await self.stand()

        return "done"

    async def finish(self, result: str) -> None:
        if self.finished:
            return

        self.finished = True
        active_games.pop(self.user.id, None)

        if result == "blackjack":
            payout = self.bet + (self.bet * BLACKJACK_PAYOUT_NUMERATOR // BLACKJACK_PAYOUT_DENOMINATOR)
            add_balance(self.user.id, payout)
        elif result == "win":
            add_balance(self.user.id, self.bet * 2)
        elif result == "push":
            add_balance(self.user.id, self.bet)
        elif result in {"timeout", "cancel"}:
            # Ending/expiring a game is not a loss. Refund the original bet.
            add_balance(self.user.id, self.bet)

        await save_data()


async def cleanup_stale_game(user_id: int) -> bool:
    """Refund and remove a stale in-memory game so a user cannot get stuck."""
    game = active_games.get(user_id)
    if game is None or game.finished:
        active_games.pop(user_id, None)
        return False
    now = datetime.now(timezone.utc)
    if (now - game.last_activity).total_seconds() < GAME_TIMEOUT_SECONDS:
        return False
    await game.finish("timeout")
    try:
        if game.message is not None:
            await game.message.edit(embed=build_embed(game, result="timeout"), view=None)
    except (discord.NotFound, discord.HTTPException):
        pass
    return True


def game_result(game: BlackjackGame) -> str:
    player_blackjack = is_blackjack(game.player)
    dealer_blackjack = is_blackjack(game.dealer)

    if player_blackjack and dealer_blackjack:
        return "push"
    if player_blackjack:
        return "blackjack"
    if dealer_blackjack:
        return "lose"

    player_value = hand_value(game.player)
    dealer_value = hand_value(game.dealer)

    if player_value > 21:
        return "bust"
    if dealer_value > 21:
        return "win"
    if player_value > dealer_value:
        return "win"
    if player_value < dealer_value:
        return "lose"
    return "push"


def build_embed(game: BlackjackGame, *, result: Optional[str] = None) -> discord.Embed:
    if result is None:
        title = "♠️ Blackjack"
        color = discord.Color.blurple()
    else:
        title = "♠️ Blackjack — " + {
            "blackjack": "BLACKJACK!",
            "win": "You Win!",
            "lose": "You Lose!",
            "push": "Push!",
            "bust": "Bust!",
            "timeout": "Game Expired",
            "cancel": "Game Ended",
        }.get(result, "Game Over")
        color = {
            "blackjack": discord.Color.gold(),
            "win": discord.Color.green(),
            "lose": discord.Color.red(),
            "push": discord.Color.orange(),
            "bust": discord.Color.red(),
            "timeout": discord.Color.orange(),
            "cancel": discord.Color.orange(),
        }.get(result, discord.Color.blurple())

    dealer_text = format_dealer_hand(game.dealer, hide_first=result is None)
    player_text = format_hand(game.player)

    embed = discord.Embed(title=title, color=color)
    embed.add_field(
        name="Dealer",
        value=f"{dealer_text}\nValue: **{'?' if result is None else hand_value(game.dealer)}**",
        inline=False,
    )
    embed.add_field(
        name=f"{game.user.display_name}'s Hand",
        value=f"{player_text}\nValue: **{hand_value(game.player)}**",
        inline=False,
    )
    embed.add_field(name="Bet", value=f"**{game.bet:,} {CURRENCY_NAME}**", inline=True)

    if result is None:
        embed.add_field(
            name="Balance",
            value=f"**{get_balance(game.user.id):,} {CURRENCY_NAME}**",
            inline=True,
        )
        embed.set_footer(text="Hit, Stand, or Double. Game expires after 2 minutes.")
    else:
        balance = get_balance(game.user.id)
        result_text = {
            "blackjack": f"Blackjack pays 3:2 — +{game.bet * 3 // 2:,} {CURRENCY_NAME}",
            "win": f"+{game.bet:,} {CURRENCY_NAME}",
            "lose": f"-{game.bet:,} {CURRENCY_NAME}",
            "push": f"Your {game.bet:,} {CURRENCY_NAME} bet was returned.",
            "bust": f"-{game.bet:,} {CURRENCY_NAME}",
            "timeout": f"Game expired — your {game.bet:,} {CURRENCY_NAME} bet was refunded.",
            "cancel": f"Game ended — your {game.bet:,} {CURRENCY_NAME} bet was refunded.",
        }.get(result, "")
        embed.add_field(name="Result", value=result_text, inline=False)
        embed.add_field(name="New Balance", value=f"**{balance:,} {CURRENCY_NAME}**", inline=True)

    return embed


class BlackjackView(discord.ui.View):
    def __init__(self, game: BlackjackGame):
        super().__init__(timeout=GAME_TIMEOUT_SECONDS)
        self.game = game

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.game.user.id:
            await interaction.response.send_message(
                "❌ This Blackjack game belongs to someone else.",
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self) -> None:
        if self.game.finished:
            return

        # An abandoned game expires without being a loss.
        # The original bet is refunded so inactivity cannot silently
        # remove Kevin Bucks.
        await self.game.finish("timeout")

        try:
            if self.game.message is not None:
                await self.game.message.edit(
                    embed=build_embed(self.game, result="timeout"),
                    view=None,
                )
        except (discord.NotFound, discord.HTTPException):
            pass

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary, emoji="🃏")
    async def hit_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        self.game.last_activity = datetime.now(timezone.utc)
        result = await self.game.hit()

        if result in {"bust", "21", "finished"}:
            final_result = game_result(self.game)
            await interaction.response.edit_message(
                embed=build_embed(self.game, result=final_result),
                view=None,
            )
            self.stop()
            return

        await interaction.response.edit_message(embed=build_embed(self.game), view=self)

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.success, emoji="✋")
    async def stand_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        self.game.last_activity = datetime.now(timezone.utc)
        await self.game.stand()
        final_result = game_result(self.game)
        await interaction.response.edit_message(
            embed=build_embed(self.game, result=final_result),
            view=None,
        )
        self.stop()

    @discord.ui.button(label="End", style=discord.ButtonStyle.danger, emoji="🛑")
    async def end_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await self.game.finish("cancel")
        await interaction.response.edit_message(
            embed=build_embed(self.game, result="cancel"),
            view=None,
        )
        self.stop()

    @discord.ui.button(label="Double", style=discord.ButtonStyle.secondary, emoji="💰")
    async def double_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        self.game.last_activity = datetime.now(timezone.utc)
        if len(self.game.player) != 2:
            await interaction.response.send_message(
                "❌ You can only double down on your initial two cards.",
                ephemeral=True,
            )
            return

        result = await self.game.double()
        if result == "insufficient":
            await interaction.response.send_message(
                f"❌ You need another **{self.game.bet:,} {CURRENCY_NAME}** to double down.",
                ephemeral=True,
            )
            return

        final_result = game_result(self.game)
        await interaction.response.edit_message(
            embed=build_embed(self.game, result=final_result),
            view=None,
        )
        self.stop()


async def start_blackjack(
    user: discord.abc.User,
    channel: discord.abc.Messageable,
    bet: int,
) -> tuple[Optional[BlackjackGame], str]:
    await cleanup_stale_game(user.id)

    if user.id in active_games:
        return None, "❌ You already have an active Blackjack game."

    balance = get_balance(user.id)
    if bet < MIN_BET:
        return None, f"❌ The minimum bet is **{MIN_BET:,} {CURRENCY_NAME}**."
    if bet > MAX_BET:
        return None, f"❌ The maximum bet is **{MAX_BET:,} {CURRENCY_NAME}**."
    if bet > balance:
        return None, f"❌ You only have **{balance:,} {CURRENCY_NAME}**."

    set_balance(user.id, balance - bet)
    game = BlackjackGame(user, channel, bet)
    active_games[user.id] = game

    # Resolve natural blackjacks immediately.
    if is_blackjack(game.player) or is_blackjack(game.dealer):
        result = game_result(game)
        await game.finish(result)

    return game, ""


async def send_blackjack(
    interaction_or_channel,
    user: discord.abc.User,
    bet: int,
    *,
    prefix_message: Optional[discord.Message] = None,
) -> None:
    game, error = await start_blackjack(user, interaction_or_channel, bet)
    if game is None:
        if isinstance(interaction_or_channel, discord.Interaction):
            await interaction_or_channel.response.send_message(error, ephemeral=False)
        else:
            await interaction_or_channel.send(error)
        return

    if isinstance(interaction_or_channel, discord.Interaction):
        if game.finished:
            await interaction_or_channel.response.send_message(
                embed=build_embed(game, result=game_result(game)),
            )
            return

        await interaction_or_channel.response.send_message(
            embed=build_embed(game),
            view=BlackjackView(game),
        )
        game.message = await interaction_or_channel.original_response()
        return

    if game.finished:
        await interaction_or_channel.send(
            embed=build_embed(game, result=game_result(game)),
        )
        return

    game.message = await interaction_or_channel.send(
        embed=build_embed(game),
        view=BlackjackView(game),
    )


# =========================
# SLASH COMMANDS
# =========================

@bot_module.tree.command(
    name="blackjack",
    description="Play Blackjack with Kevin Bucks.",
)
@app_commands.describe(bet="How many Kevin Bucks you want to bet.")
async def blackjack_command(interaction: discord.Interaction, bet: int):
    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ Blackjack can only be used in a server.",
            ephemeral=True,
        )
        return
    await send_blackjack(interaction, interaction.user, bet)


@bot_module.tree.command(
    name="balance",
    description="Check your Kevin Bucks balance.",
)
async def balance_command(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ Kevin Bucks can only be used in a server.",
            ephemeral=True,
        )
        return

    balance = get_balance(interaction.user.id)
    await interaction.response.send_message(
        f"💰 **{interaction.user.display_name}'s Kevin Bucks**\n"
        f"**{balance:,} {CURRENCY_NAME}**"
    )


@bot_module.tree.command(
    name="daily",
    description="Claim your daily Kevin Bucks.",
)
async def daily_command(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ Kevin Bucks can only be used in a server.",
            ephemeral=True,
        )
        return

    user_id = str(interaction.user.id)
    now = datetime.now(timezone.utc)

    last_claim_raw = data["daily_claims"].get(user_id)
    if last_claim_raw:
        try:
            last_claim = datetime.fromisoformat(last_claim_raw)
            next_claim = last_claim + DAILY_COOLDOWN
            if now < next_claim:
                remaining = next_claim - now
                hours = int(remaining.total_seconds() // 3600)
                minutes = int((remaining.total_seconds() % 3600) // 60)
                await interaction.response.send_message(
                    f"⏳ You already claimed your daily reward. "
                    f"Come back in **{hours}h {minutes}m**.",
                    ephemeral=True,
                )
                return
        except ValueError:
            pass

    data["daily_claims"][user_id] = now.isoformat()
    activity_state = data.setdefault("chat_activity", {}).setdefault(user_id, {})
    daily_boost_multiplier = 2 if int(activity_state.get("daily_multiplier", 1)) >= 2 else 1
    activity_state["daily_multiplier"] = 1
    role_multiplier = get_role_earnings_multiplier(interaction.user)
    reward_amount = DAILY_AMOUNT * daily_boost_multiplier * role_multiplier
    balance = add_balance(interaction.user.id, reward_amount)
    await save_data()

    applied_boosts = []
    if daily_boost_multiplier > 1:
        applied_boosts.append("Daily Boost")
    if role_multiplier > 1:
        applied_boosts.append(f"{role_multiplier}x role boost")
    boost_note = f" ({', '.join(applied_boosts)} applied)" if applied_boosts else ""

    await interaction.response.send_message(
        f"💰 You claimed **{reward_amount:,} {CURRENCY_NAME}**!{boost_note}"
        f"\nNew balance: **{balance:,} {CURRENCY_NAME}**"
    )


# =========================
# PREFIX COMMANDS
# =========================

async def handle_prefix(message: discord.Message) -> bool:
    if message.guild is None:
        return False

    content = message.content.strip()
    parts = content.split()

    if not parts:
        return False

    command = parts[0].lower()

    if command in {",blackjackcleanup", ",bjcleanup"}:
        cleaned = await cleanup_stale_game(message.author.id)
        if cleaned:
            await message.reply(
                "✅ Your stale Blackjack game was cleaned up and your bet was refunded.",
                mention_author=False,
            )
        elif message.author.id in active_games:
            await message.reply(
                "ℹ️ Your Blackjack game is still active. Use the **End** button to end it.",
                mention_author=False,
            )
        else:
            await message.reply(
                "ℹ️ You don't have an active Blackjack game.",
                mention_author=False,
            )
        return True

    if command == ",blackjack":
        if len(parts) != 2:
            await message.reply(
                f"Usage: `,blackjack <bet>` — bet {MIN_BET:,} to {MAX_BET:,} {CURRENCY_NAME}.",
                mention_author=False,
            )
            return True

        try:
            bet = int(parts[1].replace(",", ""))
        except ValueError:
            await message.reply(
                "❌ Your bet must be a whole number.",
                mention_author=False,
            )
            return True

        await send_blackjack(message.channel, message.author, bet)
        return True

    if command in {",balance", ",bal", ",bucks", ",kevinbucks"}:
        balance = get_balance(message.author.id)
        await message.reply(
            f"💰 **{message.author.display_name}'s Kevin Bucks**\n"
            f"**{balance:,} {CURRENCY_NAME}**",
            mention_author=False,
        )
        return True

    if command in {",daily", ",dailybucks"}:
        user_id = str(message.author.id)
        now = datetime.now(timezone.utc)
        last_claim_raw = data["daily_claims"].get(user_id)

        if last_claim_raw:
            try:
                last_claim = datetime.fromisoformat(last_claim_raw)
                next_claim = last_claim + DAILY_COOLDOWN
                if now < next_claim:
                    remaining = next_claim - now
                    hours = int(remaining.total_seconds() // 3600)
                    minutes = int((remaining.total_seconds() % 3600) // 60)
                    await message.reply(
                        f"⏳ You already claimed your daily reward. "
                        f"Come back in **{hours}h {minutes}m**.",
                        mention_author=False,
                    )
                    return True
            except ValueError:
                pass

        data["daily_claims"][user_id] = now.isoformat()
        activity_state = data.setdefault("chat_activity", {}).setdefault(user_id, {})
        daily_boost_multiplier = 2 if int(activity_state.get("daily_multiplier", 1)) >= 2 else 1
        activity_state["daily_multiplier"] = 1
        role_multiplier = get_role_earnings_multiplier(message.author)
        reward_amount = DAILY_AMOUNT * daily_boost_multiplier * role_multiplier
        balance = add_balance(message.author.id, reward_amount)
        await save_data()

        applied_boosts = []
        if daily_boost_multiplier > 1:
            applied_boosts.append("Daily Boost")
        if role_multiplier > 1:
            applied_boosts.append(f"{role_multiplier}x role boost")
        reward_note = f" ({', '.join(applied_boosts)} applied)" if applied_boosts else ""
        await message.reply(
            f"💰 You claimed **{reward_amount:,} {CURRENCY_NAME}**!{reward_note} "
            f"New balance: **{balance:,} {CURRENCY_NAME}**",
            mention_author=False,
        )
        return True

    return False


async def initialize() -> None:
    # Pull the persistent economy state before commands are used.
    restored = await restore_data_from_github()

    # If there was no remote file, create it so the economy has a persistent
    # starting point immediately.
    if not restored:
        await save_data()
