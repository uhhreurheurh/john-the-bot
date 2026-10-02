"""UWUIFY feature module."""

from bot import *

_UWU_DATA = json.loads(Path(__file__).with_name("uwu_words.json").read_text(encoding="utf-8"))
UWU_WORD_BLACKLIST = set(_UWU_DATA.get("word_blacklist", []))

UWU_WEBHOOK_NAME = "Uwuify Relay"
UWU_WEBHOOK_IDLE_SECONDS = 5 * 60

# Shared whitelist for both UWUIFY and HOODIFY.
UWU_ALLOWED_ROLE_IDS = textify_whitelist

# External proxy bots whose output should be checked for active UWU/HOODIFY targets.
# Use the bot name here so we do not rely on an unverified application ID.
PROXY_BOT_IDS = set()
PROXY_BOT_NAMES = {
    "bleed",
}
PROXY_REQUEST_TTL_SECONDS = 15

# Safety cleanup interval for leftover UWU webhooks.
UWU_WEBHOOK_CLEANUP_INTERVAL_SECONDS = 60

# Maximum number of unique people who can be actively UWUified at once.
MAX_ACTIVE_UWU_TARGETS = 5

# UWUIFY owns its own state. The previous merged file accidentally relied on
# variables that only existed in HOODIFY, causing NameError during activation.
uwu_webhooks: dict[int, dict] = {}
uwu_targets: dict[int, set[int]] = {}
uwu_target_lock = asyncio.Lock()

# Use the package's optional flags so the transformation is more obvious
# than the minimal default behavior.
UWU_FLAGS = uwuify.SMILEY | uwuify.YU | uwuify.STUTTER

def uwu_user_is_whitelisted(member: discord.Member | discord.User) -> bool:
    """Return True when the member has at least one allowed UWU role."""
    role_ids = [role.id for role in getattr(member, "roles", ())]
    result = any(role_id in UWU_ALLOWED_ROLE_IDS for role_id in role_ids)
    return result


def get_active_uwu_target_ids(exclude_channel_id: int | None = None) -> set[int]:
    """Return the unique member IDs currently using UWU mode."""
    active: set[int] = set()

    for channel_id, target_ids in uwu_targets.items():
        if channel_id == exclude_channel_id:
            continue
        active.update(target_ids)

    return active


def get_active_uwu_target_count() -> int:
    """Return the number of unique people currently being UWUified globally."""
    return len(get_active_uwu_target_ids())


class UwuTargetLimitReached(Exception):
    """Raised when adding a new target would exceed the global UWU limit."""


class UwuMessageBlocked(Exception):
    """Raised when a message contains text that the UWU webhook must not send."""


def get_blacklisted_uwu_word(content: str) -> str | None:
    """Return the first blocked word/phrase found in content, or None."""
    if not content or not UWU_WORD_BLACKLIST:
        return None

    for blocked in UWU_WORD_BLACKLIST:
        blocked = str(blocked).strip()
        if not blocked:
            continue

        pattern = rf"(?<!\w){re.escape(blocked)}(?!\w)"
        if re.search(pattern, content, flags=re.IGNORECASE):
            return blocked

    return None


def ensure_uwu_message_is_allowed(content: str) -> None:
    """Raise UwuMessageBlocked when the content must not be sent."""
    blocked = get_blacklisted_uwu_word(content)
    if blocked is not None:
        raise UwuMessageBlocked(blocked)


async def _delete_uwu_webhook_after_idle(channel_id: int, webhook: discord.Webhook) -> None:
    """Delete the temporary UWU webhook after 5 minutes without use."""
    try:
        await asyncio.sleep(UWU_WEBHOOK_IDLE_SECONDS)

        entry = uwu_webhooks.get(channel_id)
        if entry is not None and entry.get("webhook") is webhook:
            try:
                await webhook.delete(reason="Uwu webhook unused for 5 minutes")
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
            finally:
                uwu_webhooks.pop(channel_id, None)
                uwu_targets.pop(channel_id, None)
    except asyncio.CancelledError:
        return


def _reset_uwu_webhook_timer(channel_id: int, webhook: discord.Webhook) -> None:
    entry = uwu_webhooks.get(channel_id)
    if entry is None or entry.get("webhook") is not webhook:
        return

    old_timer = entry.get("timer")
    if old_timer is not None and not old_timer.done():
        old_timer.cancel()

    entry["timer"] = asyncio.create_task(
        _delete_uwu_webhook_after_idle(channel_id, webhook)
    )


async def get_uwu_webhook(channel: discord.TextChannel) -> discord.Webhook:
    """Get or create the temporary UWU webhook for a channel."""
    channel_id = channel.id
    entry = uwu_webhooks.get(channel_id)

    if entry is not None:
        webhook = entry.get("webhook")
        if webhook is not None:
            try:
                await webhook.fetch()
                _reset_uwu_webhook_timer(channel_id, webhook)
                return webhook
            except (discord.NotFound, discord.HTTPException) as error:
                uwu_webhooks.pop(channel_id, None)

    webhook = await channel.create_webhook(
        name=UWU_WEBHOOK_NAME,
        reason="Temporary webhook for the ,uwuify /uwuify command",
    )

    uwu_webhooks[channel_id] = {
        "webhook": webhook,
        "timer": None,
    }
    _reset_uwu_webhook_timer(channel_id, webhook)
    return webhook


async def set_uwu_target(
    channel: discord.TextChannel,
    target: discord.Member,
) -> discord.Webhook:
    """Add a target to UWU mode while enforcing a global 5-person cap."""
    try:
        await ensure_user_blacklists_ready()
    except RuntimeError as error:
        raise UserBlacklistStorageUnavailable(str(error)) from error

    if target.id in uwu_user_blacklist:
        raise UwuUserBlacklisted

    async with uwu_target_lock:
        channel_targets = uwu_targets.setdefault(channel.id, set())

        # Already active in this channel: no additional slot is needed.
        if target.id not in channel_targets:
            active_target_ids = get_active_uwu_target_ids()

            # A person already active anywhere does not consume another slot.
            if (
                target.id not in active_target_ids
                and len(active_target_ids) >= MAX_ACTIVE_UWU_TARGETS
            ):
                # Don't leave an empty set behind when the command is rejected.
                if not channel_targets:
                    uwu_targets.pop(channel.id, None)

                raise UwuTargetLimitReached(
                    f"The maximum of {MAX_ACTIVE_UWU_TARGETS} active UWU targets has been reached."
                )

            channel_targets.add(target.id)

    # Create/reuse the webhook outside the cap lock so webhook API calls do not
    # block another target from being checked against the cap.
    try:
        webhook = await get_uwu_webhook(channel)
    except Exception as error:
        async with uwu_target_lock:
            channel_targets = uwu_targets.get(channel.id)
            if channel_targets is not None:
                channel_targets.discard(target.id)
                if not channel_targets:
                    uwu_targets.pop(channel.id, None)
        raise

    _reset_uwu_webhook_timer(channel.id, webhook)
    return webhook


async def disable_uwu_target(channel_id: int) -> bool:
    """Disable UWU mode and delete its temporary webhook immediately."""
    uwu_targets.pop(channel_id, None)
    entry = uwu_webhooks.pop(channel_id, None)
    if entry is None:
        return False

    timer = entry.get("timer")
    if timer is not None and not timer.done():
        timer.cancel()

    webhook = entry.get("webhook")
    if webhook is not None:
        try:
            await webhook.delete(reason="Uwu mode disabled")
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    return True


async def disable_all_uwu_targets() -> int:
    """Disable UWU mode in every currently tracked channel and delete its webhooks."""
    channel_ids = list(uwu_targets.keys() | uwu_webhooks.keys())
    disabled_count = 0

    for channel_id in channel_ids:
        if await disable_uwu_target(channel_id):
            disabled_count += 1

    return disabled_count


async def send_uwu_message(
    channel: discord.TextChannel,
    target: discord.Member,
    content: str,
) -> list[discord.WebhookMessage]:
    if target.id in uwu_user_blacklist:
        raise UwuUserBlacklisted

    """Uwuify text and send it through the temporary webhook.

    The message is checked before and after uwuification so a blocked word/phrase
    can never be sent by this webhook. Role mentions and @everyone/@here are also
    disabled through AllowedMentions.
    """
    # Never send a blacklisted word/phrase.
    ensure_uwu_message_is_allowed(content)

    webhook = await get_uwu_webhook(channel)

    # Protect Discord mentions before uwuify transforms the text.
    # This keeps @users, @roles, and #channels intact and clickable.
    protected_mentions = []

    def protect_mention(match):
        protected_mentions.append(match.group(0))
        # Use Unicode private-use characters only. ASCII placeholder words such
        # as "__UWU_PROTECTED_0__" get transformed by uwuify itself.
        return f"\ue000{len(protected_mentions) - 1}\ue001"

    uwu_input = re.sub(
        r"<@!?\d+>|<@&\d+>|<#\d+>",
        protect_mention,
        content,
    )

    # PyPI uwuify exposes uwu(text, flags=...).
    uwu_text = uwuify.uwu(uwu_input, flags=UWU_FLAGS)
    if not uwu_text:
        uwu_text = "uwu"

    # Restore exact Discord mention tokens before sending.
    for index, original in enumerate(protected_mentions):
        uwu_text = uwu_text.replace(
            f"\ue000{index}\ue001",
            original,
        )

    # Also check the final transformed text so the webhook never sends a blocked
    # word even if the transformation itself somehow creates one.
    ensure_uwu_message_is_allowed(uwu_text)

    sent_messages: list[discord.WebhookMessage] = []
    chunks = [
        uwu_text[index:index + 2000]
        for index in range(0, len(uwu_text), 2000)
    ] or ["uwu"]

    for chunk in chunks:
        sent_messages.append(
            await webhook.send(
                chunk,
                username=target.display_name[:80],
                avatar_url=target.display_avatar.url,
                # Never allow the UWU webhook to ping roles, @everyone, or @here.
                # Normal @user mentions are still allowed.
                allowed_mentions=discord.AllowedMentions(
                    everyone=False,
                    roles=False,
                    users=True,
                    replied_user=False,
                ),
                wait=True,
            )
        )

    _reset_uwu_webhook_timer(channel.id, webhook)
    return sent_messages


async def cleanup_stale_uwu_webhooks() -> None:
    """Delete leftover UWU webhooks created by this bot.

    Active/registered UWU webhooks are skipped. This catches webhooks left
    behind when the normal 5-minute timer fails or the bot restarts.
    """
    if bot.user is None:
        return

    bot_id = bot.user.id
    tracked_ids = {        entry["webhook"].id
        for entry in uwu_webhooks.values()
        if entry.get("webhook") is not None
    }

    for guild in bot.guilds:
        try:
            webhooks = await guild.webhooks()
        except discord.Forbidden:
            continue
        except discord.HTTPException as e:
            continue
        except Exception as e:
            continue

        for webhook in webhooks:
            if webhook.id in tracked_ids:
                continue

            # Only delete webhooks with our exact name and created by this bot.
            if webhook.name != UWU_WEBHOOK_NAME:
                continue
            if webhook.user is None or webhook.user.id != bot_id:
                continue


            try:
                await webhook.delete(reason="Stale UWU webhook cleanup")
                pass
            except discord.NotFound:
                pass
            except discord.Forbidden as e:
                pass
            except discord.HTTPException as e:
                pass
            except Exception as e:
                pass


@tasks.loop(seconds=UWU_WEBHOOK_CLEANUP_INTERVAL_SECONDS)
async def uwu_webhook_cleanup_loop():
    await cleanup_stale_uwu_webhooks()


@uwu_webhook_cleanup_loop.before_loop
async def before_uwu_webhook_cleanup():
    await bot.wait_until_ready()


async def disable_uwu_for_user(user_id: int) -> int:
    """Remove one user from every active UWUIFY channel."""
    affected_channels = [
        channel_id
        for channel_id, target_ids in uwu_targets.items()
        if user_id in target_ids
    ]

    removed = 0
    for channel_id in affected_channels:
        target_ids = uwu_targets.get(channel_id)
        if target_ids is None or user_id not in target_ids:
            continue

        target_ids.discard(user_id)
        removed += 1

        if not target_ids:
            await disable_uwu_target(channel_id)

    return removed


@tree.command(
    name="unuwuify",
    description="Disable UWU mode for a selected member.",
)
@app_commands.describe(member="The member to stop UWUIFYING")
async def unuwuify_command(interaction: discord.Interaction, member: discord.Member):
    """Disable UWU mode for one selected member across all active channels."""
    if uwu_hoodify_user_is_banned(interaction.user):
        await interaction.response.send_message(
            "❌ You are banned from using UWUIFY and HOODIFY.",
            ephemeral=False,
        )
        return
    if not isinstance(interaction.user, discord.Member) or not uwu_user_is_whitelisted(interaction.user):
        await interaction.response.send_message(
            "❌ You need one of the allowed UWU roles to use this command.",
            ephemeral=False,
        )
        return

    disabled_count = await disable_uwu_for_user(member.id)

    await interaction.response.send_message(
        f"✅ UWU mode disabled for {member.mention}. "
        f"Removed them from **{disabled_count}** active channel(s).",
        ephemeral=False,
    )


@uwu_group.command(
    name="count",
    description="Show how many people are currently being UWUified.",
)
async def uwucount_command(interaction: discord.Interaction):
    """Show the global and current-channel UWU target counts."""
    global_count = get_active_uwu_target_count()

    channel_count = 0
    if isinstance(interaction.channel, discord.TextChannel):
        channel_count = len(uwu_targets.get(interaction.channel.id, set()))

    await interaction.response.send_message(
        f"🩷 **UWU count**\n"
        f"Global: **{global_count}/{MAX_ACTIVE_UWU_TARGETS}** people\n"
        f"This channel: **{channel_count}** people",
        ephemeral=False,
    )


@tree.command(
    name="uwucount",
    description="Show how many people are currently being UWUified.",
)
async def uwucount_root_command(interaction: discord.Interaction):
    global_count = get_active_uwu_target_count()
    channel_count = 0
    if isinstance(interaction.channel, discord.TextChannel):
        channel_count = len(uwu_targets.get(interaction.channel.id, set()))

    await interaction.response.send_message(
        f"🩷 **UWU count**\n"
        f"Global: **{global_count}/{MAX_ACTIVE_UWU_TARGETS}** people\n"
        f"This channel: **{channel_count}** people",
        ephemeral=False,
    )
@tree.command(
    name="uwuify",
    description="Add a member to this channel's automatic UWU mode.",
)
@app_commands.describe(
    member="The member whose messages should be automatically uwuified",
    message="Optional one-time message to send through the uwu webhook",
)
async def uwu_command(
    interaction: discord.Interaction,
    member: discord.Member,
    message: str | None = None,
):
    """Enable automatic uwu replacement for a selected member in this channel."""
    if uwu_hoodify_user_is_banned(interaction.user):
        await interaction.response.send_message(
            "❌ You are banned from using UWUIFY and HOODIFY.",
            ephemeral=False,
        )
        return
    if not isinstance(interaction.user, discord.Member) or not uwu_user_is_whitelisted(interaction.user):
        await interaction.response.send_message(
            "❌ You need one of the allowed UWU roles to use this command.",
            ephemeral=False,
        )
        return

    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message(
            "❌ This command can only be used in a normal text channel.",
            ephemeral=False,
        )
        return

    bot_member = interaction.guild.me if interaction.guild is not None else None
    permissions = (
        interaction.channel.permissions_for(bot_member)
        if bot_member is not None
        else None
    )
    # A GitHub-backed blacklist check can involve network I/O. Defer the
    # interaction first so Discord's response window cannot expire.
    await interaction.response.defer(ephemeral=False)

    if (
        permissions is None
        or not permissions.manage_messages
        or not permissions.manage_webhooks
    ):
        await interaction.followup.send(
            "❌ I need **Manage Messages** and **Manage Webhooks** permission in this channel.",
            ephemeral=False,
        )
        return

    try:
        await set_uwu_target(interaction.channel, member)

        if message:
            try:
                await send_uwu_message(interaction.channel, member, message)
            except UwuMessageBlocked as blocked_error:
                await interaction.followup.send(
                    f"❌ The UWU mode was enabled, but the one-time message was not sent because it contains a blacklisted word/phrase: `{blocked_error}`",
                    ephemeral=False,
                )
                return

        active_count = get_active_uwu_target_count()
        await interaction.followup.send(
            f"✅ Uwu mode is active for {member.mention} in this channel. "
            f"Active people: **{active_count}/{MAX_ACTIVE_UWU_TARGETS}**.\n"
            "You can add more people with another `/uwuify` command. "
            "The temporary webhook will be deleted after 5 minutes without use.",
            ephemeral=False,
        )
    except UwuUserBlacklisted:
        await interaction.followup.send(            f"❌ {member.mention} is blacklisted from using UWUIFY.",
            ephemeral=False,
        )
    except UserBlacklistStorageUnavailable as error:
        await interaction.followup.send(
            "❌ I could not verify the UWUIFY blacklist from GitHub, "
            f"so I will not activate this target. Error: `{error}`",
            ephemeral=False,
        )
    except UwuTargetLimitReached:
        await interaction.followup.send(
            f"❌ The global limit of {MAX_ACTIVE_UWU_TARGETS} UWUified people has been reached. "
            "Use `,unuwuify @user` or `/unuwuify @user` to disable UWU for one member, or wait for a slot to expire.",
            ephemeral=False,
        )
    except discord.Forbidden:
        await interaction.followup.send(
            "❌ I need **Manage Messages** and **Manage Webhooks** permission in this channel/server.",
            ephemeral=False,
        )
    except discord.HTTPException as e:
        await interaction.followup.send(
            f"❌ Discord rejected the uwu webhook request: `{e}`",
            ephemeral=False,
        )
    except Exception as error:
        await interaction.followup.send(
            "❌ The UWUIFY mode could not be enabled. "
            f"Error: {type(error).__name__}: {error}",
            ephemeral=False,
        )

