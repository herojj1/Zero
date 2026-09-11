# extras.py — NOVA helper module
import os
import re
import time
import asyncio
import logging
from datetime import datetime

import aiohttp

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

log = logging.getLogger("NOVA_EXTRAS")

SP_PER_USER_WORKERS    = 30
MSP_PER_USER_WORKERS   = 70
RZ_PER_USER_WORKERS    = 30
MRZ_PER_USER_WORKERS   = 50
SITE_PER_USER_WORKERS  = 30
PROXY_PER_USER_WORKERS = 50

API_TIMEOUT   = 60
BIN_TIMEOUT   = 60
PROXY_TIMEOUT = 12
RZ_TIMEOUT    = 60

BOT_START_TIME = time.time()
_USER_SEMS = {}


def get_user_sem(uid, sem_type="msp"):
    key = f"{uid}_{sem_type}"
    if key not in _USER_SEMS:
        limits = {
            "sp": SP_PER_USER_WORKERS, "msp": MSP_PER_USER_WORKERS,
            "rz": RZ_PER_USER_WORKERS, "mrz": MRZ_PER_USER_WORKERS,
            "site": SITE_PER_USER_WORKERS, "proxy": PROXY_PER_USER_WORKERS,
        }
        _USER_SEMS[key] = asyncio.Semaphore(limits.get(sem_type, 30))
    return _USER_SEMS[key]


def cleanup_user_sem(uid):
    for k in [k for k in _USER_SEMS if k.startswith(f"{uid}_")]:
        del _USER_SEMS[k]


_USER_HTTP_SESSIONS = {}


async def get_user_http_session(uid, purpose="general"):
    key = f"{uid}_{purpose}"
    s = _USER_HTTP_SESSIONS.get(key)
    if s is None or s.closed:
        timeout_val = RZ_TIMEOUT if purpose in ("rz", "mrz") else API_TIMEOUT
        connector = aiohttp.TCPConnector(
            limit=150, limit_per_host=50, ttl_dns_cache=300,
            use_dns_cache=True, keepalive_timeout=30, enable_cleanup_closed=True,
        )
        s = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout_val, connect=10),
            connector=connector,
        )
        _USER_HTTP_SESSIONS[key] = s
    return s


async def cleanup_user_http_session(uid, purpose="general"):
    s = _USER_HTTP_SESSIONS.pop(f"{uid}_{purpose}", None)
    if s and not s.closed:
        try:
            await s.close()
        except Exception:
            pass


class SmartRotator:
    def __init__(self):
        self._site_fails = {}
        self._proxy_fails = {}
        self._site_idx = 0
        self._proxy_idx = 0

    def pick_site(self, sites, exclude=None):
        if not sites:
            return None
        exclude = exclude or set()
        avail = [s for s in sites if s not in exclude and self._site_fails.get(s, 0) < 5]
        if not avail:
            avail = [s for s in sites if s not in exclude] or list(sites)
        self._site_idx = (self._site_idx + 1) % len(avail)
        return avail[self._site_idx]

    def pick_proxy(self, proxies, exclude=None):
        if not proxies:
            return None
        exclude = exclude or set()
        avail = [p for p in proxies if p not in exclude and self._proxy_fails.get(p, 0) < 5]
        if not avail:
            avail = [p for p in proxies if p not in exclude] or list(proxies)
        self._proxy_idx = (self._proxy_idx + 1) % len(avail)
        return avail[self._proxy_idx]

    def report_site_ok(self, site):   self._site_fails[site] = 0
    def report_site_fail(self, site): self._site_fails[site] = self._site_fails.get(site, 0) + 1
    def report_proxy_ok(self, proxy):
        if proxy: self._proxy_fails[proxy] = 0
    def report_proxy_fail(self, proxy):
        if proxy: self._proxy_fails[proxy] = self._proxy_fails.get(proxy, 0) + 1


SITE_ERROR_KEYWORDS = [
    'r4 token empty','payment method is not shopify','r2 id empty','product id is empty',
    'py id empty','clinte token','receipt_empty','receipt id is empty','receipt empty',
    'site requires login','failed to get token','no valid products','not shopify',
    'failed to get checkout','failed to detect product','failed to create checkout',
    'failed to get proposal data','site not supported','site error! status: 429',
    'token not found','handle is empty','payment method identifier is empty',
    'failed to get session token','failed to tokenize card','no_session_token',
    'no session token','no checkout token found','checkout token not found',
    'no checkout token','checkout token is empty','tokenize_fail','tokenize fail',
    'tax ammount empty','tax amount empty','tax amount is empty','del ammount empty',
    'site not supported for now','payment base card not supported','no product found',
    'checkout is not available','cart is empty','cart add failed after retries',
    'checkout_expired','checkout_not_found','no shipping methods available',
    'site error','site dead','site errors','server error','internal server error',
    'internal_server_error','application error','unexpected error','something went wrong',
    'error in 1st req','error in 1 req','error processing card','we could not process',
    'unable to process','payment provider error','payment gateway error','session expired',
    'session invalid','failed after retries','max retries exceeded','all sites dead',
    'all sites unavailable','processinf error','handle error','nonetype',
    "nonetype' object has no attribute 'get",'unknown error','unknown_error',
    'unknown_result','utm_source','shop is unavailable','store is unavailable',
    'store not found','page not found','this store is unavailable',
    'this shop is currently unavailable','password protected','enter store using password',
    'storefront is password protected','shop closed','store closed',
    'delivery_delivery_line_detail_changed','delivery_address2_required',
    'delivery_line_detail_changed','delivery_line','delivery_address','address_required',
    'submit_rejected','submit rejected:','change proxy or site','change site',
    'fake charge gate','fake gate','hcaptcha detected','hcaptcha_detected',
    'captcha at checkout','captcha_required','captcha required','cloudflare',
    'access denied','permission denied','connection error','connection failed',
    'timed out','timeout','could not resolve host','connect tunnel failed','unreachable',
    'network error','connection reset','empty reply from server','tlsv1 alert',
    'ssl routines','openssl ssl_connect','api_timeout','http error','httperror504',
    '502','503','504','bad gateway','service unavailable','gateway timeout',
    'site error! status: 404','site error! status: 401','amount_too_small',
    'amount too small','merchandise_not_enough_stock','product out of stock',
    'malformed input','url rejected','invalid_response','cart failed with status',
    'invalid json response','invalid json','inventoryreservationfailure',
    'inventory_reservation_failure','payments_positive_amount_expec',
    'payments_payment_flexibility_t','payments_credit_card_brand_not',
    'buyer_identity_presentment_currency',"'products'","error:","error: '",
    'unable to get payment token','empty submit response','empty submit',
    'order_total_changed','order total changed','invalid_payment_method',
    'invalid payment method','validation_custom','validation custom','timeout'
]

PROXY_ERROR_KEYWORDS = [
    'proxy dead','proxy error','proxy timeout','proxy connection failed','proxy refused',
]

RZ_RETRY_KEYWORDS = [
    'payment id not found','payment_id_not_found','timeout','timed out',
    'connection error','connection failed','connection reset','server error',
    'internal server error','502','503','504','bad gateway','service unavailable',
    'gateway timeout','empty reply','invalid json','could not resolve host',
    'network error','ssl routines','unreachable','proxy dead','proxy error',
    'proxy timeout','DEAD | Payment ID not found',
]


def is_site_error(text):
    if not text:
        return True
    low = text.lower().strip()
    if low == 'na':
        return True
    return any(k in low for k in SITE_ERROR_KEYWORDS)


def is_proxy_error(text):
    if not text:
        return False
    return any(k in text.lower().strip() for k in PROXY_ERROR_KEYWORDS)


def is_rz_retry_error(text):
    if not text:
        return True
    low = text.lower().strip()
    return any(k in low for k in RZ_RETRY_KEYWORDS)


def is_truly_alive(response, price):
    if not response:
        return False
    low = response.lower().strip()
    pc = str(price).replace('$', '').strip() if price else '0'
    try:
        pv = float(pc)
    except Exception:
        pv = 0.0
    bad = ['error:', 'error: ', "error: '", 'cart failed', 'invalid json',
           'inventoryreservationfailure', 'payments_positive_amount',
           'payments_payment_flexibility', 'payments_credit_card_brand']
    if any(b in low for b in bad):
        return False
    if pv == 0.0:
        normal = ['card_declined','card declined','generic_decline','generic decline',
                  'do_not_honor','do not honor','insufficient_funds','insufficient funds',
                  'stolen_card','lost_card','expired_card','expired card','otp_required',
                  'otp required','3d','authentication','cvc','ccn','generic_error',
                  'generic error','restricted_card','fraudulent','not_permitted',
                  'transaction_not_allowed','card_not_supported']
        if not any(n in low for n in normal):
            return False
    return True


def clean_rz_response(raw_resp):
    if not raw_resp:
        return raw_resp
    cleaned = re.sub(
        r'^(?:DEAD|LIVE|SUCCESS|CHARGED|APPROVED|DECLINED)\s*\|\s*ID:\s*pay_[\w]+\s*\|\s*',
        '', raw_resp, flags=re.IGNORECASE,
    ).strip()
    return cleaned if cleaned else raw_resp


_BIN_CACHE = {}
_BIN_SEM = asyncio.Semaphore(20)


async def get_bin_info_cached(bin_number, session_getter):
    bin_number = (bin_number or "")[:6]
    if not bin_number:
        return '-', '-', '-', '-', '-', ''
    if bin_number in _BIN_CACHE:
        return _BIN_CACHE[bin_number]
    try:
        session = await session_getter()
        async with _BIN_SEM:
            async with session.get(f'https://bins.antipublic.cc/bins/{bin_number}') as r:
                if r.status != 200:
                    return '-', '-', '-', '-', '-', ''
                data = await r.json(content_type=None)
                info = (
                    data.get('brand', '-'),
                    data.get('type', '-'),
                    data.get('level', '-'),
                    data.get('bank', '-'),
                    data.get('country_name', '-'),
                    data.get('country_flag', ''),
                )
                _BIN_CACHE[bin_number] = info
                return info
    except Exception:
        return '-', '-', '-', '-', '-', ''


def _fmt_uptime(seconds):
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, sec = divmod(rem, 60)
    return f"{days}d {hours:02}:{minutes:02}:{sec:02}"


def _progress_bar(pct, length=10):
    filled = int(length * pct / 100)
    return f"{'█' * filled}{'░' * (length - filled)} {pct:.1f}%"


def build_status_text():
    if not PSUTIL_AVAILABLE:
        return "<b>Status:</b> psutil not installed"
    try:
        cpu = psutil.cpu_percent(interval=0)
        cores = psutil.cpu_count(logical=True)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        net = psutil.net_io_counters()
        bot_up = _fmt_uptime(time.time() - BOT_START_TIME)
        sys_up = _fmt_uptime(time.time() - psutil.boot_time())
        restart = datetime.fromtimestamp(BOT_START_TIME).strftime('%Y-%m-%d %H:%M:%S')
        return (
            f"⌬ <b>Bot Uptime</b> ↬ <code>{bot_up}</code>\n"
            f"⌬ <b>System Uptime</b> ↬ <code>{sys_up}</code>\n"
            f"⌬ <b>Last Restart</b> ↬ <code>{restart}</code>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"⌬ <b>CPU</b> ↬ <code>{cpu:.1f}% ({cores} cores)</code>\n"
            f"⊀ <b>Usage</b> ↬ <code>{_progress_bar(cpu)}</code>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"⌬ <b>RAM</b> ↬ <code>{mem.used/(1024**3):.2f}GB / {mem.total/(1024**3):.2f}GB</code>\n"
            f"⊀ <b>Usage</b> ↬ <code>{_progress_bar(mem.percent)}</code>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"⌬ <b>Disk</b> ↬ <code>{disk.used/(1024**3):.2f}GB / {disk.total/(1024**3):.2f}GB</code>\n"
            f"⊀ <b>Usage</b> ↬ <code>{_progress_bar(disk.percent)}</code>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"⌬ <b>Network</b> ↬ <code>↑ {net.bytes_sent/(1024**2):.1f}MB ↓ {net.bytes_recv/(1024**2):.1f}MB</code>\n"
        )
    except Exception as e:
        return f"<b>Status error:</b> <code>{e}</code>"
