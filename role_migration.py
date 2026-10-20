"""Admin-only role appearance migration from the two legacy servers.

Command: ,migrateappearance [preview|apply] [main|second|both]
Copies colors and role icons only. It never edits permissions, hierarchy, or members.
"""
from __future__ import annotations

import inspect
from collections import defaultdict
import discord

SOURCE_GUILDS = {
    "main": 551166126596554775,
    "second": 985391650861879337,
}
SOURCE_LABELS = {"main": "old main server", "second": "second server"}
COMMAND_NAME = ",migrateappearance"


def _name(value: str) -> str:
    return value.casefold().strip()


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
        try:
            roles_by_key[key] = await source_guild.fetch_roles()
        except discord.DiscordException as error:
            raise RuntimeError(
                f"Couldn't read roles from the {SOURCE_LABELS[key]} ({type(error).__name__})."
            ) from error

    try:
        destination_roles = await destination.fetch_roles()
    except discord.DiscordException as error:
        raise RuntimeError(
            f"Couldn't read destination roles ({type(error).__name__})."
        ) from error

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
    matches, missing, ambiguous_target, too_high = [], [], [], []
    for name, (source_role, source_key) in chosen.items():
        targets = target_groups.get(name, [])
        if not targets:
            missing.append(source_role.name)
        elif len(targets) != 1:
            ambiguous_target.append(source_role.name)
        elif targets[0].position >= bot_member.top_role.position:
            too_high.append(targets[0].name)
        else:
            matches.append((targets[0], source_role, source_key))

    return {
        "matches": matches,
        "missing": sorted(missing, key=str.casefold),
        "ambiguous_source": sorted(ambiguous_source),
        "ambiguous_target": sorted(ambiguous_target, key=str.casefold),
        "too_high": sorted(too_high, key=str.casefold),
        "icons": "ROLE_ICONS" in destination.features,
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
        f"Sources: {sources}\n"
        f"Roles ready: {len(matches)}\n"
        f"Source roles missing in destination: {len(plan['missing'])}\n"
        f"Ambiguous source names: {len(plan['ambiguous_source'])}\n"
        f"Ambiguous destination names: {len(plan['ambiguous_target'])}\n"
        f"Roles at/above the bot's top role: {len(plan['too_high'])}\n"
        + ("Role icons are supported in this destination." if plan["icons"]
           else "Destination lacks Role Icons support; colors can still be copied.")
        + "\nOnly appearance is changed; permissions and hierarchy stay untouched."
    )
    result += _listed("Missing destination roles", plan["missing"])
    result += _listed("Duplicate source names", plan["ambiguous_source"])
    result += _listed("Duplicate destination names", plan["ambiguous_target"])
    result += _listed("Roles above bot", plan["too_high"])
    if matches:
        examples = [f"{source.name} ({SOURCE_LABELS[key]})" for _, source, key in matches[:7]]
        result += "\nExamples: " + "; ".join(examples)
        if len(matches) > 7:
            result += f"; and {len(matches) - 7} more"
    result += "\nTo apply, run ,migrateappearance apply both (or main/second)."
    return result[:1850]


async def _copy_appearance(target, source, icon_support):
    kwargs = {
        "color": source.color,
        "reason": f"Role appearance migration from source role {source.id}",
    }
    params = inspect.signature(target.edit).parameters

    # Copy enhanced colors when the installed discord.py version supports them.
    secondary = getattr(source, "secondary_color", None)
    tertiary = getattr(source, "tertiary_color", None)
    if secondary is not None:
        if "secondary_colour" in params:
            kwargs["secondary_colour"] = secondary
        elif "secondary_color" in params:
            kwargs["secondary_color"] = secondary
    if tertiary is not None:
        if "tertiary_colour" in params:
            kwargs["tertiary_colour"] = tertiary
        elif "tertiary_color" in params:
            kwargs["tertiary_color"] = tertiary

    icon_field_applied = False
    if icon_support and "display_icon" in params:
        icon = getattr(source, "display_icon", None)
        if icon is None or isinstance(icon, str):
            kwargs["display_icon"] = icon
        else:
            kwargs["display_icon"] = await icon.read()
        icon_field_applied = True

    await target.edit(**kwargs)
    return icon_field_applied


async def handle_prefix(message: discord.Message, client: discord.Client) -> bool:
    content = (message.content or "").strip()
    parts = content.split()
    if not parts or parts[0].casefold() != COMMAND_NAME:
        return False

    guild = message.guild
    if guild is None:
        return True
    if guild.id in SOURCE_GUILDS.values():
        await message.reply(
            "Run this in the new destination server, not either old server.",
            mention_author=False, allowed_mentions=discord.AllowedMentions.none(),
        )
        return True
    if not (guild.owner_id == message.author.id or
            getattr(message.author.guild_permissions, "administrator", False)):
        await message.reply(
            "Only the server owner or an administrator can run this migration.",
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

    keys = ["main", "second"] if selection == "both" else [selection]
    try:
        plan = await _plan(guild, client, keys)
    except RuntimeError as error:
        await message.reply(
            f"Migration stopped: {error}",
            mention_author=False, allowed_mentions=discord.AllowedMentions.none(),
        )
        return True

    if mode == "preview":
        await message.reply(
            _preview(guild, plan), mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True

    updated, icon_fields, failures = 0, 0, []
    for target, source, _ in plan["matches"]:
        try:
            icon_applied = await _copy_appearance(target, source, plan["icons"])
            updated += 1
            icon_fields += int(icon_applied)
        except Exception as error:
            failures.append(f"{target.name} ({type(error).__name__})")

    report = (
        f"Role appearance migration finished — {guild.name}\n"
        f"Updated roles: {updated}/{len(plan['matches'])}\n"
        f"Icon fields applied: {icon_fields}\n"
        f"Failed edits: {len(failures)}\n"
        f"Missing destination roles: {len(plan['missing'])}\n"
        "Permissions, hierarchy, member assignments, and source servers were not changed."
    )
    report += _listed("Failed edits", failures)
    report += _listed("Missing destination roles", plan["missing"])
    await message.reply(
        report[:1850], mention_author=False,
        allowed_mentions=discord.AllowedMentions.none(),
    )
    return True
