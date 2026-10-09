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
# New custom roles are placed immediately above this existing server role.
CUSTOM_ROLE_ANCHOR_ROLE_ID = 1354259084114661509
# Never let custom-role commands touch staff roles, even if the persisted
# registry is stale or corrupted. Keep this list in sync with the server's
# staff hierarchy.
PROTECTED_STAFF_ROLE_IDS = {
    1306082718060384399,  # Additional Staff Manager+-authorized role
    1518416402141417472,  # Co-owner
    1397677852056354948,  # Director
    1371738883401711656,  # Staff manager
    1371739870380425236,  # Head admin
    1371738346900029510,  # Admin
    1371739816227504160,  # Head mod
    1371738357897494589,  # Mod
    1371738371441037343,  # Trial mod
}
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


# Common color names accepted by prefix and slash commands.
NAMED_COLOR_HEX = {
    "black": "#000000", "white": "#FFFFFF", "red": "#FF0000",
    "green": "#008000", "lime": "#00FF00", "lime green": "#32CD32",
    "blue": "#0000FF", "navy": "#000080", "navy blue": "#000080",
    "dark blue": "#00008B", "light blue": "#ADD8E6", "sky blue": "#87CEEB",
    "royal blue": "#4169E1", "steel blue": "#4682B4",
    "cornflower blue": "#6495ED", "midnight blue": "#191970",
    "yellow": "#FFFF00", "gold": "#FFD700", "goldenrod": "#DAA520",
    "orange": "#FFA500", "dark orange": "#FF8C00", "coral": "#FF7F50",
    "tomato": "#FF6347", "pink": "#FFC0CB", "light pink": "#FFB6C1",
    "hot pink": "#FF69B4", "deep pink": "#FF1493", "purple": "#800080",
    "dark purple": "#301934", "violet": "#EE82EE", "indigo": "#4B0082",
    "lavender": "#E6E6FA", "plum": "#DDA0DD", "orchid": "#DA70D6",
    "magenta": "#FF00FF", "fuchsia": "#FF00FF", "cyan": "#00FFFF",
    "aqua": "#00FFFF", "teal": "#008080", "turquoise": "#40E0D0",
    "dark cyan": "#008B8B", "olive": "#808000", "olive green": "#808000",
    "dark green": "#006400", "forest green": "#228B22", "sea green": "#2E8B57",
    "light green": "#90EE90", "spring green": "#00FF7F",
    "mint green": "#98FF98", "emerald": "#50C878", "brown": "#A52A2A",
    "chocolate": "#D2691E", "tan": "#D2B48C", "beige": "#F5F5DC",
    "khaki": "#F0E68C", "peach": "#FFE5B4", "rose": "#FF007F",
    "crimson": "#DC143C", "maroon": "#800000", "dark red": "#8B0000",
    "firebrick": "#B22222", "salmon": "#FA8072", "silver": "#C0C0C0",
    "grey": "#808080", "gray": "#808080", "dark grey": "#A9A9A9",
    "dark gray": "#A9A9A9", "light grey": "#D3D3D3", "light gray": "#D3D3D3",
    "slate grey": "#708090", "slate gray": "#708090", "charcoal": "#36454F",
    "periwinkle": "#CCCCFF", "blurple": "#5865F2", "brand green": "#57F287",
}


def _normalize_color_name(value: str) -> str:
    return " ".join(value.strip().lower().replace("-", " ").replace("_", " ").split())


def _parse_color(value: str | None) -> discord.Colour | None:
    if not value:
        return None

    candidate = value.strip()
    normalized = _normalize_color_name(candidate)
    bare_hex = candidate.removeprefix("#").removeprefix("0x").removeprefix("0X")
    if re.fullmatch(r"[0-9a-fA-F]{6}", bare_hex):
        return discord.Colour(int(bare_hex, 16))
    if normalized in NAMED_COLOR_HEX:
        return discord.Colour.from_str(NAMED_COLOR_HEX[normalized])

    factory = getattr(discord.Colour, normalized.replace(" ", "_"), None)
    if callable(factory):
        try:
            return factory()
        except (TypeError, ValueError):
            pass

    try:
        return discord.Colour.from_str(candidate)
    except (TypeError, ValueError):
        return None


def _parse_color_prefix(
    parts: list[str],
    start_index: int,
    *,
    max_words: int = 3,
) -> tuple[discord.Colour | None, int]:
    """Parse a color from the longest recognized prefix of command arguments."""
    available = min(max_words, len(parts) - start_index)
    for word_count in range(available, 0, -1):
        candidate = " ".join(parts[start_index:start_index + word_count])
        parsed = _parse_color(candidate)
        if parsed is not None:
            return parsed, word_count
    return None, 0


def _is_explicit_color_token(value: str) -> bool:
    return _parse_color(value) is not None


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


async def _find_anchor_role(guild: discord.Guild) -> discord.Role | None:
    anchor_role = guild.get_role(CUSTOM_ROLE_ANCHOR_ROLE_ID)
    if anchor_role is not None:
        return anchor_role

    try:
        fetched_roles = await guild.fetch_roles()
    except (discord.Forbidden, discord.HTTPException):
        return None
    return next(
        (candidate for candidate in fetched_roles if candidate.id == CUSTOM_ROLE_ANCHOR_ROLE_ID),
        None,
    )


async def _place_role_above_anchor(
    guild: discord.Guild,
    role: discord.Role,
    *,
    required: bool = False,
    reason: str = "Keeping a custom role above its reference role",
) -> discord.Role:
    """Position a custom role directly above the reference role and verify it."""
    try:
        fetched_roles = await guild.fetch_roles()
    except (discord.Forbidden, discord.HTTPException) as error:
        if required:
            raise CustomRoleError(
                "❌ I couldn't check the server's role order. Please try again."
            ) from error
        return role

    anchor_role = next(
        (candidate for candidate in fetched_roles if candidate.id == CUSTOM_ROLE_ANCHOR_ROLE_ID),
        None,
    )
    fresh_role = next((candidate for candidate in fetched_roles if candidate.id == role.id), None)

    if anchor_role is None:
        if required:
            raise CustomRoleError(
                f"❌ I couldn't find the required reference role ({CUSTOM_ROLE_ANCHOR_ROLE_ID})."
            )
        return fresh_role or role

    if fresh_role is not None and fresh_role > anchor_role:
        return fresh_role

    bot_member = await _bot_member_with_role_permission(guild)
    if bot_member.top_role <= anchor_role:
        raise CustomRoleError(
            f"❌ Move my highest role above <@&{CUSTOM_ROLE_ANCHOR_ROLE_ID}> "
            "so I can place custom roles above it."
        )

    # Use a relative move rather than a numeric position, which can be stale
    # while Discord is still assigning the newly-created role's position.
    for _attempt in range(2):
        moving_role = guild.get_role(role.id) or fresh_role or role
        current_anchor = guild.get_role(CUSTOM_ROLE_ANCHOR_ROLE_ID) or anchor_role
        try:
            await moving_role.move(
                below=current_anchor,
                reason=reason,
            )
        except (discord.Forbidden, discord.HTTPException, TypeError, ValueError) as error:
            raise CustomRoleError(
                "❌ Discord wouldn't move the custom role above the reference role. "
                "Check my Manage Roles permission and role hierarchy."
            ) from error

        try:
            fetched_roles = await guild.fetch_roles()
        except (discord.Forbidden, discord.HTTPException) as error:
            raise CustomRoleError(
                "❌ I moved the role but couldn't verify its position. Please try again."
            ) from error

        anchor_role = next(
            (candidate for candidate in fetched_roles if candidate.id == CUSTOM_ROLE_ANCHOR_ROLE_ID),
            None,
        )
        fresh_role = next((candidate for candidate in fetched_roles if candidate.id == role.id), None)
        if anchor_role is None or fresh_role is None:
            if required:
                raise CustomRoleError("❌ I couldn't verify the custom role's new position.")
            return fresh_role or role
        if fresh_role > anchor_role:
            return fresh_role

    raise CustomRoleError(
        f"❌ Discord did not place the role above <@&{CUSTOM_ROLE_ANCHOR_ROLE_ID}>. "
        "Please check my role hierarchy."
    )


async def _validate_custom_role_safety(
    guild: discord.Guild,
    role: discord.Role,
    *,
    repair_permissions: bool = True,
) -> discord.Role:
    """Fail closed if a registered custom role could grant elevated access."""
    if role.id in PROTECTED_STAFF_ROLE_IDS or role.id == CUSTOM_ROLE_ANCHOR_ROLE_ID:
        raise CustomRoleError("❌ Security check blocked this action: that is a protected server role.")

    # Custom roles must never carry permissions. Repair unexpected permissions
    # to prevent Administrator or other elevated permissions from persisting.
    if role.permissions.value != 0:
        if not repair_permissions:
            raise CustomRoleError("❌ Security check blocked this action because the role has permissions.")
        try:
            role = await role.edit(
                permissions=discord.Permissions.none(),
                reason="Security safeguard: remove permissions from a custom role",
            )
        except (discord.Forbidden, discord.HTTPException) as error:
            raise CustomRoleError(
                "❌ Security check blocked this action because I could not remove permissions from the custom role."
            ) from error
        if role.permissions.value != 0:
            raise CustomRoleError("❌ Security check blocked this action because the role still has permissions.")

    protected_roles = [
        guild.get_role(role_id)
        for role_id in PROTECTED_STAFF_ROLE_IDS
    ]
    for protected in protected_roles:
        if protected is not None and role.position >= protected.position:
            raise CustomRoleError(
                "❌ Security check blocked this action: custom roles must stay below all staff roles. "
                "Please move the reference role lower than the staff hierarchy."
            )
    return role


async def _owned_role(
    guild: discord.Guild,
    member: discord.Member,
    *,
    ensure_position: bool = False,
) -> discord.Role | None:
    await _ensure_registry_loaded()
    role_id = _role_registry.get(member.id)
    if role_id is None:
        return None

    role = guild.get_role(role_id)
    if role is not None:
        # Never let a bad registry entry turn a staff role into a "custom" role.
        if role.id in PROTECTED_STAFF_ROLE_IDS or role.id == CUSTOM_ROLE_ANCHOR_ROLE_ID:
            raise CustomRoleError(
                "❌ Security check blocked this action because your registry points to a protected server role."
            )
        role = await _validate_custom_role_safety(guild, role)
        if ensure_position:
            role = await _place_role_above_anchor(
                guild,
                role,
                required=False,
                reason=f"Correcting custom role position for {member} ({member.id})",
            )
            role = await _validate_custom_role_safety(guild, role)
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
    name: str | None,
    primary: discord.Colour | None,
    secondary: discord.Colour | None,
) -> str:
    # Bare create commands use the account's Discord username by default.
    role_name = _validate_name(name or member.name)
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

        anchor_role = await _find_anchor_role(guild)
        if anchor_role is None:
            raise CustomRoleError(
                f"❌ I couldn't find the required reference role "
                f"({CUSTOM_ROLE_ANCHOR_ROLE_ID}) in this server."
            )

        if bot_member.top_role <= anchor_role:
            raise CustomRoleError(
                f"❌ Move my highest role above <@&{CUSTOM_ROLE_ANCHOR_ROLE_ID}> "
                "so I can place custom roles immediately above it."
            )

        role = await guild.create_role(
            name=role_name,
            permissions=discord.Permissions.none(),
            colour=primary or discord.Colour.default(),
            secondary_colour=secondary,
            hoist=False,
            mentionable=False,
            reason=f"Custom role created for {member} ({member.id})",
        )

        try:
            # Keep every newly-created custom role immediately above the
            # configured reference role, not near the bot's highest role.
            role = await _place_role_above_anchor(
                guild,
                role,
                required=True,
                reason=f"Positioning custom role above reference role {CUSTOM_ROLE_ANCHOR_ROLE_ID}",
            )
            role = await _validate_custom_role_safety(guild, role, repair_permissions=False)
            await member.add_roles(
                role,
                reason="Assigning the member's custom role",
            )
        except (CustomRoleError, discord.Forbidden, discord.HTTPException) as error:
            try:
                await role.delete(reason="Rolling back a custom role that could not be assigned")
            except (discord.Forbidden, discord.HTTPException):
                pass
            if isinstance(error, CustomRoleError):
                raise
            raise CustomRoleError(
                "❌ I couldn't finish setting up or assigning the role. Check the bot's role hierarchy "
                "and Manage Roles permission."
            ) from error

        _role_registry[member.id] = role.id
        saved = await _persist_registry()
        if primary is None:
            response = f"🎨 {member.mention}: Cool, you were assigned a custom role: {role.mention}."
        else:
            response = (
                f"🎨 {member.mention}: Cool, you were assigned a custom role "
                f"with hex code **{primary}**: {role.mention}."
            )
        if secondary is not None:
            response += f" The second color is **{secondary}**."
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
        role = await _owned_role(guild, member, ensure_position=True)
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
        response = (
            f"🎨 {member.mention}: Your custom role color was changed to **{primary}**"
        )
        if secondary is not None:
            response += f" and **{secondary}**."
        else:
            response += "."
        return response


async def _randomize_color(
    guild: discord.Guild,
    member: discord.Member,
) -> str:
    async with _role_mutation_lock:
        role = await _owned_role(guild, member, ensure_position=True)
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
        role = await _owned_role(guild, member, ensure_position=True)
        if role is None:
            raise CustomRoleError("❌ You don't have a custom role yet. Use custom role create first.")
        bot_member = await _bot_member_with_role_permission(guild)
        if role.managed or role.is_default() or bot_member.top_role <= role:
            raise CustomRoleError("❌ I cannot rename your role because it is above my highest role.")
        await role.edit(
            name=role_name,
            reason=f"Custom role renamed by {member} ({member.id})",
        )
        return f"✅ {member.mention}: Your custom role name was successfully changed to **{role_name}**."


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
        role = await _owned_role(guild, member, ensure_position=True)
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
    embed = _response_embed(message)
    if followup or interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=False)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=False)


def _response_embed(message: str) -> discord.Embed:
    """Format custom-role command replies as embeds without a title."""
    lowered = message.lower()
    if message.startswith(("❌", "⛔")) or "failed" in lowered or "error" in lowered:
        color = discord.Colour.red()
    elif "removed" in lowered:
        color = discord.Colour.dark_grey()
    else:
        color = discord.Colour.from_rgb(212, 179, 195)
    return discord.Embed(description=message, colour=color)


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
    name="Optional role name; defaults to your Discord username",
    color="Optional: color name or hex (with or without #); leave blank for no color",
    second_color="Optional named or hex second color for a gradient (requires Enhanced Role Styles)",
)
async def custom_role_create(
    interaction: discord.Interaction,
    name: str | None = None,
    color: str | None = None,
    second_color: str | None = None,
):
    primary = _parse_color(color) if color else None
    secondary = _parse_color(second_color) if second_color else None
    if (color and primary is None) or (second_color and secondary is None):
        await _send_interaction_response(
            interaction,
            "❌ Invalid color. Use a hex color like d4b3c3 or #d4b3c3, or a supported color name.",
        )
        return
    if secondary is not None and primary is None:
        await _send_interaction_response(
            interaction,
            "❌ Choose a primary color before adding a second gradient color.",
        )
        return
    await _run_slash(interaction, _create_role, name, primary, secondary)


@custom_role_group.command(name="color", description="Change your custom role's color.")
@app_commands.describe(
    color="Color name such as red or dark blue, or hex such as #FF00FF",
    second_color="Optional named or hex second color for a gradient",
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
            "❌ Invalid color. Use a hex color such as d4b3c3 or #d4b3c3, or a supported color name.",
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
        if content.lower().startswith((",custom role", ",customrole", ",cr")):
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
    elif command in {",customrole", ",cr"}:
        action_index = 1
    else:
        return False

    action = parts[action_index].lower() if len(parts) > action_index else ""
    auth_error = _authorization_error(message.guild, message.author)
    if auth_error:
        await message.reply(auth_error, mention_author=False)
        return True

    if action in {"create", "color"}:
        color_start = action_index + 1
        primary, primary_words = _parse_color_prefix(parts, color_start)
        rest = parts[color_start + primary_words:] if primary is not None else parts[color_start:]

        if action == "create":
            secondary = None
            # Optional colors come first when recognized; any remaining words
            # become a custom role name. With no name, use the author's username.
            if primary is not None and len(rest) >= 2:
                possible_secondary, secondary_words = _parse_color_prefix(rest, 0)
                if possible_secondary is not None and len(rest) > secondary_words:
                    secondary = possible_secondary
                    rest = rest[secondary_words:]

            # If no custom name was provided, use the command author's username.
            name = " ".join(rest).strip() or message.author.name
            if secondary is not None and primary is None:
                await message.reply(
                    "❌ Choose a primary color before adding a second gradient color.",
                    mention_author=False,
                )
                return True
            operation = _create_role
            args = (name, primary, secondary)
        else:
            if primary is None:
                await message.reply(
                    "❌ Please provide a color. Use d4b3c3, #d4b3c3, red, or dark blue.",
                    mention_author=False,
                )
                return True
            secondary = None
            if rest:
                secondary, secondary_words = _parse_color_prefix(rest, 0)
                if secondary is None or secondary_words != len(rest):
                    await message.reply(
                        "Usage: ,cr color <color> [second-color] (hex values may be entered with or without #)",
                        mention_author=False,
                    )
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
            "Custom role commands: ,cr create, color, random, rename, icon, and remove. Colors accept names (red, blue, dark blue) or hex (#FF00FF). Alias for ,custom role.",
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

    await message.reply(embed=_response_embed(response), mention_author=False)
    return True
