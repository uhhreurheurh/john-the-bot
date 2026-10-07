"""Coin Flip Battle for John the Bot.

Uses the shared Kevin Bucks economy from blackjack.py.
Commands:
- /coinflipbattle @user <bet>
- ,coinflipbattle @user <bet>
- ,coinflip @user <bet>

The challenged user must accept. Both players pay the same wager and a
fair coin determines the winner. The winner receives the full pot.
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
CHALLENGE_TIMEOUT = 120

active_battles: dict[int, "CoinFlipBattle"] = {}


class CoinFlipBattle:
    def __init__(self, challenger: discord.abc.User, opponent: discord.abc.User, bet: int):
        self.challenger = challenger
        self.opponent = opponent
        self.bet = bet
        self.message: Optional[discord.Message] = None
        self.finished = False
        self.accepted = False
        self.lock = asyncio.Lock()

    def player_name(self, user: discord.abc.User) -> str:
        return getattr(user, "display_name", getattr(user, "name", "Player"))

    def build_challenge_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="🪙 Coin Flip Battle",
            description=(
                f"{self.challenger.mention} challenged {self.opponent.mention} "
                f"to a Coin Flip Battle!"
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Wager",
            value=f"**{self.bet:,} {blackjack_feature.CURRENCY_NAME} each**",
            inline=False,
        )
        embed.add_field(
            name="Prize",
            value=f"**{self.bet * 2:,} {blackjack_feature.CURRENCY_NAME}**",
            inline=False,
        )
        embed.set_footer(text="Only the challenged player can accept or decline.")
        return embed

    def build_result_embed(self, winner, coin: str) -> discord.Embed:
        embed = discord.Embed(
            title="🪙 Coin Flip Battle — " + ("Heads!" if coin == "Heads" else "Tails!"),
            description=f"**{winner.mention} wins the battle!**",
            color=discord.Color.gold(),
        )
        embed.add_field(name="Coin", value=f"**{coin}**", inline=True)
        embed.add_field(
            name="Winnings",
            value=f"**{self.bet * 2:,} {blackjack_feature.CURRENCY_NAME}**",
            inline=True,
        )
        embed.add_field(
            name="Bet",
            value=f"**{self.bet:,} {blackjack_feature.CURRENCY_NAME} each**",
            inline=False,
        )
        embed.add_field(
            name=f"{self.player_name(self.challenger)}'s Balance",
            value=f"**{blackjack_feature.get_balance(self.challenger.id):,} {blackjack_feature.CURRENCY_NAME}**",
            inline=True,
        )
        embed.add_field(
            name=f"{self.player_name(self.opponent)}'s Balance",
            value=f"**{blackjack_feature.get_balance(self.opponent.id):,} {blackjack_feature.CURRENCY_NAME}**",
            inline=True,
        )
        return embed


class CoinFlipBattleView(discord.ui.View):
    def __init__(self, battle: CoinFlipBattle):
        super().__init__(timeout=CHALLENGE_TIMEOUT)
        self.battle = battle

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.battle.opponent.id:
            await interaction.response.send_message(
                "❌ Only the challenged player can accept or decline this battle.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, emoji="🪙")
    async def accept(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        battle = self.battle
        async with battle.lock:
            if battle.finished or battle.accepted:
                await interaction.response.send_message(
                    "❌ This battle has already been resolved.",
                    ephemeral=True,
                )
                return

            challenger_balance = blackjack_feature.get_balance(battle.challenger.id)
            opponent_balance = blackjack_feature.get_balance(battle.opponent.id)

            if challenger_balance < battle.bet:
                battle.finished = True
                active_battles.pop(battle.challenger.id, None)
                active_battles.pop(battle.opponent.id, None)
                await interaction.response.edit_message(
                    content="❌ The challenger no longer has enough Kevin Bucks for this battle.",
                    embed=None,
                    view=None,
                )
                return

            if opponent_balance < battle.bet:
                battle.finished = True
                active_battles.pop(battle.challenger.id, None)
                active_battles.pop(battle.opponent.id, None)
                await interaction.response.edit_message(
                    content="❌ You no longer have enough Kevin Bucks for this battle.",
                    embed=None,
                    view=None,
                )
                return

            battle.accepted = True

            blackjack_feature.set_balance(
                battle.challenger.id,
                challenger_balance - battle.bet,
            )
            blackjack_feature.set_balance(
                battle.opponent.id,
                opponent_balance - battle.bet,
            )

            coin = random.choice(("Heads", "Tails"))
            winner = battle.challenger if coin == "Heads" else battle.opponent

            blackjack_feature.add_balance(winner.id, battle.bet * 2)

            battle.finished = True
            active_battles.pop(battle.challenger.id, None)
            active_battles.pop(battle.opponent.id, None)

            await blackjack_feature.save_data()

            await interaction.response.edit_message(
                content=None,
                embed=battle.build_result_embed(winner, coin),
                view=None,
            )
            self.stop()

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger, emoji="✖️")
    async def decline(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        battle = self.battle
        async with battle.lock:
            if battle.finished:
                await interaction.response.send_message(
                    "❌ This battle has already been resolved.",
                    ephemeral=True,
                )
                return

            battle.finished = True
            active_battles.pop(battle.challenger.id, None)
            active_battles.pop(battle.opponent.id, None)

            await interaction.response.edit_message(
                content="❌ Coin Flip Battle declined.",
                embed=None,
                view=None,
            )
            self.stop()

    async def on_timeout(self) -> None:
        battle = self.battle
        async with battle.lock:
            if battle.finished:
                return

            battle.finished = True
            active_battles.pop(battle.challenger.id, None)
            active_battles.pop(battle.opponent.id, None)

            try:
                if battle.message is not None:
                    await battle.message.edit(
                        content="⌛ Coin Flip Battle expired.",
                        embed=None,
                        view=None,
                    )
            except (discord.NotFound, discord.HTTPException):
                pass


async def start_battle(
    challenger: discord.abc.User,
    opponent: discord.abc.User,
    bet: int,
):
    if challenger.id == opponent.id:
        return None, "❌ You can't battle yourself."

    if opponent.bot:
        return None, "❌ You can't challenge a bot."

    if challenger.id in active_battles or opponent.id in active_battles:
        return None, "❌ One of the players already has an active Coin Flip Battle."

    if bet < MIN_BET:
        return None, f"❌ The minimum bet is **{MIN_BET:,} {blackjack_feature.CURRENCY_NAME}**."

    if bet > MAX_BET:
        return None, f"❌ The maximum bet is **{MAX_BET:,} {blackjack_feature.CURRENCY_NAME}**."

    challenger_balance = blackjack_feature.get_balance(challenger.id)
    opponent_balance = blackjack_feature.get_balance(opponent.id)

    if challenger_balance < bet:
        return None, f"❌ You only have **{challenger_balance:,} {blackjack_feature.CURRENCY_NAME}**."

    if opponent_balance < bet:
        return None, (
            f"❌ {opponent.mention} only has **{opponent_balance:,} "
            f"{blackjack_feature.CURRENCY_NAME}** and can't cover the bet."
        )

    battle = CoinFlipBattle(challenger, opponent, bet)
    active_battles[challenger.id] = battle
    active_battles[opponent.id] = battle
    return battle, ""


async def send_battle(target, challenger, opponent, bet: int) -> None:
    battle, error = await start_battle(challenger, opponent, bet)

    if battle is None:
        if isinstance(target, discord.Interaction):
            await target.response.send_message(error, ephemeral=False)
        else:
            await target.send(error)
        return

    view = CoinFlipBattleView(battle)

    if isinstance(target, discord.Interaction):
        await target.response.send_message(
            content=opponent.mention,
            embed=battle.build_challenge_embed(),
            view=view,
        )
        battle.message = await target.original_response()
    else:
        battle.message = await target.send(
            content=opponent.mention,
            embed=battle.build_challenge_embed(),
            view=view,
        )


@bot_module.tree.command(
    name="coinflipbattle",
    description="Challenge another player to a Coin Flip Battle.",
)
@app_commands.describe(
    member="The player you want to challenge.",
    bet="How many Kevin Bucks each player wagers.",
)
async def coinflipbattle_command(
    interaction: discord.Interaction,
    member: discord.Member,
    bet: int,
):
    if interaction.guild is None:
        await interaction.response.send_message(
            "Coin Flip Battles can only be used in a server.",
            ephemeral=True,
        )
        return

    await send_battle(interaction, interaction.user, member, bet)


async def handle_prefix(message: discord.Message) -> bool:
    if message.guild is None:
        return False

    parts = message.content.strip().split()
    if not parts or parts[0].lower() not in {",coinflipbattle", ",coinflip"}:
        return False

    if len(parts) != 3 or not message.mentions:
        await message.reply(
            "Usage: ,coinflipbattle @user <bet>",
            mention_author=False,
        )
        return True

    opponent = message.mentions[0]

    try:
        bet = int(parts[2].replace(",", ""))
    except ValueError:
        await message.reply("Your bet must be a whole number.", mention_author=False)
        return True

    await send_battle(message.channel, message.author, opponent, bet)
    return True
