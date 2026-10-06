# ============================================================
# 🕵️ Instagram OSINT Scraper ULTRA v4.0
# instaharvest-v2 1.1.88
#
# Architecture:
#   Anonymous core  -> public.get_profile / public.get_posts
#   Login adapter   -> saved session + private modules for
#                       Stories / Followers / Following / richer data
#
# Important:
#   - Keep secrets out of GitHub. Set them in Colab environment vars.
#   - This tool does not bypass Instagram security challenges/limits.
#   - Anonymous mode intentionally stays on the proven public API path.
# ============================================================

# ============================================================
# 1. Runtime configuration
# ============================================================
import os

# ------------------------------------------------------------
# Visible configuration block
# ------------------------------------------------------------
# Keep real credentials OUT of the public GitHub repository.
# Put their values into these Colab environment variables instead.
NGROK_AUTH_TOKEN = os.getenv("NGROK_AUTH_TOKEN", "")
IG_USERNAME = os.getenv("IG_USERNAME", "")
IG_PASSWORD = os.getenv("IG_PASSWORD", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Fixed ngrok development domain.
# This must be the development domain assigned to your ngrok account.
# The program intentionally does NOT fall back to a random domain.
NGROK_DOMAIN = "yin-spender-percent.ngrok-free.dev"

# A fresh control token is generated per Colab runtime unless explicitly
# supplied as an environment variable. It is never stored in Drive/GitHub.
CONTROL_TOKEN = os.getenv("CONTROL_TOKEN") or secrets.token_urlsafe(24)

START_MODE = "anonymous"
REQUIRED_INSTAHARVEST_VERSION = "1.1.88"

MAX_TRAVERSE_LIMIT = 10000
THUMB_MAX_SIZE = 150
THUMB_QUALITY = 40
THUMB_OPTIMIZE = True

# Colab-safe paths
try:
    from google.colab import drive
    IN_COLAB = True
except ImportError:
    IN_COLAB = False
    drive = None

# ============================================================
# 2. Imports / dependency check
# ============================================================
import importlib.metadata as importlib_metadata
import base64
import csv
import io
import json
import random
import re
import shutil
import socket
import sqlite3
import secrets
import threading
import time
import traceback
from datetime import datetime
from html import escape as html_escape
from pathlib import Path

import requests
from flask import Flask, jsonify, render_template_string, request
from pyngrok import ngrok

try:
    INSTAHARVEST_VERSION = importlib_metadata.version("instaharvest-v2")
except importlib_metadata.PackageNotFoundError:
    INSTAHARVEST_VERSION = None

if INSTAHARVEST_VERSION != REQUIRED_INSTAHARVEST_VERSION:
    raise RuntimeError(
        "نسخة instaharvest-v2 غير صحيحة. "
        f"المطلوب: {REQUIRED_INSTAHARVEST_VERSION} | "
        f"المثبت: {INSTAHARVEST_VERSION or 'غير مثبت'}. "
        "ثبت النسخة المطلوبة في خلية dependencies."
    )

from instaharvest_v2 import Instagram

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("⚠️ PIL غير متوفر؛ التقارير ستستخدم placeholders للصورة.")

# ============================================================
# 3. Shared HTTP / locks / runtime state
# ============================================================
_http_session = requests.Session()
_http_session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/142.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
})

INSTAGRAM_JOB_LOCK = threading.Lock()
CLIENT_SWITCH_LOCK = threading.RLock()
JOB_STATE_LOCK = threading.Lock()

JOB_STATE = {
    "state": "idle",
    "username": "",
    "kind": "",
    "started_at": None,
    "finished_at": None,
    "message": "جاهز",
}

# Authentication is deliberately event-driven: the browser never polls in a
# loop and verification codes are held only in RAM until the waiting login
# callback consumes them.
AUTH_STATE_LOCK = threading.RLock()
AUTH_CODE_EVENT = threading.Event()
AUTH_CODE_VALUE = None
AUTH_CODE_TIMEOUT_SECONDS = 600
AUTH_STATE = {
    "state": "idle",
    "target_mode": "anonymous",
    "contact_point": "",
    "message": "لا توجد عملية Login قيد الانتظار.",
    "error": "",
    "started_at": None,
    "updated_at": None,
}

# ============================================================
# 4. Utility helpers
# ============================================================
def safe_int(value, default=0, minimum=None, maximum=None):
    try:
        v = int(str(value).strip() or default)
    except (ValueError, TypeError):
        v = default
    if minimum is not None:
        v = max(v, minimum)
    if maximum is not None:
        v = min(v, maximum)
    return v


def safe_name(value, fallback="item"):
    value = str(value or "").strip()
    value = re.sub(r"[^A-Za-z0-9_.@-]+", "_", value)
    return value[:120] or fallback


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def set_job_state(state, username="", kind="", message=""):
    with JOB_STATE_LOCK:
        JOB_STATE.update({
            "state": state,
            "username": username,
            "kind": kind,
            "message": message,
            "started_at": JOB_STATE.get("started_at") if state not in ("running", "starting") else now_iso(),
            "finished_at": now_iso() if state in ("done", "error", "idle") else None,
        })


def get_job_state():
    with JOB_STATE_LOCK:
        return dict(JOB_STATE)


def set_auth_state(state, message="", target_mode=None, contact_point=None, error=""):
    with AUTH_STATE_LOCK:
        if target_mode is not None:
            AUTH_STATE["target_mode"] = str(target_mode)
        if contact_point is not None:
            AUTH_STATE["contact_point"] = str(contact_point)
        if state in {"starting", "loading_session", "authenticating"} and not AUTH_STATE.get("started_at"):
            AUTH_STATE["started_at"] = now_iso()
        AUTH_STATE.update({
            "state": str(state),
            "message": str(message or ""),
            "error": str(error or ""),
            "updated_at": now_iso(),
        })


def get_auth_state():
    with AUTH_STATE_LOCK:
        return dict(AUTH_STATE)


def submit_login_code(code):
    global AUTH_CODE_VALUE
    clean = re.sub(r"\s+", "", str(code or ""))
    if not (4 <= len(clean) <= 32):
        return False, "رمز التحقق غير صالح."
    with AUTH_STATE_LOCK:
        if AUTH_STATE.get("state") != "waiting_code":
            return False, "لا يوجد طلب تحقق ينتظر رمزًا الآن."
        AUTH_CODE_VALUE = clean
        AUTH_CODE_EVENT.set()
        AUTH_STATE.update({
            "state": "verifying",
            "message": "تم استلام رمز التحقق؛ جارٍ إكمال Login…",
            "updated_at": now_iso(),
        })
    return True, "تم إرسال رمز التحقق إلى جلسة Login الجارية."


def cancel_login():
    with AUTH_STATE_LOCK:
        pending = AUTH_STATE.get("state") in {
            "starting", "loading_session", "authenticating",
            "waiting_code", "verifying"
        }
        if not pending:
            return False, "لا توجد عملية Login جارية."
        AUTH_STATE.update({
            "state": "cancelled",
            "message": "تم إلغاء عملية Login.",
            "error": "",
            "updated_at": now_iso(),
        })
        AUTH_CODE_EVENT.set()
    return True, "تم إلغاء Login."


def instagram_challenge_callback(ctx=None):
    """
    Bridge instaharvest-v2 Email/SMS verification into the web panel.

    1.1.88 has two callback shapes:
      - ChallengeHandler calls callback(context)
      - auth_platform.resolve_auth_platform calls callback() with no argument
    Supporting both is required for the auth_platform path observed in Colab.
    """
    global AUTH_CODE_VALUE

    if ctx is None:
        contact = "البريد/الهاتف المرتبط بحساب Instagram"
    else:
        contact = extract(
            ctx, "contact_point", "contact", "masked_contact",
            default="وسيلة التحقق المسجلة"
        )

    with AUTH_STATE_LOCK:
        AUTH_CODE_VALUE = None
        AUTH_CODE_EVENT.clear()

    set_auth_state(
        "waiting_code",
        "Instagram أرسل رمز تحقق. انسخه هنا ثم اضغط «إرسال الرمز».",
        target_mode="login",
        contact_point=contact,
    )
    print("🔢 Instagram طلب رمز تحقق؛ Login الآن ينتظر الرمز من لوحة التحكم.")
    if not AUTH_CODE_EVENT.wait(AUTH_CODE_TIMEOUT_SECONDS):
        set_auth_state(
            "error",
            "انتهت مهلة انتظار رمز التحقق (10 دقائق).",
            target_mode="login",
            contact_point=contact,
            error="verification code timeout",
        )
        raise RuntimeError("انتهت مهلة انتظار رمز التحقق.")
    with AUTH_STATE_LOCK:
        code = AUTH_CODE_VALUE
        AUTH_CODE_VALUE = None
        cancelled = AUTH_STATE.get("state") == "cancelled"
    if cancelled or not code:
        set_auth_state(
            "cancelled",
            "تم إلغاء انتظار رمز التحقق.",
            target_mode="login",
            contact_point=contact,
        )
        raise RuntimeError("تم إلغاء عملية Login أثناء انتظار رمز التحقق.")
    set_auth_state(
        "verifying",
        "جارٍ التحقق من رمز Instagram…",
        target_mode="login",
        contact_point=contact,
    )
    return code


def redact_error(exc):
    value = str(exc).strip() or type(exc).__name__
    # Never echo credentials into browser/Telegram messages.
    for secret in (IG_PASSWORD, IG_USERNAME, TELEGRAM_TOKEN, NGROK_AUTH_TOKEN):
        if secret:
            value = value.replace(secret, "***")
    return value[:800]


def extract(obj, *keys, default=""):
    for key in keys:
        try:
            value = obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)
        except Exception:
            value = None
        if value not in (None, ""):
            return value
    return default


def as_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    try:
        return list(value)
    except Exception:
        return [value]


def dedupe_items(items):
    seen = set()
    out = []
    for item in items or []:
        key = extract(item, "pk", "id", "code", "shortcode", default="")
        key = str(key or "")
        if not key:
            key = repr(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def parse_shortcode_from_url(url):
    m = re.search(
        r"instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)",
        str(url or ""),
        flags=re.I,
    )
    return m.group(1) if m else ""


def is_rate_limit_error(exc):
    text = str(exc).lower()
    return "429" in text or "too many requests" in text or "rate limit" in text


# ============================================================
# 5. Telegram
# ============================================================
class TelegramSender:
    @staticmethod
    def _enabled():
        return bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)

    @staticmethod
    def _post(endpoint, data=None, files=None, timeout=20):
        if not TelegramSender._enabled():
            print("ℹ️ Telegram غير مهيأ؛ تخطي الإرسال.")
            return None
        try:
            return _http_session.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{endpoint}",
                data=data or {},
                files=files,
                timeout=timeout,
            )
        except Exception as exc:
            print(f"⚠️ Telegram {endpoint}: {redact_error(exc)}")
            return None

    @staticmethod
    def send_message(text, retries=2):
        if not TelegramSender._enabled():
            return False
        for attempt in range(retries + 1):
            r = TelegramSender._post(
                "sendMessage",
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": str(text)[:4096],
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": "true",
                },
                timeout=20,
            )
            if r is not None and r.status_code == 200:
                return True
            if attempt < retries:
                time.sleep(1.5)
        if r is not None:
            print(f"⚠️ Telegram sendMessage: HTTP {r.status_code} {r.text[:250]}")
        return False

    @staticmethod
    def send_message_html(html_text, retries=2):
        if not TelegramSender._enabled():
            return False
        for attempt in range(retries + 1):
            r = TelegramSender._post(
                "sendMessage",
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": str(html_text)[:4096],
                    "parse_mode": "HTML",
                    "disable_web_page_preview": "true",
                },
                timeout=20,
            )
            if r is not None and r.status_code == 200:
                return True
            if attempt < retries:
                time.sleep(1.5)
        if r is not None:
            print(f"⚠️ Telegram HTML: HTTP {r.status_code} {r.text[:250]}")
        return False

    @staticmethod
    def send_rich_message(html_text, reply_markup=None):
        # RichMessage is not available in every Bot API gateway.
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "rich_message": json.dumps({
                "html": str(html_text)[:32768],
                "is_rtl": True,
            }, ensure_ascii=False),
            "disable_notification": False,
        }
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)

        r = TelegramSender._post("sendRichMessage", data=payload, timeout=25)
        if r is not None and r.status_code == 200:
            return True

        # Fallback to HTML sendMessage.
        return TelegramSender.send_message_html(html_text)

    @staticmethod
    def _strip_html(value):
        value = re.sub(r"<[^>]+>", "", str(value or ""))
        return (
            value.replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&amp;", "&")
            .replace("&quot;", '"')
        )

    @staticmethod
    def _post_keyboard(url, profile_url=None):
        rows = [[{"text": "🔗 فتح المنشور", "url": url}]]
        if profile_url:
            rows.append([{"text": "👤 فتح الحساب", "url": profile_url}])
        return {"inline_keyboard": rows}

    @staticmethod
    def send_photo(path, caption="", reply_markup=None):
        if not TelegramSender._enabled() or not path or not os.path.exists(path):
            return False
        try:
            with open(path, "rb") as fh:
                data = {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "caption": str(caption)[:1024],
                    "parse_mode": "HTML",
                    "show_caption_above_media": "true",
                }
                if reply_markup:
                    data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
                r = TelegramSender._post(
                    "sendPhoto", data=data, files={"photo": fh}, timeout=60
                )
            if r is not None and r.status_code == 200:
                return True
            if r is not None:
                print(f"⚠️ Telegram photo: HTTP {r.status_code} {r.text[:250]}")

            # Caption fallback.
            with open(path, "rb") as fh:
                data["caption"] = TelegramSender._strip_html(caption)[:1024]
                r = TelegramSender._post(
                    "sendPhoto", data=data, files={"photo": fh}, timeout=60
                )
            return r is not None and r.status_code == 200
        except Exception as exc:
            print(f"⚠️ صورة: {redact_error(exc)}")
            return False

    @staticmethod
    def send_video(path, caption="", reply_markup=None):
        if not TelegramSender._enabled() or not path or not os.path.exists(path):
            return False
        try:
            with open(path, "rb") as fh:
                data = {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "caption": str(caption)[:1024],
                    "parse_mode": "HTML",
                    "show_caption_above_media": "true",
                    "supports_streaming": "true",
                }
                if reply_markup:
                    data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
                r = TelegramSender._post(
                    "sendVideo", data=data, files={"video": fh}, timeout=120
                )
            if r is not None and r.status_code == 200:
                return True
            if r is not None:
                print(f"⚠️ Telegram video: HTTP {r.status_code} {r.text[:250]}")

            with open(path, "rb") as fh:
                data["caption"] = TelegramSender._strip_html(caption)[:1024]
                r = TelegramSender._post(
                    "sendVideo", data=data, files={"video": fh}, timeout=120
                )
            return r is not None and r.status_code == 200
        except Exception as exc:
            print(f"⚠️ فيديو: {redact_error(exc)}")
            return False

    @staticmethod
    def send_document(path, caption=""):
        if not TelegramSender._enabled() or not path or not os.path.exists(path):
            return False
        try:
            with open(path, "rb") as fh:
                r = TelegramSender._post(
                    "sendDocument",
                    data={
                        "chat_id": TELEGRAM_CHAT_ID,
                        "caption": str(caption)[:1024],
                        "parse_mode": "HTML",
                    },
                    files={"document": fh},
                    timeout=120,
                )
            return r is not None and r.status_code == 200
        except Exception as exc:
            print(f"⚠️ ملف: {redact_error(exc)}")
            return False

    @staticmethod
    def send_follower_list(username, followers, title="👥 المتابعون"):
        followers = list(followers or [])
        if not followers:
            return False
        total = len(followers)
        for start in range(0, total, 20):
            chunk = followers[start:start + 20]
            lines = []
            for idx, user in enumerate(chunk, start + 1):
                uname = html_escape(str(extract(user, "username", default="") or ""))
                fullname = html_escape(str(extract(user, "full_name", "fullname", default="—") or "—"))
                lines.append(f"<b>{idx:03d}.</b> @{uname} — {fullname}")
            msg = (
                f"<b>{title} — @{html_escape(username)}</b>\n"
                f"📦 {start + 1}–{start + len(chunk)} من {total}\n\n"
                + "\n".join(lines)
            )
            TelegramSender.send_message_html(msg)
            time.sleep(0.5)
        return True


# ============================================================
# 6. Paths
# ============================================================
if IN_COLAB:
    if not os.path.exists("/content/drive/MyDrive"):
        drive.mount("/content/drive")
    BASE_PATH = "/content/drive/MyDrive/Instagram_Scraper_DB"
else:
    BASE_PATH = os.path.join(os.getcwd(), "Instagram_Scraper_DB")

DB_PATH = os.path.join(BASE_PATH, "instagram_data.db")
MEDIA_PATH = os.path.join(BASE_PATH, "media")
REPORTS_PATH = os.path.join(BASE_PATH, "reports")
STORIES_PATH = os.path.join(BASE_PATH, "stories")
HIGHLIGHTS_PATH = os.path.join(BASE_PATH, "highlights")
DB_BACKUP_PATH = os.path.join(BASE_PATH, "db_backups")

for folder in (BASE_PATH, MEDIA_PATH, REPORTS_PATH, STORIES_PATH, HIGHLIGHTS_PATH, DB_BACKUP_PATH):
    os.makedirs(folder, exist_ok=True)

print(f"📁 قاعدة البيانات: {DB_PATH}")


# ============================================================
# 7. Thumbnails
# ============================================================
class ThumbnailEngine:
    _cache = {}
    _max_cache = 500

    @classmethod
    def from_url(cls, url, max_size=THUMB_MAX_SIZE, quality=THUMB_QUALITY, cache_key=None):
        if not url or not PIL_AVAILABLE:
            return None
        key = cache_key or str(url)
        if key in cls._cache:
            return cls._cache[key]
        try:
            r = _http_session.get(str(url), timeout=20, stream=True)
            if r.status_code != 200:
                return None
            img = Image.open(io.BytesIO(r.content))
            img.thumbnail((max_size, max_size), Image.LANCZOS)
            if img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(
                buf, format="JPEG", quality=quality,
                optimize=THUMB_OPTIMIZE, progressive=True
            )
            encoded = base64.b64encode(buf.getvalue()).decode()
            if len(cls._cache) >= cls._max_cache:
                cls._cache.pop(next(iter(cls._cache)))
            cls._cache[key] = encoded
            return encoded
        except Exception as exc:
            print(f"⚠️ Thumbnail URL: {redact_error(exc)}")
            return None

    @staticmethod
    def from_file(path, max_size=THUMB_MAX_SIZE, quality=THUMB_QUALITY):
        if not path or not os.path.exists(path) or not PIL_AVAILABLE:
            return None
        if path.lower().endswith((".mp4", ".mov", ".webm")):
            return None
        thumb_path = path + ".thumb.jpg"
        try:
            if os.path.exists(thumb_path):
                with open(thumb_path, "rb") as fh:
                    return base64.b64encode(fh.read()).decode()
            img = Image.open(path)
            img.thumbnail((max_size, max_size), Image.LANCZOS)
            if img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(
                buf, format="JPEG", quality=quality,
                optimize=THUMB_OPTIMIZE, progressive=True
            )
            raw = buf.getvalue()
            try:
                with open(thumb_path, "wb") as fh:
                    fh.write(raw)
            except Exception:
                pass
            return base64.b64encode(raw).decode()
        except Exception as exc:
            print(f"⚠️ Thumbnail file: {redact_error(exc)}")
            return None

    @staticmethod
    def placeholder_svg(icon="📷"):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="150" height="150">'
            '<rect fill="#2a2a3e" width="150" height="150"/>'
            f'<text x="75" y="85" font-size="50" text-anchor="middle" fill="#e94560">{icon}</text>'
            "</svg>"
        )
        return base64.b64encode(svg.encode()).decode()


# ============================================================
# 8. SQLite — cumulative / non-destructive
# ============================================================
def db_connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def table_exists(cur, table):
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return cur.fetchone() is not None


def table_columns(cur, table):
    cur.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def backup_database(reason="migration"):
    if not os.path.exists(DB_PATH):
        return None
    try:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = os.path.join(
            DB_BACKUP_PATH,
            f"instagram_data_before_{safe_name(reason)}_{stamp}.sqlite"
        )
        src = db_connect()
        dst = sqlite3.connect(dest)
        with dst:
            src.backup(dst)
        dst.close()
        src.close()
        print(f"🛡️ DB backup: {dest}")
        return dest
    except Exception as exc:
        print(f"⚠️ DB backup: {redact_error(exc)}")
        return None


def init_db():
    conn = db_connect()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            full_name TEXT,
            bio TEXT,
            followers INTEGER,
            following INTEGER,
            posts_count INTEGER,
            is_private BOOLEAN,
            is_verified BOOLEAN,
            last_scraped DATETIME
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER,
            post_code TEXT UNIQUE,
            post_url TEXT,
            media_type TEXT,
            caption TEXT,
            like_count INTEGER,
            comment_count INTEGER,
            view_count INTEGER DEFAULT 0,
            play_count INTEGER DEFAULT 0,
            taken_at DATETIME,
            file_path TEXT,
            downloaded BOOLEAN DEFAULT 0,
            thumbnail_url TEXT,
            FOREIGN KEY(account_id) REFERENCES accounts(id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER,
            comment_text TEXT,
            commenter_username TEXT,
            commenter_full_name TEXT,
            created_at DATETIME,
            FOREIGN KEY(post_id) REFERENCES posts(id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS followers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER,
            username TEXT,
            full_name TEXT,
            first_seen DATETIME,
            UNIQUE(account_id, username),
            FOREIGN KEY(account_id) REFERENCES accounts(id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS following (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER,
            username TEXT,
            full_name TEXT,
            first_seen DATETIME,
            UNIQUE(account_id, username),
            FOREIGN KEY(account_id) REFERENCES accounts(id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS stories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER,
            story_pk TEXT UNIQUE,
            media_type TEXT,
            taken_at DATETIME,
            file_path TEXT,
            story_url TEXT,
            downloaded BOOLEAN DEFAULT 0,
            thumbnail_url TEXT,
            FOREIGN KEY(account_id) REFERENCES accounts(id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS highlights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER,
            highlight_pk TEXT UNIQUE,
            title TEXT,
            media_count INTEGER,
            cover_url TEXT,
            cover_path TEXT,
            downloaded BOOLEAN DEFAULT 0,
            FOREIGN KEY(account_id) REFERENCES accounts(id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS highlight_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            highlight_id INTEGER,
            item_pk TEXT UNIQUE,
            media_type TEXT,
            taken_at DATETIME,
            file_path TEXT,
            video_url TEXT,
            downloaded BOOLEAN DEFAULT 0,
            FOREIGN KEY(highlight_id) REFERENCES highlights(id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS search_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT,
            result_type TEXT,
            username TEXT,
            full_name TEXT,
            pk TEXT,
            extra_data TEXT,
            created_at DATETIME
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS mentions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT,
            source_type TEXT,
            source_pk TEXT,
            source_url TEXT,
            context TEXT,
            mentioned_username TEXT,
            mentioned_full_name TEXT,
            created_at DATETIME
        )
    """)

    changes = []
    if table_exists(cur, "accounts"):
        cols = table_columns(cur, "accounts")
        for col, decl in {
            "is_business": "INTEGER DEFAULT 0",
            "category": "TEXT DEFAULT ''",
            "external_url": "TEXT DEFAULT ''",
            "profile_pic": "TEXT DEFAULT ''",
        }.items():
            if col not in cols:
                changes.append(("accounts", col))
    if table_exists(cur, "stories"):
        cols = table_columns(cur, "stories")
        if "story_url" not in cols:
            changes.append(("stories", "story_url"))
        if "thumbnail_url" not in cols:
            changes.append(("stories", "thumbnail_url"))

    if changes:
        conn.commit()
        conn.close()
        backup_database("schema_migration")
        conn = db_connect()
        cur = conn.cursor()

        cols = table_columns(cur, "accounts")
        for col, decl in {
            "is_business": "INTEGER DEFAULT 0",
            "category": "TEXT DEFAULT ''",
            "external_url": "TEXT DEFAULT ''",
            "profile_pic": "TEXT DEFAULT ''",
        }.items():
            if col not in cols:
                cur.execute(f"ALTER TABLE accounts ADD COLUMN {col} {decl}")

        cols = table_columns(cur, "stories")
        if "story_url" not in cols:
            cur.execute("ALTER TABLE stories ADD COLUMN story_url TEXT DEFAULT ''")
        if "thumbnail_url" not in cols:
            cur.execute("ALTER TABLE stories ADD COLUMN thumbnail_url TEXT DEFAULT ''")

    # Dedupe comments without rewriting the existing table.
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_comments_dedupe
        ON comments(post_id, commenter_username, created_at, comment_text)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_posts_account_taken
        ON posts(account_id, taken_at)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_followers_account
        ON followers(account_id)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_following_account
        ON following(account_id)
    """)

    conn.commit()
    conn.close()
    print("✅ قاعدة البيانات جاهزة — البيانات القديمة محفوظة.")


def save_or_update_account(info):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO accounts (
            username, full_name, bio, followers, following, posts_count,
            is_private, is_verified, last_scraped,
            is_business, category, external_url, profile_pic
        )
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(username) DO UPDATE SET
            full_name=excluded.full_name,
            bio=excluded.bio,
            followers=excluded.followers,
            following=excluded.following,
            posts_count=excluded.posts_count,
            is_private=excluded.is_private,
            is_verified=excluded.is_verified,
            last_scraped=excluded.last_scraped,
            is_business=excluded.is_business,
            category=excluded.category,
            external_url=excluded.external_url,
            profile_pic=excluded.profile_pic
    """, (
        str(info.get("username", "")),
        str(info.get("full_name", "")),
        str(info.get("bio", "")),
        int(info.get("followers", 0) or 0),
        int(info.get("following", 0) or 0),
        int(info.get("posts_count", 0) or 0),
        int(bool(info.get("is_private", False))),
        int(bool(info.get("is_verified", False))),
        now_iso(),
        int(bool(info.get("is_business", False))),
        str(info.get("category", "")),
        str(info.get("external_url", "")),
        str(info.get("profile_pic", "")),
    ))
    conn.commit()
    conn.close()


def get_account_id(username):
    conn = db_connect()
    row = conn.execute(
        "SELECT id FROM accounts WHERE username=?",
        (str(username).lower().lstrip("@"),),
    ).fetchone()
    conn.close()
    return row[0] if row else None


def get_account_info(username):
    conn = db_connect()
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM accounts WHERE username=?",
        (str(username).lower().lstrip("@"),),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def save_post_if_not_exists(account_username, media):
    username = str(account_username).lower().lstrip("@")
    code = str(extract(media, "code", "shortcode", default="") or "")
    if not code:
        return None

    conn = db_connect()
    cur = conn.cursor()
    account = cur.execute(
        "SELECT id FROM accounts WHERE username=?",
        (username,),
    ).fetchone()
    if not account:
        conn.close()
        return None

    account_id = account[0]
    taken = extract(media, "taken_at", default=None)
    taken_str = taken.isoformat() if hasattr(taken, "isoformat") else (str(taken) if taken else "")
    cur.execute("""
        INSERT INTO posts (
            account_id, post_code, post_url, media_type, caption,
            like_count, comment_count, view_count, play_count, taken_at,
            file_path, downloaded, thumbnail_url
        )
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(post_code) DO UPDATE SET
            account_id=excluded.account_id,
            post_url=excluded.post_url,
            caption=excluded.caption,
            like_count=excluded.like_count,
            comment_count=excluded.comment_count,
            view_count=excluded.view_count,
            play_count=excluded.play_count,
            taken_at=excluded.taken_at,
            thumbnail_url=excluded.thumbnail_url
    """, (
        account_id,
        code,
        f"https://www.instagram.com/p/{code}/",
        str(extract(media, "media_type", "product_type", default="") or ""),
        str(extract(media, "caption_text", "caption", default="") or ""),
        int(extract(media, "like_count", "likes", default=0) or 0),
        int(extract(media, "comment_count", "comments", default=0) or 0),
        int(extract(media, "view_count", "views", default=0) or 0),
        int(extract(media, "play_count", "plays", default=0) or 0),
        taken_str,
        "",
        0,
        str(extract(media, "thumbnail_url", "display_url", default="") or ""),
    ))
    post_id = cur.execute(
        "SELECT id FROM posts WHERE post_code=?", (code,)
    ).fetchone()[0]
    conn.commit()
    conn.close()
    return post_id


def update_post_media(post_id, media_type, file_path, thumbnail_url=""):
    conn = db_connect()
    conn.execute("""
        UPDATE posts
        SET media_type=?, file_path=?, downloaded=?, thumbnail_url=?
        WHERE id=?
    """, (
        media_type,
        str(file_path or ""),
        int(bool(file_path and os.path.exists(file_path))),
        str(thumbnail_url or ""),
        post_id,
    ))
    conn.commit()
    conn.close()


def save_comment(post_id, comment):
    try:
        user = extract(comment, "user", default={}) or {}
        username = str(extract(user, "username", default="") or "")
        fullname = str(extract(user, "full_name", "fullname", default="") or "")
        text = str(extract(comment, "text", "comment_text", default="") or "")
        created = str(
            extract(comment, "created_at_utc", "created_at", default="") or ""
        )
        conn = db_connect()
        conn.execute("""
            INSERT INTO comments (
                post_id, comment_text, commenter_username,
                commenter_full_name, created_at
            )
            SELECT ?,?,?,?,?
            WHERE NOT EXISTS (
                SELECT 1 FROM comments
                WHERE post_id=?
                  AND commenter_username=?
                  AND created_at=?
                  AND comment_text=?
            )
        """, (
            post_id, text, username, fullname, created,
            post_id, username, created, text
        ))
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"⚠️ save_comment: {redact_error(exc)}")


def save_follower(account_id, user_obj):
    username = str(extract(user_obj, "username", default="") or "")
    if not username:
        return
    fullname = str(extract(user_obj, "full_name", "fullname", default="") or "")
    try:
        conn = db_connect()
        conn.execute("""
            INSERT INTO followers(account_id, username, full_name, first_seen)
            VALUES (?,?,?,?)
            ON CONFLICT(account_id, username) DO UPDATE SET
                full_name=excluded.full_name
        """, (account_id, username, fullname, now_iso()))
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"⚠️ save_follower: {redact_error(exc)}")


def save_following(account_id, user_obj):
    username = str(extract(user_obj, "username", default="") or "")
    if not username:
        return
    fullname = str(extract(user_obj, "full_name", "fullname", default="") or "")
    try:
        conn = db_connect()
        conn.execute("""
            INSERT INTO following(account_id, username, full_name, first_seen)
            VALUES (?,?,?,?)
            ON CONFLICT(account_id, username) DO UPDATE SET
                full_name=excluded.full_name
        """, (account_id, username, fullname, now_iso()))
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"⚠️ save_following: {redact_error(exc)}")


def save_story(account_id, story, file_path, story_url="", thumbnail_url=""):
    pk = str(extract(story, "pk", "id", default="") or "")
    if not pk:
        return False
    media_type = "video" if str(extract(story, "video_url", default="") or "") else "image"
    taken = extract(story, "taken_at", default=None)
    taken_str = taken.isoformat() if hasattr(taken, "isoformat") else str(taken or "")
    try:
        conn = db_connect()
        conn.execute("""
            INSERT INTO stories(
                account_id, story_pk, media_type, taken_at, file_path,
                story_url, downloaded, thumbnail_url
            )
            VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(story_pk) DO UPDATE SET
                account_id=excluded.account_id,
                media_type=excluded.media_type,
                taken_at=excluded.taken_at,
                file_path=excluded.file_path,
                story_url=excluded.story_url,
                downloaded=excluded.downloaded,
                thumbnail_url=excluded.thumbnail_url
        """, (
            account_id, pk, media_type, taken_str, str(file_path or ""),
            str(story_url or ""), int(bool(file_path and os.path.exists(file_path))),
            str(thumbnail_url or ""),
        ))
        conn.commit()
        conn.close()
        return True
    except Exception as exc:
        print(f"⚠️ save_story: {redact_error(exc)}")
        return False


def save_highlight_record(account_id, highlight, cover_path=""):
    pk = str(extract(highlight, "pk", "id", default="") or "")
    if not pk:
        return None
    try:
        conn = db_connect()
        conn.execute("""
            INSERT INTO highlights(
                account_id, highlight_pk, title, media_count,
                cover_url, cover_path, downloaded
            )
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(highlight_pk) DO UPDATE SET
                account_id=excluded.account_id,
                title=excluded.title,
                media_count=excluded.media_count,
                cover_url=excluded.cover_url,
                cover_path=excluded.cover_path,
                downloaded=excluded.downloaded
        """, (
            account_id,
            pk,
            str(extract(highlight, "title", "name", default="") or ""),
            int(extract(highlight, "media_count", "item_count", default=0) or 0),
            str(extract(highlight, "cover_url", "thumbnail_url", default="") or ""),
            str(cover_path or ""),
            int(bool(cover_path and os.path.exists(cover_path))),
        ))
        hid = conn.execute(
            "SELECT id FROM highlights WHERE highlight_pk=?", (pk,)
        ).fetchone()[0]
        conn.commit()
        conn.close()
        return hid
    except Exception as exc:
        print(f"⚠️ save_highlight: {redact_error(exc)}")
        return None


def save_highlight_item(highlight_id, item, file_path=""):
    pk = str(extract(item, "pk", "id", default="") or "")
    if not pk:
        return False
    taken = extract(item, "taken_at", default=None)
    taken_str = taken.isoformat() if hasattr(taken, "isoformat") else str(taken or "")
    media_type = "video" if extract(item, "video_url", default="") else "image"
    try:
        conn = db_connect()
        conn.execute("""
            INSERT INTO highlight_items(
                highlight_id, item_pk, media_type, taken_at, file_path,
                video_url, downloaded
            )
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(item_pk) DO UPDATE SET
                highlight_id=excluded.highlight_id,
                media_type=excluded.media_type,
                taken_at=excluded.taken_at,
                file_path=excluded.file_path,
                video_url=excluded.video_url,
                downloaded=excluded.downloaded
        """, (
            highlight_id, pk, media_type, taken_str, str(file_path or ""),
            str(extract(item, "video_url", default="") or ""),
            int(bool(file_path and os.path.exists(file_path))),
        ))
        conn.commit()
        conn.close()
        return True
    except Exception as exc:
        print(f"⚠️ save_highlight_item: {redact_error(exc)}")
        return False


def save_mention(query, source_type, source_pk, source_url, context,
                 mentioned_username="", mentioned_full_name=""):
    try:
        conn = db_connect()
        conn.execute("""
            INSERT INTO mentions(
                query, source_type, source_pk, source_url, context,
                mentioned_username, mentioned_full_name, created_at
            )
            VALUES(?,?,?,?,?,?,?,?)
        """, (
            str(query), source_type, str(source_pk or ""), str(source_url or ""),
            str(context or "")[:1000], str(mentioned_username or ""),
            str(mentioned_full_name or ""), now_iso(),
        ))
        conn.commit()
        conn.close()
        return True
    except Exception as exc:
        print(f"⚠️ save_mention: {redact_error(exc)}")
        return False


def get_posts_with_comments(account_id, limit=None, order="desc"):
    conn = db_connect()
    conn.row_factory = sqlite3.Row
    direction = "DESC" if order == "desc" else "ASC"
    limit_sql = f"LIMIT {int(limit)}" if limit else ""
    rows = conn.execute(
        f"SELECT * FROM posts WHERE account_id=? "
        f"ORDER BY taken_at {direction} {limit_sql}",
        (account_id,),
    ).fetchall()
    posts = [dict(row) for row in rows]
    for post in posts:
        comments = conn.execute(
            "SELECT * FROM comments WHERE post_id=? ORDER BY created_at ASC",
            (post["id"],),
        ).fetchall()
        post["comments"] = [dict(c) for c in comments]
    conn.close()
    return posts


def get_followers(account_id, limit=None):
    conn = db_connect()
    limit_sql = f"LIMIT {int(limit)}" if limit else ""
    rows = conn.execute(
        f"SELECT * FROM followers WHERE account_id=? "
        f"ORDER BY first_seen ASC {limit_sql}",
        (account_id,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_following(account_id, limit=None):
    conn = db_connect()
    limit_sql = f"LIMIT {int(limit)}" if limit else ""
    rows = conn.execute(
        f"SELECT * FROM following WHERE account_id=? "
        f"ORDER BY first_seen ASC {limit_sql}",
        (account_id,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_stories(account_id):
    conn = db_connect()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM stories WHERE account_id=? ORDER BY taken_at DESC",
        (account_id,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_highlights(account_id):
    conn = db_connect()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM highlights WHERE account_id=? ORDER BY title ASC",
        (account_id,),
    ).fetchall()
    result = [dict(row) for row in rows]
    for item in result:
        rows2 = conn.execute(
            "SELECT * FROM highlight_items WHERE highlight_id=? ORDER BY taken_at ASC",
            (item["id"],),
        ).fetchall()
        item["items"] = [dict(x) for x in rows2]
    conn.close()
    return result


# ============================================================
# 9. Instagram client manager — atomic Anonymous/Login switch
# ============================================================
class InstagramClient:
    _ig = None
    _mode = START_MODE if START_MODE in ("anonymous", "login") else "anonymous"
    _status = "starting"
    _last_error = ""
    _session_loaded = False
    _last_throttle_at = 0.0
    _last_challenge_at = 0.0
    _session_file = None
    _user_id_cache = {}
    _user_cache_file = (
        "/content/drive/MyDrive/ig_user_cache_v2.json"
        if IN_COLAB else "ig_user_cache_v2.json"
    )
    _THROTTLE_COOLDOWN = 300

    @classmethod
    def init_paths(cls):
        cls._session_file = os.path.join(BASE_PATH, "instaharvest_session.json")
        os.makedirs(BASE_PATH, exist_ok=True)

    @classmethod
    def _load_user_cache(cls):
        if not os.path.exists(cls._user_cache_file):
            return
        try:
            with open(cls._user_cache_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                cls._user_id_cache = {str(k): str(v) for k, v in data.items()}
                print(f"✅ User cache: {len(cls._user_id_cache)} مستخدم")
        except Exception as exc:
            print(f"⚠️ User cache: {redact_error(exc)}")

    @classmethod
    def _save_user_cache(cls):
        try:
            with open(cls._user_cache_file, "w", encoding="utf-8") as fh:
                json.dump(cls._user_id_cache, fh, ensure_ascii=False, indent=2)
        except Exception as exc:
            print(f"⚠️ حفظ User cache: {redact_error(exc)}")

    @classmethod
    def _set_error(cls, exc):
        cls._last_error = redact_error(exc)
        low = cls._last_error.lower()
        if is_rate_limit_error(exc):
            cls._last_throttle_at = time.time()
        if "challenge" in low or "verification" in low or "checkpoint" in low:
            cls._last_challenge_at = time.time()
        cls._status = "error"

    @classmethod
    def _mode_label(cls):
        return "🔓 Anonymous" if cls._mode == "anonymous" else "🔐 Login"

    @classmethod
    def get_status(cls):
        auth = get_auth_state()
        session_exists = bool(cls._session_file and os.path.exists(cls._session_file))
        job = get_job_state()
        active_mode = cls._mode
        pending_mode = auth.get("target_mode") if auth.get("state") not in ("idle", "ready", "error", "cancelled") else active_mode
        if cls._status == "ready":
            base_message = f"✅ {cls._mode_label()} — العميل جاهز"
        elif cls._status == "error":
            base_message = (
                f"❌ {cls._mode_label()} — "
                f"{html_escape(cls._last_error or 'خطأ غير محدد')}"
            )
        else:
            base_message = f"⏳ {cls._mode_label()} — {cls._status}"
        return {
            "mode": active_mode,
            "target_mode": pending_mode,
            "status": cls._status,
            "session_loaded": bool(cls._session_loaded),
            "session_saved": session_exists,
            "session_file": cls._session_file or "",
            "throttle_remaining": cls.throttle_remaining(),
            "last_error": cls._last_error,
            "auth": auth,
            "job": job,
            "message": base_message,
        }

    @classmethod
    def is_throttled(cls):
        return bool(
            cls._last_throttle_at and
            time.time() - cls._last_throttle_at < cls._THROTTLE_COOLDOWN
        )

    @classmethod
    def throttle_remaining(cls):
        if not cls._last_throttle_at:
            return 0
        return max(
            0,
            int(cls._THROTTLE_COOLDOWN - (time.time() - cls._last_throttle_at)),
        )

    @classmethod
    def mark_throttle(cls, exc=None):
        cls._last_throttle_at = time.time()
        if exc:
            cls._last_error = redact_error(exc)
        print(f"🛑 Instagram throttle: تهدئة {cls._THROTTLE_COOLDOWN}s")

    @classmethod
    def human_delay(cls, a=1.0, b=2.0):
        time.sleep(random.uniform(a, b))

    @classmethod
    def _build_anonymous(cls):
        print("🔓 إنشاء عميل Anonymous...")
        return Instagram.anonymous(unlimited=True)

    @classmethod
    def _build_login(cls):
        # The library explicitly supports a challenge_callback for email/SMS
        # verification. The callback is connected to the web panel instead of
        # blocking the Flask request.
        ig = Instagram(challenge_callback=instagram_challenge_callback)

        if cls._session_file and os.path.exists(cls._session_file):
            set_auth_state(
                "loading_session",
                "♻️ تحميل جلسة Login المحفوظة من Google Drive…",
                target_mode="login",
            )
            try:
                print(f"♻️ محاولة تحميل جلسة Login: {cls._session_file}")
                ig.auth.load_session(cls._session_file)
                valid = True
                if hasattr(ig.auth, "validate_session"):
                    validation = ig.auth.validate_session()
                    valid = validation is not False
                if valid:
                    print("✅ الجلسة المحفوظة صالحة — لا حاجة لإعادة كلمة المرور.")
                    return ig, True
                print("⚠️ الجلسة المحفوظة غير صالحة؛ سيتم إنشاء Login جديد.")
            except Exception as exc:
                if is_rate_limit_error(exc):
                    raise RuntimeError(
                        "Instagram أعاد 429 أثناء التحقق من الجلسة المحفوظة؛ "
                        "تم إيقاف Login ولن تتم محاولة كلمة المرور تلقائياً."
                    )
                print(f"⚠️ تعذر استخدام الجلسة المحفوظة: {redact_error(exc)}")

        if not IG_USERNAME or not IG_PASSWORD:
            raise RuntimeError(
                "لا توجد جلسة Login صالحة محفوظة، وبيانات "
                "IG_USERNAME/IG_PASSWORD غير موجودة في بيئة Colab."
            )

        set_auth_state(
            "authenticating",
            "🔐 جارٍ تسجيل الدخول باسم المستخدم وكلمة المرور…",
            target_mode="login",
        )
        print("🔐 بدء Login للحساب المهيأ في بيئة Colab.")
        # IMPORTANT: constructor-level challenge_callback is used by the
        # ChallengeHandler, but AuthAPI.login() requires the callback to be
        # passed explicitly for checkpoint/auth_platform flows in 1.1.88.
        result = ig.login(
            IG_USERNAME,
            IG_PASSWORD,
            challenge_callback=instagram_challenge_callback,
        )
        if result is False:
            raise RuntimeError("المكتبة أعادت False من ig.login().")

        if cls._session_file:
            try:
                os.makedirs(os.path.dirname(cls._session_file), exist_ok=True)
                ig.auth.save_session(cls._session_file)
                print(f"💾 تم حفظ جلسة Login بشكل دائم في Google Drive: {cls._session_file}")
            except Exception as exc:
                print(f"⚠️ تعذر حفظ الجلسة: {redact_error(exc)}")
                raise RuntimeError(
                    "نجح Login لكن تعذر حفظ الجلسة في Google Drive."
                ) from exc
        return ig, False

    @classmethod
    def _login_worker(cls, previous_ig, previous_mode,
                      previous_status, previous_session_loaded):
        try:
            ig, session_loaded = cls._build_login()
            with CLIENT_SWITCH_LOCK:
                cls._ig = ig
                cls._mode = "login"
                cls._status = "ready"
                cls._session_loaded = session_loaded
                cls._last_error = ""
            set_auth_state(
                "ready",
                (
                    "✅ Login جاهز باستخدام الجلسة المحفوظة."
                    if session_loaded
                    else "✅ تم تسجيل الدخول وحفظ الجلسة في Google Drive."
                ),
                target_mode="login",
            )
            set_job_state("done", "", "login", "Login جاهز")
            print("✅ تم تفعيل Login فعلياً.")
        except Exception as exc:
            cancelled = get_auth_state().get("state") == "cancelled"
            with CLIENT_SWITCH_LOCK:
                cls._ig = previous_ig
                cls._mode = previous_mode
                cls._status = previous_status if previous_ig is not None else "error"
                cls._session_loaded = previous_session_loaded
                cls._last_error = redact_error(exc)
                if is_rate_limit_error(exc):
                    cls._last_throttle_at = time.time()
            if cancelled:
                set_auth_state(
                    "cancelled",
                    "تم إلغاء Login؛ بقي الوضع السابق فعالاً.",
                    target_mode="login",
                )
                set_job_state("done", "", "login", "تم إلغاء Login")
            else:
                error_text = redact_error(exc)
                if "Email verification needed" in error_text:
                    user_message = (
                        "❌ Instagram طلب تحققًا بالبريد، لكن callback الخاص بالرمز "
                        "لم يُقبل من مسار Login الحالي."
                    )
                else:
                    user_message = (
                        "❌ فشل Login. راجع تفاصيل الخطأ ثم أعد المحاولة فقط عند الحاجة."
                    )
                set_auth_state(
                    "error",
                    user_message,
                    target_mode="login",
                    error=error_text,
                )
                set_job_state("error", "", "login", redact_error(exc))
            print(f"❌ Login: {redact_error(exc)}")
            traceback.print_exc()
        finally:
            with CLIENT_SWITCH_LOCK:
                cls._login_thread = None
            INSTAGRAM_JOB_LOCK.release()

    @classmethod
    def start_login(cls):
        with CLIENT_SWITCH_LOCK:
            if cls._mode == "login" and cls._ig is not None and cls._status == "ready":
                return cls.get_status()

            auth_state = get_auth_state()
            if auth_state.get("state") in {
                "starting", "loading_session", "authenticating",
                "waiting_code", "verifying"
            }:
                return cls.get_status()

            if not INSTAGRAM_JOB_LOCK.acquire(blocking=False):
                raise RuntimeError(
                    "لا يمكن بدء Login أثناء تنفيذ سكراب/بحث آخر."
                )

            previous_ig = cls._ig
            previous_mode = cls._mode
            previous_status = cls._status
            previous_session_loaded = cls._session_loaded
            cls._login_thread = True

            set_auth_state(
                "starting",
                "⏳ تم بدء Login في الخلفية. اضغط «تحديث الحالة» عند الحاجة.",
                target_mode="login",
            )
            set_job_state("starting", "", "login", "بدء عملية Login")

            thread = threading.Thread(
                target=cls._login_worker,
                args=(
                    previous_ig, previous_mode,
                    previous_status, previous_session_loaded
                ),
                daemon=True,
            )
            thread.start()
            return cls.get_status()

    @classmethod
    def switch_mode(cls, requested_mode):
        requested_mode = str(requested_mode or "").strip().lower()
        if requested_mode not in ("anonymous", "login"):
            raise ValueError("الوضع يجب أن يكون anonymous أو login")

        if requested_mode == "login":
            return cls.start_login()

        if get_auth_state().get("state") in {
            "starting", "loading_session", "authenticating",
            "waiting_code", "verifying"
        }:
            raise RuntimeError(
                "Login ما زال قيد التنفيذ؛ أكمل رمز التحقق أو اضغط «إلغاء Login» أولاً."
            )

        if not INSTAGRAM_JOB_LOCK.acquire(blocking=False):
            raise RuntimeError("لا يمكن تبديل الوضع أثناء تنفيذ مهمة سكراب/بحث.")

        try:
            with CLIENT_SWITCH_LOCK:
                old_ig = cls._ig
                try:
                    cls._status = "switching"
                    cls._last_error = ""
                    cls._ig = cls._build_anonymous()
                    cls._mode = "anonymous"
                    cls._session_loaded = False
                    cls._status = "ready"
                    set_auth_state(
                        "ready",
                        "✅ Anonymous جاهز.",
                        target_mode="anonymous",
                    )
                    print("✅ تم تفعيل Anonymous.")
                    return cls.get_status()
                except Exception as exc:
                    cls._ig = old_ig
                    cls._status = "ready" if old_ig is not None else "error"
                    cls._last_error = redact_error(exc)
                    set_auth_state(
                        "error",
                        "فشل تفعيل Anonymous.",
                        target_mode="anonymous",
                        error=redact_error(exc),
                    )
                    raise
        finally:
            INSTAGRAM_JOB_LOCK.release()

    @classmethod
    def get_client(cls):
        with CLIENT_SWITCH_LOCK:
            if cls._ig is not None and cls._status == "ready":
                return cls._ig

            cls._load_user_cache()
            try:
                cls._status = "starting"
                if cls._mode == "anonymous":
                    cls._ig = cls._build_anonymous()
                    cls._session_loaded = False
                    set_auth_state(
                        "ready",
                        "✅ Anonymous جاهز.",
                        target_mode="anonymous",
                    )
                else:
                    cls._ig, cls._session_loaded = cls._build_login()
                    cls._status = "ready"
                    set_auth_state(
                        "ready",
                        "✅ Login جاهز.",
                        target_mode="login",
                    )
                cls._status = "ready"
                cls._last_error = ""
                return cls._ig
            except Exception as exc:
                cls._ig = None
                cls._set_error(exc)
                set_auth_state(
                    "error",
                    "فشل تهيئة عميل Instagram.",
                    target_mode=cls._mode,
                    error=redact_error(exc),
                )
                print(f"❌ إنشاء عميل Instagram: {redact_error(exc)}")
                return None

    @classmethod
    def refresh_if_needed(cls):
        if cls._ig is None or cls._status != "ready":
            return False
        if cls.is_throttled():
            print(
                f"🛑 Instagram في تهدئة؛ المتبقي {cls.throttle_remaining()}s"
            )
            return False
        return True



# ============================================================
# 10. Universal Search
# ============================================================
class UniversalSearcher:
    def __init__(self, ig):
        self.ig = ig

    def analyze_query(self, query):
        q = str(query or "").strip()
        analysis = {
            "original": q,
            "clean": q,
            "type": "general",
            "keywords": [],
        }

        if re.search(r"instagram\.com/(?:p|reel|tv)/", q, re.I):
            analysis["type"] = "media_url"
            analysis["clean"] = q
            analysis["shortcode"] = parse_shortcode_from_url(q)
            return analysis

        profile_match = re.search(
            r"instagram\.com/([A-Za-z0-9_.]+)/?$", q, re.I
        )
        if profile_match:
            candidate = profile_match.group(1)
            if candidate.lower() not in {"p", "reel", "tv", "explore"}:
                analysis["type"] = "user"
                analysis["clean"] = candidate
                return analysis

        if q.startswith("#"):
            analysis["type"] = "hashtag"
            analysis["clean"] = q[1:].strip()
            return analysis

        if q.startswith("@"):
            analysis["type"] = "user"
            analysis["clean"] = q[1:].strip()
            return analysis

        analysis["keywords"] = [w for w in q.split() if len(w.strip()) >= 3]
        return analysis

    def _call(self, root, dotted, *args, **kwargs):
        current = root
        for part in dotted.split("."):
            if not hasattr(current, part):
                return None
            current = getattr(current, part)
        try:
            return current(*args, **kwargs)
        except TypeError:
            # Some versions expose a method with fewer optional args.
            if kwargs:
                try:
                    return current(*args)
                except Exception:
                    return None
            return None

    def _search_general(self, query, limit):
        results = []
        try:
            if hasattr(self.ig, "public") and hasattr(self.ig.public, "search"):
                results = as_list(self.ig.public.search(query))
        except Exception as exc:
            print(f"⚠️ public.search: {redact_error(exc)}")
        return results[:limit]

    def _hunt_mentions(self, query, max_posts=5, max_comments=20):
        tokens = [t.lstrip("#@") for t in re.findall(r"[A-Za-z0-9_.-]+", str(query or ""))]
        tokens = [t for t in tokens if len(t) >= 3][:3]
        mentions = []

        for token in tokens:
            try:
                posts = as_list(
                    self.ig.public.get_hashtag_posts(token, max_count=max_posts)
                )
            except Exception as exc:
                print(f"⚠️ mention hashtag @{token}: {redact_error(exc)}")
                continue

            for media in posts[:max_posts]:
                pk = str(extract(media, "pk", "id", default="") or "")
                code = str(extract(media, "code", "shortcode", default="") or "")
                source_url = f"https://www.instagram.com/p/{code}/"
                caption = str(extract(media, "caption_text", "caption", default="") or "")
                if token.lower() in caption.lower():
                    item = {
                        "type": "caption",
                        "source_pk": pk,
                        "source_url": source_url,
                        "context": caption[:500],
                        "mentioned_username": token,
                        "mentioned_full_name": "",
                    }
                    mentions.append(item)
                    save_mention(
                        token, "caption", pk, source_url, caption[:500], token, ""
                    )

                try:
                    comments = as_list(
                        self._call(
                            self.ig.public, "get_comments",
                            code, max_count=max_comments
                        )
                    )
                except Exception:
                    comments = []

                for comment in comments:
                    text_value = str(
                        extract(comment, "text", "comment_text", default="") or ""
                    )
                    if token.lower() not in text_value.lower():
                        continue
                    user = extract(comment, "user", default={}) or {}
                    uname = str(extract(user, "username", default="") or "")
                    fullname = str(
                        extract(user, "full_name", "fullname", default="") or ""
                    )
                    item = {
                        "type": "comment",
                        "source_pk": pk,
                        "source_url": source_url,
                        "context": text_value[:500],
                        "commenter": uname,
                        "commenter_full_name": fullname,
                    }
                    mentions.append(item)
                    save_mention(
                        token, "comment", pk, source_url,
                        text_value[:500], uname, fullname
                    )
            InstagramClient.human_delay(0.3, 0.7)

        return mentions

    def search(self, query, max_results_per_type=10, hunt_mentions=False):
        if not InstagramClient.refresh_if_needed():
            raise RuntimeError("عميل Instagram غير جاهز أو في فترة تهدئة.")

        analysis = self.analyze_query(query)
        limit = safe_int(max_results_per_type, 10, 1, 50)
        results = {
            "analysis": analysis,
            "users": [],
            "hashtags": [],
            "posts": [],
            "reels": [],
            "mentions": [],
            "stats": {"total": 0},
        }
        print(f"🔍 بحث: {query} | النوع={analysis['type']}")

        if analysis["type"] == "user":
            username = analysis["clean"]
            try:
                if InstagramClient._mode == "login" and hasattr(self.ig, "users"):
                    user = self.ig.users.get_full_profile(username)
                else:
                    user = self.ig.public.get_profile(username)
                if user:
                    results["users"] = [user]
            except Exception as exc:
                print(f"⚠️ profile: {redact_error(exc)}")

            try:
                results["posts"] = as_list(
                    self.ig.public.get_posts(username, max_count=limit)
                )[:limit]
            except Exception as exc:
                print(f"⚠️ user posts public: {redact_error(exc)}")
                if InstagramClient._mode == "login":
                    try:
                        uid = str(extract(results["users"][0], "pk", "id", default=""))
                        results["posts"] = as_list(
                            self._call(
                                self.ig.feed, "get_all_posts",
                                uid, max_posts=limit, delay=1.0
                            )
                        )[:limit]
                    except Exception as exc2:
                        print(f"⚠️ user posts private fallback: {redact_error(exc2)}")

            try:
                results["reels"] = as_list(
                    self.ig.public.get_reels(username, max_count=limit)
                )[:limit]
            except Exception:
                pass

        elif analysis["type"] == "media_url":
            url = analysis["clean"]
            try:
                media = self.ig.public.get_post_by_url(url)
                if media:
                    results["posts"] = [media]
            except Exception as exc:
                print(f"⚠️ get_post_by_url: {redact_error(exc)}")
                if analysis.get("shortcode") and hasattr(self.ig, "media"):
                    try:
                        media = self.ig.media.get_by_shortcode(analysis["shortcode"])
                        if media:
                            results["posts"] = [media]
                    except Exception as exc2:
                        print(f"⚠️ media.get_by_shortcode: {redact_error(exc2)}")

        elif analysis["type"] == "hashtag":
            tag = analysis["clean"]
            try:
                posts = as_list(
                    self.ig.public.get_hashtag_posts(tag, max_count=limit)
                )
                results["posts"] = posts[:limit]
                results["hashtags"] = [{
                    "name": tag,
                    "media_count": len(posts),
                }]
            except Exception as exc:
                print(f"⚠️ hashtag posts: {redact_error(exc)}")
                if hasattr(self.ig, "search"):
                    try:
                        results["hashtags"] = as_list(
                            self.ig.search.search_hashtags(tag)
                        )[:limit]
                    except Exception:
                        pass

        else:
            results["users"] = self._search_general(query, limit)
            if InstagramClient._mode == "login" and hasattr(self.ig, "search"):
                try:
                    results["users"] = as_list(
                        self.ig.search.search_users(query)
                    )[:limit] or results["users"]
                except Exception:
                    pass
                try:
                    results["hashtags"] = as_list(
                        self.ig.search.search_hashtags(query)
                    )[:limit]
                except Exception:
                    pass

        if hunt_mentions:
            results["mentions"] = self._hunt_mentions(
                query, max_posts=min(5, limit), max_comments=20
            )

        results["stats"]["total"] = sum(
            len(results[k]) for k in
            ("users", "hashtags", "posts", "reels", "mentions")
        )
        self._save_search_results(query, results)
        self._send_summary(query, results)
        return results

    def _save_search_results(self, query, results):
        try:
            conn = db_connect()
            for rtype in ("users", "hashtags", "posts", "reels", "mentions"):
                for item in results.get(rtype, []):
                    username = str(extract(
                        item, "username", "commenter", default=""
                    ) or "")
                    fullname = str(extract(
                        item, "full_name", "fullname",
                        "commenter_full_name", default=""
                    ) or "")
                    pk = str(extract(
                        item, "pk", "id", "source_pk", default=""
                    ) or "")
                    conn.execute("""
                        INSERT INTO search_results(
                            query, result_type, username, full_name,
                            pk, extra_data, created_at
                        ) VALUES(?,?,?,?,?,?,?)
                    """, (
                        query, rtype, username, fullname, pk, "{}",
                        now_iso(),
                    ))
            conn.commit()
            conn.close()
        except Exception as exc:
            print(f"⚠️ حفظ نتائج البحث: {redact_error(exc)}")

    def _send_summary(self, query, results):
        lines = [
            "<b>🔍 نتائج البحث الشامل</b>",
            f"📝 {html_escape(str(query))}",
        ]
        if results["users"]:
            lines.append(f"👤 المستخدمون: {len(results['users'])}")
            for user in results["users"][:5]:
                lines.append(
                    f"• @{html_escape(str(extract(user, 'username', default='?')))}"
                )
        if results["hashtags"]:
            lines.append(f"#️⃣ الهاشتاجات: {len(results['hashtags'])}")
        if results["posts"]:
            lines.append(f"📸 المنشورات: {len(results['posts'])}")
        if results["reels"]:
            lines.append(f"🎬 الريلز: {len(results['reels'])}")
        if results["mentions"]:
            lines.append(f"🔎 المطابقات النصية: {len(results['mentions'])}")
        TelegramSender.send_message_html("\n".join(lines))

    def generate_search_report(self, query, results):
        sections = []
        if results["users"]:
            cards = []
            for user in results["users"]:
                username = html_escape(str(extract(user, "username", default="?") or "?"))
                fullname = html_escape(str(
                    extract(user, "full_name", "fullname", default="") or ""
                ))
                followers = int(
                    extract(user, "follower_count", "followers", default=0) or 0
                )
                pic_url = str(extract(
                    user, "profile_pic_url", "profile_pic_url_hd", default=""
                ) or "")
                b64 = ThumbnailEngine.from_url(pic_url, 80, 50, f"user_{username}") if pic_url else None
                pic = (
                    f'<img src="data:image/jpeg;base64,{b64}" class="user-pic">'
                    if b64 else '<div class="user-pic placeholder">👤</div>'
                )
                cards.append(
                    f'<div class="user-card">{pic}<div>'
                    f'<b>@{username}</b><div>{fullname}</div>'
                    f'<small>👥 {followers:,}</small></div></div>'
                )
            sections.append(
                f'<section class="section"><h2>👤 المستخدمون ({len(cards)})</h2>'
                f'<div class="users-grid">{"".join(cards)}</div></section>'
            )

        if results["posts"]:
            cards = []
            for media in results["posts"]:
                code = html_escape(str(extract(media, "code", "shortcode", default="") or ""))
                caption = html_escape(str(
                    extract(media, "caption_text", "caption", default="") or ""
                )[:250])
                likes = int(extract(media, "like_count", "likes", default=0) or 0)
                comments = int(extract(media, "comment_count", "comments", default=0) or 0)
                thumb_url = str(extract(
                    media, "thumbnail_url", "display_url", default=""
                ) or "")
                b64 = ThumbnailEngine.from_url(
                    thumb_url, THUMB_MAX_SIZE, THUMB_QUALITY, f"search_{code}"
                ) if thumb_url else None
                if not b64:
                    b64 = ThumbnailEngine.placeholder_svg("📷")
                cards.append(
                    f'<div class="search-card">'
                    f'<a href="https://www.instagram.com/p/{code}" target="_blank">'
                    f'<img src="data:image/jpeg;base64,{b64}" class="search-thumb"></a>'
                    f'<div class="search-info"><div>❤️ {likes:,} · 💬 {comments:,}</div>'
                    f'<div>{caption}</div>'
                    f'<a href="https://www.instagram.com/p/{code}" target="_blank">🔗 فتح</a></div>'
                    f'</div>'
                )
            sections.append(
                f'<section class="section"><h2>📸 المنشورات ({len(cards)})</h2>'
                f'<div class="search-grid">{"".join(cards)}</div></section>'
            )

        if results["mentions"]:
            rows = []
            for mention in results["mentions"]:
                rows.append(
                    f'<div class="mention-row"><b>{html_escape(str(mention.get("type","")))}</b> '
                    f'· {html_escape(str(mention.get("context",""))[:500])} '
                    f'<a href="{html_escape(str(mention.get("source_url","")))}" target="_blank">فتح</a></div>'
                )
            sections.append(
                f'<section class="section"><h2>🔎 Mention Hunter ({len(rows)})</h2>'
                + "".join(rows) + "</section>"
            )

        css = """
*{box-sizing:border-box}
body{font-family:Arial,sans-serif;background:linear-gradient(135deg,#1a1a2e,#0f3460);
color:#e9edf2;padding:16px;line-height:1.6}
.container{max-width:1100px;margin:auto}
.header,.section{background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);
border-radius:16px;padding:20px;margin-bottom:16px}
.header{text-align:center;background:linear-gradient(135deg,#e94560,#c73659)}
.users-grid,.search-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}
.user-card,.search-card{background:rgba(0,0,0,.22);border-radius:12px;padding:12px}
.user-card{display:flex;gap:12px}
.user-pic{width:70px;height:70px;border-radius:50%;object-fit:cover;flex:none}
.search-card{display:flex;gap:10px}
.search-thumb{width:130px;height:130px;object-fit:cover;border-radius:9px}
.search-info{padding:4px;overflow-wrap:anywhere}
.placeholder{display:flex;align-items:center;justify-content:center;background:#24263b}
a{color:#7cc7ff}
.mention-row{padding:10px;border-bottom:1px solid rgba(255,255,255,.1)}
"""
        safe_query = safe_name(query, "search")
        report = (
            "<!doctype html><html lang='ar' dir='rtl'><head><meta charset='utf-8'>"
            f"<title>بحث — {html_escape(str(query))}</title><style>{css}</style></head>"
            "<body><div class='container'>"
            f"<div class='header'><h1>🔍 تقرير البحث</h1><div>{html_escape(str(query))}</div>"
            f"<small>{now_iso()}</small></div>"
            + "".join(sections)
            + "</div></body></html>"
        )
        path = os.path.join(
            REPORTS_PATH,
            f"search_{safe_query}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        )
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(report)
        return path


# ============================================================
# 11. Main scraper
# ============================================================
class InstagramSearcher:
    def __init__(self):
        self.ig = InstagramClient.get_client()
        if self.ig is None:
            raise RuntimeError("فشل الحصول على عميل Instagram.")
        self.universal_searcher = UniversalSearcher(self.ig)

    @staticmethod
    def _profile_username(username):
        return str(username or "").strip().lstrip("@").lower()

    def _call(self, root, dotted, *args, **kwargs):
        current = root
        for part in dotted.split("."):
            if not hasattr(current, part):
                return None
            current = getattr(current, part)
        try:
            return current(*args, **kwargs)
        except TypeError:
            try:
                return current(*args)
            except Exception:
                return None
        except Exception:
            raise

    def _get_user_id(self, username):
        username = self._profile_username(username)
        if not username:
            return None
        if username in InstagramClient._user_id_cache:
            return InstagramClient._user_id_cache[username]
        try:
            # CRITICAL: Anonymous uses the known-good public method.
            user = self.ig.public.get_profile(username)
            uid = str(extract(user, "pk", "id", default="") or "")
            if uid:
                InstagramClient._user_id_cache[username] = uid
                InstagramClient._save_user_cache()
            return uid or None
        except Exception as exc:
            print(f"⚠️ get_user_id @{username}: {redact_error(exc)}")
            return None

    def get_profile_info(self, username):
        username = self._profile_username(username)
        if not username:
            return None
        if not InstagramClient.refresh_if_needed():
            raise RuntimeError("عميل Instagram غير جاهز.")

        print(f"📊 معلومات الحساب: @{username}")
        try:
            if InstagramClient._mode == "login" and hasattr(self.ig, "users"):
                user = self.ig.users.get_full_profile(username)
            else:
                # CRITICAL Anonymous core path.
                user = self.ig.public.get_profile(username)

            if not user:
                raise RuntimeError(f"الحساب @{username} غير موجود أو تعذر جلبه.")

            uid = str(extract(user, "pk", "id", default="") or "")
            if uid:
                InstagramClient._user_id_cache[username] = uid
                InstagramClient._save_user_cache()

            info = {
                "username": str(extract(user, "username", default=username) or username).lower().lstrip("@"),
                "full_name": str(extract(user, "full_name", "fullname", default="") or ""),
                "bio": str(extract(user, "biography", "bio", default="") or ""),
                "posts_count": int(extract(user, "media_count", "posts_count", default=0) or 0),
                "followers": int(extract(user, "follower_count", "followers", default=0) or 0),
                "following": int(extract(user, "following_count", "following", default=0) or 0),
                "profile_pic": str(extract(
                    user, "profile_pic_url_hd", "profile_pic_url", default=""
                ) or ""),
                "is_private": bool(extract(user, "is_private", default=False)),
                "is_verified": bool(extract(user, "is_verified", default=False)),
                "is_business": bool(extract(
                    user, "is_business", "is_professional_account", default=False
                )),
                "category": str(extract(
                    user, "category", "business_category_name", default=""
                ) or ""),
                "external_url": str(extract(user, "external_url", default="") or ""),
            }
            save_or_update_account(info)

            uname = html_escape(info["username"])
            profile_link = f"https://www.instagram.com/{uname}/"
            status = "🔒 خاص" if info["is_private"] else "🌐 عام"
            verified = "✅ موثق" if info["is_verified"] else "— غير موثق"
            account_type = "💼 احترافي" if info["is_business"] else "👤 شخصي"

            html = (
                f"<b>👤 @{uname}</b>\n"
                f"<b>{html_escape(info['full_name'] or '—')}</b>\n\n"
                f"📊 <b>إحصائيات</b>\n"
                f"• المنشورات: <b>{info['posts_count']:,}</b>\n"
                f"• المتابعون: <b>{info['followers']:,}</b>\n"
                f"• يتابع: <b>{info['following']:,}</b>\n"
                f"• الحالة: <b>{status}</b>\n"
                f"• التوثيق: <b>{verified}</b>\n"
                f"• النوع: <b>{account_type}</b>\n"
                f"• الفئة: <b>{html_escape(info['category'] or '—')}</b>\n\n"
                f"<blockquote expandable>{html_escape(info['bio'][:1000] or 'لا يوجد')}</blockquote>"
            )
            if info["external_url"]:
                html += f'\n🌐 <a href="{html_escape(info["external_url"])}">الموقع الخارجي</a>'
            html += f'\n\n<a href="{profile_link}">🔗 فتح الحساب على Instagram</a>'

            folder = os.path.join(MEDIA_PATH, username)
            os.makedirs(folder, exist_ok=True)

            if info["profile_pic"]:
                pic_path = os.path.join(folder, "profile_pic.jpg")
                try:
                    if not os.path.exists(pic_path) or os.path.getsize(pic_path) == 0:
                        r = _http_session.get(info["profile_pic"], timeout=30)
                        if r.status_code == 200:
                            with open(pic_path, "wb") as fh:
                                fh.write(r.content)
                    if os.path.exists(pic_path):
                        TelegramSender.send_photo(
                            pic_path,
                            html,
                            reply_markup=TelegramSender._post_keyboard(profile_link),
                        )
                    else:
                        TelegramSender.send_rich_message(
                            html,
                            reply_markup=TelegramSender._post_keyboard(profile_link),
                        )
                except Exception as exc:
                    print(f"⚠️ profile picture: {redact_error(exc)}")
                    TelegramSender.send_rich_message(
                        html,
                        reply_markup=TelegramSender._post_keyboard(profile_link),
                    )
            else:
                TelegramSender.send_rich_message(
                    html,
                    reply_markup=TelegramSender._post_keyboard(profile_link),
                )
            return info

        except Exception as exc:
            print(f"❌ get_profile_info: {redact_error(exc)}")
            raise

    def _fetch_posts(self, username, max_count=MAX_TRAVERSE_LIMIT):
        username = self._profile_username(username)
        print(
            f"📸 جلب المنشورات الأساسية لـ @{username} "
            f"(mode={InstagramClient._mode_label()})..."
        )

        # CRITICAL: preserve the exact known-working public call first.
        try:
            posts = self.ig.public.get_posts(username, max_count=max_count)
            posts = list(posts or [])
            print(f"📦 public.get_posts → {len(posts)}")
            if posts:
                return posts
        except Exception as exc:
            print(f"⚠️ public.get_posts: {redact_error(exc)}")
            if is_rate_limit_error(exc):
                InstagramClient.mark_throttle(exc)
                return []

        # Only Login mode gets a private fallback.
        if InstagramClient._mode == "login":
            uid = self._get_user_id(username)
            if uid and hasattr(self.ig, "feed"):
                try:
                    posts = self._call(
                        self.ig.feed, "get_all_posts",
                        uid, max_posts=max_count, delay=1.0
                    )
                    posts = list(posts or [])
                    print(f"📦 feed.get_all_posts fallback → {len(posts)}")
                    return posts
                except Exception as exc:
                    print(f"⚠️ feed.get_all_posts: {redact_error(exc)}")
        return []

    def _fetch_reels(self, username, max_count=50):
        try:
            reels = self.ig.public.get_reels(
                self._profile_username(username),
                max_count=max_count
            )
            return list(reels or [])
        except Exception as exc:
            print(f"⚠️ get_reels: {redact_error(exc)}")
            return []

    def _fetch_comments(self, media, max_comments=20):
        code = str(extract(media, "code", "shortcode", default="") or "")
        if code:
            try:
                comments = self.ig.public.get_comments(code, max_count=max_comments)
                return list(comments or [])[:max_comments]
            except Exception as exc:
                if is_rate_limit_error(exc):
                    InstagramClient.mark_throttle(exc)
        if InstagramClient._mode == "login":
            pk = str(extract(media, "pk", "id", default="") or "")
            if pk and hasattr(self.ig, "media"):
                try:
                    comments = self.ig.media.get_comments_parsed(pk)
                    return list(comments or [])[:max_comments]
                except Exception as exc:
                    print(f"⚠️ private comments: {redact_error(exc)}")
        return []

    def _fetch_followers(self, username, max_count=100):
        if InstagramClient._mode != "login":
            print("ℹ️ Followers تتطلب Login.")
            return []
        uid = self._get_user_id(username)
        if not uid:
            return []
        try:
            users = self._call(self.ig, "friendships.get_all_followers", uid)
            if users is None:
                users = self._call(self.ig, "graphql.get_followers", uid)
            users = list(users or [])
            print(f"👥 followers → {len(users)}")
            return users[:max_count]
        except Exception as exc:
            print(f"⚠️ followers: {redact_error(exc)}")
            return []

    def _fetch_following(self, username, max_count=100):
        if InstagramClient._mode != "login":
            print("ℹ️ Following تتطلب Login.")
            return []
        uid = self._get_user_id(username)
        if not uid:
            return []
        try:
            users = self._call(self.ig, "friendships.get_all_following", uid)
            users = list(users or [])
            print(f"➡️ following → {len(users)}")
            return users[:max_count]
        except Exception as exc:
            print(f"⚠️ following: {redact_error(exc)}")
            return []

    def _fetch_stories(self, username, max_count=50):
        if InstagramClient._mode != "login":
            print("ℹ️ Stories في الوضع Anonymous غير مفعلة عمداً.")
            return []

        uid = self._get_user_id(username)
        if not uid:
            return []
        try:
            items = self._call(self.ig, "stories.get_user_stories", uid)
            items = list(items or [])
            print(f"📖 stories.get_user_stories → {len(items)}")
            return items[:max_count]
        except Exception as exc:
            print(f"⚠️ Stories: {redact_error(exc)}")
            return []

    def _fetch_highlights(self, username, max_count=20):
        username = self._profile_username(username)
        try:
            # Public highlights are attempted first in Anonymous mode.
            if InstagramClient._mode == "anonymous" and hasattr(self.ig, "public"):
                items = self._call(self.ig.public, "get_highlights", username)
                items = list(items or [])
                print(f"📌 public highlights → {len(items)}")
                return items[:max_count]

            uid = self._get_user_id(username)
            if not uid:
                return []

            items = self._call(self.ig, "stories.get_highlights_tray", uid)
            items = list(items or [])
            if not items and hasattr(self.ig, "public"):
                items = self._call(self.ig.public, "get_highlights", username)
                items = list(items or [])
            print(f"📌 highlights → {len(items)}")
            return items[:max_count]
        except Exception as exc:
            print(f"⚠️ Highlights: {redact_error(exc)}")
            return []

    def _download_url(self, url, path, timeout=90):
        if not url:
            return False
        try:
            if os.path.exists(path) and os.path.getsize(path) > 0:
                return True

            headers = {"Referer": "https://www.instagram.com/"}
            with _http_session.get(
                str(url), headers=headers, timeout=timeout, stream=True
            ) as r:
                if r.status_code != 200:
                    print(f"      ⚠️ media HTTP {r.status_code}")
                    return False
                with open(path, "wb") as fh:
                    for chunk in r.iter_content(1024 * 64):
                        if chunk:
                            fh.write(chunk)
            return os.path.exists(path) and os.path.getsize(path) > 0
        except Exception as exc:
            print(f"      ⚠️ download_url: {redact_error(exc)}")
            return False

    def _normalize_native_download(self, result, folder, basename):
        output = []
        values = as_list(result)
        for idx, value in enumerate(values, 1):
            if value is None:
                continue
            try:
                source = os.fspath(value)
            except TypeError:
                source = str(value)
            if not os.path.exists(source):
                continue

            ext = os.path.splitext(source)[1].lower() or ".jpg"
            target_name = f"{basename}" if len(values) == 1 else f"{basename}_{idx:02d}"
            target = os.path.join(folder, target_name + ext)
            if os.path.abspath(source) != os.path.abspath(target):
                try:
                    shutil.copy2(source, target)
                    source = target
                except Exception:
                    pass
            kind = "video" if ext in (".mp4", ".mov", ".webm") else "image"
            output.append((source, kind))
        return output

    def _media_parts(self, media):
        carousel = extract(
            media, "carousel_media", "carousel_media_items", default=[]
        )
        carousel = as_list(carousel)
        if len(carousel) > 1:
            return carousel
        return [media]

    def _download_media(self, media, folder):
        os.makedirs(folder, exist_ok=True)
        code = str(extract(
            media, "code", "shortcode", "pk", "id", default="media"
        ) or "media")

        # Login: use the library downloader first.
        if InstagramClient._mode == "login":
            pk = str(extract(media, "pk", "id", default="") or "")
            if pk and hasattr(self.ig, "download"):
                try:
                    got = self.ig.download.download_media(pk)
                    normalized = self._normalize_native_download(
                        got, folder, safe_name(code)
                    )
                    if normalized:
                        return normalized
                except Exception as exc:
                    print(f"      ⚠️ native media download: {redact_error(exc)}")

        parts = self._media_parts(media)
        output = []
        for idx, part in enumerate(parts, 1):
            video_url = str(extract(part, "video_url", default="") or "")
            image_url = str(extract(
                part, "display_url", "thumbnail_url", "image_url", default=""
            ) or "")
            url = video_url or image_url
            if not url:
                continue

            ext = ".mp4" if video_url else ".jpg"
            suffix = "" if len(parts) == 1 else f"_{idx:02d}"
            target = os.path.join(folder, safe_name(code) + suffix + ext)
            if not self._download_url(url, target):
                continue
            output.append((target, "video" if video_url else "image"))
            print(f"      ✅ نُزّل: {os.path.basename(target)}")

        return output

    def _download_story(self, story, folder, index=1):
        os.makedirs(folder, exist_ok=True)
        pk = str(extract(story, "pk", "id", default=f"story_{index}") or f"story_{index}")
        video_url = str(extract(story, "video_url", default="") or "")
        image_url = str(extract(
            story, "display_url", "thumbnail_url", "image_url", "url", default=""
        ) or "")
        url = video_url or image_url
        if not url:
            return None
        ext = ".mp4" if video_url else ".jpg"
        path = os.path.join(folder, f"story_{safe_name(pk)}{ext}")
        if self._download_url(url, path, timeout=90):
            return path
        return None

    def _download_highlight_item(self, item, folder, index=1):
        os.makedirs(folder, exist_ok=True)
        pk = str(extract(item, "pk", "id", default=f"item_{index}") or f"item_{index}")
        video_url = str(extract(item, "video_url", default="") or "")
        image_url = str(extract(
            item, "display_url", "thumbnail_url", "image_url", "url", default=""
        ) or "")
        url = video_url or image_url
        if not url:
            return None
        ext = ".mp4" if video_url else ".jpg"
        path = os.path.join(folder, f"item_{safe_name(pk)}{ext}")
        if self._download_url(url, path, timeout=90):
            return path
        return None

    def _fetch_highlight_items(self, highlight):
        items = extract(highlight, "items", "stories", "media", default=None)
        if items:
            return as_list(items)

        pk = str(extract(highlight, "pk", "id", default="") or "")
        if not pk:
            return []

        # Use a method when this particular library build exposes one.
        for dotted in (
            "stories.get_highlight_items",
            "stories.get_highlight",
            "public.get_highlight",
        ):
            try:
                got = self._call(self.ig, dotted, pk)
                if got:
                    if isinstance(got, dict) and "items" in got:
                        return as_list(got["items"])
                    return as_list(got)
            except Exception:
                pass
        return []

    def _post_caption_html(self, media, index, total, username):
        code = html_escape(str(
            extract(media, "code", "shortcode", default="") or ""
        ))
        url = f"https://www.instagram.com/p/{code}/"
        date = extract(media, "taken_at", default=None)
        if hasattr(date, "strftime"):
            date_text = date.strftime("%Y-%m-%d %H:%M")
        else:
            date_text = html_escape(str(date or "غير معروف"))
        caption = html_escape(str(
            extract(media, "caption_text", "caption", default="(لا يوجد نص)") or ""
        )[:800])
        likes = int(extract(media, "like_count", "likes", default=0) or 0)
        comments = int(extract(media, "comment_count", "comments", default=0) or 0)
        views = int(extract(media, "view_count", "views", default=0) or 0)
        plays = int(extract(media, "play_count", "plays", default=0) or 0)
        metric = f"👁️ {views:,}" if views else (f"▶️ {plays:,}" if plays else "👁️ —")
        profile_url = f"https://www.instagram.com/{html_escape(username)}/"
        return (
            f"<b>📌 المنشور #{index:02d} / {total:02d}</b> • <code>{code}</code>\n"
            f"👤 <a href='{profile_url}'>@{html_escape(username)}</a>\n"
            f"📅 {date_text}\n"
            f"❤️ {likes:,}   💬 {comments:,}   {metric}\n\n"
            f"<blockquote>{caption}</blockquote>\n"
            f"<a href='{url}'>🔗 فتح المنشور على Instagram</a>"
        )

    def _process_media(self, media, username, folder, index, total,
                       fetch_comments=False, max_comments=20):
        try:
            post_id = save_post_if_not_exists(username, media)
            if post_id is None:
                print(f"      ⚠️ لم يتم حفظ الحساب @{username} في DB.")
                return False

            files = self._download_media(media, folder)
            if not files:
                update_post_media(
                    post_id,
                    str(extract(media, "media_type", "product_type", default="") or ""),
                    "",
                    str(extract(media, "thumbnail_url", "display_url", default="") or ""),
                )
                TelegramSender.send_rich_message(
                    self._post_caption_html(media, index, total, username)
                    + "\n\n⚠️ <b>تعذر تنزيل الوسائط</b> — بقيت بيانات المنشور في DB."
                )
                return False

            first_path = files[0][0]
            media_type = files[0][1] if len(files) == 1 else "carousel"
            update_post_media(
                post_id,
                media_type,
                first_path,
                str(extract(media, "thumbnail_url", "display_url", default="") or ""),
            )

            code = str(extract(media, "code", "shortcode", default="") or "")
            keyboard = TelegramSender._post_keyboard(
                f"https://www.instagram.com/p/{code}/",
                f"https://www.instagram.com/{html_escape(username)}/",
            )
            caption = self._post_caption_html(media, index, total, username)

            sent_any = False
            for file_index, (path, kind) in enumerate(files, 1):
                media_caption = caption if file_index == 1 else (
                    f"<b>📌 جزء {file_index}/{len(files)}</b>\n"
                    f"<a href='https://www.instagram.com/p/{code}/'>فتح المنشور</a>"
                )
                ok = (
                    TelegramSender.send_video(
                        path, media_caption,
                        reply_markup=keyboard if file_index == 1 else None
                    )
                    if kind == "video"
                    else TelegramSender.send_photo(
                        path, media_caption,
                        reply_markup=keyboard if file_index == 1 else None
                    )
                )
                sent_any = sent_any or ok
                if not ok:
                    print(f"      ⚠️ Telegram لم يرسل {os.path.basename(path)}")
                InstagramClient.human_delay(0.3, 0.8)

            if fetch_comments and not InstagramClient.is_throttled():
                comments = self._fetch_comments(media, max_comments)
                for comment in comments:
                    save_comment(post_id, comment)

            return sent_any

        except Exception as exc:
            if is_rate_limit_error(exc):
                InstagramClient.mark_throttle(exc)
                print("🛑 توقف آمن بسبب 429.")
                return False
            print(f"⚠️ process_media: {redact_error(exc)}")
            return False

    def _scrape_followers(self, username, account_id, max_followers):
        followers = self._fetch_followers(username, max_followers)
        for user in followers:
            save_follower(account_id, user)
        if followers:
            TelegramSender.send_follower_list(username, followers, "👥 Followers")
        else:
            TelegramSender.send_message_html(
                f"<b>ℹ️ Followers</b>\n@{html_escape(username)}\nغير متاحة في الوضع الحالي."
            )
        return len(followers)

    def _scrape_following(self, username, account_id, max_following):
        following = self._fetch_following(username, max_following)
        for user in following:
            save_following(account_id, user)
        if following:
            lines = []
            for idx, user in enumerate(following, 1):
                uname = html_escape(str(extract(user, "username", default="") or ""))
                fullname = html_escape(str(extract(user, "full_name", "fullname", default="—") or "—"))
                lines.append(f"<b>{idx:03d}.</b> @{uname} — {fullname}")
            for start in range(0, len(lines), 20):
                TelegramSender.send_message_html(
                    f"<b>➡️ Following — @{html_escape(username)}</b>\n"
                    f"📦 {start + 1}–{min(start + 20, len(lines))} من {len(lines)}\n\n"
                    + "\n".join(lines[start:start + 20])
                )
        else:
            TelegramSender.send_message_html(
                f"<b>ℹ️ Following</b>\n@{html_escape(username)}\nغير متاحة في الوضع الحالي."
            )
        return len(following)

    def _scrape_stories(self, username, account_id, max_stories):
        stories = self._fetch_stories(username, max_stories)
        folder = os.path.join(STORIES_PATH, username)
        ok = 0
        for idx, story in enumerate(stories, 1):
            path = self._download_story(story, folder, idx)
            if not path:
                continue
            ok += 1
            save_story(
                account_id,
                story,
                path,
                str(extract(story, "video_url", "url", "display_url", default="") or ""),
                str(extract(story, "thumbnail_url", "display_url", default="") or ""),
            )
            caption = (
                f"<b>📖 Story #{idx:02d}/{len(stories):02d}</b>\n"
                f"👤 @{html_escape(username)}\n"
                f"🔐 {InstagramClient._mode_label()}"
            )
            if path.lower().endswith((".mp4", ".mov", ".webm")):
                TelegramSender.send_video(path, caption)
            else:
                TelegramSender.send_photo(path, caption)
        print(f"📖 Stories: {ok}/{len(stories)}")
        return ok

    def _scrape_highlights(self, username, account_id, max_highlights):
        highlights = self._fetch_highlights(username, max_highlights)
        if not highlights:
            TelegramSender.send_message_html(
                f"<b>ℹ️ Highlights</b>\n@{html_escape(username)}\nلم تُرجع المنصة Highlights."
            )
            return 0

        folder = os.path.join(HIGHLIGHTS_PATH, username)
        total_items = 0

        for idx, highlight in enumerate(highlights, 1):
            title = str(extract(
                highlight, "title", "name", default=f"Highlight {idx}"
            ) or f"Highlight {idx}")
            cover_path = ""
            cover_url = str(extract(
                highlight, "cover_url", "thumbnail_url", default=""
            ) or "")
            if cover_url:
                cover_path = os.path.join(
                    folder,
                    f"cover_{idx:02d}.jpg"
                )
                if not self._download_url(cover_url, cover_path):
                    cover_path = ""

            highlight_id = save_highlight_record(account_id, highlight, cover_path)
            items = self._fetch_highlight_items(highlight)
            if highlight_id is None:
                continue

            TelegramSender.send_message_html(
                f"<b>📌 Highlight #{idx}</b> — {html_escape(title)}\n"
                f"👤 @{html_escape(username)}\n"
                f"📦 العناصر المعروفة: {len(items)}"
            )

            for item_index, item in enumerate(items, 1):
                path = self._download_highlight_item(item, folder, item_index)
                if not path:
                    continue
                total_items += 1
                save_highlight_item(highlight_id, item, path)
                caption = f"<b>📌 {html_escape(title)}</b> — {item_index}"
                if path.lower().endswith((".mp4", ".mov", ".webm")):
                    TelegramSender.send_video(path, caption)
                else:
                    TelegramSender.send_photo(path, caption)

        print(f"📌 Highlights downloaded items: {total_items}")
        return total_items

    def scrape(self, username, max_posts=10, scrape_mode="smart_merge",
               order="desc", fetch_comments=False, max_comments=20,
               fetch_followers=False, max_followers=100,
               fetch_stories=False, max_stories=50,
               fetch_highlights=False, max_highlights=20,
               fetch_following=False, max_following=100,
               fetch_reels=False, max_reels=20):
        username = self._profile_username(username)
        if not username:
            raise ValueError("اسم المستخدم مطلوب.")
        if not InstagramClient.refresh_if_needed():
            raise RuntimeError(
                f"عميل Instagram غير جاهز؛ status={InstagramClient._status}"
            )

        print("=" * 70)
        print(f"🚀 SCRAPE @{username} | mode={InstagramClient._mode_label()}")
        print("=" * 70)

        set_job_state("running", username, "scrape", "بدء السكراب")

        profile = self.get_profile_info(username)
        if not profile:
            raise RuntimeError(f"تعذر الحصول على بروفايل @{username}")

        account_id = get_account_id(username)
        if not account_id:
            raise RuntimeError("لم يتم إنشاء سجل الحساب في DB.")

        folder = os.path.join(MEDIA_PATH, username)
        os.makedirs(folder, exist_ok=True)

        max_posts = safe_int(max_posts, 10, 1, 500)
        all_posts = self._fetch_posts(username, max_posts * 2)
        if InstagramClient.is_throttled():
            TelegramSender.send_message_html(
                f"<b>🛑 توقف آمن</b>\n429 من Instagram أثناء جلب منشورات @{html_escape(username)}"
            )
            return

        key_sort = lambda x: str(extract(x, "taken_at", default="") or "")
        all_posts = sorted(all_posts, key=key_sort, reverse=(order != "asc"))
        targets = dedupe_items(all_posts)[:max_posts]

        print(f"📦 all_posts={len(all_posts)} | unique targets={len(targets)}")

        if not targets:
            TelegramSender.send_message_html(
                f"<b>ℹ️ لم تُرجع public.get_posts أي منشورات</b>\n"
                f"👤 @{html_escape(username)}\n"
                f"🔐 {InstagramClient._mode_label()}"
            )
        else:
            TelegramSender.send_rich_message(
                f"<b>📦 Instagram OSINT</b>\n"
                f"<b>👤 @{html_escape(username)}</b>\n"
                f"📌 <b>{len(targets)}</b> منشور للتنزيل\n"
                f"📅 {'الأحدث أولاً' if order == 'desc' else 'الأقدم أولاً'}",
                TelegramSender._post_keyboard(
                    f"https://www.instagram.com/{html_escape(username)}/"
                ),
            )

        successful = 0
        for idx, media in enumerate(targets, 1):
            if InstagramClient.is_throttled():
                print(f"🛑 توقف: 429 بعد {successful}/{len(targets)}")
                break
            code = str(extract(media, "code", "shortcode", "pk", default="?") or "?")
            print(f"📄 [{idx}/{len(targets)}] {code}")
            if self._process_media(
                media, username, folder, idx, len(targets),
                fetch_comments=fetch_comments, max_comments=max_comments
            ):
                successful += 1
            InstagramClient.human_delay()

        # Optional independent collectors.
        reels_count = 0
        reels_downloaded = 0
        if fetch_reels and not InstagramClient.is_throttled():
            reels = self._fetch_reels(username, max_reels)
            unique_reels = dedupe_items(reels)
            target_codes = {
                str(extract(m, "code", "shortcode", "pk", "id", default="") or "")
                for m in targets
            }
            reels_to_process = []
            for reel in unique_reels:
                key = str(extract(reel, "code", "shortcode", "pk", "id", default="") or "")
                if key and key in target_codes:
                    continue
                reels_to_process.append(reel)
            reels_to_process = reels_to_process[:safe_int(max_reels, 20, 1, 100)]
            reels_count = len(reels_to_process)
            print(f"🎬 Reels جديدة للمعالجة: {reels_count}")
            for idx, reel in enumerate(reels_to_process, 1):
                if InstagramClient.is_throttled():
                    break
                print(f"🎬 Reel [{idx}/{len(reels_to_process)}] "
                      f"{extract(reel, 'code', 'shortcode', 'pk', default='?')}")
                if self._process_media(
                    reel, username, folder, idx, len(reels_to_process),
                    fetch_comments=fetch_comments, max_comments=max_comments
                ):
                    reels_downloaded += 1

        followers_count = 0
        following_count = 0
        stories_count = 0
        highlight_items_count = 0

        if fetch_followers and not InstagramClient.is_throttled():
            followers_count = self._scrape_followers(
                username, account_id, safe_int(max_followers, 100, 1, 5000)
            )

        if fetch_following and not InstagramClient.is_throttled():
            following_count = self._scrape_following(
                username, account_id, safe_int(max_following, 100, 1, 5000)
            )

        if fetch_stories and not InstagramClient.is_throttled():
            stories_count = self._scrape_stories(
                username, account_id, safe_int(max_stories, 50, 1, 200)
            )

        if fetch_highlights and not InstagramClient.is_throttled():
            highlight_items_count = self._scrape_highlights(
                username, account_id, safe_int(max_highlights, 20, 1, 100)
            )

        csv_path = self._csv_report(username)
        html_path = self._html_report(username, order)

        if csv_path:
            TelegramSender.send_document(csv_path, f"📊 CSV @{username}")
        if html_path:
            TelegramSender.send_document(html_path, f"📄 HTML @{username}")

        summary = (
            f"<b>✅ انتهت المهمة</b>\n"
            f"👤 @{html_escape(username)}\n"
            f"📌 المنشورات: <b>{len(targets)}</b>\n"
            f"✅ نُفّذ بنجاح: <b>{successful}</b>\n"
            f"🎬 Reels: <b>{reels_count}</b> — نُزّل <b>{reels_downloaded}</b>\n"
            f"👥 Followers: <b>{followers_count}</b>\n"
            f"➡️ Following: <b>{following_count}</b>\n"
            f"📖 Stories: <b>{stories_count}</b>\n"
            f"📌 Highlight items: <b>{highlight_items_count}</b>\n"
            f"🔐 {InstagramClient._mode_label()}"
        )
        TelegramSender.send_rich_message(summary)
        set_job_state("done", username, "scrape", "انتهت المهمة")
        print("=" * 70)

    def _csv_report(self, username):
        try:
            aid = get_account_id(username)
            if not aid:
                return None
            conn = db_connect()
            conn.row_factory = sqlite3.Row
            posts = conn.execute(
                "SELECT * FROM posts WHERE account_id=? ORDER BY taken_at DESC",
                (aid,),
            ).fetchall()
            followers = conn.execute(
                "SELECT username, full_name FROM followers WHERE account_id=?",
                (aid,),
            ).fetchall()
            following = conn.execute(
                "SELECT username, full_name FROM following WHERE account_id=?",
                (aid,),
            ).fetchall()
            stories = conn.execute(
                "SELECT * FROM stories WHERE account_id=? ORDER BY taken_at DESC",
                (aid,),
            ).fetchall()
            conn.close()

            path = os.path.join(
                REPORTS_PATH,
                f"{safe_name(username)}_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            )
            with open(path, "w", newline="", encoding="utf-8-sig") as fh:
                writer = csv.writer(fh)
                writer.writerow([
                    "type", "code_or_pk", "url", "media_type",
                    "likes", "comments", "views", "plays", "taken_at",
                    "username", "full_name", "caption", "file_path"
                ])
                for row in posts:
                    writer.writerow([
                        "post", row["post_code"], row["post_url"], row["media_type"],
                        row["like_count"], row["comment_count"], row["view_count"],
                        row["play_count"], row["taken_at"], username, "",
                        (row["caption"] or "")[:1000], row["file_path"] or ""
                    ])
                for row in followers:
                    writer.writerow([
                        "follower", "", "", "", "", "", "", "", "",
                        row["username"], row["full_name"], "", ""
                    ])
                for row in following:
                    writer.writerow([
                        "following", "", "", "", "", "", "", "", "",
                        row["username"], row["full_name"], "", ""
                    ])
                for row in stories:
                    writer.writerow([
                        "story", row["story_pk"], row["story_url"], row["media_type"],
                        "", "", "", "", row["taken_at"],
                        username, "", "", row["file_path"] or ""
                    ])
            print(f"📄 CSV: {path}")
            return path
        except Exception as exc:
            print(f"⚠️ CSV report: {redact_error(exc)}")
            return None

    def _html_report(self, username, order="desc"):
        try:
            aid = get_account_id(username)
            if not aid:
                return None
            account = get_account_info(username) or {}
            posts = get_posts_with_comments(aid, order=order)
            followers = get_followers(aid)
            following = get_following(aid)
            stories = get_stories(aid)
            highlights = get_highlights(aid)

            profile_pic = ""
            candidate = os.path.join(MEDIA_PATH, username, "profile_pic.jpg")
            if os.path.exists(candidate):
                profile_pic = ThumbnailEngine.from_file(candidate, 150, 50) or ""

            cards = []
            for post in posts:
                thumb = ThumbnailEngine.from_file(
                    post.get("file_path", ""), THUMB_MAX_SIZE, THUMB_QUALITY
                ) if post.get("file_path") else None
                if not thumb:
                    thumb = ThumbnailEngine.from_url(
                        post.get("thumbnail_url", ""), THUMB_MAX_SIZE,
                        THUMB_QUALITY, f"report_{post.get('post_code','')}"
                    )
                if not thumb:
                    thumb = ThumbnailEngine.placeholder_svg("📷")

                comment_html = ""
                if post.get("comments"):
                    rows = []
                    for c in post["comments"][:30]:
                        rows.append(
                            f"<li>@{html_escape(str(c.get('commenter_username','') or ''))}: "
                            f"{html_escape(str(c.get('comment_text','') or '')[:300])}</li>"
                        )
                    comment_html = "<details><summary>💬 التعليقات</summary><ul>" + "".join(rows) + "</ul></details>"

                cards.append(
                    f"<article class='post'>"
                    f"<img src='data:image/jpeg;base64,{thumb}'>"
                    f"<div><b>{html_escape(str(post.get('media_type','') or ''))}</b> "
                    f"❤️ {int(post.get('like_count',0) or 0):,} "
                    f"💬 {int(post.get('comment_count',0) or 0):,} "
                    f"👁️ {int(post.get('view_count',0) or 0):,}</div>"
                    f"<p>{html_escape(str(post.get('caption','') or '')[:800])}</p>"
                    f"<a target='_blank' href='{html_escape(str(post.get('post_url','')))}'>🔗 فتح المنشور</a>"
                    f"{comment_html}</article>"
                )

            follower_html = "".join(
                f"<span>@{html_escape(str(x.get('username','') or ''))}</span>"
                for x in followers
            ) or "<em>لا توجد بيانات</em>"

            following_html = "".join(
                f"<span>@{html_escape(str(x.get('username','') or ''))}</span>"
                for x in following
            ) or "<em>لا توجد بيانات</em>"

            story_html = "".join(
                f"<div><span>📖 {html_escape(str(x.get('story_pk','')))}</span>"
                f"<small>{html_escape(str(x.get('taken_at','')))}</small></div>"
                for x in stories
            ) or "<em>لا توجد Stories محفوظة</em>"

            highlight_html = ""
            for h in highlights:
                title = html_escape(str(h.get("title", "") or ""))
                highlight_html += (
                    f"<div class='highlight'><b>📌 {title}</b> "
                    f"<span>({len(h.get('items', []))} عناصر محفوظة / "
                    f"{int(h.get('media_count',0) or 0)} إجمالاً)</span></div>"
                )
            highlight_html = highlight_html or "<em>لا توجد Highlights محفوظة</em>"

            pic_html = (
                f"<img class='profile' src='data:image/jpeg;base64,{profile_pic}'>"
                if profile_pic else "<div class='profile placeholder'>👤</div>"
            )

            css = """
*{box-sizing:border-box}
body{font-family:Arial,sans-serif;background:linear-gradient(135deg,#1a1a2e,#0f3460);
color:#e9edf2;padding:16px;line-height:1.6}
.container{max-width:1150px;margin:auto}
.header,.section,.post{background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);
border-radius:16px;padding:18px;margin-bottom:16px}
.header{text-align:center;background:linear-gradient(135deg,#e94560,#c73659)}
.profile{width:140px;height:140px;border-radius:50%;object-fit:cover}
.placeholder{display:inline-flex;align-items:center;justify-content:center;background:#24263b}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;margin-top:12px}
.stats div{background:rgba(0,0,0,.22);padding:9px;border-radius:9px;text-align:center}
.post{display:flex;gap:14px}
.post>img{width:150px;height:150px;object-fit:cover;border-radius:10px;flex:none}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px}
.grid span{background:rgba(0,0,0,.22);padding:8px;border-radius:8px;overflow-wrap:anywhere}
a{color:#7cc7ff}
@media(max-width:600px){.post{flex-direction:column}.post>img{width:100%;height:auto}}
"""
            safe_user = html_escape(username)
            html = (
                "<!doctype html><html lang='ar' dir='rtl'><head><meta charset='utf-8'>"
                f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
                f"<title>OSINT — @{safe_user}</title><style>{css}</style></head>"
                "<body><div class='container'>"
                f"<header class='header'><h1>🕵️ Instagram OSINT</h1>"
                f"{pic_html}<h2>@{safe_user}</h2>"
                f"<div>{html_escape(str(account.get('full_name','') or ''))}</div>"
                "<div class='stats'>"
                f"<div>📸 {int(account.get('posts_count',0) or 0):,}</div>"
                f"<div>👥 {int(account.get('followers',0) or 0):,}</div>"
                f"<div>➡️ {int(account.get('following',0) or 0):,}</div>"
                f"<div>{'🔒' if account.get('is_private') else '🌐'}</div>"
                "</div>"
                f"<p>{html_escape(str(account.get('bio','') or '')[:1000])}</p></header>"
                f"<section class='section'><h2>📸 المنشورات ({len(posts)})</h2>"
                f"{''.join(cards) or '<em>لا توجد منشورات محفوظة</em>'}</section>"
                f"<section class='section'><h2>👥 Followers ({len(followers)})</h2>"
                f"<div class='grid'>{follower_html}</div></section>"
                f"<section class='section'><h2>➡️ Following ({len(following)})</h2>"
                f"<div class='grid'>{following_html}</div></section>"
                f"<section class='section'><h2>📖 Stories ({len(stories)})</h2>"
                f"{story_html}</section>"
                f"<section class='section'><h2>📌 Highlights ({len(highlights)})</h2>"
                f"{highlight_html}</section>"
                "<footer style='opacity:.65;text-align:center'>"
                f"Generated {html_escape(now_iso())} · instaharvest-v2 {html_escape(INSTAHARVEST_VERSION)}"
                "</footer></div></body></html>"
            )
            path = os.path.join(
                REPORTS_PATH,
                f"{safe_name(username)}_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
            )
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(html)
            print(f"📄 HTML report: {path}")
            return path
        except Exception as exc:
            print(f"⚠️ HTML report: {redact_error(exc)}")
            traceback.print_exc()
            return None


# ============================================================
# 12. Flask control panel
# ============================================================
app = Flask(__name__)


@app.before_request
def require_control_token():
    public_paths = {"/", "/health", "/favicon.ico"}
    if request.path in public_paths:
        return None
    supplied = str(request.headers.get("X-Control-Token", "") or "").strip()
    if not secrets.compare_digest(supplied, CONTROL_TOKEN):
        return jsonify({
            "ok": False,
            "error": "مفتاح التحكم مفقود أو غير صحيح."
        }), 401
    return None


def api_status(message=None):
    status = InstagramClient.get_status()
    if message:
        status["message"] = message
    status["job"] = get_job_state()
    return status


HTML_PAGE = r"""<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Instagram OSINT ULTRA v4</title>
<style>
*{box-sizing:border-box}
body{margin:0;padding:18px;font-family:system-ui,-apple-system,Segoe UI,Arial,sans-serif;
background:linear-gradient(135deg,#101523,#0c2745);color:#111}
.card{max-width:760px;margin:auto;background:#fff;border-radius:20px;padding:22px;
box-shadow:0 20px 70px rgba(0,0,0,.35)}
h1{text-align:center;margin:0 0 6px;color:#0f3460}
.sub{text-align:center;color:#667085;margin-bottom:18px}
.section{border:1px solid #e5e7eb;border-radius:14px;padding:14px;margin:12px 0}
.title{font-weight:800;color:#0f3460;margin-bottom:10px}
.row{display:flex;gap:9px;align-items:center}
.row>*{flex:1}
input[type=text],input[type=number],textarea,button{
width:100%;padding:11px;border-radius:10px;font:inherit}
input[type=text],input[type=number],textarea{border:2px solid #e5e7eb}
textarea{min-height:90px;resize:vertical}
button{border:0;background:#e94560;color:#fff;font-weight:800;cursor:pointer}
button.secondary{background:#0f9d76}
button.gray{background:#64748b}
button:disabled{opacity:.55;cursor:not-allowed}
.opt{display:flex;gap:9px;align-items:center;padding:9px;background:#f8fafc;border-radius:9px;margin:5px 0}
.opt input{width:18px;height:18px}
.mode-grid{display:grid;grid-template-columns:1fr 1fr;gap:9px}
.mode-box{padding:12px;border:2px solid #e5e7eb;border-radius:12px;text-align:center;cursor:pointer}
.mode-box.active{border-color:#e94560;background:#fff4f6}
.mode-box input{display:none}
.badge{padding:10px;border-radius:10px;background:#eef2ff;margin-top:10px;text-align:center}
.badge.ok{background:#dcfce7;color:#166534}
.badge.err{background:#fee2e2;color:#991b1b}
.badge.wait{background:#fef3c7;color:#92400e}
.small{font-size:.85rem;color:#667085}
.notice{padding:10px;background:#fff7ed;border:1px solid #fed7aa;border-radius:10px}
.tabs{display:flex;gap:8px;margin-bottom:10px}
.tab{background:#f1f5f9;color:#475569}
.tab.active{background:#0f3460;color:#fff}
.panel{display:none}.panel.active{display:block}
@media(max-width:560px){.row,.mode-grid{grid-template-columns:1fr;display:block}.row>*{margin-bottom:7px}}
</style>
</head>
<body>
<div class="card">
<h1>🕵️ Instagram OSINT Scraper ULTRA v4</h1>
<div class="sub">Anonymous ثابت + Login اختياري للميزات الخاصة</div>

<div class="section">
<div class="title">🛡️ حماية لوحة التحكم</div>
<div class="small">أدخل مفتاح التحكم الذي طبعه Colab في الخلية عند التشغيل. يُحفظ محليًا في هذا المتصفح فقط.</div>
<div class="row" style="margin-top:7px">
<input type="password" id="controlToken" autocomplete="off" placeholder="مفتاح التحكم">
<button id="saveControlToken" class="secondary" type="button">حفظ المفتاح</button>
</div>
<div id="tokenBox" class="badge wait">لم يتم حفظ مفتاح التحكم بعد</div>
</div>

<div class="section">
<div class="title">🔐 وضع التشغيل</div>
<div class="mode-grid">
<label class="mode-box" id="anonBox">
<input type="radio" name="mode" value="anonymous">
<div>🔓 Anonymous</div><div class="small">المنشورات/الملف العام</div>
</label>
<label class="mode-box" id="loginBox">
<input type="radio" name="mode" value="login">
<div>🔐 Login</div><div class="small">Stories / Followers / Following</div>
</label>
</div>
<div class="row" style="margin-top:9px">
<button id="applyMode">تفعيل الوضع المحدد</button>
<button id="refreshStatus" class="gray">تحديث الحالة</button>
</div>
<div id="statusBox" class="badge">اضغط «تحديث الحالة» لمعرفة الحالة الحالية</div>

<div id="authPanel" class="section" style="display:none">
<div class="title">🔢 رمز التحقق من Instagram</div>
<div id="authMessage" class="small" style="margin-bottom:8px"></div>
<div class="row">
<input type="text" id="verificationCode" inputmode="numeric" autocomplete="one-time-code"
       placeholder="أدخل رمز البريد/الهاتف" disabled>
<button id="submitCode" class="secondary" disabled>إرسال الرمز</button>
</div>
<div class="row" style="margin-top:7px">
<button id="cancelLogin" class="gray" type="button">إلغاء Login</button>
</div>
<div class="small" style="margin-top:7px">
الرمز لا يُحفظ في Google Drive أو GitHub؛ يبقى في الذاكرة فقط حتى يستلمه Login.
بعد ضغط «تفعيل Login»، استخدم «تحديث الحالة» يدويًا مرة واحدة عندما يصل الرمز؛
عند ظهور حالة «بانتظار الرمز» ستصبح الخانة مفعلة.
</div>
</div>
</div>

<div class="tabs">
<button class="tab active" onclick="showTab('scrape',this)">📊 سكراب</button>
<button class="tab" onclick="showTab('search',this)">🔍 بحث</button>
</div>

<div id="scrape" class="panel active">
<form id="scrapeForm">
<div class="section">
<div class="title">📌 الحساب</div>
<input type="text" name="username" placeholder="اسم المستخدم أو @username" required>
<div class="row" style="margin-top:7px">
<input type="number" name="max_posts" value="10" min="1" max="500">
<select name="order" style="padding:11px;border:2px solid #e5e7eb;border-radius:10px;font:inherit">
<option value="desc">الأحدث أولاً</option>
<option value="asc">الأقدم أولاً</option>
</select>
</div>
</div>

<div class="section">
<div class="title">⚙️ الطبقات</div>
<div class="opt"><input type="checkbox" name="fetch_reels" value="1">🎬 Reels</div>
<div class="opt"><input type="checkbox" name="fetch_comments" value="1">💬 Comments</div>
<div class="opt"><input type="checkbox" name="fetch_stories" value="1">📖 Stories <span class="small">(Login)</span></div>
<div class="opt"><input type="checkbox" name="fetch_highlights" value="1">📌 Highlights</div>
<div class="opt"><input type="checkbox" name="fetch_followers" value="1">👥 Followers <span class="small">(Login)</span></div>
<div class="opt"><input type="checkbox" name="fetch_following" value="1">➡️ Following <span class="small">(Login)</span></div>
</div>

<div class="section">
<div class="title">🔢 الحدود</div>
<div class="row">
<input type="number" name="max_comments" value="20" min="1" max="200" placeholder="Comments">
<input type="number" name="max_stories" value="50" min="1" max="200" placeholder="Stories">
<input type="number" name="max_followers" value="100" min="1" max="5000" placeholder="Followers">
</div>
<div class="row">
<input type="number" name="max_following" value="100" min="1" max="5000" placeholder="Following">
<input type="number" name="max_highlights" value="20" min="1" max="100" placeholder="Highlights">
<input type="number" name="max_reels" value="20" min="1" max="100" placeholder="Reels">
</div>
</div>

<button type="submit" id="scrapeBtn">🚀 بدء السكراب</button>
</form>
</div>

<div id="search" class="panel">
<form id="searchForm">
<div class="section">
<div class="title">🔍 البحث</div>
<textarea name="query" placeholder="@username أو #hashtag أو رابط Instagram أو كلمة بحث" required></textarea>
</div>
<div class="opt"><input type="checkbox" name="hunt_mentions" value="1">🔎 Mention Hunter (طلبات إضافية)</div>
<div class="opt"><input type="checkbox" name="generate_report" value="1" checked>📄 تقرير HTML</div>
<button type="submit" id="searchBtn" class="secondary">🔍 تنفيذ البحث</button>
</form>
</div>

<div class="notice small" style="margin-top:14px">
⚠️ لا يوجد polling مستمر للحالة. زر تحديث الحالة يرسل طلبًا واحدًا فقط،
وتبديل الوضع يُنفّذ بطلب واحد ثم تتوقف الطلبات.
</div>
</div>

<script>
function showTab(id, button){
  document.querySelectorAll('.panel').forEach(x=>x.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  if(button) button.classList.add('active');
}

function selectedMode(){
  const r=document.querySelector('input[name="mode"]:checked');
  return r ? r.value : 'anonymous';
}

function paintMode(mode){
  document.querySelectorAll('.mode-box').forEach(x=>x.classList.remove('active'));
  document.getElementById(mode==='login'?'loginBox':'anonBox').classList.add('active');
}

function renderStatus(data){
  const box=document.getElementById('statusBox');
  box.className='badge '+(data.status==='ready'?'ok':(data.status==='error'?'err':'wait'));
  const auth=data.auth||{};
  let msg=data.message || (data.status||'');
  if(auth.message) msg += ' · '+auth.message;
  if(auth.contact_point) msg += ' · '+auth.contact_point;
  if(data.session_loaded || data.session_saved) msg += ' · جلسة Drive موجودة';
  if(data.throttle_remaining>0) msg += ' · تهدئة '+data.throttle_remaining+'s';
  if(data.job && data.job.state==='running'){
    msg += ' · مهمة: '+(data.job.username||'');
  }
  box.textContent=msg;

  const visualMode =
    (['starting','loading_session','authenticating','waiting_code','verifying'].includes(auth.state)
      ? 'login' : (data.mode||'anonymous'));
  document.querySelectorAll('.mode-box').forEach(x=>x.classList.remove('active'));
  const selected=document.querySelector('input[name="mode"][value="'+visualMode+'"]');
  if(selected){ selected.checked=true; paintMode(visualMode); }

  const panel=document.getElementById('authPanel');
  const authMessage=document.getElementById('authMessage');
  const loginInProgress =
    ['starting','loading_session','authenticating','waiting_code','verifying'].includes(auth.state);
  const waiting=auth.state==='waiting_code';
  if(panel){
    panel.style.display=loginInProgress ? 'block' : 'none';
  }
  if(authMessage){
    if(waiting){
      authMessage.textContent =
        (auth.message||'أدخل رمز التحقق المرسل من Instagram ثم اضغط «إرسال الرمز».') +
        (auth.contact_point ? ' · جهة التحقق: '+auth.contact_point : '');
    } else if(loginInProgress){
      authMessage.textContent =
        (auth.message||'⏳ Login قيد التنفيذ.') +
        ' · عندما يصل الرمز اضغط «تحديث الحالة» مرة واحدة، ثم ستصبح خانة الرمز قابلة للكتابة.';
    } else {
      authMessage.textContent=auth.message||'';
    }
  }
  const submit=document.getElementById('submitCode');
  const codeInput=document.getElementById('verificationCode');
  if(submit) submit.disabled=!waiting;
  if(codeInput){
    codeInput.disabled=!waiting;
    codeInput.setAttribute('aria-disabled',String(!waiting));
  }
  if(waiting && codeInput){
    try{
      codeInput.focus();
    }catch(e){}
  }
}

function controlToken(){
  return (document.getElementById('controlToken')?.value ||
          localStorage.getItem('ig_osint_control_token') || '').trim();
}

function apiHeaders(){
  const token=controlToken();
  return token ? {'X-Control-Token': token} : {};
}

async function refreshStatus(){
  try{
    const r=await fetch('/status?once='+Date.now(),{
      cache:'no-store',
      headers:apiHeaders()
    });
    const data=await r.json();
    if(r.status===401){
      const box=document.getElementById('tokenBox');
      if(box){box.className='badge err';box.textContent=data.error||'مفتاح التحكم غير صحيح';}
      renderStatus({status:'error',message:'أدخل مفتاح التحكم الصحيح ثم حدّث الحالة.'});
      return;
    }
    renderStatus(data);
  }catch(e){
    renderStatus({status:'error',message:'تعذر الاتصال بالسيرفر'});
  }
}



const tokenInput=document.getElementById('controlToken');
const tokenBox=document.getElementById('tokenBox');
if(tokenInput){
  const saved=localStorage.getItem('ig_osint_control_token')||'';
  tokenInput.value=saved;
  if(saved && tokenBox){
    tokenBox.className='badge ok';
    tokenBox.textContent='✅ مفتاح محفوظ محليًا';
  }
}
document.getElementById('saveControlToken').addEventListener('click',()=>{
  const token=tokenInput.value.trim();
  if(!token){
    tokenBox.className='badge err';
    tokenBox.textContent='أدخل مفتاح التحكم أولاً.';
    return;
  }
  localStorage.setItem('ig_osint_control_token',token);
  tokenBox.className='badge ok';
  tokenBox.textContent='✅ تم حفظ المفتاح محليًا في هذا المتصفح.';
});

document.querySelectorAll('input[name="mode"]').forEach(r=>{
  r.addEventListener('change',()=>paintMode(selectedMode()));
});

document.getElementById('applyMode').addEventListener('click',async()=>{
  const btn=document.getElementById('applyMode');
  btn.disabled=true;
  btn.textContent='⏳ جارٍ التبديل…';
  try{
    const r=await fetch('/mode',{
      method:'POST',
      headers:{'Content-Type':'application/json',...apiHeaders()},
      body:JSON.stringify({mode:selectedMode()})
    });
    const data=await r.json();
    renderStatus(data);
    if(!r.ok && data.error) alert(data.error);
  }catch(e){
    alert('فشل طلب تبديل الوضع');
  }finally{
    btn.disabled=false;
    btn.textContent='تفعيل الوضع المحدد';
  }
});

document.getElementById('refreshStatus').addEventListener('click',refreshStatus);

document.getElementById('submitCode').addEventListener('click',async()=>{
  const input=document.getElementById('verificationCode');
  const btn=document.getElementById('submitCode');
  const code=(input.value||'').trim();
  if(!code) return;
  btn.disabled=true;
  try{
    const body=new URLSearchParams({code});
    const r=await fetch('/auth/code',{method:'POST',headers:apiHeaders(),body});
    const data=await r.json();
    renderStatus(data);
    if(!r.ok) alert(data.error || data.message || 'لم يتم قبول الرمز');
    else input.value='';
  }catch(err){
    alert('تعذر إرسال رمز التحقق');
  }finally{
    btn.disabled=false;
  }
});

document.getElementById('cancelLogin').addEventListener('click',async()=>{
  try{
    const r=await fetch('/auth/cancel',{method:'POST',headers:apiHeaders()});
    const data=await r.json();
    renderStatus(data);
    if(!r.ok) alert(data.error || data.message || 'تعذر إلغاء Login');
  }catch(err){
    alert('تعذر إلغاء Login');
  }
});

document.getElementById('scrapeForm').addEventListener('submit',async(e)=>{
  e.preventDefault();
  const btn=document.getElementById('scrapeBtn');
  btn.disabled=true;
  btn.textContent='⏳ تم إرسال المهمة…';
  try{
    const body=new URLSearchParams(new FormData(e.target));
    const r=await fetch('/start',{method:'POST',headers:apiHeaders(),body});
    const data=await r.json();
    alert(data.message || data.error || 'تم الإرسال');
  }catch(err){alert('تعذر إرسال المهمة');}
  finally{btn.disabled=false;btn.textContent='🚀 بدء السكراب';}
});

document.getElementById('searchForm').addEventListener('submit',async(e)=>{
  e.preventDefault();
  const btn=document.getElementById('searchBtn');
  btn.disabled=true;
  btn.textContent='⏳ تم إرسال البحث…';
  try{
    const body=new URLSearchParams(new FormData(e.target));
    const r=await fetch('/search',{method:'POST',headers:apiHeaders(),body});
    const data=await r.json();
    alert(data.message || data.error || 'تم إرسال البحث');
  }catch(err){alert('تعذر إرسال البحث');}
  finally{btn.disabled=false;btn.textContent='🔍 تنفيذ البحث';}
});

// لا يوجد أي طلب تلقائي عند فتح الصفحة؛ كل تحديث للحالة يدوي.
// هذا يمنع مئات طلبات /status المتكررة داخل خلية Colab.
</script>
</body>
</html>"""


@app.route("/")
def index():
    return render_template_string(HTML_PAGE)


@app.route("/health")
def health():
    return jsonify({
        "ok": True,
        "version": "4.0",
        "instaharvest": INSTAHARVEST_VERSION,
        "mode": InstagramClient._mode,
    })


@app.route("/status")
def status():
    return jsonify(api_status())


@app.route("/mode", methods=["POST"])
def mode_switch():
    body = request.get_json(silent=True) or request.form
    requested = str(body.get("mode", "")).strip().lower()
    if requested not in ("anonymous", "login"):
        return jsonify({"error": "mode must be anonymous or login"}), 400

    try:
        status_data = InstagramClient.switch_mode(requested)
        pending_login = (
            requested == "login"
            and status_data.get("auth", {}).get("state") in {
                "starting", "loading_session", "authenticating",
                "waiting_code", "verifying"
            }
        )
        return jsonify({
            **status_data,
            "ok": True,
            "pending": pending_login,
            "message": (
                "⏳ بدأ Login في الخلفية. اضغط «تحديث الحالة» عند طلب الرمز."
                if pending_login
                else f"✅ تم تفعيل {status_data['mode']}"
            ),
        })
    except Exception as exc:
        current = InstagramClient.get_status()
        return jsonify({
            **current,
            "ok": False,
            "error": redact_error(exc),
            "message": (
                f"❌ لم يتم تفعيل {requested}. "
                f"الوضع الحالي ما زال {current['mode']}."
            ),
        }), 409


@app.route("/auth/code", methods=["POST"])
def auth_code():
    body = request.get_json(silent=True) or request.form
    ok, message = submit_login_code(body.get("code", ""))
    if not ok:
        current = api_status(message)
        return jsonify({
            **current,
            "ok": False,
            "error": message,
        }), 409
    return jsonify({
        **api_status(message),
        "ok": True,
    })


@app.route("/auth/cancel", methods=["POST"])
def auth_cancel():
    ok, message = cancel_login()
    if not ok:
        current = api_status(message)
        return jsonify({
            **current,
            "ok": False,
            "error": message,
        }), 409
    return jsonify({
        **api_status(message),
        "ok": True,
    })


@app.route("/favicon.ico")
def favicon():
    return ("", 204)


@app.route("/start", methods=["POST"])
def start_scrape():
    username = str(request.form.get("username", "")).strip()
    if get_auth_state().get("state") in {
        "starting", "loading_session", "authenticating",
        "waiting_code", "verifying"
    }:
        return jsonify({
            "ok": False,
            "error": "Login ما زال قيد التهيئة. أكمل رمز التحقق أولاً ثم ابدأ السكراب.",
            "auth": get_auth_state(),
        }), 409
    if not username:
        return jsonify({"ok": False, "error": "اسم المستخدم مطلوب."}), 400

    options = {
        "max_posts": safe_int(request.form.get("max_posts"), 10, 1, 500),
        "order": request.form.get("order", "desc"),
        "fetch_comments": request.form.get("fetch_comments") == "1",
        "max_comments": safe_int(request.form.get("max_comments"), 20, 1, 200),
        "fetch_stories": request.form.get("fetch_stories") == "1",
        "max_stories": safe_int(request.form.get("max_stories"), 50, 1, 200),
        "fetch_highlights": request.form.get("fetch_highlights") == "1",
        "max_highlights": safe_int(request.form.get("max_highlights"), 20, 1, 100),
        "fetch_followers": request.form.get("fetch_followers") == "1",
        "max_followers": safe_int(request.form.get("max_followers"), 100, 1, 5000),
        "fetch_following": request.form.get("fetch_following") == "1",
        "max_following": safe_int(request.form.get("max_following"), 100, 1, 5000),
        "fetch_reels": request.form.get("fetch_reels") == "1",
        "max_reels": safe_int(request.form.get("max_reels"), 20, 1, 100),
    }

    if not INSTAGRAM_JOB_LOCK.acquire(blocking=False):
        return jsonify({
            "ok": False,
            "error": "هناك مهمة أخرى قيد التنفيذ.",
            "job": get_job_state(),
        }), 409

    def worker():
        set_job_state("starting", username, "scrape", "تهيئة السكراب")
        try:
            searcher = InstagramSearcher()
            searcher.scrape(username, **options)
        except Exception as exc:
            set_job_state("error", username, "scrape", redact_error(exc))
            print(f"❌ scrape worker: {redact_error(exc)}")
            traceback.print_exc()
            TelegramSender.send_message_html(
                f"<b>❌ فشل السكراب</b>\n{html_escape(redact_error(exc))}"
            )
        finally:
            INSTAGRAM_JOB_LOCK.release()

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({
        "ok": True,
        "message": f"🚀 بدأت مهمة @{html_escape(username)} — {options['max_posts']} منشور",
        "mode": InstagramClient._mode,
    })


@app.route("/search", methods=["POST"])
def start_search():
    query = str(request.form.get("query", "")).strip()
    if get_auth_state().get("state") in {
        "starting", "loading_session", "authenticating",
        "waiting_code", "verifying"
    }:
        return jsonify({
            "ok": False,
            "error": "Login ما زال قيد التهيئة. أكمل المصادقة أولاً.",
            "auth": get_auth_state(),
        }), 409
    if not query:
        return jsonify({"ok": False, "error": "الاستعلام مطلوب."}), 400

    max_per_type = safe_int(request.form.get("max_per_type"), 10, 1, 50)
    hunt_mentions = request.form.get("hunt_mentions") == "1"
    generate_report = request.form.get("generate_report") == "1"

    if not INSTAGRAM_JOB_LOCK.acquire(blocking=False):
        return jsonify({
            "ok": False,
            "error": "هناك مهمة أخرى قيد التنفيذ.",
        }), 409

    def worker():
        set_job_state("starting", query, "search", "تهيئة البحث")
        try:
            searcher = InstagramSearcher()
            results = searcher.universal_searcher.search(
                query, max_per_type, hunt_mentions=hunt_mentions
            )
            if generate_report:
                report = searcher.universal_searcher.generate_search_report(
                    query, results
                )
                if report:
                    TelegramSender.send_document(
                        report, f"🔍 تقرير البحث: {query}"
                    )
            TelegramSender.send_message_html(
                f"<b>✅ اكتمل البحث</b>\n"
                f"📝 {html_escape(query)}\n"
                f"📊 النتائج: {results['stats']['total']}"
            )
            set_job_state("done", query, "search", "اكتمل البحث")
        except Exception as exc:
            set_job_state("error", query, "search", redact_error(exc))
            print(f"❌ search worker: {redact_error(exc)}")
            traceback.print_exc()
            TelegramSender.send_message_html(
                f"<b>❌ فشل البحث</b>\n{html_escape(redact_error(exc))}"
            )
        finally:
            INSTAGRAM_JOB_LOCK.release()

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({
        "ok": True,
        "message": f"🔍 بدأ البحث: {html_escape(query)}",
        "mode": InstagramClient._mode,
    })


# ============================================================
# 13. Startup
# ============================================================
init_db()
InstagramClient.init_paths()
InstagramClient._load_user_cache()

print(f"📦 instaharvest-v2: {INSTAHARVEST_VERSION}")
print("🛡️ Control Panel Token (انسخه إلى صفحة ngrok):")
print(f"   {CONTROL_TOKEN}")
print("🧩 Features:")
print("   🔓 Anonymous: profile + posts + reels + public search")
print("   🔐 Login: saved session + Stories + Followers + Following")
print("   📌 Highlights + comments + cumulative SQLite + CSV/HTML")
print("   🔎 Search + optional Mention Hunter")
print("   🔁 Anonymous/Login switch: Login runs in background with manual status refresh")
print("   🌐 Control panel: zero automatic polling; status/code actions are manual")

if not os.getenv("NGROK_AUTH_TOKEN") and NGROK_AUTH_TOKEN:
    os.environ["NGROK_AUTH_TOKEN"] = NGROK_AUTH_TOKEN

# First client must be Anonymous by default so the known-good public path
# does not depend on Instagram password authentication.
InstagramClient._mode = "anonymous"
InstagramClient.get_client()

try:
    ngrok.kill()
except Exception:
    pass

if NGROK_AUTH_TOKEN:
    ngrok.set_auth_token(NGROK_AUTH_TOKEN)
else:
    print("⚠️ NGROK_AUTH_TOKEN غير موجود؛ pyngrok سيحاول إعداد النفق الحالي.")

PORT = 5000
try:
    # Use the account's fixed development domain. Do not silently fall back
    # to ngrok.connect(PORT) because that would change the public URL.
    try:
        tunnel = ngrok.connect(
            PORT,
            domain=NGROK_DOMAIN,
            bind_tls=True,
        )
    except TypeError:
        tunnel = ngrok.connect(
            PORT,
            domain=NGROK_DOMAIN,
        )
except Exception as exc:
    print(f"❌ تعذر فتح نطاق ngrok الثابت: {redact_error(exc)}")
    print(f"   النطاق المطلوب: https://{NGROK_DOMAIN}")
    raise RuntimeError(
        "لم يتم فتح ngrok لأن النطاق الثابت غير متاح لهذا الحساب/الجلسة. "
        "لن يتم إنشاء رابط عشوائي بديل."
    ) from exc

PUBLIC_URL = getattr(tunnel, "public_url", str(tunnel))
print("=" * 70)
print(f"🌐 Control Panel: https://{NGROK_DOMAIN}")
print("=" * 70)
print("✅ لا يوجد polling مستمر لـ /status.")
print("✅ تبديل Anonymous/Login يتم في طلب واحد.")
print("✅ البيانات القديمة في Google Drive لا تُحذف.")
print("=" * 70)

app.run(host="0.0.0.0", port=PORT, threaded=True, debug=False, use_reloader=False)
