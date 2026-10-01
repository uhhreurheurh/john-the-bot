"""Staff strike feature module."""

from bot import *

def load_staff_strikes() -> list[dict]:
    """Load all staff strikes from local disk."""
    if not STAFF_STRIKES_FILE.exists():
        return []

    try:
        with STAFF_STRIKES_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (json.JSONDecodeError, OSError, TypeError):
        return []

    if not isinstance(data, list):
        return []

    cleaned = []
    for item in data:
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
        })

    return cleaned


def _save_staff_strikes_local(strikes: list[dict]) -> None:
    """Persist staff strikes to the local filesystem."""
    try:
        with STAFF_STRIKES_FILE.open("w", encoding="utf-8") as file:
            json.dump(strikes, file, indent=2)
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
    for strike in staff_strikes:
        if int(strike.get("user_id", 0)) != int(user_id):
            continue
        original_role_id = strike.get("original_staff_role_id")
        if original_role_id:
            return int(original_role_id)
    return None


async def send_staff_strike_log(channel_id: int, content: str) -> bool:
    """Send a normal-text staff strike role-change message."""
    guild = bot.get_guild(MAIN_SERVER)
    if guild is None:
        return False

    channel = guild.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return False

    if not hasattr(channel, "send"):
        return False

    try:
        await channel.send(
            content,
            allowed_mentions=discord.AllowedMentions(
                everyone=False,
                roles=False,
                users=True,
                replied_user=False,
            ),
        )
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False


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
    """Apply the role consequence for the member's current active strike count."""
    active_strikes = (
        get_user_staff_strikes(member.id)
        if active_count is None
        else [
            strike
            for strike in staff_strikes
            if int(strike.get("user_id", 0)) == int(member.id)
        ]
    )
    active_count = len(active_strikes)

    original_role_id = (
        original_role_id_override
        if original_role_id_override is not None
        else get_original_staff_role_id(member.id)
    )
    current_info = get_staff_role_for_member(member)

    if original_role_id is None and current_info is not None and active_count > 0:
        original_role_id = current_info[1].id
        for strike in active_strikes:
            strike["original_staff_role_id"] = original_role_id

    original_role = member.guild.get_role(original_role_id) if original_role_id else None
    original_role_name = original_role.name if original_role is not None else "No Staff Role"

    if active_count < 2:
        desired_role = original_role
        desired_name = original_role_name if desired_role is not None else "No Staff Role"
    elif active_count == 2:
        next_role_info = get_next_staff_role(original_role_id) if original_role_id else None
        desired_role = (
            member.guild.get_role(next_role_info[1])
            if next_role_info is not None
            else None
        )
        desired_name = (
            next_role_info[0]
            if next_role_info is not None
            else "Suspended"
        )
    else:
        desired_role = None
        desired_name = "Suspended"

    managed_staff_roles = [
        member.guild.get_role(role_id)
        for _, role_id in STAFF_ROLE_HIERARCHY
    ]
    managed_staff_roles = [role for role in managed_staff_roles if role is not None]

    current_staff_role = current_info[1] if current_info is not None else None
    current_name = current_info[0] if current_info is not None else (
        "Suspended" if current_staff_role is None and active_count >= 3 else "No Staff Role"
    )

    roles_to_remove = [
        role
        for role in managed_staff_roles
        if desired_role is None or role.id != desired_role.id
    ]

    bot_member = member.guild.me
    can_manage = (
        bot_member is not None
        and bot_member.guild_permissions.manage_roles
        and member.guild.owner_id != member.id
    )

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
            "Suspended" if active_count >= 3 else "No Staff Role"
        )
        role_changed = (
            desired_role is not None
            and refreshed_info is not None
            and refreshed_info[1].id == desired_role.id
        ) or (
            desired_role is None and refreshed_info is None
        )
    else:
        actual_name = desired_name
        role_changed = current_name != desired_name

    if role_changed and current_name != actual_name:
        if log_two_strikes and active_count == 2:
            await send_staff_strike_log(
                STAFF_STRIKE_TWO_ACTIVE_CHANNEL_ID,
                f"<@{member.id}> {current_name} to {actual_name}",
            )
        elif log_three_strikes and active_count >= 3:
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

    for user_id, user_expired in by_user.items():
        member = await resolve_main_guild_member(user_id)
        if member is None:
            continue

        active_count = len(get_user_staff_strikes(user_id))
        before_active_count = active_count + len(user_expired)

        before_info = get_staff_role_for_member(member)
        if before_info is not None:
            before_name = before_info[0]
        elif before_active_count >= 3:
            before_name = "Suspended"
        else:
            before_name = "No Staff Role"

        await apply_staff_strike_consequences(
            member,
            active_count=active_count,
        )

        refreshed = await resolve_main_guild_member(user_id) or member
        after_info = get_staff_role_for_member(refreshed)
        after_name = after_info[0] if after_info is not None else (
            "Suspended" if active_count >= 3 else "No Staff Role"
        )

        if before_name != after_name:
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
    }
    staff_strikes.append(strike)
    return strike


STAFF_STRIKE_SYNC_LOCK = asyncio.Lock()
staff_strike_github_sync_error: str | None = None


def _github_get_staff_strikes() -> tuple[bool, list[dict], str | None]:
    """Fetch the staff strike file from GitHub.

    Returns (exists, strikes, blob_sha).
    """
    encoded_branch = urllib.parse.quote(GITHUB_BRANCH, safe="")
    url = f"{_github_contents_url(STAFF_STRIKES_FILE)}?ref={encoded_branch}"

    try:
        payload = _github_request_json(url)
    except RuntimeError as error:
        if str(error).startswith("GitHub API HTTP 404:"):
            return False, [], None
        raise

    encoded_content = payload.get("content", "")
    if not encoded_content:
        return True, [], payload.get("sha")

    try:
        decoded = base64.b64decode(
            "".join(str(encoded_content).split())
        ).decode("utf-8")
        data = json.loads(decoded)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f"GitHub strike file {STAFF_STRIKES_FILE.name!r} has invalid JSON."
        ) from error

    if not isinstance(data, list):
        raise RuntimeError(
            f"GitHub strike file {STAFF_STRIKES_FILE.name!r} must contain a JSON list."
        )

    cleaned = []
    for item in data:
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
            })
        except (KeyError, TypeError, ValueError):
            continue

    return True, cleaned, payload.get("sha")


def _github_save_staff_strikes(strikes: list[dict]) -> None:
    """Create or update the staff strike file in GitHub."""
    exists, _, blob_sha = _github_get_staff_strikes()
    serialized = json.dumps(strikes, indent=2) + "\n"

    payload = {
        "message": f"Update {STAFF_STRIKES_FILE.name}",
        "content": base64.b64encode(
            serialized.encode("utf-8")
        ).decode("ascii"),
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
    """Save locally and sync staff strikes to GitHub when configured."""
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
            )
            staff_strike_github_sync_error = None
            return True
        except Exception as error:
            staff_strike_github_sync_error = str(error)
            return False


async def sync_staff_strikes_from_github() -> bool:
    """Load staff strike records from GitHub."""
    global staff_strikes, staff_strike_github_sync_error

    if not GITHUB_TOKEN:
        staff_strike_github_sync_error = "GITHUB_TOKEN is not configured."
        return False

    async with STAFF_STRIKE_SYNC_LOCK:
        try:
            exists, remote_strikes, _ = await asyncio.to_thread(
                _github_get_staff_strikes
            )

            if exists:
                staff_strikes.clear()
                staff_strikes.extend(remote_strikes)
                _save_staff_strikes_local(staff_strikes)
            else:
                await asyncio.to_thread(
                    _github_save_staff_strikes,
                    list(staff_strikes),
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



async def strike_command(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str,
    days: app_commands.Range[int, 7, 40],
):
    """Issue a time-limited staff strike."""
    await interaction.response.defer(ephemeral=False)

    reason = reason.strip()
    if not reason:
        await interaction.followup.send(
            "❌ You must provide a reason for the strike.",
            ephemeral=True,
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
            ephemeral=True,
        )
        return

    original_role_id = get_original_staff_role_id(member.id) or current_staff_info[1].id

    strike = create_staff_strike(
        user_id=member.id,
        reason=reason,
        days=int(days),
        issued_by=interaction.user.id,
        original_staff_role_id=original_role_id,
    )
    active_count = len(get_user_staff_strikes(member.id))
    consequence = await apply_staff_strike_consequences(
        member,
        active_count=active_count,
        log_two_strikes=(active_count == 2),
        log_three_strikes=(active_count >= 3),
    )
    synced = await save_staff_strikes()

    await send_staff_strike_log(
        STAFF_STRIKE_ACTIVE_CHANNEL_ID,
        format_staff_strike(member, strike),
    )

    extra = ""
    if not synced:
        extra = "\n⚠️ GitHub sync failed; the strike was saved locally."
    elif consequence is not None:
        old_role, new_role = consequence
        extra = f"\nRole action: **{old_role} → {new_role}**"

    await interaction.followup.send(
        format_staff_strike(member, strike)
        + f"\n\nActive strikes: **{active_count}**"
        + extra,
        ephemeral=False,
    )


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
    description="Remove a specific active staff strike from a member.",
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
    """Remove one active strike and immediately recalculate staff rank."""
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

    original_role_id = (
        int(matching["original_staff_role_id"])
        if matching.get("original_staff_role_id") is not None
        else get_original_staff_role_id(member.id)
    )

    before_info = get_staff_role_for_member(member)
    before_role_name = before_info[0] if before_info is not None else (
        "Suspended" if len(get_user_staff_strikes(member.id)) >= 3 else "No Staff Role"
    )

    staff_strikes.remove(matching)

    active_count = len(get_user_staff_strikes(member.id))
    consequence = await apply_staff_strike_consequences(
        member,
        active_count=active_count,
        original_role_id_override=original_role_id,
    )
    synced = await save_staff_strikes()

    response = (
        f"✅ Removed strike #{int(strike_number)} from "
        f"{member.mention} / {member.id}.\n\n"
        f"Active strikes: **{active_count}**"
    )

    if consequence is not None:
        old_role, new_role = consequence
        response += f"\nRole action: **{old_role} → {new_role}**"
    elif original_role_id is not None:
        refreshed = await resolve_main_guild_member(member.id) or member
        after_info = get_staff_role_for_member(refreshed)
        after_role_name = after_info[0] if after_info is not None else (
            "Suspended" if active_count >= 3 else "No Staff Role"
        )
        if before_role_name != after_role_name:
            response += f"\nRole action: **{before_role_name} → {after_role_name}**"

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
