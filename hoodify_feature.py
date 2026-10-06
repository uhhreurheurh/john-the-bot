"""HOODIFY feature module."""

from bot import *

_HOODIFY_DATA = json.loads(Path(__file__).with_name("hoodify_words.json").read_text(encoding="utf-8"))
HOOD_REPLACEMENTS = [(item["pattern"], item["replacement"]) for item in _HOODIFY_DATA.get("replacements", [])]
HOOD_OPENERS = list(_HOODIFY_DATA.get("openers", []))
HOOD_MID_PHRASES = list(_HOODIFY_DATA.get("mid_phrases", []))
HOOD_CLOSERS = list(_HOODIFY_DATA.get("closers", []))
HOOD_EXTRAS = list(_HOODIFY_DATA.get("extras", []))
HOOD_WORD_BLACKLIST = set(_HOODIFY_DATA.get("word_blacklist", []))

HOOD_WEBHOOK_NAME = "Hoodify Relay"
HOOD_WEBHOOK_IDLE_SECONDS = 5 * 60
HOOD_WEBHOOK_CLEANUP_INTERVAL_SECONDS = 60

# Shared whitelist for both UWUIFY and HOODIFY.
HOOD_ALLOWED_ROLE_IDS = textify_whitelist


def hood_user_is_whitelisted(member: discord.Member | discord.User) -> bool:
    """Return True when the member has at least one shared Textify whitelist role."""
    role_ids = {
        int(getattr(role, "id", 0))
        for role in getattr(member, "roles", ())
    }
    return any(role_id in HOOD_ALLOWED_ROLE_IDS for role_id in role_ids)


def get_active_hood_target_ids(exclude_channel_id: int | None = None) -> set[int]:
    """Return unique target IDs currently active in HOODIFY."""
    active: set[int] = set()
    for channel_id, target_ids in hood_targets.items():
        if channel_id == exclude_channel_id:
            continue
        active.update(target_ids)
    return active


def get_active_hood_target_count() -> int:
    """Return the number of unique active HOODIFY targets."""
    return len(get_active_hood_target_ids())


class HoodTargetLimitReached(Exception):
    """Raised when adding a HOODIFY target would exceed the global cap."""


class HoodUwuConflict(Exception):
    """Raised when a target is already active in UWUIFY."""


class HoodMessageBlocked(Exception):
    """Raised when a HOODIFY message contains a blacklisted word/phrase."""


def get_blacklisted_hood_word(content: str) -> str | None:
    """Return the first blocked word/phrase found in content, or None."""
    if not content or not HOOD_WORD_BLACKLIST:
        return None

    for blocked in HOOD_WORD_BLACKLIST:
        blocked = str(blocked).strip()
        if not blocked:
            continue

        pattern = rf"(?<!\w){re.escape(blocked)}(?!\w)"
        if re.search(pattern, content, flags=re.IGNORECASE):
            return blocked

    return None


def ensure_hood_message_is_allowed(content: str) -> None:
    """Raise HoodMessageBlocked when the content is not allowed."""
    blocked = get_blacklisted_hood_word(content)
    if blocked is not None:
        raise HoodMessageBlocked(blocked)


async def _delete_hood_webhook_after_idle(
    channel_id: int,
    webhook: discord.Webhook,
) -> None:
    """Delete a temporary HOODIFY webhook after 5 minutes of inactivity."""
    try:
        await asyncio.sleep(HOOD_WEBHOOK_IDLE_SECONDS)

        entry = hood_webhooks.get(channel_id)
        if entry is not None and entry.get("webhook") is webhook:
            try:
                await webhook.delete(reason="Hoodify webhook unused for 5 minutes")
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
            finally:
                hood_webhooks.pop(channel_id, None)
                hood_targets.pop(channel_id, None)
    except asyncio.CancelledError:
        return


def _reset_hood_webhook_timer(channel_id: int, webhook: discord.Webhook) -> None:
    entry = hood_webhooks.get(channel_id)
    if entry is None or entry.get("webhook") is not webhook:
        return

    old_timer = entry.get("timer")
    if old_timer is not None and not old_timer.done():
        old_timer.cancel()

    entry["timer"] = asyncio.create_task(
        _delete_hood_webhook_after_idle(channel_id, webhook)
    )


async def get_hood_webhook(channel: discord.TextChannel) -> discord.Webhook:
    """Get or create the temporary HOODIFY webhook for a channel."""
    channel_id = channel.id
    entry = hood_webhooks.get(channel_id)

    if entry is not None:
        webhook = entry.get("webhook")
        if webhook is not None:
            # Reuse the cached webhook immediately. Fetching it on every
        # message added an unnecessary Discord API round trip and noticeable latency.
        _reset_hood_webhook_timer(channel_id, webhook)
        return webhook

    webhook = await channel.create_webhook(
        name=HOOD_WEBHOOK_NAME,
        reason="Temporary webhook for the ,hoodify /hoodify command",
    )

    hood_webhooks[channel_id] = {
        "webhook": webhook,
        "timer": None,
    }
    _reset_hood_webhook_timer(channel_id, webhook)
    return webhook


async def set_hood_target(
    channel: discord.TextChannel,
    target: discord.Member,
) -> discord.Webhook:
    """Enable HOODIFY for a target and return the channel webhook."""
    try:
        await ensure_user_blacklists_ready()
    except RuntimeError as error:
        raise UserBlacklistStorageUnavailable(str(error)) from error

    if target.id in hood_user_blacklist:
        raise HoodUserBlacklisted

    # Import these lazily because hoodify_feature.py and bot.py import each
    # other during startup. By the time a command runs, bot.py has finished
    # importing uwuify_feature and created the shared Textify mode lock.
    from bot import TEXTIFY_MODE_LOCK, uwuify_feature

    # Keep the UWU/HOODIFY conflict rule atomic with all other Textify mode changes.
    async with TEXTIFY_MODE_LOCK:
        async with hood_target_lock:
            channel_targets = hood_targets.setdefault(channel.id, set())

            if target.id not in channel_targets:
                active_target_ids = get_active_hood_target_ids()

                # A target already active in another HOODIFY channel uses the
                # same global slot and does not consume an extra one.
                if (
                    target.id not in active_target_ids
                    and len(active_target_ids) >= MAX_ACTIVE_HOOD_TARGETS
                ):
                    if not channel_targets:
                        hood_targets.pop(channel.id, None)
                    raise HoodTargetLimitReached(
                        f"The maximum of {MAX_ACTIVE_HOOD_TARGETS} active HOODIFY targets has been reached."
                    )

                # Never allow the same person to run UWUIFY and HOODIFY together.
                if any(
                    target.id in target_ids
                    for target_ids in uwuify_feature.uwu_targets.values()
                ):
                    if not channel_targets:
                        hood_targets.pop(channel.id, None)
                    raise HoodUwuConflict

                channel_targets.add(target.id)

    try:
        webhook = await get_hood_webhook(channel)
    except Exception:
        async with hood_target_lock:
            channel_targets = hood_targets.get(channel.id)
            if channel_targets is not None:
                channel_targets.discard(target.id)
                if not channel_targets:
                    hood_targets.pop(channel.id, None)
        raise

    _reset_hood_webhook_timer(channel.id, webhook)
    return webhook


async def disable_hood_target(channel_id: int) -> bool:
    """Disable HOODIFY for one channel and delete its temporary webhook."""
    hood_targets.pop(channel_id, None)

    entry = hood_webhooks.pop(channel_id, None)
    if entry is None:
        return False

    timer = entry.get("timer")
    if timer is not None and not timer.done():
        timer.cancel()

    webhook = entry.get("webhook")
    if webhook is not None:
        try:
            await webhook.delete(reason="Hoodify mode disabled")
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    return True


async def disable_all_hood_targets() -> int:
    """Disable HOODIFY everywhere and delete its temporary webhooks."""
    channel_ids = list(hood_targets.keys() | hood_webhooks.keys())
    disabled_count = 0

    for channel_id in channel_ids:
        if await disable_hood_target(channel_id):
            disabled_count += 1

    return disabled_count


async def disable_hood_for_user(user_id: int) -> int:
    """Remove one user from every active HOODIFY channel."""
    affected_channels = [
        channel_id
        for channel_id, target_ids in hood_targets.items()
        if user_id in target_ids
    ]

    removed = 0
    for channel_id in affected_channels:
        target_ids = hood_targets.get(channel_id)
        if target_ids is None or user_id not in target_ids:
            continue

        target_ids.discard(user_id)
        removed += 1

        if not target_ids:
            await disable_hood_target(channel_id)

    return removed

# Maximum number of unique people who can be actively HOODIFIED at once.
MAX_ACTIVE_HOOD_TARGETS = 5

# channel_id -> {"webhook": discord.Webhook, "timer": asyncio.Task | None}
hood_webhooks: dict[int, dict] = {}

# channel_id -> set of target member IDs.
hood_targets: dict[int, set[int]] = {}

# Protect the global HOODIFY target cap from simultaneous commands.
hood_target_lock = asyncio.Lock()


def hoodify_text(content: str) -> str:
    """Convert ordinary text while preserving URLs and Discord tokens exactly."""
    if not content:
        return content

    token_re = re.compile(r"https?://\S+|<@!?\d+>|<@&\d+>|<#\d+>|<a?:\w+:\d+>")
    parts = []
    last = 0
    content = str(content)

    for match in token_re.finditer(content):
        if match.start() > last:
            segment = content[last:match.start()]
            parts.append(_hoodify_segment(segment))
        parts.append(match.group(0))
        last = match.end()

    if last < len(content):
        parts.append(_hoodify_segment(content[last:]))

    return "".join(parts)


def _hoodify_segment(content: str) -> str:
    result = content
    for pattern, replacement in HOOD_REPLACEMENTS:
        if isinstance(replacement, (tuple, list)):
            replacement = random.choice(replacement)
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)

    result = re.sub(r"!{3,}", "!!", result)
    result = re.sub(r"\?{3,}", "??", result)
    result = re.sub(r"\.{4,}", "...", result)
    result = re.sub(r"[ \t]{2,}", " ", result).strip()

    words = result.split()
    if words:
        if random.random() < 0.42 and len(words) >= 3:
            result = f"{random.choice(HOOD_OPENERS)}, {result}"
        if random.random() < 0.34 and len(words) >= 5:
            parts = result.split()
            phrase = random.choice(HOOD_MID_PHRASES)
            pos = random.randint(1, max(1, len(parts) - 1))
            parts.insert(pos, phrase)
            result = " ".join(parts)
        if random.random() < 0.18 and len(words) >= 4:
            result = f"{result} {random.choice(HOOD_CLOSERS)}"

    return result




def hoodify_embed(embed: discord.Embed) -> discord.Embed:
    """Copy an embed while transforming text fields but preserving every URL."""
    data = embed.to_dict()

    def transform(value):
        if not value:
            return value
        # hoodify_text already preserves URLs, mentions, and Discord tokens.
        value = hoodify_text(str(value))
        ensure_hood_message_is_allowed(value)
        return value

    for key in ("title", "description"):
        if key in data and data[key] is not None:
            data[key] = transform(data[key])
    if data.get("author") and data["author"].get("name") is not None:
        data["author"]["name"] = transform(data["author"]["name"])
    if data.get("footer") and data["footer"].get("text") is not None:
        data["footer"]["text"] = transform(data["footer"]["text"])
    for field in data.get("fields", []):
        if field.get("name") is not None:
            field["name"] = transform(field["name"])
        if field.get("value") is not None:
            field["value"] = transform(field["value"])
    # Discord link embeds often put GIF/media previews in the thumbnail.
    # Promote that media to the main image so the webhook shows the full-size media.
    if data.get("thumbnail") and data.get("thumbnail", {}).get("url") and not data.get("image", {}).get("url"):
        data["image"] = {"url": data["thumbnail"]["url"]}

    return discord.Embed.from_dict(data)


async def send_hood_message(
    channel: discord.TextChannel,
    target: discord.Member,
    content: str,
    embeds: list[discord.Embed] | None = None,
) -> list[discord.WebhookMessage]:
    if target.id in hood_user_blacklist:
        raise HoodUserBlacklisted

    ensure_hood_message_is_allowed(content)
    embeds = embeds or []
    image_embed_banned = has_image_embed_ban_role(target)
    transformed_embeds = [] if image_embed_banned else [hoodify_embed(embed) for embed in embeds]

    webhook = await get_hood_webhook(channel)
    hood_text = hoodify_text(content)

    if not hood_text:
        hood_text = "yo"

    ensure_hood_message_is_allowed(hood_text)

    sent_messages: list[discord.WebhookMessage] = []
    chunks = (
        [hood_text[index:index + 2000] for index in range(0, len(hood_text), 2000)]
        if hood_text
        else [None]
    )

    for chunk_index, chunk in enumerate(chunks):
        sent_messages.append(
            await webhook.send(
                chunk,
                username=target.display_name[:80],
                avatar_url=target.display_avatar.url,
                allowed_mentions=discord.AllowedMentions(
                    everyone=False,
                    roles=False,
                    users=True,
                    replied_user=False,
                ),
                embeds=transformed_embeds if chunk_index == 0 else [],
                suppress_embeds=image_embed_banned,
                wait=True,
            )
        )

    _reset_hood_webhook_timer(channel.id, webhook)
    return sent_messages

async def cleanup_stale_hood_webhooks() -> None:
    if bot.user is None:
        return

    bot_id = bot.user.id
    tracked_ids = {
        entry["webhook"].id
        for entry in hood_webhooks.values()
        if entry.get("webhook") is not None
    }

    for guild in bot.guilds:
        try:
            webhooks = await guild.webhooks()
        except (discord.Forbidden, discord.HTTPException):
            continue

        for webhook in webhooks:
            if webhook.id in tracked_ids:
                continue
            if webhook.name != HOOD_WEBHOOK_NAME:
                continue
            if webhook.user is None or webhook.user.id != bot_id:
                continue

            try:
                await webhook.delete(reason="Stale Hoodify webhook cleanup")
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

@tasks.loop(seconds=HOOD_WEBHOOK_CLEANUP_INTERVAL_SECONDS)
async def hood_webhook_cleanup_loop():
    await cleanup_stale_hood_webhooks()

@hood_webhook_cleanup_loop.before_loop
async def before_hood_webhook_cleanup():
    await bot.wait_until_ready()

@tree.command(
    name="unhoodify",
    description="Disable HOODIFY for a selected member.",
)
@app_commands.describe(member="The member to stop HOODIFYING")
async def unhoodify_command(
    interaction: discord.Interaction,
    member: discord.Member,
):
    if uwu_hoodify_user_is_banned(interaction.user):
        await interaction.response.send_message(
            "❌ You are banned from using UWUIFY and HOODIFY.",
            ephemeral=False,
        )
        return
    if not isinstance(interaction.user, discord.Member) or not hood_user_is_whitelisted(interaction.user):
        await interaction.response.send_message(
            "❌ You need one of the allowed HOODIFY roles to use this command.",
            ephemeral=False,
        )
        return

    disabled_count = await disable_hood_for_user(member.id)
    await interaction.response.send_message(
        f"✅ HOODIFY disabled for {member.mention}. "
        f"Removed them from **{disabled_count}** active channel(s).",
        ephemeral=False,
    )


@hood_group.command(
    name="count",
    description="Show how many people are currently being HOODIFIED.",
)
async def hoodcount_command(interaction: discord.Interaction):
    global_count = get_active_hood_target_count()
    channel_count = 0
    if isinstance(interaction.channel, discord.TextChannel):
        channel_count = len(hood_targets.get(interaction.channel.id, set()))

    await interaction.response.send_message(
        f"🖤 **HOODIFY count**\n"
        f"Global: **{global_count}/{MAX_ACTIVE_HOOD_TARGETS}** people\n"
        f"This channel: **{channel_count}** people",
        ephemeral=False,
    )


@tree.command(
    name="hoodcount",
    description="Show how many people are currently being HOODIFIED.",
)
async def hoodcount_root_command(interaction: discord.Interaction):
    global_count = get_active_hood_target_count()
    channel_count = 0
    if isinstance(interaction.channel, discord.TextChannel):
        channel_count = len(hood_targets.get(interaction.channel.id, set()))

    await interaction.response.send_message(
        f"🖤 **HOODIFY count**\n"
        f"Global: **{global_count}/{MAX_ACTIVE_HOOD_TARGETS}** people\n"
        f"This channel: **{channel_count}** people",
        ephemeral=False,
    )
@tree.command(
    name="hoodify",
    description="Add a member to this channel's automatic HOODIFY mode.",
)
@app_commands.describe(
    member="The member whose messages should be automatically hoodified",
    message="Optional one-time message to send through the hoodify webhook",
)
async def hoodify_command(
    interaction: discord.Interaction,
    member: discord.Member,
    message: str | None = None,
):
    if uwu_hoodify_user_is_banned(interaction.user):
        await interaction.response.send_message(
            "❌ You are banned from using UWUIFY and HOODIFY.",
            ephemeral=False,
        )
        return

    if not isinstance(interaction.user, discord.Member) or not hood_user_is_whitelisted(interaction.user):
        await interaction.response.send_message(
            "❌ You need one of the allowed HOODIFY roles to use this command.",
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
        await set_hood_target(interaction.channel, member)

        if message:
            try:
                await send_hood_message(interaction.channel, member, message)
            except HoodMessageBlocked as blocked_error:
                await interaction.followup.send(
                    f"❌ HOODIFY was enabled, but the one-time message was not sent "
                    f"because it contains a blacklisted word/phrase: `{blocked_error}`",
                    ephemeral=False,
                )
                return

        active_count = get_active_hood_target_count()
        await interaction.followup.send(
            f"✅ HOODIFY is active for {member.mention} in this channel. "
            f"Active people: **{active_count}/{MAX_ACTIVE_HOOD_TARGETS}**.\n"
            "You can add more people with another `/hoodify` command. "
            "The temporary webhook will be deleted after 5 minutes without use.",
            ephemeral=False,
        )
    except HoodUserBlacklisted:
        await interaction.followup.send(
            f"❌ {member.mention} is blacklisted from using HOODIFY.",
            ephemeral=False,
        )
    except UserBlacklistStorageUnavailable as error:
        await interaction.followup.send(
            "❌ I could not verify the HOODIFY blacklist from GitHub, "
            f"so I will not activate this target. Error: `{error}`",
            ephemeral=False,
        )
    except HoodTargetLimitReached:
        await interaction.followup.send(
            f"❌ The global limit of {MAX_ACTIVE_HOOD_TARGETS} HOODIFIED people has been reached. "
            "Use `,unhoodify @user` or `/unhoodify @user` to disable HOODIFY for one member.",
            ephemeral=False,
        )
    except discord.Forbidden:
        await interaction.followup.send(
            "❌ I need **Manage Messages** and **Manage Webhooks** permission in this channel/server.",
            ephemeral=False,
        )
    except discord.HTTPException as e:
        await interaction.followup.send(
            f"❌ Discord rejected the HOODIFY webhook request: `{e}`",
            ephemeral=False,
        )
    except Exception:
        await interaction.followup.send(
            "❌ The HOODIFY mode could not be enabled.",
            ephemeral=False,
        )


