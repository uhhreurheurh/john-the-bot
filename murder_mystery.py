"""
Button-driven Murder Mystery game for John the Bot.

All game state and UI live in this module. The main bot only imports
murder_group and registers it with its existing command tree.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import discord
from discord import app_commands

import bot as bot_module


MIN_PLAYERS = 3
MAX_PLAYERS = 12
LOBBY_TIMEOUT = 900
GAME_TIMEOUT = 1800

CASES = [
    ("The Midnight Museum", ["The Gallery", "The Security Office", "The Egyptian Wing", "The Courtyard"]),
    ("Murder at Blackwood Manor", ["The Library", "The Kitchen", "The Conservatory", "The Ballroom"]),
    ("The Last Train", ["The Dining Car", "The Sleeper Car", "The Engine Room", "The Baggage Car"]),
]

CLUES = [
    "A muddy footprint was found near the scene.",
    "Someone heard hurried footsteps just before the lights went out.",
    "A torn piece of dark fabric was found on the floor.",
    "A witness remembers seeing someone near the scene, but cannot identify them.",
    "A strange smell of smoke lingers in the room.",
    "A dropped item suggests the culprit was in a hurry.",
]


@dataclass
class MurderGame:
    guild_id: int
    channel_id: int
    host_id: int
    case_name: str
    locations: list[str]
    players: dict[int, discord.Member] = field(default_factory=dict)
    roles: dict[int, str] = field(default_factory=dict)
    alive: set[int] = field(default_factory=set)
    votes: dict[int, int] = field(default_factory=dict)
    clues: list[str] = field(default_factory=list)
    round_number: int = 0
    phase: str = "lobby"
    message: discord.Message | None = None


games: dict[int, MurderGame] = {}


def _game_for(interaction: discord.Interaction) -> MurderGame | None:
    return games.get(interaction.channel_id or 0)


def _player_list(game: MurderGame) -> str:
    if not game.players:
        return "No players have joined yet."
    return "\n".join(
        f"{'🟢' if uid in game.alive else '⚫'} {member.mention}"
        for uid, member in game.players.items()
    )


def _lobby_embed(game: MurderGame) -> discord.Embed:
    embed = discord.Embed(
        title=f"🕵️ Murder Mystery: {game.case_name}",
        description=(
            "A crime has taken place. Gather your friends, join the lobby, "
            "and see if you can uncover the murderer.\n\n"
            f"**Players ({len(game.players)}/{MAX_PLAYERS})**\n{_player_list(game)}"
        ),
        color=discord.Color.dark_purple(),
    )
    embed.add_field(name="How to play", value="Join with the button or `/murder join`. The host starts with **Begin Game** or `/murder begin`.", inline=False)
    embed.set_footer(text=f"At least {MIN_PLAYERS} players are required.")
    return embed


def _round_embed(game: MurderGame, message: str | None = None) -> discord.Embed:
    embed = discord.Embed(
        title=f"🔎 {game.case_name} — Round {game.round_number}",
        description=(
            (message + "\n\n" if message else "")
            + f"**Players still alive ({len(game.alive)})**\n"
            + _player_list(game)
            + f"\n\n**Evidence discovered:** {len(game.clues)}"
        ),
        color=discord.Color.dark_red(),
    )
    if game.clues:
        embed.add_field(name="Public evidence", value="\n".join(f"• {c}" for c in game.clues[-4:]), inline=False)
    embed.set_footer(text="Investigate privately, then cast your vote.")
    return embed


async def _finish_game(game: MurderGame, channel: discord.abc.Messageable, winner: str, reason: str):
    game.phase = "finished"
    murderer_id = next((uid for uid, role in game.roles.items() if role == "Murderer"), None)
    murderer = game.players.get(murderer_id) if murderer_id else None
    embed = discord.Embed(
        title=f"🏁 {winner} win!",
        description=f"{reason}\n\n**The murderer was:** {murderer.mention if murderer else 'Unknown'}",
        color=discord.Color.green() if winner == "The Innocents" else discord.Color.dark_red(),
    )
    embed.add_field(name="Final survivors", value=_player_list(game), inline=False)
    await channel.send(embed=embed)
    games.pop(game.channel_id, None)


async def _start_game(interaction: discord.Interaction, game: MurderGame):
    if interaction.user.id != game.host_id:
        await interaction.response.send_message("Only the player who created this lobby can start the game.", ephemeral=True)
        return
    if game.phase != "lobby":
        await interaction.response.send_message("This game has already started.", ephemeral=True)
        return
    if len(game.players) < MIN_PLAYERS:
        await interaction.response.send_message(f"You need at least {MIN_PLAYERS} players to begin.", ephemeral=True)
        return

    ids = list(game.players)
    random.shuffle(ids)
    game.roles = {uid: "Innocent" for uid in ids}
    game.roles[ids[0]] = "Murderer"
    if len(ids) >= 5:
        game.roles[ids[1]] = "Detective"
    game.alive = set(ids)
    game.phase = "playing"
    game.round_number = 1
    game.votes.clear()

    dm_failures = []
    for uid, member in game.players.items():
        role = game.roles[uid]
        private_text = (
            f"Your secret role in **{game.case_name}** is **{role}**. "
            "Keep it secret!"
        )
        if role == "Murderer":
            private_text += " Blend in, mislead the others, and avoid being voted out."
        elif role == "Detective":
            private_text += " Pay close attention to clues and help the group find the murderer."
        try:
            await member.send(private_text)
        except (discord.Forbidden, discord.HTTPException):
            dm_failures.append(member.mention)

    view = RoundView(game)
    await interaction.response.edit_message(embed=_round_embed(game, "The investigation begins. Check your DMs for your secret role."), view=view)
    if dm_failures:
        await interaction.followup.send(
            "I couldn't DM these players their secret roles: " + ", ".join(dm_failures)
            + ". They may need to allow DMs from server members.",
            ephemeral=True,
        )


async def _resolve_votes(game: MurderGame, channel: discord.abc.Messageable):
    if not game.votes:
        return
    counts: dict[int, int] = {}
    for target in game.votes.values():
        counts[target] = counts.get(target, 0) + 1
    highest = max(counts.values())
    leaders = [uid for uid, count in counts.items() if count == highest]

    if len(leaders) == 1:
        eliminated = leaders[0]
        game.alive.discard(eliminated)
        member = game.players[eliminated]
        if game.roles.get(eliminated) == "Murderer":
            await _finish_game(game, channel, "The Innocents", f"The group voted out {member.mention}, who was the murderer.")
            return
        result = f"The group voted out {member.mention}. They were an **{game.roles.get(eliminated, 'Innocent')}**, not the murderer."
    else:
        result = "The vote was tied. Nobody was eliminated this round."

    if len(game.alive) <= 1:
        await _finish_game(game, channel, "The Murderer", "Only one innocent remains.")
        return

    murderer_id = next(uid for uid, role in game.roles.items() if role == "Murderer")
    possible_victims = [uid for uid in game.alive if uid != murderer_id]
    if possible_victims:
        victim_id = random.choice(possible_victims)
        game.alive.discard(victim_id)
        result += f"\nDuring the night, **{game.players[victim_id].display_name}** was found eliminated."
    if murderer_id not in game.alive:
        await _finish_game(game, channel, "The Innocents", result)
        return
    if len(game.alive) <= 2:
        await _finish_game(game, channel, "The Murderer", result + "\nThe murderer has outnumbered the remaining innocent.")
        return

    game.round_number += 1
    game.votes.clear()
    await channel.send(embed=_round_embed(game, result), view=RoundView(game))


class JoinButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Join Game", style=discord.ButtonStyle.success, emoji="🕵️")

    async def callback(self, interaction: discord.Interaction):
        game = _game_for(interaction)
        if not game or game.phase != "lobby":
            await interaction.response.send_message("This lobby is no longer open.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Join from inside the server.", ephemeral=True)
            return
        if interaction.user.id in game.players:
            await interaction.response.send_message("You're already in this game.", ephemeral=True)
            return
        if len(game.players) >= MAX_PLAYERS:
            await interaction.response.send_message("This game is full.", ephemeral=True)
            return
        game.players[interaction.user.id] = interaction.user
        await interaction.response.edit_message(embed=_lobby_embed(game), view=self.view)


class StartButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Begin Game", style=discord.ButtonStyle.primary, emoji="▶️")

    async def callback(self, interaction: discord.Interaction):
        game = _game_for(interaction)
        if not game:
            await interaction.response.send_message("This game no longer exists.", ephemeral=True)
            return
        await _start_game(interaction, game)


class LobbyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=LOBBY_TIMEOUT)
        self.add_item(JoinButton())
        self.add_item(StartButton())

    async def on_timeout(self):
        for game in list(games.values()):
            if game.phase == "lobby" and game.message and game.message.id == getattr(self, "message_id", None):
                games.pop(game.channel_id, None)


class InvestigateButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Investigate", style=discord.ButtonStyle.secondary, emoji="🔍")

    async def callback(self, interaction: discord.Interaction):
        game = _game_for(interaction)
        if not game or game.phase != "playing" or interaction.user.id not in game.alive:
            await interaction.response.send_message("You can't investigate in this game.", ephemeral=True)
            return
        location = random.choice(game.locations)
        clue = random.choice(CLUES)
        game.clues.append(f"{location}: {clue}")
        await interaction.response.send_message(
            f"**You investigate {location}.**\n{clue}\n\nThis clue is private to you. Share it with the group if you think it matters.",
            ephemeral=True,
        )


class VoteSelect(discord.ui.Select):
    def __init__(self, game: MurderGame, voter_id: int):
        options = [
            discord.SelectOption(label=member.display_name[:100], value=str(uid))
            for uid, member in game.players.items()
            if uid in game.alive and uid != voter_id
        ]
        super().__init__(placeholder="Choose who you think is the murderer...", min_values=1, max_values=1, options=options)
        self.game = game

    async def callback(self, interaction: discord.Interaction):
        game = _game_for(interaction)
        if not game or game.phase != "playing" or interaction.user.id not in game.alive:
            await interaction.response.send_message("You can't vote in this game.", ephemeral=True)
            return
        if interaction.user.id in game.votes:
            await interaction.response.send_message("You've already voted this round.", ephemeral=True)
            return
        target_id = int(self.values[0])
        game.votes[interaction.user.id] = target_id
        await interaction.response.send_message("Your vote has been recorded privately.", ephemeral=True)
        if len(game.votes) >= len(game.alive):
            channel = interaction.channel
            if channel:
                await _resolve_votes(game, channel)


class VoteView(discord.ui.View):
    def __init__(self, game: MurderGame, voter_id: int):
        super().__init__(timeout=120)
        self.add_item(VoteSelect(game, voter_id))


class VoteButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Vote / Accuse", style=discord.ButtonStyle.danger, emoji="⚖️")

    async def callback(self, interaction: discord.Interaction):
        game = _game_for(interaction)
        if not game or game.phase != "playing" or interaction.user.id not in game.alive:
            await interaction.response.send_message("You can't vote in this game.", ephemeral=True)
            return
        if interaction.user.id in game.votes:
            await interaction.response.send_message("You've already voted this round.", ephemeral=True)
            return
        await interaction.response.send_message("Select the person you believe is the murderer:", view=VoteView(game, interaction.user.id), ephemeral=True)


class StatusButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Game Status", style=discord.ButtonStyle.secondary, emoji="📋")

    async def callback(self, interaction: discord.Interaction):
        game = _game_for(interaction)
        if not game:
            await interaction.response.send_message("This game has ended.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"**Round:** {game.round_number}\n**Alive:** {len(game.alive)}\n**Votes cast:** {len(game.votes)}/{len(game.alive)}\n**Your role:** {game.roles.get(interaction.user.id, 'Not a player')}",
            ephemeral=True,
        )


class RoundView(discord.ui.View):
    def __init__(self, game: MurderGame):
        super().__init__(timeout=GAME_TIMEOUT)
        self.add_item(InvestigateButton())
        self.add_item(VoteButton())
        self.add_item(StatusButton())


murder_group = app_commands.Group(name="murder", description="Play a button-driven Murder Mystery game.")


@murder_group.command(name="start", description="Create a new Murder Mystery lobby.")
@app_commands.guild_only()
async def murder_start(interaction: discord.Interaction):
    if interaction.guild is None or interaction.channel is None:
        await interaction.response.send_message("Start a game in a server channel.", ephemeral=True)
        return
    if interaction.channel_id in games:
        await interaction.response.send_message("There's already a Murder Mystery game in this channel.", ephemeral=True)
        return
    case_name, locations = random.choice(CASES)
    game = MurderGame(interaction.guild.id, interaction.channel_id, interaction.user.id, case_name, locations)
    if isinstance(interaction.user, discord.Member):
        game.players[interaction.user.id] = interaction.user
    games[interaction.channel_id] = game
    view = LobbyView()
    await interaction.response.send_message(embed=_lobby_embed(game), view=view)
    game.message = await interaction.original_response()
    view.message_id = game.message.id


@murder_group.command(name="join", description="Join the open Murder Mystery lobby in this channel.")
@app_commands.guild_only()
async def murder_join(interaction: discord.Interaction):
    game = _game_for(interaction)
    if not game or game.phase != "lobby":
        await interaction.response.send_message("There isn't an open lobby in this channel. Use `/murder start`.", ephemeral=True)
        return
    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Join from inside the server.", ephemeral=True)
        return
    if interaction.user.id in game.players:
        await interaction.response.send_message("You're already in the lobby.", ephemeral=True)
        return
    if len(game.players) >= MAX_PLAYERS:
        await interaction.response.send_message("This lobby is full.", ephemeral=True)
        return
    game.players[interaction.user.id] = interaction.user
    await interaction.response.send_message("You joined the Murder Mystery lobby.", ephemeral=True)
    if game.message:
        await game.message.edit(embed=_lobby_embed(game))


@murder_group.command(name="begin", description="Start the lobby you created.")
@app_commands.guild_only()
async def murder_begin(interaction: discord.Interaction):
    game = _game_for(interaction)
    if not game:
        await interaction.response.send_message("There isn't a game in this channel.", ephemeral=True)
        return
    await _start_game(interaction, game)


@murder_group.command(name="status", description="Check the current Murder Mystery game.")
@app_commands.guild_only()
async def murder_status(interaction: discord.Interaction):
    game = _game_for(interaction)
    if not game:
        await interaction.response.send_message("There isn't a game in this channel.", ephemeral=True)
        return
    if game.phase == "lobby":
        await interaction.response.send_message(embed=_lobby_embed(game), ephemeral=True)
    else:
        role = game.roles.get(interaction.user.id, "Not a player")
        await interaction.response.send_message(
            f"**Case:** {game.case_name}\n**Round:** {game.round_number}\n**Alive:** {len(game.alive)}\n**Your secret role:** {role}",
            ephemeral=True,
        )


@murder_group.command(name="end", description="End the current Murder Mystery game (host only).")
@app_commands.guild_only()
async def murder_end(interaction: discord.Interaction):
    game = _game_for(interaction)
    if not game:
        await interaction.response.send_message("There isn't a game in this channel.", ephemeral=True)
        return
    if interaction.user.id != game.host_id:
        await interaction.response.send_message("Only the lobby host can end this game.", ephemeral=True)
        return
    games.pop(game.channel_id, None)
    await interaction.response.send_message("The Murder Mystery game has been ended.", ephemeral=False)
