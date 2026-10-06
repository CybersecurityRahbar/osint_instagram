# ============================================================
# 🕵️ Instagram OSINT Scraper ULTRA v3.1 — instaharvest-v2 powered (FIXED)
# ✅ تصحيح: استخدام public API بدلاً من users API في الوضع المجهول
# ✅ Powered by instaharvest-v2 (async, anti-detection, 14-layer fallback)
# ============================================================

# ============================================================
# 1. الإعدادات
# ============================================================
NGROK_AUTH_TOKEN = "3D7jdtzHrAWcvB3wrvii9O9XQWK_5xxaxx8csciSnyQWnNz2b"
IG_USERNAME = "cybe_rphantom400"
IG_PASSWORD = "phantom711#$opn"

# instaharvest-v2: الوضع المجهول يعمل للبيانات العامة عبر ig.public.*
USE_ANONYMOUS_MODE = True
CURRENT_MODE = "anonymous"
AUTH_SESSION_FILE = None

TELEGRAM_TOKEN = "8244019772:AAHT7uLh1yHEKlKJsDUps4SNPRpkjSG_msw"
TELEGRAM_CHAT_ID = "8266435771"

MAX_TRAVERSE_LIMIT = 10000
THUMB_MAX_SIZE = 150
THUMB_QUALITY = 40
THUMB_OPTIMIZE = True

# ============================================================
# 2. الاستيرادات
# ============================================================
REQUIRED_INSTAHARVEST_VERSION = "1.1.88"

import importlib.metadata as importlib_metadata

try:
    INSTAHARVEST_VERSION = importlib_metadata.version("instaharvest-v2")
except importlib_metadata.PackageNotFoundError:
    INSTAHARVEST_VERSION = None

if INSTAHARVEST_VERSION != REQUIRED_INSTAHARVEST_VERSION:
    raise RuntimeError(
        "نسخة instaharvest-v2 غير صحيحة. "
        f"المطلوب: {REQUIRED_INSTAHARVEST_VERSION} | "
        f"المثبت حالياً: {INSTAHARVEST_VERSION or 'غير مثبت'}. "
        "ثبّت النسخة المطلوبة: pip install instaharvest-v2==1.1.88"
    )

import os, shutil, json, io, time, random, requests, threading, sqlite3, traceback, csv, re, base64
from datetime import datetime
from html import escape as html_escape
from flask import Flask, request, render_template_string, jsonify
from pyngrok import ngrok

from instaharvest_v2 import Instagram

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("⚠️ PIL غير متوفر")

try:
    from google.colab import drive
    IN_COLAB = True
except ImportError:
    IN_COLAB = False
    drive = None

_http_session = requests.Session()
_http_session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
})

# ============================================================
# 3. التليجرام (مع retry)
# ============================================================
class TelegramSender:
    @staticmethod
    def send_message(text, retries=2):
        for i in range(retries + 1):
            try:
                r = _http_session.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                    data={
                        "chat_id": TELEGRAM_CHAT_ID,
                        "text": text[:4096],
                        "parse_mode": "Markdown",
                        "disable_web_page_preview": "true",
                    },
                    timeout=15)
                if r.status_code == 200:
                    return True
                if i < retries:
                    time.sleep(2)
            except Exception as e:
                if i < retries:
                    time.sleep(2)
                else:
                    print(f"⚠️ تليجرام: {e}")
        return False

    @staticmethod
    def send_rich_message(html_text, reply_markup=None, retries=2):
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "rich_message": json.dumps({
                "html": html_text[:32768],
                "is_rtl": True
            }, ensure_ascii=False),
            "disable_notification": False,
        }
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)

        for i in range(retries + 1):
            try:
                r = _http_session.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendRichMessage",
                    data=payload, timeout=20)
                if r.status_code == 200:
                    return True
                if i < retries:
                    time.sleep(1.5)
            except Exception as e:
                if i < retries:
                    time.sleep(1.5)
                else:
                    print(f"⚠️ Rich Message: {e}")

        try:
            r = _http_session.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": TelegramSender._strip_rich_html(html_text)[:4096],
                    "parse_mode": "HTML",
                    "disable_web_page_preview": "false",
                },
                timeout=15)
            return r.status_code == 200
        except Exception as e:
            print(f"⚠️ Rich fallback: {e}")
            return False

    @staticmethod
    def _strip_rich_html(html_text):
        text_value = re.sub(r"<blockquote[^>]*>", "", html_text, flags=re.I)
        text_value = re.sub(r"</blockquote>", "", text_value, flags=re.I)
        text_value = re.sub(r"<details[^>]*>", "", text_value, flags=re.I)
        text_value = re.sub(r"</details>", "", text_value, flags=re.I)
        text_value = re.sub(r"<[^>]+>", "", text_value)
        return text_value.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"')

    @staticmethod
    def send_follower_list(username, followers):
        followers = list(followers or [])
        total = len(followers)
        if total == 0:
            return False

        chunk_size = 20
        for start_idx in range(0, total, chunk_size):
            chunk = followers[start_idx:start_idx + chunk_size]
            lines = []
            for absolute_idx, follower in enumerate(chunk, start_idx + 1):
                uname = html_escape(str(getattr(follower, "username", "") or ""))
                fullname = html_escape(str(getattr(follower, "full_name", "") or "—"))
                lines.append(f"<b>{absolute_idx:03d}.</b> @{uname} — {fullname}")
            html_text = (
                f"<b>👥 المتابعون — @{html_escape(username.lstrip('@'))}</b>\n"
                f"📦 {start_idx + 1}–{start_idx + len(chunk)} من {total}\n\n"
                + "\n".join(lines)
            )
            TelegramSender.send_message_html(html_text)
            time.sleep(0.7)
        return True

    @staticmethod
    def send_message_html(html_text, retries=2):
        for i in range(retries + 1):
            try:
                r = _http_session.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                    data={
                        "chat_id": TELEGRAM_CHAT_ID,
                        "text": html_text[:4096],
                        "parse_mode": "HTML",
                        "disable_web_page_preview": "true",
                    },
                    timeout=15)
                if r.status_code == 200:
                    return True
            except Exception as e:
                if i < retries:
                    time.sleep(1.5)
                else:
                    print(f"⚠️ Telegram HTML: {e}")
            if i < retries:
                time.sleep(1.5)
        return False

    @staticmethod
    def _post_keyboard(url, profile_url=None):
        rows = [[{"text": "🔗 فتح المنشور", "url": url}]]
        if profile_url:
            rows.append([{"text": "👤 فتح الحساب", "url": profile_url}])
        return {"inline_keyboard": rows}

    @staticmethod
    def send_photo(photo_path, caption="", reply_markup=None):
        try:
            with open(photo_path, "rb") as f:
                payload = {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "caption": caption[:1024],
                    "parse_mode": "HTML",
                    "show_caption_above_media": "true",
                }
                if reply_markup:
                    payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
                r = _http_session.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                    data=payload, files={"photo": f}, timeout=30)
                if r.status_code == 200:
                    return True

                payload["caption"] = TelegramSender._strip_rich_html(caption)[:1024]
                r = _http_session.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                    data=payload, files={"photo": f}, timeout=30)
                return r.status_code == 200
        except Exception as e:
            print(f"⚠️ صورة: {e}")
            return False

    @staticmethod
    def send_video(video_path, caption="", reply_markup=None):
        try:
            with open(video_path, "rb") as f:
                payload = {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "caption": caption[:1024],
                    "parse_mode": "HTML",
                    "show_caption_above_media": "true",
                    "supports_streaming": "true",
                }
                if reply_markup:
                    payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
                r = _http_session.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendVideo",
                    data=payload, files={"video": f}, timeout=90)
                if r.status_code == 200:
                    return True

                payload["caption"] = TelegramSender._strip_rich_html(caption)[:1024]
                r = _http_session.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendVideo",
                    data=payload, files={"video": f}, timeout=90)
                return r.status_code == 200
        except Exception as e:
            print(f"⚠️ فيديو: {e}")
            return False

    @staticmethod
    def send_document(file_path, caption=""):
        try:
            with open(file_path, "rb") as f:
                r = _http_session.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument",
                    data={
                        "chat_id": TELEGRAM_CHAT_ID,
                        "caption": caption[:1024],
                        "parse_mode": "HTML",
                    },
                    files={"document": f}, timeout=60)
                return r.status_code == 200
        except Exception as e:
            print(f"⚠️ ملف: {e}")
            return False

# ============================================================
# 4. المسارات
# ============================================================
if IN_COLAB:
    if not os.path.exists("/content/drive/MyDrive"):
        drive.mount('/content/drive')
    BASE_PATH = "/content/drive/MyDrive/Instagram_Scraper_DB"
else:
    BASE_PATH = os.path.join(os.getcwd(), "Instagram_Scraper_DB")

DB_PATH = os.path.join(BASE_PATH, "instagram_data.db")
MEDIA_PATH = os.path.join(BASE_PATH, "media")
REPORTS_PATH = os.path.join(BASE_PATH, "reports")
STORIES_PATH = os.path.join(BASE_PATH, "stories")
HIGHLIGHTS_PATH = os.path.join(BASE_PATH, "highlights")

for p in (BASE_PATH, MEDIA_PATH, REPORTS_PATH, STORIES_PATH, HIGHLIGHTS_PATH):
    os.makedirs(p, exist_ok=True)

print(f"📁 {BASE_PATH}")

# ============================================================
# 5. 🎨 نظام الصور المصغرة
# ============================================================
class ThumbnailEngine:
    _url_cache = {}
    _MAX_CACHE_SIZE = 500

    @classmethod
    def from_url(cls, url, max_size=THUMB_MAX_SIZE, quality=THUMB_QUALITY, cache_key=None):
        if not url or not PIL_AVAILABLE:
            return None
        cache_key = cache_key or str(url)
        if cache_key in cls._url_cache:
            return cls._url_cache[cache_key]
        try:
            r = _http_session.get(str(url), timeout=15, stream=True)
            if r.status_code != 200:
                return None
            img = Image.open(io.BytesIO(r.content))
            img.thumbnail((max_size, max_size), Image.LANCZOS)
            if img.mode in ('RGBA', 'P', 'LA'):
                img = img.convert('RGB')
            buffer = io.BytesIO()
            img.save(buffer, format='JPEG', quality=quality, optimize=THUMB_OPTIMIZE,
                    progressive=True)
            b64 = base64.b64encode(buffer.getvalue()).decode()
            if len(cls._url_cache) >= cls._MAX_CACHE_SIZE:
                oldest = next(iter(cls._url_cache))
                del cls._url_cache[oldest]
            cls._url_cache[cache_key] = b64
            return b64
        except Exception as e:
            print(f"      ⚠️ thumb_from_url: {e}")
            return None

    @staticmethod
    def from_file(file_path, max_size=THUMB_MAX_SIZE, quality=THUMB_QUALITY):
        if not file_path or not os.path.exists(file_path) or not PIL_AVAILABLE:
            return None
        thumb_path = file_path + ".thumb.jpg"
        if os.path.exists(thumb_path):
            try:
                with open(thumb_path, "rb") as f:
                    return base64.b64encode(f.read()).decode()
            except: pass
        try:
            if file_path.lower().endswith(('.mp4', '.mov', '.webm')):
                return None
            img = Image.open(file_path)
            img.thumbnail((max_size, max_size), Image.LANCZOS)
            if img.mode in ('RGBA', 'P', 'LA'):
                img = img.convert('RGB')
            buffer = io.BytesIO()
            img.save(buffer, format='JPEG', quality=quality, optimize=THUMB_OPTIMIZE,
                    progressive=True)
            b64 = base64.b64encode(buffer.getvalue()).decode()
            try:
                with open(thumb_path, "wb") as f:
                    f.write(buffer.getvalue())
            except: pass
            return b64
        except Exception as e:
            print(f"      ⚠️ thumb_from_file: {e}")
            return None

    @staticmethod
    def placeholder_svg(icon='📷'):
        svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="150" height="150" viewBox="0 0 150 150">
        <rect fill="#2a2a3e" width="150" height="150"/>
        <text x="75" y="85" font-size="50" text-anchor="middle" fill="#e94560">{icon}</text>
        </svg>'''
        return base64.b64encode(svg.encode()).decode()

# ============================================================
# 6. قاعدة البيانات
# ============================================================
def _table_exists(cursor, table):
    cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cursor.fetchone() is not None

def _table_columns(cursor, table):
    cursor.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}

def _backup_db_before_migration():
    if not os.path.exists(DB_PATH):
        return None
    try:
        backup_dir = os.path.join(BASE_PATH, "db_backups")
        os.makedirs(backup_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(
            backup_dir, f"instagram_scraper_before_schema_migration_{stamp}.sqlite"
        )
        shutil.copy2(DB_PATH, path)
        print(f"🛡️ نسخة احتياطية لقاعدة البيانات: {path}")
        return path
    except Exception as e:
        print(f"⚠️ النسخة الاحتياطية: {type(e).__name__}: {str(e)[:160]}")
        return None

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    if os.path.exists(DB_PATH):
        needs_backup = False
        if _table_exists(c, "accounts"):
            cols = _table_columns(c, "accounts")
            needs_backup = not {"is_business","category","external_url","profile_pic"}.issubset(cols)
        else:
            needs_backup = True
        c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='following'")
        if c.fetchone() is None:
            needs_backup = True
        if needs_backup:
            conn.close()
            _backup_db_before_migration()
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE,
        full_name TEXT, bio TEXT, followers INTEGER, following INTEGER,
        posts_count INTEGER, is_private BOOLEAN, is_verified BOOLEAN,
        last_scraped DATETIME)''')

    c.execute('''CREATE TABLE IF NOT EXISTS posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER,
        post_code TEXT UNIQUE, post_url TEXT, media_type TEXT, caption TEXT,
        like_count INTEGER, comment_count INTEGER, view_count INTEGER DEFAULT 0,
        play_count INTEGER DEFAULT 0, taken_at DATETIME,
        file_path TEXT, downloaded BOOLEAN DEFAULT 0,
        thumbnail_url TEXT,
        FOREIGN KEY(account_id) REFERENCES accounts(id))''')

    c.execute('''CREATE TABLE IF NOT EXISTS comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER,
        comment_text TEXT, commenter_username TEXT, commenter_full_name TEXT,
        created_at DATETIME, FOREIGN KEY(post_id) REFERENCES posts(id))''')

    c.execute('''CREATE TABLE IF NOT EXISTS followers (
        id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER,
        username TEXT, full_name TEXT, first_seen DATETIME,
        UNIQUE(account_id, username),
        FOREIGN KEY(account_id) REFERENCES accounts(id))''')

    c.execute('''CREATE TABLE IF NOT EXISTS stories (
        id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER,
        story_pk TEXT UNIQUE, media_type TEXT, taken_at DATETIME,
        file_path TEXT, story_url TEXT, downloaded BOOLEAN DEFAULT 0,
        thumbnail_url TEXT,
        FOREIGN KEY(account_id) REFERENCES accounts(id))''')

    c.execute('''CREATE TABLE IF NOT EXISTS highlights (
        id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER,
        highlight_pk TEXT UNIQUE, title TEXT, media_count INTEGER,
        cover_url TEXT, cover_path TEXT, downloaded BOOLEAN DEFAULT 0,
        FOREIGN KEY(account_id) REFERENCES accounts(id))''')

    c.execute('''CREATE TABLE IF NOT EXISTS highlight_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT, highlight_id INTEGER,
        item_pk TEXT UNIQUE, media_type TEXT, taken_at DATETIME,
        file_path TEXT, video_url TEXT, downloaded BOOLEAN DEFAULT 0,
        FOREIGN KEY(highlight_id) REFERENCES highlights(id))''')

    c.execute('''CREATE TABLE IF NOT EXISTS search_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT, query TEXT, result_type TEXT,
        username TEXT, full_name TEXT, pk TEXT, extra_data TEXT,
        created_at DATETIME)''')

    c.execute('''CREATE TABLE IF NOT EXISTS mentions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, query TEXT, source_type TEXT,
        source_pk TEXT, source_url TEXT, context TEXT,
        mentioned_username TEXT, mentioned_full_name TEXT,
        created_at DATETIME)''')

    account_cols = _table_columns(c, "accounts")
    for col, decl in {
        "is_business": "INTEGER DEFAULT 0",
        "category": "TEXT DEFAULT ''",
        "external_url": "TEXT DEFAULT ''",
        "profile_pic": "TEXT DEFAULT ''",
    }.items():
        if col not in account_cols:
            c.execute(f"ALTER TABLE accounts ADD COLUMN {col} {decl}")
            print(f"🔧 Migration: أضيف {col} لـ accounts")

    c.execute('''CREATE TABLE IF NOT EXISTS following (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER,
        username TEXT,
        full_name TEXT,
        first_seen DATETIME,
        UNIQUE(account_id, username),
        FOREIGN KEY(account_id) REFERENCES accounts(id)
    )''')

    conn.commit()
    conn.close()
    print("✅ قاعدة البيانات جاهزة")

def save_or_update_account(info):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute('''INSERT INTO accounts
        (username, full_name, bio, followers, following, posts_count,
         is_private, is_verified, last_scraped, is_business, category, external_url, profile_pic)
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
            profile_pic=excluded.profile_pic''',
        (info['username'], info['full_name'], info['bio'], info['followers'],
         info['following'], info['posts_count'], int(info['is_private']),
         int(info['is_verified']), datetime.now().isoformat(),
         int(info.get('is_business', False)), str(info.get('category', '')),
         str(info.get('external_url', '')), str(info.get('profile_pic', ''))))
    conn.commit(); conn.close()

def _extract(obj, *keys, default=''):
    """استخراج مرن من dict أو object"""
    for k in keys:
        if isinstance(obj, dict):
            v = obj.get(k)
        else:
            v = getattr(obj, k, None)
        if v not in (None, ''):
            return v
    return default

def save_post_if_not_exists(account_username, media, media_type=None, file_path=None):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT id FROM accounts WHERE username=?", (account_username,))
    row = c.fetchone()
    if not row:
        conn.close()
        return None
    account_id = row[0]

    code = str(_extract(media, 'code', 'shortcode', default=''))
    if not code:
        conn.close()
        return None

    c.execute("SELECT id, file_path, downloaded FROM posts WHERE post_code=?", (code,))
    existing = c.fetchone()
    if existing:
        post_id = existing[0]
        taken = _extract(media, 'taken_at', default=None)
        taken_str = taken.isoformat() if hasattr(taken, 'isoformat') else (str(taken) if taken else None)
        c.execute('''UPDATE posts SET
            post_url=?, media_type=COALESCE(?, media_type), caption=?,
            like_count=?, comment_count=?, view_count=?, play_count=?,
            taken_at=?, thumbnail_url=?
            WHERE id=?''',
            (f"https://www.instagram.com/p/{code}/", media_type,
             str(_extract(media, 'caption_text', 'caption', default='')),
             int(_extract(media, 'like_count', 'likes', default=0) or 0),
             int(_extract(media, 'comment_count', 'comments', default=0) or 0),
             int(_extract(media, 'view_count', 'views', default=0) or 0),
             int(_extract(media, 'play_count', 'plays', default=0) or 0),
             taken_str,
             str(_extract(media, 'thumbnail_url', 'display_url', default='')),
             post_id))
        conn.commit(); conn.close()
        return post_id

    thumb_url = str(_extract(media, 'thumbnail_url', 'display_url', default=''))
    taken = _extract(media, 'taken_at', default=None)
    taken_str = taken.isoformat() if hasattr(taken, 'isoformat') else (str(taken) if taken else None)

    c.execute('''INSERT INTO posts (account_id, post_code, post_url, media_type,
        caption, like_count, comment_count, view_count, play_count, taken_at,
        file_path, downloaded, thumbnail_url)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (account_id, code, f"https://www.instagram.com/p/{code}/",
         media_type, str(_extract(media, 'caption_text', 'caption', default='')),
         int(_extract(media, 'like_count', 'likes', default=0) or 0),
         int(_extract(media, 'comment_count', 'comments', default=0) or 0),
         int(_extract(media, 'view_count', 'views', default=0) or 0),
         int(_extract(media, 'play_count', 'plays', default=0) or 0),
         taken_str, file_path, 1 if file_path else 0, thumb_url))
    post_id = c.lastrowid
    conn.commit(); conn.close()
    return post_id

def update_post_media_type(post_id, media_type, file_path, thumbnail_url=""):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("UPDATE posts SET media_type=?, file_path=?, thumbnail_url=? WHERE id=?",
             (media_type, file_path, thumbnail_url, post_id))
    conn.commit(); conn.close()

def save_comment(post_id, comment):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    try:
        user = _extract(comment, 'user', default={})
        username = _extract(user, 'username', default='') if user else ''
        fullname = _extract(user, 'full_name', default='') if user else ''
        c.execute('''INSERT OR IGNORE INTO comments
            (post_id, comment_text, commenter_username, commenter_full_name, created_at)
            VALUES (?,?,?,?,?)''',
            (post_id, str(_extract(comment, 'text', 'comment_text', default='')),
             username, fullname,
             str(_extract(comment, 'created_at_utc', 'created_at', default=''))))
        conn.commit()
    except Exception as e:
        print(f"      ⚠️ save_comment: {e}")
    conn.close()

def save_follower(account_id, follower):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    try:
        c.execute('''INSERT OR IGNORE INTO followers
            (account_id, username, full_name, first_seen) VALUES (?,?,?,?)''',
            (account_id, str(_extract(follower, 'username', default='')),
             str(_extract(follower, 'full_name', default='')), datetime.now().isoformat()))
        conn.commit()
    except: pass
    conn.close()

def save_story(account_id, story, file_path, story_url=""):
    try:
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        taken = _extract(story, 'taken_at', default=None)
        taken_str = taken.isoformat() if hasattr(taken, 'isoformat') else (str(taken) if taken else None)
        c.execute('''INSERT OR REPLACE INTO stories
            (account_id, story_pk, media_type, taken_at, file_path, story_url, downloaded)
            VALUES (?,?,?,?,?,?,1)''',
            (account_id, str(_extract(story, 'pk', 'id', default='')),
             'video' if int(_extract(story, 'media_type', default=1) or 1) == 2 else 'image',
             taken_str, file_path, story_url))
        conn.commit(); conn.close()
        return True
    except Exception as e:
        print(f"      ⚠️ DB save_story: {e}")
        return False

def save_highlight_record(account_id, highlight, cover_path=""):
    try:
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        pk = str(_extract(highlight, 'pk', 'id', default=''))
        title = str(_extract(highlight, 'title', 'name', default=''))
        count = int(_extract(highlight, 'media_count', 'item_count', default=0) or 0)
        cover = str(_extract(highlight, 'cover_url', 'thumbnail_url', default='') or '')
        c.execute('''INSERT INTO highlights
            (account_id, highlight_pk, title, media_count, cover_url, cover_path, downloaded)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(highlight_pk) DO UPDATE SET
                title=excluded.title,
                media_count=excluded.media_count,
                cover_url=excluded.cover_url,
                cover_path=excluded.cover_path,
                downloaded=excluded.downloaded''',
            (account_id, pk, title, count, cover, cover_path, 1 if cover_path else 0))
        hid = c.execute("SELECT id FROM highlights WHERE highlight_pk=?", (pk,)).fetchone()[0]
        conn.commit(); conn.close()
        return hid
    except Exception as e:
        print(f"      ⚠️ save_highlight: {type(e).__name__}: {str(e)[:120]}")
        try: conn.close()
        except Exception: pass
        return None

def save_highlight_item_record(highlight_id, item, file_path=""):
    try:
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        pk = str(_extract(item, 'pk', 'id', default=''))
        media_type = 'video' if _extract(item, 'video_url', default='') else 'image'
        taken = _extract(item, 'taken_at', default=None)
        taken_str = taken.isoformat() if hasattr(taken, 'isoformat') else (str(taken) if taken else None)
        video_url = str(_extract(item, 'video_url', default='') or '')
        c.execute('''INSERT INTO highlight_items
            (highlight_id, item_pk, media_type, taken_at, file_path, video_url, downloaded)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(item_pk) DO UPDATE SET
                highlight_id=excluded.highlight_id,
                media_type=excluded.media_type,
                taken_at=excluded.taken_at,
                file_path=excluded.file_path,
                video_url=excluded.video_url,
                downloaded=excluded.downloaded''',
            (highlight_id, pk, media_type, taken_str, file_path, video_url, 1 if file_path else 0))
        conn.commit(); conn.close()
        return True
    except Exception as e:
        print(f"      ⚠️ save_highlight_item: {type(e).__name__}: {str(e)[:120]}")
        try: conn.close()
        except Exception: pass
        return False

def get_account_id(username):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT id FROM accounts WHERE username=?", (username,))
    row = c.fetchone(); conn.close()
    return row[0] if row else None

def get_account_info(username):
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM accounts WHERE username=?", (username,))
    row = c.fetchone(); conn.close()
    return dict(row) if row else None

def get_posts_with_comments(account_id, limit=None, order='desc'):
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    c = conn.cursor()
    o = 'DESC' if order == 'desc' else 'ASC'
    l = f'LIMIT {limit}' if limit else ''
    c.execute(f"SELECT * FROM posts WHERE account_id=? ORDER BY taken_at {o} {l}", (account_id,))
    posts = [dict(r) for r in c.fetchall()]
    for post in posts:
        c.execute("SELECT * FROM comments WHERE post_id=? ORDER BY created_at ASC", (post['id'],))
        post['comments'] = [dict(r) for r in c.fetchall()]
    conn.close(); return posts

def get_followers(account_id, limit=None):
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    c = conn.cursor()
    l = f'LIMIT {limit}' if limit else ''
    c.execute(f"SELECT * FROM followers WHERE account_id=? ORDER BY first_seen ASC {l}", (account_id,))
    f = [dict(r) for r in c.fetchall()]; conn.close(); return f


def get_following(account_id, limit=None):
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    c = conn.cursor()
    l = f'LIMIT {limit}' if limit else ''
    c.execute(f"SELECT * FROM following WHERE account_id=? ORDER BY first_seen ASC {l}", (account_id,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close(); return rows

def save_following(account_id, user_obj):
    try:
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute('''INSERT OR IGNORE INTO following
            (account_id, username, full_name, first_seen) VALUES (?,?,?,?)''',
            (account_id,
             str(_extract(user_obj, 'username', default='')),
             str(_extract(user_obj, 'full_name', 'fullname', default='')),
             datetime.now().isoformat()))
        conn.commit(); conn.close(); return True
    except Exception as e:
        print(f"      ⚠️ save_following: {e}")
        try: conn.close()
        except Exception: pass
        return False

def get_stories(account_id):
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM stories WHERE account_id=? ORDER BY taken_at DESC", (account_id,))
    s = [dict(r) for r in c.fetchall()]; conn.close(); return s

def get_highlights(account_id):
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM highlights WHERE account_id=? ORDER BY title ASC", (account_id,))
    rows = [dict(r) for r in c.fetchall()]
    for h in rows:
        c.execute("SELECT * FROM highlight_items WHERE highlight_id=? ORDER BY taken_at ASC", (h['id'],))
        h['items'] = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def save_mention(query, source_type, source_pk, source_url, context,
                 mentioned_username="", mentioned_full_name=""):
    try:
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute('''INSERT INTO mentions
            (query, source_type, source_pk, source_url, context,
             mentioned_username, mentioned_full_name, created_at)
            VALUES (?,?,?,?,?,?,?,?)''',
            (query, source_type, source_pk, source_url, context,
             mentioned_username, mentioned_full_name, datetime.now().isoformat()))
        conn.commit(); conn.close()
        return True
    except Exception as e:
        print(f"      ⚠️ save_mention: {e}")
        return False

# ============================================================
# 7. عميل انستغرام (instaharvest-v2) — Anonymous Mode
# ============================================================
class InstagramClient:
    _ig = None
    _mode = CURRENT_MODE if CURRENT_MODE in ("anonymous", "login") else "anonymous"
    _status = "starting"
    _last_error = ""
    _session_loaded = False
    _last_throttle_at = 0.0
    _last_challenge_at = 0.0

    _user_id_cache = {}
    _USER_CACHE_FILE = (
        "/content/drive/MyDrive/ig_user_cache_v2.json" if IN_COLAB
        else "ig_user_cache_v2.json"
    )

    @classmethod
    def init_paths(cls):
        global AUTH_SESSION_FILE
        AUTH_SESSION_FILE = os.path.join(BASE_PATH, "instaharvest_session.json")
        os.makedirs(BASE_PATH, exist_ok=True)

    @classmethod
    def _load_user_cache(cls):
        if not os.path.exists(cls._USER_CACHE_FILE):
            return
        try:
            with open(cls._USER_CACHE_FILE, "r", encoding="utf-8") as f:
                cls._user_id_cache = json.load(f)
            print(f"✅ تم تحميل {len(cls._user_id_cache)} مستخدم من الـ Cache")
        except Exception as e:
            print(f"⚠️ User cache: {type(e).__name__}")

    @classmethod
    def _save_user_cache(cls):
        try:
            with open(cls._USER_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(cls._user_id_cache, f, ensure_ascii=False)
        except Exception as e:
            print(f"⚠️ Save user cache: {type(e).__name__}")

    @classmethod
    def _mode_label(cls):
        return "🔓 Anonymous" if cls._mode == "anonymous" else "🔐 Login"

    @classmethod
    def get_status(cls):
        return {
            "mode": cls._mode,
            "status": cls._status,
            "session_loaded": bool(cls._session_loaded),
            "message": (
                f"✅ {cls._mode_label()} — العميل جاهز"
                if cls._status == "ready" else
                "⏳ 🔐 جاري تسجيل الدخول/تحميل الجلسة..."
                if cls._status == "logging_in" else
                f"❌ {cls._mode_label()} — {html_escape(cls._last_error or 'خطأ غير محدد')}"
                if cls._status == "error" else
                f"ℹ️ {cls._mode_label()} — {cls._status}"
            )
        }

    @classmethod
    def _record_error(cls, exc):
        cls._last_error = (str(exc).strip() or type(exc).__name__)[:700]
        low = cls._last_error.lower()
        if "429" in low or "too many requests" in low:
            cls._last_throttle_at = time.time()
        if "challenge" in low or "verification" in low:
            cls._last_challenge_at = time.time()
        cls._status = "error"

    @classmethod
    def is_throttled(cls):
        return bool(cls._last_throttle_at and time.time() - cls._last_throttle_at < 300)

    @classmethod
    def throttle_remaining(cls):
        return max(0, int(300 - (time.time() - cls._last_throttle_at))) if cls._last_throttle_at else 0

    @classmethod
    def _make_anonymous(cls):
        cls._ig = Instagram.anonymous(unlimited=True)
        cls._mode = "anonymous"
        cls._status = "ready"
        cls._session_loaded = False
        cls._last_error = ""
        print("🔓 Anonymous Mode — جاهز")

    @classmethod
    def _make_login(cls):
        cls._status = "logging_in"
        cls._last_error = ""
        ig = Instagram()

        if AUTH_SESSION_FILE and os.path.exists(AUTH_SESSION_FILE):
            try:
                ig.auth.load_session(AUTH_SESSION_FILE)
                valid = True
                if hasattr(ig.auth, "validate_session"):
                    result = ig.auth.validate_session()
                    if result is False:
                        valid = False
                if valid:
                    cls._ig = ig
                    cls._mode = "login"
                    cls._status = "ready"
                    cls._session_loaded = True
                    print(f"♻️ تم تحميل جلسة الأداة من: {AUTH_SESSION_FILE}")
                    return ig
                print("⚠️ الجلسة المحفوظة غير صالحة.")
            except Exception as e:
                print(f"⚠️ فشل تحميل الجلسة: {type(e).__name__}: {str(e)[:180]}")

        if not IG_USERNAME or not IG_PASSWORD:
            raise RuntimeError("IG_USERNAME و IG_PASSWORD مطلوبان لوضع Login.")

        try:
            result = ig.login(IG_USERNAME, IG_PASSWORD)
            if result is False:
                raise RuntimeError("Instagram رفض تسجيل الدخول.")
            if AUTH_SESSION_FILE:
                ig.auth.save_session(AUTH_SESSION_FILE)
            cls._ig = ig
            cls._mode = "login"
            cls._status = "ready"
            cls._session_loaded = False
            cls._last_error = ""
            print(f"✅ Login ناجح — جلسة الأداة محفوظة في {AUTH_SESSION_FILE}")
            return ig
        except Exception:
            cls._ig = None
            raise

    @classmethod
    def switch_mode(cls, mode):
        mode = str(mode or "").strip().lower()
        if mode not in ("anonymous", "login"):
            raise ValueError("الوضع يجب أن يكون anonymous أو login")

        with INSTAGRAM_JOB_LOCK:
            cls._ig = None
            cls._session_loaded = False
            try:
                if mode == "anonymous":
                    cls._make_anonymous()
                else:
                    cls._make_login()
            except Exception as e:
                cls._record_error(e)
                if mode == "login":
                    # أبقِ الصفحة والسيرفر قابلين للاستخدام حتى يفشل Login.
                    try:
                        cls._make_anonymous()
                        print("↩️ عاد العميل إلى Anonymous بعد فشل Login")
                    except Exception:
                        pass
                raise
        return cls.get_status()

    @classmethod
    def get_client(cls):
        if cls._ig is not None and cls._status == "ready":
            return cls._ig
        cls._load_user_cache()
        try:
            return cls._make_login() if cls._mode == "login" else cls._make_anonymous() or cls._ig
        except Exception as e:
            cls._record_error(e)
            return None

    @classmethod
    def refresh_if_needed(cls):
        return cls.get_client() is not None and cls._status == "ready"

    @classmethod
    def human_delay(cls, a=0.8, b=2.2):
        time.sleep(random.uniform(a, b))

# ============================================================
# 8. 🔍 محرك البحث الشامل
# ============================================================
class UniversalSearcher:
    def __init__(self, ig):
        self.ig = ig

    def analyze_query(self, query):
        query = query.strip()
        analysis = {'original': query, 'clean': query, 'type': 'general', 'targets': [], 'keywords': []}

        if query.startswith('#'):
            analysis['type'] = 'hashtag'
            analysis['clean'] = query[1:].strip()
            return analysis

        if query.startswith('@'):
            analysis['type'] = 'user'
            analysis['clean'] = query[1:].strip()
            return analysis

        if 'instagram.com' in query:
            m = re.search(r'instagram\.com/([A-Za-z0-9_.]+)', query)
            if m:
                analysis['type'] = 'user'
                analysis['clean'] = m.group(1)
                return analysis

        words = query.split()
        analysis['keywords'] = [w for w in words if len(w) > 2]
        analysis['type'] = 'general'
        return analysis

    def _call_media_comments(self, media_pk, max_count=20):
        try:
            if hasattr(self.ig, "media") and hasattr(self.ig.media, "get_comments_parsed"):
                return list(self.ig.media.get_comments_parsed(media_pk) or [])[:max_count]
        except Exception:
            pass
        return []

    def _hunt_mentions(self, query, max_posts=5, max_comments_per_post=20):
        clean = str(query or "").lower().replace("#","").replace("@","").strip()
        if not clean:
            return []
        mentions = []
        try:
            posts = list(self.ig.public.get_hashtag_posts(clean, max_count=max_posts) or [])
        except Exception:
            posts = []
        for media in posts[:max_posts]:
            pk = str(_extract(media, 'pk','id',default=''))
            code = str(_extract(media, 'code','shortcode',default=''))
            source = f"https://www.instagram.com/p/{code}/"
            caption = str(_extract(media,'caption_text','caption',default='') or '')
            if clean in caption.lower():
                mentions.append({'type':'caption','source_pk':pk,'source_url':source,
                                 'context':caption[:500],'mentioned_username':clean,
                                 'mentioned_full_name':''})
                save_mention(clean,'caption',pk,source,caption[:500],clean,'')

            try:
                comments = self._call_media_comments(pk, max_comments_per_post)
                for comment in comments:
                    txt = str(_extract(comment,'text','comment_text',default='') or '')
                    if clean not in txt.lower():
                        continue
                    user = _extract(comment,'user',default={})
                    uname = str(_extract(user,'username',default=''))
                    fullname = str(_extract(user,'full_name','fullname',default=''))
                    mentions.append({'type':'comment','source_pk':pk,'source_url':source,
                                     'context':txt[:500],'commenter':uname,
                                     'commenter_full_name':fullname})
                    save_mention(clean,'comment',pk,source,txt[:500],uname,fullname)
            except Exception:
                pass
            InstagramClient.human_delay(0.4,0.9)
        return mentions

    def search(self, query, max_results_per_type=10, hunt_mentions=True):
        analysis = self.analyze_query(query)
        print(f"🔍 بحث شامل: '{query}' — النوع: {analysis['type']}")
        results = {
            'analysis': analysis,
            'users': [], 'hashtags': [], 'posts': [],
            'reels': [], 'mentions': [], 'stats': {'total': 0}
        }

        if analysis['type'] == 'user':
            name = analysis['clean']
            try:
                if InstagramClient._mode == 'login' and hasattr(self.ig, 'users'):
                    u = self.ig.users.get_full_profile(name)
                else:
                    u = self.ig.public.get_profile(name)
                if u: results['users'] = [u]
            except Exception as e:
                print(f"   ⚠️ profile: {type(e).__name__}: {str(e)[:120]}")

            try:
                results['posts'] = list(self.ig.public.get_posts(name, max_count=max_results_per_type) or [])
            except Exception:
                if InstagramClient._mode == 'login' and results['users']:
                    try:
                        uid = str(_extract(results['users'][0],'pk','id',default=''))
                        results['posts'] = list(self.ig.feed.get_all_posts(uid, max_posts=max_results_per_type, delay=1.0) or [])
                    except Exception:
                        pass
            try:
                results['reels'] = list(self.ig.public.get_reels(name, max_count=max_results_per_type) or [])
            except Exception:
                pass

        elif analysis['type'] == 'hashtag':
            tag = analysis['clean']
            try:
                results['posts'] = list(self.ig.public.get_hashtag_posts(tag, max_count=max_results_per_type) or [])
                results['hashtags'] = [{'name':tag,'media_count':len(results['posts'])}]
            except Exception:
                if InstagramClient._mode == 'login' and hasattr(self.ig, 'search'):
                    try:
                        results['hashtags'] = list(self.ig.search.search_hashtags(tag) or [])[:max_results_per_type]
                    except Exception:
                        pass

        else:
            if InstagramClient._mode == 'login' and hasattr(self.ig, 'search'):
                try:
                    results['users'] = list(self.ig.search.search_users(query) or [])[:max_results_per_type]
                except Exception:
                    pass
                try:
                    results['hashtags'] = list(self.ig.search.search_hashtags(query) or [])[:max_results_per_type]
                except Exception:
                    pass
            elif hasattr(self.ig, 'public'):
                try:
                    results['users'] = list(self.ig.public.search(query) or [])[:max_results_per_type]
                except Exception:
                    try:
                        u = self.ig.public.get_profile(query)
                        if u: results['users'] = [u]
                    except Exception:
                        pass

        if hunt_mentions and analysis['type'] == 'general':
            results['mentions'] = self._hunt_mentions(
                query, max_posts=min(5,max_results_per_type),
                max_comments_per_post=20
            )

        results['stats']['total'] = sum(
            len(results[k]) for k in ('users','hashtags','posts','reels','mentions')
        )
        self._save_search_results(query, results)
        self._send_summary(query, results)
        return results

    def _save_search_results(self, query, results):
        try:
            conn = sqlite3.connect(DB_PATH); c = conn.cursor()
            for rtype in ['users', 'hashtags', 'posts', 'reels', 'mentions']:
                for item in results[rtype]:
                    username = str(_extract(item, 'username', 'commenter', default=''))
                    full_name = str(_extract(item, 'full_name', 'fullname', 'commenter_full_name', default=''))
                    pk = str(_extract(item, 'pk', 'id', 'source_pk', default=''))
                    c.execute('''INSERT INTO search_results
                        (query, result_type, username, full_name, pk, extra_data, created_at)
                        VALUES (?,?,?,?,?,?,?)''',
                        (query, rtype, username, full_name, pk, '{}', datetime.now().isoformat()))
            conn.commit(); conn.close()
        except Exception as e:
            print(f"⚠️ حفظ نتائج البحث: {e}")

    def _send_summary(self, query, results):
        summary_lines = [f"🔍 *نتائج البحث الشامل*\n📝 الاستعلام: `{query}`\n"]

        if results['users']:
            summary_lines.append(f"👤 مستخدمين: {len(results['users'])}")
            for u in results['users'][:3]:
                uname = _extract(u, 'username', default='?')
                summary_lines.append(f"   • @{uname}")

        if results['hashtags']:
            summary_lines.append(f"#️⃣ هاشتاجات: {len(results['hashtags'])}")

        if results['posts']:
            summary_lines.append(f"📸 منشورات: {len(results['posts'])}")

        TelegramSender.send_message("\n".join(summary_lines))

    def generate_search_report(self, query, results):
        ts = datetime.now().strftime('%Y-%m-%d %H:%M')
        sections_html = []

        # ============ المستخدمين ============
        if results['users']:
            items_html = ""
            for u in results['users']:
                pic_url = str(_extract(u, 'profile_pic_url', 'profile_pic_url_hd', default=''))
                pic_b64 = ThumbnailEngine.from_url(pic_url, 80, 50, cache_key=f"u_{_extract(u, 'username')}") if pic_url else None
                pic_tag = (f"<img src='data:image/jpeg;base64,{pic_b64}' class='user-pic'>"
                          if pic_b64 else "<div class='user-pic placeholder'>👤</div>")

                followers = int(_extract(u, 'follower_count', 'followers', default=0) or 0)
                bio = str(_extract(u, 'biography', 'bio', default=''))[:150]

                items_html += f'''<div class="user-card">
                    {pic_tag}
                    <div class="user-info">
                        <div class="user-username">@{_extract(u, 'username', default='?')}</div>
                        <div class="user-name">{_extract(u, 'full_name', 'fullname', default='')}</div>
                        <div class="user-stats">👥 {followers:,} متابع</div>
                        <div class="user-bio">{bio}</div>
                    </div>
                </div>'''
            sections_html.append(f'''<section class="section">
                <h2 class="section-title">👤 المستخدمون ({len(results['users'])})</h2>
                <div class="users-grid">{items_html}</div>
            </section>''')

        # ============ المنشورات ============
        if results['posts']:
            items_html = ""
            for p in results['posts']:
                thumb_url = str(_extract(p, 'thumbnail_url', 'display_url', default=''))
                thumb_b64 = None
                if thumb_url:
                    thumb_b64 = ThumbnailEngine.from_url(
                        thumb_url, THUMB_MAX_SIZE, THUMB_QUALITY,
                        cache_key=f"p_{_extract(p, 'code', 'shortcode', default='')}")
                if not thumb_b64:
                    thumb_b64 = ThumbnailEngine.placeholder_svg('📷')

                code = _extract(p, 'code', 'shortcode', default='')
                caption = str(_extract(p, 'caption_text', 'caption', default=''))[:200]
                likes = int(_extract(p, 'like_count', 'likes', default=0) or 0)
                comments = int(_extract(p, 'comment_count', 'comments', default=0) or 0)

                items_html += f'''<div class="search-post-card">
                    <a href="https://instagram.com/p/{code}" target="_blank" class="post-thumb-wrap">
                        <img src="data:image/jpeg;base64,{thumb_b64}" class="post-thumb" loading="lazy">
                    </a>
                    <div class="search-post-info">
                        <div class="post-stats">❤️ {likes:,} — 💬 {comments:,}</div>
                        <div class="post-caption">{caption}</div>
                        <a href="https://instagram.com/p/{code}" target="_blank" class="post-link">🔗 فتح</a>
                    </div>
                </div>'''
            sections_html.append(f'''<section class="section">
                <h2 class="section-title">📸 المنشورات ({len(results['posts'])})</h2>
                <div class="search-posts-grid">{items_html}</div>
            </section>''')

        css = """
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Tahoma,Arial,sans-serif;
background:linear-gradient(135deg,#1a1a2e 0%,#16213e 50%,#0f3460 100%);
background-attachment:fixed;color:#e0e0e0;line-height:1.6;padding:16px;min-height:100vh}
.container{max-width:1200px;margin:0 auto}
.header{background:linear-gradient(135deg,#e94560 0%,#c73659 100%);
padding:30px;border-radius:20px;margin-bottom:20px;text-align:center}
.section{background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);
border-radius:16px;padding:20px;margin-bottom:20px}
.section-title{font-size:1.2rem;color:#e94560;margin-bottom:16px;
padding-bottom:10px;border-bottom:2px solid #e94560}
.users-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px}
.user-card{background:rgba(0,0,0,0.2);border-radius:12px;padding:14px;
display:flex;gap:12px;border:1px solid rgba(233,69,96,0.25)}
.user-pic{width:60px;height:60px;border-radius:50%;object-fit:cover;border:2px solid #e94560}
.search-posts-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}
.search-post-card{background:rgba(0,0,0,0.2);border-radius:12px;overflow:hidden;
border:1px solid rgba(233,69,96,0.25);display:flex}
.post-thumb-wrap{width:120px;flex-shrink:0}
.post-thumb{width:100%;aspect-ratio:1/1;object-fit:cover}
.search-post-info{flex:1;padding:10px}
.post-link{color:#64b5f6;text-decoration:none;font-size:0.75rem}
"""
        html = f"""<!DOCTYPE html>
<html lang="ar" dir="rtl"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>تقرير بحث — {query}</title><style>{css}</style></head>
<body><div class="container">
<div class="header"><h1>🔍 تقرير البحث الشامل</h1>
<div>📝 {query}</div><div>📅 {ts}</div></div>
{''.join(sections_html)}
</div></body></html>"""

        safe_query = "".join(c for c in query if c.isalnum() or c in "_- ")[:50] or "search"
        f = os.path.join(REPORTS_PATH, f"search_{safe_query}_{datetime.now().strftime('%Y%m%d_%H%M')}.html")
        with open(f, 'w', encoding='utf-8') as fp:
            fp.write(html)

        size_kb = os.path.getsize(f) / 1024
        print(f"📄 تقرير البحث: {f} ({size_kb:.1f} KB)")
        TelegramSender.send_document(f, f"🔍 تقرير بحث: {query}\n📦 الحجم: {size_kb:.1f} KB")
        return f

# ============================================================
# 9. أداة السكراب — instaharvest-v2 Public API
# ============================================================
class InstagramSearcher:
    def __init__(self):
        self.ig = InstagramClient.get_client()
        if self.ig is None:
            raise RuntimeError("فشل الحصول على عميل انستغرام")
        self.universal_searcher = UniversalSearcher(self.ig)

    def _get_user_id(self, username):
        username = str(username or "").lower().strip().lstrip("@")
        if not username:
            return None
        if username in InstagramClient._user_id_cache:
            return InstagramClient._user_id_cache[username]
        try:
            # ✅ الصحيح: public.get_profile
            user = self.ig.public.get_profile(username)
            uid = str(_extract(user, 'pk', 'id', default=''))
            if uid:
                InstagramClient._user_id_cache[username] = uid
                InstagramClient._save_user_cache()
                return uid
        except Exception as e:
            print(f"      ⚠️ user_id lookup: {type(e).__name__}: {str(e)[:120]}")
        return None

    def _fetch_posts(self, username, max_count=MAX_TRAVERSE_LIMIT):
        try:
            # ✅ الصحيح: public.get_posts
            posts = self.ig.public.get_posts(username, max_count=max_count)
            return list(posts) if posts else []
        except Exception as e:
            print(f"   ⚠️ get_posts: {type(e).__name__}: {str(e)[:120]}")
            return []

    def _fetch_reels(self, username, max_count=50):
        try:
            if hasattr(self.ig.public, 'get_reels'):
                reels = self.ig.public.get_reels(username, max_count=max_count)
                return list(reels) if reels else []
        except Exception as e:
            print(f"   ⚠️ get_reels: {type(e).__name__}")
        return []

    def _fetch_stories(self, username, max_count=50):
        """القصص غير مدعومة في الوضع المجهول عادةً"""
        try:
            if hasattr(self.ig.public, 'get_stories'):
                stories = self.ig.public.get_stories(username, max_count=max_count)
                return list(stories) if stories else []
            else:
                print("   ℹ️ القصص غير مدعومة في الوضع المجهول — تخطي")
                return []
        except Exception as e:
            print(f"   ⚠️ get_stories: {type(e).__name__}: {str(e)[:120]}")
            return []

    def _download_media(self, media, folder):
        """تحميل الوسائط من URL مباشرة"""
        os.makedirs(folder, exist_ok=True)
        downloaded = []
        try:
            code = str(_extract(media, 'code', 'shortcode', 'pk', 'id', default='media'))
            video_url = _extract(media, 'video_url', default=None)
            image_url = _extract(media, 'display_url', 'thumbnail_url', 'image_url', default=None)

            target_url = video_url or image_url
            if not target_url:
                print(f"      ⚠️ لا يوجد URL للوسائط")
                return []

            ext = 'mp4' if video_url else 'jpg'
            filepath = os.path.join(folder, f"{code}.{ext}")

            r = _http_session.get(str(target_url), timeout=60, stream=True)
            if r.status_code == 200:
                with open(filepath, 'wb') as f:
                    for chunk in r.iter_content(8192):
                        f.write(chunk)
                kind = 'video' if ext == 'mp4' else 'image'
                downloaded.append((filepath, kind))
                print(f"      ✅ نُزّل: {os.path.basename(filepath)}")
            else:
                print(f"      ⚠️ HTTP {r.status_code} للوسائط")

        except Exception as e:
            print(f"      ⚠️ download: {type(e).__name__}: {str(e)[:140]}")
        return downloaded

    def _post_caption_html(self, media, index, total, username):
        code = html_escape(str(_extract(media, 'code', 'shortcode', default='')))
        url = f"https://www.instagram.com/p/{code}/"
        date = _extract(media, 'taken_at', default=None)
        date_text = date.strftime("%Y-%m-%d %H:%M") if hasattr(date, 'strftime') else "غير معروف"
        caption = str(_extract(media, 'caption_text', 'caption', default='(لا يوجد نص)'))
        caption = html_escape(caption[:600])
        likes = int(_extract(media, 'like_count', 'likes', default=0) or 0)
        comments = int(_extract(media, 'comment_count', 'comments', default=0) or 0)
        profile_url = f"https://www.instagram.com/{html_escape(username)}/"

        views = int(_extract(media, 'view_count', 'views', default=0) or 0)
        plays = int(_extract(media, 'play_count', 'plays', default=0) or 0)
        view_line = f"👁️ {views:,}" if views else (f"▶️ {plays:,}" if plays else "👁️ —")
        return (
            f"<b>📌 المنشور #{index:02d} / {total:02d}</b>  •  <code>{code}</code>\n"
            f"👤 <a href=\"{profile_url}\">@{html_escape(username)}</a>\n"
            f"📅 {date_text}\n"
            f"❤️ {likes:,}   💬 {comments:,}   {view_line}\n"
            f"\n<blockquote>{caption}</blockquote>\n"
            f'<a href="{url}">🔗 فتح المنشور على Instagram</a>'
        )

    def _process_media(self, media, username, folder, index=1, total=1,
                       fetch_comments=False, max_comments=20):
        try:
            pid = save_post_if_not_exists(username, media, None)
            if pid is None:
                print(f"      ⚠️ لا يوجد حساب محفوظ لـ @{username}")
                return False

            files = self._download_media(media, folder)

            if not files:
                TelegramSender.send_rich_message(
                    self._post_caption_html(media, index, total, username) +
                    "\n\n⚠️ <b>تعذر تنزيل الوسائط</b> — تم الاحتفاظ بالمنشور في DB."
                )
                return False

            main_fp = files[0][0]
            mtype = files[0][1] if len(files) == 1 else 'carousel'
            thumb_url = str(_extract(media, 'thumbnail_url', 'display_url', default=''))

            update_post_media_type(pid, mtype, main_fp, thumb_url)

            keyboard = TelegramSender._post_keyboard(
                f"https://www.instagram.com/p/{html_escape(str(_extract(media, 'code', default='')))}/",
                f"https://www.instagram.com/{html_escape(username)}/"
            )

            caption = self._post_caption_html(media, index, total, username)
            for file_index, (path, kind) in enumerate(files, 1):
                media_caption = caption
                if kind == 'video':
                    ok = TelegramSender.send_video(path, media_caption, reply_markup=keyboard if file_index == 1 else None)
                else:
                    ok = TelegramSender.send_photo(path, media_caption, reply_markup=keyboard if file_index == 1 else None)
                if not ok:
                    print(f"      ⚠️ تعذر إرسال {os.path.basename(path)}")
                InstagramClient.human_delay(0.5, 1.2)

            if fetch_comments:
                try:
                    code = _extract(media, 'code', 'shortcode', default='')
                    if code and hasattr(self.ig.public, 'get_comments'):
                        comments = self.ig.public.get_comments(code, max_count=max_comments)
                        for c in (comments or []):
                            save_comment(pid, c)
                except Exception as ce:
                    print(f"   ⚠️ تعليقات: {type(ce).__name__}: {str(ce)[:100]}")

            return True

        except Exception as e:
            if "rate" in str(e).lower() or "429" in str(e).lower():
                InstagramClient._mark_throttle(e)
                print("🛑 تم إيقاف المعالجة مؤقتاً بسبب 429.")
                return False
            print(f"⚠️ معالجة: {type(e).__name__}: {str(e)[:120]}")
            return False

    def _call(self, obj, dotted, *args, **kwargs):
        current = obj
        for part in dotted.split("."):
            if not hasattr(current, part):
                return None
            current = getattr(current, part)
        return current(*args, **kwargs) if callable(current) else current

    def _fetch_followers(self, username, max_count=100):
        if InstagramClient._mode != "login":
            return []
        uid = self._get_user_id(username)
        if not uid:
            return []
        try:
            users = self._call(self.ig, "friendships.get_all_followers", uid)
            if users is None:
                users = self._call(self.ig, "graphql.get_followers", uid)
            return list(users or [])[:max_count]
        except Exception as e:
            print(f"   ⚠️ followers: {type(e).__name__}: {str(e)[:160]}")
            return []

    def _fetch_following(self, username, max_count=100):
        if InstagramClient._mode != "login":
            return []
        uid = self._get_user_id(username)
        if not uid:
            return []
        try:
            users = self._call(self.ig, "friendships.get_all_following", uid)
            return list(users or [])[:max_count]
        except Exception as e:
            print(f"   ⚠️ following: {type(e).__name__}: {str(e)[:160]}")
            return []

    def _fetch_stories(self, username, max_count=50):
        uid = self._get_user_id(username)
        if not uid:
            return []
        try:
            items = self._call(self.ig, "stories.get_user_stories", uid)
            if items is None and hasattr(self.ig, "public"):
                items = self._call(self.ig.public, "get_stories", username, max_count=max_count)
            return list(items or [])[:max_count]
        except Exception as e:
            print(f"   ⚠️ stories: {type(e).__name__}: {str(e)[:160]}")
            return []

    def _fetch_highlights(self, username, max_count=20):
        uid = self._get_user_id(username)
        if not uid:
            return []
        try:
            items = self._call(self.ig, "stories.get_highlights_tray", uid)
            if items is None:
                items = self._call(self.ig, "public.get_highlights", username)
            return list(items or [])[:max_count]
        except Exception as e:
            print(f"   ⚠️ highlights: {type(e).__name__}: {str(e)[:160]}")
            return []

    def _fetch_comments(self, media, max_comments=20):
        code = _extract(media, 'code', 'shortcode', default='')
        if code and hasattr(self.ig, "public"):
            try:
                return list(self.ig.public.get_comments(code, max_count=max_comments) or [])
            except Exception:
                pass
        try:
            pk = _extract(media, 'pk', 'id', default='')
            items = self._call(self.ig, "media.get_comments_parsed", pk)
            return list(items or [])[:max_comments]
        except Exception:
            return []

    def _download_url(self, url, filepath):
        if not url:
            return False
        try:
            r = _http_session.get(str(url), timeout=60, stream=True)
            if r.status_code != 200:
                return False
            with open(filepath, "wb") as f:
                for chunk in r.iter_content(1024 * 32):
                    f.write(chunk)
            return True
        except Exception:
            return False

    def _normalize_download_result(self, result, folder, name):
        vals = result if isinstance(result, (list, tuple, set)) else [result]
        out = []
        for item in vals:
            if item is None:
                continue
            path = os.fspath(item) if isinstance(item, os.PathLike) else str(item)
            if not os.path.exists(path):
                continue
            ext = os.path.splitext(path)[1] or ".jpg"
            target = os.path.join(folder, name + ext)
            if os.path.abspath(path) != os.path.abspath(target):
                try:
                    shutil.copy2(path, target)
                    path = target
                except Exception:
                    pass
            out.append((path, "video" if path.lower().endswith((".mp4",".mov",".webm")) else "image"))
        return out

    def _download_media(self, media, folder):
        os.makedirs(folder, exist_ok=True)
        code = str(_extract(media, 'code', 'shortcode', 'pk', 'id', default='media'))

        if InstagramClient._mode == "login":
            pk = _extract(media, 'pk', 'id', default='')
            if pk:
                try:
                    got = self._call(self.ig, "download.download_media", pk)
                    normalized = self._normalize_download_result(got, folder, code)
                    if normalized:
                        return normalized
                except Exception as e:
                    print(f"      ⚠️ native download: {type(e).__name__}")

        video_url = _extract(media, 'video_url', default='')
        image_url = _extract(media, 'display_url', 'thumbnail_url', 'image_url', default='')
        url = video_url or image_url
        if not url:
            return []
        ext = ".mp4" if video_url else ".jpg"
        fp = os.path.join(folder, code + ext)
        return [(fp, "video" if video_url else "image")] if self._download_url(url, fp) else []

    def _download_story(self, story, folder, index):
        os.makedirs(folder, exist_ok=True)
        pk = str(_extract(story, 'pk', 'id', default=f"story_{index}"))
        if InstagramClient._mode == "login":
            try:
                got = self._call(self.ig, "download.download_media", pk)
                normalized = self._normalize_download_result(got, folder, f"story_{pk}")
                if normalized:
                    return normalized[0][0]
            except Exception:
                pass
        url = _extract(story, 'video_url', 'display_url', 'thumbnail_url', 'url', default='')
        ext = ".mp4" if _extract(story, 'video_url', default='') else ".jpg"
        fp = os.path.join(folder, f"story_{pk}{ext}")
        return fp if self._download_url(url, fp) else None

    def _download_highlight_item(self, item, folder, index):
        pk = str(_extract(item, 'pk', 'id', default=f"highlight_{index}"))
        if InstagramClient._mode == "login":
            try:
                got = self._call(self.ig, "download.download_media", pk)
                normalized = self._normalize_download_result(got, folder, f"highlight_{pk}")
                if normalized:
                    return normalized[0][0]
            except Exception:
                pass
        url = _extract(item, 'video_url', 'display_url', 'thumbnail_url', 'url', default='')
        ext = ".mp4" if _extract(item, 'video_url', default='') else ".jpg"
        fp = os.path.join(folder, f"highlight_{pk}{ext}")
        return fp if self._download_url(url, fp) else None

    def get_profile_info(self, username):
        if not InstagramClient.refresh_if_needed():
            return None
        username = str(username or "").strip().lstrip("@")
        try:
            if InstagramClient._mode == "anonymous":
                u = self.ig.public.get_profile(username)
            else:
                u = self._call(self.ig, "users.get_full_profile", username)
                if u is None:
                    u = self._call(self.ig, "users.get_by_username", username)
                if u is None and hasattr(self.ig, "public"):
                    u = self.ig.public.get_profile(username)

            uid = str(_extract(u, 'pk', 'id', default=''))
            if uid:
                InstagramClient._user_id_cache[username.lower()] = uid
                InstagramClient._save_user_cache()

            info = {
                'username': str(_extract(u, 'username', default=username)),
                'full_name': str(_extract(u, 'full_name', 'fullname', default='')),
                'bio': str(_extract(u, 'biography', 'bio', default='')),
                'posts_count': int(_extract(u, 'media_count', 'posts_count', default=0) or 0),
                'followers': int(_extract(u, 'follower_count', 'followers', default=0) or 0),
                'following': int(_extract(u, 'following_count', 'following', default=0) or 0),
                'profile_pic': str(_extract(u, 'profile_pic_url', 'profile_pic_url_hd', default='')),
                'is_private': bool(_extract(u, 'is_private', default=False)),
                'is_verified': bool(_extract(u, 'is_verified', default=False)),
                'is_business': bool(_extract(u, 'is_business', 'is_professional_account', default=False)),
                'category': str(_extract(u, 'category', 'business_category_name', default='')),
                'external_url': str(_extract(u, 'external_url', 'external_lynx_url', default='')),
            }
            save_or_update_account(info)

            uname = html_escape(info['username'])
            profile_html = (
                f"<b>👤 @{uname}</b>\n"
                f"<b>{html_escape(info['full_name'] or '—')}</b>\n\n"
                f"📊 <b>الإحصائيات</b>\n"
                f"• المنشورات: <b>{info['posts_count']:,}</b>\n"
                f"• المتابعون: <b>{info['followers']:,}</b>\n"
                f"• يتابع: <b>{info['following']:,}</b>\n"
                f"• الحالة: <b>{'🔒 خاص' if info['is_private'] else '🌐 عام'}</b>\n"
                f"• التوثيق: <b>{'✅ موثق' if info['is_verified'] else '— غير موثق'}</b>\n"
                f"• النوع: <b>{'💼 احترافي/تجاري' if info['is_business'] else '👤 شخصي'}</b>\n"
                f"• الفئة: <b>{html_escape(info['category'] or '—')}</b>\n\n"
                f"<blockquote expandable>{html_escape(info['bio'][:700] or 'لا يوجد')}</blockquote>"
            )
            if info['external_url']:
                profile_html += f'\n🌐 <a href="{html_escape(info["external_url"])}">الموقع الخارجي</a>'
            profile_html += f'\n\n<a href="https://www.instagram.com/{uname}/">🔗 فتح الحساب على Instagram</a>'

            key = TelegramSender._post_keyboard(f"https://www.instagram.com/{uname}/")
            if info['profile_pic']:
                try:
                    r = _http_session.get(info['profile_pic'], timeout=15)
                    if r.status_code == 200:
                        folder = os.path.join(MEDIA_PATH, info['username'])
                        os.makedirs(folder, exist_ok=True)
                        pp = os.path.join(folder, "profile_pic.jpg")
                        with open(pp, "wb") as fp: fp.write(r.content)
                        TelegramSender.send_photo(pp, profile_html, reply_markup=key)
                    else:
                        TelegramSender.send_rich_message(profile_html, reply_markup=key)
                except Exception:
                    TelegramSender.send_rich_message(profile_html, reply_markup=key)
            else:
                TelegramSender.send_rich_message(profile_html, reply_markup=key)
            return info
        except Exception as e:
            print(f"❌ profile: {type(e).__name__}: {str(e)[:250]}")
            TelegramSender.send_message(f"❌ {type(e).__name__}: {html_escape(str(e)[:500])}")
            return None

    def scrape(self, username, max_posts=10, scrape_mode='smart_merge', order='desc',
               fetch_comments=False, max_comments=20, fetch_followers=False, max_followers=100,
               fetch_stories=False, max_stories=50,
               fetch_highlights=False, max_highlights=20,
               fetch_following=False, max_following=100):
        if not InstagramClient.refresh_if_needed():
            TelegramSender.send_message("❌ العميل غير جاهز حالياً")
            return
        username = str(username or "").strip().lstrip("@")
        successful = 0
        targets = []
        try:
            uid = self._get_user_id(username)
            if not uid:
                TelegramSender.send_message(f"❌ لم يتم العثور على @{html_escape(username)}")
                return

            aid = get_account_id(username)
            folder = os.path.join(MEDIA_PATH, username)
            os.makedirs(folder, exist_ok=True)

            posts = self._fetch_posts(username, max_count=max_posts * 2)
            reels = self._fetch_reels(username, max_count=max_posts)
            combined = list(posts or []) + list(reels or [])
            seen, unique = set(), []
            for media in combined:
                key = str(_extract(media, 'pk', 'id', 'code', 'shortcode', default=''))
                if key and key not in seen:
                    seen.add(key); unique.append(media)
            unique.sort(key=lambda x: str(_extract(x,'taken_at','timestamp',default='') or ''), reverse=(order=='desc'))
            targets = unique[:max_posts]

            for i, media in enumerate(targets, 1):
                if InstagramClient.is_throttled():
                    break
                if self._process_media(media, username, folder, i, len(targets),
                                       fetch_comments=fetch_comments, max_comments=max_comments):
                    successful += 1
                InstagramClient.human_delay()

            if fetch_followers:
                followers = self._fetch_followers(username, max_followers)
                for user in followers: save_follower(aid, user)
                if followers:
                    TelegramSender.send_follower_list(username, followers)
                else:
                    TelegramSender.send_message("ℹ️ لا توجد قائمة متابعين متاحة في الوضع الحالي.")

            if fetch_following:
                following = self._fetch_following(username, max_following)
                for user in following: save_following(aid, user)
                if following:
                    lines = [
                        f"<b>{i:03d}.</b> @{html_escape(str(_extract(u,'username',default='')))} — "
                        f"{html_escape(str(_extract(u,'full_name','fullname',default='—')))}"
                        for i,u in enumerate(following,1)
                    ]
                    for start in range(0,len(lines),20):
                        TelegramSender.send_message_html(
                            f"<b>➡️ Following — @{html_escape(username)}</b>\n"
                            f"📦 {start+1}–{min(start+20,len(lines))} من {len(lines)}\n\n" +
                            "\n".join(lines[start:start+20])
                        )

            if fetch_stories:
                stories = self._fetch_stories(username, max_stories)
                sf = os.path.join(STORIES_PATH, username)
                os.makedirs(sf, exist_ok=True)
                story_ok = 0
                for i, story in enumerate(stories,1):
                    fp = self._download_story(story,sf,i)
                    if not fp: continue
                    story_ok += 1
                    save_story(aid, story, fp, str(_extract(story,'video_url','display_url','url',default='')))
                    cap = f"<b>📖 Story #{i:02d}/{len(stories):02d}</b>\n👤 @{html_escape(username)}\n🔐 {InstagramClient._mode_label()}"
                    if fp.lower().endswith(('.mp4','.mov','.webm')):
                        TelegramSender.send_video(fp,cap)
                    else:
                        TelegramSender.send_photo(fp,cap)
                print(f"📖 Stories: {story_ok}/{len(stories)}")

            if fetch_highlights:
                highlights = self._fetch_highlights(username, max_highlights)
                hf = os.path.join(HIGHLIGHTS_PATH, username)
                os.makedirs(hf, exist_ok=True)
                total_hi = 0
                for hi, highlight in enumerate(highlights,1):
                    title = str(_extract(highlight,'title','name',default=f'Highlight {hi}'))
                    items = _extract(highlight,'items','stories',default=[]) or []
                    highlight_id = save_highlight_record(aid, highlight)
                    TelegramSender.send_rich_message(
                        f"<b>📌 Highlight #{hi}</b> — {html_escape(title)}\n"
                        f"👤 @{html_escape(username)}\n📦 {len(items)} عنصر"
                    )
                    for i,item in enumerate(list(items),1):
                        fp = self._download_highlight_item(item,hf,i)
                        if not fp: continue
                        total_hi += 1
                        save_highlight_item_record(highlight_id, item, fp)
                        if fp.lower().endswith(('.mp4','.mov','.webm')):
                            TelegramSender.send_video(fp,f"<b>📌 {html_escape(title)}</b> — {i}")
                        else:
                            TelegramSender.send_photo(fp,f"<b>📌 {html_escape(title)}</b> — {i}")
                print(f"📌 Highlights: {total_hi}")

            csv_p = self._csv_report(username)
            html_p = self._html_report(username, order)
            if csv_p: TelegramSender.send_document(csv_p, f"📊 CSV @{username}")
            if html_p: TelegramSender.send_document(html_p, f"📄 HTML @{username}")

            TelegramSender.send_rich_message(
                f"<b>✅ انتهت المهمة</b>\n👤 @{html_escape(username)}\n"
                f"📦 العناصر: <b>{len(targets)}</b>\n✅ نُزّل: <b>{successful}</b>\n"
                f"🔐 {InstagramClient._mode_label()}"
            )
        except Exception as e:
            InstagramClient._record_error(e)
            print(f"❌ scrape: {type(e).__name__}: {str(e)[:300]}")
            traceback.print_exc()
            TelegramSender.send_message(f"❌ {type(e).__name__}: {html_escape(str(e)[:500])}")

    def _csv_report(self, username):
        try:
            aid = get_account_id(username)
            if not aid: return None
            conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT * FROM posts WHERE account_id=? ORDER BY taken_at DESC", (aid,))
            posts = c.fetchall()
            c.execute("SELECT username, full_name FROM followers WHERE account_id=?", (aid,))
            fl = c.fetchall(); conn.close()
            ts = datetime.now().strftime('%Y%m%d_%H%M')
            rf = os.path.join(REPORTS_PATH, f"{username}_posts_{ts}.csv")
            with open(rf, 'w', newline='', encoding='utf-8-sig') as f:
                w = csv.writer(f)
                w.writerow(['الكود','الرابط','النوع','إعجابات','تعليقات','مشاهدات','تشغيل','التاريخ','النص'])
                for r in posts:
                    w.writerow([r['post_code'], r['post_url'], r['media_type'],
                                r['like_count'], r['comment_count'], r['view_count'], r['play_count'], r['taken_at'],
                                (r['caption'] or '')[:500]])
            return rf
        except Exception as e:
            print(f"⚠️ CSV: {e}"); return None

    def _html_report(self, username, order='desc'):
        try:
            aid = get_account_id(username)
            if not aid: return None
            ai = get_account_info(username) or {}
            posts = get_posts_with_comments(aid, order=order)
            fl = get_followers(aid)
            following = get_following(aid)
            stories = get_stories(aid)
            highlights = get_highlights(aid)

            pic_b64 = ""
            pp = os.path.join(MEDIA_PATH, username, "profile_pic.jpg")
            if os.path.exists(pp):
                pic_b64 = ThumbnailEngine.from_file(pp, 100, 50)

            ts = datetime.now().strftime('%Y-%m-%d %H:%M')
            oar = 'الأحدث أولاً' if order == 'desc' else 'الأقدم أولاً'

            stories_html = ""
            if stories:
                items = ""
                shown = 0
                for s in stories:
                    p = s.get('file_path', '')
                    b64 = ThumbnailEngine.from_file(p, THUMB_MAX_SIZE, THUMB_QUALITY) if p else None
                    if not b64:
                        b64 = ThumbnailEngine.placeholder_svg('📖')
                    items += (f'<div class="thumb-item">'
                              f'<img src="data:image/jpeg;base64,{b64}" loading="lazy">'
                              f'</div>')
                    shown += 1
                if shown:
                    stories_html = f'''<section class="section">
                        <h2 class="section-title">📖 القصص ({shown})</h2>
                        <div class="thumb-grid">{items}</div>
                    </section>'''

            highlights_html = ""
            if highlights:
                rows = "".join(
                    f"<div class='follower-item'><b>📌 {html_escape(str(h.get('title') or 'Highlight'))}</b> "
                    f"— {int(h.get('media_count') or len(h.get('items', [])))} عنصر</div>"
                    for h in highlights
                )
                highlights_html = (
                    f"<section class='section'><h2 class='section-title'>📌 Highlights ({len(highlights)})</h2>"
                    f"<div class='followers-grid'>{rows}</div></section>"
                )

            following_html = ""
            if following:
                rows = "".join(
                    f"<div class='follower-item'>@{html_escape(str(x.get('username') or ''))} — "
                    f"{html_escape(str(x.get('full_name') or '—'))}</div>"
                    for x in following
                )
                following_html = (
                    f"<section class='section'><h2 class='section-title'>➡️ Following ({len(following)})</h2>"
                    f"<div class='followers-grid'>{rows}</div></section>"
                )

            pic_html = (f"<img src='data:image/jpeg;base64,{pic_b64}' class='profile-pic'>"
                        if pic_b64 else
                        "<div class='profile-pic placeholder'>👤</div>")
            fl_html = ("<div class='followers-grid'>" + "".join(
                f"<div class='follower-item'><div>@{x['username']}</div></div>" for x in fl) + "</div>"
            ) if fl else "<p class='empty'>لا يوجد متابعون</p>"

            posts_html = ""
            for p in posts:
                mt = {'image':'📷 صورة','video':'🎥 فيديو','carousel':'🎠 كاروسيل'}.get(p.get('media_type'),'📄')
                file_path = p.get('file_path', '')
                post_thumb = ThumbnailEngine.from_file(file_path, THUMB_MAX_SIZE, THUMB_QUALITY) if file_path else None
                if not post_thumb:
                    post_thumb = ThumbnailEngine.placeholder_svg('📷')

                posts_html += (f"<div class='post-card'>"
                    f"<div class='post-header'><div class='post-info'>"
                    f"<span class='post-type'>{mt}</span></div>"
                    f"<div class='post-stats'><span>❤️ {p['like_count']:,}</span>"
                    f"<span>💬 {p['comment_count']:,}</span></div></div>"
                    f"<div class='post-body'>"
                    f"<img src='data:image/jpeg;base64,{post_thumb}' class='post-thumb'>"
                    f"<div class='post-content'>"
                    f"{'<div class=post-caption>' + str(p.get('caption','')) + '</div>' if p.get('caption') else ''}"
                    f"<a href='{p['post_url']}' target='_blank' class='post-link'>🔗 فتح المنشور</a>"
                    f"</div></div></div>")

            css = """
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,'Segoe UI',Tahoma,Arial,sans-serif;
background:linear-gradient(135deg,#1a1a2e,#0f3460);color:#e0e0e0;padding:16px;min-height:100vh}
.container{max-width:1100px;margin:0 auto}
.header{background:linear-gradient(135deg,#e94560,#c73659);padding:30px;
border-radius:20px;margin-bottom:20px;text-align:center}
.section{background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);
border-radius:16px;padding:20px;margin-bottom:20px}
.section-title{color:#e94560;margin-bottom:16px;padding-bottom:10px;border-bottom:2px solid #e94560}
.profile-pic{width:130px;height:130px;border-radius:50%;border:3px solid #e94560;object-fit:cover}
.post-card{background:rgba(0,0,0,0.2);border-radius:14px;padding:16px;margin-bottom:14px;border-right:3px solid #e94560}
.post-thumb{width:120px;height:120px;object-fit:cover;border-radius:8px}
.followers-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:8px}
.follower-item{background:rgba(255,255,255,0.05);padding:8px;border-radius:8px}
.thumb-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(100px,1fr));gap:8px}
.thumb-item img{width:100%;border-radius:8px}
"""
            html = f"""<!DOCTYPE html>
<html lang="ar" dir="rtl"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>تقرير OSINT — @{username}</title><style>{css}</style></head>
<body><div class="container">
<div class="header"><h1>🕵️ تقرير OSINT — @{username}</h1><div>📅 {ts} — {oar}</div></div>
<section class="section"><h2 class="section-title">👤 معلومات الحساب</h2>
<div style="text-align:center">{pic_html}
<h2>@{ai.get('username','')}</h2>
<div>{ai.get('full_name','')}</div>
<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(80px,1fr));gap:10px;margin-top:12px">
<div>📸 {ai.get('posts_count',0):,} منشور</div>
<div>👥 {ai.get('followers',0):,} متابع</div>
<div>➡️ {ai.get('following',0):,} يتابع</div>
</div>
{'<div style="margin-top:10px;padding:10px;background:rgba(0,0,0,0.2);border-radius:8px">'+str(ai.get('bio',''))+'</div>' if ai.get('bio') else ''}
</div></section>
{stories_html}
{highlights_html}
<section class="section"><h2 class="section-title">👥 المتابعون ({len(fl)})</h2>{fl_html}</section>
{following_html}
<section class="section"><h2 class="section-title">📸 المنشورات ({len(posts)})</h2>{posts_html}</section>
<div style="text-align:center;padding:20px;opacity:0.6">Instagram OSINT Scraper ULTRA v3.1</div>
</div></body></html>"""

            f = os.path.join(REPORTS_PATH, f"{username}_report_{datetime.now().strftime('%Y%m%d_%H%M')}.html")
            with open(f, 'w', encoding='utf-8') as fp: fp.write(html)
            print(f"📄 تقرير HTML: {f} ({os.path.getsize(f)/1024:.1f} KB)")
            return f
        except Exception as e:
            print(f"⚠️ HTML: {e}"); traceback.print_exc(); return None

# ============================================================
# 10. الواجهة
# ============================================================
INSTAGRAM_JOB_LOCK = threading.Lock()
app = Flask(__name__)

HTML_PAGE = """<!DOCTYPE html>
<html lang="ar" dir="rtl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Instagram OSINT ULTRA</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Tahoma,Arial,sans-serif;background:linear-gradient(135deg,#111827,#0f3460);min-height:100vh;padding:14px}
.card{max-width:720px;margin:auto;background:#fff;border-radius:18px;padding:22px;box-shadow:0 18px 55px rgba(0,0,0,.4)}h1{text-align:center;color:#0f3460;margin-bottom:5px}.sub{text-align:center;color:#666;font-size:.85rem;margin-bottom:14px}
.status{background:#eef7ff;border:1px solid #bddcff;border-radius:10px;padding:11px;text-align:center;margin-bottom:14px;line-height:1.5}.st{font-weight:800;color:#0f3460;border-bottom:2px solid #e94560;padding-bottom:7px;margin-bottom:9px}.sec{margin-bottom:17px}
input[type=text],input[type=number],textarea{width:100%;padding:11px;border:2px solid #ddd;border-radius:9px;margin:4px 0;font:inherit}
button{width:100%;padding:12px;border:0;border-radius:10px;color:white;font-weight:800;font-size:1rem;cursor:pointer;margin-top:7px}.primary{background:linear-gradient(135deg,#e94560,#c73659)}.secondary{background:linear-gradient(135deg,#10b981,#059669)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:8px}.choice{background:#f5f7fa;padding:10px;border-radius:9px}.opt{display:flex;align-items:center;gap:8px;background:#f7f8fa;padding:8px;border-radius:9px;margin:4px 0}.opt input{width:18px;height:18px}.small{font-size:.76rem;color:#666}.hint{font-size:.78rem;color:#666;margin:4px 0}
.tabs{display:flex;gap:7px;border-bottom:2px solid #eee;margin-bottom:16px}.tab{background:transparent;color:#666;border-radius:0;margin:0;border-bottom:3px solid transparent}.tab.active{color:#e94560;border-bottom-color:#e94560}.tab-content{display:none}.tab-content.active{display:block}
</style></head><body><div class="card">
<h1>🕵️ Instagram OSINT Scraper ULTRA</h1><div class="sub">instaharvest-v2 • Anonymous ↔ Login</div>
<div class="status" id="status">⏳ قراءة الحالة...</div>

<div class="sec"><div class="st">🔐 وضع الاتصال</div>
<form action="/mode" method="post" onsubmit="const b=this.querySelector('button');b.disabled=true;b.textContent='⏳ جاري التبديل...';">
<div class="grid2">
<label class="choice"><input type="radio" name="mode" value="anonymous" id="m_anon"> 🔓 Anonymous<br><span class="small">بيانات عامة بدون حساب</span></label>
<label class="choice"><input type="radio" name="mode" value="login" id="m_login"> 🔐 Login<br><span class="small">الجلسة + Stories + Followers</span></label>
</div><button class="primary" type="submit">🔄 تطبيق الوضع</button></form>
<div class="small">التبديل لا يحذف جلسة Login المحفوظة في Google Drive.</div></div>

<div class="tabs"><button type="button" class="tab active" onclick="tab('scrape',this)">📊 سكراب</button><button type="button" class="tab" onclick="tab('search',this)">🔍 بحث</button></div>

<div id="scrape-tab" class="tab-content active">
<form action="/start" method="post">
<div class="sec"><div class="st">📌 الحساب</div><input type="text" name="username" placeholder="اسم المستخدم بدون @" required><input type="number" name="max_posts" value="10" min="1" max="500"></div>
<div class="sec"><div class="st">📅 الترتيب</div><div class="grid2"><label class="choice"><input type="radio" name="order" value="desc" checked> الأحدث أولاً</label><label class="choice"><input type="radio" name="order" value="asc"> الأقدم أولاً</label></div></div>
<div class="sec"><div class="st">⚙️ البيانات المتقدمة</div>
<label class="opt"><input type="checkbox" name="fetch_comments" value="1"> 💬 التعليقات</label>
<div class="hint">عدد التعليقات لكل منشور: <input type="number" style="width:85px" name="max_comments" value="20" min="1" max="200"></div>
<label class="opt"><input type="checkbox" name="fetch_followers" value="1"> 👥 أسماء المتابعين <span class="small">(Login)</span></label>
<div class="hint">عدد المتابعين: <input type="number" style="width:85px" name="max_followers" value="100" min="1" max="1000"></div>
<label class="opt"><input type="checkbox" name="fetch_following" value="1"> ➡️ Following <span class="small">(Login)</span></label>
<div class="hint">عدد Following: <input type="number" style="width:85px" name="max_following" value="100" min="1" max="1000"></div>
<label class="opt"><input type="checkbox" name="fetch_stories" value="1"> 📖 Stories</label>
<div class="hint">عدد Stories: <input type="number" style="width:85px" name="max_stories" value="50" min="1" max="200"></div>
<label class="opt"><input type="checkbox" name="fetch_highlights" value="1"> 📌 Highlights</label>
<div class="hint">عدد Highlights: <input type="number" style="width:85px" name="max_highlights" value="20" min="1" max="100"></div>
</div><button class="primary" type="submit">🚀 بدء السكراب</button>
</form></div>

<div id="search-tab" class="tab-content">
<form action="/search" method="post"><div class="sec"><div class="st">🔍 البحث الشامل</div><textarea name="query" placeholder="@username أو #hashtag أو كلمة..." required></textarea><input type="number" name="max_per_type" value="10" min="1" max="50"></div>
<label class="opt"><input type="checkbox" name="hunt_mentions" value="1" checked> 🎯 Mention Hunter</label><label class="opt"><input type="checkbox" name="generate_report" value="1" checked> 📄 تقرير HTML</label>
<button class="secondary" type="submit">🔍 بدء البحث</button></form></div>
</div>
<script>
function tab(n,b){document.querySelectorAll('.tab-content').forEach(x=>x.classList.remove('active'));document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));document.getElementById(n+'-tab').classList.add('active');b.classList.add('active')}
async function status(){try{const r=await fetch('/status');const s=await r.json();document.getElementById('status').innerHTML=s.message;document.getElementById('m_anon').checked=s.mode==='anonymous';document.getElementById('m_login').checked=s.mode==='login'}catch(e){document.getElementById('status').textContent='⚠️ تعذر قراءة حالة العميل'}}
status();setInterval(status,2000);
</script></body></html>"""
def safe_int(value, default=0, minimum=None, maximum=None):
    try:
        v = int(str(value).strip() or default)
    except (ValueError, TypeError):
        v = default
    if minimum is not None and v < minimum: v = minimum
    if maximum is not None and v > maximum: v = maximum
    return v

@app.route('/')
def index():
    return render_template_string(HTML_PAGE)

@app.route('/status')
def status():
    return jsonify(InstagramClient.get_status())

@app.route('/mode', methods=['POST'])
def mode_switch():
    mode = request.form.get('mode','anonymous').strip().lower()
    if mode not in ('anonymous','login'):
        return "❌ وضع غير صحيح", 400
    def task():
        try:
            InstagramClient.switch_mode(mode)
            TelegramSender.send_message(
                f"✅ الوضع الحالي: {'🔓 Anonymous' if mode == 'anonymous' else '🔐 Login'}"
            )
        except Exception as e:
            traceback.print_exc()
            TelegramSender.send_message(f"❌ فشل التبديل: {html_escape(str(e)[:700])}")
    threading.Thread(target=task, daemon=True).start()
    return "✅ جاري تبديل الوضع — ستتحدث الحالة تلقائياً.", 202

@app.route('/start', methods=['POST'])
def start_scrape():
    u=request.form.get('username','').strip()
    mp=safe_int(request.form.get('max_posts'),10,1,500)
    o=request.form.get('order','desc') or 'desc'
    fc=request.form.get('fetch_comments')=='1'; mc=safe_int(request.form.get('max_comments'),20,1,200)
    ff=request.form.get('fetch_followers')=='1'; mf=safe_int(request.form.get('max_followers'),100,1,1000)
    fg=request.form.get('fetch_following')=='1'; mg=safe_int(request.form.get('max_following'),100,1,1000)
    fs=request.form.get('fetch_stories')=='1'; ms=safe_int(request.form.get('max_stories'),50,1,200)
    fh=request.form.get('fetch_highlights')=='1'; mh=safe_int(request.form.get('max_highlights'),20,1,100)
    if not u:return "❌ اسم المستخدم مطلوب",400

    def task():
        if not INSTAGRAM_JOB_LOCK.acquire(blocking=False):
            TelegramSender.send_message("⏳ هناك مهمة أخرى قيد التنفيذ."); return
        try:
            searcher=InstagramSearcher()
            searcher.get_profile_info(u)
            searcher.scrape(u,mp,'smart_merge',o,fc,mc,ff,mf,fs,ms,fh,mh,fg,mg)
        except Exception as e:
            traceback.print_exc(); TelegramSender.send_message(f"❌ {html_escape(str(e)[:800])}")
        finally: INSTAGRAM_JOB_LOCK.release()
    threading.Thread(target=task,daemon=True).start()
    return f"✅ @{html_escape(u)} — جاري السكراب — {InstagramClient._mode_label()}",200

@app.route('/search', methods=['POST'])
def start_search():
    query=request.form.get('query','').strip()
    max_per_type=safe_int(request.form.get('max_per_type'),10,1,50)
    hunt=request.form.get('hunt_mentions')=='1'; report=request.form.get('generate_report')=='1'
    if not query:return "❌ الاستعلام مطلوب",400
    def task():
        if not INSTAGRAM_JOB_LOCK.acquire(blocking=False):
            TelegramSender.send_message("⏳ هناك مهمة أخرى قيد التنفيذ."); return
        try:
            searcher=InstagramSearcher()
            results=searcher.universal_searcher.search(query,max_per_type,hunt_mentions=hunt)
            if report: searcher.universal_searcher.generate_search_report(query,results)
        except Exception as e:
            traceback.print_exc(); TelegramSender.send_message(f"❌ خطأ البحث: {html_escape(str(e)[:800])}")
        finally: INSTAGRAM_JOB_LOCK.release()
    threading.Thread(target=task,daemon=True).start()
    return f"✅ جاري البحث عن: {html_escape(query)}",200

# ============================================================
# 11. التشغيل
# ============================================================
if NGROK_AUTH_TOKEN and not NGROK_AUTH_TOKEN.startswith("ضع_"):
    ngrok.set_auth_token(NGROK_AUTH_TOKEN)
    print("✅ ngrok")

init_db()
InstagramClient.init_paths()
InstagramClient._load_user_cache()
print(f"📦 instaharvest-v2 version: {INSTAHARVEST_VERSION}")
print(f"🔐 وضع التشغيل الابتدائي: {InstagramClient._mode}")
try:
    InstagramClient.get_client()
except Exception as startup_error:
    print(f"⚠️ تهيئة العميل: {type(startup_error).__name__}: {str(startup_error)[:300]}")

print("\n🚀 تشغيل السيرفر...")
url = ngrok.connect(5000).public_url
print("="*60)
print(f"🌐 {url}")
print("="*60)
print("✨ الميزات:")
print("   🔓 Anonymous — profile + posts + reels + public search")
print("   🔐 Login — saved session + Stories + Followers + Following")
print("   📌 Highlights + 💬 Comments + 📄 CSV/HTML Reports")
print("   📲 Telegram + cumulative SQLite DB")
print("="*60)

app.run(host='0.0.0.0', port=5000)
