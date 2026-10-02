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

# Backward-compatible alias. The actual whitelist is shared in bot.py.
HOOD_ALLOWED_ROLE_IDS = textify_whitelist

# Maximum number of unique people who can be actively HOODIFIED at once.
MAX_ACTIVE_HOOD_TARGETS = 5

# channel_id -> {"webhook": discord.Webhook, "timer": asyncio.Task | None}
hood_webhooks: dict[int, dict] = {}

# channel_id -> set of target member IDs.