"""
Mines feature for John the Bot.

Uses the existing Kevin Bucks economy from blackjack.py and provides:
- /mines
- ,mines
- Interactive Mines board
- Cash-out button
- One active Mines game per user
"""

from __future__ import annotations

import asyncio
import math
import random
from typing import Optional

import discord
from discord import app_commands

import bot as bot_module
import blackjack as blackjack_feature


# =========================
# CONFIG
# =========================

BOARD_SIZE = 4
BOARD_CELLS = BOARD_SIZE * BOARD_SIZE

MIN_BET = 10
MAX_BET = 100_000

DEFAULT_MINES = 3
MIN_MINES = 1
MAX_MINES = 12

GAME_TIMEOUT_SECONDS = 300

# Small house edge so the displayed cash-out multiplier is deterministic
# and still rewards successful streaks.
HOUSE_EDGE_MULTIPLIER = 0.97


active_mines_games: dict[int, "MinesGame"] = {}


def calculate_multiplier(mine_count: int, safe_picks: int) -> float:
    """Return the cash-out multiplier after safe_picks successful reveals."""
    if safe_picks <= 0:
        return 0.0

    probability = 1.0
    for pick in range(safe_picks):
        remaining_cells = BOARD_CELLS - pick
        remaining_safe = BOARD_CELLS - mine_count - pick
        probability *= remaining_safe / remaining_cells

    if probability <= 0:
        return 0.0

    return HOUSE_EDGE_MULTIPLIER / probability


def cashout_amount(bet: int, mine_count: int, safe_picks: int) -> int:
    multiplier = calculate_multiplier(mine_count, safe_picks)
    return max(0, math.floor(bet * multiplier))


class MinesGame:
    def __init__(
        self,
        user: discord.abc.User,
        channel: discord.abc.Messageable,
        bet: int,
        mine_count: int,
    ):
        self.user = user
        self.channel = channel
        self.bet = bet
        self.mine_count = mine_count
        self.mine_positions = set(random.sample(range(BOARD_CELLS), mine_count))
        self.revealed_safe: set[int] = set()
        self.revealed_mines: set[int] = set()
        self.message: Optional[discord.Message] = None
        self.finished = False
        self.lock = asyncio.Lock()

    @property
    def safe_picks(self) -> int:
        return len(self.revealed_safe)

    @property
    def all_safe_revealed(self) -> bool:
        return self.safe_picks == BOARD_CELLS - self.mine_count

    @property
    def multiplier(self) -> float:
        return calculate_multiplier(self.mine_count, self.safe_picks)

    @property
    def current_cashout(self) -> int:
        return cashout_amount(self.bet, self.mine_count, self.safe_picks)

    def reveal_all_mines(self) -> None:
        self.revealed_mines.update(self.mine_positions)

    async def finish(self, result: str, payout: int = 0) -> None:
        if self.finished:
            return

        self.finished = True
        active_mines_games.pop(self.user.id, None)

        if payout > 0:
            blackjack_feature.add_balance(self.user.id, payout)

        # An expired/abandoned game is not a loss. Refund the original
        # bet so inactivity cannot silently remove Kevin Bucks.
        if result == "timeout":
            blackjack_feature.add_balance(self.user.id, self.bet)

        await blackjack_feature.save_data()

    def result_text(self, result: Optional[str]) -> str:
        if result is None:
            if self.safe_picks == 0:
                return (
                    f"Current cash-out: **{self.bet:,} {blackjack_feature.CURRENCY_NAME}** "
                    "(0 safe picks)"
                )

            return (
                f"Current cash-out: **{self.current_cashout:,} "
                f"{blackjack_feature.CURRENCY_NAME}** "
                f"(**{self.multiplier:.2f}x**)"
            )

        if result == "cashout":
            profit = self.current_cashout - self.bet
            return (
                f"Cashed out for **{self.current_cashout:,} "
                f"{blackjack_feature.CURRENCY_NAME}** "
                f"({profit:+,} profit)."
            )

        if result == "clear":
            return (
                f"You cleared the board and won **{self.current_cashout:,} "
                f"{blackjack_feature.CURRENCY_NAME}**."
            )

        if result == "mine":
            return (
                f"You hit a mine and lost **{self.bet:,} "
                f"{blackjack_feature.CURRENCY_NAME}**."
            )

        if result == "timeout":
            return (
                f"The game expired and your **{self.bet:,} "
                f"{blackjack_feature.CURRENCY_NAME}** bet was refunded."
            )

        return "Game over."

    def build_embed(self, result: Optional[str] = None) -> discord.Embed:
        if result is None:
            title = "Mines"
            color = discord.Color.blurple()
        elif result in {"cashout", "clear"}:
            title = "Mines — You Win"
            color = discord.Color.green()
        elif result == "timeout":
            title = "Mines — Game Expired"
            color = discord.Color.orange()
        else:
            title = "Mines — Game Over"
            color = discord.Color.red()

        embed = discord.Embed(title=title, color=color)
        embed.add_field(
            name="Bet",
            value=f"**{self.bet:,} {blackjack_feature.CURRENCY_NAME}**",
            inline=True,
        )
        embed.add_field(
            name="Mines",
            value=f"**{self.mine_count}**",
            inline=True,
        )
        embed.add_field(
            name="Safe Picks",
            value=f"**{self.safe_picks}/{BOARD_CELLS - self.mine_count}**",
            inline=True,
        )

        if result is None:
            if self.safe_picks > 0:
                cashout_text = (
                    f"**{self.current_cashout:,} "
                    f"{blackjack_feature.CURRENCY_NAME}** "
                    f"({self.multiplier:.2f}x)"
                )
            else:
                cashout_text = "Pick at least one safe tile to cash out."

            embed.add_field(
                name="Cash Out",
                value=cashout_text,
                inline=False,
            )
            embed.set_footer(
                text="Pick a tile or cash out. The game expires after 5 minutes."
            )
        else:
            embed.add_field(
                name="Result",
                value=self.result_text(result),
                inline=False,
            )
            embed.add_field(
                name="New Balance",
                value=(
                    f"**{blackjack_feature.get_balance(self.user.id):,} "
                    f"{blackjack_feature.CURRENCY_NAME}**"
                ),
                inline=False,
            )

        return embed


class MinesView(discord.ui.View):
    def __init__(self, game: MinesGame):
        super().__init__(timeout=GAME_TIMEOUT_SECONDS)
        self.game = game
        self._build_board()
        self._update_cashout_button()

    def _build_board(self) -> None:
        for index in range(BOARD_CELLS):
            button = discord.ui.Button(
                label="?",
                style=discord.ButtonStyle.secondary,
                row=index // BOARD_SIZE,
                custom_id=f"mines:{self.game.user.id}:{index}",
            )

            async def callback(
                interaction: discord.Interaction,
                button: discord.ui.Button = button,
                index: int = index,
            ):
                await self._handle_tile(interaction, button, index)

            button.callback = callback
            self.add_item(button)

        self.cashout_button = discord.ui.Button(
            label="Cash Out",
            style=discord.ButtonStyle.success,
            row=4,
            custom_id=f"mines-cashout:{self.game.user.id}",
        )
        self.cashout_button.callback = self._handle_cashout
        self.add_item(self.cashout_button)

    def _update_cashout_button(self) -> None:
        self.cashout_button.disabled = self.game.safe_picks == 0 or self.game.finished

    def _update_board_buttons(self) -> None:
        for child in self.children:
            if not isinstance(child, discord.ui.Button):
                continue
            if not child.custom_id or not child.custom_id.startswith("mines:"):
                continue

            index = int(child.custom_id.rsplit(":", 1)[-1])

            if index in self.game.revealed_safe:
                child.label = "💎"
                child.style = discord.ButtonStyle.success
                child.disabled = True
            elif index in self.game.revealed_mines:
                child.label = "💣"
                child.style = discord.ButtonStyle.danger
                child.disabled = True
            elif self.game.finished:
                child.label = "💣" if index in self.game.mine_positions else "💎"
                child.style = (
                    discord.ButtonStyle.danger
                    if index in self.game.mine_positions
                    else discord.ButtonStyle.success
                )
                child.disabled = True

    async def _handle_tile(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
        index: int,
    ) -> None:
        if interaction.user.id != self.game.user.id:
            await interaction.response.send_message(
                "This Mines game belongs to someone else.",
                ephemeral=True,
            )
            return

        async with self.game.lock:
            if self.game.finished:
                await interaction.response.send_message(
                    "This Mines game is already finished.",
                    ephemeral=True,
                )
                return

            if index in self.game.revealed_safe or index in self.game.revealed_mines:
                await interaction.response.send_message(
                    "That tile has already been revealed.",
                    ephemeral=True,
                )
                return

            if index in self.game.mine_positions:
                self.game.revealed_mines.add(index)
                self.game.reveal_all_mines()
                await self.game.finish("mine")
                self._update_board_buttons()
                self._update_cashout_button()
                await interaction.response.edit_message(
                    embed=self.game.build_embed("mine"),
                    view=None,
                )
                self.stop()
                return

            self.game.revealed_safe.add(index)

            if self.game.all_safe_revealed:
                payout = self.game.current_cashout
                await self.game.finish("clear", payout=payout)
                self._update_board_buttons()
                self._update_cashout_button()
                await interaction.response.edit_message(
                    embed=self.game.build_embed("clear"),
                    view=None,
                )
                self.stop()
                return

            self._update_board_buttons()
            self._update_cashout_button()
            await interaction.response.edit_message(
                embed=self.game.build_embed(),
                view=self,
            )

    async def _handle_cashout(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.game.user.id:
            await interaction.response.send_message(
                "This Mines game belongs to someone else.",
                ephemeral=True,
            )
            return

        async with self.game.lock:
            if self.game.finished:
                await interaction.response.send_message(
                    "This Mines game is already finished.",
                    ephemeral=True,
                )
                return

            if self.game.safe_picks == 0:
                await interaction.response.send_message(
                    "Reveal at least one safe tile before cashing out.",
                    ephemeral=True,
                )
                return

            payout = self.game.current_cashout
            await self.game.finish("cashout", payout=payout)
            self._update_board_buttons()
            self._update_cashout_button()
            await interaction.response.edit_message(
                embed=self.game.build_embed("cashout"),
                view=None,
            )
            self.stop()

    async def on_timeout(self) -> None:
        async with self.game.lock:
            if self.game.finished:
                return

            self.game.reveal_all_mines()
            await self.game.finish("timeout")
            self._update_board_buttons()
            self._update_cashout_button()

            try:
                if self.game.message is not None:
                    await self.game.message.edit(
                        embed=self.game.build_embed("timeout"),
                        view=None,
                    )
            except (discord.NotFound, discord.HTTPException):
                pass


async def start_mines(
    user: discord.abc.User,
    channel: discord.abc.Messageable,
    bet: int,
    mine_count: int = DEFAULT_MINES,
) -> tuple[Optional[MinesGame], str]:
    if user.id in active_mines_games:
        return None, "You already have an active Mines game."

    balance = blackjack_feature.get_balance(user.id)

    if bet < MIN_BET:
        return (
            None,
            f"The minimum bet is **{MIN_BET:,} {blackjack_feature.CURRENCY_NAME}**.",
        )

    if bet > MAX_BET:
        return (
            None,
            f"The maximum bet is **{MAX_BET:,} {blackjack_feature.CURRENCY_NAME}**.",
        )

    if bet > balance:
        return (
            None,
            f"You only have **{balance:,} {blackjack_feature.CURRENCY_NAME}**.",
        )

    if mine_count < MIN_MINES or mine_count > MAX_MINES:
        return (
            None,
            f"Choose between **{MIN_MINES}** and **{MAX_MINES}** mines.",
        )

    if mine_count >= BOARD_CELLS:
        return None, "There must always be at least one safe tile."

    blackjack_feature.set_balance(user.id, balance - bet)
    await blackjack_feature.save_data()

    game = MinesGame(user, channel, bet, mine_count)
    active_mines_games[user.id] = game
    return game, ""


async def send_mines(
    interaction_or_channel,
    user: discord.abc.User,
    bet: int,
    mine_count: int = DEFAULT_MINES,
) -> None:
    game, error = await start_mines(
        user,
        interaction_or_channel,
        bet,
        mine_count,
    )

    if game is None:
        if isinstance(interaction_or_channel, discord.Interaction):
            await interaction_or_channel.response.send_message(
                error,
                ephemeral=False,
            )
        else:
            await interaction_or_channel.send(error)
        return

    view = MinesView(game)

    if isinstance(interaction_or_channel, discord.Interaction):
        await interaction_or_channel.response.send_message(
            embed=game.build_embed(),
            view=view,
        )
        game.message = await interaction_or_channel.original_response()
        return

    game.message = await interaction_or_channel.send(
        embed=game.build_embed(),
        view=view,
    )


# =========================
# SLASH COMMAND
# =========================

@bot_module.tree.command(
    name="mines",
    description="Play Mines with Kevin Bucks.",
)
@app_commands.describe(
    bet="How many Kevin Bucks you want to bet.",
    mines="How many mines to hide on the board (1-12).",
)
async def mines_command(
    interaction: discord.Interaction,
    bet: int,
    mines: int = DEFAULT_MINES,
):
    if interaction.guild is None:
        await interaction.response.send_message(
            "Mines can only be used in a server.",
            ephemeral=True,
        )
        return

    await send_mines(interaction, interaction.user, bet, mines)


# =========================
# PREFIX COMMAND
# =========================

async def handle_prefix(message: discord.Message) -> bool:
    if message.guild is None:
        return False

    parts = message.content.strip().split()
    if not parts:
        return False

    command = parts[0].lower()

    if command not in {",mines", ",mine"}:
        return False

    if len(parts) not in {2, 3}:
        await message.reply(
            f"Usage: \`,mines <bet> [mines]\` — bet {MIN_BET:,} to "
            f"{MAX_BET:,} {blackjack_feature.CURRENCY_NAME}.",
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

    mine_count = DEFAULT_MINES
    if len(parts) == 3:
        try:
            mine_count = int(parts[2])
        except ValueError:
            await message.reply(
                "The number of mines must be a whole number.",
                mention_author=False,
            )
            return True

    await send_mines(message.channel, message.author, bet, mine_count)
    return True
