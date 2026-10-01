# =============================================================================
# shopify_harvester.py — Shopify site harvester + validator
# =============================================================================
# Validates that a domain is a live Shopify store with in-range products.
#
# Public API:
#   validate_one(url, proxy_url, min_price, max_price) -> dict
#   scan_file(src, dst, proxy_url, min_price, max_price, concurrency) -> (live, dead, over, errors)
#   harvest_from_seeds(seeds, extra_roots, dst, proxy_url, min_price, max_price) -> (live, dead, over, errors)
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

logger = logging.getLogger("sh_harvester")
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
    url = url.strip().lower()
    url = re.sub(r"^https?://", "", url).rstrip("/")
    if url.startswith("www."):
        url = url[4:]
    if "/" in url:
        url = url.split("/")[0]
    return url


def _parse_proxy(p: Optional[str]) -> Optional[str]:
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


async def validate_one(shop_domain: str, proxy_url: Optional[str] = None,
                       min_price: float = 0.01, max_price: float = 7.00,
                       timeout: float = 12.0) -> dict:
    """
    Returns:
      {
        "url": normalized_domain,
        "ok": bool,
        "status": int,
        "reason": short string,
        "price": float (min available product price, 0.0 if unknown),
        "count": int (# available variants)
      }
    """
    domain = _normalize(shop_domain)
    out = {"url": domain, "ok": False, "status": 0, "reason": "unchecked",
           "price": 0.0, "count": 0}

    if not domain:
        out["reason"] = "empty"
        return out

    url = f"https://{domain}/products.json?limit=250"
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            kw = {
                "headers": headers,
                "timeout": aiohttp.ClientTimeout(total=timeout, connect=8),
                "allow_redirects": False,
            }
            if proxy_url:
                kw["proxy"] = proxy_url
            async with session.get(url, **kw) as r:
                out["status"] = r.status
                if r.status == 404:
                    out["reason"] = "404"
                    return out
                if r.status == 401:
                    out["reason"] = "password protected"
                    return out
                if r.status == 403:
                    out["reason"] = "403 forbidden"
                    return out
                if r.status >= 500:
                    out["reason"] = f"http {r.status}"
                    return out
                if r.status in (301, 302, 307, 308):
                    out["reason"] = "redirect (custom domain)"
                    return out
                if r.status != 200:
                    out["reason"] = f"http {r.status}"
                    return out

                body = await r.text()
                # quick sniff: must be JSON with products array
                if '"products"' not in body:
                    out["reason"] = "not shopify json"
                    return out

                try:
                    data = json.loads(body)
                except Exception:
                    out["reason"] = "invalid json"
                    return out

                products = data.get("products") or []
                if not products:
                    out["reason"] = "no products"
                    return out

                min_found = float("inf")
                count = 0
                for p in products:
                    for v in p.get("variants") or []:
                        if v.get("available") is False:
                            continue
                        try:
                            pf = float(v.get("price") or 0)
                        except (TypeError, ValueError):
                            continue
                        if pf <= 0:
                            continue
                        count += 1
                        if pf < min_found:
                            min_found = pf

                if count == 0:
                    out["reason"] = "all out of stock"
                    return out

                out["count"] = count
                out["price"] = min_found if min_found != float("inf") else 0.0

                if min_found < min_price:
                    out["reason"] = f"below ${min_price:.2f}"
                    return out
                if min_found > max_price:
                    out["reason"] = f"over ${max_price:.2f}"
                    return out

                out["ok"] = True
                out["reason"] = f"${min_found:.2f}"
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
                          min_price: float, max_price: float,
                          concurrency: int, progress_cb=None):
    live, dead, over, errors = [], [], [], []
    sem = asyncio.Semaphore(concurrency)
    done = [0]

    async def worker(u):
        async with sem:
            r = await validate_one(u, proxy_url, min_price, max_price)
        done[0] += 1
        if r["ok"]:
            live.append(r["url"])
        elif r["reason"].startswith("over "):
            over.append(r["url"])
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
    return live, dead, over, errors


def scan_file(src: str, dst: str, proxy_url: Optional[str] = None,
              min_price: float = 0.01, max_price: float = 7.00,
              concurrency: int = 60, progress_cb=None) -> Tuple[List[str], List[str], List[str], List[str]]:
    if not os.path.exists(src):
        logger.warning(f"scan_file: {src} missing")
        return [], [], [], []

    with open(src, "r", encoding="utf-8", errors="ignore") as f:
        urls = [ln.strip() for ln in f if ln.strip()]
    if not urls:
        return [], [], [], []

    urls = list(dict.fromkeys(urls))
    logger.info(f"scanning {len(urls)} Shopify domains from {src}")

    proxy = _parse_proxy(proxy_url)
    live, dead, over, errors = asyncio.run(
        _validate_batch(urls, proxy, min_price, max_price, concurrency, progress_cb))

    live = list(dict.fromkeys(live))
    logger.info(f"live={len(live)} dead={len(dead)} over={len(over)} errors={len(errors)}")

    try:
        with open(dst, "w", encoding="utf-8") as f:
            for u in live:
                f.write(u + "\n")
    except Exception as e:
        logger.error(f"write {dst} failed: {e}")

    return live, dead, over, errors


# ──────────────────────── Seed harvesting ────────────────────────────
# Shopify's `myshopify.com` subdomains follow store-slug naming. Common
# suffixes and English word stems get probed.

_COMMON_SHOPS = [
    # generic brand stems
    "shop", "store", "official", "the", "buy", "get", "daily", "custom",
    "unique", "prime", "pro", "elite", "premium", "urban", "modern",
    "pure", "wild", "fresh", "clean", "smart", "swift", "golden", "silver",
    "royal", "grand", "little", "big", "tiny", "mini", "mega", "super",
    "best", "top", "just", "only", "true", "real", "new", "old", "young",
    "bright", "dark", "bold", "calm", "quiet", "loud", "fast", "slow",
    "cozy", "warm", "cool", "soft", "hard", "light", "deep", "high", "low",
    # product categories
    "coffee", "tea", "candle", "candles", "soap", "skincare", "beauty",
    "cosmetics", "fashion", "clothing", "apparel", "shoes", "jewelry",
    "accessories", "bags", "watches", "home", "kitchen", "bedding",
    "furniture", "decor", "art", "prints", "posters", "toys", "kids",
    "baby", "pets", "dog", "cat", "fitness", "sports", "outdoor", "camping",
    "garden", "plants", "succulents", "tech", "gadgets", "electronics",
    "books", "stationery", "snacks", "food", "drinks", "wine", "beer",
    "supplements", "vitamins", "protein", "coffee-shop", "roasters",
]


async def harvest_from_seeds(
    seeds: List[str],
    extra_stems: Optional[List[str]] = None,
    dst: str = "sites.txt",
    proxy_url: Optional[str] = None,
    min_price: float = 0.01,
    max_price: float = 7.00,
    concurrency: int = 60,
    progress_cb=None,
) -> Tuple[List[str], List[str], List[str], List[str]]:

    candidates = set()
    for s in seeds or []:
        s = _normalize(s)
        if s:
            candidates.add(s)

    stems = list(_COMMON_SHOPS) + list(extra_stems or [])
    suffixes = ["", "shop", "store", "co", "official", "india", "us", "uk"]

    for stem in stems:
        stem = re.sub(r"[^a-z0-9\-]", "", stem.lower())
        if not stem:
            continue
        for suf in suffixes:
            slug = f"{stem}{suf}" if suf else stem
            candidates.add(f"{slug}.myshopify.com")

    candidates = list(candidates)
    logger.info(f"harvest: {len(candidates)} candidates")

    proxy = _parse_proxy(proxy_url)
    live, dead, over, errors = asyncio.run(
        _validate_batch(candidates, proxy, min_price, max_price, concurrency, progress_cb))

    live = list(dict.fromkeys(live))
    logger.info(f"harvest: live={len(live)} dead={len(dead)} over={len(over)} errors={len(errors)}")

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

    return live, dead, over, errors


# ──────────────────────── CLI ────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python shopify_harvester.py scan <src.txt> [dst.txt] [proxy]")
        print("  python shopify_harvester.py harvest <seed1,seed2,...> [dst.txt] [proxy]")
        sys.exit(1)

    mode = sys.argv[1]
    if mode == "scan":
        src = sys.argv[2]
        dst = sys.argv[3] if len(sys.argv) > 3 else "sites_live.txt"
        proxy = sys.argv[4] if len(sys.argv) > 4 else None
        live, dead, over, errors = scan_file(src, dst, proxy)
        print(f"\nlive: {len(live)}\ndead: {len(dead)}\nover: {len(over)}\nerrors: {len(errors)}")
        print(f"written: {dst}")
    elif mode == "harvest":
        seeds = sys.argv[2].split(",")
        dst = sys.argv[3] if len(sys.argv) > 3 else "sites.txt"
        proxy = sys.argv[4] if len(sys.argv) > 4 else None
        live, dead, over, errors = harvest_from_seeds(seeds, None, dst, proxy)
        print(f"\nlive: {len(live)}\ndead: {len(dead)}\nover: {len(over)}\nerrors: {len(errors)}")
        print(f"written: {dst}")
    else:
        print(f"unknown mode: {mode}")
        sys.exit(1)
