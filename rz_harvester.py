# =============================================================================
# rz_harvester.py — Razorpay site harvester + validator
# =============================================================================
# Given a list of candidate Razorpay URLs, verifies each is a real hosted
# checkout page and outputs the live ones. Two modes:
#
#   - scan_file(src, dst): checks every URL in src, writes live ones to dst
#   - harvest_from_seeds(seeds, extra_slugs, dst): expands seeds into new
#     slugs by probing common patterns, tests, writes live
#
# Public API:
#   scan_file(src, dst, proxy_url) -> (live, dead, errors)
#   harvest_from_seeds(seeds, extra, dst, proxy_url) -> (live, dead, errors)
#   validate_one(url, proxy_url) -> dict {url, ok, reason, title, amount}
# =============================================================================

import os
import re
import json
import time
import asyncio
import random
import logging
from typing import List, Tuple, Optional
from urllib.parse import urlparse

import aiohttp

logger = logging.getLogger("rz_harvester")
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(h)
    logger.setLevel(logging.INFO)


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
]


def _normalize(url: str) -> str:
    if not url:
        return url
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def _parse_proxy(p: Optional[str]) -> Optional[str]:
    """Returns a proxy URL ready to hand to aiohttp."""
    if not p:
        return None
    p = p.strip()
    if "://" in p:
        return p
    if "@" in p:
        return "http://" + p
    parts = p.split(":")
    if len(parts) == 2:
        return f"http://{parts[0]}:{parts[1]}"
    if len(parts) == 4:
        return f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
    return None


async def validate_one(url: str, proxy_url: Optional[str] = None,
                       timeout: float = 12.0) -> dict:
    """
    Return:
      {
        "url": normalized_url,
        "ok": bool,
        "status": int,
        "reason": short string,
        "title": page title if found,
      }
    """
    url = _normalize(url)
    out = {"url": url, "ok": False, "status": 0, "reason": "unchecked", "title": ""}
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-IN,en;q=0.9",
    }

    try:
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            kw = {
                "headers": headers,
                "timeout": aiohttp.ClientTimeout(total=timeout, connect=8),
                "allow_redirects": True,
            }
            if proxy_url:
                kw["proxy"] = proxy_url
            async with session.get(url, **kw) as r:
                out["status"] = r.status
                if r.status == 404:
                    out["reason"] = "404"
                    return out
                if r.status == 410:
                    out["reason"] = "410 gone"
                    return out
                if r.status >= 500:
                    out["reason"] = f"http {r.status}"
                    return out
                if r.status != 200:
                    out["reason"] = f"http {r.status}"
                    return out

                body = await r.text()
                low = body.lower()
                final_url = str(r.url)

                # Razorpay hosted page tells:
                # 1. Contains reference to Razorpay checkout JS
                has_rzp_js = ("checkout.razorpay.com" in low
                              or "rzp.io" in low
                              or "razorpay.com/v1/checkout" in low
                              or "cdn.razorpay.com" in low)
                # 2. Contains payment page structure
                has_page_marker = ("payment_page" in low
                                   or "payment link" in low
                                   or "pages.razorpay.com" in low)
                # 3. Has key_id / keyless_header / payment_link_id present
                has_data = ("key_id" in low
                            or "keyless_header" in low
                            or "payment_link_id" in low)

                if final_url and "404" in final_url.lower():
                    out["reason"] = "redirect to 404"
                    return out

                title_m = re.search(r"<title[^>]*>([^<]{1,120})</title>", body, re.I)
                if title_m:
                    out["title"] = title_m.group(1).strip()

                if has_rzp_js and (has_page_marker or has_data):
                    out["ok"] = True
                    out["reason"] = "live"
                    return out
                if has_data and "razorpay" in low:
                    out["ok"] = True
                    out["reason"] = "live (data)"
                    return out
                if "not found" in low and "razorpay" in low:
                    out["reason"] = "razorpay 404 page"
                    return out

                out["reason"] = "not a razorpay checkout page"
                return out

    except asyncio.TimeoutError:
        out["reason"] = "timeout"
        return out
    except aiohttp.ClientError as e:
        out["reason"] = f"client error: {type(e).__name__}"
        return out
    except Exception as e:
        out["reason"] = f"error: {type(e).__name__}"
        return out


async def _validate_batch(urls: List[str], proxy_url: Optional[str],
                          concurrency: int, progress_cb=None) -> Tuple[List[str], List[str], List[str]]:
    live, dead, errors = [], [], []
    sem = asyncio.Semaphore(concurrency)
    done = [0]

    async def worker(u):
        async with sem:
            r = await validate_one(u, proxy_url)
        done[0] += 1
        if r["ok"]:
            live.append(r["url"])
        elif "error" in r["reason"] or "timeout" in r["reason"]:
            errors.append(f"{r['url']} | {r['reason']}")
        else:
            dead.append(f"{r['url']} | {r['reason']}")
        if progress_cb:
            try:
                progress_cb(done[0], len(urls), r)
            except Exception:
                pass

    await asyncio.gather(*(worker(u) for u in urls))
    return live, dead, errors


def scan_file(src: str, dst: str, proxy_url: Optional[str] = None,
              concurrency: int = 30, progress_cb=None) -> Tuple[List[str], List[str], List[str]]:
    """
    Read URL list from `src`, validate each, write live ones to `dst`.
    Returns (live, dead, errors).
    """
    if not os.path.exists(src):
        logger.warning(f"scan_file: {src} missing")
        return [], [], []

    with open(src, "r", encoding="utf-8", errors="ignore") as f:
        urls = [ln.strip() for ln in f if ln.strip()]
    if not urls:
        return [], [], []

    # dedupe
    urls = list(dict.fromkeys(urls))
    logger.info(f"scanning {len(urls)} URLs from {src}")

    proxy = _parse_proxy(proxy_url)
    live, dead, errors = asyncio.run(_validate_batch(urls, proxy, concurrency, progress_cb))

    # dedupe live output
    live = list(dict.fromkeys(live))
    logger.info(f"live={len(live)} dead={len(dead)} errors={len(errors)}")

    try:
        with open(dst, "w", encoding="utf-8") as f:
            for u in live:
                f.write(u + "\n")
    except Exception as e:
        logger.error(f"write {dst} failed: {e}")

    return live, dead, errors


# ──────────────────────── Slug harvesting ────────────────────────────

# Candidate slugs to probe. Big generic list — actual Razorpay slugs are
# business-specific, so we probe common words plus optional seed hints.
_COMMON_SLUG_HINTS = [
    "donate", "donation", "pay", "payment", "payments", "paynow", "paynow786",
    "checkout", "fees", "fee", "booking", "book", "order", "store", "shop",
    "buy", "buynow", "cart", "subscription", "subscribe", "register",
    "registration", "ticket", "tickets", "entry", "entryticket", "course",
    "courseregistration", "feespayment", "schoolfees", "collegefees",
    "tuitionfees", "donate-now", "donate-any", "contribute", "contribution",
    "support", "supportus", "seva", "sevas", "daan", "daanam", "help",
    "helpus", "sponsor", "sponsorship", "fund", "fundraiser", "temple",
    "church", "mandir", "ashram", "trust", "foundation", "ngo", "charity",
    "welfare", "gurudwara", "masjid", "donationpage", "donation-form",
    "get-trial", "get-trial", "startlaunchkit", "trial", "pro", "elite",
    "plan", "plan1", "starter", "basic", "premium", "annual", "monthly",
    "yearly", "quarterly", "mudgar", "mudgarbeginners", "mudgarbeginnerscourse",
    "beginners", "beginners-course", "buy-course", "course-payment",
]


async def harvest_from_seeds(
    seeds: List[str],
    extra_slugs: Optional[List[str]] = None,
    dst: str = "rz_sites.txt",
    proxy_url: Optional[str] = None,
    concurrency: int = 30,
    progress_cb=None,
) -> Tuple[List[str], List[str], List[str]]:
    """
    Build a candidate list from seeds + common slug hints, validate each,
    write live ones to dst. Returns (live, dead, errors).

    seeds can be:
      - full URLs (kept as-is)
      - bare slug names (prepended with pages.razorpay.com/)
    """
    candidates = set()
    for s in seeds or []:
        s = s.strip()
        if not s:
            continue
        if s.startswith(("http://", "https://")):
            candidates.add(s.rstrip("/"))
        else:
            # treat as bare slug
            s_slug = s.strip("/").split("/")[-1]
            candidates.add(f"https://pages.razorpay.com/{s_slug}")

    hints = list(_COMMON_SLUG_HINTS) + list(extra_slugs or [])
    for h in hints:
        h = h.strip()
        if not h:
            continue
        candidates.add(f"https://pages.razorpay.com/{h}")

    candidates = list(candidates)
    logger.info(f"harvest: {len(candidates)} candidates")

    proxy = _parse_proxy(proxy_url)
    live, dead, errors = asyncio.run(_validate_batch(candidates, proxy, concurrency, progress_cb))

    live = list(dict.fromkeys(live))
    logger.info(f"harvest: live={len(live)} dead={len(dead)} errors={len(errors)}")

    # merge with existing dst content
    existing = set()
    if os.path.exists(dst):
        try:
            with open(dst, "r", encoding="utf-8", errors="ignore") as f:
                existing = {ln.strip() for ln in f if ln.strip()}
        except Exception:
            pass
    merged = list(dict.fromkeys(list(existing) + live))
    try:
        with open(dst, "w", encoding="utf-8") as f:
            for u in merged:
                f.write(u + "\n")
        logger.info(f"harvest: wrote {len(merged)} total to {dst}")
    except Exception as e:
        logger.error(f"write {dst} failed: {e}")

    return live, dead, errors


def harvest_seeds_only_sync(seeds, extra_slugs, dst, proxy_url, concurrency=30):
    return harvest_from_seeds(seeds, extra_slugs, dst, proxy_url, concurrency)


# ──────────────────────── CLI ────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python rz_harvester.py scan <src.txt> [dst.txt] [proxy]")
        print("  python rz_harvester.py harvest <seed1,seed2,...> [dst.txt] [proxy]")
        sys.exit(1)

    mode = sys.argv[1]
    if mode == "scan":
        src = sys.argv[2]
        dst = sys.argv[3] if len(sys.argv) > 3 else "rz_sites_live.txt"
        proxy = sys.argv[4] if len(sys.argv) > 4 else None
        live, dead, errors = scan_file(src, dst, proxy)
        print(f"\nlive: {len(live)}\ndead: {len(dead)}\nerrors: {len(errors)}")
        print(f"written to: {dst}")
    elif mode == "harvest":
        seeds = sys.argv[2].split(",")
        dst = sys.argv[3] if len(sys.argv) > 3 else "rz_sites.txt"
        proxy = sys.argv[4] if len(sys.argv) > 4 else None
        live, dead, errors = harvest_from_seeds(seeds, None, dst, proxy)
        print(f"\nlive: {len(live)}\ndead: {len(dead)}\nerrors: {len(errors)}")
        print(f"written to: {dst}")
    else:
        print(f"unknown mode: {mode}")
        sys.exit(1)
