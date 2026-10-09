"""
Personal custom-role commands inspired by Bleed's boosterrole feature.

Slash commands: /custom role create, color, random, rename, icon, remove
Prefix equivalents: ,custom role ...
Only configured staff roles in MAIN_SERVER may use this feature.
"""

from __future__ import annotations

import asyncio
import base64
import json
import random
import re
import shlex
import urllib.parse
import urllib.request
from pathlib import Path

import discord
from discord import app_commands

import bot as bot_module


ALLOWED_ROLE_IDS = {
    1306082718060384399,
    1341594605686358047,
}
REGISTRY_FILE = Path(__file__).with_name("custom_roles.json")
MAX_ICON_BYTES = 256 * 1024

_role_registry: dict[int, int] = {}
_registry_loaded = False
_registry_load_lock = asyncio.Lock()
_role_mutation_lock = asyncio.Lock()
_registry_sync_error: str | None = None


class CustomRoleError(Exception):
    """A safe, user-facing custom-role error."""


def _member_is_authorized(member: discord.Member | discord.User) -> bool:
    return bool(
        {role.id for role in getattr(member, "roles", ())}
        & ALLOWED_ROLE_IDS
    )


def _authorization_error(
    guild: discord.Guild | None,
    member: discord.Member | discord.User,
) -> str | None:
    if guild is None or guild.id != bot_module.MAIN_SERVER:
        return "❌ Custom roles can only be managed in the main server."
    if not isinstance(member, discord.Member) or not _member_is_authorized(member):
        return "❌ You do not have permission to use custom role commands."
    return None


def _local_registry_read() -> dict[int, int]:
    try:
        with REGISTRY_FILE.open("r", encoding="utf-8") as file:
            payload = json.load(file)
        if not isinstance(payload, dict):
            return {}
        result: dict[int, int] = {}
        for member_id, role_id in payload.items():
            try:
                result[int(member_id)] = int(role_id)
            except (ValueError, TypeError):
                continue
        return result
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return {}


def _local_registry_write(registry: dict[int, int]) -> None:
    with REGISTRY_FILE.open("w", encoding="utf-8") as file:
        json.dump(
            {str(member_id): role_id for member_id, role_id in sorted(registry.items())},
            file,
            indent=2,
        )
        file.write("\n")


def _github_registry_read() -> tuple[dict[int, int] | None, str | None]:
    branch = urllib.parse.quote(bot_module.GITHUB_BRANCH, safe="")
    url = f"{bot_module._github_contents_url(REGISTRY_FILE)}?ref={branch}"
    try:
        payload = bot_module._github_request_json(url)
    except RuntimeError as error:
        if str(error).startswith("GitHub API HTTP 404:"):
            return None, None
        raise

    encoded_content = payload.get("content", "")
    if not encoded_content:
        return {}, payload.get("sha")

    try:
        raw = base64.b64decode(
            "".join(str(encoded_content).split())
        ).decode("utf-8")
        content = json.loads(raw)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("custom_roles.json on GitHub contains invalid JSON.") from error

    if not isinstance(content, dict):
        raise RuntimeError("custom_roles.json on GitHub must contain a JSON object.")

    registry: dict[int, int] = {}
    for member_id, role_id in content.items():
        try:
            registry[int(member_id)] = int(role_id)
        except (ValueError, TypeError):
            continue
    return registry, payload.get("sha")


def _github_registry_write(registry: dict[int, int]) -> None:
    _, blob_sha = _github_registry_read()
    serialized = json.dumps(
        {str(member_id): role_id for member_id, role_id in sorted(registry.items())},
        indent=2,
    ) + "\n"
    payload = {
        "message": "Update custom role registry",
        "content": base64.b64encode(serialized.encode("utf-8")).decode("ascii"),
        "branch": bot_module.GITHUB_BRANCH,
    }
    if blob_sha:
        payload["sha"] = blob_sha
    bot_module._github_request_json(
        bot_module._github_contents_url(REGISTRY_FILE),
        method="PUT",
        payload=payload,
    )


async def _ensure_registry_loaded() -> None:
    global _registry_loaded, _registry_sync_error

    if _registry_loaded:
        return

    async with _registry_load_lock:
        if _registry_loaded:
            return

        local_registry = await asyncio.to_thread(_local_registry_read)
        if not bot_module.GITHUB_TOKEN:
            _role_registry.update(local_registry)
            _registry_sync_error = "GITHUB_TOKEN is not configured; the role registry cannot be backed up to GitHub."
            _registry_loaded = True
            return

        try:
            remote_registry, _ = await asyncio.to_thread(_github_registry_read)
            if remote_registry is not None:
                _role_registry.clear()
                _role_registry.update(remote_registry)
                await asyncio.to_thread(_local_registry_write, _role_registry)
            else:
                _role_registry.update(local_registry)
                if local_registry:
                    await asyncio.to_thread(_github_registry_write, _role_registry)
            _registry_sync_error = None
        except Exception as error:
            _role_registry.update(local_registry)
            _registry_sync_error = str(error)

        _registry_loaded = True


async def _persist_registry() -> bool:
    global _registry_sync_error

    try:
        await asyncio.to_thread(_local_registry_write, _role_registry)
    except OSError as error:
        _registry_sync_error = f"Could not save custom_roles.json locally: {error}"
        return False

    if not bot_module.GITHUB_TOKEN:
        _registry_sync_error = "GITHUB_TOKEN is not configured; the role registry only saved locally."
        return False

    try:
        await asyncio.to_thread(_github_registry_write, dict(_role_registry))
        _registry_sync_error = None
        return True
    except Exception as error:
        _registry_sync_error = str(error)
        return False


def _parse_color(value: str | None) -> discord.Colour | None:
    if not value:
        return None

    candidate = value.strip()
    named = {
        "black", "white", "red", "green", "blue", "blurple", "yellow",
        "orange", "purple", "magenta", "teal", "dark_blue", "dark_green",
        "dark_red", "dark_purple", "gold", "light_grey", "dark_grey",
        "grey", "pink", "fuchsia", "brand_green",
    }
    normalized = candidate.lower().replace("-", "_").replace(" ", "_")
    if normalized in named:
        factory = getattr(discord.Colour, normalized, None)
        if callable(factory):
            return factory()

    try:
        return discord.Colour.from_str(candidate)
    except (TypeError, ValueError):
        return None


def _is_explicit_color_token(value: str) -> bool:
    candidate = value.strip()
    return bool(
        candidate.startswith("#")
        or candidate.lower().startswith("0x")
        or re.fullmatch(r"[0-9a-fA-F]{6}", candidate)
        or re.fullmatch(r"rgb\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*\)", candidate, flags=re.IGNORECASE)
    )


async def _bot_member_with_role_permission(guild: discord.Guild) -> discord.Member:
    bot_member = guild.me
    if bot_member is None and bot_module.bot.user is not None:
        try:
            bot_member = await guild.fetch_member(bot_module.bot.user.id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            bot_member = None

    if bot_member is None or not bot_member.guild_permissions.manage_roles:
        raise CustomRoleError("❌ I need the Manage Roles permission to manage custom roles.")
    return bot_member


async def _owned_role(guild: discord.Guild, member: discord.Member) -> discord.Role | None:
    await _ensure_registry_loaded()
    role_id = _role_registry.get(member.id)
    if role_id is None:
        return None

    role = guild.get_role(role_id)
    if role is not None:
        return role

    _role_registry.pop(member.id, None)
    await _persist_registry()
    return None


def _validate_name(name: str) -> str:
    cleaned = discord.utils.escape_mentions(name).strip()
    if not cleaned:
        raise CustomRoleError("❌ Please provide a name for your custom role.")
    if len(cleaned) > 100:
        raise CustomRoleError("❌ Custom role names cannot be longer than 100 characters.")
    return cleaned


def _validate_gradient(guild: discord.Guild, secondary: discord.Colour | None) -> None:
    if secondary is not None and "ENHANCED_ROLE_COLORS" not in guild.features:
        raise CustomRoleError(
            "❌ Two-color gradients require Enhanced Role Styles to be enabled in this server. "
            "You can still create or use a single-color role."
        )


async def _create_role(
    guild: discord.Guild,
    member: discord.Member,
    name: str,
    primary: discord.Colour,
    secondary: discord.Colour | None,
) -> str:
    role_name = _validate_name(name)
    _validate_gradient(guild, secondary)

    async with _role_mutation_lock:
        await _ensure_registry_loaded()
        existing = await _owned_role(guild, member)
        if existing is not None:
            raise CustomRoleError(
                f"❌ You already have {existing.mention}. Use custom role rename, "
                "custom role color, or custom role remove to manage it."
            )

        bot_member = await _bot_member_with_role_permission(guild)
        if primary.value == 0:
            raise CustomRoleError("❌ Please use a visible color instead of the default role color.")

        role = await guild.create_role(
            name=role_name,
            permissions=discord.Permissions.none(),
            colour=primary,
            secondary_colour=secondary,
            hoist=False,
            mentionable=False,
            reason=f"Custom role created for {member} ({member.id})",
        )

        try:
            # Place the role just below the bot's highest role so the custom
            # color is visible over lower roles whenever hierarchy allows it.
            if bot_member.top_role.position > 1:
                role = await role.edit(
                    position=max(1, bot_member.top_role.position - 1),
                    reason="Positioning a member's custom role below the bot role",
                )
            await member.add_roles(
                role,
                reason="Assigning the member's custom role",
            )
        except (discord.Forbidden, discord.HTTPException) as error:
            try:
                await role.delete(reason="Rolling back a custom role that could not be assigned")
            except (discord.Forbidden, discord.HTTPException):
                pass
            raise CustomRoleError(
                "❌ I couldn't finish setting up or assigning the role. Check the bot's role hierarchy "
                "and Manage Roles permission."
            ) from error

        _role_registry[member.id] = role.id
        saved = await _persist_registry()
        response = f"✅ Created {role.mention} for you."
        if secondary is not None:
            response += " It uses a two-color gradient."
        if not saved:
            response += (
                "\n⚠️ The role was created, but its registry could not sync to GitHub. "
                "It may not be manageable by command after a redeploy."
            )
        return response


async def _change_color(
    guild: discord.Guild,
    member: discord.Member,
    primary: discord.Colour,
    secondary: discord.Colour | None,
) -> str:
    _validate_gradient(guild, secondary)
    async with _role_mutation_lock:
        role = await _owned_role(guild, member)
        if role is None:
            raise CustomRoleError("❌ You don't have a custom role yet. Use custom role create first.")
        bot_member = await _bot_member_with_role_permission(guild)
        if role.managed or role.is_default() or bot_member.top_role <= role:
            raise CustomRoleError(
                "❌ I cannot edit your role because it is managed or at/above my highest role. "
                "Move my highest role above it in Server Settings → Roles."
            )
        await role.edit(
            colour=primary,
            secondary_colour=secondary,
            reason=f"Custom role color changed by {member} ({member.id})",
        )
        return (
            f"✅ Updated the color of {role.mention} to {primary}."
            + (f" Secondary color: {secondary}." if secondary is not None else " The role now uses a solid color.")
        )


async def _randomize_color(
    guild: discord.Guild,
    member: discord.Member,
) -> str:
    async with _role_mutation_lock:
        role = await _owned_role(guild, member)
        if role is None:
            raise CustomRoleError("❌ You don't have a custom role yet. Use custom role create first.")
        bot_member = await _bot_member_with_role_permission(guild)
        if role.managed or role.is_default() or bot_member.top_role <= role:
            raise CustomRoleError("❌ I cannot edit your role because it is above my highest role.")
        color = discord.Colour(random.randint(1, 0xFFFFFF))
        await role.edit(
            colour=color,
            secondary_colour=None,
            reason=f"Custom role randomized by {member} ({member.id})",
        )
        return f"✅ {role.mention} now has a random color: {color}."


async def _rename_role(
    guild: discord.Guild,
    member: discord.Member,
    new_name: str,
) -> str:
    role_name = _validate_name(new_name)
    async with _role_mutation_lock:
        role = await _owned_role(guild, member)
        if role is None:
            raise CustomRoleError("❌ You don't have a custom role yet. Use custom role create first.")
        bot_member = await _bot_member_with_role_permission(guild)
        if role.managed or role.is_default() or bot_member.top_role <= role:
            raise CustomRoleError("❌ I cannot rename your role because it is above my highest role.")
        await role.edit(
            name=role_name,
            reason=f"Custom role renamed by {member} ({member.id})",
        )
        return f"✅ Your custom role is now named {role_name}."


def _download_icon(url: str) -> bytes:
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise CustomRoleError("❌ Provide a direct HTTPS URL to a PNG or JPEG image.")
    request = urllib.request.Request(
        url.strip(),
        headers={"User-Agent": "John-the-Bot custom role icon"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            content_type = response.headers.get_content_type().lower()
            data = response.read(MAX_ICON_BYTES + 1)
    except Exception as error:
        raise CustomRoleError("❌ I couldn't download that role icon. Check that the image URL works.") from error

    if content_type not in {"image/png", "image/jpeg"}:
        raise CustomRoleError("❌ The role icon URL must return a PNG or JPEG image.")
    if not data or len(data) > MAX_ICON_BYTES:
        raise CustomRoleError("❌ The role icon must be smaller than 256 KB.")
    if content_type == "image/png" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise CustomRoleError("❌ That URL did not return a valid PNG image.")
    if content_type == "image/jpeg" and not data.startswith(b"\xff\xd8\xff"):
        raise CustomRoleError("❌ That URL did not return a valid JPEG image.")
    return data


async def _set_icon(
    guild: discord.Guild,
    member: discord.Member,
    url: str,
) -> str:
    if "ROLE_ICONS" not in guild.features:
        raise CustomRoleError("❌ This server does not have Discord's Role Icons feature enabled.")
    icon_data = await asyncio.to_thread(_download_icon, url)
    async with _role_mutation_lock:
        role = await _owned_role(guild, member)
        if role is None:
            raise CustomRoleError("❌ You don't have a custom role yet. Use custom role create first.")
        bot_member = await _bot_member_with_role_permission(guild)
        if role.managed or role.is_default() or bot_member.top_role <= role:
            raise CustomRoleError("❌ I cannot edit your role because it is above my highest role.")
        await role.edit(
            display_icon=icon_data,
            reason=f"Custom role icon changed by {member} ({member.id})",
        )
        return f"✅ Updated the icon for {role.mention}."


async def _remove_role(
    guild: discord.Guild,
    member: discord.Member,
) -> str:
    async with _role_mutation_lock:
        role = await _owned_role(guild, member)
        if role is None:
            raise CustomRoleError("❌ You don't have a custom role to remove.")
        bot_member = await _bot_member_with_role_permission(guild)
        if role.managed or role.is_default() or bot_member.top_role <= role:
            raise CustomRoleError("❌ I cannot delete your role because it is managed or above my highest role.")
        await role.delete(reason=f"Custom role removed by {member} ({member.id})")
        _role_registry.pop(member.id, None)
        saved = await _persist_registry()
        response = "✅ Your custom role was removed."
        if not saved:
            response += "\n⚠️ The role was deleted, but the registry could not sync to GitHub."
        return response


async def _send_interaction_response(
    interaction: discord.Interaction,
    message: str,
    *,
    followup: bool = False,
) -> None:
    if followup or interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=False)
    else:
        await interaction.response.send_message(message, ephemeral=False)


async def _run_slash(
    interaction: discord.Interaction,
    operation,
    *args,
) -> None:
    auth_error = _authorization_error(interaction.guild, interaction.user)
    if auth_error:
        await _send_interaction_response(interaction, auth_error)
        return

    await interaction.response.defer(ephemeral=False)
    try:
        response = await operation(interaction.guild, interaction.user, *args)
    except CustomRoleError as error:
        response = str(error)
    except discord.Forbidden:
        response = (
            "❌ Discord denied that role action. Check that I have Manage Roles "
            "and that my highest role is above your custom role."
        )
    except discord.HTTPException as error:
        response = f"❌ Discord rejected the custom role change: {error}"
    except Exception as error:
        response = f"❌ The custom role action failed: {type(error).__name__}: {error}"
    await _send_interaction_response(interaction, response, followup=True)


custom_group = app_commands.Group(
    name="custom",
    description="Manage your personal custom role.",
)
custom_role_group = app_commands.Group(
    name="role",
    description="Create, customize, and remove your personal role.",
)


@custom_role_group.command(name="create", description="Create your own custom role.")
@app_commands.describe(
    name="The name for your role",
    color="Primary color, such as #FF00FF or red",
    second_color="Optional second color for a gradient (requires Enhanced Role Styles)",
)
async def custom_role_create(
    interaction: discord.Interaction,
    name: str,
    color: str,
    second_color: str | None = None,
):
    primary = _parse_color(color)
    secondary = _parse_color(second_color) if second_color else None
    if primary is None or (second_color and secondary is None):
        await _send_interaction_response(
            interaction,
            "❌ Invalid color. Use a hex color such as #FF00FF, 0xFF00FF, or a supported color name.",
        )
        return
    await _run_slash(interaction, _create_role, name, primary, secondary)


@custom_role_group.command(name="color", description="Change your custom role's color.")
@app_commands.describe(
    color="Primary color, such as #FF00FF or red",
    second_color="Optional second color for a gradient",
)
async def custom_role_color(
    interaction: discord.Interaction,
    color: str,
    second_color: str | None = None,
):
    primary = _parse_color(color)
    secondary = _parse_color(second_color) if second_color else None
    if primary is None or (second_color and secondary is None):
        await _send_interaction_response(
            interaction,
            "❌ Invalid color. Use a hex color such as #FF00FF, 0xFF00FF, or a supported color name.",
        )
        return
    await _run_slash(interaction, _change_color, primary, secondary)


@custom_role_group.command(name="random", description="Give your custom role a random solid color.")
async def custom_role_random(interaction: discord.Interaction):
    await _run_slash(interaction, _randomize_color)


@custom_role_group.command(name="rename", description="Rename your custom role.")
@app_commands.describe(name="The new name for your role")
async def custom_role_rename(interaction: discord.Interaction, name: str):
    await _run_slash(interaction, _rename_role, name)


@custom_role_group.command(name="icon", description="Set your custom role's icon from a PNG or JPEG URL.")
@app_commands.describe(url="Direct HTTPS URL to a PNG or JPEG image")
async def custom_role_icon(interaction: discord.Interaction, url: str):
    await _run_slash(interaction, _set_icon, url)


@custom_role_group.command(name="remove", description="Delete your custom role.")
async def custom_role_remove(interaction: discord.Interaction):
    await _run_slash(interaction, _remove_role)


custom_group.add_command(custom_role_group)
bot_module.tree.add_command(custom_group)


async def handle_prefix(message: discord.Message) -> bool:
    """Handle ,custom role commands. Returns True when the message was a command."""
    if message.guild is None or message.author.bot:
        return False

    content = message.content.strip()
    if not content.startswith(","):
        return False

    try:
        parts = shlex.split(content)
    except ValueError:
        if content.lower().startswith((",custom role", ",customrole")):
            await message.reply(
                "❌ I couldn't parse those arguments. Put quotes around a role name with special characters.",
                mention_author=False,
            )
            return True
        return False

    if not parts:
        return False

    command = parts[0].lower()
    if command == ",custom":
        if len(parts) < 2 or parts[1].lower() != "role":
            return False
        action_index = 2
    elif command == ",customrole":
        action_index = 1
    else:
        return False

    action = parts[action_index].lower() if len(parts) > action_index else ""
    auth_error = _authorization_error(message.guild, message.author)
    if auth_error:
        await message.reply(auth_error, mention_author=False)
        return True

    if action in {"create", "color"}:
        color_index = action_index + 1
        expected_prefix = (
            ",custom role create <color> [second-color] <name>"
            if command == ",custom"
            else ",customrole create <color> [second-color] <name>"
        )
        if len(parts) <= color_index:
            await message.reply(
                f"Usage: {expected_prefix}",
                mention_author=False,
            )
            return True

        primary = _parse_color(parts[color_index])
        if primary is None:
            await message.reply(
                "❌ Invalid color. Use a hex color such as #FF00FF, 0xFF00FF, or a supported color name.",
                mention_author=False,
            )
            return True

        rest = parts[color_index + 1:]
        secondary = None
        if action == "create":
            if len(rest) >= 2 and _is_explicit_color_token(rest[0]):
                secondary = _parse_color(rest[0])
                if secondary is None:
                    await message.reply("❌ The second color is invalid.", mention_author=False)
                    return True
                rest = rest[1:]
            name = " ".join(rest).strip()
            if not name:
                await message.reply(
                    "Usage: ,custom role create <color> [second-color] <name>",
                    mention_author=False,
                )
                return True
            operation = _create_role
            args = (name, primary, secondary)
        else:
            if len(rest) > 1:
                await message.reply(
                    "Usage: ,custom role color <color> [second-color]",
                    mention_author=False,
                )
                return True
            if rest:
                if not _is_explicit_color_token(rest[0]):
                    await message.reply(
                        "❌ For a second color, use a hex value such as #00FF00.",
                        mention_author=False,
                    )
                    return True
                secondary = _parse_color(rest[0])
                if secondary is None:
                    await message.reply("❌ The second color is invalid.", mention_author=False)
                    return True
            operation = _change_color
            args = (primary, secondary)

    elif action == "random":
        operation = _randomize_color
        args = ()
    elif action == "rename":
        name = " ".join(parts[action_index + 1:]).strip()
        if not name:
            await message.reply(
                "Usage: ,custom role rename <new name>",
                mention_author=False,
            )
            return True
        operation = _rename_role
        args = (name,)
    elif action == "icon":
        url_parts = parts[action_index + 1:]
        if len(url_parts) != 1:
            await message.reply(
                "Usage: ,custom role icon <direct-https-image-url>",
                mention_author=False,
            )
            return True
        operation = _set_icon
        args = (url_parts[0],)
    elif action == "remove":
        operation = _remove_role
        args = ()
    else:
        await message.reply(
            "Custom role commands: ,custom role create, color, random, rename, icon, and remove.",
            mention_author=False,
        )
        return True

    try:
        response = await operation(message.guild, message.author, *args)
    except CustomRoleError as error:
        response = str(error)
    except discord.Forbidden:
        response = (
            "❌ Discord denied that role action. Check that I have Manage Roles "
            "and that my highest role is above your custom role."
        )
    except discord.HTTPException as error:
        response = f"❌ Discord rejected the custom role change: {error}"
    except Exception as error:
        response = f"❌ The custom role action failed: {type(error).__name__}: {error}"

    await message.reply(response, mention_author=False)
    return True
