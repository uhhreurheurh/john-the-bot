"""HOODIFY feature module."""

from bot import *

_HOODIFY_DATA = json.loads(Path(__file__).with_name("hoodify_words.json").read_text(encoding="utf-8"))
HOOD_REPLACEMENTS = [(item["pattern"], item["replacement"]) for item in _HOODIFY_DATA.get("replacements", [])]
HOOD_OPENERS = list(_HOODIFY_DATA.get("openers", []))
HOOD_MID_PHRASES = list(_HOODIFY_DATA.get("mid_phrases", []))
HOOD_CLOSERS = list(_HOODIFY_DATA.get("closers", []))
HOOD_EXTRAS = list(_HOODIFY_DATA.get("extras", []))

HOOD_WEBHOOK_NAME = "Hoodify Relay"
HOOD_WEBHOOK_IDLE_SECONDS = 5 * 60

# Members with one of these roles may enable/disable HOODIFY.
# Set to the same roles as UWU, or change them independently.
HOOD_ALLOWED_ROLE_IDS = {
    1518416402141417472,
    1378810715611336914,
    1377468541779050636,
}

# Separate blacklist for HOODIFY messages.
HOOD_WORD_BLACKLIST = {
    # "example",
}

# Maximum number of unique people who can be actively HOODIFIED at once.
MAX_ACTIVE_HOOD_TARGETS = 5

# channel_id -> {"webhook": discord.Webhook, "timer": asyncio.Task | None}
hood_webhooks: dict[int, dict] = {}

# channel_id -> set of target member IDs.
hood_targets: dict[int, set[int]] = {}

# Protect the global HOODIFY target cap from simultaneous commands.
hood_target_lock = asyncio.Lock()

# Load the editable blacklist after the legacy inline default is declared.
HOOD_WORD_BLACKLIST = set(_HOODIFY_DATA.get("word_blacklist", []))

def hoodify_text(content: str) -> str:
    """Convert ordinary text into varied casual internet slang.

    This is general casual/internet slang and does not imitate a racial or
    ethnic dialect. The output has controlled randomness so repeated messages
    do not always receive the exact same wording.
    """
    if not content:
        return content

    result = content
    protected = []

    def protect(match):
        protected.append(match.group(0))
        # ASCII placeholders can themselves be modified by slang replacements.
        # Private-use Unicode characters are left untouched.
        return f"\ue002{len(protected) - 1}\ue003"

    result = re.sub(
        r"https?://\S+|<@!?\d+>|<@&\d+>|<#\d+>|<a?:\w+:\d+>",
        protect,
        result,
    )

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
        # Random seasoning. Each has a probability so not every message gets
        # every extra phrase.
        if random.random() < 0.42 and len(words) >= 3:
            opener = random.choice(HOOD_OPENERS)
            result = f"{opener}, {result}"

        if random.random() < 0.34 and len(words) >= 5:
            parts = result.split()
            phrase = random.choice(HOOD_MID_PHRASES)
            pos = random.randint(1, max(1, len(parts) - 1))
            parts.insert(pos, phrase)
            result = " ".join(parts)

        if random.random() < 0.50:
            closer = random.choice(HOOD_CLOSERS)
            if not re.search(
                r"(?:\bfr\b|\bngl\b|\blowkey\b|\bdeadass\b|no cap|bet|on god|😭|💀)$",
                result,
                flags=re.IGNORECASE,
            ):
                if result.endswith((".", "!", "?")):
                    result = result[:-1].rstrip() + f" {closer}" + result[-1]
                else:
                    result += f" {closer}"

        # Occasionally add one small standalone slang word, but never stack
        # more than one so the message stays readable.
        if random.random() < 0.22 and len(words) >= 4:
            extra = random.choice(HOOD_EXTRAS)
            if extra.lower() not in result.lower():
                result += f" {extra}"

    result = re.sub(r"[ \t]{2,}", " ", result).strip()

    for index, original in enumerate(protected):
        result = result.replace(f"\ue002{index}\ue003", original)

    # For a message where nothing matched and random seasoning didn't fire,
    # use one of several fallbacks instead of always adding "fr".
    if result == content.strip() and result:
        result = random.choice([
            f"yo, {result}",
            f"{result} ngl",
            f"{result} no cap",
            f"{result} fr",
            f"{result} lowkey",
        ])

    return result


def hood_user_is_whitelisted(member: discord.Member | discord.User) -> bool:
    """Return True when the member has at least one allowed HOODIFY role."""
    return any(
        role.id in HOOD_ALLOWED_ROLE_IDS
        for role in getattr(member, "roles", ())
    )

def get_active_hood_target_ids(exclude_channel_id: int | None = None) -> set[int]:
    active: set[int] = set()
    for channel_id, target_ids in hood_targets.items():
        if channel_id == exclude_channel_id:
            continue
        active.update(target_ids)
    return active

def get_active_hood_target_count() -> int:
    return len(get_active_hood_target_ids())

class HoodTargetLimitReached(Exception):
    """Raised when adding a HOODIFY target would exceed the global cap."""

class HoodMessageBlocked(Exception):
    """Raised when a HOODIFY message contains blocked content."""

def get_blacklisted_hood_word(content: str) -> str | None:
    if not content or not HOOD_WORD_BLACKLIST:
        return None
    for blocked in HOOD_WORD_BLACKLIST:
        blocked = str(blocked).strip()
        if not blocked:
            continue
        pattern = rf"(?<!\\w){re.escape(blocked)}(?!\\w)"
        if re.search(pattern, content, flags=re.IGNORECASE):
            return blocked
    return None

def ensure_hood_message_is_allowed(content: str) -> None:
    blocked = get_blacklisted_hood_word(content)
    if blocked is not None:
        raise HoodMessageBlocked(blocked)

async def _delete_hood_webhook_after_idle(
    channel_id: int,
    webhook: discord.Webhook,
) -> None:
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
    channel_id = channel.id
    entry = hood_webhooks.get(channel_id)

    if entry is not None:
        webhook = entry.get("webhook")
        if webhook is not None:
            try:
                await webhook.fetch()
                _reset_hood_webhook_timer(channel_id, webhook)
                return webhook
            except (discord.NotFound, discord.HTTPException):
                hood_webhooks.pop(channel_id, None)

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
    try:
        await ensure_user_blacklists_ready()
    except RuntimeError as error:
        raise UserBlacklistStorageUnavailable(str(error)) from error

    if target.id in hood_user_blacklist:
        raise HoodUserBlacklisted

    async with hood_target_lock:
        channel_targets = hood_targets.setdefault(channel.id, set())

        if target.id not in channel_targets:
            active_target_ids = get_active_hood_target_ids()
            if (
                target.id not in active_target_ids
                and len(active_target_ids) >= MAX_ACTIVE_HOOD_TARGETS
            ):
                if not channel_targets:
                    hood_targets.pop(channel.id, None)
                raise HoodTargetLimitReached(
                    f"The maximum of {MAX_ACTIVE_HOOD_TARGETS} active HOODIFY "
                    "targets has been reached."
                )
            channel_targets.add(target.id)

    webhook = await get_hood_webhook(channel)
    _reset_hood_webhook_timer(channel.id, webhook)
    return webhook

async def disable_hood_target(channel_id: int) -> bool:
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
    channel_ids = list(hood_targets.keys() | hood_webhooks.keys())
    disabled_count = 0
    for channel_id in channel_ids:
        if await disable_hood_target(channel_id):
            disabled_count += 1
    return disabled_count

async def send_hood_message(
    channel: discord.TextChannel,
    target: discord.Member,    content: str,
) -> list[discord.WebhookMessage]:
    if target.id in hood_user_blacklist:
        raise HoodUserBlacklisted

    ensure_hood_message_is_allowed(content)

    webhook = await get_hood_webhook(channel)
    hood_text = hoodify_text(content)

    if not hood_text:
        hood_text = "yo"

    ensure_hood_message_is_allowed(hood_text)

    sent_messages: list[discord.WebhookMessage] = []
    chunks = [
        hood_text[index:index + 2000]
        for index in range(0, len(hood_text), 2000)
    ] or ["yo"]

    for chunk in chunks:
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

@tasks.loop(seconds=HOOD_WEBHOOK_CLEANUP_INTERVAL_SECONDS if "HOOD_WEBHOOK_CLEANUP_INTERVAL_SECONDS" in globals() else 60)
async def hood_webhook_cleanup_loop():
    await cleanup_stale_hood_webhooks()

@hood_webhook_cleanup_loop.before_loop
async def before_hood_webhook_cleanup():
    await bot.wait_until_ready()

# channel_id -> {"webhook": discord.Webhook, "timer": asyncio.Task | None}
uwu_webhooks: dict[int, dict] = {}

# channel_id -> set of target member IDs.
# Multiple people can be UWUified in the same channel at once.
uwu_targets: dict[int, set[int]] = {}

# Protect the global 5-person cap from simultaneous commands.
uwu_target_lock = asyncio.Lock()



@tree.command(
    name="unhoodify",
    description="Disable HOODIFY for a selected member.",
)
@app_commands.describe(member="The member to stop HOODIFYING")
async def unhoodify_command(interaction: discord.Interaction, member: discord.Member):
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
    if bot_member is None or not interaction.channel.permissions_for(bot_member).manage_messages:
        await interaction.response.send_message(
            "❌ I need **Manage Messages** permission in this channel to replace messages.",
            ephemeral=False,
        )
        return

    try:
        await set_hood_target(interaction.channel, member)

        if message:
            try:
                await send_hood_message(interaction.channel, member, message)
            except HoodMessageBlocked as blocked_error:
                await interaction.response.send_message(
                    f"❌ HOODIFY was enabled, but the one-time message was not sent "
                    f"because it contains a blacklisted word/phrase: `{blocked_error}`",
                    ephemeral=False,
                )
                return

        active_count = get_active_hood_target_count()
        await interaction.response.send_message(
            f"✅ HOODIFY is active for {member.mention} in this channel. "
            f"Active people: **{active_count}/{MAX_ACTIVE_HOOD_TARGETS}**.\n"
            "You can add more people with another `/hoodify` command. "
            "The temporary webhook will be deleted after 5 minutes without use.",
            ephemeral=False,
        )
    except HoodUserBlacklisted:
        await interaction.response.send_message(
            f"❌ {member.mention} is blacklisted from using HOODIFY.",
            ephemeral=False,
        )
    except UserBlacklistStorageUnavailable as error:
        await interaction.response.send_message(
            "❌ I could not verify the HOODIFY blacklist from GitHub, "
            f"so I will not activate this target. Error: `{error}`",
            ephemeral=False,
        )
    except HoodTargetLimitReached:
        await interaction.response.send_message(
            f"❌ The global limit of {MAX_ACTIVE_HOOD_TARGETS} HOODIFIED people has been reached. "
            "Use `,unhoodify @user` or `/unhoodify @user` to disable HOODIFY for one member.",
            ephemeral=False,
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ I need **Manage Messages** and **Manage Webhooks** permission in this channel/server.",
            ephemeral=False,
        )
    except discord.HTTPException as e:
        await interaction.response.send_message(
            f"❌ Discord rejected the HOODIFY webhook request: `{e}`",
            ephemeral=False,
        )
    except Exception:
        await interaction.response.send_message(
            "❌ The HOODIFY mode could not be enabled.",
            ephemeral=False,
        )

