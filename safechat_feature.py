"""SAFECHAT feature module.

SafeChat is a non-transforming relay based on the existing HOODIFY behavior:
- preserves the original message text exactly
- preserves the target's display name exactly
- preserves embeds without modifying their text
- uses temporary per-channel webhooks
- uses the shared Textify blacklist
- has no global target cap
"""

from bot import *

SAFECHAT_WEBHOOK_NAME = "SafeChat Relay"
SAFECHAT_WEBHOOK_IDLE_SECONDS = 5 * 60
SAFECHAT_WEBHOOK_CLEANUP_INTERVAL_SECONDS = 60
MAX_ACTIVE_SAFECHAT_TARGETS = None  # SafeChat has no target cap.

# channel_id -> {"webhook": discord.Webhook, "timer": asyncio.Task | None}
safechat_webhooks: dict[int, dict] = {}

# channel_id -> set of target member IDs.
safechat_targets: dict[int, set[int]] = {}

safechat_target_lock = asyncio.Lock()


def get_active_safechat_target_ids(exclude_channel_id: int | None = None) -> set[int]:
    active: set[int] = set()
    for channel_id, target_ids in safechat_targets.items():
        if channel_id == exclude_channel_id:
            continue
        active.update(target_ids)
    return active


def get_active_safechat_target_count() -> int:
    return len(get_active_safechat_target_ids())


class SafeChatTargetLimitReached(Exception):
    pass


class SafeChatUserBlacklisted(Exception):
    pass


class SafeChatUwuConflict(Exception):
    pass


class SafeChatHoodConflict(Exception):
    pass


async def _delete_safechat_webhook_after_idle(
    channel_id: int,
    webhook: discord.Webhook,
) -> None:
    try:
        await asyncio.sleep(SAFECHAT_WEBHOOK_IDLE_SECONDS)

        entry = safechat_webhooks.get(channel_id)
        if entry is not None and entry.get("webhook") is webhook:
            try:
                await webhook.delete(reason="SafeChat webhook unused for 5 minutes")
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
            finally:
                safechat_webhooks.pop(channel_id, None)
                safechat_targets.pop(channel_id, None)
    except asyncio.CancelledError:
        return


def _reset_safechat_webhook_timer(
    channel_id: int,
    webhook: discord.Webhook,
) -> None:
    entry = safechat_webhooks.get(channel_id)
    if entry is None or entry.get("webhook") is not webhook:
        return

    old_timer = entry.get("timer")
    if old_timer is not None and not old_timer.done():
        old_timer.cancel()

    entry["timer"] = asyncio.create_task(
        _delete_safechat_webhook_after_idle(channel_id, webhook)
    )


async def get_safechat_webhook(channel: discord.TextChannel) -> discord.Webhook:
    """Get or create SafeChat's temporary relay webhook."""
    channel_id = channel.id
    entry = safechat_webhooks.get(channel_id)

    if entry is not None:
        webhook = entry.get("webhook")
        if webhook is not None:
            try:
                await webhook.fetch()
                _reset_safechat_webhook_timer(channel_id, webhook)
                return webhook
            except (discord.NotFound, discord.HTTPException):
                safechat_webhooks.pop(channel_id, None)

    webhook = await channel.create_webhook(
        name=SAFECHAT_WEBHOOK_NAME,
        reason="Temporary webhook for the SafeChat command",
    )

    safechat_webhooks[channel_id] = {
        "webhook": webhook,
        "timer": None,
    }
    _reset_safechat_webhook_timer(channel_id, webhook)
    return webhook


async def set_safechat_target(
    channel: discord.TextChannel,
    target: discord.Member,
) -> discord.Webhook:
    """Enable SafeChat for a target and return the relay webhook."""
    try:
        await ensure_user_blacklists_ready()
    except RuntimeError as error:
        raise UserBlacklistStorageUnavailable(str(error)) from error

    if target.id in safechat_ban:
        raise SafeChatUserBlacklisted

    # Keep SafeChat from being active alongside either text-transform mode.
    from bot import TEXTIFY_MODE_LOCK, uwuify_feature, hoodify_feature

    async with TEXTIFY_MODE_LOCK:
        async with safechat_target_lock:
            channel_targets = safechat_targets.setdefault(channel.id, set())

            if target.id not in channel_targets:
                if any(
                    target.id in target_ids
                    for target_ids in uwuify_feature.uwu_targets.values()
                ):
                    if not channel_targets:
                        safechat_targets.pop(channel.id, None)
                    raise SafeChatUwuConflict

                if any(
                    target.id in target_ids
                    for target_ids in hoodify_feature.hood_targets.values()
                ):
                    if not channel_targets:
                        safechat_targets.pop(channel.id, None)
                    raise SafeChatHoodConflict

                channel_targets.add(target.id)

    try:
        webhook = await get_safechat_webhook(channel)
    except Exception:
        async with safechat_target_lock:
            channel_targets = safechat_targets.get(channel.id)
            if channel_targets is not None:
                channel_targets.discard(target.id)
                if not channel_targets:
                    safechat_targets.pop(channel.id, None)
        raise

    _reset_safechat_webhook_timer(channel.id, webhook)
    return webhook


async def disable_safechat_target(channel_id: int) -> bool:
    safechat_targets.pop(channel_id, None)

    entry = safechat_webhooks.pop(channel_id, None)
    if entry is None:
        return False

    timer = entry.get("timer")
    if timer is not None and not timer.done():
        timer.cancel()

    webhook = entry.get("webhook")
    if webhook is not None:
        try:
            await webhook.delete(reason="SafeChat mode disabled")
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    return True


async def disable_safechat_for_user(user_id: int) -> int:
    affected_channels = [
        channel_id
        for channel_id, target_ids in safechat_targets.items()
        if user_id in target_ids
    ]

    removed = 0
    for channel_id in affected_channels:
        target_ids = safechat_targets.get(channel_id)
        if target_ids is None or user_id not in target_ids:
            continue

        target_ids.discard(user_id)
        removed += 1

        if not target_ids:
            await disable_safechat_target(channel_id)

    return removed


async def disable_all_safechat_targets() -> int:
    channel_ids = list(safechat_targets.keys() | safechat_webhooks.keys())
    disabled_count = 0

    for channel_id in channel_ids:
        if await disable_safechat_target(channel_id):
            disabled_count += 1

    return disabled_count


async def send_safechat_message(
    channel: discord.TextChannel,
    target: discord.Member,
    content: str,
    embeds: list[discord.Embed] | None = None,
    reply_header: str | None = None,
    files: list[discord.File] | None = None,
) -> list[discord.WebhookMessage]:
    """Relay the message without changing its text, name, or embed contents."""
    if target.id in safechat_ban:
        raise SafeChatUserBlacklisted

    webhook = await get_safechat_webhook(channel)
    embeds = embeds or []

    # Intentionally do not strip, rewrite, filter, or transform content.
    # If this was a reply, append a real user mention for the replied-to author.
    if reply_mention:
        content = f"{content} {reply_mention}" if content else reply_mention

    # Discord's webhook username is limited to 80 characters, so the original
    # display name is passed through unchanged up to Discord's own limit.
    username = target.display_name[:80]

    sent_messages: list[discord.WebhookMessage] = []

    if content:
        chunks = [
            content[index:index + 2000]
            for index in range(0, len(content), 2000)
        ]
    else:
        chunks = [None]

    for chunk_index, chunk in enumerate(chunks):
        send_kwargs = {
            "content": chunk,
            "username": username,
            "avatar_url": target.display_avatar.url,
            "allowed_mentions": discord.AllowedMentions(
                everyone=False,
                roles=False,
                users=True,
                replied_user=False,
            ),
            "embeds": embeds if chunk_index == 0 else [],
            "files": files if chunk_index == 0 else [],
            "wait": True,
        }

        try:
            sent_messages.append(await webhook.send(**send_kwargs))
        except (discord.NotFound, discord.HTTPException):
            # A temporary SafeChat webhook can disappear between messages
            # (cleanup, manual deletion, or Discord-side invalidation).  Drop
            # the stale cache and transparently create a fresh relay webhook.
            current = safechat_webhooks.get(channel.id)
            if current is not None and current.get("webhook") is webhook:
                safechat_webhooks.pop(channel.id, None)

            webhook = await get_safechat_webhook(channel)
            sent_messages.append(await webhook.send(**send_kwargs))

    _reset_safechat_webhook_timer(channel.id, webhook)
    return sent_messages


async def cleanup_stale_safechat_webhooks() -> None:
    if bot.user is None:
        return

    bot_id = bot.user.id
    tracked_ids = {
        entry["webhook"].id
        for entry in safechat_webhooks.values()
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
            if webhook.name != SAFECHAT_WEBHOOK_NAME:
                continue
            if webhook.user is None or webhook.user.id != bot_id:
                continue

            try:
                await webhook.delete(reason="Stale SafeChat webhook cleanup")
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass


@tasks.loop(seconds=SAFECHAT_WEBHOOK_CLEANUP_INTERVAL_SECONDS)
async def safechat_webhook_cleanup_loop():
    await cleanup_stale_safechat_webhooks()


@safechat_webhook_cleanup_loop.before_loop
async def before_safechat_webhook_cleanup():
    await bot.wait_until_ready()
