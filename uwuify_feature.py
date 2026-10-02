"""UWUIFY feature module."""

from bot import *

_UWU_DATA = json.loads(Path(__file__).with_name("uwu_words.json").read_text(encoding="utf-8"))
UWU_WORD_BLACKLIST = set(_UWU_DATA.get("word_blacklist", []))

UWU_WEBHOOK_NAME = "Uwuify Relay"
UWU_WEBHOOK_IDLE_SECONDS = 5 * 60

# Only members with one of these role IDs may enable/disable UWU mode.
UWU_ALLOWED_ROLE_IDS = {
    1518416402141417472,
    1378810715611336914,
    1377468541779050636,
}

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