"""Admin-only role appearance migration from the two legacy servers.

Command: ,migrateappearance [preview|apply] [main|second|both]
Copies colors and role icons only. It never edits permissions, hierarchy, or members.
"""
from __future__ import annotations

import inspect
import unicodedata
from collections import defaultdict
import discord

SOURCE_GUILDS = {
    "main": 551166126596554775,
    "second": 985391650861879337,
}
SOURCE_LABELS = {"main": "old main server", "second": "second server"}
COMMAND_NAME = ",migrateappearance"
# This command only edits roles that already exist in the destination server.


def _name(value: str) -> str:
    return value.casefold().strip()


def _normalized_name(value: str) -> str:
    """Normalize formatting and emoji wrappers for safe fallback name matching."""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(char for char in normalized if char.isalnum())


def _groups(roles):
    result = defaultdict(list)
    for role in roles:
        if not role.is_default() and not role.managed:
            result[_name(role.name)].append(role)
    return result


def _choose_sources(roles_by_key, keys):
    chosen, ambiguous = {}, set()
    for key in keys:
        for name, roles in _groups(roles_by_key[key]).items():
            if name in chosen or name in ambiguous:
                continue
            if len(roles) != 1:
                ambiguous.add(name)
            else:
                chosen[name] = (roles[0], key)
    return chosen, ambiguous


async def _plan(destination, client, keys):
    roles_by_key = {}
    for key in keys:
        source_guild = client.get_guild(SOURCE_GUILDS[key])
        if source_guild is None:
            raise RuntimeError(
                f"I can't access the {SOURCE_LABELS[key]}. The bot must be installed there."
            )
        # Guild role data is kept current in discord.py's gateway cache.
        # Reading cached roles avoids requiring Manage Roles on source servers.
        roles_by_key[key] = list(source_guild.roles)

    # Cached destination roles are sufficient for matching by name.
    destination_roles = list(destination.roles)

    bot_member = destination.me
    if bot_member is None and client.user is not None:
        try:
            bot_member = await destination.fetch_member(client.user.id)
        except discord.DiscordException:
            bot_member = None
    if bot_member is None:
        raise RuntimeError("Couldn't verify the bot's role position in this server.")
    if not bot_member.guild_permissions.manage_roles:
        raise RuntimeError("I need Manage Roles permission in the new server.")

    chosen, ambiguous_source = _choose_sources(roles_by_key, keys)
    target_groups = _groups(destination_roles)
    normalized_targets = defaultdict(list)
    for role in destination_roles:
        if not role.is_default() and not role.managed:
            normalized = _normalized_name(role.name)
            if normalized:
                normalized_targets[normalized].append(role)

    normalized_source_counts = defaultdict(int)
    for source_name in chosen:
        normalized = _normalized_name(source_name)
        if normalized:
            normalized_source_counts[normalized] += 1

    matches, missing, ambiguous_target, too_high = [], [], [], []
    matched_target_ids = set()
    for name, (source_role, source_key) in chosen.items():
        targets = target_groups.get(name, [])
        used_normalized_fallback = False

        # First prefer an exact, case-insensitive name match. If punctuation,
        # spacing, or decorative emoji wrappers differ, fall back only when the
        # normalized source and destination names are both unique.
        if not targets:
            normalized = _normalized_name(source_role.name)
            fallback_targets = normalized_targets.get(normalized, []) if normalized else []
            if normalized_source_counts.get(normalized, 0) != 1 or len(fallback_targets) != 1:
                if fallback_targets:
                    ambiguous_target.append(source_role.name)
                    continue
            else:
                targets = fallback_targets
                used_normalized_fallback = bool(targets)

        if not targets:
            missing.append(source_role.name)
        elif len(targets) != 1:
            ambiguous_target.append(source_role.name)
        elif targets[0].id in matched_target_ids:
            ambiguous_target.append(source_role.name)
        elif targets[0].position >= bot_member.top_role.position:
            too_high.append(targets[0].name)
        else:
            matches.append((targets[0], source_role, source_key))
            matched_target_ids.add(targets[0].id)

    missing.sort(key=str.casefold)
    return {
        "matches": matches,
        "missing": missing,
        "ambiguous_source": sorted(ambiguous_source),
        "ambiguous_target": sorted(set(ambiguous_target), key=str.casefold),
        "too_high": sorted(too_high, key=str.casefold),
        "icons": "ROLE_ICONS" in destination.features,
        "enhanced_colors": "ENHANCED_ROLE_COLORS" in destination.features,
        "keys": keys,
    }


def _listed(label, names, limit=7):
    if not names:
        return ""
    shown = ", ".join(repr(name[:65]) for name in names[:limit])
    if len(names) > limit:
        shown += f", and {len(names) - limit} more"
    return f"\n{label}: {shown}"


def _preview(guild, plan):
    matches = plan["matches"]
    sources = " + ".join(SOURCE_LABELS[key] for key in plan["keys"])
    result = (
        f"Role appearance migration preview — {guild.name}\n"
        f"Existing roles ready to update: {len(matches)}\n"
        f"Unmatched source roles to skip: {len(plan['missing'])}\n"
        f"Ambiguous source names: {len(plan['ambiguous_source'])}\n"
        f"Ambiguous destination names: {len(plan['ambiguous_target'])}\n"
        f"Roles at/above the bot's top role: {len(plan['too_high'])}\n"
        + ("Role icons are supported in this destination." if plan["icons"]
           else "Destination lacks Role Icons support; colors can still be copied.")
        + ("\nEnhanced gradient/holographic colors are supported." if plan["enhanced_colors"]
           else "\nEnhanced gradient/holographic colors are not supported here; primary colors will be copied.")
        + "\nOnly appearance is changed; permissions and hierarchy stay untouched."
    )
    result += _listed("Unmatched source roles (will be skipped)", plan["missing"])
    result += _listed("Duplicate source names", plan["ambiguous_source"])
    result += _listed("Duplicate destination names", plan["ambiguous_target"])
    result += _listed("Roles above bot", plan["too_high"])
    if matches:
        examples = [f"{source.name} ({SOURCE_LABELS[key]})" for _, source, key in matches[:7]]
        result += "\nExamples: " + "; ".join(examples)
        if len(matches) > 7:
            result += f"; and {len(matches) - 7} more"
    result += "\nRun ,migrateappearance apply both to copy appearance to existing matching roles only."
    return result[:1850]


async def _copy_appearance(target, source, icon_support, enhanced_color_support):
    kwargs = {
        "color": source.color,
        "reason": f"Role appearance migration from source role {source.id}",
    }
    params = inspect.signature(target.edit).parameters

    # Enhanced colors require destination server support. Copy None as well so
    # a simple source color can clear an old gradient/holographic target style.
    if enhanced_color_support:
        secondary = getattr(source, "secondary_color", None)
        tertiary = getattr(source, "tertiary_color", None)
        if "secondary_colour" in params:
            kwargs["secondary_colour"] = secondary
        elif "secondary_color" in params:
            kwargs["secondary_color"] = secondary
        if "tertiary_colour" in params:
            kwargs["tertiary_colour"] = tertiary
        elif "tertiary_color" in params:
            kwargs["tertiary_color"] = tertiary

    icon_field_applied = False
    if icon_support and "display_icon" in params:
        icon = getattr(source, "display_icon", None)
        if icon is None or isinstance(icon, (str, discord.PartialEmoji)):
            kwargs["display_icon"] = icon
        else:
            kwargs["display_icon"] = await icon.read()
        icon_field_applied = True

    await target.edit(**kwargs)
    return icon_field_applied


async def run_migration(
    destination: discord.Guild,
    client: discord.Client,
    mode: str = "preview",
    selection: str = "both",
) -> str:
    """Run preview/apply logic and return a user-visible diagnostic report."""
    if mode not in {"preview", "apply"}:
        raise ValueError("Choose preview or apply.")
    if selection not in {"main", "second", "both"}:
        raise ValueError("Choose main, second, or both.")
    if destination.id in SOURCE_GUILDS.values():
        raise RuntimeError("Run this command in the new destination server, not an old source server.")

    keys = ["main", "second"] if selection == "both" else [selection]
    plan = await _plan(destination, client, keys)

    if mode == "preview":
        return _preview(destination, plan)

    updated, icon_fields, failures = 0, 0, []
    for target, source, source_key in plan["matches"]:
        try:
            icon_applied = await _copy_appearance(
                target, source, plan["icons"], plan["enhanced_colors"]
            )
            updated += 1
            icon_fields += int(icon_applied)
        except Exception as error:
            failures.append(
                f"{target.name} <- {SOURCE_LABELS[source_key]}: "
                f"{type(error).__name__}: {str(error)[:90]}"
            )

    report = (
        f"Role appearance migration finished — {destination.name}\n"
        f"Updated existing roles: {updated}/{len(plan['matches'])}\n"
        f"Unmatched source roles skipped: {len(plan['missing'])}\n"
        f"Ambiguous source names: {len(plan['ambiguous_source'])}\n"
        f"Ambiguous destination roles skipped: {len(plan['ambiguous_target'])}\n"
        f"Roles above the bot skipped: {len(plan['too_high'])}\n"
        f"Icon fields applied: {icon_fields}\n"
        f"Failed edits: {len(failures)}\n"
        "Only existing role appearance was changed. No roles were created; "
        "permissions, hierarchy, member assignments, and source servers were not changed."
    )
    report += _listed("Unmatched source roles", plan["missing"])
    report += _listed("Ambiguous source names", plan["ambiguous_source"])
    report += _listed("Ambiguous destination roles", plan["ambiguous_target"])
    report += _listed("Roles above the bot", plan["too_high"])
    report += _listed("Failed edits", failures)
    return report[:1850]


async def handle_prefix(message: discord.Message, client: discord.Client) -> bool:
    content = (message.content or "").strip()
    parts = content.split()
    if not parts or parts[0].casefold() != COMMAND_NAME:
        return False

    guild = message.guild
    if guild is None:
        await message.reply(
            "❌ This command must be run inside the new destination server.",
            mention_author=False, allowed_mentions=discord.AllowedMentions.none(),
        )
        return True

    if not (guild.owner_id == message.author.id or
            getattr(message.author.guild_permissions, "administrator", False)):
        await message.reply(
            "❌ Only the server owner or an administrator can run this migration.",
            mention_author=False, allowed_mentions=discord.AllowedMentions.none(),
        )
        return True

    args = parts[1:]
    mode = "preview"
    if args and args[0].casefold() in {"preview", "apply"}:
        mode = args.pop(0).casefold()
    selection = args.pop(0).casefold() if args else "both"
    if args or selection not in {"main", "second", "both"}:
        await message.reply(
            "Usage: ,migrateappearance [preview|apply] [main|second|both]\n"
            "Example: ,migrateappearance preview both",
            mention_author=False, allowed_mentions=discord.AllowedMentions.none(),
        )
        return True

    try:
        response = await run_migration(guild, client, mode, selection)
    except Exception as error:
        response = (
            f"❌ Migration could not run: **{type(error).__name__}** — "
            f"{str(error)[:500] or 'No details were provided.'}"
        )

    await message.reply(
        response[:1850], mention_author=False,
        allowed_mentions=discord.AllowedMentions.none(),
    )
    return True
