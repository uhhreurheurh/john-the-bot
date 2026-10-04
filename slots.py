"""
Slots feature for John the Bot.

Uses the shared Kevin Bucks economy from blackjack.py.
Provides:
- /slots
- ,slots
- Owner-only interactive Spin Again button.
"""

from __future__ import annotations

import asyncio
import random
from typing import Optional

import discord
from discord import app_commands

import bot as bot_module
import blackjack as blackjack_feature


MIN_BET = 10
MAX_BET = 100_000
GAME_TIMEOUT_SECONDS = 120

SYMBOLS = ["🍒", "🍋", "🍊", "🍇", "🔔", "💎", "7️⃣"]

# Payout is the total returned, including the original bet.
PAYOUTS = {
    ("7️⃣", "7️⃣", "7️⃣"): 15.0,
    ("💎", "💎", "💎"): 10.0,
    ("🔔", "🔔", "🔔"): 7.0,
    ("🍇", "🍇", "🍇"): 5.0,
    ("🍊", "🍊", "🍊"): 4.0,
    ("🍋", "🍋", "🍋"): 3.0,
    ("🍒", "🍒", "🍒"): 2.0,
}

active_slots_games: dict[int, "SlotsView"] = {}


def spin_reels() -> tuple[str, str, str]:
    return tuple(random.choice(SYMBOLS) for _ in range(3))


def payout_for(bet: int, reels: tuple[str, str, str]) -> int:
    multiplier = PAYOUTS.get(reels)
    if multiplier is not None:
        return int(bet * multiplier)

    # Two matching symbols return 1.5x the bet.
    if len(set(reels)) == 2:
        return int(bet * 1.5)

    return 0


class SlotsView(discord.ui.View):
    def __init__(self, user: discord.abc.User, bet: int):
        super().__init__(timeout=GAME_TIMEOUT_SECONDS)
        self.user = user
        self.bet = bet
        self.reels = spin_reels()
        self.payout = 0
        self.message: Optional[discord.Message] = None
        self.finished = False
        self.lock = asyncio.Lock()
        self._resolve_spin()

    def _resolve_spin(self) -> None:
        self.payout = payout_for(self.bet, self.reels)
        if self.payout:
            blackjack_feature.add_balance(self.user.id, self.payout)

    @property
    def is_win(self) -> bool:
        return self.payout > 0

    def build_embed(self) -> discord.Embed:
        if self.is_win:
            title = "🎰 Slots — WIN!"
            color = discord.Color.green()
            result = (
                f"You won **{self.payout:,} {blackjack_feature.CURRENCY_NAME}**!"
            )
        else:
            title = "🎰 Slots — No Win"
            color = discord.Color.red()
            result = (
                f"You lost **{self.bet:,} {blackjack_feature.CURRENCY_NAME}**."
            )

        embed = discord.Embed(title=title, color=color)
        embed.add_field(
            name="Reels",
            value=f"## {self.reels[0]} │ {self.reels[1]} │ {self.reels[2]}",
            inline=False,
        )
        embed.add_field(name="Bet", value=f"**{self.bet:,} {blackjack_feature.CURRENCY_NAME}**")
        embed.add_field(name="Result", value=result, inline=False)
        embed.add_field(
            name="Balance",
            value=f"**{blackjack_feature.get_balance(self.user.id):,} {blackjack_feature.CURRENCY_NAME}**",
            inline=False,
        )
        embed.set_footer(text="Only the person who started this Slots game can use its buttons.")
        return embed

    @discord.ui.button(
        label="Spin Again",
        style=discord.ButtonStyle.primary,
        emoji="🎰",
    )
    async def spin_again(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message(
                "This Slots game belongs to someone else.",
                ephemeral=True,
            )
            return

        async with self.lock:
            if self.finished:
                await interaction.response.send_message(
                    "This Slots game has expired.",
                    ephemeral=True,
                )
                return

            balance = blackjack_feature.get_balance(self.user.id)
            if self.bet > balance:
                await interaction.response.send_message(
                    f"You need **{self.bet:,} {blackjack_feature.CURRENCY_NAME}** to spin again. "
                    f"You have **{balance:,}**.",
                    ephemeral=True,
                )
                return

            blackjack_feature.set_balance(self.user.id, balance - self.bet)
            self.reels = spin_reels()
            self.payout = payout_for(self.bet, self.reels)
            if self.payout:
                blackjack_feature.add_balance(self.user.id, self.payout)

            await blackjack_feature.save_data()
            await interaction.response.edit_message(
                embed=self.build_embed(),
                view=self,
            )

    async def on_timeout(self) -> None:
        self.finished = True
        active_slots_games.pop(self.user.id, None)
        for child in self.children:
            child.disabled = True

        try:
            if self.message is not None:
                await self.message.edit(view=self)
        except (discord.NotFound, discord.HTTPException):
            pass


async def start_slots(
    user: discord.abc.User,
    channel: discord.abc.Messageable,
    bet: int,
):
    if user.id in active_slots_games:
        return None, "You already have an active Slots game."

    balance = blackjack_feature.get_balance(user.id)

    if bet < MIN_BET:
        return None, f"The minimum bet is **{MIN_BET:,} {blackjack_feature.CURRENCY_NAME}**."
    if bet > MAX_BET:
        return None, f"The maximum bet is **{MAX_BET:,} {blackjack_feature.CURRENCY_NAME}**."
    if bet > balance:
        return None, f"You only have **{balance:,} {blackjack_feature.CURRENCY_NAME}**."

    # Pay the wager before spinning.
    blackjack_feature.set_balance(user.id, balance - bet)
    view = SlotsView(user, bet)
    active_slots_games[user.id] = view

    # The initial spin's payout is already calculated by SlotsView.
    # Add its winnings after the wager was deducted.
    if view.payout:
        # SlotsView initially calculated against the bet; the balance deduction
        # happened immediately before construction, so the payout is safe to add.
        pass

    await blackjack_feature.save_data()
    return view, ""


async def send_slots(target, user: discord.abc.User, bet: int) -> None:
    view, error = await start_slots(user, target, bet)

    if view is None:
        if isinstance(target, discord.Interaction):
            await target.response.send_message(error, ephemeral=False)
        else:
            await target.send(error)
        return

    # Apply the initial spin payout after the bet was deducted.
    if view.payout:
        # _resolve_spin only computed payout during construction; the actual
        # payout must be credited here.
        blackjack_feature.add_balance(user.id, view.payout)
        await blackjack_feature.save_data()

    if isinstance(target, discord.Interaction):
        await target.response.send_message(embed=view.build_embed(), view=view)
        view.message = await target.original_response()
    else:
        view.message = await target.send(embed=view.build_embed(), view=view)


@bot_module.tree.command(
    name="slots",
    description="Play Slots with Kevin Bucks.",
)
@app_commands.describe(
    bet="How many Kevin Bucks you want to bet.",
)
async def slots_command(
    interaction: discord.Interaction,
    bet: int,
):
    if interaction.guild is None:
        await interaction.response.send_message(
            "Slots can only be used in a server.",
            ephemeral=True,
        )
        return

    await send_slots(interaction, interaction.user, bet)


async def handle_prefix(message: discord.Message) -> bool:
    if message.guild is None:
        return False

    parts = message.content.strip().split()
    if not parts or parts[0].lower() not in {",slots", ",slot"}:
        return False

    if len(parts) != 2:
        await message.reply(
            f"Usage: `,slots <bet>` — bet {MIN_BET:,} to {MAX_BET:,} "
            f"{blackjack_feature.CURRENCY_NAME}.",
            mention_author=False,
        )
        return True

    try:
        bet = int(parts[1].replace(",", ""))
    except ValueError:
        await message.reply(
            "Your bet must be a whole number.",
            mention_author=False,
        )
        return True

    await send_slots(message.channel, message.author, bet)
    return True
