# ============================================================
# 🕵️ Instagram OSINT Scraper ULTRA v7.1 (FIXED — Drive Enabled)
# ✅ instadata + SQLite تراكمية + تقارير HTML/CSV
# ✅ Telegram Rich Messages (Bot API 10.1+)
# ✅ منع تكرار دقيق + نسخ الوسائط إلى Google Drive
# ============================================================

# ============================================================
# 1. الإعدادات
# ============================================================
NGROK_AUTH_TOKEN = ""
IG_USERNAME = ""
IG_PASSWORD = ""

DEFAULT_MODE = "anonymous"

TELEGRAM_TOKEN = ""
TELEGRAM_CHAT_ID = ""

THUMB_MAX_SIZE = 150
THUMB_QUALITY = 40
THUMB_OPTIMIZE = True

# ============================================================
# 2. الاستيرادات (قبل أي مسار — لأننا سنركّب Drive)
# ============================================================
import os, shutil, json, io, time, random, requests, threading, sqlite3, traceback, csv, re, base64, subprocess, sys, tempfile, glob, secrets
from datetime import datetime
from html import escape as html_escape
from flask import Flask, request, render_template_string, jsonify
from pyngrok import ngrok

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("⚠️ PIL غير متوفر")

# ============================================================
# 3. تركيب Google Drive — إلزامي قبل أي مسار دائم
# ============================================================
IN_COLAB = False
try:
    from google.colab import drive
    IN_COLAB = True
except ImportError:
    drive = None
    print("⚠️ لسنا داخل Colab — سيتم استخدام مسار محلي")

if IN_COLAB:
    if not os.path.exists("/content/drive/MyDrive"):
        print("📂 تركيب Google Drive...")
        drive.mount("/content/drive")
    else:
        print("✅ Google Drive مركّب مسبقاً")
else:
    print("⚠️ خارج Colab: سيتم حفظ البيانات في المجلد المحلي")

# الآن — بعد التركيب — نحدد المسارات
BASE_PATH = "/content/drive/MyDrive/Instagram_Scraper_DB" if IN_COLAB else os.path.join(os.getcwd(), "Instagram_Scraper_DB")
DB_PATH = os.path.join(BASE_PATH, "instagram_data.db")
MEDIA_PATH = os.path.join(BASE_PATH, "media")
REPORTS_PATH = os.path.join(BASE_PATH, "reports")
STORIES_PATH = os.path.join(BASE_PATH, "stories")
HIGHLIGHTS_PATH = os.path.join(BASE_PATH, "highlights")
DB_BACKUP_PATH = os.path.join(BASE_PATH, "db_backups")

# ============================================================
# 4. إنشاء المجلدات والتحقق من الكتابة الفعلية على Drive
# ============================================================
for folder in (BASE_PATH, MEDIA_PATH, REPORTS_PATH, STORIES_PATH, HIGHLIGHTS_PATH, DB_BACKUP_PATH):
    os.makedirs(folder, exist_ok=True)

# اختبار كتابة فعلي على Drive
def verify_drive_writable():
    try:
        test_file = os.path.join(BASE_PATH, f".write_test_{secrets.token_hex(4)}.tmp")
        with open(test_file, "w") as f:
            f.write("ok")
        os.remove(test_file)
        print(f"✅ التحقق من الكتابة على Drive: نجح")
        print(f"   📁 BASE_PATH = {BASE_PATH}")
        print(f"   📁 DB_PATH   = {DB_PATH}")
        return True
    except Exception as e:
        print(f"❌ فشل الكتابة على Drive: {e}")
        return False

verify_drive_writable()

_http_session = requests.Session()
_http_session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
})

# ============================================================
# 5. الحالة العالمية + المسارات المؤقتة
# ============================================================
class AppState:
    mode = DEFAULT_MODE
    login_in_progress = False
    login_error = ""
    last_mode_change = 0.0
    lock = threading.Lock()

STATE = AppState()

TEMP_ROOT = tempfile.mkdtemp(prefix="ig_osint_v7_")
TEMP_INSTADATA = os.path.join(TEMP_ROOT, "instadata")
TEMP_MEDIA = os.path.join(TEMP_ROOT, "media")
TEMP_COOKIES = os.path.join(TEMP_ROOT, "cookies.json")
TEMP_REPORTS = os.path.join(TEMP_ROOT, "reports")

for p in (TEMP_INSTADATA, TEMP_MEDIA, TEMP_REPORTS):
    os.makedirs(p, exist_ok=True)

print(f"📁 TEMP: {TEMP_ROOT}")
print(f"📁 DB:   {DB_PATH}")

def cleanup_temp():
    try:
        shutil.rmtree(TEMP_ROOT, ignore_errors=True)
        print(f"🗑️ تم حذف المجلد المؤقت: {TEMP_ROOT}")
    except Exception as e:
        print(f"⚠️ فشل حذف المجلد المؤقت: {e}")

# ============================================================
# 6. قاعدة البيانات SQLite التراكمية
# ============================================================
def db_connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def table_exists(cur, table):
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None

def table_columns(cur, table):
    cur.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}

def backup_database(reason="migration"):
    if not os.path.exists(DB_PATH):
        return None
    try:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = os.path.join(DB_BACKUP_PATH, f"instagram_data_before_{re.sub(r'[^A-Za-z0-9_-]', '_', reason)}_{stamp}.sqlite")
        src = db_connect()
        dst = sqlite3.connect(dest)
        with dst:
            src.backup(dst)
        dst.close(); src.close()
        print(f"🛡️ DB backup: {dest}")
        return dest
    except Exception as exc:
        print(f"⚠️ DB backup: {exc}")
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
            last_scraped DATETIME,
            is_business INTEGER DEFAULT 0,
            category TEXT DEFAULT '',
            external_url TEXT DEFAULT '',
            profile_pic TEXT DEFAULT ''
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
            story_url TEXT DEFAULT '',
            downloaded BOOLEAN DEFAULT 0,
            thumbnail_url TEXT DEFAULT '',
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

    cur.execute("CREATE INDEX IF NOT EXISTS idx_posts_account_taken ON posts(account_id, taken_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_followers_account ON followers(account_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_following_account ON following(account_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_stories_account ON stories(account_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_highlights_account ON highlights(account_id)")

    conn.commit()
    conn.close()
    print(f"✅ قاعدة البيانات جاهزة على Drive: {DB_PATH}")

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
        str(info.get("username", "")).lower().lstrip("@"),
        str(info.get("full_name", "")),
        str(info.get("bio", "")),
        int(info.get("followers", 0) or 0),
        int(info.get("following", 0) or 0),
        int(info.get("posts_count", 0) or 0),
        int(bool(info.get("is_private", False))),
        int(bool(info.get("is_verified", False))),
        datetime.now().isoformat(timespec="seconds"),
        int(bool(info.get("is_business", False))),
        str(info.get("category", "")),
        str(info.get("external_url", "")),
        str(info.get("profile_pic", "")),
    ))
    conn.commit()
    conn.close()

def get_account_id(username):
    conn = db_connect()
    row = conn.execute("SELECT id FROM accounts WHERE username=?", (str(username).lower().lstrip("@"),)).fetchone()
    conn.close()
    return row[0] if row else None

def get_account_info(username):
    conn = db_connect()
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM accounts WHERE username=?", (str(username).lower().lstrip("@"),)).fetchone()
    conn.close()
    return dict(row) if row else None

def post_exists(post_code):
    if not post_code:
        return False
    conn = db_connect()
    row = conn.execute("SELECT id FROM posts WHERE post_code=?", (str(post_code),)).fetchone()
    conn.close()
    return row is not None

def save_post_if_not_exists(account_username, media):
    username = str(account_username).lower().lstrip("@")
    code = str(media.get("shortcode") or media.get("code") or "")
    if not code:
        return None, False

    conn = db_connect()
    cur = conn.cursor()
    account = cur.execute("SELECT id FROM accounts WHERE username=?", (username,)).fetchone()
    if not account:
        conn.close()
        return None, False

    account_id = account[0]

    existing = cur.execute("SELECT id FROM posts WHERE post_code=?", (code,)).fetchone()
    if existing:
        cur.execute("""
            UPDATE posts SET
                like_count=COALESCE(?, like_count),
                comment_count=COALESCE(?, comment_count),
                view_count=COALESCE(?, view_count),
                play_count=COALESCE(?, play_count),
                caption=COALESCE(?, caption),
                thumbnail_url=COALESCE(?, thumbnail_url)
            WHERE id=?
        """, (
            int(media.get("like_count", 0) or 0) or None,
            int(media.get("comment_count", 0) or 0) or None,
            int(media.get("view_count", 0) or 0) or None,
            int(media.get("play_count", 0) or 0) or None,
            str(media.get("caption", "") or "")[:2000] or None,
            str(media.get("thumbnail_url", "") or "") or None,
            existing[0],
        ))
        conn.commit()
        conn.close()
        return existing[0], False

    taken = media.get("taken_at") or media.get("timestamp")
    taken_str = taken.isoformat() if hasattr(taken, "isoformat") else (str(taken) if taken else "")

    cur.execute("""
        INSERT INTO posts (
            account_id, post_code, post_url, media_type, caption,
            like_count, comment_count, view_count, play_count, taken_at,
            file_path, downloaded, thumbnail_url
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        account_id,
        code,
        f"https://www.instagram.com/p/{code}/",
        str(media.get("media_type", "") or ""),
        str(media.get("caption", "") or "")[:2000],
        int(media.get("like_count", 0) or 0),
        int(media.get("comment_count", 0) or 0),
        int(media.get("view_count", 0) or 0),
        int(media.get("play_count", 0) or 0),
        taken_str,
        "",
        0,
        str(media.get("thumbnail_url", "") or ""),
    ))
    post_id = cur.lastrowid
    conn.commit()
    conn.close()
    return post_id, True

def update_post_media(post_id, media_type, file_path, thumbnail_url=""):
    conn = db_connect()
    conn.execute("""
        UPDATE posts SET media_type=?, file_path=?, downloaded=?, thumbnail_url=?
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
        user = comment.get("user", {}) if isinstance(comment, dict) else {}
        username = str(user.get("username", "") or "")
        fullname = str(user.get("full_name", "") or "")
        text = str(comment.get("text", "") or "")
        created = str(comment.get("created_at_utc", "") or comment.get("created_at", "") or "")
        conn = db_connect()
        conn.execute("""
            INSERT INTO comments (post_id, comment_text, commenter_username, commenter_full_name, created_at)
            SELECT ?,?,?,?,?
            WHERE NOT EXISTS (
                SELECT 1 FROM comments WHERE post_id=? AND commenter_username=? AND created_at=? AND comment_text=?
            )
        """, (post_id, text, username, fullname, created, post_id, username, created, text))
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"⚠️ save_comment: {exc}")

def save_follower(account_id, user_obj):
    username = str(user_obj.get("username", "") or "")
    if not username:
        return
    fullname = str(user_obj.get("full_name", "") or "")
    try:
        conn = db_connect()
        conn.execute("""
            INSERT INTO followers(account_id, username, full_name, first_seen) VALUES (?,?,?,?)
            ON CONFLICT(account_id, username) DO UPDATE SET full_name=excluded.full_name
        """, (account_id, username, fullname, datetime.now().isoformat(timespec="seconds")))
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"⚠️ save_follower: {exc}")

def save_following(account_id, user_obj):
    username = str(user_obj.get("username", "") or "")
    if not username:
        return
    fullname = str(user_obj.get("full_name", "") or "")
    try:
        conn = db_connect()
        conn.execute("""
            INSERT INTO following(account_id, username, full_name, first_seen) VALUES (?,?,?,?)
            ON CONFLICT(account_id, username) DO UPDATE SET full_name=excluded.full_name
        """, (account_id, username, fullname, datetime.now().isoformat(timespec="seconds")))
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"⚠️ save_following: {exc}")

def save_story(account_id, story, file_path, story_url="", thumbnail_url=""):
    pk = str(story.get("pk") or story.get("id") or "")
    if not pk:
        return False
    media_type = "video" if story.get("video_url") else "image"
    taken = story.get("taken_at")
    taken_str = taken.isoformat() if hasattr(taken, "isoformat") else str(taken or "")
    try:
        conn = db_connect()
        conn.execute("""
            INSERT INTO stories(account_id, story_pk, media_type, taken_at, file_path, story_url, downloaded, thumbnail_url)
            VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(story_pk) DO UPDATE SET
                account_id=excluded.account_id, media_type=excluded.media_type,
                taken_at=excluded.taken_at, file_path=excluded.file_path,
                story_url=excluded.story_url, downloaded=excluded.downloaded,
                thumbnail_url=excluded.thumbnail_url
        """, (account_id, pk, media_type, taken_str, str(file_path or ""),
              str(story_url or ""), int(bool(file_path and os.path.exists(file_path))), str(thumbnail_url or "")))
        conn.commit()
        conn.close()
        return True
    except Exception as exc:
        print(f"⚠️ save_story: {exc}")
        return False

def save_highlight_record(account_id, highlight, cover_path=""):
    pk = str(highlight.get("pk") or highlight.get("id") or "")
    if not pk:
        return None
    try:
        conn = db_connect()
        conn.execute("""
            INSERT INTO highlights(account_id, highlight_pk, title, media_count, cover_url, cover_path, downloaded)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(highlight_pk) DO UPDATE SET
                account_id=excluded.account_id, title=excluded.title,
                media_count=excluded.media_count, cover_url=excluded.cover_url,
                cover_path=excluded.cover_path, downloaded=excluded.downloaded
        """, (account_id, pk, str(highlight.get("title", "") or ""),
              int(highlight.get("media_count", 0) or 0),
              str(highlight.get("cover_url", "") or ""), str(cover_path or ""),
              int(bool(cover_path and os.path.exists(cover_path)))))
        hid = conn.execute("SELECT id FROM highlights WHERE highlight_pk=?", (pk,)).fetchone()[0]
        conn.commit()
        conn.close()
        return hid
    except Exception as exc:
        print(f"⚠️ save_highlight: {exc}")
        return None

def save_highlight_item(highlight_id, item, file_path=""):
    pk = str(item.get("pk") or item.get("id") or "")
    if not pk:
        return False
    taken = item.get("taken_at")
    taken_str = taken.isoformat() if hasattr(taken, "isoformat") else str(taken or "")
    media_type = "video" if item.get("video_url") else "image"
    try:
        conn = db_connect()
        conn.execute("""
            INSERT INTO highlight_items(highlight_id, item_pk, media_type, taken_at, file_path, video_url, downloaded)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(item_pk) DO UPDATE SET
                highlight_id=excluded.highlight_id, media_type=excluded.media_type,
                taken_at=excluded.taken_at, file_path=excluded.file_path,
                video_url=excluded.video_url, downloaded=excluded.downloaded
        """, (highlight_id, pk, media_type, taken_str, str(file_path or ""),
              str(item.get("video_url", "") or ""),
              int(bool(file_path and os.path.exists(file_path)))))
        conn.commit()
        conn.close()
        return True
    except Exception as exc:
        print(f"⚠️ save_highlight_item: {exc}")
        return False

def save_mention(query, source_type, source_pk, source_url, context, mentioned_username="", mentioned_full_name=""):
    try:
        conn = db_connect()
        conn.execute("""
            INSERT INTO mentions(query, source_type, source_pk, source_url, context, mentioned_username, mentioned_full_name, created_at)
            VALUES(?,?,?,?,?,?,?,?)
        """, (str(query), source_type, str(source_pk or ""), str(source_url or ""),
              str(context or "")[:1000], str(mentioned_username or ""),
              str(mentioned_full_name or ""), datetime.now().isoformat(timespec="seconds")))
        conn.commit()
        conn.close()
        return True
    except Exception as exc:
        print(f"⚠️ save_mention: {exc}")
        return False

def get_posts_with_comments(account_id, limit=None, order="desc"):
    conn = db_connect()
    conn.row_factory = sqlite3.Row
    direction = "DESC" if order == "desc" else "ASC"
    limit_sql = f"LIMIT {int(limit)}" if limit else ""
    rows = conn.execute(
        f"SELECT * FROM posts WHERE account_id=? ORDER BY taken_at {direction} {limit_sql}",
        (account_id,)).fetchall()
    posts = [dict(row) for row in rows]
    for post in posts:
        comments = conn.execute("SELECT * FROM comments WHERE post_id=? ORDER BY created_at ASC", (post["id"],)).fetchall()
        post["comments"] = [dict(c) for c in comments]
    conn.close()
    return posts

def get_followers(account_id, limit=None):
    conn = db_connect()
    limit_sql = f"LIMIT {int(limit)}" if limit else ""
    rows = conn.execute(f"SELECT * FROM followers WHERE account_id=? ORDER BY first_seen ASC {limit_sql}", (account_id,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_following(account_id, limit=None):
    conn = db_connect()
    limit_sql = f"LIMIT {int(limit)}" if limit else ""
    rows = conn.execute(f"SELECT * FROM following WHERE account_id=? ORDER BY first_seen ASC {limit_sql}", (account_id,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_stories(account_id):
    conn = db_connect()
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM stories WHERE account_id=? ORDER BY taken_at DESC", (account_id,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_highlights(account_id):
    conn = db_connect()
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM highlights WHERE account_id=? ORDER BY title ASC", (account_id,)).fetchall()
    result = [dict(row) for row in rows]
    for item in result:
        rows2 = conn.execute("SELECT * FROM highlight_items WHERE highlight_id=? ORDER BY taken_at ASC", (item["id"],)).fetchall()
        item["items"] = [dict(x) for x in rows2]
    conn.close()
    return result

# ============================================================
# 7. Telegram — Rich Messages (Bot API 10.1+)
# ============================================================
class TelegramSender:
    @staticmethod
    def send_message(text, retries=2):
        for i in range(retries + 1):
            try:
                r = _http_session.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                    data={"chat_id": TELEGRAM_CHAT_ID, "text": text[:4096],
                          "parse_mode": "Markdown", "disable_web_page_preview": "true"}, timeout=15)
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
                "is_rtl": True,
                "skip_entity_detection": False
            }, ensure_ascii=False),
            "disable_notification": False,
        }
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        for i in range(retries + 1):
            try:
                r = _http_session.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendRichMessage",
                    data=payload, timeout=25)
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
                data={"chat_id": TELEGRAM_CHAT_ID,
                      "text": TelegramSender._strip_rich_html(html_text)[:4096],
                      "parse_mode": "HTML", "disable_web_page_preview": "false"}, timeout=15)
            return r.status_code == 200
        except Exception as e:
            print(f"⚠️ Rich fallback: {e}")
            return False

    @staticmethod
    def send_message_html(html_text, retries=2):
        for i in range(retries + 1):
            try:
                r = _http_session.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                    data={"chat_id": TELEGRAM_CHAT_ID, "text": html_text[:4096],
                          "parse_mode": "HTML", "disable_web_page_preview": "true"}, timeout=15)
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
    def _strip_rich_html(html_text):
        t = re.sub(r"<details[^>]*>", "", html_text, flags=re.I)
        t = re.sub(r"</details>", "", t, flags=re.I)
        t = re.sub(r"<summary[^>]*>.*?</summary>", "", t, flags=re.I | re.S)
        t = re.sub(r"<table[^>]*>", "", t, flags=re.I)
        t = re.sub(r"</table>", "", t, flags=re.I)
        t = re.sub(r"<tr[^>]*>", "\n", t, flags=re.I)
        t = re.sub(r"</tr>", "", t, flags=re.I)
        t = re.sub(r"<t[hd][^>]*>", " | ", t, flags=re.I)
        t = re.sub(r"</t[hd]>", "", t, flags=re.I)
        t = re.sub(r"<[^>]+>", "", t)
        return t.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"')

    @staticmethod
    def _post_keyboard(url, profile_url=None):
        rows = [[{"text": "🔗 فتح المنشور", "url": url}]]
        if profile_url:
            rows.append([{"text": "👤 فتح الحساب", "url": profile_url}])
        return {"inline_keyboard": rows}

    @staticmethod
    def send_photo(photo_path, caption="", reply_markup=None):
        try:
            if not os.path.isfile(photo_path):
                print(f"⚠️ ليس ملفاً: {photo_path}")
                return False
            with open(photo_path, "rb") as f:
                payload = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption[:1024],
                           "parse_mode": "HTML", "show_caption_above_media": "true"}
                if reply_markup:
                    payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
                r = _http_session.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                    data=payload, files={"photo": f}, timeout=60)
                if r.status_code == 200:
                    return True
                payload["caption"] = TelegramSender._strip_rich_html(caption)[:1024]
                r = _http_session.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                    data=payload, files={"photo": f}, timeout=60)
                return r.status_code == 200
        except Exception as e:
            print(f"⚠️ صورة: {e}")
            return False

    @staticmethod
    def send_video(video_path, caption="", reply_markup=None):
        try:
            if not os.path.isfile(video_path):
                print(f"⚠️ ليس ملفاً: {video_path}")
                return False
            with open(video_path, "rb") as f:
                payload = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption[:1024],
                           "parse_mode": "HTML", "show_caption_above_media": "true",
                           "supports_streaming": "true"}
                if reply_markup:
                    payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
                r = _http_session.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendVideo",
                    data=payload, files={"video": f}, timeout=180)
                if r.status_code == 200:
                    return True
                payload["caption"] = TelegramSender._strip_rich_html(caption)[:1024]
                r = _http_session.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendVideo",
                    data=payload, files={"video": f}, timeout=180)
                return r.status_code == 200
        except Exception as e:
            print(f"⚠️ فيديو: {e}")
            return False

    @staticmethod
    def send_document(file_path, caption=""):
        try:
            if not os.path.isfile(file_path):
                return False
            with open(file_path, "rb") as f:
                r = _http_session.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument",
                    data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption[:1024], "parse_mode": "HTML"},
                    files={"document": f}, timeout=120)
                return r.status_code == 200
        except Exception as e:
            print(f"⚠️ ملف: {e}")
            return False

# ============================================================
# 8. Thumbnail Engine
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
            img.save(buffer, format='JPEG', quality=quality, optimize=THUMB_OPTIMIZE, progressive=True)
            b64 = base64.b64encode(buffer.getvalue()).decode()
            if len(cls._url_cache) >= cls._MAX_CACHE_SIZE:
                cls._url_cache.pop(next(iter(cls._url_cache)))
            cls._url_cache[cache_key] = b64
            return b64
        except Exception as e:
            print(f"      ⚠️ thumb_from_url: {e}")
            return None

    @staticmethod
    def from_file(file_path, max_size=THUMB_MAX_SIZE, quality=THUMB_QUALITY):
        if not file_path or not os.path.isfile(file_path) or not PIL_AVAILABLE:
            return None
        try:
            if file_path.lower().endswith(('.mp4', '.mov', '.webm')):
                return None
            img = Image.open(file_path)
            img.thumbnail((max_size, max_size), Image.LANCZOS)
            if img.mode in ('RGBA', 'P', 'LA'):
                img = img.convert('RGB')
            buffer = io.BytesIO()
            img.save(buffer, format='JPEG', quality=quality, optimize=THUMB_OPTIMIZE, progressive=True)
            return base64.b64encode(buffer.getvalue()).decode()
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
# 9. Helpers
# ============================================================
def _extract(obj, *keys, default=''):
    for k in keys:
        if isinstance(obj, dict):
            v = obj.get(k)
        else:
            v = getattr(obj, k, None)
        if v not in (None, ''):
            return v
    return default

def safe_int(value, default=0, minimum=None, maximum=None):
    try:
        v = int(str(value).strip() or default)
    except (ValueError, TypeError):
        v = default
    if minimum is not None and v < minimum: v = minimum
    if maximum is not None and v > maximum: v = maximum
    return v

def safe_name(value, fallback="item"):
    value = str(value or "").strip()
    value = re.sub(r"[^A-Za-z0-9_.@-]+", "_", value)
    return value[:120] or fallback

def now_iso():
    return datetime.now().isoformat(timespec="seconds")

# ============================================================
# ⭐ 10. دوال النسخ إلى Google Drive (الإصلاح الأساسي)
# ============================================================
def save_media_to_drive(src_path, username, subfolder="posts", new_name=None):
    """
    نسخ ملف وسائط من المجلد المؤقت إلى Google Drive.
    يُرجع المسار النهائي على Drive، أو None عند الفشل.
    """
    if not src_path or not os.path.isfile(src_path):
        return None
    try:
        # destination: /content/drive/MyDrive/Instagram_Scraper_DB/media/<username>/<subfolder>/
        dest_dir = os.path.join(MEDIA_PATH, safe_name(username), subfolder)
        os.makedirs(dest_dir, exist_ok=True)

        name = new_name or os.path.basename(src_path)
        dest = os.path.join(dest_dir, name)

        # إذا كان موجوداً بنفس الحجم، لا نعيد النسخ (توفير)
        if os.path.exists(dest) and os.path.getsize(dest) == os.path.getsize(src_path):
            return dest

        shutil.copy2(src_path, dest)
        print(f"      💾 Drive: {os.path.relpath(dest, BASE_PATH)} "
              f"({os.path.getsize(dest)/1024:.1f} KB)")
        return dest
    except Exception as e:
        print(f"      ❌ فشل النسخ إلى Drive: {e}")
        return None

def save_story_to_drive(src_path, username, new_name=None):
    return save_media_to_drive(src_path, username, subfolder="stories", new_name=new_name)

def save_highlight_to_drive(src_path, username, new_name=None):
    return save_media_to_drive(src_path, username, subfolder="highlights", new_name=new_name)

def save_profile_pic_to_drive(src_path, username):
    return save_media_to_drive(src_path, username, subfolder="profile", new_name="profile_pic.jpg")

# ============================================================
# 11. مدير instadata
# ============================================================
class InstadataManager:
    _cookies_file = TEMP_COOKIES
    _supports_no_resume = None

    @classmethod
    def _check_python_version(cls):
        major, minor = sys.version_info[:2]
        if (major, minor) < (3, 13):
            print(f"   ⚠️ Python {major}.{minor} — instadata تتطلب 3.13+")
            return False
        return True

    @classmethod
    def _check_instadata_installed(cls):
        try:
            r = subprocess.run([sys.executable, "-m", "instadata", "--version"],
                               capture_output=True, text=True, timeout=15)
            if r.returncode == 0:
                ver = (r.stdout.strip() or r.stderr.strip())[:100]
                print(f"   ✅ instadata: {ver}")
                return True
            else:
                print(f"   ❌ instadata غير متاح: {r.stderr[:200]}")
                return False
        except Exception as e:
            print(f"   ❌ فشل التحقق: {e}")
            return False

    @classmethod
    def _detect_no_resume_support(cls):
        if cls._supports_no_resume is not None:
            return cls._supports_no_resume
        try:
            r = subprocess.run([sys.executable, "-m", "instadata", "profile", "--help"],
                               capture_output=True, text=True, timeout=15)
            help_text = (r.stdout + r.stderr).lower()
            cls._supports_no_resume = "--no-resume" in help_text or "no-resume" in help_text
            if cls._supports_no_resume:
                print("   ✅ instadata يدعم --no-resume")
            else:
                print("   ℹ️ instadata لا يدعم --no-resume (سنستخدم مجلدات فريدة)")
            return cls._supports_no_resume
        except Exception as e:
            print(f"   ⚠️ فشل اكتشاف --no-resume: {e}")
            cls._supports_no_resume = False
            return False

    @classmethod
    def run_command(cls, args, timeout=600, stream_output=True):
        cmd = [sys.executable, "-m", "instadata"] + args
        if STATE.mode == "login":
            if os.path.exists(cls._cookies_file):
                cmd += ["--cookies", cls._cookies_file]
            else:
                print(f"   ⚠️ cookies غير موجود")

        print(f"\n   🚀 {' '.join(cmd)}")
        print(f"   {'='*60}")

        try:
            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', errors='replace', bufsize=1)

            stdout_lines = []
            stderr_lines = []

            def read_stream(stream, lines, prefix):
                for line in iter(stream.readline, ''):
                    if line:
                        lines.append(line)
                        if stream_output:
                            print(f"   {prefix} {line.rstrip()}")

            t1 = threading.Thread(target=read_stream, args=(process.stdout, stdout_lines, "📤"))
            t2 = threading.Thread(target=read_stream, args=(process.stderr, stderr_lines, "📥"))
            t1.start(); t2.start()

            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                print(f"   ❌ مهلة {timeout}ث")
                t1.join(timeout=5); t2.join(timeout=5)
                return "".join(stdout_lines), "Timeout", -1

            t1.join(timeout=10); t2.join(timeout=10)
            stdout = "".join(stdout_lines)
            stderr = "".join(stderr_lines)

            print(f"   {'='*60}")
            print(f"   📊 كود الخروج = {process.returncode}")
            if process.returncode != 0:
                print(f"   ⚠️ stderr: {stderr[-500:]}")
            return stdout, stderr, process.returncode
        except Exception as e:
            print(f"   ❌ {type(e).__name__}: {e}")
            traceback.print_exc()
            return "", str(e), -1

    @classmethod
    def resolve_user_id(cls, username):
        username = str(username or "").lower().strip().lstrip("@")
        stdout, stderr, rc = cls.run_command(["whoami", username], timeout=60, stream_output=False)
        if rc == 0 and stdout:
            uid = stdout.strip().split()[-1] if stdout.strip() else None
            if uid and uid.isdigit():
                return uid
        return None

    @classmethod
    def switch_mode(cls, new_mode):
        if new_mode not in ("anonymous", "login"):
            return False, "الوضع يجب أن يكون anonymous أو login"
        with STATE.lock:
            if STATE.mode == new_mode:
                return True, f"الوضع الحالي هو بالفعل {new_mode}"
            print(f"🔄 تبديل: {STATE.mode} → {new_mode}")
            STATE.mode = new_mode
            STATE.login_error = ""
            STATE.last_mode_change = time.time()
        if new_mode == "login":
            if not os.path.exists(cls._cookies_file):
                STATE.login_error = f"ملف cookies غير موجود: {cls._cookies_file}"
                return False, STATE.login_error
        return True, f"تم التبديل إلى: {new_mode}"

    @classmethod
    def human_delay(cls, a=1.5, b=4):
        time.sleep(random.uniform(a, b))

    @classmethod
    def purge_global_state(cls, username, uid=None):
        home = os.path.expanduser("~")
        possible_dirs = [
            os.path.join(home, ".cache", "instadata"),
            os.path.join(home, ".local", "share", "instadata"),
            os.path.join(home, ".local", "state", "instadata"),
            os.path.join(home, ".config", "instadata"),
            os.path.join(home, ".instadata"),
            "/tmp/instadata", "/var/tmp/instadata",
        ]
        possible_files = [f"{username}_posts.state.json", f"{username}.state.json"]
        if uid:
            possible_files.extend([f"{uid}_posts.state.json", f"{uid}.state.json"])

        removed = 0
        for d in possible_dirs:
            if os.path.exists(d):
                try:
                    shutil.rmtree(d, ignore_errors=True)
                    removed += 1
                except: pass
        for base in [home, "/tmp", "/var/tmp", os.path.join(home, ".cache")]:
            if not os.path.isdir(base):
                continue
            for fname in possible_files:
                fp = os.path.join(base, fname)
                if os.path.exists(fp):
                    try:
                        os.remove(fp)
                        removed += 1
                    except: pass
        if removed:
            print(f"   ✅ تم حذف {removed} عنصر حالة قديم")

# ============================================================
# 12. محرك البحث الشامل
# ============================================================
class UniversalSearcher:
    def analyze_query(self, query):
        query = query.strip()
        analysis = {'original': query, 'clean': query, 'type': 'general'}
        if query.startswith('#'):
            analysis['type'] = 'hashtag'; analysis['clean'] = query[1:].strip()
        elif query.startswith('@'):
            analysis['type'] = 'user'; analysis['clean'] = query[1:].strip()
        elif 'instagram.com' in query:
            m = re.search(r'instagram\.com/([A-Za-z0-9_.]+)', query)
            if m:
                analysis['type'] = 'user'; analysis['clean'] = m.group(1)
        return analysis

    def search(self, query, max_results_per_type=10, hunt_mentions=True):
        analysis = self.analyze_query(query)
        print(f"🔍 بحث: '{query}' — النوع: {analysis['type']}")
        results = {'analysis': analysis, 'users': [], 'hashtags': [],
                   'posts': [], 'reels': [], 'mentions': [], 'stats': {'total': 0}}
        if analysis['type'] == 'user':
            uid = InstadataManager.resolve_user_id(analysis['clean'])
            if uid:
                results['users'] = [{'username': analysis['clean'], 'pk': uid}]
            posts = self._fetch_posts_metadata(analysis['clean'], max_results_per_type)
            if posts:
                results['posts'] = posts
        results['stats']['total'] = len(results['users']) + len(results['posts'])
        self._send_summary(query, results)
        return results

    def _fetch_posts_metadata(self, username, limit=10):
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        out_dir = os.path.join(TEMP_INSTADATA, "search", f"{username}_{ts}")
        os.makedirs(out_dir, exist_ok=True)
        args = ["profile", username, "--limit", str(limit), "--output", out_dir, "--metadata"]
        if InstadataManager._detect_no_resume_support():
            args.append("--no-resume")
        stdout, stderr, rc = InstadataManager.run_command(args, timeout=300, stream_output=False)
        if rc != 0:
            return []
        for root, dirs, files in os.walk(out_dir):
            if "metadata.jsonl" in files:
                meta = os.path.join(root, "metadata.jsonl")
                posts = []
                try:
                    with open(meta, 'r', encoding='utf-8') as f:
                        for line in f:
                            if line.strip():
                                posts.append(json.loads(line))
                    return posts
                except: pass
        return []

    def _send_summary(self, query, results):
        total = results['stats']['total']
        html = f"""<b>🔍 نتائج البحث الشامل</b>
📝 الاستعلام: <code>{html_escape(query)}</code>
📊 إجمالي النتائج: <b>{total}</b>

<table bordered striped>
<tr><td><b>النوع</b></td><td><b>العدد</b></td></tr>
<tr><td>👤 مستخدمين</td><td>{len(results['users'])}</td></tr>
<tr><td>📸 منشورات</td><td>{len(results['posts'])}</td></tr>
</table>"""
        TelegramSender.send_rich_message(html)

    def generate_search_report(self, query, results):
        ts = datetime.now().strftime('%Y-%m-%d %H:%M')
        sections = []
        if results['users']:
            items = ""
            for u in results['users']:
                uname = _extract(u, 'username', default='?')
                items += f'<div class="user-card"><div class="user-username">@{uname}</div></div>'
            sections.append(f'<section class="section"><h2 class="section-title">👤 المستخدمون ({len(results["users"])})</h2>{items}</section>')
        if results['posts']:
            items = ""
            for p in results['posts']:
                code = _extract(p, 'shortcode', 'code', default='')
                caption = str(_extract(p, 'caption', 'caption_text', default=''))[:200]
                thumb_url = _extract(p, 'thumbnail_url', 'display_url', default='')
                thumb = ThumbnailEngine.from_url(thumb_url, THUMB_MAX_SIZE, THUMB_QUALITY, f"srch_{code}") if thumb_url else None
                if not thumb:
                    thumb = ThumbnailEngine.placeholder_svg("📷")
                items += f'''<div class="search-post-card">
<img src="data:image/jpeg;base64,{thumb}" class="search-thumb">
<div class="post-caption">{caption}</div>
<a href="https://instagram.com/p/{code}" target="_blank">🔗 فتح</a></div>'''
            sections.append(f'<section class="section"><h2 class="section-title">📸 المنشورات ({len(results["posts"])})</h2>{items}</section>')

        css = """
body{font-family:-apple-system,'Segoe UI',Tahoma,Arial,sans-serif;
background:linear-gradient(135deg,#1a1a2e,#0f3460);color:#e0e0e0;padding:16px}
.container{max-width:1100px;margin:0 auto}
.header{background:linear-gradient(135deg,#e94560,#c73659);padding:30px;border-radius:20px;margin-bottom:20px;text-align:center}
.section{background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);border-radius:16px;padding:20px;margin-bottom:20px}
.section-title{color:#e94560;margin-bottom:16px}
a{color:#64b5f6}
.user-card{background:rgba(0,0,0,.2);padding:10px;border-radius:10px;margin:6px 0}
.search-post-card{background:rgba(0,0,0,.2);padding:12px;border-radius:12px;margin:8px 0}
.search-thumb{width:100px;height:100px;object-fit:cover;border-radius:8px}
"""
        html = f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8">
<title>تقرير بحث — {query}</title><style>{css}</style></head>
<body><div class="container">
<div class="header"><h1>🔍 تقرير البحث</h1><div>📝 {query}</div><div>📅 {ts}</div></div>
{''.join(sections)}
</div></body></html>"""

        # حفظ التقرير على Drive
        safe_query = safe_name(query, "search")
        f = os.path.join(REPORTS_PATH, f"search_{safe_query}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html")
        with open(f, 'w', encoding='utf-8') as fp: fp.write(html)
        print(f"💾 تقرير البحث محفوظ على Drive: {f}")
        TelegramSender.send_document(f, f"🔍 تقرير بحث: {query}")
        return True

# ============================================================
# 13. أداة السكراب الرئيسية
# ============================================================
class InstadataSearcher:
    def __init__(self):
        self.universal_searcher = UniversalSearcher()

    def get_profile_info(self, username):
        print(f"📊 معلومات: {username}")
        try:
            uid = InstadataManager.resolve_user_id(username)
            if not uid:
                TelegramSender.send_message(f"⚠️ فشل جلب user_id لـ @{username}")
                return None

            db_info = get_account_info(username)
            mode_label = "🔓 مجهول" if STATE.mode == "anonymous" else "🔐 مسجل دخول"
            uname = html_escape(username)

            profile_html = f"""<b>👤 @{uname}</b>

<b>📊 معلومات الحساب</b>
<table bordered striped>
<tr><td>🆔 User ID</td><td><b>{uid}</b></td></tr>
<tr><td>⚙️ الوضع</td><td><b>{mode_label}</b></td></tr>"""
            if db_info:
                profile_html += f"""
<tr><td>👥 المتابعون</td><td><b>{int(db_info.get('followers', 0) or 0):,}</b></td></tr>
<tr><td>📸 المنشورات</td><td><b>{int(db_info.get('posts_count', 0) or 0):,}</b></td></tr>"""
            profile_html += f"""</table>

<a href="https://www.instagram.com/{uname}/">🔗 فتح الحساب</a>"""
            TelegramSender.send_rich_message(profile_html)

            save_or_update_account({
                "username": username, "full_name": db_info.get("full_name", "") if db_info else "",
                "bio": db_info.get("bio", "") if db_info else "",
                "followers": db_info.get("followers", 0) if db_info else 0,
                "following": db_info.get("following", 0) if db_info else 0,
                "posts_count": db_info.get("posts_count", 0) if db_info else 0,
                "is_private": db_info.get("is_private", False) if db_info else False,
                "is_verified": db_info.get("is_verified", False) if db_info else False,
            })
            return {'username': username, 'pk': uid}
        except Exception as e:
            print(f"❌ {type(e).__name__}: {e}")
            TelegramSender.send_message(f"❌ {type(e).__name__}: {html_escape(str(e)[:300])}")
            return None

    def _extract_shortcode(self, filename):
        name = os.path.splitext(filename)[0]
        name = re.sub(r'_\d+$', '', name)
        parts = name.split('_')
        if len(parts) >= 3:
            return parts[-1]
        elif len(parts) == 2:
            return parts[-1]
        match = re.search(r'([A-Za-z0-9_-]{11})', name)
        if match:
            return match.group(1)
        return name

    def scrape(self, username, max_posts=10, order='desc',
               fetch_stories=False, max_stories=50,
               fetch_highlights=False, max_highlights=20):
        print(f"\n{'='*60}")
        print(f"🔍 @{username} | {max_posts} منشور | الوضع: {STATE.mode}")
        print(f"{'='*60}")

        try:
            if not InstadataManager._check_instadata_installed():
                TelegramSender.send_message("❌ instadata غير مثبت")
                return

            uid = InstadataManager.resolve_user_id(username)
            if not uid:
                TelegramSender.send_message(f"⚠️ فشل جلب user_id لـ @{username}")

            # إنشاء حساب في DB إن لم يكن موجوداً
            account_id = get_account_id(username)
            if not account_id:
                save_or_update_account({
                    "username": username, "full_name": "", "bio": "",
                    "followers": 0, "following": 0, "posts_count": 0,
                    "is_private": False, "is_verified": False,
                })
                account_id = get_account_id(username)
                print(f"   ✅ سجل الحساب أُنشئ في DB: id={account_id}")

            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            parent_dir = os.path.join(TEMP_INSTADATA, f"{username}_{ts}")
            os.makedirs(parent_dir, exist_ok=True)
            InstadataManager.purge_global_state(username, uid)
            supports_no_resume = InstadataManager._detect_no_resume_support()

            args = ["profile", username, "--limit", str(max_posts),
                    "--output", parent_dir, "--metadata", "--workers", "4"]
            if supports_no_resume:
                args.append("--no-resume")

            stdout, stderr, rc = InstadataManager.run_command(args, timeout=1800)

            if rc != 0 and supports_no_resume:
                err_lower = (stderr + stdout).lower()
                if "no-resume" in err_lower or "unrecognized" in err_lower or "unknown" in err_lower:
                    print(f"   ⚠️ --no-resume فشل، إعادة المحاولة بدونه...")
                    InstadataManager._supports_no_resume = False
                    args = [a for a in args if a != "--no-resume"]
                    stdout, stderr, rc = InstadataManager.run_command(args, timeout=1800)

            if rc != 0:
                TelegramSender.send_message(
                    f"❌ <b>فشل instadata</b>\nكود: {rc}\n"
                    f"<code>{html_escape(stderr[-400:] or stdout[-400:] or '(فارغ)')}</code>")
                return

            all_files = []
            for root, dirs, files in os.walk(parent_dir):
                for f in files:
                    all_files.append(os.path.join(root, f))

            print(f"\n   📂 ملفات: {len(all_files)}")
            for fp in all_files[:30]:
                size = os.path.getsize(fp) if os.path.isfile(fp) else 0
                print(f"      - {os.path.relpath(fp, parent_dir)} ({size/1024:.1f} KB)")

            if not all_files:
                TelegramSender.send_message(
                    f"⚠️ <b>لا توجد ملفات</b> لـ @{username}\n"
                    f"<b>stdout:</b> <code>{html_escape(stdout[-300:] or '(فارغ)')}</code>")
                return

            posts_data = []
            for root, dirs, files in os.walk(parent_dir):
                if "metadata.jsonl" in files:
                    meta = os.path.join(root, "metadata.jsonl")
                    print(f"   📄 قراءة: {meta}")
                    with open(meta, 'r', encoding='utf-8') as f:
                        for line in f:
                            if line.strip():
                                try: posts_data.append(json.loads(line))
                                except: pass
                    break

            print(f"   ✅ metadata: {len(posts_data)} سجل")

            if not posts_data:
                media_exts = ('.jpg', '.jpeg', '.png', '.webp', '.mp4', '.mov', '.webm')
                media_files = [f for f in all_files if f.lower().endswith(media_exts)]
                print(f"   📸 ملفات وسائط: {len(media_files)}")
                for fp in media_files[:max_posts]:
                    fname = os.path.basename(fp)
                    shortcode = self._extract_shortcode(fname)
                    posts_data.append({
                        'shortcode': shortcode, 'file_path': fp, 'caption': '',
                        'like_count': 0, 'comment_count': 0, 'timestamp': None,
                    })

            if not posts_data:
                TelegramSender.send_message(
                    f"⚠️ <b>لا بيانات قابلة للقراءة</b> لـ @{username}\n"
                    f"الملفات: {', '.join([os.path.basename(f) for f in all_files[:10]])}")
                return

            if order == 'asc':
                posts_data.sort(key=lambda x: x.get('timestamp', 0) or 0)
            else:
                posts_data.sort(key=lambda x: x.get('timestamp', 0) or 0, reverse=True)

            targets = posts_data[:max_posts]

            intro = f"""<b>📦 Instagram OSINT (instadata v7.1)</b>
👤 <b>@{html_escape(username.lstrip('@'))}</b>
📌 <b>{len(targets)}</b> منشور
📅 الترتيب: <b>{'الأحدث' if order == 'desc' else 'الأقدم'}</b>
⚙️ الوضع: <b>{'🔓 مجهول' if STATE.mode == 'anonymous' else '🔐 مسجل دخول'}</b>
💾 الحفظ: <b>Google Drive + SQLite</b>"""
            TelegramSender.send_rich_message(intro)

            successful = 0
            already_exists = 0
            for i, post in enumerate(targets, 1):
                code = post.get('shortcode', '?')
                print(f"\n   📄 [{i}/{len(targets)}] {code}")

                if post_exists(code):
                    already_exists += 1
                    print(f"      ⏭️ موجود مسبقاً في DB — تخطي التنزيل")
                    TelegramSender.send_message_html(
                        f"⏭️ <b>محمل مسبقاً</b>\n"
                        f"📌 <code>{html_escape(code)}</code>\n"
                        f"👤 @{html_escape(username)}\n"
                        f"<i>هذا المنشور موجود في قاعدة البيانات — تم تخطي التنزيل.</i>")
                    continue

                if self._process_post(post, username, parent_dir, account_id,
                                      index=i, total=len(targets)):
                    successful += 1
                InstadataManager.human_delay(0.5, 1.5)

            if fetch_stories:
                print("\n📖 جلب القصص...")
                self._fetch_and_process_stories(username, max_stories, account_id)

            if fetch_highlights:
                print("\n📌 جلب Highlights...")
                self._fetch_and_process_highlights(username, max_highlights, account_id)

            # التقارير
            csv_path = self._generate_csv_report(username)
            html_path = self._generate_html_report(username, order)

            summary_html = f"""<b>✅ انتهت المهمة</b>
👤 @{html_escape(username.lstrip('@'))}
📦 المحدد: <b>{len(targets)}</b>
✅ نُزّل جديد: <b>{successful}</b>
⏭️ محمل مسبقاً: <b>{already_exists}</b>

<table bordered striped>
<tr><td>💾 قاعدة البيانات</td><td>✅ {DB_PATH}</td></tr>
<tr><td>📄 CSV</td><td>{'✅' if csv_path else '❌'}</td></tr>
<tr><td>🌐 HTML</td><td>{'✅' if html_path else '❌'}</td></tr>
</table>"""
            TelegramSender.send_rich_message(summary_html)

            if csv_path:
                TelegramSender.send_document(csv_path, f"📊 CSV @{username}")
            if html_path:
                TelegramSender.send_document(html_path, f"🌐 HTML @{username}")

            try:
                shutil.rmtree(parent_dir, ignore_errors=True)
                print(f"   🗑️ حذف مجلد @{username} من TEMP")
            except: pass

        except Exception as e:
            print(f"❌ {type(e).__name__}: {str(e)[:250]}")
            traceback.print_exc()
            TelegramSender.send_message(
                f"❌ <b>خطأ غير متوقع</b>\n"
                f"<code>{html_escape(traceback.format_exc()[-800:])}</code>")

    def _process_post(self, post, username, out_dir, account_id, index=1, total=1):
        try:
            shortcode = post.get('shortcode', '')
            if not shortcode:
                return False

            media_files = []
            if 'file_path' in post and os.path.isfile(post.get('file_path', '')):
                media_files.append(post['file_path'])
            else:
                for root, dirs, files in os.walk(out_dir):
                    for f in files:
                        if f.endswith(('.json', '.jsonl', '.thumb.jpg')):
                            continue
                        if not f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp', '.mp4', '.mov', '.webm')):
                            continue
                        if shortcode in f:
                            media_files.append(os.path.join(root, f))

            if not media_files:
                print(f"      ⚠️ لا ملفات لـ {shortcode}")
                return False

            print(f"      📎 {len(media_files)} ملف")

            # ⭐ حفظ في DB أولاً
            post_id, was_new = save_post_if_not_exists(username, {
                'shortcode': shortcode,
                'caption': post.get('caption', ''),
                'like_count': post.get('like_count', 0),
                'comment_count': post.get('comment_count', 0),
                'view_count': post.get('view_count', 0),
                'taken_at': post.get('timestamp'),
                'media_type': 'video' if media_files[0].lower().endswith(('.mp4', '.mov', '.webm')) else 'image',
                'thumbnail_url': '',
            })

            # ⭐ نسخ الوسائط إلى Google Drive
            drive_paths = []
            for idx, src in enumerate(media_files, 1):
                ext = os.path.splitext(src)[1].lower()
                new_name = f"{shortcode}" + (f"_{idx:02d}" if len(media_files) > 1 else "") + ext
                drive_path = save_media_to_drive(src, username, subfolder="posts", new_name=new_name)
                if drive_path:
                    drive_paths.append(drive_path)

            if not drive_paths:
                print(f"      ❌ فشل نسخ كل الملفات إلى Drive")

            keyboard = TelegramSender._post_keyboard(
                f"https://www.instagram.com/p/{shortcode}/",
                f"https://www.instagram.com/{username}/")
            caption = self._post_caption_html(post, index, total, username)

            sent = 0
            for fi, path in enumerate(media_files, 1):
                if not os.path.isfile(path):
                    continue
                ext = os.path.splitext(path)[1].lower()
                ok = False
                if ext in ('.mp4', '.mov', '.webm'):
                    ok = TelegramSender.send_video(path, caption,
                                                    reply_markup=keyboard if fi == 1 else None)
                else:
                    ok = TelegramSender.send_photo(path, caption,
                                                    reply_markup=keyboard if fi == 1 else None)
                if ok:
                    sent += 1
                InstadataManager.human_delay(0.3, 0.8)

            # تحديث DB بمسار Drive الفعلي
            if post_id and drive_paths:
                update_post_media(post_id,
                    'video' if drive_paths[0].lower().endswith(('.mp4', '.mov', '.webm')) else 'image',
                    drive_paths[0], '')

            return sent > 0
        except Exception as e:
            print(f"      ⚠️ {type(e).__name__}: {str(e)[:120]}")
            traceback.print_exc()
            return False

    def _post_caption_html(self, post, index, total, username):
        shortcode = html_escape(str(post.get('shortcode', '')))
        url = f"https://www.instagram.com/p/{shortcode}/"
        ts = post.get('timestamp')
        if ts:
            try: date_text = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
            except: date_text = str(ts)
        else:
            date_text = "غير معروف"
        caption = html_escape(str(post.get('caption', '(لا يوجد نص)'))[:600])
        likes = int(post.get('like_count', 0) or 0)
        comments = int(post.get('comment_count', 0) or 0)
        views = int(post.get('view_count', 0) or 0)
        profile_url = f"https://www.instagram.com/{html_escape(username)}/"
        return f"""<b>📌 المنشور #{index:02d} / {total:02d}</b>
👤 <a href="{profile_url}">@{html_escape(username)}</a>
📅 {date_text}
❤️ {likes:,}   💬 {comments:,}   👁️ {views:,}

<blockquote>{caption}</blockquote>

<a href="{url}">🔗 فتح المنشور</a>"""

    def _fetch_and_process_stories(self, username, max_stories, account_id):
        if STATE.mode != "login":
            TelegramSender.send_message("ℹ️ القصص تتطلب تسجيل دخول")
            return
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        parent_dir = os.path.join(TEMP_INSTADATA, "stories", f"{username}_{ts}")
        os.makedirs(parent_dir, exist_ok=True)
        args = ["story", username, "--output", parent_dir, "--metadata"]
        if InstadataManager._detect_no_resume_support():
            args.append("--no-resume")
        stdout, stderr, rc = InstadataManager.run_command(args, timeout=600, stream_output=False)
        if rc != 0:
            print(f"   ⚠️ فشل: {stderr[:200]}")
            return
        media_files = []
        for root, dirs, files in os.walk(parent_dir):
            for f in files:
                if f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp', '.mp4', '.mov', '.webm')):
                    media_files.append(os.path.join(root, f))
        for i, fpath in enumerate(media_files[:max_stories], 1):
            ext = os.path.splitext(fpath)[1].lower()
            # ⭐ نسخ إلى Drive
            drive_path = save_story_to_drive(fpath, username,
                                             new_name=f"story_{i:03d}{ext}")
            caption = f"📖 قصة {i} — @{username}"
            if ext in ('.mp4', '.mov', '.webm'):
                TelegramSender.send_video(fpath, caption)
            else:
                TelegramSender.send_photo(fpath, caption)
            # حفظ في DB
            save_story(account_id, {'pk': f"story_{username}_{i}"}, drive_path or fpath, "", "")
            InstadataManager.human_delay(0.5, 1)
        shutil.rmtree(parent_dir, ignore_errors=True)

    def _fetch_and_process_highlights(self, username, max_highlights, account_id):
        if STATE.mode != "login":
            TelegramSender.send_message("ℹ️ Highlights تتطلب تسجيل دخول")
            return
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        parent_dir = os.path.join(TEMP_INSTADATA, "highlights", f"{username}_{ts}")
        os.makedirs(parent_dir, exist_ok=True)
        args = ["highlights", username, "--output", parent_dir, "--metadata"]
        if InstadataManager._detect_no_resume_support():
            args.append("--no-resume")
        stdout, stderr, rc = InstadataManager.run_command(args, timeout=900, stream_output=False)
        if rc != 0:
            print(f"   ⚠️ فشل: {stderr[:200]}")
            return
        media_files = []
        for root, dirs, files in os.walk(parent_dir):
            for f in files:
                if f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp', '.mp4', '.mov', '.webm')):
                    media_files.append(os.path.join(root, f))
        for i, fpath in enumerate(media_files[:max_highlights * 5], 1):
            ext = os.path.splitext(fpath)[1].lower()
            # ⭐ نسخ إلى Drive
            save_highlight_to_drive(fpath, username, new_name=f"highlight_{i:03d}{ext}")
            caption = f"📌 Highlight — @{username}"
            if ext in ('.mp4', '.mov', '.webm'):
                TelegramSender.send_video(fpath, caption)
            else:
                TelegramSender.send_photo(fpath, caption)
            InstadataManager.human_delay(0.5, 1)
        shutil.rmtree(parent_dir, ignore_errors=True)

    # ========================================================
    # تقارير CSV و HTML
    # ========================================================
    def _generate_csv_report(self, username):
        try:
            aid = get_account_id(username)
            if not aid:
                return None
            conn = db_connect()
            conn.row_factory = sqlite3.Row
            posts = conn.execute("SELECT * FROM posts WHERE account_id=? ORDER BY taken_at DESC", (aid,)).fetchall()
            followers = conn.execute("SELECT username, full_name FROM followers WHERE account_id=?", (aid,)).fetchall()
            following = conn.execute("SELECT username, full_name FROM following WHERE account_id=?", (aid,)).fetchall()
            stories = conn.execute("SELECT * FROM stories WHERE account_id=? ORDER BY taken_at DESC", (aid,)).fetchall()
            conn.close()

            path = os.path.join(REPORTS_PATH, f"{safe_name(username)}_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
            with open(path, "w", newline="", encoding="utf-8-sig") as fh:
                writer = csv.writer(fh)
                writer.writerow(["type", "code_or_pk", "url", "media_type", "likes",
                                 "comments", "views", "plays", "taken_at",
                                 "username", "full_name", "caption", "file_path"])
                for row in posts:
                    writer.writerow(["post", row["post_code"], row["post_url"], row["media_type"],
                                     row["like_count"], row["comment_count"], row["view_count"],
                                     row["play_count"], row["taken_at"], username, "",
                                     (row["caption"] or "")[:1000], row["file_path"] or ""])
                for row in followers:
                    writer.writerow(["follower", "", "", "", "", "", "", "", "",
                                     row["username"], row["full_name"], "", ""])
                for row in following:
                    writer.writerow(["following", "", "", "", "", "", "", "", "",
                                     row["username"], row["full_name"], "", ""])
                for row in stories:
                    writer.writerow(["story", row["story_pk"], row["story_url"], row["media_type"],
                                     "", "", "", "", row["taken_at"], username, "", "", row["file_path"] or ""])
            print(f"📄 CSV على Drive: {path}")
            return path
        except Exception as exc:
            print(f"⚠️ CSV report: {exc}")
            return None

    def _generate_html_report(self, username, order="desc"):
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

            # صورة البروفايل
            profile_pic = ""
            for candidate_dir in ("profile", "posts"):
                candidate = os.path.join(MEDIA_PATH, safe_name(username), candidate_dir, "profile_pic.jpg")
                if os.path.exists(candidate):
                    profile_pic = ThumbnailEngine.from_file(candidate, 150, 50) or ""
                    break

            cards = []
            for post in posts:
                thumb = None
                # أولاً: من الملف المحلي على Drive
                if post.get("file_path") and os.path.exists(post["file_path"]):
                    thumb = ThumbnailEngine.from_file(post["file_path"], THUMB_MAX_SIZE, THUMB_QUALITY)
                # ثانياً: من thumbnail_url
                if not thumb:
                    thumb_url = post.get("thumbnail_url", "")
                    if thumb_url:
                        thumb = ThumbnailEngine.from_url(thumb_url, THUMB_MAX_SIZE, THUMB_QUALITY,
                                                         f"rpt_{post.get('post_code','')}")
                # ثالثاً: من ملف الفيديو — استخراج أول frame
                if not thumb and post.get("file_path") and os.path.exists(post["file_path"]) \
                   and post["file_path"].lower().endswith(('.mp4', '.mov', '.webm')):
                    thumb = self._video_thumbnail(post["file_path"])
                if not thumb:
                    thumb = ThumbnailEngine.placeholder_svg("📷")

                comment_html = ""
                if post.get("comments"):
                    rows = []
                    for c in post["comments"][:30]:
                        rows.append(f"<li>@{html_escape(str(c.get('commenter_username','') or ''))}: "
                                    f"{html_escape(str(c.get('comment_text','') or '')[:300])}</li>")
                    comment_html = f"<details><summary>💬 التعليقات ({len(post['comments'])})</summary><ul>" + "".join(rows) + "</ul></details>"

                media_badge = "🎬" if post.get("media_type") == "video" else "📷"
                downloaded_badge = "💾" if post.get("downloaded") else ""
                cards.append(f"""
<article class="post-card">
  <div class="post-thumb-wrap">
    <img src="data:image/jpeg;base64,{thumb}" class="post-thumb" loading="lazy">
    <span class="media-badge">{media_badge}{downloaded_badge}</span>
  </div>
  <div class="post-body">
    <div class="post-meta">
      <span>❤️ {int(post.get('like_count',0) or 0):,}</span>
      <span>💬 {int(post.get('comment_count',0) or 0):,}</span>
      <span>👁️ {int(post.get('view_count',0) or 0):,}</span>
    </div>
    <p class="post-caption">{html_escape(str(post.get('caption','') or '')[:400])}</p>
    <a class="post-link" target="_blank" href="{html_escape(str(post.get('post_url','')))}">🔗 فتح المنشور</a>
    {comment_html}
  </div>
</article>""")

            follower_html = "".join(f"<span class='chip'>@{html_escape(str(x.get('username','') or ''))}</span>" for x in followers) or "<em>لا توجد بيانات</em>"
            following_html = "".join(f"<span class='chip'>@{html_escape(str(x.get('username','') or ''))}</span>" for x in following) or "<em>لا توجد بيانات</em>"
            story_html = "".join(f"<div class='chip'><span>📖 {html_escape(str(x.get('story_pk','')))}</span> "
                                 f"<small>{html_escape(str(x.get('taken_at','')))}</small></div>" for x in stories) or "<em>لا توجد Stories محفوظة</em>"

            highlight_html = ""
            for h in highlights:
                title = html_escape(str(h.get("title", "") or ""))
                highlight_html += (f"<div class='highlight'><b>📌 {title}</b> "
                                   f"<span>({len(h.get('items', []))} عناصر محفوظة / "
                                   f"{int(h.get('media_count',0) or 0)} إجمالاً)</span></div>")
            highlight_html = highlight_html or "<em>لا توجد Highlights محفوظة</em>"

            pic_html = (f"<img class='profile' src='data:image/jpeg;base64,{profile_pic}'>"
                        if profile_pic else "<div class='profile placeholder'>👤</div>")

            css = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,'Segoe UI',Tahoma,Arial,sans-serif;
background:linear-gradient(135deg,#0f172a,#1e293b);color:#e2e8f0;
padding:16px;line-height:1.6;min-height:100vh}
.container{max-width:1200px;margin:auto}
.header{background:linear-gradient(135deg,#e94560,#c73659);border-radius:24px;
padding:32px;text-align:center;margin-bottom:20px;box-shadow:0 20px 60px rgba(233,69,96,.35)}
.profile{width:130px;height:130px;border-radius:50%;object-fit:cover;border:4px solid rgba(255,255,255,.3)}
.placeholder{display:inline-flex;align-items:center;justify-content:center;background:#334155;font-size:48px}
.header h2{margin-top:12px;font-size:1.6rem}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(100px,1fr));gap:10px;margin-top:16px}
.stats div{background:rgba(0,0,0,.25);padding:10px;border-radius:12px;text-align:center;font-weight:700}
.section{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);
border-radius:20px;padding:22px;margin-bottom:18px}
.section h2{color:#e94560;margin-bottom:16px;font-size:1.2rem}
.posts-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
.post-card{background:rgba(0,0,0,.22);border-radius:16px;overflow:hidden;
display:flex;flex-direction:column;transition:transform .2s}
.post-card:hover{transform:translateY(-3px)}
.post-thumb-wrap{position:relative}
.post-thumb{width:100%;height:200px;object-fit:cover;display:block;background:#1e293b}
.media-badge{position:absolute;top:8px;right:8px;background:rgba(0,0,0,.75);
padding:4px 8px;border-radius:8px;font-size:14px}
.post-body{padding:14px;flex:1;display:flex;flex-direction:column;gap:8px}
.post-meta{display:flex;gap:12px;font-size:.85rem;color:#94a3b8}
.post-caption{font-size:.88rem;color:#cbd5e1;flex:1}
.post-link{color:#64b5f6;font-weight:700;text-decoration:none}
.post-link:hover{text-decoration:underline}
details{font-size:.8rem;color:#94a3b8}
details summary{cursor:pointer;font-weight:600;padding:4px 0}
details ul{padding-right:18px;margin-top:6px}
.grid{display:flex;flex-wrap:wrap;gap:8px}
.chip{background:rgba(0,0,0,.22);padding:7px 12px;border-radius:10px;font-size:.85rem;overflow-wrap:anywhere}
.highlight{background:rgba(0,0,0,.22);padding:10px;border-radius:10px;margin:5px 0}
a{color:#7cc7ff}
footer{opacity:.6;text-align:center;padding:20px;font-size:.8rem}
@media(max-width:600px){.posts-grid{grid-template-columns:1fr}}
"""
            safe_user = html_escape(username)
            html = f"""<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OSINT — @{safe_user}</title><style>{css}</style></head>
<body><div class="container">
<header class="header">
{pic_html}
<h2>@{safe_user}</h2>
<div>{html_escape(str(account.get('full_name','') or ''))}</div>
<div class="stats">
<div>📸 {int(account.get('posts_count',0) or 0):,}</div>
<div>👥 {int(account.get('followers',0) or 0):,}</div>
<div>➡️ {int(account.get('following',0) or 0):,}</div>
<div>{'🔒' if account.get('is_private') else '🌐'}</div>
</div>
<p style="margin-top:12px;opacity:.9">{html_escape(str(account.get('bio','') or '')[:1000])}</p>
</header>

<section class="section"><h2>📸 المنشورات ({len(posts)})</h2>
<div class="posts-grid">{''.join(cards) or '<em>لا توجد منشورات محفوظة</em>'}</div></section>

<section class="section"><h2>👥 Followers ({len(followers)})</h2>
<div class="grid">{follower_html}</div></section>

<section class="section"><h2>➡️ Following ({len(following)})</h2>
<div class="grid">{following_html}</div></section>

<section class="section"><h2>📖 Stories ({len(stories)})</h2>
<div class="grid">{story_html}</div></section>

<section class="section"><h2>📌 Highlights ({len(highlights)})</h2>
{highlight_html}</section>

<footer>Generated {html_escape(now_iso())} · Instagram OSINT ULTRA v7.1</footer>
</div></body></html>"""

            path = os.path.join(REPORTS_PATH, f"{safe_name(username)}_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(html)
            print(f"🌐 HTML على Drive: {path}")
            return path
        except Exception as exc:
            print(f"⚠️ HTML report: {exc}")
            traceback.print_exc()
            return None

    def _video_thumbnail(self, video_path):
        """استخراج صورة مصغرة من الفيديو عبر ffmpeg إن توفّر"""
        try:
            thumb_path = video_path + ".thumb.jpg"
            if os.path.exists(thumb_path):
                return ThumbnailEngine.from_file(thumb_path, THUMB_MAX_SIZE, THUMB_QUALITY)
            r = subprocess.run(
                ["ffmpeg", "-y", "-ss", "00:00:01", "-i", video_path,
                 "-vframes", "1", "-vf", "scale=300:-1", thumb_path],
                capture_output=True, timeout=30)
            if r.returncode == 0 and os.path.exists(thumb_path):
                return ThumbnailEngine.from_file(thumb_path, THUMB_MAX_SIZE, THUMB_QUALITY)
            return None
        except Exception:
            return None

# ============================================================
# 14. Flask App
# ============================================================
INSTAGRAM_JOB_LOCK = threading.Lock()
app = Flask(__name__)

HTML_PAGE = """<!DOCTYPE html>
<html lang="ar" dir="rtl"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Instagram OSINT Scraper ULTRA v7.1</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,'Segoe UI',Tahoma,Arial,sans-serif;
background:linear-gradient(135deg,#1a1a2e,#0f3460);min-height:100vh;padding:20px;
display:flex;justify-content:center;align-items:flex-start}
.card{background:rgba(255,255,255,0.98);padding:30px;border-radius:20px;
box-shadow:0 20px 60px rgba(0,0,0,0.5);width:100%;max-width:700px}
h1{color:#0f3460;font-size:1.4rem;text-align:center;margin-bottom:6px}
.sub{color:#666;text-align:center;margin-bottom:20px;font-size:0.9rem}
.mode-bar{display:flex;gap:10px;margin-bottom:20px;padding:12px;
background:#f0f4f8;border-radius:12px;align-items:center;justify-content:center;flex-wrap:wrap}
.mode-btn{padding:10px 20px;border:2px solid #ccc;border-radius:10px;background:#fff;
cursor:pointer;font-weight:600;font-family:inherit;font-size:0.9rem}
.mode-btn.active{background:#e94560;color:#fff;border-color:#e94560}
.mode-status{font-size:0.85rem;color:#333;padding:6px 12px;border-radius:8px;background:#d4edda}
.mode-status.error{background:#f8d7da}
.st{color:#0f3460;font-weight:700;margin-bottom:10px;padding-bottom:6px;border-bottom:2px solid #e94560}
input[type="text"],input[type="number"],textarea{width:100%;padding:12px;margin:5px 0;
border:2px solid #e0e0e0;border-radius:10px;font-size:1rem;font-family:inherit}
input:focus,textarea:focus{border-color:#e94560;outline:none}
.opt{display:flex;align-items:center;gap:10px;padding:10px;background:#f8f9fa;
border-radius:10px;margin:5px 0;cursor:pointer}
.opt input[type="checkbox"]{width:18px;height:18px}
.opt.disabled{opacity:0.5;cursor:not-allowed}
button{width:100%;padding:14px;background:linear-gradient(135deg,#e94560,#c73659);
border:none;border-radius:10px;color:white;font-size:1.05rem;font-weight:700;
cursor:pointer;margin-top:10px;font-family:inherit}
button.secondary{background:linear-gradient(135deg,#10b981,#059669)}
.tabs{display:flex;gap:8px;margin-bottom:20px;border-bottom:2px solid #e0e0e0}
.tab{flex:1;padding:10px;background:transparent;border:none;cursor:pointer;
font-size:0.95rem;font-weight:600;color:#666;border-bottom:3px solid transparent;font-family:inherit}
.tab.active{color:#e94560;border-bottom-color:#e94560}
.tab-content{display:none}.tab-content.active{display:block}
.sec{margin-bottom:18px}
.rg{display:flex;gap:8px}
.ro{flex:1;padding:10px;background:#f8f9fa;border-radius:10px;text-align:center;cursor:pointer}
.ro input{display:none}
.ro:has(input:checked){background:rgba(233,69,96,0.1);border:2px solid #e94560;color:#e94560;font-weight:700}
.status{margin-top:14px;padding:12px;background:#d4edda;border-radius:8px;color:#155724;text-align:center;font-size:0.85rem}
.warn{background:#fff3cd;border:1px solid #ffc107;color:#856404;padding:10px;
border-radius:8px;font-size:0.8rem;margin-top:8px;display:none}
.warn.show{display:block}
.info-box{background:#e3f2fd;border:1px solid #90caf9;color:#1565c0;padding:10px;
border-radius:8px;font-size:0.8rem;margin-top:8px}
.drive-info{background:#dcfce7;border:1px solid #86efac;color:#166534;padding:10px;
border-radius:8px;font-size:0.8rem;margin-top:8px;word-break:break-all}
</style></head><body><div class="card">
<h1>🕵️ Instagram OSINT Scraper ULTRA v7.1</h1>
<p class="sub">✅ instadata + SQLite على Drive + تقارير + Rich Messages</p>

<div class="drive-info">
💾 <b>مسار الحفظ:</b> <code>{{db_path}}</code><br>
📂 <b>الوسائط:</b> <code>{{media_path}}</code>
</div>

<div class="mode-bar">
<span style="font-weight:700;color:#0f3460">⚙️ وضع الوصول:</span>
<button class="mode-btn {{'active' if mode == 'anonymous' else ''}}"
onclick="setMode('anonymous')">🔓 مجهول (عام)</button>
<button class="mode-btn {{'active' if mode == 'login' else ''}}"
onclick="setMode('login')">🔐 تسجيل دخول (متقدم)</button>
<span class="mode-status {{'error' if login_error else ''}}" id="modeStatus">
{{'⚠️ ' + login_error if login_error else '✅ ' + mode_label}}
</span>
</div>

<div class="tabs">
<button class="tab active" onclick="showTab('scrape',this)">📊 سكراب</button>
<button class="tab" onclick="showTab('search',this)">🔍 بحث</button>
</div>

<div id="scrape-tab" class="tab-content active">
<form action="/start" method="post">
<div class="sec"><div class="st">📌 البيانات</div>
<input type="text" name="username" placeholder="اسم المستخدم" required>
<input type="number" name="max_posts" value="10" min="1" max="500">
</div>
<div class="sec"><div class="st">📅 الترتيب</div>
<div class="rg">
<label class="ro"><input type="radio" name="order" value="desc" checked>الأحدث</label>
<label class="ro"><input type="radio" name="order" value="asc">الأقدم</label>
</div></div>
<div class="sec"><div class="st">⚙️ خيارات متقدمة</div>
<div class="opt"><input type="checkbox" name="fetch_stories" value="1" id="fs">
<label for="fs">📖 القصص (Stories) — يتطلب تسجيل دخول</label></div>
<div class="opt"><input type="checkbox" name="fetch_highlights" value="1" id="fh">
<label for="fh">📌 Highlights — يتطلب تسجيل دخول</label></div>
</div>
<div class="info-box">
ℹ️ <b>ملاحظة:</b> كل شيء يُرسل للتليجرام + يُحفظ على Google Drive (DB + media + reports).
</div>
<div class="warn" id="loginWarn">⚠️ هذه الخيارات تتطلب تفعيل "تسجيل دخول"</div>
<button type="submit">🚀 بدء السكراب</button>
</form></div>

<div id="search-tab" class="tab-content">
<form action="/search" method="post">
<div class="sec"><div class="st">🔍 البحث الشامل</div>
<textarea name="query" placeholder="اكتب اسم، @مستخدم..." required></textarea>
</div>
<div class="sec"><div class="opt"><input type="checkbox" name="generate_report" value="1" checked id="gr">
<label for="gr">📄 تقرير HTML</label></div></div>
<button type="submit" class="secondary">🔍 بدء البحث</button>
</form></div>

<div class="status" id="mainStatus">✅ الوضع الحالي: <b>{{mode_label}}</b></div>
</div>
<script>
function showTab(n,b){
document.querySelectorAll('.tab-content').forEach(c=>c.classList.remove('active'));
document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
document.getElementById(n+'-tab').classList.add('active');b.classList.add('active');
}
function setMode(m){
fetch('/set_mode',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({mode:m})})
.then(r=>r.json()).then(d=>{
if(d.success){
document.querySelectorAll('.mode-btn').forEach(b=>b.classList.remove('active'));
event.target.classList.add('active');
document.getElementById('modeStatus').textContent='✅ '+d.message;
document.getElementById('modeStatus').className='mode-status';
document.getElementById('mainStatus').innerHTML='✅ الوضع الحالي: <b>'+d.message+'</b>';
}else{
document.getElementById('modeStatus').textContent='⚠️ '+d.message;
document.getElementById('modeStatus').className='mode-status error';
}
});
}
document.querySelectorAll('input[name="fetch_stories"],input[name="fetch_highlights"]').forEach(cb=>{
cb.addEventListener('change',()=>{
const any=Array.from(document.querySelectorAll('input[name="fetch_stories"],input[name="fetch_highlights"]')).some(c=>c.checked);
document.getElementById('loginWarn').classList.toggle('show',any&&!{{'true' if mode=='login' else 'false'}});
});
});
</script></body></html>"""

@app.route('/')
def index():
    mode_label = "🔓 مجهول (عام)" if STATE.mode == "anonymous" else "🔐 مسجل دخول (متقدم)"
    return render_template_string(HTML_PAGE, mode=STATE.mode,
                                  mode_label=mode_label,
                                  login_error=STATE.login_error,
                                  db_path=DB_PATH,
                                  media_path=MEDIA_PATH)

@app.route('/set_mode', methods=['POST'])
def set_mode():
    data = request.get_json(silent=True) or {}
    new_mode = data.get('mode', 'anonymous')
    success, message = InstadataManager.switch_mode(new_mode)
    mode_label = "🔓 مجهول" if new_mode == "anonymous" else "🔐 مسجل دخول"
    return jsonify({
        "success": success,
        "message": f"تم التبديل إلى {mode_label}" if success else message
    })

@app.route('/start', methods=['POST'])
def start_scrape():
    u = request.form.get('username', '').strip()
    mp = safe_int(request.form.get('max_posts'), 10, 1, 500)
    o = request.form.get('order', 'desc') or 'desc'
    fs = request.form.get('fetch_stories') == '1'
    fh = request.form.get('fetch_highlights') == '1'

    if not u: return "❌ اسم المستخدم مطلوب", 400

    def task():
        if not INSTAGRAM_JOB_LOCK.acquire(blocking=False):
            TelegramSender.send_message("⏳ هناك مهمة أخرى قيد التنفيذ")
            return
        try:
            searcher = InstadataSearcher()
            searcher.get_profile_info(u)
            searcher.scrape(u, mp, o, fs, 50, fh, 20)
        except Exception as e:
            traceback.print_exc()
            TelegramSender.send_message(f"❌ {html_escape(str(e)[:800])}")
        finally:
            INSTAGRAM_JOB_LOCK.release()

    threading.Thread(target=task).start()
    return f"✅ @{u} — {mp} منشور", 200

@app.route('/search', methods=['POST'])
def start_search():
    query = request.form.get('query', '').strip()
    max_per_type = safe_int(request.form.get('max_per_type'), 10, 1, 50)
    generate_report = request.form.get('generate_report') == '1'

    if not query: return "❌ الاستعلام مطلوب", 400

    def task():
        if not INSTAGRAM_JOB_LOCK.acquire(blocking=False):
            TelegramSender.send_message("⏳ هناك مهمة أخرى قيد التنفيذ")
            return
        try:
            searcher = InstadataSearcher()
            results = searcher.universal_searcher.search(query, max_per_type, hunt_mentions=False)
            if generate_report:
                searcher.universal_searcher.generate_search_report(query, results)
        except Exception as e:
            traceback.print_exc()
            TelegramSender.send_message(f"❌ خطأ: {html_escape(str(e)[:800])}")
        finally:
            INSTAGRAM_JOB_LOCK.release()

    threading.Thread(target=task).start()
    return f"✅ جاري البحث عن: {query}", 200

# ============================================================
# 15. التشغيل
# ============================================================
if NGROK_AUTH_TOKEN and not NGROK_AUTH_TOKEN.startswith("ضع_"):
    ngrok.set_auth_token(NGROK_AUTH_TOKEN)
    print("✅ ngrok")

print(f"📦 Engine: instadata (CLI mode)")
print(f"🐍 Python: {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
print(f"⚙️ الوضع الافتراضي: {STATE.mode}")
print(f"💾 DB على Drive: {DB_PATH}")
print(f"📁 Media على Drive: {MEDIA_PATH}")

InstadataManager._check_python_version()
InstadataManager._check_instadata_installed()
InstadataManager._detect_no_resume_support()

# تهيئة قاعدة البيانات
init_db()

print("\n🚀 تشغيل السيرفر...")
url = ngrok.connect(5000).public_url
print("="*60)
print(f"🌐 {url}")
print("="*60)
print("✨ الميزات:")
print("   🔓 وضع مجهول + 🔐 تسجيل دخول (تبديل من الواجهة)")
print("   💾 SQLite تراكمية على Google Drive (نفس المسار السابق)")
print("   🎯 منع تكرار دقيق + إشعار 'محمل مسبقاً'")
print("   📄 تقارير CSV + HTML مع صور مصغرة محفوظة على Drive")
print("   📁 نسخ تلقائي للوسائط إلى Drive/media/<username>/")
print("   🎨 Telegram Rich Messages (جداول + Details + RTL)")
print("="*60)

try:
    app.run(host='0.0.0.0', port=5000)
finally:
    cleanup_temp()
