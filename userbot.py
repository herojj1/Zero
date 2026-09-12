# =============================================================================
# userbot.py — Standalone Telegram userbot
# Shows "Playing a game" status for a few seconds when someone DMs you.
# Runs SEPARATELY from your NOVA bot. No shared code, no shared session.
# =============================================================================

import os
import asyncio
import logging
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError

# =============================================================================
# ============================ CONFIG =========================================
# Values are read from environment variables. Do NOT hardcode credentials here.
#
# Set them in your terminal (or Railway / Heroku / Replit env panel):
#
#   Windows PowerShell:
#       $env:USER_API_ID="33657928"
#       $env:USER_API_HASH="a61fde61442113b9a65c699f7020d59a"
#       $env:USER_PHONE="+92300234345"
#
#   Linux / macOS:
#       export USER_API_ID="24785476"
#       export USER_API_HASH="your_32_char_api_hash"
#       export USER_PHONE="+92300234345"
#
#   Optional:
#       $env:USER_BOT_TOKEN="..."          (bot mode — see note below)
#       $env:USER_SESSION_NAME="aayco_user"
# =============================================================================

API_ID    = int(os.getenv("USER_API_ID") or 0
               33657928)
API_HASH  = os.getenv("USER_API_HASH", "a61fde61442113b9a65c699f7020d59a")
PHONE     = os.getenv("USER_PHONE", "")
BOT_TOKEN = os.getenv("USER_BOT_TOKEN", "8881611682:AAGUaw5qi17Qy3cLGtJwIe6qXoK17WcW_lU")   # optional, bot mode (no status gimmick)
SESSION_NAME = os.getenv("USER_SESSION_NAME", "aayco_user")

# ─── Tunables (edit these directly if you want) ───
COOLDOWN_SEC   = 60       # seconds between actions per sender (anti-flood)
ACTION_TYPE    = "game"   # options: game, typing, audio, video, photo,
                          #          document, cancel, record-round,
                          #          record-audio, record-video
ACTION_SECONDS = 8        # how long to hold the status

# =============================================================================
# ========================= END OF CONFIG =====================================
# =============================================================================


# ====================== LOGGING ======================
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("USERBOT")


def _check_config():
    """Validate config before attempting to connect."""
    if not API_ID or API_ID <= 0:
        raise SystemExit(
            "❌ USER_API_ID is missing or invalid.\n"
            "   It must be a positive integer.\n"
            "   Set it with:  $env:USER_API_ID=\"24785476\"   (PowerShell)\n"
            "              or export USER_API_ID=\"24785476\"  (Linux/macOS)"
        )
    if not API_HASH:
        raise SystemExit(
            "❌ USER_API_HASH is missing.\n"
            "   Get one from https://my.telegram.org → API Development Tools.\n"
            "   It must be exactly 32 characters."
        )
    if len(API_HASH) != 32:
        raise SystemExit(
            f"❌ USER_API_HASH is invalid (length={len(API_HASH)}, expected 32).\n"
            "   Get a fresh one from https://my.telegram.org"
        )
    if not PHONE and not BOT_TOKEN:
        raise SystemExit(
            "❌ Neither USER_PHONE nor USER_BOT_TOKEN is set.\n"
            "   For the 'Playing a game' gimmick, set USER_PHONE.\n"
            "   Bots cannot set a presence status."
        )


# ====================== CLIENT ======================
client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

_ME = None
_LAST = {}          # sender_id -> last action timestamp


# ====================== HANDLER ======================
@client.on(events.NewMessage())
async def handle_action(event):
    global _ME

    # Cache our own identity once
    if _ME is None:
        try:
            _ME = await client.get_me()
            log.info(f"Logged in as {_ME.first_name} (@{_ME.username}) id={_ME.id}")
        except Exception as e:
            log.warning(f"get_me failed: {e}")
            return

    # Only act on private DMs from other people
    if not event.is_private:
        return
    if event.sender_id == _ME.id:
        return

    # Per-sender cooldown (anti-flood)
    now = asyncio.get_event_loop().time()
    if now - _LAST.get(event.sender_id, 0) < COOLDOWN_SEC:
        return
    _LAST[event.sender_id] = now

    # Trigger the status
    try:
        async with client.action(event.chat_id, ACTION_TYPE):
            await asyncio.sleep(ACTION_SECONDS)
    except FloodWaitError as e:
        log.warning(f"FloodWait {e.seconds}s — sleeping")
        await asyncio.sleep(e.seconds)
    except Exception as e:
        log.warning(f"action failed: {type(e).__name__}: {e}")
        try:
            await event.reply(str(e))
        except Exception:
            pass


# ====================== MAIN ======================
async def main():
    _check_config()

    if PHONE:
        log.info("Starting USER mode (phone login)...")
        log.info("If this is the first run, Telegram will send you a login code.")
        await client.start(phone=PHONE)
    elif BOT_TOKEN:
        log.info("Starting BOT mode (bot token)...")
        log.warning(
            "⚠️ Bot accounts cannot set a 'Playing a game' status.\n"
            "   This mode will connect but the gimmick will NOT work.\n"
            "   Set USER_PHONE instead for the status feature."
        )
        await client.start(bot_token=BOT_TOKEN)
    else:
        raise SystemExit("No login method available.")

    me = await client.get_me()
    log.info(f"✅ userbot online as {me.first_name} (id={me.id})")
    log.info(f"   Watching for DMs. Cooldown: {COOLDOWN_SEC}s per sender.")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
