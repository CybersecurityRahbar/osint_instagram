#============================================================
# 🕵️ Instagram OSINT Scraper ULTRA v2.1 — session migration fixed
# ✅ Cache ذكي + Fallback methods + LRU Cache للصور
# ============================================================

# ============================================================
# 1. الإعدادات
# ============================================================
NGROK_AUTH_TOKEN = " توكن انجروك"
IG_USERNAME = "اسم المستخدم لحسابك"
IG_PASSWORD = "كلمة المرور "

# مسار المصادقة الافتراضي: محاكاة مسار login القديم داخل instagrapi 3.x.
# لا تستخدم Session ID المنسوخ من متصفح شخصي.
LOGIN_STRATEGY = "legacy_first"
PRIVATE_TRANSPORT_FOR_LOGIN = "requests"
TELEGRAM_TOKEN = "توكن تيلجرام"
TELEGRAM_CHAT_ID = "معرف تلجرام "

MAX_TRAVERSE_LIMIT = 10000
THUMB_MAX_SIZE = 150
THUMB_QUALITY = 40
THUMB_OPTIMIZE = True

# ============================================================
# 2. الاستيرادات + توافق الاعتماديات
# ============================================================
# النسخة المستهدفة مثبتة هنا داخل الملف نفسه حتى لا يعتمد تشغيل Colab
# على requirements.txt أو أي ملف خارجي.
REQUIRED_INSTAGRAPI_VERSION = "3.0.20"

import importlib.metadata as importlib_metadata

try:
    INSTAGRAPI_VERSION = importlib_metadata.version("instagrapi")
except importlib_metadata.PackageNotFoundError:
    INSTAGRAPI_VERSION = None

if INSTAGRAPI_VERSION != REQUIRED_INSTAGRAPI_VERSION:
    raise RuntimeError(
        "نسخة instagrapi غير صحيحة. "
        f"المطلوب: {REQUIRED_INSTAGRAPI_VERSION} | "
        f"المثبت حالياً: {INSTAGRAPI_VERSION or 'غير مثبت'}. "
        "ثبّت النسخة المطلوبة في خلية التثبيت المنفصلة ثم أعد تشغيل خلية الأداة."
    )

import os, shutil, json, io, time, random, requests, threading, sqlite3, traceback, csv, re, base64
from functools import lru_cache
from datetime import datetime
from html import escape as html_escape
from flask import Flask, request, render_template_string
from pyngrok import ngrok
import instagrapi
from instagrapi import Client
from instagrapi.exceptions import TwoFactorRequired

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
        """إرسال Telegram Rich Message (Bot API 10.1+ / 10.3)."""
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
                # fallback handled below after retries
                if i < retries:
                    time.sleep(1.5)
            except Exception as e:
                if i < retries:
                    time.sleep(1.5)
                else:
                    print(f"⚠️ Rich Message: {e}")

        # توافق خلفي: لا نفقد الرسالة إذا لم يكن sendRichMessage متاحاً.
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
        """Fallback نصي بسيط عند عدم دعم Rich Messages."""
        text_value = re.sub(r"<blockquote[^>]*>", "", html_text, flags=re.I)
        text_value = re.sub(r"</blockquote>", "", text_value, flags=re.I)
        text_value = re.sub(r"<details[^>]*>", "", text_value, flags=re.I)
        text_value = re.sub(r"</details>", "", text_value, flags=re.I)
        text_value = re.sub(r"<[^>]+>", "", text_value)
        return text_value.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"')

    @staticmethod
    def send_follower_list(username, followers):
        """إرسال قائمة المتابعين كاملة على دفعات مرقمة دون تجاوز حد الرسالة."""
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

                # بعض العملاء/الحسابات قد لا تقبل بعض عناصر Rich formatting
                # داخل caption؛ نعيدها كنص HTML بسيط فقط في هذه الحالة.
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
# 5. 🎨 نظام الصور المصغرة المدمجة مع LRU Cache
# ============================================================
class ThumbnailEngine:
    """محرك الصور المصغرة - مع LRU Cache لمنع الطلبات المتكررة"""
    
    # LRU Cache لحفظ الـ base64 المحملة مسبقاً
    _url_cache = {}
    _MAX_CACHE_SIZE = 500
    
    @classmethod
    def from_url(cls, url, max_size=THUMB_MAX_SIZE, quality=THUMB_QUALITY, cache_key=None):
        """تحميل صورة من URL وإرجاع base64 - مع Cache ذكي"""
        if not url or not PIL_AVAILABLE:
            return None
        
        cache_key = cache_key or str(url)
        
        # 1. التحقق من الـ Cache
        if cache_key in cls._url_cache:
            return cls._url_cache[cache_key]
        
        # 2. التحميل
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
            
            # 3. حفظ في Cache (LRU)
            if len(cls._url_cache) >= cls._MAX_CACHE_SIZE:
                # إزالة أقدم عنصر
                oldest = next(iter(cls._url_cache))
                del cls._url_cache[oldest]
            cls._url_cache[cache_key] = b64
            
            return b64
        except Exception as e:
            print(f"      ⚠️ thumb_from_url: {e}")
            return None
    
    @classmethod
    def from_url_to_file(cls, url, file_path, max_size=THUMB_MAX_SIZE, quality=THUMB_QUALITY):
        """تحميل thumbnail من URL وحفظه كملف .thumb.jpg محلي"""
        if not url or not PIL_AVAILABLE:
            return None
        
        thumb_path = file_path + ".thumb.jpg"
        if os.path.exists(thumb_path):
            return thumb_path
        
        try:
            r = _http_session.get(str(url), timeout=15, stream=True)
            if r.status_code != 200:
                return None
            img = Image.open(io.BytesIO(r.content))
            img.thumbnail((max_size, max_size), Image.LANCZOS)
            if img.mode in ('RGBA', 'P', 'LA'):
                img = img.convert('RGB')
            img.save(thumb_path, format='JPEG', quality=quality, optimize=THUMB_OPTIMIZE,
                    progressive=True)
            return thumb_path
        except Exception as e:
            print(f"      ⚠️ thumb_to_file: {e}")
            return None
    
    @staticmethod
    def from_file(file_path, max_size=THUMB_MAX_SIZE, quality=THUMB_QUALITY):
        """قراءة ملف محلي .thumb.jpg وإرجاع base64"""
        if not file_path or not os.path.exists(file_path) or not PIL_AVAILABLE:
            return None
        
        thumb_path = file_path + ".thumb.jpg"
        # إذا كان ملف thumbnail موجود، نستخدمه مباشرة
        if os.path.exists(thumb_path):
            try:
                with open(thumb_path, "rb") as f:
                    return base64.b64encode(f.read()).decode()
            except: pass
        
        # إذا كان صورة، ننشئ thumb منه
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
            
            # حفظ كملف للاستخدام المستقبلي
            try:
                with open(thumb_path, "wb") as f:
                    f.write(buffer.getvalue())
            except: pass
            
            return b64
        except Exception as e:
            print(f"      ⚠️ thumb_from_file: {e}")
            return None
    
    @staticmethod
    def from_media(media, max_size=THUMB_MAX_SIZE, quality=THUMB_QUALITY):
        """استخراج thumbnail من media object - بدون تحميل فوري، فقط إرجاع URL"""
        # نرجع فقط URL - التحميل سيكون عند الحاجة مع Cache
        if hasattr(media, 'thumbnail_url') and media.thumbnail_url:
            return str(media.thumbnail_url)
        
        if hasattr(media, 'image_versions2') and media.image_versions2:
            candidates = getattr(media.image_versions2, 'candidates', [])
            if candidates:
                smallest = min(candidates, 
                              key=lambda c: getattr(c, 'width', 0) * getattr(c, 'height', 0))
                url = getattr(smallest, 'url', None)
                if url: return str(url)
        
        if hasattr(media, 'display_url') and media.display_url:
            return str(media.display_url)
        
        return None
    
    @staticmethod
    def placeholder_svg(icon='📷'):
        """placeholder SVG مضغوط كـ base64"""
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

def _backup_db_before_migration(reason="schema"):
    """إنشاء نسخة احتياطية تلقائية قبل أي تغيير في قاعدة البيانات."""
    try:
        if not DB_PATH or not os.path.exists(DB_PATH):
            return None
        backup_dir = os.path.join(BASE_PATH, "db_backups")
        os.makedirs(backup_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(backup_dir, f"instagram_scraper_{reason}_{stamp}.sqlite")
        shutil.copy2(DB_PATH, backup_path)
        print(f"🛡️ نسخة احتياطية لقاعدة البيانات: {backup_path}")
        return backup_path
    except Exception as e:
        print(f"⚠️ تعذر إنشاء نسخة قاعدة البيانات الاحتياطية: {e}")
        return None


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # اكتشاف تغييرات schema قبل تنفيذها، ثم أخذ نسخة كاملة واحدة من DB.
    needs_backup = False
    if os.path.exists(DB_PATH):
        try:
            post_existing = _table_columns(c, 'posts') if _table_exists(c, 'posts') else set()
            story_existing = _table_columns(c, 'stories') if _table_exists(c, 'stories') else set()
            follower_global_unique = False
            if _table_exists(c, 'followers'):
                c.execute("PRAGMA index_list(followers)")
                for idx in c.fetchall():
                    if len(idx) >= 3 and idx[2]:
                        c.execute(f"PRAGMA index_info({idx[1]})")
                        if [row[2] for row in c.fetchall()] == ["username"]:
                            follower_global_unique = True
                            break
            needs_backup = (
                ('view_count' not in post_existing)
                or ('play_count' not in post_existing)
                or ('thumbnail_url' not in story_existing)
                or ('story_url' not in story_existing)
                or follower_global_unique
            )
        except Exception:
            needs_backup = True

    if needs_backup:
        _backup_db_before_migration("before_schema_migration")
    
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
    
    conn.commit()
    
    # Migration
    post_cols = _table_columns(c, 'posts')
    if 'view_count' not in post_cols:
        c.execute("ALTER TABLE posts ADD COLUMN view_count INTEGER DEFAULT 0")
        print("🔧 Migration: أضيف view_count لـ posts")
    if 'play_count' not in post_cols:
        c.execute("ALTER TABLE posts ADD COLUMN play_count INTEGER DEFAULT 0")
        print("🔧 Migration: أضيف play_count لـ posts")

    for table in ['posts', 'stories']:
        cols = _table_columns(c, table)
        if 'thumbnail_url' not in cols:
            c.execute(f"ALTER TABLE {table} ADD COLUMN thumbnail_url TEXT")
            print(f"🔧 Migration: أضيف thumbnail_url لـ {table}")
    # Migration: النسخ القديمة كانت تجعل username فريدًا على مستوى كل القاعدة،
    # مما يمنع تخزين نفس المتابع تحت حسابين مختلفين.
    c.execute("PRAGMA index_list(followers)")
    follower_indexes = c.fetchall()
    global_unique_username = False
    for idx in follower_indexes:
        if len(idx) >= 3 and idx[2]:
            idx_name = idx[1]
            c.execute(f"PRAGMA index_info({idx_name})")
            idx_cols = [row[2] for row in c.fetchall()]
            if idx_cols == ["username"]:
                global_unique_username = True
                break
    if global_unique_username:
        print("🔧 Migration: إصلاح uniqueness للمتابعين إلى (account_id, username)")
        c.execute("PRAGMA foreign_keys=OFF")
        c.execute('''CREATE TABLE followers_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER,
            username TEXT, full_name TEXT, first_seen DATETIME,
            UNIQUE(account_id, username),
            FOREIGN KEY(account_id) REFERENCES accounts(id))''')
        c.execute('''INSERT OR IGNORE INTO followers_new
            (id, account_id, username, full_name, first_seen)
            SELECT id, account_id, username, full_name, first_seen FROM followers''')
        c.execute("DROP TABLE followers")
        c.execute("ALTER TABLE followers_new RENAME TO followers")
        c.execute("PRAGMA foreign_keys=ON")
    # إصلاح قاعدة بيانات قديمة لم يكن فيها story_url رغم أن save_story يستخدمه.
    story_cols = _table_columns(c, 'stories')
    if 'story_url' not in story_cols:
        c.execute("ALTER TABLE stories ADD COLUMN story_url TEXT")
        print("🔧 Migration: أضيف story_url لـ stories")
    
    conn.commit(); conn.close()
    print("✅ قاعدة البيانات جاهزة")

def save_or_update_account(info):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute('''INSERT INTO accounts
        (username, full_name, bio, followers, following, posts_count,
         is_private, is_verified, last_scraped) VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(username) DO UPDATE SET
            full_name=excluded.full_name,
            bio=excluded.bio,
            followers=excluded.followers,
            following=excluded.following,
            posts_count=excluded.posts_count,
            is_private=excluded.is_private,
            is_verified=excluded.is_verified,
            last_scraped=excluded.last_scraped''',
        (info['username'], info['full_name'], info['bio'], info['followers'],
         info['following'], info['posts_count'], int(info['is_private']),
         int(info['is_verified']), datetime.now().isoformat()))
    conn.commit(); conn.close()

def save_post_if_not_exists(account_username, media, media_type=None, file_path=None):
    """إنشاء المنشور أو إعادة ID الموجود حتى يمكن استكمال تنزيل سابق فشل."""
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT id FROM accounts WHERE username=?", (account_username,))
    row = c.fetchone()
    if not row:
        conn.close()
        return None
    account_id = row[0]

    c.execute("SELECT id FROM posts WHERE post_code=?", (media.code,))
    existing = c.fetchone()
    if existing:
        post_id = existing[0]
        c.execute(
            "UPDATE posts SET "
            "media_type=COALESCE(?, media_type), "
            "file_path=COALESCE(?, file_path), "
            "downloaded=CASE WHEN ? IS NOT NULL THEN 1 ELSE downloaded END, "
            "like_count=?, comment_count=?, view_count=?, play_count=?, thumbnail_url=COALESCE(?, thumbnail_url) "
            "WHERE id=?",
            (
                media_type, file_path, file_path,
                int(getattr(media, "like_count", 0) or 0),
                int(getattr(media, "comment_count", 0) or 0),
                int(getattr(media, "view_count", 0) or 0),
                int(getattr(media, "play_count", 0) or 0),
                str(getattr(media, "thumbnail_url", "") or "") or None,
                post_id
            )
        )
            conn.commit()
        conn.close()
        return post_id

    thumb_url = str(getattr(media, 'thumbnail_url', '') or '')
    c.execute('''INSERT INTO posts (account_id, post_code, post_url, media_type,
        caption, like_count, comment_count, view_count, play_count, taken_at,
        file_path, downloaded, thumbnail_url)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (account_id, media.code, f"https://www.instagram.com/p/{media.code}/",
         media_type, media.caption_text or "",
         int(getattr(media, "like_count", 0) or 0),
         int(getattr(media, "comment_count", 0) or 0),
         int(getattr(media, "view_count", 0) or 0),
         int(getattr(media, "play_count", 0) or 0),
         media.taken_at.isoformat(), file_path, 1 if file_path else 0, thumb_url))
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
    c.execute('''INSERT OR IGNORE INTO comments
        (post_id, comment_text, commenter_username, commenter_full_name, created_at)
        VALUES (?,?,?,?,?)''',
        (post_id, comment.text, comment.user.username,
         comment.user.full_name, comment.created_at_utc.isoformat()))
    conn.commit(); conn.close()

def save_follower(account_id, follower):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute('''INSERT OR IGNORE INTO followers
        (account_id, username, full_name, first_seen) VALUES (?,?,?,?)''',
        (account_id, follower.username, follower.full_name, datetime.now().isoformat()))
    conn.commit(); conn.close()

def save_story(account_id, story, file_path, story_url=""):
    try:
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        taken = getattr(story, 'taken_at', None)
        thumb_url = str(getattr(story, 'thumbnail_url', '') or '')
        c.execute('''INSERT OR REPLACE INTO stories
            (account_id, story_pk, media_type, taken_at, file_path, story_url, downloaded, thumbnail_url)
            VALUES (?,?,?,?,?,?,1,?)''',
            (account_id, str(getattr(story, 'pk', '')),
             'video' if getattr(story, 'media_type', 1) == 2 else 'image',
             taken.isoformat() if taken else None,
             file_path, story_url, thumb_url))
        conn.commit(); conn.close()
        return True
    except Exception as e:
        print(f"      ⚠️ DB save_story: {e}")
        return False

def save_highlight(account_id, highlight, cover_url="", cover_path=""):
    try:
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        pk = str(getattr(highlight, 'pk', ''))
        c.execute('''INSERT OR REPLACE INTO highlights
            (account_id, highlight_pk, title, media_count, cover_url, cover_path, downloaded)
            VALUES (?,?,?,?,?,?,1)''',
            (account_id, pk, getattr(highlight, 'title', ''),
             getattr(highlight, 'media_count', 0), cover_url, cover_path))
        conn.commit()
        c.execute("SELECT id FROM highlights WHERE highlight_pk=?", (pk,))
        row = c.fetchone()
        hl_id = row[0] if row else None
        conn.close()
        return hl_id
    except Exception as e:
        print(f"      ⚠️ DB save_highlight: {e}")
        return None

def save_highlight_item(highlight_id, item, file_path, video_url=""):
    try:
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        taken = getattr(item, 'taken_at', None)
        c.execute('''INSERT OR REPLACE INTO highlight_items
            (highlight_id, item_pk, media_type, taken_at, file_path, video_url, downloaded)
            VALUES (?,?,?,?,?,?,1)''',
            (highlight_id, str(getattr(item, 'pk', '')),
             'video' if getattr(item, 'media_type', 1) == 2 else 'image',
             taken.isoformat() if taken else None,
             file_path, video_url))
        conn.commit(); conn.close()
        return True
    except Exception as e:
        print(f"      ⚠️ DB save_highlight_item: {e}")
        return False

def get_highlights(account_id):
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM highlights WHERE account_id=? ORDER BY title ASC", (account_id,))
    highlights = [dict(r) for r in c.fetchall()]
    for h in highlights:
        c.execute("SELECT * FROM highlight_items WHERE highlight_id=? ORDER BY taken_at ASC", (h['id'],))
        h['items'] = [dict(r) for r in c.fetchall()]
    conn.close()
    return highlights

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

def get_stories(account_id):
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM stories WHERE account_id=? ORDER BY taken_at DESC", (account_id,))
    s = [dict(r) for r in c.fetchall()]; conn.close(); return s

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
# 7. عميل انستغرام (مع Cache ذكي + حل مشاكل 429)
# ============================================================
class InstagramClient:
    _client = None
    _save_counter = 0
    _last_login_failure_at = 0.0
    _last_login_error = ""
    _last_sessionid_failure_at = 0.0
    _last_throttle_at = 0.0
    _last_challenge_at = 0.0
    _last_challenge_error = ""
    _THROTTLE_COOLDOWN_SECONDS = 300
    _SESSION_VALIDATION_INTERVAL_SECONDS = 600
    _last_session_validation_at = 0.0
    _LOGIN_COOLDOWN_SECONDS = 900
    _SESSIONID_COOLDOWN_SECONDS = 900
    # جلسة مستقلة مملوكة للأداة؛ لا تستخدم ملف Session ID السابق من المتصفح.
    _SESSION_FILE = ("/content/drive/MyDrive/ig_tool_session_legacy_v3.json" if IN_COLAB
                     else "ig_tool_session_legacy_v3.json")
    
    # 🎯 Cache ذكي لـ user_id - يمنع 429
    _user_id_cache = {}
    _USER_CACHE_FILE = ("/content/drive/MyDrive/ig_user_cache.json" if IN_COLAB
                        else "ig_user_cache.json")
    
    @classmethod
    def _load_user_cache(cls):
        """تحميل cache المستخدمين من الملف"""
        if os.path.exists(cls._USER_CACHE_FILE):
            try:
                with open(cls._USER_CACHE_FILE, "r", encoding="utf-8") as f:
                    cls._user_id_cache = json.load(f)
                    print(f"✅ تم تحميل {len(cls._user_id_cache)} مستخدم من الـ Cache")
            except: pass
    
    @classmethod
    def _save_user_cache(cls):
        """حفظ cache المستخدمين"""
        try:
            with open(cls._USER_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(cls._user_id_cache, f, ensure_ascii=False)
        except: pass
    
    @classmethod
    def get_user_id_cached(cls, client, username):
        """حل اسم مستخدم مباشر عبر Private Mobile API فقط."""
        username = str(username or "").lower().strip().lstrip("@")
        if not username:
            return None

        if username in cls._user_id_cache:
            return cls._user_id_cache[username]

        try:
            if hasattr(client, "user_info_by_username_v1"):
                user = client.user_info_by_username_v1(username)
            else:
                user = client.user_info_by_username(username)
            uid = str(getattr(user, "pk", "") or "")
            actual_username = str(getattr(user, "username", username) or username).lower()
            if uid:
                cls._user_id_cache[username] = uid
                cls._user_id_cache[actual_username] = uid
                cls._save_user_cache()
                return uid
        except Exception as e:
            if cls._is_rate_limit_error(e):
                cls._mark_throttle(e)
            print(f"      ⚠️ private username lookup: {type(e).__name__}: {str(e)[:120]}")

        # Fallback داخل private API فقط.
        try:
            uid = str(client.user_id_from_username(username))
            if uid:
                cls._user_id_cache[username] = uid
                cls._save_user_cache()
                return uid
        except Exception as e:
            if cls._is_rate_limit_error(e):
                cls._mark_throttle(e)
            print(f"      ⚠️ user_id_from_username: {type(e).__name__}: {str(e)[:100]}")
        return None

    @staticmethod
    def _get_settings(client):
        return client.get_settings() if hasattr(client, "get_settings") else client.dump_settings()

    @staticmethod
    def _set_settings(client, data):
        if hasattr(client, "set_settings"):
            client.set_settings(data)
        else:
            tmp = "/tmp/_ig_session_tmp.json"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            client.load_settings(tmp)

    @classmethod
    def _login_error_details(cls, exc):
        """إرجاع تفاصيل آمنة للخطأ بدون تسريب كلمة المرور أو الأسرار."""
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        message = str(exc).strip() or type(exc).__name__
        return status, message[:500]

    @classmethod
    def _mark_throttle(cls, exc=None):
        cls._last_throttle_at = time.time()
        if exc is not None:
            status, message = cls._login_error_details(exc)
            if status or message:
                cls._last_login_error = f"HTTP {status}: {message}" if status else message
        # 429 ليس logout؛ نحفظ آخر حالة للجلسة قبل إيقاف المهمة.
        if cls._client is not None:
            try:
                cls._save_session(force=True)
            except Exception:
                pass

    @classmethod
    def is_throttled(cls):
        return bool(cls._last_throttle_at and
                    (time.time() - cls._last_throttle_at) < cls._THROTTLE_COOLDOWN_SECONDS)

    @classmethod
    def throttle_remaining(cls):
        if not cls._last_throttle_at:
            return 0
        return max(0, int(cls._THROTTLE_COOLDOWN_SECONDS -
                          (time.time() - cls._last_throttle_at)))

    @staticmethod
    def _is_rate_limit_error(exc):
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        text_value = str(exc).lower()
        return status == 429 or "429" in text_value or "too many requests" in text_value

    @classmethod
    def _mark_challenge(cls, exc=None):
        cls._last_challenge_at = time.time()
        if exc is not None:
            status, message = cls._login_error_details(exc)
            cls._last_challenge_error = f"HTTP {status}: {message}" if status else message
        if cls._client is not None:
            try:
                cls._save_session(force=True)
            except Exception:
                pass

    @classmethod
    def is_challenged(cls):
        return bool(cls._last_challenge_at)

    @classmethod
    def challenge_reason(cls):
        return cls._last_challenge_error or "Instagram طلب تحقق أمني يدوي."

    @classmethod
    def _configure_client(cls, client, legacy=False):
        """توحيد إعداد عميل Instagram مع إمكانية تشغيل المسار القديم."""
        if hasattr(client, "set_retry_config"):
            kwargs = {
                "public_request_retries_count": 1,
                "public_request_retries_timeout": 2,
                "session_retry_total": 1,
                "session_retry_backoff_factor": 1,
                "session_retry_statuses": [429, 500, 502, 503, 504],
            }
            if legacy or LOGIN_STRATEGY == "legacy_first":
                kwargs["private_transport"] = PRIVATE_TRANSPORT_FOR_LOGIN
            else:
                kwargs["private_transport"] = "curl"
            client.set_retry_config(**kwargs)
        return client

    @classmethod
    def _normalize_sessionid(cls, value):
        value = str(value or "").strip()
        return value.strip('"').strip("'").strip()

    @classmethod
    def _get_runtime_sessionid(cls):
        """Session ID من متصفح خارجي معطل عمداً لأسباب أمنية."""
        return ""


    @classmethod
    def _activate_sessionid(cls, client, sessionid, source="Session ID"):
        print("🛑 Session ID من المتصفح معطل في هذه الأداة لأسباب أمنية.")
        return False


    @classmethod
    def _prompt_for_sessionid(cls):
        return ""


    @classmethod
    def get_client(cls):
        if cls._client is not None:
            return cls._client

        # منع إعادة ضرب endpoint تسجيل الدخول أثناء فترة 429/الرفض المؤقت.
        # يسمح فقط بمسار Session ID مختلف إذا كان المستخدم قد وفره ولم
        # تتم تجربته وفشله خلال نفس فترة التهدئة.
        if cls._last_login_failure_at:
            elapsed = time.time() - cls._last_login_failure_at
            if elapsed < cls._LOGIN_COOLDOWN_SECONDS:
                runtime_sessionid = cls._get_runtime_sessionid()
                session_elapsed = time.time() - cls._last_sessionid_failure_at if cls._last_sessionid_failure_at else float("inf")
                if runtime_sessionid and session_elapsed >= cls._SESSIONID_COOLDOWN_SECONDS:
                    try:
                        session_client = cls._configure_client(Client(), legacy=(LOGIN_STRATEGY == "legacy_first"))
                        if cls._activate_sessionid(session_client, runtime_sessionid, "Session ID أثناء cooldown"):
                            return cls._client
                    except Exception:
                        pass

                remaining = int(cls._LOGIN_COOLDOWN_SECONDS - elapsed)
                print(f"⏳ آخر محاولة دخول فشلت. لن نكرر تسجيل الدخول الآن؛ المتبقي تقريباً {remaining}s")
                if cls._last_login_error:
                    print(f"   السبب السابق: {cls._last_login_error}")
                return None

        cls._load_user_cache()

        # 1) الجلسة المحفوظة هي المسار الأول والأكثر أماناً للـrate limit
        client = None
        client = cls._configure_client(Client(), legacy=(LOGIN_STRATEGY == "legacy_first"))
        session_loaded = False

        if os.path.exists(cls._SESSION_FILE):
            try:
                client.load_settings(cls._SESSION_FILE, override_app_version=True)
                cls._configure_client(client, legacy=(LOGIN_STRATEGY == "legacy_first"))
                session_loaded = True
                print("♻️ تم تحميل الجلسة المحفوظة مع ترقية ملف app profile")

                # تحقق من الجلسة أولاً. إذا كانت صالحة، لا نرسل login()
                # ولا كلمة المرور مرة أخرى.
                try:
                    if hasattr(client, "account_info"):
                        client.account_info()
                        cls._client = client
                        cls._last_login_failure_at = 0.0
                        cls._last_login_error = ""
                        print("✅ الجلسة المحفوظة صالحة — لا حاجة لإعادة تسجيل الدخول")
                        return cls._client
                except Exception as session_check_error:
                    status, message = cls._login_error_details(session_check_error)
                    lower = message.lower()
                    if status == 429 or "429" in lower:
                        cls._last_login_failure_at = time.time()
                        cls._last_login_error = f"HTTP {status}: {message}" if status else message
                        print("🛑 الجلسة موجودة لكن Instagram أعاد 429 أثناء التحقق منها.")
                        print("   لن ننتقل إلى password/CAA تلقائياً.")
                        return None
                    if "login_required" not in lower and "challenge_required" not in lower:
                        print(f"⚠️ تعذر التحقق من الجلسة المحفوظة: {type(session_check_error).__name__}: {str(session_check_error)[:160]}")
            except Exception as e:
                print(f"⚠️ تعذر تحميل الجلسة المحفوظة: {type(e).__name__}: {str(e)[:160]}")
                client = cls._configure_client(Client(), legacy=(LOGIN_STRATEGY == "legacy_first"))

        if not session_loaded:
            print("ℹ️ لا توجد جلسة محفوظة — سيتم تنفيذ محاولة دخول واحدة فقط بكلمة المرور")

        # 3) لا نصل إلى login() إلا عندما لا توجد جلسة قابلة للاستخدام
        #    أو تحتاج الجلسة إلى إعادة مصادقة.
        try:
            cls._configure_client(client, legacy=(LOGIN_STRATEGY == "legacy_first"))
            if LOGIN_STRATEGY == "legacy_first" and hasattr(client, "login_legacy"):
                try:
                    ok = client.login_legacy(IG_USERNAME, IG_PASSWORD)
                except TwoFactorRequired:
                    from getpass import getpass
                    code = getpass("🔐 Instagram verification code (2FA/backup code): ").strip()
                    if not code:
                        raise
                    ok = client.login_legacy(
                        IG_USERNAME, IG_PASSWORD, verification_code=code
                    )
            else:
                ok = client.login(IG_USERNAME, IG_PASSWORD)
            if ok is False:
                raise RuntimeError("Instagram رفض تسجيل الدخول بدون إرجاع جلسة صالحة")

            cls._client = client
            cls._last_login_failure_at = 0.0
            cls._last_login_error = ""
            cls._save_session(force=True)
            print(f"✅ جلسة Instagram نشطة — instagrapi {getattr(instagrapi, '__version__', '?')}")
            TelegramSender.send_message("✅ *جلسة أدوات نشطة*")
            return cls._client

        except Exception as e:
            status, message = cls._login_error_details(e)
            detail = f"HTTP {status}: {message}" if status else message
            cls._last_login_failure_at = time.time()
            cls._last_login_error = detail

            if status == 429 or "429" in message:
                cls._mark_throttle(e)
                print("🛑 Instagram أعاد 429 من CAA أثناء تسجيل الدخول.")
                print("   لن نكرر password/CAA ولن نطلب Session ID من المتصفح.")
                print("   هذه حالة تقييد مؤقت وليست فشلاً في كلمة المرور بالضرورة.")
            elif "challenge_required" in message.lower() or "manual verification" in message.lower():
                cls._mark_challenge(e)
                print("🛑 Instagram طلب تحقق أمني يدوي.")
                print("   لن نحاول تجاوز التحقق أو نسخ جلسة المتصفح.")
                print("   أكمل التحقق من تطبيق/موقع Instagram الرسمي أولاً.")
            else:
                print(f"❌ فشل تسجيل الدخول: {detail}")

            cls._client = None
            return None

    @classmethod
    def _save_session(cls, force=False):
        cls._save_counter += 1
        if not force and cls._save_counter < 5: return
        if cls._client:
            try:
                with open(cls._SESSION_FILE, "w", encoding="utf-8") as f:
                    json.dump(cls._get_settings(cls._client), f, ensure_ascii=False, indent=2)
                cls._save_counter = 0
            except Exception as e:
                print(f"⚠️ حفظ: {e}")

    @classmethod
    def refresh_if_needed(cls):
        if cls._client is None:
            return False

        # إذا كان Instagram قد فرض 429، لا نرسل أي طلب جديد حتى تنتهي
        # فترة التهدئة. الجلسة نفسها تبقى محفوظة وفعالة.
        if cls.is_throttled():
            print(f"🛑 Instagram في فترة تهدئة بسبب 429 — المتبقي تقريباً {cls.throttle_remaining()}s")
            return False

        # لا نرسل account_info() مع كل مهمة؛ نتحقق دورياً فقط.
        if cls._last_session_validation_at:
            age = time.time() - cls._last_session_validation_at
            if age < cls._SESSION_VALIDATION_INTERVAL_SECONDS:
                return True

        try:
            if not hasattr(cls._client, "account_info"):
                raise AttributeError("Client 3.x لا يحتوي account_info()")
            cls._client.account_info()
            cls._last_session_validation_at = time.time()
            return True
        except Exception as e:
            lower = str(e).lower()
            if cls._is_rate_limit_error(e):
                cls._mark_throttle(e)
                print("🛑 Instagram أعاد 429 أثناء التحقق من الجلسة.")
                print("   الجلسة لم تُلغَ ولم نعد تسجيل الدخول؛ تم حفظ حالة التقييد فقط.")
                return False

            if "challenge_required" in lower or "manual verification" in lower:
                cls._mark_challenge(e)
                print("🛑 Instagram طلب تحقق أمني أثناء التحقق من الجلسة.")
                print("   لا إعادة login ولا نسخ Session ID ولا استمرار في الطلبات.")
                return False

            if "login_required" in lower:
                try:
                    if LOGIN_STRATEGY == "legacy_first" and hasattr(cls._client, "login_legacy"):
                        ok = cls._client.login_legacy(IG_USERNAME, IG_PASSWORD, relogin=True)
                    else:
                        ok = cls._client.login(IG_USERNAME, IG_PASSWORD, relogin=True)
                    if ok is False:
                        return False
                    cls._last_session_validation_at = time.time()
                    cls._save_session(force=True)
                    return True
                except Exception as relogin_exc:
                    status2, message2 = cls._login_error_details(relogin_exc)
                    if cls._is_rate_limit_error(relogin_exc):
                        cls._mark_throttle(relogin_exc)
                    cls._last_login_failure_at = time.time()
                    cls._last_login_error = f"HTTP {status2}: {message2}" if status2 else message2
                    return False

            print(f"⚠️ فشل التحقق من جلسة Instagram: {type(e).__name__}: {str(e)[:180]}")
            return False

    @classmethod
    def human_delay(cls, a=1.5, b=4):
        time.sleep(random.uniform(a, b))

# ============================================================
# 8. 🔍 محرك البحث الشامل (مع إصلاح TypeError)
# ============================================================
class UniversalSearcher:
    def __init__(self, client):
        self.client = client
    
    def analyze_query(self, query):
        query = query.strip()
        analysis = {
            'original': query, 'clean': query,
            'type': 'general', 'targets': [], 'keywords': []
        }
        
        if query.startswith('#'):
            analysis['type'] = 'hashtag'
            analysis['clean'] = query[1:].strip()
            analysis['targets'].append(('hashtag', analysis['clean']))
            return analysis
        
        if query.startswith('@'):
            analysis['type'] = 'user'
            analysis['clean'] = query[1:].strip()
            analysis['targets'].append(('user', analysis['clean']))
            return analysis
        
        if 'instagram.com' in query:
            m = re.search(r'instagram\.com/([A-Za-z0-9_.]+)', query)
            if m:
                analysis['type'] = 'user'
                analysis['clean'] = m.group(1)
                analysis['targets'].append(('user', analysis['clean']))
                return analysis
        
        words = query.split()
        analysis['keywords'] = [w for w in words if len(w) > 2]
        analysis['type'] = 'general'
        analysis['targets'] = [
            ('users', query),
            ('hashtags', query),
            ('reels', query),
            ('music', query),
        ]
        
        if len(words) == 1:
            analysis['targets'].append(('hashtag_medias', words[0]))
        elif len(words) <= 3:
            analysis['targets'].append(('hashtag_medias', words[0]))
        
        return analysis
    
    def _safe_search_users(self, query, count):
        """بحث آمن عن المستخدمين"""
        try:
            result = self.client.search_users(query)
            return list(result)[:count] if result else []
        except Exception as e:
            print(f"      ⚠️ search_users: {type(e).__name__}: {str(e)[:60]}")
            return []
    
    def _safe_search_hashtags(self, query, count):
        """بحث آمن عن الهاشتاجات - مع fallback"""
        # المحاولة 1: search_hashtags
        try:
            if hasattr(self.client, 'search_hashtags'):
                result = self.client.search_hashtags(query)
                return list(result)[:count] if result else []
        except Exception as e:
            print(f"      ⚠️ search_hashtags (v1): {type(e).__name__}")
        
        # المحاولة 2: search_hashtags_v1
        try:
            if hasattr(self.client, 'search_hashtags_v1'):
                result = self.client.search_hashtags_v1(query)
                return list(result)[:count] if result else []
        except Exception as e:
            print(f"      ⚠️ search_hashtags_v1: {type(e).__name__}")
        
        # المحاولة 3: hashtag_info مباشر
        try:
            clean = query.lstrip('#').strip()
            info = self.client.hashtag_info(clean)
            return [info] if info else []
        except Exception as e:
            print(f"      ⚠️ hashtag_info: {type(e).__name__}")
        
        return []
    
    def _safe_hashtag_medias(self, tag, count):
        """جلب منشورات هاشتاج بأمان"""
        # المحاولة 1: hashtag_medias_top
        try:
            medias = self.client.hashtag_medias_top(tag, count)
            return list(medias) if medias else []
        except Exception as e:
            print(f"      ⚠️ hashtag_medias_top: {type(e).__name__}")
        
        # المحاولة 2: hashtag_medias_recent
        try:
            medias = self.client.hashtag_medias_recent(tag, count)
            return list(medias) if medias else []
        except Exception as e:
            print(f"      ⚠️ hashtag_medias_recent: {type(e).__name__}")
        
        return []
    
    def _safe_search_reels(self, query, count):
        """بحث آمن عن الريلز"""
        try:
            if hasattr(self.client, 'search_reels'):
                result = self.client.search_reels(query)
                return list(result)[:count] if result else []
        except Exception as e:
            print(f"      ⚠️ search_reels: {type(e).__name__}")
        return []
    
    def _safe_search_music(self, query, count):
        """بحث آمن عن الأغاني"""
        try:
            if hasattr(self.client, 'search_music'):
                result = self.client.search_music(query)
                return list(result)[:count] if result else []
        except Exception as e:
            print(f"      ⚠️ search_music: {type(e).__name__}")
        return []
    
    def search(self, query, max_results_per_type=10, hunt_mentions=True):
        analysis = self.analyze_query(query)
        print(f"🔍 بحث شامل: '{query}' — النوع: {analysis['type']}")
        
        results = {
            'analysis': analysis,
            'users': [], 'hashtags': [], 'posts': [],
            'reels': [], 'music': [], 'mentions': [],
            'stats': {'total': 0}
        }
        
        # 1. المستخدمين
        if analysis['type'] in ('general', 'user'):
            search_term = analysis['clean'] if analysis['type'] == 'user' else query
            results['users'] = self._safe_search_users(search_term, max_results_per_type)
            print(f"   ✅ users: {len(results['users'])}")
            InstagramClient.human_delay(1, 2)
        
        # 2. الهاشتاجات
        if analysis['type'] in ('general', 'hashtag'):
            search_term = analysis['clean'] if analysis['type'] == 'hashtag' else query
            results['hashtags'] = self._safe_search_hashtags(search_term, max_results_per_type)
            print(f"   ✅ hashtags: {len(results['hashtags'])}")
            InstagramClient.human_delay(1, 2)
        
        # 3. منشورات الهاشتاج
        if analysis['type'] == 'hashtag' or 'hashtag_medias' in [t[0] for t in analysis['targets']]:
            tag = None
            if analysis['type'] == 'hashtag':
                tag = analysis['clean']
            else:
                if results['hashtags']:
                    tag = getattr(results['hashtags'][0], 'name', None)
                elif analysis['keywords']:
                    tag = analysis['keywords'][0]
                else:
                    tag = analysis['clean']
            
            if tag:
                results['posts'] = self._safe_hashtag_medias(tag, max_results_per_type)
                print(f"   ✅ posts (#{tag}): {len(results['posts'])}")
                InstagramClient.human_delay(1, 2)
        
        # 4. الريلز
        if analysis['type'] == 'general':
            results['reels'] = self._safe_search_reels(query, max_results_per_type)
            print(f"   ✅ reels: {len(results['reels'])}")
            InstagramClient.human_delay(1, 2)
        
        # 5. الأغاني
        if analysis['type'] == 'general':
            results['music'] = self._safe_search_music(query, max_results_per_type)
            print(f"   ✅ music: {len(results['music'])}")
            InstagramClient.human_delay(1, 2)
        
        # 6. Mention Hunter — optional because it adds hashtag + comment requests
        if hunt_mentions:
            results['mentions'] = self._hunt_mentions(query, 5, 30)
            print(f"   🎯 mentions: {len(results['mentions'])}")
        else:
            results['mentions'] = []
            print("   ⏭️ Mention Hunter معطّل")
        
        results['stats']['total'] = (len(results['users']) + len(results['hashtags']) + 
                                     len(results['posts']) + len(results['reels']) + 
                                     len(results['music']) + len(results['mentions']))
        
        self._save_search_results(query, results)
        self._send_summary(query, results)
        
        return results
    
    def _hunt_mentions(self, query, max_search_posts=5, max_comments_per_post=30):
        mentions = []
        query_lower = query.lower().strip()
        clean_query = query_lower.replace('#', '').replace('@', '').strip()
        
        if not clean_query:
            return mentions
        
        print(f"   🎯 بدء Mention Hunter: '{clean_query}'")
        
        # نستخدم دالة آمنة
        hashtags = self._safe_search_hashtags(clean_query, 2)
        
        if not hashtags:
            return mentions
        
        top_tag = getattr(hashtags[0], 'name', None)
        if not top_tag:
            return mentions
        
        print(f"      📍 فحص منشورات #{top_tag}...")
        medias = self._safe_hashtag_medias(top_tag, max_search_posts)
        
        for media in medias[:max_search_posts]:
            # فحص الكابشن
            caption = getattr(media, 'caption_text', '') or ''
            if clean_query in caption.lower() or f"@{clean_query}" in caption.lower():
                mentions.append({
                    'type': 'caption',
                    'source_pk': str(getattr(media, 'pk', '')),
                    'source_url': f"https://instagram.com/p/{getattr(media, 'code', '')}/",
                    'context': caption[:200],
                    'media_type': getattr(media, 'media_type', 1),
                    'user': getattr(media.user, 'username', '') if hasattr(media, 'user') else ''
                })
                save_mention(clean_query, 'caption', str(getattr(media, 'pk', '')),
                            f"https://instagram.com/p/{getattr(media, 'code', '')}/",
                            caption[:500])
            
            # فحص User Tags
            try:
                usertags = getattr(media, 'usertags', []) or []
                for tag in usertags:
                    tag_user = getattr(tag, 'user', None)
                    if tag_user:
                        tag_username = getattr(tag_user, 'username', '').lower()
                        tag_fullname = getattr(tag_user, 'full_name', '').lower()
                        if clean_query in tag_username or clean_query in tag_fullname:
                            mentions.append({
                                'type': 'usertag',
                                'source_pk': str(getattr(media, 'pk', '')),
                                'source_url': f"https://instagram.com/p/{getattr(media, 'code', '')}/",
                                'context': f"تم الإشارة إلى @{tag_username} ({tag_fullname})",
                                'mentioned_username': tag_username,
                                'mentioned_full_name': tag_fullname
                            })
            except: pass
            
            # فحص التعليقات
            try:
                comments = self.client.media_comments(media.pk, amount=max_comments_per_post)
                for comment in comments:
                    comment_text = getattr(comment, 'text', '') or ''
                    if (clean_query in comment_text.lower() or 
                        f"@{clean_query}" in comment_text.lower()):
                        mentions.append({
                            'type': 'comment',
                            'source_pk': str(getattr(media, 'pk', '')),
                            'source_url': f"https://instagram.com/p/{getattr(media, 'code', '')}/",
                            'context': comment_text[:200],
                            'commenter': getattr(comment.user, 'username', ''),
                            'commenter_full_name': getattr(comment.user, 'full_name', '')
                        })
                        save_mention(clean_query, 'comment', str(getattr(media, 'pk', '')),
                                    f"https://instagram.com/p/{getattr(media, 'code', '')}/",
                                    comment_text[:500],
                                    getattr(comment.user, 'username', ''),
                                    getattr(comment.user, 'full_name', ''))
            except Exception as e:
                print(f"      ⚠️ comments: {type(e).__name__}")
            
            InstagramClient.human_delay(0.5, 1.2)
        
        return mentions
    
    def _save_search_results(self, query, results):
        try:
            conn = sqlite3.connect(DB_PATH); c = conn.cursor()
            for rtype in ['users', 'hashtags', 'posts', 'reels', 'music']:
                for item in results[rtype]:
                    username = getattr(item, 'username', '') or ''
                    full_name = getattr(item, 'full_name', '') or ''
                    pk = str(getattr(item, 'pk', ''))
                    extra = json.dumps({
                        'type': rtype,
                        'bio': getattr(item, 'biography', '')[:200] if hasattr(item, 'biography') else '',
                        'followers': getattr(item, 'follower_count', 0) if hasattr(item, 'follower_count') else 0,
                    }, ensure_ascii=False)
                    c.execute('''INSERT INTO search_results 
                        (query, result_type, username, full_name, pk, extra_data, created_at)
                        VALUES (?,?,?,?,?,?,?)''',
                        (query, rtype, username, full_name, pk, extra, datetime.now().isoformat()))
            conn.commit(); conn.close()
        except Exception as e:
            print(f"⚠️ حفظ نتائج البحث: {e}")
    
    def _send_summary(self, query, results):
        summary_lines = [f"🔍 *نتائج البحث الشامل*\n📝 الاستعلام: `{query}`\n"]
        
        if results['users']:
            summary_lines.append(f"👤 مستخدمين: {len(results['users'])}")
            for u in results['users'][:3]:
                summary_lines.append(f"   • @{u.username} ({u.full_name})")
        
        if results['hashtags']:
            summary_lines.append(f"#️⃣ هاشتاجات: {len(results['hashtags'])}")
            for h in results['hashtags'][:3]:
                summary_lines.append(f"   • #{getattr(h, 'name', '?')} ({getattr(h, 'media_count', 0):,} منشور)")
        
        if results['posts']:
            summary_lines.append(f"📸 منشورات: {len(results['posts'])}")
        
        if results['reels']:
            summary_lines.append(f"🎬 ريلز: {len(results['reels'])}")
        
        if results['music']:
            summary_lines.append(f"🎵 أغاني: {len(results['music'])}")
            for m in results['music'][:2]:
                summary_lines.append(f"   • {getattr(m, 'title', '')} - {getattr(m, 'display_artist', '')}")
        
        if results['mentions']:
            summary_lines.append(f"🎯 إشارات: {len(results['mentions'])}")
            for m in results['mentions'][:3]:
                summary_lines.append(f"   • [{m['type']}] {m['context'][:80]}")
        
        TelegramSender.send_message("\n".join(summary_lines))
    
    def generate_search_report(self, query, results):
        """توليد تقرير HTML مع استخدام Cache للصور"""
        ts = datetime.now().strftime('%Y-%m-%d %H:%M')
        sections_html = []
        
        # ============ المستخدمين ============
        if results['users']:
            items_html = ""
            for u in results['users']:
                pic_url = str(getattr(u, 'profile_pic_url', '') or '')
                # استخدام Cache - نفس URL لن يُحمّل مرتين
                pic_b64 = ThumbnailEngine.from_url(pic_url, 80, 50, cache_key=f"u_{u.username}") if pic_url else None
                pic_tag = (f"<img src='data:image/jpeg;base64,{pic_b64}' class='user-pic'>" 
                          if pic_b64 else "<div class='user-pic placeholder'>👤</div>")
                
                followers = getattr(u, 'follower_count', 0)
                bio = getattr(u, 'biography', '')[:150]
                is_private = getattr(u, 'is_private', False)
                is_verified = getattr(u, 'is_verified', False)
                
                badges = ""
                if is_verified: badges += '<span class="badge verified">✅</span>'
                if is_private: badges += '<span class="badge private">🔒</span>'
                
                items_html += f'''<div class="user-card">
                    {pic_tag}
                    <div class="user-info">
                        <div class="user-username">@{u.username} {badges}</div>
                        <div class="user-name">{u.full_name}</div>
                        <div class="user-stats">👥 {followers:,} متابع</div>
                        <div class="user-bio">{bio}</div>
                        <a href="https://instagram.com/{u.username}" target="_blank" class="user-link">🔗 فتح الملف</a>
                    </div>
                </div>'''
            sections_html.append(f'''<section class="section">
                <h2 class="section-title">👤 المستخدمون ({len(results['users'])})</h2>
                <div class="users-grid">{items_html}</div>
            </section>''')
        
        # ============ الهاشتاجات ============
        if results['hashtags']:
            items_html = ""
            for h in results['hashtags']:
                media_count = getattr(h, 'media_count', 0)
                cover_url = str(getattr(h, 'profile_pic_url', '') or '')
                cover_b64 = ThumbnailEngine.from_url(cover_url, 100, 50, 
                                                    cache_key=f"h_{getattr(h, 'name', '')}") if cover_url else None
                cover_tag = (f"<img src='data:image/jpeg;base64,{cover_b64}' class='hashtag-cover'>"
                            if cover_b64 else "<div class='hashtag-cover placeholder'>#️⃣</div>")
                items_html += f'''<div class="hashtag-card">
                    {cover_tag}
                    <div class="hashtag-name">#{getattr(h, 'name', '?')}</div>
                    <div class="hashtag-stats">📸 {media_count:,} منشور</div>
                    <a href="https://instagram.com/explore/tags/{getattr(h, 'name', '')}" target="_blank" class="hashtag-link">🔗 استكشاف</a>
                </div>'''
            sections_html.append(f'''<section class="section">
                <h2 class="section-title">#️⃣ الهاشتاجات ({len(results['hashtags'])})</h2>
                <div class="hashtags-grid">{items_html}</div>
            </section>''')
        
        # ============ المنشورات (مع Cache!) ============
        if results['posts']:
            items_html = ""
            for p in results['posts']:
                # 🎯 استخدام Cache - كل thumbnail يتم تحميله مرة واحدة فقط
                thumb_url = ThumbnailEngine.from_media(p)
                thumb_b64 = None
                if thumb_url:
                    thumb_b64 = ThumbnailEngine.from_url(
                        thumb_url, THUMB_MAX_SIZE, THUMB_QUALITY,
                        cache_key=f"p_{getattr(p, 'code', '')}")
                if not thumb_b64:
                    thumb_b64 = ThumbnailEngine.placeholder_svg('📷')
                
                mt = getattr(p, 'media_type', 1)
                mt_label = {'image':'📷','video':'🎥','carousel':'🎠'}.get(
                    'video' if mt == 2 else ('carousel' if mt == 8 else 'image'), '📷')
                
                caption = (getattr(p, 'caption_text', '') or '')[:200]
                likes = getattr(p, 'like_count', 0)
                comments = getattr(p, 'comment_count', 0)
                owner = getattr(p.user, 'username', '') if hasattr(p, 'user') else ''
                
                items_html += f'''<div class="search-post-card">
                    <a href="https://instagram.com/p/{p.code}" target="_blank" class="post-thumb-wrap">
                        <img src="data:image/jpeg;base64,{thumb_b64}" class="post-thumb" loading="lazy">
                        <div class="post-thumb-badge">{mt_label}</div>
                    </a>
                    <div class="search-post-info">
                        <div class="post-owner">@{owner}</div>
                        <div class="post-stats">❤️ {likes:,} — 💬 {comments:,}</div>
                        <div class="post-caption">{caption}</div>
                        <a href="https://instagram.com/p/{p.code}" target="_blank" class="post-link">🔗 فتح</a>
                    </div>
                </div>'''
            sections_html.append(f'''<section class="section">
                <h2 class="section-title">📸 المنشورات ({len(results['posts'])})</h2>
                <div class="search-posts-grid">{items_html}</div>
            </section>''')
        
        # ============ الريلز ============
        if results['reels']:
            items_html = ""
            for r in results['reels']:
                thumb_url = ThumbnailEngine.from_media(r)
                thumb_b64 = None
                if thumb_url:
                    thumb_b64 = ThumbnailEngine.from_url(
                        thumb_url, THUMB_MAX_SIZE, THUMB_QUALITY,
                        cache_key=f"r_{getattr(r, 'code', '')}")
                if not thumb_b64:
                    thumb_b64 = ThumbnailEngine.placeholder_svg('🎬')
                
                title = getattr(r, 'caption_text', '') or 'بدون عنوان'
                plays = getattr(r, 'play_count', 0) or getattr(r, 'like_count', 0)
                
                items_html += f'''<div class="reel-card">
                    <img src="data:image/jpeg;base64,{thumb_b64}" class="reel-thumb" loading="lazy">
                    <div class="reel-badge">🎬</div>
                    <div class="reel-info">
                        <div class="reel-title">{title[:80]}</div>
                        <div class="reel-stats">▶️ {plays:,}</div>
                    </div>
                </div>'''
            sections_html.append(f'''<section class="section">
                <h2 class="section-title">🎬 الريلز ({len(results['reels'])})</h2>
                <div class="reels-grid">{items_html}</div>
            </section>''')
        
        # ============ الأغاني ============
        if results['music']:
            items_html = ""
            for m in results['music']:
                cover_url = str(getattr(m, 'cover', '') or '')
                cover_b64 = ThumbnailEngine.from_url(
                    cover_url, 100, 50,
                    cache_key=f"m_{getattr(m, 'title', '')}") if cover_url else None
                cover_tag = (f"<img src='data:image/jpeg;base64,{cover_b64}' class='music-cover'>"
                            if cover_b64 else "<div class='music-cover placeholder'>🎵</div>")
                title = getattr(m, 'title', 'Unknown')
                artist = getattr(m, 'display_artist', 'Unknown')
                items_html += f'''<div class="music-card">
                    {cover_tag}
                    <div class="music-info">
                        <div class="music-title">{title}</div>
                        <div class="music-artist">{artist}</div>
                    </div>
                </div>'''
            sections_html.append(f'''<section class="section">
                <h2 class="section-title">🎵 الأغاني ({len(results['music'])})</h2>
                <div class="music-grid">{items_html}</div>
            </section>''')
        
        # ============ الإشارات ============
        if results['mentions']:
            items_html = ""
            for m in results['mentions']:
                icon = {'caption':'📝','comment':'💬','usertag':'🏷️'}.get(m['type'], '🎯')
                context = m.get('context', '')[:200]
                source = m.get('user', m.get('commenter', ''))
                items_html += f'''<div class="mention-card">
                    <div class="mention-header">
                        <span class="mention-type">{icon} {m['type']}</span>
                        {f'<span class="mention-user">@{source}</span>' if source else ''}
                    </div>
                    <div class="mention-context">{context}</div>
                    <a href="{m['source_url']}" target="_blank" class="mention-link">🔗 فتح المصدر</a>
                </div>'''
            sections_html.append(f'''<section class="section">
                <h2 class="section-title">🎯 الإشارات ({len(results['mentions'])})</h2>
                <div class="mentions-list">{items_html}</div>
            </section>''')
        
        css = """
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Tahoma,Arial,sans-serif;
background:linear-gradient(135deg,#1a1a2e 0%,#16213e 50%,#0f3460 100%);
background-attachment:fixed;color:#e0e0e0;line-height:1.6;padding:16px;min-height:100vh}
.container{max-width:1200px;margin:0 auto}
.header{background:linear-gradient(135deg,#e94560 0%,#c73659 100%);
padding:30px;border-radius:20px;margin-bottom:20px;text-align:center}
.header h1{font-size:1.5rem;margin-bottom:6px}
.header .query{background:rgba(0,0,0,0.3);padding:8px 14px;border-radius:20px;
display:inline-block;margin:8px 0;font-size:1rem}
.header .date{opacity:0.9;font-size:0.85rem;margin-top:6px}
.stats-bar{display:flex;flex-wrap:wrap;justify-content:center;gap:10px;margin-top:12px}
.stat-pill{background:rgba(0,0,0,0.4);padding:6px 12px;border-radius:15px;font-size:0.8rem}
.section{background:rgba(255,255,255,0.05);backdrop-filter:blur(12px);
border:1px solid rgba(255,255,255,0.1);border-radius:16px;padding:20px;margin-bottom:20px}
.section-title{font-size:1.2rem;color:#e94560;margin-bottom:16px;
padding-bottom:10px;border-bottom:2px solid #e94560}
.users-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px}
.user-card{background:rgba(0,0,0,0.2);border-radius:12px;padding:14px;
display:flex;gap:12px;border:1px solid rgba(233,69,96,0.25)}
.user-pic{width:60px;height:60px;border-radius:50%;object-fit:cover;
border:2px solid #e94560;flex-shrink:0}
.user-pic.placeholder{background:#333;display:flex;align-items:center;
justify-content:center;font-size:1.8rem}
.user-info{flex:1;min-width:0}
.user-username{color:#e94560;font-weight:700;font-size:0.95rem}
.badge{display:inline-block;font-size:0.7rem;padding:1px 5px;border-radius:8px;margin-right:3px}
.badge.verified{background:#10b981;color:#fff}
.badge.private{background:#6b7280;color:#fff}
.user-name{opacity:0.85;font-size:0.85rem}
.user-stats{font-size:0.75rem;color:#64b5f6;margin-top:3px}
.user-bio{font-size:0.75rem;opacity:0.7;margin-top:4px;
max-height:2.5em;overflow:hidden;text-overflow:ellipsis}
.user-link{color:#64b5f6;text-decoration:none;font-size:0.75rem;margin-top:6px;display:inline-block}
.hashtags-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px}
.hashtag-card{background:rgba(233,69,96,0.1);border:1px solid rgba(233,69,96,0.3);
border-radius:12px;padding:14px;text-align:center}
.hashtag-cover{width:60px;height:60px;border-radius:50%;object-fit:cover;margin:0 auto 8px;display:block}
.hashtag-cover.placeholder{background:#333;display:flex;align-items:center;
justify-content:center;font-size:1.8rem;margin:0 auto 8px}
.hashtag-name{font-size:1.05rem;font-weight:700;color:#e94560;margin-bottom:6px}
.hashtag-stats{font-size:0.8rem;opacity:0.85}
.hashtag-link{color:#64b5f6;text-decoration:none;font-size:0.75rem;display:inline-block;margin-top:8px}
.search-posts-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}
.search-post-card{background:rgba(0,0,0,0.2);border-radius:12px;overflow:hidden;
border:1px solid rgba(233,69,96,0.25);display:flex;gap:0}
.post-thumb-wrap{display:block;position:relative;width:120px;flex-shrink:0;aspect-ratio:1/1;overflow:hidden}
.post-thumb{width:100%;height:100%;object-fit:cover;display:block}
.post-thumb-badge{position:absolute;top:6px;right:6px;background:rgba(0,0,0,0.8);
color:#fff;padding:3px 7px;border-radius:12px;font-size:0.7rem}
.search-post-info{flex:1;padding:10px;min-width:0}
.post-owner{font-size:0.78rem;color:#e94560;font-weight:600;margin-bottom:4px}
.post-stats{font-size:0.78rem;opacity:0.85;margin-bottom:4px}
.post-caption{font-size:0.75rem;opacity:0.8;max-height:3em;overflow:hidden;
text-overflow:ellipsis;margin-bottom:6px;word-break:break-word}
.post-link{color:#64b5f6;text-decoration:none;font-size:0.75rem}
.reels-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px}
.reel-card{background:rgba(0,0,0,0.2);border-radius:12px;overflow:hidden;
border:1px solid rgba(233,69,96,0.25);position:relative}
.reel-thumb{width:100%;aspect-ratio:9/16;object-fit:cover;display:block}
.reel-badge{position:absolute;top:8px;right:8px;background:rgba(0,0,0,0.8);
color:#fff;padding:3px 8px;border-radius:12px;font-size:0.75rem}
.reel-info{padding:10px}
.reel-title{font-size:0.8rem;opacity:0.9;margin-bottom:4px;
max-height:2.5em;overflow:hidden}
.reel-stats{font-size:0.75rem;color:#64b5f6}
.music-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px}
.music-card{background:rgba(0,0,0,0.2);border-radius:12px;padding:12px;
display:flex;gap:10px;align-items:center}
.music-cover{width:70px;height:70px;border-radius:10px;object-fit:cover;flex-shrink:0}
.music-cover.placeholder{background:#333;display:flex;align-items:center;
justify-content:center;font-size:1.8rem}
.music-title{font-weight:700;font-size:0.9rem;margin-bottom:2px}
.music-artist{opacity:0.8;font-size:0.78rem}
.mentions-list{display:flex;flex-direction:column;gap:10px}
.mention-card{background:rgba(100,181,246,0.1);border:1px solid rgba(100,181,246,0.3);
border-radius:10px;padding:12px}
.mention-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}
.mention-type{font-size:0.8rem;font-weight:700;color:#64b5f6}
.mention-user{font-size:0.75rem;color:#e94560}
.mention-context{font-size:0.85rem;opacity:0.9;padding:6px;
background:rgba(0,0,0,0.2);border-radius:6px;margin-bottom:6px;word-break:break-word}
.mention-link{color:#64b5f6;text-decoration:none;font-size:0.75rem}
@media(max-width:600px){
    .search-post-card{flex-direction:column}
    .post-thumb-wrap{width:100%;aspect-ratio:16/9}
    .users-grid{grid-template-columns:1fr}
}
        """
        
        html = f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>تقرير بحث — {query}</title>
<style>{css}</style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>🔍 تقرير البحث الشامل</h1>
        <div class="query">📝 {query}</div>
        <div class="date">📅 {ts}</div>
        <div class="stats-bar">
            <span class="stat-pill">👤 {len(results['users'])}</span>
            <span class="stat-pill">#️⃣ {len(results['hashtags'])}</span>
            <span class="stat-pill">📸 {len(results['posts'])}</span>
            <span class="stat-pill">🎬 {len(results['reels'])}</span>
            <span class="stat-pill">🎵 {len(results['music'])}</span>
            <span class="stat-pill">🎯 {len(results['mentions'])}</span>
        </div>
    </div>
    {''.join(sections_html)}
    <div style="text-align:center;padding:20px;opacity:0.6;font-size:0.8rem">
        Instagram OSINT Scraper ULTRA v2 — Cache ذكي للصور
    </div>
</div>
</body>
</html>"""
        
        safe_query = "".join(c for c in query if c.isalnum() or c in "_- ")[:50] or "search"
        f = os.path.join(REPORTS_PATH, f"search_{safe_query}_{datetime.now().strftime('%Y%m%d_%H%M')}.html")
        with open(f, 'w', encoding='utf-8') as fp:
            fp.write(html)
        
        size_kb = os.path.getsize(f) / 1024
        print(f"📄 تقرير البحث: {f} ({size_kb:.1f} KB)")
        TelegramSender.send_document(f, f"🔍 تقرير بحث: {query}\n📦 الحجم: {size_kb:.1f} KB\n💾 Cache: {len(ThumbnailEngine._url_cache)} صورة")
        
        return f

# ============================================================
# 9. أداة السكراب
# ============================================================
class InstagramSearcher:
    def __init__(self):
        self.client = InstagramClient.get_client()
        if self.client is None:
            reason = InstagramClient._last_login_error or "لا توجد جلسة صالحة أو فشل تسجيل الدخول"
            raise RuntimeError(f"فشل الحصول على عميل انستغرام: {reason}")
        self.universal_searcher = UniversalSearcher(self.client)

    def _fast_cursor_traverse(self, fetch_func, user_id, max_total):
        all_items, end_cursor, page, t0 = [], None, 0, time.time()
        while len(all_items) < max_total:
            try:
                items, end_cursor = fetch_func(user_id, amount=50, end_cursor=end_cursor)
                if not items: break
                all_items.extend(items); page += 1
                if not end_cursor: break
                time.sleep(random.uniform(0.3, 0.6))
            except Exception as e:
                if InstagramClient._is_rate_limit_error(e):
                    InstagramClient._mark_throttle(e)
                    print("   🛑 traversal توقف بسبب 429؛ الجلسة محفوظة.")
                else:
                    print(f"   ⚠️ traversal: {type(e).__name__}: {str(e)[:120]}")
                break
        print(f"   ✅ traversal: {len(all_items)} / {page} صفحة / {time.time()-t0:.1f}ث")
        return all_items

    def _fetch_posts_full(self, user_id, max_total=MAX_TRAVERSE_LIMIT):
        if hasattr(self.client, 'user_medias_paginated'):
            return self._fast_cursor_traverse(self.client.user_medias_paginated, user_id, max_total)
        return self.client.user_medias(user_id, amount=max_total)

    def _fetch_clips_full(self, user_id, max_total=MAX_TRAVERSE_LIMIT):
        try:
            if hasattr(self.client, 'user_clips_paginated'):
                return self._fast_cursor_traverse(self.client.user_clips_paginated, user_id, max_total)
            return self.client.user_clips(user_id, amount=max_total)
        except Exception as e:
            print(f"   ⚠️ clips: {e}"); return []

    def _select_targets(self, user_id, count, mode, order):
        """
        Normal profile scraping uses user_medias(), which already returns the
        user's feed media (photos, videos and Reels). The separate user_clips()
        endpoint is reserved for explicit search/clip workflows and is not
        needed for the normal profile task.
        """
        need_full = (order == 'asc')

        if need_full:
            medias = self._fetch_posts_full(user_id)
            medias.sort(key=lambda x: getattr(x, 'taken_at', datetime.min))
        else:
            # For the common newest-first workflow, one authenticated feed call
            # is enough to obtain the requested number of publications.
            request_amount = count
            if mode == 'posts_only':
                # A small over-fetch makes it possible to skip pure videos while
                # still honoring the requested number of image/post entries.
                request_amount = min(max(count * 3, count + 5), 100)
            medias = self.client.user_medias(user_id, amount=request_amount)
            medias.sort(key=lambda x: getattr(x, 'taken_at', datetime.min), reverse=True)

        if mode == 'posts_only':
            medias = [
                m for m in medias
                if int(getattr(m, 'media_type', 0) or 0) in (1, 8)
            ]

        # All current profile media are already represented by user_medias().
        # Keep order, deduplicate by pk, then enforce the requested count.
        seen, unique = set(), []
        for media in medias:
            pk = getattr(media, 'pk', None)
            if pk in seen:
                continue
            seen.add(pk)
            unique.append(media)

        return unique[:count]

    def _fetch_stories_via_reels_media(self, user_id):
        try:
            result = self.client.private_request(
                "feed/reels_media/",
                data={"user_ids": json.dumps([str(user_id)])},
            )
            reels = result.get("reels", {}) if isinstance(result, dict) else {}
            raw_items = []
            for _uid, reel in reels.items():
                if isinstance(reel, dict):
                    raw_items.extend(reel.get("items", []) or [])
            if not raw_items: return []
            from instagrapi.extractors import extract_story_v1
            stories = []
            for item in raw_items:
                try: stories.append(extract_story_v1(item))
                except Exception: pass
            return stories
        except Exception:
            return []

    def _fetch_stories_smart(self, user_id, max_stories=50):
        if not InstagramClient.refresh_if_needed():
            return []
        strategies = [
            ("user_stories_v1", lambda: self.client.user_stories_v1(user_id, amount=max_stories))
        ]
        if not hasattr(self.client, "user_stories_v1"):
            strategies.append(("user_stories", lambda: self.client.user_stories(user_id, amount=max_stories)))
        for name, func in strategies:
            try:
                stories = func()
                if stories:
                    print(f"   ✅ نجحت: {name} — {len(stories)} قصة")
                    return stories
            except Exception as e:
                if "challenge" in str(e).lower() or "manual verification" in str(e).lower():
                    InstagramClient._mark_challenge(e)
                    print("   🛑 توقف القصص بسبب Challenge أمني.")
                    return []
                print(f"   ⚠️ {name}: {type(e).__name__}")
        return []

    def _extract_story_urls(self, story):
        vurl = getattr(story, 'video_url', None)
        if vurl: return str(vurl), 'video'
        turl = getattr(story, 'thumbnail_url', None)
        if turl: return str(turl), 'image'
        return None, None

    def fetch_stories(self, username, max_stories=50):
        try:
            uid = InstagramClient.get_user_id_cached(self.client, username)
            if not uid:
                print(f"⚠️ @{username} لم يتم العثور على ID")
                return 0
            
            account_id = get_account_id(username)
            if not account_id:
                print(f"⚠️ @{username} غير موجود في DB")
                return 0

            print(f"📖 جلب القصص لـ @{username} (ID: {uid})...")
            stories = self._fetch_stories_smart(uid, max_stories)
            if not stories:
                print(f"ℹ️ لا توجد قصص نشطة لـ @{username}")
                return 0

            story_folder = os.path.join(STORIES_PATH, username)
            os.makedirs(story_folder, exist_ok=True)
            downloaded = 0

            for i, story in enumerate(stories, 1):
                pk = str(getattr(story, 'pk', f'unknown_{i}'))
                story_url, media_kind = self._extract_story_urls(story)
                if not story_url:
                    continue

                ext = 'mp4' if media_kind == 'video' else 'jpg'
                file_path = os.path.join(story_folder, f"story_{pk}.{ext}")
                downloaded_ok = False
                try:
                    if hasattr(self.client, "story_download"):
                        native_path = self.client.story_download(
                            pk, filename=os.path.basename(file_path), folder=story_folder
                        )
                        if native_path and os.path.exists(native_path):
                            file_path = os.fspath(native_path)
                            downloaded_ok = True
                    elif hasattr(self.client, "story_download_by_url"):
                        native_path = self.client.story_download_by_url(
                            str(story_url), os.path.basename(file_path), story_folder
                        )
                        if native_path and os.path.exists(native_path):
                            file_path = os.fspath(native_path)
                            downloaded_ok = True
                except Exception as de:
                    if InstagramClient._is_rate_limit_error(de):
                        InstagramClient._mark_throttle(de)
                    print(f"      ⚠️ تنزيل Story: {type(de).__name__}: {str(de)[:120]}")

                if downloaded_ok:
                    save_story(account_id, story, file_path, story_url)
                    downloaded += 1
                InstagramClient.human_delay(0.8, 1.8)

            print(f"✅ النتيجة: {downloaded}/{len(stories)}")
            return downloaded

        except Exception as e:
            print(f"❌ فشل: {e}"); traceback.print_exc()
            return 0

    def fetch_highlights(self, username, max_highlights=20, max_items_per_hl=50):
        try:
            uid = InstagramClient.get_user_id_cached(self.client, username)
            if not uid: return 0
            account_id = get_account_id(username)
            if not account_id: return 0

            print(f"📌 جلب الـ Highlights لـ @{username}...")
            try:
                if hasattr(self.client, "user_highlights_v1"):
                    highlights = self.client.user_highlights_v1(uid, amount=max_highlights)
                else:
                    highlights = self.client.user_highlights(uid, amount=max_highlights)
            except Exception as he:
                print(f"   ⚠️ user_highlights فشل: {type(he).__name__}: {str(he)[:100]}")
                return 0

            if not highlights: return 0

            folder = os.path.join(HIGHLIGHTS_PATH, username)
            os.makedirs(folder, exist_ok=True)
            total_highlights = 0

            for hl in highlights[:max_highlights]:
                hl_pk = str(getattr(hl, 'pk', ''))
                cover_url = ""
                try:
                    cover_media = getattr(hl, 'cover_media', None)
                    if cover_media:
                        cropped = getattr(cover_media, 'cropped_image_version', None)
                        if cropped: cover_url = str(getattr(cropped, 'url', ''))
                        if not cover_url:
                            cover_url = str(getattr(cover_media, 'thumbnail_url', ''))
                except: pass
                
                cover_path = ""
                hl_id = save_highlight(account_id, hl, cover_url, cover_path)
                if not hl_id:
                    continue

                # 3.x يعيد عناصر الـHighlight نفسها داخل hl.items عبر v1.
                items = list(getattr(hl, "items", []) or [])[:max_items_per_hl]
                for item in items:
                    try:
                        item_pk = str(getattr(item, "pk", "") or "")
                        if not item_pk:
                            continue
                        item_folder = os.path.join(folder, str(getattr(hl, "pk", "")))
                        os.makedirs(item_folder, exist_ok=True)
                        if hasattr(self.client, "story_download"):
                            item_path = self.client.story_download(
                                item_pk, folder=item_folder, overwrite=True
                            )
                        elif hasattr(self.client, "story_download_by_url"):
                            item_url = getattr(item, "video_url", None) or getattr(item, "thumbnail_url", None)
                            if not item_url:
                                continue
                            item_path = self.client.story_download_by_url(
                                str(item_url), f"highlight_{item_pk}", item_folder
                            )
                        else:
                            continue
                        if item_path and os.path.exists(item_path):
                            save_highlight_item(
                                hl_id, item, os.fspath(item_path),
                                str(getattr(item, "video_url", "") or "")
                            )
                    except Exception as ie:
                        if InstagramClient._is_rate_limit_error(ie):
                            InstagramClient._mark_throttle(ie)
                            break
                        print(f"      ⚠️ Highlight item: {type(ie).__name__}: {str(ie)[:100]}")

                    if InstagramClient.is_throttled():
                        break

                total_highlights += 1
                if InstagramClient.is_throttled():
                    break
                InstagramClient.human_delay(1, 2)

            print(f"✅ النتيجة: {total_highlights} Highlights")
            return total_highlights

        except Exception as e:
            print(f"❌ فشل: {e}"); traceback.print_exc()
            return 0

    def _get_low_res_video_url(self, media):
        try:
            versions = getattr(media, 'video_versions', None)
            if not versions: return None
            best, best_pixels = None, float('inf')
            for v in versions:
                w = getattr(v, 'width', 0) or 0
                h = getattr(v, 'height', 0) or 0
                pixels = w * h
                if 0 < pixels < best_pixels:
                    best_pixels = pixels; best = v
            if best and getattr(best, 'url', None):
                return str(best.url)
        except Exception: pass
        return None

    def _native_download_media(self, media, folder):
        """
        تنزيل الوسائط عبر downloader الرسمي في instagrapi، مع fallback واحد
        يعتمد على *_download_by_url بدلاً من requests الخام لتجنب TooManyRedirects.
        """
        os.makedirs(folder, exist_ok=True)
        downloaded = []

        def add_path(path, kind=None):
            if not path:
                return
            p = os.fspath(path)
            if os.path.exists(p):
                inferred = kind or ('video' if p.lower().endswith(('.mp4', '.mov', '.webm')) else 'image')
                downloaded.append((p, inferred))

        try:
            media_type = int(getattr(media, 'media_type', 0) or 0)
            pk = str(getattr(media, 'pk', '') or '')

            if media_type == 1:
                if hasattr(self.client, 'photo_download'):
                    add_path(self.client.photo_download(pk, folder=folder, overwrite=True), 'image')
                elif getattr(media, 'thumbnail_url', None) and hasattr(self.client, 'photo_download_by_url'):
                    add_path(
                        self.client.photo_download_by_url(
                            str(media.thumbnail_url), f"image_{getattr(media, 'code', pk)}.jpg",
                            folder=folder, overwrite=True
                        ), 'image'
                    )

            elif media_type == 2:
                if hasattr(self.client, 'video_download'):
                    add_path(self.client.video_download(pk, folder=folder, overwrite=True), 'video')
                elif getattr(media, 'video_url', None) and hasattr(self.client, 'video_download_by_url'):
                    add_path(
                        self.client.video_download_by_url(
                            str(media.video_url), f"video_{getattr(media, 'code', pk)}.mp4",
                            folder=folder, overwrite=True
                        ), 'video'
                    )

            elif media_type == 8:
                if hasattr(self.client, 'album_download'):
                    result = self.client.album_download(pk, folder=folder, overwrite=True)
                    if isinstance(result, (list, tuple)):
                        for p in result:
                            add_path(p)
                    else:
                        add_path(result)
                else:
                    # Fallback: احصل على موارد الكاروسيل مرة واحدة فقط.
                    mi = self.client.media_info(pk)
                    for idx, resource in enumerate(getattr(mi, 'resources', []) or [], 1):
                        rpk = str(getattr(resource, 'pk', '') or '')
                        if int(getattr(resource, 'media_type', 0) or 0) == 2 and hasattr(self.client, 'video_download'):
                            add_path(
                                self.client.video_download(rpk, folder=folder, overwrite=True),
                                'video'
                            )
                        elif hasattr(self.client, 'photo_download'):
                            add_path(
                                self.client.photo_download(rpk, folder=folder, overwrite=True),
                                'image'
                            )

            if downloaded:
                return downloaded

        except Exception as e:
            if InstagramClient._is_rate_limit_error(e):
                InstagramClient._mark_throttle(e)
                raise
            print(f"      ⚠️ downloader الأصلي: {type(e).__name__}: {str(e)[:140]}")

        # Fallback واحد فقط بواسطة URL الرسمي الذي يحتويه media.
        try:
            code = str(getattr(media, 'code', getattr(media, 'pk', 'media')))
            media_type = int(getattr(media, 'media_type', 0) or 0)

            if media_type == 1 and getattr(media, 'thumbnail_url', None) and hasattr(self.client, 'photo_download_by_url'):
                add_path(
                    self.client.photo_download_by_url(
                        str(media.thumbnail_url), f"image_{code}.jpg",
                        folder=folder, overwrite=True
                    ), 'image'
                )
            elif media_type == 2 and getattr(media, 'video_url', None) and hasattr(self.client, 'video_download_by_url'):
                add_path(
                    self.client.video_download_by_url(
                        str(media.video_url), f"video_{code}.mp4",
                        folder=folder, overwrite=True
                    ), 'video'
                )

        except Exception as e:
            if InstagramClient._is_rate_limit_error(e):
                InstagramClient._mark_throttle(e)
                raise
            print(f"      ⚠️ fallback downloader: {type(e).__name__}: {str(e)[:140]}")

        return downloaded

    def _post_caption_html(self, media, index, total, username):
        code = html_escape(str(getattr(media, 'code', '') or ''))
        url = f"https://www.instagram.com/p/{code}/"
        date = getattr(media, 'taken_at', None)
        date_text = date.strftime("%Y-%m-%d %H:%M") if date else "غير معروف"
        caption = str(getattr(media, 'caption_text', '') or "(لا يوجد نص)")
        caption = html_escape(caption[:600])
        likes = int(getattr(media, 'like_count', 0) or 0)
        comments = int(getattr(media, 'comment_count', 0) or 0)
        profile_url = f"https://www.instagram.com/{html_escape(username)}/"

        views = int(getattr(media, "view_count", 0) or 0)
        plays = int(getattr(media, "play_count", 0) or 0)
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

            files = self._native_download_media(media, folder)

            if not files:
                # لا نعتبر المنشور منجزاً إذا لم تُحفظ وسائطه.
                TelegramSender.send_rich_message(
                    self._post_caption_html(media, index, total, username) +
                    "\n\n⚠️ <b>تعذر تنزيل الوسائط الآن</b> — تم الاحتفاظ بالمنشور في قاعدة البيانات لإعادة المحاولة."
                )
                print(f"      ⚠️ لم يتم تنزيل الوسائط للمنشور {getattr(media, 'code', '?')}")
                return False

            main_fp = files[0][0]
            mtype = files[0][1] if len(files) == 1 else 'carousel'
            thumb_url = str(getattr(media, 'thumbnail_url', '') or '')

            update_post_media_type(pid, mtype, main_fp, thumb_url)

            keyboard = TelegramSender._post_keyboard(
                f"https://www.instagram.com/p/{html_escape(str(getattr(media, 'code', '')))}" + "/",
                f"https://www.instagram.com/{html_escape(username)}/"
            )

            caption = self._post_caption_html(media, index, total, username)
            for file_index, (path, kind) in enumerate(files, 1):
                media_caption = caption
                if file_index > 1:
                    media_caption = (
                        f"<b>📌 المنشور #{index:02d} / {total:02d}</b>  •  "
                        f"🖼️ ملف {file_index}/{len(files)}\n"
                        f'<a href="https://www.instagram.com/p/{html_escape(str(getattr(media, "code", "")))}">🔗 فتح المنشور</a>'
                    )
                if kind == 'video':
                    ok = TelegramSender.send_video(path, media_caption, reply_markup=keyboard if file_index == 1 else None)
                else:
                    ok = TelegramSender.send_photo(path, media_caption, reply_markup=keyboard if file_index == 1 else None)
                if not ok:
                    print(f"      ⚠️ تعذر إرسال {os.path.basename(path)} إلى Telegram")
                InstagramClient.human_delay(0.5, 1.2)

            if fetch_comments and int(getattr(media, 'comment_count', 0) or 0) > 0:
                try:
                    if hasattr(self.client, "media_comments_v1"):
                        comments = self.client.media_comments_v1(media.pk, amount=max_comments)
                    else:
                        comments = self.client.media_comments(media.pk, amount=max_comments)
                    for c in comments:
                        save_comment(pid, c)
                except Exception as ce:
                    if InstagramClient._is_rate_limit_error(ce):
                        InstagramClient._mark_throttle(ce)
                    print(f"   ⚠️ تعليقات: {type(ce).__name__}: {str(ce)[:100]}")

            InstagramClient._save_session()
            return True

        except Exception as e:
            if InstagramClient._is_rate_limit_error(e):
                InstagramClient._mark_throttle(e)
                print("🛑 تم إيقاف المعالجة مؤقتاً بسبب 429. الجلسة محفوظة ولن يتم تسجيل الخروج.")
                return False
            print(f"⚠️ معالجة: {type(e).__name__}: {str(e)[:120]}")
            return False

    def get_profile_info(self, username):
        if not InstagramClient.refresh_if_needed():
            if InstagramClient.is_throttled():
                TelegramSender.send_message(
                    f"🛑 Instagram يفرض تهدئة مؤقتة بسبب كثرة الطلبات. الجلسة محفوظة. "
                    f"المتبقي تقريباً {InstagramClient.throttle_remaining()} ثانية."
                )
            else:
                TelegramSender.send_message("❌ لا توجد جلسة Instagram صالحة حالياً")
            return None

        print(f"📊 معلومات: {username}")
        try:
            # اسم مستخدم مباشر: جلب الملف الكامل مباشرة بدون Search endpoint.
            if hasattr(self.client, "user_info_by_username_v1"):
                u = self.client.user_info_by_username_v1(username)
            else:
                u = self.client.user_info_by_username(username)
            uid = str(getattr(u, 'pk', '') or '')
            if uid:
                InstagramClient._user_id_cache[str(username).lower().lstrip('@')] = uid
                InstagramClient._save_user_cache()

            info = {
                'username': str(getattr(u, 'username', username) or username),
                'full_name': str(getattr(u, 'full_name', '') or ''),
                'bio': str(getattr(u, 'biography', '') or ''),
                'posts_count': int(getattr(u, 'media_count', 0) or 0),
                'followers': int(getattr(u, 'follower_count', 0) or 0),
                'following': int(getattr(u, 'following_count', 0) or 0),
                'profile_pic': str(getattr(u, 'profile_pic_url', '') or ''),
                'is_private': bool(getattr(u, 'is_private', False)),
                'is_verified': bool(getattr(u, 'is_verified', False)),
                'is_business': bool(getattr(u, 'is_business', False) or getattr(u, 'is_professional_account', False)),
                'category': str(getattr(u, 'category', '') or getattr(u, 'business_category_name', '') or ''),
                'external_url': str(getattr(u, 'external_url', '') or ''),
            }
            save_or_update_account(info)
            InstagramClient._save_session()

            uname = html_escape(info['username'])
            fullname = html_escape(info['full_name'] or "—")
            bio = html_escape(info['bio'][:700] or "لا يوجد")
            status = "🔒 خاص" if info['is_private'] else "🌐 عام"
            verified = "✅ موثق" if info['is_verified'] else "— غير موثق"
            account_type = "💼 احترافي/تجاري" if info['is_business'] else "👤 شخصي"
            cat = html_escape(info['category']) if info['category'] else "—"
            website = info['external_url']

            profile_html = (
                f"<b>👤 @{uname}</b>\n"
                f"<b>{fullname}</b>\n\n"
                f"📊 <b>إحصائيات الحساب</b>\n"
                f"• المنشورات: <b>{info['posts_count']:,}</b>\n"
                f"• المتابعون: <b>{info['followers']:,}</b>\n"
                f"• يتابع: <b>{info['following']:,}</b>\n"
                f"• الحالة: <b>{status}</b>\n"
                f"• التوثيق: <b>{verified}</b>\n"
                f"• النوع: <b>{account_type}</b>\n"
                f"• الفئة: <b>{cat}</b>\n\n"
                f"<blockquote expandable>{bio}</blockquote>"
            )
            if website:
                profile_html += f'\n🌐 <a href="{html_escape(website)}">الموقع الخارجي</a>'

            profile_html += (
                f'\n\n<a href="https://www.instagram.com/{uname}/">🔗 فتح الحساب على Instagram</a>'
            )

            keyboard = TelegramSender._post_keyboard(
                f"https://www.instagram.com/{uname}/"
            )

            if info['profile_pic']:
                try:
                    r = _http_session.get(info['profile_pic'], timeout=15)
                    if r.status_code == 200:
                        fpath = os.path.join(MEDIA_PATH, info['username'])
                        os.makedirs(fpath, exist_ok=True)
                        p = os.path.join(fpath, "profile_pic.jpg")
                        with open(p, "wb") as fp:
                            fp.write(r.content)
                        TelegramSender.send_photo(
                            p, profile_html, reply_markup=keyboard
                        )
                    else:
                        TelegramSender.send_rich_message(profile_html, reply_markup=keyboard)
                except Exception as e:
                    print(f"⚠️ صورة البروفايل: {type(e).__name__}: {str(e)[:100]}")
                    TelegramSender.send_rich_message(profile_html, reply_markup=keyboard)
            else:
                TelegramSender.send_rich_message(profile_html, reply_markup=keyboard)

            return info
        except Exception as e:
            if "challenge" in str(e).lower() or "manual verification" in str(e).lower():
                InstagramClient._mark_challenge(e)
                TelegramSender.send_message("🛑 Instagram طلب تحقق أمني. أوقفنا الطلبات ولم نعد تسجيل الدخول تلقائياً.")
            elif InstagramClient._is_rate_limit_error(e):
                InstagramClient._mark_throttle(e)
                TelegramSender.send_message("🛑 Instagram فرض تهدئة أثناء جلب معلومات الحساب. الجلسة محفوظة.")
            else:
                print(f"❌ {type(e).__name__}: {e}")
                TelegramSender.send_message(f"❌ {type(e).__name__}: {html_escape(str(e)[:300])}")
            return None

    def scrape(self, username, max_posts=10, scrape_mode='smart_merge', order='desc',
               fetch_comments=False, max_comments=20, fetch_followers=False, max_followers=100,
               fetch_stories=False, max_stories=50,
               fetch_highlights=False, max_highlights=20):
        if not InstagramClient.refresh_if_needed():
            if InstagramClient.is_throttled():
                TelegramSender.send_message(
                    f"🛑 Instagram يفرض تهدئة مؤقتة بسبب 429. "
                    f"الجلسة لم تنتهِ ولن نعيد تسجيل الدخول. المتبقي تقريباً {InstagramClient.throttle_remaining()} ثانية."
                )
            else:
                TelegramSender.send_message("❌ لا توجد جلسة Instagram صالحة حالياً")
            return

        mode_names = {'posts_only': '📸 منشورات فقط',
                      'posts_and_videos': '📸🎬 منشورات+فيديوهات',
                      'smart_merge': '🧠 دمج ذكي'}
        print(f"🔍 @{username} | {mode_names.get(scrape_mode)} | "
              f"{'الأحدث' if order == 'desc' else 'الأقدم'} | {max_posts}")

        try:
            # 🎯 استخدام Cache الذكي
            uid = InstagramClient.get_user_id_cached(self.client, username)
            if not uid:
                TelegramSender.send_message(f"❌ لم يتم العثور على @{username}")
                return
            
            account_id = get_account_id(username)
            InstagramClient.human_delay()
            folder = os.path.join(MEDIA_PATH, username); os.makedirs(folder, exist_ok=True)

            targets = self._select_targets(uid, max_posts, scrape_mode, order)
            if targets:
                profile_url = f"https://www.instagram.com/{html_escape(username.lstrip('@'))}/"
                intro_html = (
                    f"<b>📦 Instagram OSINT</b>\n"
                    f"<b>👤 @{html_escape(username.lstrip('@'))}</b>\n"
                    f"📌 <b>{len(targets)}</b> منشور محدد للتنزيل\n"
                    f"🧠 الوضع: <b>{html_escape(mode_names.get(scrape_mode, scrape_mode))}</b>\n"
                    f"📅 الترتيب: <b>{'الأحدث' if order == 'desc' else 'الأقدم'}</b>"
                )
                TelegramSender.send_rich_message(
                    intro_html,
                    reply_markup=TelegramSender._post_keyboard(profile_url)
                )
                successful = 0
                for i, m in enumerate(targets, 1):
                    if InstagramClient.is_throttled():
                        print(f"🛑 توقف آمن بعد 429 — حفظنا ما تم إنجازه ({successful}/{len(targets)})")
                        TelegramSender.send_message(
                            f"🛑 توقف آمن بسبب تقييد Instagram بعد {successful}/{len(targets)} منشورات.\n"
                            f"الجلسة محفوظة، ولن يتم تسجيل الدخول من جديد.\n"
                            f"⏳ التهدئة الحالية: {InstagramClient.throttle_remaining()} ثانية."
                        )
                        break
                    print(f"📄 [{i}/{len(targets)}] {m.code}")
                    if self._process_media(
                        m, username, folder, index=i, total=len(targets),
                        fetch_comments=fetch_comments, max_comments=max_comments
                    ):
                        successful += 1
                    InstagramClient.human_delay(1.5, 3.0)

            if fetch_followers and account_id:
                try:
                    if hasattr(self.client, "user_followers_v1"):
                        fl = self.client.user_followers_v1(uid, amount=max_followers)
                    else:
                        fl = list(self.client.user_followers(uid, amount=max_followers).values())
                    fl = list(fl)[:max_followers]
                    for follower in fl:
                        save_follower(account_id, follower)
                    print(f"👥 تم جلب {len(fl)} متابع من الحد المطلوب {max_followers}")
                    TelegramSender.send_follower_list(username, fl)
                except Exception as fe:
                    if "challenge" in str(fe).lower() or "manual verification" in str(fe).lower():
                        InstagramClient._mark_challenge(fe)
                        print("🛑 جلب المتابعين توقف بسبب Challenge أمني.")
                    elif InstagramClient._is_rate_limit_error(fe):
                        InstagramClient._mark_throttle(fe)
                        print("🛑 جلب المتابعين توقف بسبب 429؛ الجلسة محفوظة.")
                    else:
                        print(f"⚠️ متابعين: {type(fe).__name__}: {str(fe)[:120]}")

            if fetch_stories:
                print("📖 جلب القصص...")
                self.fetch_stories(username, max_stories=max_stories)

            if fetch_highlights:
                print("📌 جلب الـ Highlights...")
                self.fetch_highlights(username, max_highlights=max_highlights)

            csv_p = self._csv_report(username)
            html_p = self._html_report(username, order)
            if csv_p: TelegramSender.send_document(csv_p, f"📊 CSV @{username}")
            if html_p: TelegramSender.send_document(html_p, f"📄 HTML @{username}")

            InstagramClient._save_session(force=True)
            TelegramSender.send_rich_message(
                f"<b>✅ انتهت المهمة</b>\n"
                f"👤 @{html_escape(username.lstrip('@'))}\n"
                f"📦 المحدد: <b>{len(targets) if targets else 0}</b>\n"
                f"✅ نُزّل بنجاح: <b>{successful if targets else 0}</b>\n"
                f"💾 النتائج والوسائط محفوظة في Google Drive."
            )
        except Exception as e:
            if InstagramClient._is_rate_limit_error(e):
                InstagramClient._mark_throttle(e)
                print("🛑 توقفت المهمة بسبب 429 من Instagram.")
                print("   الجلسة لم تنتهِ؛ تم حفظها وإيقاف الطلبات مؤقتاً.")
                TelegramSender.send_message(
                    f"🛑 <b>Instagram فرض تهدئة مؤقتة</b>\n"
                    f"الجلسة محفوظة ولم يتم تسجيل الخروج.\n"
                    f"⏳ التهدئة: {InstagramClient.throttle_remaining()} ثانية.",
                )
            else:
                print(f"❌ {type(e).__name__}: {str(e)[:250]}")
                traceback.print_exc()
                TelegramSender.send_message(
                    f"❌ {type(e).__name__}: {html_escape(str(e)[:500])}"
                )

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
                w.writerow(['الكود','الرابط','النوع','إعجابات','تعليقات','التاريخ','النص'])
                for r in posts:
                    w.writerow([r['post_code'], r['post_url'], r['media_type'],
                                r['like_count'], r['comment_count'], r['taken_at'],
                                (r['caption'] or '')[:500]])
            if fl:
                ff = os.path.join(REPORTS_PATH, f"{username}_followers_{ts}.csv")
                with open(ff, 'w', newline='', encoding='utf-8-sig') as f:
                    w = csv.writer(f); w.writerow(['username','full_name'])
                    for r in fl: w.writerow([r['username'], r['full_name']])
            return rf
        except Exception as e:
            print(f"⚠️ CSV: {e}"); return None

    # ========================================================
    # 📄 تقرير HTML — يستخدم الملفات المحلية فقط! (بدون طلبات جديدة)
    # ========================================================
    def _html_report(self, username, order='desc'):
        try:
            aid = get_account_id(username)
            if not aid: return None
            ai = get_account_info(username)
            posts = get_posts_with_comments(aid, order=order)
            fl = get_followers(aid)
            stories = get_stories(aid)
            highlights = get_highlights(aid)

            # بروفايل
            pic_b64 = ""
            pp = os.path.join(MEDIA_PATH, username, "profile_pic.jpg")
            if os.path.exists(pp):
                pic_b64 = ThumbnailEngine.from_file(pp, 100, 50)

            ts = datetime.now().strftime('%Y-%m-%d %H:%M')
            oar = 'الأحدث أولاً' if order == 'desc' else 'الأقدم أولاً'

            # ============ Stories ============
            stories_html = ""
            if stories:
                items = ""
                shown = 0
                for s in stories:
                    p = s.get('file_path', '')
                    
                    # 🎯 استخدام ThumbnailEngine.from_file فقط - لا يوجد طلب HTTP جديد!
                    b64 = ThumbnailEngine.from_file(p, THUMB_MAX_SIZE, THUMB_QUALITY) if p else None
                    if not b64:
                        b64 = ThumbnailEngine.placeholder_svg('📖' if s.get('media_type') != 'video' else '🎥')
                    
                    is_vid = s.get('media_type') == 'video'
                    icon = '🎥' if is_vid else '🖼️'
                    date_txt = (s.get('taken_at') or '')[:16]
                    
                    items += (f'<div class="thumb-item">'
                              f'<img src="data:image/jpeg;base64,{b64}" alt="story" loading="lazy">'
                              f'<div class="thumb-badge">{icon}</div>'
                              f'<div class="thumb-date">{date_txt}</div>'
                              f'</div>')
                    shown += 1
                if shown:
                    stories_html = f'''<section class="section">
                        <h2 class="section-title">📖 القصص المؤقتة ({shown})</h2>
                        <div class="thumb-grid">{items}</div>
                    </section>'''

            # ============ Highlights ============
            highlights_html = ""
            if highlights:
                hl_blocks = ""
                for h in highlights:
                    title = h.get('title', '')
                    cover_path = h.get('cover_path', '')
                    
                    cover_b64 = ""
                    if cover_path and os.path.exists(cover_path):
                        cover_b64 = ThumbnailEngine.from_file(cover_path, 80, 50)

                    cover_tag = (f'<img src="data:image/jpeg;base64,{cover_b64}" '
                                 f'class="hl-cover" alt="{title}">') if cover_b64 else \
                                '<div class="hl-cover hl-cover-placeholder">📌</div>'

                    items = h.get('items', [])
                    items_html = ""
                    shown_items = 0
                    for it in items:
                        p = it.get('file_path', '')
                        # 🎯 من الملف المحلي فقط - بدون طلبات HTTP!
                        b64 = ThumbnailEngine.from_file(p, THUMB_MAX_SIZE, THUMB_QUALITY) if p else None
                        if not b64:
                            b64 = ThumbnailEngine.placeholder_svg('📌')
                        
                        is_vid = it.get('media_type') == 'video'
                        icon = '🎥' if is_vid else '🖼️'
                        items_html += (f'<div class="thumb-item">'
                                       f'<img src="data:image/jpeg;base64,{b64}" alt="item" loading="lazy">'
                                       f'<div class="thumb-badge">{icon}</div>'
                                       f'</div>')
                        shown_items += 1

                    hl_blocks += f'''<div class="hl-block">
                        <div class="hl-header">
                            {cover_tag}
                            <div class="hl-info">
                                <div class="hl-title">📌 {title}</div>
                                <div class="hl-count">{shown_items} عنصر</div>
                            </div>
                        </div>
                        <div class="thumb-grid">{items_html}</div>
                    </div>'''

                if hl_blocks:
                    highlights_html = f'''<section class="section">
                        <h2 class="section-title">📌 الاستوري المميز ({len(highlights)})</h2>
                        <div class="hl-container">{hl_blocks}</div>
                    </section>'''

            # ============ Profile ============
            pic_html = (f"<img src='data:image/jpeg;base64,{pic_b64}' class='profile-pic' alt='Profile'>"
                        if pic_b64 else
                        "<div class='profile-pic placeholder'>👤</div>")
            fl_html = ("<div class='followers-grid'>" + "".join(
                f"<div class='follower-item'><div class='follower-username'>@{x['username']}</div>"
                f"<div class='follower-name'>{x['full_name']}</div></div>" for x in fl) + "</div>"
            ) if fl else "<p class='empty'>لا يوجد متابعون</p>"

            # ============ Posts — من الملفات المحلية فقط! ============
            posts_html = ""
            for p in posts:
                mt = {'image':'📷 صورة','video':'🎥 فيديو','carousel':'🎠 كاروسيل'}.get(p.get('media_type'),'📄')
                
                file_path = p.get('file_path', '')
                
                # 🎯 استخدام from_file فقط - يقرأ .thumb.jpg المحلي
                # لا يوجد أي طلب HTTP جديد!
                post_thumb = ThumbnailEngine.from_file(file_path, THUMB_MAX_SIZE, THUMB_QUALITY) if file_path else None
                
                if not post_thumb:
                    post_thumb = ThumbnailEngine.placeholder_svg('📷')
                
                thumb_tag = (f"<a href='{p['post_url']}' target='_blank' class='post-thumb-wrap'>"
                             f"<img src='data:image/jpeg;base64,{post_thumb}' "
                             f"class='post-thumb' alt='post' loading='lazy'>"
                             f"<div class='post-thumb-open'>🔗 فتح</div></a>")

                cm = ""
                if p['comments']:
                    cm = ("<div class='comments-section'><button class='comments-toggle' "
                          "onclick=\"var n=this.nextElementSibling;"
                          "n.style.display=n.style.display==='none'?'block':'none';\">"
                          f"💬 عرض {len(p['comments'])} تعليق</button><div style='display:none;'>")
                    for c in p['comments']:
                        cm += (f"<div class='comment'><div class='commenter'>@{c['commenter_username']}</div>"
                               f"<div class='comment-text'>{c['comment_text']}</div>"
                               f"<div class='comment-date'>{c['created_at']}</div></div>")
                    cm += "</div></div>"

                posts_html += (f"<div class='post-card'>"
                    f"<div class='post-header'><div class='post-info'>"
                    f"<span class='post-type'>{mt}</span> <span class='post-date'>📅 {p['taken_at']}</span></div>"
                    f"<div class='post-stats'><span>❤️ {p['like_count']:,}</span>"
                    f"<span>💬 {p['comment_count']:,}</span></div></div>"
                    f"<div class='post-body'>{thumb_tag}<div class='post-content'>"
                    f"{'<div class=post-caption>' + str(p.get('caption','')) + '</div>' if p.get('caption') else ''}"
                    f"<a href='{p['post_url']}' target='_blank' class='post-link'>🔗 فتح المنشور</a>"
                    f"</div></div>{cm}</div>")

            css = """
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html{font-size:16px;scroll-behavior:smooth}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Tahoma,Arial,sans-serif;
background:linear-gradient(135deg,#1a1a2e 0%,#16213e 50%,#0f3460 100%);
background-attachment:fixed;color:#e0e0e0;line-height:1.6;
padding:clamp(8px,2vw,16px);min-height:100vh;-webkit-font-smoothing:antialiased}
.container{max-width:1100px;margin:0 auto;width:100%}
.header{background:linear-gradient(135deg,#e94560 0%,#c73659 100%);
padding:clamp(18px,4vw,40px);border-radius:clamp(12px,2vw,20px);
margin-bottom:clamp(14px,3vw,28px);text-align:center;box-shadow:0 8px 32px rgba(233,69,96,0.3)}
.header h1{font-size:clamp(1.1rem,4vw,1.8rem);margin-bottom:6px;font-weight:700;word-break:break-word}
.header .date{opacity:0.9;font-size:clamp(0.7rem,2vw,0.9rem)}
.section{background:rgba(255,255,255,0.05);backdrop-filter:blur(12px);
-webkit-backdrop-filter:blur(12px);border:1px solid rgba(255,255,255,0.1);
border-radius:clamp(12px,2vw,20px);padding:clamp(14px,3vw,28px);margin-bottom:clamp(14px,3vw,28px)}
.section-title{font-size:clamp(1rem,3vw,1.3rem);color:#e94560;
margin-bottom:clamp(10px,2vw,20px);padding-bottom:clamp(6px,1.5vw,12px);
border-bottom:2px solid #e94560;font-weight:700}
.profile-grid{display:grid;grid-template-columns:1fr;gap:clamp(14px,3vw,24px);align-items:center;text-align:center}
@media(min-width:600px){.profile-grid{grid-template-columns:130px 1fr;text-align:right}}
.profile-pic{width:clamp(80px,18vw,130px);height:clamp(80px,18vw,130px);
border-radius:50%;border:3px solid #e94560;object-fit:cover;margin:0 auto;display:block}
.profile-pic.placeholder{background:#333;display:flex;align-items:center;justify-content:center;font-size:clamp(2rem,6vw,3rem)}
.profile-info h2{color:#fff;font-size:clamp(1.05rem,3vw,1.5rem);margin-bottom:4px;word-break:break-word}
.profile-info .fullname{font-size:clamp(0.85rem,2.5vw,1.05rem);opacity:0.9;margin-bottom:10px}
.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(75px,1fr));gap:clamp(6px,1.5vw,14px);margin-top:10px}
.stat-card{background:rgba(233,69,96,0.1);padding:clamp(8px,2vw,16px);
border-radius:clamp(8px,1.5vw,12px);text-align:center;border:1px solid rgba(233,69,96,0.3)}
.stat-number{font-size:clamp(0.95rem,3vw,1.5rem);font-weight:700;color:#e94560;line-height:1.2}
.stat-label{font-size:clamp(0.6rem,1.8vw,0.8rem);opacity:0.8;margin-top:3px}
.bio{margin-top:clamp(10px,2vw,16px);padding:clamp(10px,2vw,16px);
background:rgba(0,0,0,0.2);border-radius:clamp(8px,1.5vw,12px);
white-space:pre-wrap;font-size:clamp(0.75rem,2vw,0.95rem);word-break:break-word;line-height:1.7}
.thumb-grid{
    display:grid;
    grid-template-columns:repeat(auto-fill,minmax(90px,1fr));
    gap:clamp(6px,1.2vw,10px);
}
@media(min-width:600px){.thumb-grid{grid-template-columns:repeat(auto-fill,minmax(110px,1fr));}}
@media(min-width:900px){.thumb-grid{grid-template-columns:repeat(auto-fill,minmax(130px,1fr));}}
.thumb-item{
    position:relative;background:rgba(0,0,0,0.3);
    border-radius:clamp(6px,1.5vw,10px);overflow:hidden;
    border:1px solid rgba(233,69,96,0.25);
    transition:transform 0.2s;
}
.thumb-item:hover{transform:scale(1.04);z-index:2}
.thumb-item img{
    width:100%;height:auto;display:block;
    aspect-ratio:9/16;object-fit:cover;
}
.thumb-badge{
    position:absolute;top:4px;right:4px;
    background:rgba(0,0,0,0.75);padding:2px 6px;
    border-radius:10px;font-size:clamp(0.6rem,1.5vw,0.72rem);
    line-height:1;
}
.thumb-date{
    position:absolute;bottom:0;left:0;right:0;
    background:linear-gradient(transparent,rgba(0,0,0,0.85));
    padding:8px 6px 4px;font-size:clamp(0.55rem,1.5vw,0.7rem);
    text-align:center;color:#fff;
}
.hl-container{display:flex;flex-direction:column;gap:clamp(14px,3vw,22px)}
.hl-block{background:rgba(0,0,0,0.25);border-radius:clamp(10px,2vw,16px);
padding:clamp(12px,2.5vw,20px);border:1px solid rgba(233,69,96,0.25)}
.hl-header{display:flex;align-items:center;gap:clamp(10px,2vw,16px);
margin-bottom:clamp(10px,2vw,16px);
padding-bottom:clamp(10px,2vw,14px);border-bottom:1px solid rgba(255,255,255,0.1)}
.hl-cover{width:clamp(55px,10vw,75px);height:clamp(55px,10vw,75px);
border-radius:50%;border:3px solid #e94560;object-fit:cover;flex-shrink:0}
.hl-cover-placeholder{display:flex;align-items:center;justify-content:center;
background:#333;font-size:24px}
.hl-info{flex:1;min-width:0}
.hl-title{font-size:clamp(0.9rem,2.4vw,1.15rem);font-weight:700;color:#fff;
margin-bottom:4px;word-break:break-word}
.hl-count{font-size:clamp(0.7rem,1.8vw,0.82rem);opacity:0.75}
.post-card{background:rgba(0,0,0,0.2);border-radius:clamp(10px,2vw,14px);
padding:clamp(12px,2.5vw,20px);margin-bottom:clamp(10px,2.5vw,18px);
border-right:3px solid #e94560}
.post-header{display:flex;justify-content:space-between;align-items:center;
flex-wrap:wrap;gap:6px;margin-bottom:10px}
.post-info{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.post-type{background:#e94560;color:#fff;padding:3px 9px;border-radius:20px;
font-size:clamp(0.6rem,1.8vw,0.75rem);font-weight:600;white-space:nowrap}
.post-date{font-size:clamp(0.65rem,1.9vw,0.82rem);opacity:0.85}
.post-stats{display:flex;gap:10px;font-size:clamp(0.65rem,1.9vw,0.82rem);opacity:0.85}
.post-body{display:grid;grid-template-columns:1fr;gap:12px;align-items:start}
@media(min-width:600px){.post-body{grid-template-columns:140px 1fr}}
.post-thumb-wrap{
    display:block;position:relative;border-radius:10px;overflow:hidden;
    border:2px solid rgba(233,69,96,0.35);
    transition:transform 0.2s, border-color 0.2s;
}
.post-thumb-wrap:hover{transform:scale(1.03);border-color:#e94560}
.post-thumb{width:100%;height:auto;display:block;aspect-ratio:1/1;object-fit:cover}
.post-thumb-open{
    position:absolute;bottom:0;left:0;right:0;
    background:rgba(233,69,96,0.85);color:#fff;
    text-align:center;padding:4px;font-size:0.75rem;font-weight:600;
}
.post-content{min-width:0}
.post-caption{white-space:pre-wrap;margin-bottom:10px;
padding:clamp(8px,2vw,14px);background:rgba(255,255,255,0.05);
border-radius:8px;font-size:clamp(0.75rem,2vw,0.92rem);
word-break:break-word;line-height:1.7}
.post-link{color:#64b5f6;text-decoration:none;
font-size:clamp(0.7rem,2vw,0.88rem);display:inline-block;padding:5px 0;font-weight:500}
.post-link:hover{text-decoration:underline}
.comments-section{margin-top:12px;padding-top:12px;
border-top:1px solid rgba(255,255,255,0.1)}
.comments-toggle{background:rgba(100,181,246,0.1);border:1px solid rgba(100,181,246,0.3);
color:#64b5f6;cursor:pointer;font-size:clamp(0.7rem,2vw,0.88rem);
padding:7px 12px;border-radius:20px;font-family:inherit}
.comments-toggle:hover{background:rgba(100,181,246,0.2)}
.comment{background:rgba(255,255,255,0.03);padding:clamp(7px,1.5vw,12px);
border-radius:8px;margin-top:6px}
.commenter{font-weight:700;color:#e94560;font-size:clamp(0.7rem,2vw,0.82rem)}
.comment-text{font-size:clamp(0.75rem,2vw,0.88rem);margin-top:3px;word-break:break-word}
.comment-date{font-size:clamp(0.55rem,1.6vw,0.7rem);opacity:0.6;margin-top:3px}
.followers-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));
gap:clamp(6px,1.5vw,12px);max-height:400px;overflow-y:auto;padding:clamp(3px,1vw,8px)}
.follower-item{background:rgba(255,255,255,0.05);padding:clamp(7px,1.5vw,12px);
border-radius:8px;font-size:clamp(0.7rem,2vw,0.82rem);word-break:break-word}
.follower-username{color:#e94560;font-weight:700;font-size:clamp(0.7rem,2vw,0.82rem)}
.follower-name{opacity:0.8;font-size:clamp(0.6rem,1.8vw,0.75rem);margin-top:2px}
.empty{opacity:0.6;text-align:center;padding:18px;font-size:clamp(0.75rem,2vw,0.9rem)}
.footer{text-align:center;padding:clamp(14px,3vw,24px);
opacity:0.6;font-size:clamp(0.65rem,1.8vw,0.8rem)}
@media print{body{background:#fff;color:#000;padding:0}
.header{background:#e94560 !important;-webkit-print-color-adjust:exact;print-color-adjust:exact}
.section{background:#f5f5f5;border:1px solid #ddd;break-inside:avoid}
.post-card{background:#fafafa;border-right:3px solid #e94560}
.post-link{color:#0066cc}}
"""
            html = f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="theme-color" content="#e94560">
<title>تقرير OSINT — @{username}</title>
<style>{css}</style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>🕵️ تقرير OSINT — @{username}</h1>
        <div class="date">📅 {ts} — {oar}</div>
    </div>

    <section class="section">
        <h2 class="section-title">👤 معلومات الحساب</h2>
        <div class="profile-grid">
            <div>{pic_html}</div>
            <div class="profile-info">
                <h2>@{ai.get('username','')}</h2>
                <div class="fullname">{ai.get('full_name','')}</div>
                <div class="stats-grid">
                    <div class="stat-card"><div class="stat-number">{ai.get('posts_count',0):,}</div><div class="stat-label">منشور</div></div>
                    <div class="stat-card"><div class="stat-number">{ai.get('followers',0):,}</div><div class="stat-label">متابع</div></div>
                    <div class="stat-card"><div class="stat-number">{ai.get('following',0):,}</div><div class="stat-label">يتابع</div></div>
                    <div class="stat-card"><div class="stat-number">{'نعم' if ai.get('is_verified') else 'لا'}</div><div class="stat-label">موثق</div></div>
                    <div class="stat-card"><div class="stat-number">{'نعم' if ai.get('is_private') else 'لا'}</div><div class="stat-label">خاص</div></div>
                </div>
                {'<div class="bio"><b>السيرة:</b><br>' + str(ai.get('bio','')) + '</div>' if ai.get('bio') else ''}
            </div>
        </div>
    </section>

    {highlights_html}

    {stories_html}

    <section class="section">
        <h2 class="section-title">👥 المتابعون ({len(fl)})</h2>
        {fl_html}
    </section>

    <section class="section">
        <h2 class="section-title">📸 المنشورات ({len(posts)})</h2>
        {posts_html}
    </section>

    <div class="footer">Instagram OSINT Scraper ULTRA v2 — Cache ذكي ({len(ThumbnailEngine._url_cache)} صورة)</div>
</div>
</body>
</html>"""

            f = os.path.join(REPORTS_PATH, f"{username}_report_{datetime.now().strftime('%Y%m%d_%H%M')}.html")
            with open(f, 'w', encoding='utf-8') as fp: fp.write(html)
            size_kb = os.path.getsize(f) / 1024
            print(f"📄 تقرير HTML: {f} ({size_kb:.1f} KB)")
            return f
        except Exception as e:
            print(f"⚠️ HTML: {e}"); traceback.print_exc(); return None

# ============================================================
# 10. الواجهة
# ============================================================
# تنفيذ Instagram واحد في كل مرة لكل جلسة Colab.
# يمنع تداخل /start و /search على نفس client.
INSTAGRAM_JOB_LOCK = threading.Lock()

app = Flask(__name__)

HTML_PAGE = """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="theme-color" content="#e94560">
<title>Instagram OSINT Scraper ULTRA v2</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html{font-size:16px}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Tahoma,Arial,sans-serif;
background:linear-gradient(135deg,#1a1a2e 0%,#16213e 50%,#0f3460 100%);
background-attachment:fixed;min-height:100vh;padding:clamp(10px,2.5vw,24px);
display:flex;justify-content:center;align-items:flex-start;-webkit-font-smoothing:antialiased}
.card{background:rgba(255,255,255,0.98);padding:clamp(18px,4vw,32px);
border-radius:clamp(12px,2.5vw,20px);box-shadow:0 20px 60px rgba(0,0,0,0.5);
width:100%;max-width:620px;margin-bottom:20px}
h1{color:#0f3460;font-size:clamp(1.15rem,3.5vw,1.6rem);text-align:center;margin-bottom:6px;line-height:1.3}
.sub{color:#666;font-size:clamp(0.75rem,2vw,0.9rem);text-align:center;margin-bottom:clamp(16px,3vw,24px)}
.sec{margin-bottom:clamp(14px,2.5vw,20px)}
.st{color:#0f3460;font-weight:700;font-size:clamp(0.8rem,2.2vw,0.95rem);
margin-bottom:clamp(8px,1.5vw,12px);padding-bottom:clamp(5px,1vw,8px);border-bottom:2px solid #e94560}
input[type="text"],input[type="number"]{width:100%;padding:clamp(10px,2vw,13px);
margin:5px 0;border:2px solid #e0e0e0;border-radius:clamp(6px,1.5vw,10px);
font-size:clamp(0.85rem,2.2vw,1rem);box-sizing:border-box;font-family:inherit}
input:focus{border-color:#e94560;outline:none}
textarea{width:100%;padding:clamp(10px,2vw,13px);margin:5px 0;border:2px solid #e0e0e0;
border-radius:clamp(6px,1.5vw,10px);font-size:clamp(0.85rem,2.2vw,1rem);
box-sizing:border-box;font-family:inherit;resize:vertical;min-height:80px}
textarea:focus{border-color:#e94560;outline:none}
.opt{display:flex;align-items:center;gap:10px;padding:clamp(9px,1.8vw,12px);
background:#f8f9fa;border-radius:clamp(6px,1.5vw,10px);margin:5px 0;cursor:pointer;border:2px solid transparent}
.opt:hover{background:#e9ecef}
.opt input[type="checkbox"]{width:clamp(16px,4vw,20px);height:clamp(16px,4vw,20px);cursor:pointer;flex-shrink:0}
.opt label{cursor:pointer;font-size:clamp(0.8rem,2.1vw,0.9rem);color:#333;flex:1;line-height:1.4}
.hint{font-size:clamp(0.65rem,1.7vw,0.75rem);color:#888;margin-right:clamp(22px,5vw,30px);
margin-top:3px;display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.hint input[type="number"]{width:clamp(60px,15vw,80px);padding:5px 8px;margin:0;border:1px solid #ddd;font-size:clamp(0.7rem,1.8vw,0.8rem)}
.mg{display:flex;flex-direction:column;gap:clamp(6px,1.2vw,9px)}
.mo{display:flex;align-items:flex-start;gap:clamp(10px,2vw,14px);
padding:clamp(10px,2vw,14px);background:#f8f9fa;border-radius:clamp(7px,1.5vw,10px);
cursor:pointer;border:2px solid transparent;transition:0.2s}
.mo:hover{background:#e9ecef}
.mo input[type="radio"]{margin-top:4px;width:clamp(16px,4vw,20px);height:clamp(16px,4vw,20px);cursor:pointer;flex-shrink:0;accent-color:#e94560}
.mo.selected{background:rgba(233,69,96,0.08);border-color:#e94560}
.mc{flex:1;min-width:0}
.mt{font-weight:700;font-size:clamp(0.85rem,2.2vw,1rem);color:#0f3460;margin-bottom:3px;line-height:1.3}
.md{font-size:clamp(0.7rem,1.9vw,0.82rem);color:#666;line-height:1.5}
.rg{display:flex;gap:clamp(6px,1.2vw,10px);margin:5px 0}
.ro{flex:1;padding:clamp(9px,1.8vw,12px);background:#f8f9fa;border-radius:clamp(6px,1.5vw,10px);
text-align:center;cursor:pointer;border:2px solid transparent;transition:0.2s;font-size:clamp(0.75rem,2vw,0.9rem)}
.ro:hover{background:#e9ecef}
.ro input{display:none}
.ro:has(input:checked){background:rgba(233,69,96,0.1);border-color:#e94560;color:#e94560;font-weight:700}
.warn{background:#fff3cd;border:1px solid #ffc107;color:#856404;padding:clamp(9px,1.8vw,12px);
border-radius:8px;font-size:clamp(0.7rem,1.8vw,0.8rem);margin-top:8px;display:none;line-height:1.6}
.warn.show{display:block}
button{width:100%;padding:clamp(12px,2.5vw,16px);background:linear-gradient(135deg,#e94560,#c73659);
border:none;border-radius:clamp(8px,1.8vw,12px);color:white;font-size:clamp(0.9rem,2.5vw,1.1rem);
font-weight:700;cursor:pointer;transition:0.3s;margin-top:clamp(8px,1.5vw,12px);font-family:inherit;
box-shadow:0 4px 15px rgba(233,69,96,0.3)}
button:hover{transform:translateY(-2px);box-shadow:0 8px 25px rgba(233,69,96,0.4)}
button.secondary{background:linear-gradient(135deg,#10b981,#059669);
box-shadow:0 4px 15px rgba(16,185,129,0.3)}
button.secondary:hover{box-shadow:0 8px 25px rgba(16,185,129,0.4)}
.status{margin-top:clamp(12px,2vw,16px);padding:clamp(10px,2vw,14px);background:#d4edda;
border-radius:8px;color:#155724;font-size:clamp(0.75rem,1.9vw,0.85rem);text-align:center;line-height:1.5}
.tabs{display:flex;gap:8px;margin-bottom:clamp(14px,3vw,20px);
border-bottom:2px solid #e0e0e0;padding-bottom:0}
.tab{flex:1;padding:10px 14px;background:transparent;border:none;cursor:pointer;
font-size:clamp(0.8rem,2.1vw,0.95rem);font-weight:600;color:#666;
border-bottom:3px solid transparent;margin-bottom:-2px;transition:0.2s;font-family:inherit}
.tab.active{color:#e94560;border-bottom-color:#e94560}
.tab-content{display:none}
.tab-content.active{display:block}
.feats{margin-top:clamp(12px,2vw,16px);background:#f8f9fa;padding:clamp(12px,2vw,16px);border-radius:10px}
.feats ul{list-style:none;padding:0;margin:0}
.feats li{color:#333;margin:5px 0;font-size:clamp(0.7rem,1.8vw,0.82rem);line-height:1.5}
.badge{display:inline-block;background:#e94560;color:white;padding:2px 8px;border-radius:10px;
font-size:clamp(0.55rem,1.4vw,0.65rem);margin-right:5px;font-weight:600}
.badge-new{background:#10b981}
.search-examples{background:#fff8e1;padding:10px;border-radius:8px;margin-top:8px;
font-size:clamp(0.7rem,1.8vw,0.8rem);color:#795548;line-height:1.6}
.search-examples b{color:#e94560}
</style>
</head>
<body>
<div>
<div class="card">
    <h1>🕵️ Instagram OSINT Scraper ULTRA v2</h1>
    <p class="sub">✅ Cache ذكي + صور مصغرة مدمجة + بحث شامل</p>
    
    <div class="tabs">
        <button class="tab active" onclick="showTab('scrape', this)">📊 سكراب مستخدم</button>
        <button class="tab" onclick="showTab('search', this)">🔍 بحث شامل</button>
    </div>
    
    <div id="scrape-tab" class="tab-content active">
        <form id="f" action="/start" method="post">
            <div class="sec"><div class="st">📌 البيانات الأساسية</div>
                <input type="text" name="username" placeholder="اسم المستخدم (بدون @)" required>
                <input type="number" name="max_posts" placeholder="عدد المنشورات" value="10" min="1" max="500">
            </div>
            <div class="sec"><div class="st">📋 وضع السكراب</div>
                <div class="mg">
                    <label class="mo" onclick="sm(this)">
                        <input type="radio" name="scrape_mode" value="posts_only">
                        <div class="mc"><div class="mt">📸 المنشورات فقط</div>
                        <div class="md">من القسم الرئيسي فقط.</div></div></label>
                    <label class="mo" onclick="sm(this)">
                        <input type="radio" name="scrape_mode" value="posts_and_videos">
                        <div class="mc"><div class="mt">📸🎬 المنشورات + الفيديوهات</div>
                        <div class="md">من القسمين بشكل منفصل.</div></div></label>
                    <label class="mo selected" onclick="sm(this)">
                        <input type="radio" name="scrape_mode" value="smart_merge" checked>
                        <div class="mc"><div class="mt">🧠 الدمج الذكي <span class="badge">الأقوى</span></div>
                        <div class="md">دمج + إزالة تكرار + فرز + أخذ العدد المطلوب.</div></div></label>
                </div>
            </div>
            <div class="sec"><div class="st">📅 الترتيب</div>
                <div class="rg">
                    <label class="ro"><input type="radio" name="order" value="desc" checked onchange="tw()">الأحدث أولاً</label>
                    <label class="ro"><input type="radio" name="order" value="asc" onchange="tw()">الأقدم أولاً</label>
                </div>
                <div class="warn" id="w">⚡ <strong>cursor hopping:</strong> نجمع الكودات فقط ثم نحمّل أقدم N فقط.</div>
            </div>
            <div class="sec"><div class="st">⚙️ خيارات إضافية</div>
                <div class="opt"><input type="checkbox" name="fetch_stories" value="1" id="fs">
                    <label for="fs">📖 جلب القصص المؤقتة (Stories 24h)</label></div>
                <div class="hint"><span>الحد الأقصى:</span>
                    <input type="number" name="max_stories" value="50" min="1" max="200"> قصة</div>

                <div class="opt" style="margin-top:8px"><input type="checkbox" name="fetch_highlights" value="1" id="fh">
                    <label for="fh">📌 جلب الاستوري المميز (Highlights)</label></div>
                <div class="hint"><span>الحد الأقصى:</span>
                    <input type="number" name="max_highlights" value="20" min="1" max="100"> Highlight</div>

                <div class="opt" style="margin-top:8px"><input type="checkbox" name="fetch_comments" value="1" id="c">
                    <label for="c">💬 جلب التعليقات</label></div>
                <div class="hint"><span>لكل منشور:</span>
                    <input type="number" name="max_comments" value="20" min="1" max="200"> تعليق</div>

                <div class="opt" style="margin-top:8px"><input type="checkbox" name="fetch_followers" value="1" id="fo">
                    <label for="fo">👥 جلب المتابعين</label></div>
                <div class="hint"><span>الحد الأقصى:</span>
                    <input type="number" name="max_followers" value="100" min="1" max="1000"> متابع</div>
            </div>
            <button type="submit">🚀 بدء السكراب</button>
        </form>
    </div>
    
    <div id="search-tab" class="tab-content">
        <form id="fs" action="/search" method="post">
            <div class="sec">
                <div class="st">🔍 محرك البحث الشامل</div>
                <textarea name="query" placeholder="ابحث عن أي شيء... اسم، جملة، هاشتاج، شخص..." required></textarea>
                <div class="search-examples">
                    <b>أمثلة:</b><br>
                    <code>#مصر</code> — بحث عن هاشتاج<br>
                    <code>@cristiano</code> — بحث عن مستخدم<br>
                    <code>محمد صلاح ليفربول</code> — بحث شامل (جمل)<br>
                    <code>أغنية عمرو دياب</code> — بحث عن أغنية + مستخدم<br>
                    <code>اسم الشخص</code> — بحث عن أين ذُكر هذا الاسم
                </div>
            </div>
            <div class="sec">
                <div class="st">⚙️ إعدادات البحث</div>
                <div class="hint"><span>الحد الأقصى لكل نوع:</span>
                    <input type="number" name="max_per_type" value="10" min="1" max="50"> عنصر</div>
                <div class="opt" style="margin-top:8px"><input type="checkbox" name="hunt_mentions" value="1" id="hm" checked>
                    <label for="hm">🎯 تفعيل Mention Hunter (البحث عن الإشارات)</label></div>
                <div class="opt"><input type="checkbox" name="generate_report" value="1" id="gr" checked>
                    <label for="gr">📄 إنشاء تقرير HTML شامل</label></div>
            </div>
            <button type="submit" class="secondary">🔍 بدء البحث الشامل</button>
        </form>
    </div>
    
    <div class="status">✅ ستصلك النتائج + تقارير HTML مضغوطة على تليجرام<br>
    <b>Cache ذكي</b>: نفس الصورة لا تُحمّل مرتين</div>
    <div class="feats"><div class="st" style="border-color:#0f3460;margin-top:0">✨ الميزات</div>
        <ul>
            <li>💾 <b>User Cache</b> — منع 429 بشكل فعال</li>
            <li>🖼️ <b>Thumb Cache</b> — نفس الصورة تُحمّل مرة واحدة</li>
            <li>🎨 صور مصغرة مدمجة (3-8KB)</li>
            <li>🔍 بحث شامل + Mention Hunter</li>
            <li>🛡️ Fallback methods للأخطاء</li>
            <li>⚡ traversal سريع (cursor hopping)</li>
            <li>📖 قصص + 📌 Highlights</li>
        </ul>
    </div>
</div>
</div>
<script>
function sm(el){
    document.querySelectorAll('.mo').forEach(o=>o.classList.remove('selected'));
    el.classList.add('selected');
    el.querySelector('input').checked=true;
}
function tw(){
    const a=document.querySelector('input[name="order"][value="asc"]').checked;
    document.getElementById('w').classList.toggle('show',a);
}
function showTab(tabName, btn){
    document.querySelectorAll('.tab-content').forEach(c=>c.classList.remove('active'));
    document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
    document.getElementById(tabName+'-tab').classList.add('active');
    btn.classList.add('active');
}
</script>
</body>
</html>"""

def safe_int(value, default=0, minimum=None, maximum=None):
    try:
        v = int(str(value).strip() or default)
    except (ValueError, TypeError):
        v = default
    if minimum is not None and v < minimum: v = minimum
    if maximum is not None and v > maximum: v = maximum
    return v

@app.route('/')
def index(): return render_template_string(HTML_PAGE)

@app.route('/start', methods=['POST'])
def start_scrape():
    u = request.form.get('username', '').strip()
    mp = safe_int(request.form.get('max_posts'), 10, 1, 500)
    sm_ = request.form.get('scrape_mode', 'smart_merge') or 'smart_merge'
    o = request.form.get('order', 'desc') or 'desc'
    fc = request.form.get('fetch_comments') == '1'
    mc = safe_int(request.form.get('max_comments'), 20, 1, 200)
    ff = request.form.get('fetch_followers') == '1'
    mf = safe_int(request.form.get('max_followers'), 100, 1, 1000)
    fs = request.form.get('fetch_stories') == '1'
    ms = safe_int(request.form.get('max_stories'), 50, 1, 200)
    fh = request.form.get('fetch_highlights') == '1'
    mh = safe_int(request.form.get('max_highlights'), 20, 1, 100)

    if not u: return "❌ اسم المستخدم مطلوب", 400

    def task():
        if not INSTAGRAM_JOB_LOCK.acquire(blocking=False):
            TelegramSender.send_message(
                "⏳ <b>هناك مهمة Instagram أخرى قيد التنفيذ</b>\n"
                "تم رفض المهمة الجديدة مؤقتًا لمنع تداخل الطلبات و429."
            )
            return
        try:
            searcher = InstagramSearcher()
            searcher.get_profile_info(u)
            searcher.scrape(u, mp, sm_, o, fc, mc, ff, mf, fs, ms, fh, mh)
        except Exception as e:
            traceback.print_exc()
            TelegramSender.send_message(f"❌ {html_escape(str(e)[:800])}")
        finally:
            INSTAGRAM_JOB_LOCK.release()

    threading.Thread(target=task).start()
    mn = {'posts_only': '📸 منشورات', 'posts_and_videos': '📸🎬+فيديو', 'smart_merge': '🧠 دمج'}
    opts = [mn.get(sm_, sm_), 'الأحدث' if o == 'desc' else 'الأقدم']
    if fs: opts.append(f'ستوري({ms})')
    if fh: opts.append(f'Highlights({mh})')
    return f"✅ @{u} — {', '.join(opts)} — {mp} منشور", 200

@app.route('/search', methods=['POST'])
def start_search():
    query = request.form.get('query', '').strip()
    max_per_type = safe_int(request.form.get('max_per_type'), 10, 1, 50)
    hunt_mentions = request.form.get('hunt_mentions') == '1'
    generate_report = request.form.get('generate_report') == '1'
    
    if not query: return "❌ الاستعلام مطلوب", 400

    def task():
        if not INSTAGRAM_JOB_LOCK.acquire(blocking=False):
            TelegramSender.send_message(
                "⏳ <b>هناك مهمة Instagram أخرى قيد التنفيذ</b>\n"
                "تم رفض البحث الجديد مؤقتًا لمنع تداخل الطلبات و429."
            )
            return
        try:
            searcher = InstagramSearcher()
            results = searcher.universal_searcher.search(
                query, max_per_type, hunt_mentions=hunt_mentions
            )
            
            if generate_report:
                searcher.universal_searcher.generate_search_report(query, results)
            
            TelegramSender.send_message(f"✅ *اكتمل البحث*\n📝 `{query}`\n📊 إجمالي النتائج: {results['stats']['total']}\n💾 Cache: {len(ThumbnailEngine._url_cache)} صورة")
        except Exception as e:
            traceback.print_exc()
            TelegramSender.send_message(f"❌ خطأ في البحث: {html_escape(str(e)[:800])}")
        finally:
            INSTAGRAM_JOB_LOCK.release()

    threading.Thread(target=task).start()
    return f"✅ جاري البحث الشامل عن: {query}", 200

# ============================================================
# 11. التشغيل
# ============================================================
if NGROK_AUTH_TOKEN and not NGROK_AUTH_TOKEN.startswith("ضع_"):
    ngrok.set_auth_token(NGROK_AUTH_TOKEN)
    print("✅ ngrok")

init_db()
print(f"📦 instagrapi version: {getattr(instagrapi, '__version__', '?')}")
print("🔐 تهيئة الجلسة...")
InstagramClient.get_client()

print("\n🚀 تشغيل السيرفر...")
url = ngrok.connect(5000).public_url
print("="*60)
print(f"🌐 {url}")
print("="*60)
print("✨ الميزات:")
print(f"   🎨 صور مصغرة مدمجة ({THUMB_MAX_SIZE}px, quality {THUMB_QUALITY})")
print("   💾 Cache ذكي للمستخدمين (يمنع 429)")
print("   🖼️ Cache ذكي للصور (يمنع التحميل المتكرر)")
print("   🔍 بحث شامل + Mention Hunter")
print("   🛡️ Fallback methods لكل دالة")
print("="*60)

app.run(host='0.0.0.0', port=5000)
