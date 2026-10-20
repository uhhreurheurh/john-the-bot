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
# Create missing roles in small batches to reduce the chance of server anti-nuke
# systems treating a migration as a sudden role-spam event.
CREATE_BATCH_SIZE = 5
# Track roles created during this live migration process. Their appearance was
# copied during creation, so later batch runs should not edit them again.
_CREATED_ROLE_IDS: set[int] = set()


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
    matches, missing, ambiguous_target, too_high = [], [], [], []
    for name, (source_role, source_key) in chosen.items():
        targets = target_groups.get(name, [])
        if not targets:
            missing.append((source_role, source_key))
        elif len(targets) != 1:
            ambiguous_target.append(source_role.name)
        elif targets[0].position >= bot_member.top_role.position:
            too_high.append(targets[0].name)
        else:
            matches.append((targets[0], source_role, source_key))

    missing.sort(key=lambda item: item[0].name.casefold())
    return {
        "matches": matches,
        "missing": missing,
        "ambiguous_source": sorted(ambiguous_source),
        "ambiguous_target": sorted(ambiguous_target, key=str.casefold),
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
    matches = [
        (target, source, key)
        for target, source, key in plan["matches"]
        if target.id not in _CREATED_ROLE_IDS
    ]
    previously_created = len(plan["matches"]) - len(matches)
    sources = " + ".join(SOURCE_LABELS[key] for key in plan["keys"])
    result = (
        f"Role appearance migration preview — {guild.name}\n"
        f"Sources: {sources}\n"
        f"Existing roles ready to update: {len(matches)}\n"
        f"Roles already created by this migration: {previously_created}\n"
        f"Roles to create: {len(plan['missing'])}\n"
        f"Creation batch size: up to {CREATE_BATCH_SIZE} per apply\n"
        f"Ambiguous source names: {len(plan['ambiguous_source'])}\n"
        f"Ambiguous destination names: {len(plan['ambiguous_target'])}\n"
        f"Roles at/above the bot's top role: {len(plan['too_high'])}\n"
        + ("Role icons are supported in this destination." if plan["icons"]
           else "Destination lacks Role Icons support; colors can still be copied.")
        + ("\nEnhanced gradient/holographic colors are supported." if plan["enhanced_colors"]
           else "\nEnhanced gradient/holographic colors are not supported here; primary colors will be copied.")
        + "\nOnly appearance is changed; permissions and hierarchy stay untouched."
    )
    result += _listed(
        "Roles that will be created",
        [source.name for source, _ in plan["missing"]],
    )
    result += _listed("Duplicate source names", plan["ambiguous_source"])
    result += _listed("Duplicate destination names", plan["ambiguous_target"])
    result += _listed("Roles above bot", plan["too_high"])
    if matches:
        examples = [f"{source.name} ({SOURCE_LABELS[key]})" for _, source, key in matches[:7]]
        result += "\nExamples: " + "; ".join(examples)
        if len(matches) > 7:
            result += f"; and {len(matches) - 7} more"
    result += (
        "\nApply in small batches with ,migrateappearance apply both. "
        "Repeat until all missing roles are created; run it once more to update existing roles."
    )
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
        if icon is None or isinstance(icon, str):
            kwargs["display_icon"] = icon
        else:
            kwargs["display_icon"] = await icon.read()
        icon_field_applied = True

    await target.edit(**kwargs)
    return icon_field_applied


async def _create_role_with_appearance(guild, source, source_key, plan):
    """Create a permissionless role and copy supported appearance at creation."""
    params = inspect.signature(guild.create_role).parameters
    kwargs = {
        "name": source.name,
        "permissions": discord.Permissions.none(),
        "color": source.color,
        "hoist": False,
        "mentionable": False,
        "reason": (
            f"Safe role appearance migration from "
            f"{SOURCE_LABELS[source_key]} role {source.id}"
        ),
    }
    fallback_edit_needed = False
    source_icon = getattr(source, "display_icon", None)
    icon_applied = False

    if plan["icons"] and source_icon is not None:
        if "display_icon" in params:
            kwargs["display_icon"] = (
                source_icon if isinstance(source_icon, str)
                else await source_icon.read()
            )
            icon_applied = True
        else:
            fallback_edit_needed = True

    if plan["enhanced_colors"]:
        for attr, names in (
            ("secondary_color", ("secondary_color", "secondary_colour")),
            ("tertiary_color", ("tertiary_color", "tertiary_colour")),
        ):
            value = getattr(source, attr, None)
            if value is None:
                continue
            accepted = next((name for name in names if name in params), None)
            if accepted is None:
                fallback_edit_needed = True
            else:
                kwargs[accepted] = value

    target = await guild.create_role(**kwargs)

    # Compatibility fallback for an older discord.py release whose create_role
    # doesn't expose one of the appearance fields.
    if fallback_edit_needed:
        fallback_icon_applied = await _copy_appearance(
            target, source, plan["icons"], plan["enhanced_colors"]
        )
        icon_applied = icon_applied or fallback_icon_applied

    return target, icon_applied


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

    updated, created, icon_fields, failures = 0, 0, 0, []
    # If roles are still missing, create only a small batch this run. Their
    # permissions are explicitly empty, and we do not assign them to members
    # or reposition them. Existing role edits wait until creation is complete.
    if plan["missing"]:
        batch = plan["missing"][:CREATE_BATCH_SIZE]
        for source, source_key in batch:
            try:
                target, icon_applied = await _create_role_with_appearance(
                    guild, source, source_key, plan
                )
                created += 1
                _CREATED_ROLE_IDS.add(target.id)
                icon_fields += int(icon_applied)
            except Exception as error:
                failures.append(f"{source.name} create/appearance ({type(error).__name__})")

        remaining = max(0, len(plan["missing"]) - created)
        report = (
            f"Role migration batch finished — {guild.name}\n"
            f"Created roles this run: {created}/{len(batch)}\n"
            f"Icon fields applied: {icon_fields}\n"
            f"Failed operations: {len(failures)}\n"
            f"Roles still to create: {remaining}\n"
            "New roles have no permissions and have not been assigned to anyone. "
            "Existing role appearance has not been edited in this batch."
        )
        if remaining:
            report += "\nRun ,migrateappearance apply both again to continue with the next batch."
        else:
            report += "\nAll missing roles were created with their supported appearance. "
            report += "Run apply again only if you also need to update pre-existing matching roles."
    else:
        existing_matches = [
            (target, source, source_key)
            for target, source, source_key in plan["matches"]
            if target.id not in _CREATED_ROLE_IDS
        ]
        skipped_created = len(plan["matches"]) - len(existing_matches)
        for target, source, _ in existing_matches:
            try:
                icon_applied = await _copy_appearance(
                    target, source, plan["icons"], plan["enhanced_colors"]
                )
                updated += 1
                icon_fields += int(icon_applied)
            except Exception as error:
                failures.append(f"{target.name} ({type(error).__name__})")

        report = (
            f"Role appearance migration finished — {guild.name}\n"
            f"Updated existing roles: {updated}/{len(existing_matches)}\n"
            f"Previously-created roles skipped: {skipped_created}\n"
            f"Icon fields applied: {icon_fields}\n"
            f"Failed edits: {len(failures)}\n"
            "Permissions, hierarchy, member assignments, and source servers were not changed."
        )

    report += _listed("Failed operations", failures)
    await message.reply(
        report[:1850], mention_author=False,
        allowed_mentions=discord.AllowedMentions.none(),
    )
    return True
