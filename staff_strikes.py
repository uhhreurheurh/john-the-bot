"""Staff strike feature module."""

from bot import *
from bot import _github_contents_url, _github_request_json

# Strike tiers, counted in currently active (unexpired) strikes:
#   1        no role consequence
#   2        one-rank demotion by default, optionally permanent
#   3+       every staff role removed; reinstatement is a human decision.
# Three is also the cap, because a fully demoted member holds no staff role
# and strike_command refuses members without one.
STAFF_STRIKE_ACTIVATION_THRESHOLD = 2
STAFF_STRIKE_DEMOTION_THRESHOLD = 3
STAFF_MANAGER_OR_HIGHER_ROLE_IDS = {
    1306082718060384399,  # Additional staff-promotion authorization
    1518416402141417472,  # Co Owner
    1397677852056354948,  # Director
    1371738883401711656,  # Staff Manager
}
STAFF_STRIKE_DUPLICATE_WINDOW_SECONDS = 5.0
_recent_staff_strike_submissions: dict[tuple, float] = {}
staff_permanent_demotions: dict[int, dict] = {}

# Ranks that may only be held by one member at a time. Whenever the strike
# system would place someone into one of these and another member already holds
# it, the target falls through to the next rank down instead. Director is the
# rank below Co Owner, so this is what stops a demoted Co Owner from creating a
# second Director.
SINGLE_HOLDER_STAFF_ROLE_IDS = {
    1397677852056354948,  # Director
}

def load_staff_strikes() -> list[dict]:
    """Load strikes and permanent-demotion state, supporting the legacy list format."""
    global staff_permanent_demotions

    if not STAFF_STRIKES_FILE.exists():
        staff_permanent_demotions.clear()
        return []

    try:
        with STAFF_STRIKES_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (json.JSONDecodeError, OSError, TypeError):
        return []

    if isinstance(data, list):
        raw_strikes = data
        raw_permanent_demotions = {}
    elif isinstance(data, dict):
        raw_strikes = data.get("strikes", [])
        raw_permanent_demotions = data.get("permanent_demotions", {})
    else:
        return []

    if not isinstance(raw_strikes, list):
        return []
    if not isinstance(raw_permanent_demotions, dict):
        raw_permanent_demotions = {}

    cleaned_permanent_demotions = {}
    for raw_user_id, item in raw_permanent_demotions.items():
        if not isinstance(item, dict):
            continue
        try:
            user_id = int(item.get("user_id", raw_user_id))
            original_role_id = int(item["original_role_id"])
            demoted_role_id = (
                int(item["demoted_role_id"])
                if item.get("demoted_role_id") is not None
                else None
            )
            issued_by = int(item["issued_by"])
            issued_at = str(item["issued_at"])
            reason = str(item.get("reason", "")).strip()
        except (KeyError, TypeError, ValueError):
            continue
        cleaned_permanent_demotions[user_id] = {
            "user_id": user_id,
            "original_role_id": original_role_id,
            "demoted_role_id": demoted_role_id,
            "issued_by": issued_by,
            "issued_at": issued_at,
            "reason": reason,
        }

    staff_permanent_demotions.clear()
    staff_permanent_demotions.update(cleaned_permanent_demotions)

    cleaned = []
    for item in raw_strikes:
        if not isinstance(item, dict):
            continue
        try:
            user_id = int(item["user_id"])
            strike_number = int(item["strike_number"])
            issued_by = int(item["issued_by"])
            reason = str(item["reason"]).strip()
            issued_at = str(item["issued_at"])
            expires_at = str(item["expires_at"])
            original_staff_role_id = (
                int(item["original_staff_role_id"])
                if item.get("original_staff_role_id") is not None
                else None
            )
        except (KeyError, TypeError, ValueError):
            continue
        if not reason or strike_number < 1:
            continue
        cleaned.append({
            "user_id": user_id,
            "strike_number": strike_number,
            "reason": reason,
            "issued_by": issued_by,
            "issued_at": issued_at,
            "expires_at": expires_at,
            "original_staff_role_id": original_staff_role_id,
            "log_message_id": (int(item["log_message_id"]) if item.get("log_message_id") else None),
        })

    return cleaned


def _save_staff_strikes_local(strikes: list[dict]) -> None:
    """Persist active strikes and permanent-demotion state locally."""
    data = {
        "strikes": strikes,
        "permanent_demotions": {
            str(user_id): record
            for user_id, record in staff_permanent_demotions.items()
        },
    }
    try:
        with STAFF_STRIKES_FILE.open("w", encoding="utf-8") as file:
            json.dump(data, file, indent=2)
            file.write("\n")
    except OSError:
        pass


staff_strikes = load_staff_strikes()


def _staff_strike_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def prune_expired_staff_strikes() -> list[dict]:
    """Remove expired strikes and return the records that expired."""
    global staff_strikes

    now = datetime.now(timezone.utc)
    expired = []
    active = []

    for strike in staff_strikes:
        expires_at = _staff_strike_datetime(strike.get("expires_at", ""))
        if expires_at is not None and expires_at > now:
            active.append(strike)
        else:
            expired.append(strike)

    staff_strikes = active
    return expired


def get_staff_role_for_member(member: discord.Member) -> tuple[str, discord.Role] | None:
    """Return the member's highest configured staff role."""
    role_by_id = {role.id: role for role in member.roles}
    for role_name, role_id in STAFF_ROLE_HIERARCHY:
        role = role_by_id.get(role_id)
        if role is not None:
            return role_name, role
    return None


def get_next_staff_role(role_id: int) -> tuple[str, int] | None:
    """Return the configured rank one level below the supplied role."""
    for index, (_, current_id) in enumerate(STAFF_ROLE_HIERARCHY):
        if current_id != role_id:
            continue
        if index + 1 >= len(STAFF_ROLE_HIERARCHY):
            return None
        return STAFF_ROLE_HIERARCHY[index + 1]
    return None


def get_original_staff_role_id(user_id: int) -> int | None:
    """Get the role the member had before strike consequences were applied."""
    permanent_record = staff_permanent_demotions.get(int(user_id))
    if permanent_record and permanent_record.get("original_role_id"):
        return int(permanent_record["original_role_id"])
    for strike in staff_strikes:
        if int(strike.get("user_id", 0)) != int(user_id):
            continue
        original_role_id = strike.get("original_staff_role_id")
        if original_role_id:
            return int(original_role_id)
    return None


def is_duplicate_staff_strike_submission(
    issuer_id: int,
    user_id: int,
    days: int,
    reason: str,
) -> bool:
    """Ignore rapid repeated submissions with identical strike details."""
    now = asyncio.get_running_loop().time()
    expired_keys = [
        key for key, submitted_at in _recent_staff_strike_submissions.items()
        if now - submitted_at > STAFF_STRIKE_DUPLICATE_WINDOW_SECONDS
    ]
    for key in expired_keys:
        _recent_staff_strike_submissions.pop(key, None)

    key = (
        int(issuer_id),
        int(user_id),
        int(days),
        " ".join(reason.casefold().split()),
    )
    previous = _recent_staff_strike_submissions.get(key)
    if previous is not None and now - previous <= STAFF_STRIKE_DUPLICATE_WINDOW_SECONDS:
        return True
    _recent_staff_strike_submissions[key] = now
    return False


def register_permanent_staff_demotion(
    member: discord.Member,
    *,
    original_role_id: int,
    issued_by: int,
    reason: str,
) -> dict:
    """Persist the demoted rank until Staff Manager or higher promotes the member."""
    next_role_info = get_next_staff_role(original_role_id)
    next_role = (
        member.guild.get_role(next_role_info[1])
        if next_role_info is not None
        else None
    )
    next_name = next_role_info[0] if next_role_info is not None else "Permanently Demoted"
    if next_role is not None:
        next_role, next_name = _fall_through_capped_staff_ranks(
            member, next_role, next_name, None
        )

    record = {
        "user_id": int(member.id),
        "original_role_id": int(original_role_id),
        "demoted_role_id": int(next_role.id) if next_role is not None else None,
        "issued_by": int(issued_by),
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason.strip(),
    }
    staff_permanent_demotions[int(member.id)] = record
    return record


def staff_promotion_command_allowed(member: discord.Member) -> bool:
    return any(role.id in STAFF_MANAGER_OR_HIGHER_ROLE_IDS for role in member.roles)


async def staff_promotion_command_check(interaction: discord.Interaction) -> bool:
    if interaction.guild_id != MAIN_SERVER:
        raise app_commands.CheckFailure(
            "This command can only be used in the main server."
        )
    if not isinstance(interaction.user, discord.Member):
        raise app_commands.CheckFailure("Could not verify your server roles.")
    if not staff_promotion_command_allowed(interaction.user):
        raise app_commands.CheckFailure(
            "Only Staff Manager or higher can approve this promotion."
        )
    return True


async def perform_staff_promotion(
    actor: discord.Member,
    member: discord.Member,
    target_role: discord.Role,
) -> tuple[bool, str]:
    """Apply a Staff Manager+-approved promotion and clear a permanent demotion."""
    if not staff_promotion_command_allowed(actor):
        return False, "❌ Only Staff Manager or higher can promote a permanently demoted member."
    if member.guild.id != MAIN_SERVER or actor.guild.id != MAIN_SERVER:
        return False, "❌ This command can only be used in the main server."
    if target_role.id not in STAFF_ROLE_IDS:
        return False, "❌ Choose a role from the configured staff hierarchy."

    actor_info = get_staff_role_for_member(actor)
    target_index = next(
        (i for i, (_, role_id) in enumerate(STAFF_ROLE_HIERARCHY) if role_id == target_role.id),
        None,
    )
    actor_index = next(
        (i for i, (_, role_id) in enumerate(STAFF_ROLE_HIERARCHY) if actor_info and role_id == actor_info[1].id),
        None,
    )
    if target_index is None or actor_index is None or target_index < actor_index:
        return False, "❌ You cannot promote someone to a rank higher than your own."
    if member.id not in staff_permanent_demotions:
        return False, f"❌ {member.mention} does not have an active permanent-demotion record."

    member_info = get_staff_role_for_member(member)
    desired_role, _ = _fall_through_capped_staff_ranks(
        member,
        target_role,
        target_role.name,
        member_info[1] if member_info else None,
    )
    if desired_role is None or desired_role.id != target_role.id:
        return False, f"❌ **{target_role.name}** is already held by someone else or cannot be assigned safely."

    bot_member = member.guild.me
    if (
        bot_member is None
        or not bot_member.guild_permissions.manage_roles
        or bot_member.top_role <= target_role
        or member.top_role >= bot_member.top_role
    ):
        return False, "❌ I cannot manage that rank. Check the bot's role hierarchy and Manage Roles permission."

    managed_roles = [
        member.guild.get_role(role_id)
        for _, role_id in STAFF_ROLE_HIERARCHY
    ]
    managed_roles = [role for role in managed_roles if role is not None and role in member.roles]
    try:
        remove_roles = [
            role for role in managed_roles
            if next(i for i, (_, role_id) in enumerate(STAFF_ROLE_HIERARCHY) if role_id == role.id) < target_index
            and not role.managed and not role.is_default()
            and bot_member.top_role > role
        ]
        if remove_roles:
            await member.remove_roles(*remove_roles, reason=f"Promotion approved by {actor.id}")
        if target_role not in member.roles:
            await member.add_roles(target_role, reason=f"Promotion approved by {actor.id}")
    except (discord.Forbidden, discord.HTTPException):
        return False, "❌ Discord rejected the role change. Check the bot's role permissions."

    staff_permanent_demotions.pop(int(member.id), None)
    synced = await save_staff_strikes()
    response = f"✅ Promoted {member.mention} to **{target_role.name}** and cleared the permanent demotion."
    if not synced:
        response += "\n⚠️ The change was saved locally, but GitHub sync failed."
    return True, response


def _staff_role_is_held_by_another(member: discord.Member, role: discord.Role) -> bool:
    """Return True when a single-holder staff rank is already occupied."""
    if role.id not in SINGLE_HOLDER_STAFF_ROLE_IDS:
        return False

    return any(
        other.id != member.id
        and not other.bot
        and role in other.roles
        for other in member.guild.members
    )


def _fall_through_capped_staff_ranks(
    member: discord.Member,
    desired_role: discord.Role,
    desired_name: str,
    fallback_role: discord.Role | None,
) -> tuple[discord.Role | None, str]:
    """Move the target down while it is a capped rank another member holds.

    Used for both demotions and restores, so a capped rank is never handed to a
    second member by the strike system.
    """
    visited: set[int] = set()

    while (
        desired_role is not None
        and desired_role.id not in visited
        and _staff_role_is_held_by_another(member, desired_role)
    ):
        visited.add(desired_role.id)

        next_rank = get_next_staff_role(desired_role.id)
        lower_role = member.guild.get_role(next_rank[1]) if next_rank else None

        if lower_role is None:
            # Nothing left below the capped rank. Hold the rank the member
            # already has rather than demoting past what a strike should do.
            return fallback_role, (
                fallback_role.name if fallback_role is not None else "No Staff Role"
            )

        desired_role = lower_role
        desired_name = next_rank[0]

    return desired_role, desired_name


async def send_staff_strike_log(channel_id: int, content: str) -> discord.Message | None:
    """Send a normal-text staff strike role-change message and return it."""
    guild = bot.get_guild(MAIN_SERVER)
    if guild is None:
        return None

    channel = guild.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    if not hasattr(channel, "send"):
        return None

    try:
        return await channel.send(
            content,
            allowed_mentions=discord.AllowedMentions(
                everyone=False,
                roles=False,
                users=True,
                replied_user=False,
            ),
        )
    except (discord.Forbidden, discord.HTTPException):
        return None


async def resolve_main_guild_member(user_id: int) -> discord.Member | None:
    guild = bot.get_guild(MAIN_SERVER)
    if guild is None:
        return None

    member = guild.get_member(int(user_id))
    if member is not None:
        return member

    try:
        return await guild.fetch_member(int(user_id))
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return None


async def apply_staff_strike_consequences(
    member: discord.Member,
    *,
    active_count: int | None = None,
    original_role_id_override: int | None = None,
    log_two_strikes: bool = False,
    log_three_strikes: bool = False,
) -> tuple[str, str] | None:
    """Apply the role consequence for the member's current active strike count.

    Pass active_count to apply the consequence for an explicit count rather than
    the recorded one. handle_expired_staff_strikes depends on that: by the time
    it runs, prune_expired_staff_strikes has already dropped the records, so
    the recorded count is zero and a permanent demotion would otherwise be
    undone on the spot.
    """
    active_strikes = get_user_staff_strikes(member.id)
    if active_count is None:
        active_count = len(active_strikes)

    original_role_id = (
        original_role_id_override
        if original_role_id_override is not None
        else get_original_staff_role_id(member.id)
    )
    current_info = get_staff_role_for_member(member)

    if original_role_id is None and current_info is not None:
        # Fall back to the member's current rank whenever the recorded one is
        # unavailable. This must not be gated on active_count: once the last
        # strike expires, prune_expired_staff_strikes has already dropped the
        # record that carried original_staff_role_id, so gating here resolved
        # to None and stripped every staff role from a member whose only
        # strike had simply lapsed.
        original_role_id = current_info[1].id
        for strike in active_strikes:
            strike["original_staff_role_id"] = original_role_id

    original_role = member.guild.get_role(original_role_id) if original_role_id else None
    original_role_name = original_role.name if original_role is not None else "No Staff Role"

    if original_role is None and current_info is not None:
        # The recorded rank no longer exists in the guild. Never resolve to
        # "remove every staff role" on a guess; hold the rank we can see.
        original_role = current_info[1]
        original_role_name = current_info[0]

    permanent_record = staff_permanent_demotions.get(int(member.id))

    if active_count >= STAFF_STRIKE_DEMOTION_THRESHOLD:
        # Three active strikes still trigger a full staff-role removal.
        desired_role = None
        desired_name = "Suspended"
    elif permanent_record is not None:
        # A second-strike permanent demotion survives expiry/removal of strikes.
        locked_role_id = permanent_record.get("demoted_role_id")
        desired_role = member.guild.get_role(int(locked_role_id)) if locked_role_id else None
        desired_name = desired_role.name if desired_role is not None else "Permanently Demoted"
        if locked_role_id and desired_role is None and current_info is not None:
            # If the configured role was deleted, hold the visible rank rather than stripping roles.
            desired_role = current_info[1]
            desired_name = current_info[0]
    elif active_count < STAFF_STRIKE_ACTIVATION_THRESHOLD:
        # No active consequence below the activation threshold.
        desired_role = original_role
        desired_name = original_role_name if desired_role is not None else "No Staff Role"
    else:
        next_role_info = get_next_staff_role(original_role_id) if original_role_id else None
        next_role = (
            member.guild.get_role(next_role_info[1])
            if next_role_info is not None
            else None
        )
        if next_role is not None:
            desired_role = next_role
            desired_name = next_role_info[0]
        else:
            # At the lowest rank, a temporary demotion cannot go lower.
            desired_role = original_role
            desired_name = (
                original_role_name if desired_role is not None else "No Staff Role"
            )

    if desired_role is not None:
        # Never place a second member into a single-holder rank.
        desired_role, desired_name = _fall_through_capped_staff_ranks(
            member,
            desired_role,
            desired_name,
            original_role,
        )

    managed_staff_roles = {
        role_id: member.guild.get_role(role_id)
        for _, role_id in STAFF_ROLE_HIERARCHY
    }
    managed_staff_roles = {
        role_id: role
        for role_id, role in managed_staff_roles.items()
        if role is not None
    }

    current_staff_role = current_info[1] if current_info is not None else None
    current_name = current_info[0] if current_info is not None else (
        "Suspended"
        if active_count >= STAFF_STRIKE_DEMOTION_THRESHOLD
        else "Permanently Demoted"
        if permanent_record is not None and permanent_record.get("demoted_role_id") is None
        else "No Staff Role"
    )

    if desired_role is None:
        # A permanent demotion clears every managed staff role.
        roles_to_remove = list(managed_staff_roles.values())
    else:
        # Remove only the ranks above the target. A member holding Staff
        # Manager, Head Admin, Admin, Head Mod and Trial Mod who is demoted
        # one step loses Staff Manager alone; the ranks at or below the target
        # are not consequences of the strike and must be left alone.
        desired_index = next(
            (
                index
                for index, (_, role_id) in enumerate(STAFF_ROLE_HIERARCHY)
                if role_id == desired_role.id
            ),
            None,
        )
        if desired_index is None:
            # The target is outside the configured hierarchy, so remove that one
            # role rather than guessing a range of ranks.
            roles_to_remove = [
                role
                for role_id, role in managed_staff_roles.items()
                if role_id == desired_role.id
            ]
        else:
            roles_to_remove = [
                managed_staff_roles[role_id]
                for index, (_, role_id) in enumerate(STAFF_ROLE_HIERARCHY)
                if index < desired_index and role_id in managed_staff_roles
            ]

    bot_member = member.guild.me
    can_manage = (
        bot_member is not None
        and bot_member.guild_permissions.manage_roles
        and member.guild.owner_id != member.id
    )

    # Only touch roles the member actually holds. Discord ignores removals for
    # roles they do not have, but requesting them is needless work and puts
    # ranks they never held into the audit-log reason.
    held_role_ids = {role.id for role in member.roles}
    roles_to_remove = [
        role for role in roles_to_remove if role.id in held_role_ids
    ]

    if can_manage:
        try:
            removable = [
                role
                for role in roles_to_remove
                if not role.managed and not role.is_default()
                and bot_member.top_role > role
                and member.top_role < bot_member.top_role
            ]
            if removable:
                await member.remove_roles(
                    *removable,
                    reason=f"Staff strike consequence ({active_count} active strikes)",
                )

            if (
                desired_role is not None
                and desired_role not in member.roles
                and not desired_role.managed
                and not desired_role.is_default()
                and bot_member.top_role > desired_role
                and member.top_role < bot_member.top_role
            ):
                await member.add_roles(
                    desired_role,
                    reason=f"Staff strike consequence ({active_count} active strikes)",
                )
        except (discord.Forbidden, discord.HTTPException):
            pass

    refreshed = await resolve_main_guild_member(member.id)
    if refreshed is not None:
        member = refreshed
        refreshed_info = get_staff_role_for_member(member)
        actual_name = refreshed_info[0] if refreshed_info is not None else (
            "Suspended"
            if active_count >= STAFF_STRIKE_DEMOTION_THRESHOLD
            else "Permanently Demoted"
            if permanent_record is not None and permanent_record.get("demoted_role_id") is None
            else "No Staff Role"
        )
        # Compare the member's effective top rank before and after rather than
        # requiring the target rank itself. With several staff roles held at
        # once the target is often not the top rank, and the strike still took
        # effect by dropping the rank above it.
        role_changed = (
            (current_staff_role.id if current_staff_role is not None else None)
            != (refreshed_info[1].id if refreshed_info is not None else None)
        )
    else:
        actual_name = desired_name
        role_changed = current_name != desired_name

    if role_changed and current_name != actual_name:
        if log_two_strikes and active_count == STAFF_STRIKE_ACTIVATION_THRESHOLD:
            await send_staff_strike_log(
                STAFF_STRIKE_TWO_ACTIVE_CHANNEL_ID,
                f"<@{member.id}> {current_name} to {actual_name}",
            )
        elif log_three_strikes and active_count >= STAFF_STRIKE_DEMOTION_THRESHOLD:
            await send_staff_strike_log(
                STAFF_STRIKE_TWO_ACTIVE_CHANNEL_ID,
                f"<@{member.id}> {current_name} to Suspended",
            )

        return current_name, actual_name

    return None


async def handle_expired_staff_strikes(
    expired_strikes: list[dict],
) -> None:
    """Restore/demote staff roles after strikes expire and announce promotions."""
    if not expired_strikes:
        return

    by_user: dict[int, list[dict]] = {}
    for strike in expired_strikes:
        by_user.setdefault(int(strike["user_id"]), []).append(strike)

    # Update each original strike log in place so expiry does not create
    # a second, separate log entry. Older strikes without a saved message ID
    # are safely skipped.
    guild = bot.get_guild(MAIN_SERVER)
    channel = guild.get_channel(STAFF_STRIKE_ACTIVE_CHANNEL_ID) if guild else None
    if channel is None:
        try:
            channel = await bot.fetch_channel(STAFF_STRIKE_ACTIVE_CHANNEL_ID)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            channel = None

    for strike in expired_strikes:
        message_id = strike.get("log_message_id")
        if not message_id or channel is None or not hasattr(channel, "fetch_message"):
            continue
        try:
            message = await channel.fetch_message(int(message_id))
            original_content = message.content
            if "Strike expired" not in original_content:
                expires_at = _staff_strike_datetime(strike.get("expires_at", ""))
                expiry_text = (
                    f"\n\n✅ **Strike expired**"
                    + (f" <t:{int(expires_at.timestamp())}:R>" if expires_at else "")
                )
                await message.edit(content=original_content + expiry_text)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError, TypeError):
            pass

    for user_id, user_expired in by_user.items():
        member = await resolve_main_guild_member(user_id)
        if member is None:
            continue

        active_count = len(get_user_staff_strikes(user_id))
        before_active_count = active_count + len(user_expired)

        # Carry the pre-strike rank out of the expired batch. The records are
        # already gone from staff_strikes by this point, so without this a
        # member below the demotion threshold had no rank to be restored to.
        original_role_id = None
        for strike in user_expired:
            recorded = strike.get("original_staff_role_id")
            if recorded:
                original_role_id = int(recorded)
                break

        permanently_demoted = before_active_count >= STAFF_STRIKE_DEMOTION_THRESHOLD

        before_info = get_staff_role_for_member(member)
        if before_info is not None:
            before_name = before_info[0]
        elif permanently_demoted:
            before_name = "Suspended"
        else:
            before_name = "No Staff Role"

        if permanently_demoted:
            # Reaching the demotion threshold is a permanent demotion. Expiry
            # must not hand the rank back, so enforce the demoted state instead
            # of restoring, and leave reinstatement to a human. This also means
            # a member who was demoted earlier and then wrongly kept a rank
            # loses it when their strikes lapse.
            await apply_staff_strike_consequences(
                member,
                active_count=STAFF_STRIKE_DEMOTION_THRESHOLD,
                original_role_id_override=original_role_id,
            )
        else:
            await apply_staff_strike_consequences(
                member,
                active_count=active_count,
                original_role_id_override=original_role_id,
            )

        refreshed = await resolve_main_guild_member(user_id) or member
        after_info = get_staff_role_for_member(refreshed)
        after_name = after_info[0] if after_info is not None else (
            "Suspended" if permanently_demoted else "No Staff Role"
        )

        if before_name != after_name:
            if permanently_demoted:
                await send_staff_strike_log(
                    STAFF_STRIKE_EXPIRED_CHANNEL_ID,
                    f"<@{user_id}> {before_name} to {after_name} "
                    f"(permanent demotion at {STAFF_STRIKE_DEMOTION_THRESHOLD} strikes; "
                    "reinstate by hand)",
                )
            elif int(user_id) in staff_permanent_demotions:
                await send_staff_strike_log(
                    STAFF_STRIKE_EXPIRED_CHANNEL_ID,
                    f"<@{user_id}> {before_name} to {after_name} "
                    "(second-strike permanent demotion remains; Staff Manager or higher must promote)",
                )
            else:
                await send_staff_strike_log(
                    STAFF_STRIKE_EXPIRED_CHANNEL_ID,
                    f"<@{user_id}> {before_name} to {after_name}",
                )


def get_user_staff_strikes(user_id: int) -> list[dict]:
    """Return currently active strikes for one user, sorted by strike number."""
    return sorted(
        (
            strike
            for strike in staff_strikes
            if int(strike.get("user_id", 0)) == int(user_id)
        ),
        key=lambda strike: int(strike["strike_number"]),
    )


def next_staff_strike_number(user_id: int) -> int:
    """Get the next sequential strike number for a user."""
    highest = 0
    for strike in staff_strikes:
        if int(strike.get("user_id", 0)) != int(user_id):
            continue
        try:
            highest = max(highest, int(strike["strike_number"]))
        except (KeyError, TypeError, ValueError):
            continue
    return highest + 1


def create_staff_strike(
    *,
    user_id: int,
    reason: str,
    days: int,
    issued_by: int,
    original_staff_role_id: int | None = None,
) -> dict:
    """Create a new time-limited staff strike."""
    reason = reason.strip()
    number = next_staff_strike_number(user_id)
    now = datetime.now(timezone.utc)

    strike = {
        "user_id": int(user_id),
        "strike_number": number,
        "reason": reason,
        "issued_by": int(issued_by),
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(days=int(days))).isoformat(),
        "original_staff_role_id": (
            int(original_staff_role_id)
            if original_staff_role_id is not None
            else None
        ),
        "log_message_id": None,
    }
    staff_strikes.append(strike)
    return strike


STAFF_STRIKE_SYNC_LOCK = asyncio.Lock()
staff_strike_github_sync_error: str | None = None


def _github_get_staff_strikes() -> tuple[bool, list[dict], str | None, dict[int, dict]]:
    """Fetch active strikes and permanent-demotion state, accepting legacy list files."""
    encoded_branch = urllib.parse.quote(GITHUB_BRANCH, safe="")
    url = f"{_github_contents_url(STAFF_STRIKES_FILE)}?ref={encoded_branch}"
    try:
        payload = _github_request_json(url)
    except RuntimeError as error:
        if str(error).startswith("GitHub API HTTP 404:"):
            return False, [], None, {}
        raise

    encoded_content = payload.get("content", "")
    if not encoded_content:
        return True, [], payload.get("sha"), {}

    try:
        decoded = base64.b64decode("".join(str(encoded_content).split())).decode("utf-8")
        data = json.loads(decoded)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f"GitHub strike file {STAFF_STRIKES_FILE.name!r} has invalid JSON."
        ) from error

    if isinstance(data, list):
        raw_strikes, raw_permanent_demotions = data, {}
    elif isinstance(data, dict):
        raw_strikes = data.get("strikes", [])
        raw_permanent_demotions = data.get("permanent_demotions", {})
    else:
        raise RuntimeError("Staff strike JSON has an unsupported format.")
    if not isinstance(raw_strikes, list):
        raise RuntimeError("The strikes field must be a JSON list.")
    if not isinstance(raw_permanent_demotions, dict):
        raw_permanent_demotions = {}

    cleaned = []
    for item in raw_strikes:
        if not isinstance(item, dict):
            continue
        try:
            cleaned.append({
                "user_id": int(item["user_id"]),
                "strike_number": int(item["strike_number"]),
                "reason": str(item["reason"]).strip(),
                "issued_by": int(item["issued_by"]),
                "issued_at": str(item["issued_at"]),
                "expires_at": str(item["expires_at"]),
                "original_staff_role_id": (
                    int(item["original_staff_role_id"])
                    if item.get("original_staff_role_id") is not None
                    else None
                ),
                "log_message_id": (int(item["log_message_id"]) if item.get("log_message_id") else None),
            })
        except (KeyError, TypeError, ValueError):
            continue

    cleaned_permanent = {}
    for raw_user_id, item in raw_permanent_demotions.items():
        if not isinstance(item, dict):
            continue
        try:
            user_id = int(item.get("user_id", raw_user_id))
            cleaned_permanent[user_id] = {
                "user_id": user_id,
                "original_role_id": int(item["original_role_id"]),
                "demoted_role_id": (
                    int(item["demoted_role_id"])
                    if item.get("demoted_role_id") is not None
                    else None
                ),
                "issued_by": int(item["issued_by"]),
                "issued_at": str(item["issued_at"]),
                "reason": str(item.get("reason", "")).strip(),
            }
        except (KeyError, TypeError, ValueError):
            continue
    return True, cleaned, payload.get("sha"), cleaned_permanent


def _github_save_staff_strikes(
    strikes: list[dict],
    permanent_demotions: dict[int, dict],
) -> None:
    """Create or update the combined staff strike state file in GitHub."""
    exists, _, blob_sha, _ = _github_get_staff_strikes()
    serialized = json.dumps({
        "strikes": strikes,
        "permanent_demotions": {
            str(user_id): record for user_id, record in permanent_demotions.items()
        },
    }, indent=2) + "\n"
    payload = {
        "message": f"Update {STAFF_STRIKES_FILE.name}",
        "content": base64.b64encode(serialized.encode("utf-8")).decode("ascii"),
        "branch": GITHUB_BRANCH,
    }
    if exists and blob_sha:
        payload["sha"] = blob_sha
    _github_request_json(
        _github_contents_url(STAFF_STRIKES_FILE),
        method="PUT",
        payload=payload,
    )


async def save_staff_strikes() -> bool:
    """Save local strikes and permanent-demotion state, then sync to GitHub."""
    global staff_strike_github_sync_error
    _save_staff_strikes_local(staff_strikes)
    if not GITHUB_TOKEN:
        staff_strike_github_sync_error = "GITHUB_TOKEN is not configured."
        return False
    async with STAFF_STRIKE_SYNC_LOCK:
        try:
            await asyncio.to_thread(
                _github_save_staff_strikes,
                list(staff_strikes),
                dict(staff_permanent_demotions),
            )
            staff_strike_github_sync_error = None
            return True
        except Exception as error:
            staff_strike_github_sync_error = str(error)
            return False


async def sync_staff_strikes_from_github() -> bool:
    """Load strike records and permanent-demotion state from GitHub."""
    global staff_strikes, staff_strike_github_sync_error
    if not GITHUB_TOKEN:
        staff_strike_github_sync_error = "GITHUB_TOKEN is not configured."
        return False
    async with STAFF_STRIKE_SYNC_LOCK:
        try:
            exists, remote_strikes, _, remote_permanent = await asyncio.to_thread(
                _github_get_staff_strikes
            )
            if exists:
                staff_strikes.clear()
                staff_strikes.extend(remote_strikes)
                staff_permanent_demotions.clear()
                staff_permanent_demotions.update(remote_permanent)
                _save_staff_strikes_local(staff_strikes)
            else:
                await asyncio.to_thread(
                    _github_save_staff_strikes,
                    list(staff_strikes),
                    dict(staff_permanent_demotions),
                )
            staff_strike_github_sync_error = None
            return True
        except Exception as error:
            staff_strike_github_sync_error = str(error)
            return False


def staff_strike_command_allowed(member: discord.Member) -> bool:
    return any(role.id in STAFF_STRIKE_ALLOWED_ROLE_IDS for role in member.roles)


async def staff_strike_command_check(interaction: discord.Interaction) -> bool:
    if interaction.guild_id != MAIN_SERVER:
        raise app_commands.CheckFailure(
            "This command can only be used in the main server."
        )

    if not isinstance(interaction.user, discord.Member):
        raise app_commands.CheckFailure(
            "Could not verify your server roles."
        )

    if not staff_strike_command_allowed(interaction.user):
        raise app_commands.CheckFailure(
            "You do not have permission to use the staff strike system."
        )

    return True


def format_staff_strike(target: discord.Member, strike: dict) -> str:
    return (
        f"{target.mention} / {target.id}\n\n"
        f"strike #{int(strike['strike_number'])}: {strike['reason']}\n\n"
        f"{int(round(( 
            _staff_strike_datetime(strike['expires_at'])
            - _staff_strike_datetime(strike['issued_at'])
        ).total_seconds() / 86400))}d"
    )


def build_staff_strike_embed(
    target: discord.Member,
    strike: dict,
    active_count: int,
    *,
    consequence: tuple[str, str] | None = None,
    sync_failed: bool = False,
) -> discord.Embed:
    """Build the shared strike result embed used by slash and prefix commands."""
    issued_at = _staff_strike_datetime(strike["issued_at"])
    expires_at = _staff_strike_datetime(strike["expires_at"])
    duration_days = (
        int(round((expires_at - issued_at).total_seconds() / 86400))
        if issued_at is not None and expires_at is not None
        else 0
    )

    embed = discord.Embed(
        title="⚔️ Strike Information",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="User",
        value=f"{target.mention} | `{target.id}`",
        inline=False,
    )
    embed.add_field(
        name=f"Strike #{int(strike['strike_number'])}",
        value=(
            f"**Reason:** {discord.utils.escape_markdown(str(strike['reason']))}\n"
            f"**Duration:** {duration_days}d\n"
            f"**Active Strikes:** {active_count}"
        ),
        inline=False,
    )

    if consequence is not None:
        old_role, new_role = consequence
        embed.add_field(
            name="Role Action",
            value=f"**{discord.utils.escape_markdown(old_role)} → {discord.utils.escape_markdown(new_role)}**",
            inline=False,
        )

    if sync_failed:
        embed.set_footer(text="⚠️ GitHub sync failed; the strike was saved locally.")

    return embed


def parse_staff_strike_duration(
    value: str,
) -> int | None:
    value = value.strip().lower()
    if value.endswith("d"):
        value = value[:-1].strip()
    try:
        days = int(value)
    except ValueError:
        return None

    if not 7 <= days <= 40:
        return None

    return days



@tree.command(
    name="strike",
    description="Give a staff strike that expires after 7 to 40 days.",
)
@app_commands.describe(
    member="The staff member receiving the strike",
    reason="Reason for the strike",
    days="How many days the strike should last (7-40)",
    demotion="For the second active strike, choose temporary or permanent demotion.",
)
@app_commands.choices(demotion=[
    app_commands.Choice(name="Temporary demotion", value="temporary"),
    app_commands.Choice(name="Permanent demotion", value="permanent"),
])
@app_commands.check(staff_strike_command_check)
async def strike_command(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str,
    days: app_commands.Range[int, 7, 40],
    demotion: app_commands.Choice[str] | None = None,
):
    """Issue a time-limited staff strike."""
    await interaction.response.defer(ephemeral=False)

    reason = reason.strip()
    if not reason:
        await interaction.followup.send(
            "❌ You must provide a reason for the strike.",
            ephemeral=False,
        )
        return

    expired = prune_expired_staff_strikes()
    if expired:
        await handle_expired_staff_strikes(expired)
        await save_staff_strikes()

    current_staff_info = get_staff_role_for_member(member)
    if current_staff_info is None:
        await interaction.followup.send(
            "❌ The selected member does not have a configured staff role.",
            ephemeral=False,
        )
        return

    original_role_id = get_original_staff_role_id(member.id) or current_staff_info[1].id
    active_before = len(get_user_staff_strikes(member.id))
    demotion_value = demotion.value if demotion is not None else "temporary"
    if demotion_value == "permanent" and active_before + 1 != STAFF_STRIKE_ACTIVATION_THRESHOLD:
        await interaction.followup.send(
            "❌ Choose permanent demotion only when this will be the member's second active strike.",
            ephemeral=False,
        )
        return
    if is_duplicate_staff_strike_submission(interaction.user.id, member.id, int(days), reason):
        await interaction.followup.send(
            "⚠️ This looks like a duplicate strike submission. No additional strike was added.",
            ephemeral=False,
        )
        return

    strike = create_staff_strike(
        user_id=member.id,
        reason=reason,
        days=int(days),
        issued_by=interaction.user.id,
        original_staff_role_id=original_role_id,
    )
    active_count = len(get_user_staff_strikes(member.id))
    if active_count == STAFF_STRIKE_ACTIVATION_THRESHOLD and demotion_value == "permanent":
        register_permanent_staff_demotion(
            member,
            original_role_id=original_role_id,
            issued_by=interaction.user.id,
            reason=reason,
        )
    consequence = await apply_staff_strike_consequences(
        member,
        active_count=active_count,
        log_two_strikes=(active_count == STAFF_STRIKE_ACTIVATION_THRESHOLD),
        log_three_strikes=(active_count >= STAFF_STRIKE_DEMOTION_THRESHOLD),
    )
    synced = await save_staff_strikes()

    log_message = await send_staff_strike_log(
        STAFF_STRIKE_ACTIVE_CHANNEL_ID,
        format_staff_strike(member, strike),
    )
    if log_message is not None:
        strike["log_message_id"] = log_message.id
        await save_staff_strikes()

    await interaction.followup.send(
        "✅ Strike issued successfully.",
        ephemeral=False,
    )


@tree.command(
    name="staffpromote",
    description="Promote a permanently demoted staff member (Staff Manager or higher).",
)
@app_commands.describe(
    member="The permanently demoted staff member",
    role="The staff rank to promote them to",
)
@app_commands.check(staff_promotion_command_check)
async def staffpromote_command(
    interaction: discord.Interaction,
    member: discord.Member,
    role: discord.Role,
):
    await interaction.response.defer(ephemeral=False)
    _, response = await perform_staff_promotion(interaction.user, member, role)
    await interaction.followup.send(response, ephemeral=False)


@tree.command(
    name="strikes",
    description="View a member's active staff strikes.",
)
@app_commands.describe(member="The member whose active strikes you want to view")
@app_commands.check(staff_strike_command_check)
async def strikes_command(
    interaction: discord.Interaction,
    member: discord.Member,
):
    """Show a member's active staff strikes."""
    expired = prune_expired_staff_strikes()
    if expired:
        await handle_expired_staff_strikes(expired)
        await save_staff_strikes()

    strikes = get_user_staff_strikes(member.id)
    if not strikes:
        await interaction.response.send_message(
            f"{member.mention} / {member.id}\n\nNo active strikes.",
            ephemeral=False,
        )
        return

    blocks = []
    for strike in strikes:
        blocks.append(
            f"strike #{int(strike['strike_number'])}: {strike['reason']}"
        )

    await interaction.response.send_message(
        f"{member.mention} / {member.id}\n\n" + "\n\n".join(blocks),
        ephemeral=False,
    )


@tree.command(
    name="removestrike",
    description="Remove a strike record. Does not change the member's roles.",
)
@app_commands.describe(
    member="The staff member whose strike you want to remove",
    strike_number="The strike number to remove",
)
@app_commands.check(staff_strike_command_check)
async def removestrike_command(
    interaction: discord.Interaction,
    member: discord.Member,
    strike_number: app_commands.Range[int, 1, 9999],
):
    """Remove one active strike record without changing the member's roles.

Strikes can be issued in error or resolved by talking to the owner or a
high-ranking staff member, so this corrects the record only. It never promotes
or demotes anyone, and it never reverses a permanent demotion; reinstatement is
a manual action in Discord.
"""
    await interaction.response.defer(ephemeral=False)

    expired = prune_expired_staff_strikes()
    if expired:
        await handle_expired_staff_strikes(expired)
        await save_staff_strikes()

    matching = next(
        (
            strike
            for strike in staff_strikes
            if int(strike.get("user_id", 0)) == member.id
            and int(strike.get("strike_number", 0)) == int(strike_number)
        ),
        None,
    )

    if matching is None:
        await interaction.followup.send(
            f"❌ {member.mention} does not have an active strike #{int(strike_number)}.",
            ephemeral=False,
        )
        return

    before_info = get_staff_role_for_member(member)
    before_role_name = before_info[0] if before_info is not None else (
        "Suspended"
        if len(get_user_staff_strikes(member.id)) >= STAFF_STRIKE_DEMOTION_THRESHOLD
        else "No Staff Role"
    )

    staff_strikes.remove(matching)

    active_count = len(get_user_staff_strikes(member.id))
    synced = await save_staff_strikes()

    response = (
        f"✅ Removed strike #{int(strike_number)} from "
        f"{member.mention} / {member.id}.\n\n"
        f"Active strikes: **{active_count}**"
    )

    # Roles are deliberately left alone here. Removing a strike corrects the
    # record, it does not reinstate a rank: a strike can have been issued in
    # error or resolved by talking to the owner or a high-ranking staff member,
    # and those are human decisions. Reassigning a rank here would also undo a
    # permanent demotion, so any reinstatement is done by hand in Discord.
    if before_role_name in ("Suspended", "No Staff Role"):
        response += (
            f"\n\n{member.mention} currently has no staff role. "
            "Reinstatement is manual and is not done by this command."
        )

    if not synced:
        response += "\n⚠️ GitHub sync failed; the strike removal was saved locally."

    await interaction.followup.send(response, ephemeral=False)



async def staff_strike_expiry_worker():
    """Expire staff strikes, restore/demote roles, and announce promotions."""
    await bot.wait_until_ready()

    while not bot.is_closed():
        try:
            expired = prune_expired_staff_strikes()
            if expired:
                await handle_expired_staff_strikes(expired)
                await save_staff_strikes()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

        await asyncio.sleep(STAFF_STRIKE_EXPIRY_CHECK_SECONDS)
