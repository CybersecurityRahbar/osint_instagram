# ============================================================
# Facebook OSINT — Playwright + noVNC + Captcha Auto-Wait
# ============================================================
import os, json, time, random, csv, io, re, base64, traceback, tempfile, asyncio, subprocess
from datetime import datetime
from html import escape as html_escape
from urllib.parse import quote

import requests
from PIL import Image

from google.colab import drive
if not os.path.exists("/content/drive/MyDrive"):
    print("📂 تركيب Google Drive...")
    drive.mount("/content/drive")
else:
    print("✅ Drive مركّب مسبقًا")

from playwright.async_api import async_playwright
from pyngrok import ngrok

# ============================================================
# الإعدادات
# ============================================================
TELEGRAM_TOKEN = ""
TELEGRAM_CHAT_ID = ""
EMAIL = ""
PASSWORD = ""
NGROK_TOKEN = ""

SESSION_DIR = "/content/drive/MyDrive/FB_Sessions"
os.makedirs(SESSION_DIR, exist_ok=True)
SESSION_FILE = os.path.join(SESSION_DIR, "fb_session.json")

TMP = tempfile.mkdtemp(prefix="fb_osint_")
MEDIA_TMP = os.path.join(TMP, "media")
SHOTS_TMP = os.path.join(TMP, "shots")
os.makedirs(MEDIA_TMP, exist_ok=True)
os.makedirs(SHOTS_TMP, exist_ok=True)

THUMB_SIZE, THUMB_QUALITY = 150, 40
SHOT_COUNTER = {"n": 0}
CAPTCHA_TIMEOUT = 900     # 15 دقيقة لحل captcha
MANUAL_LOGIN_TIMEOUT = 900  # 15 دقيقة لدخول يدوي

_http = requests.Session()
_http.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
})


# ============================================================
# Telegram
# ============================================================
class TelegramSender:
    @staticmethod
    def send_message(text, parse_mode="HTML"):
        chunks = [text[i:i+4000] for i in range(0, len(text), 4000)] or [""]
        for ch in chunks:
            try:
                r = _http.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                    data={"chat_id": TELEGRAM_CHAT_ID, "text": ch,
                          "parse_mode": parse_mode,
                          "disable_web_page_preview": "true"}, timeout=20)
                if r.status_code != 200:
                    _http.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                               data={"chat_id": TELEGRAM_CHAT_ID, "text": ch}, timeout=20)
                time.sleep(0.3)
            except Exception as e:
                print(f"⚠️ TG: {e}")

    @staticmethod
    def send_photo(path, caption=""):
        try:
            if not os.path.isfile(path):
                return
            with open(path, "rb") as f:
                _http.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                    data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption[:1024],
                          "parse_mode": "HTML"},
                    files={"photo": f}, timeout=60)
        except Exception as e:
            print(f"⚠️ TG photo: {e}")

    @staticmethod
    def send_document(path, caption=""):
        try:
            if not os.path.isfile(path):
                return
            with open(path, "rb") as f:
                _http.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument",
                    data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption[:1024],
                          "parse_mode": "HTML"},
                    files={"document": f}, timeout=180)
        except Exception as e:
            print(f"⚠️ TG doc: {e}")


# ============================================================
# Screenshot Helper
# ============================================================
async def snap(page, label, send=True):
    try:
        SHOT_COUNTER["n"] += 1
        n = SHOT_COUNTER["n"]
        path = os.path.join(SHOTS_TMP, f"{n:02d}_{label}.png")
        try:
            await page.screenshot(path=path, full_page=False)
        except Exception:
            return
        print(f"   📸 صورة #{n}: {label}")
        if send:
            TelegramSender.send_photo(path, f"📸 <b>{label}</b>\n#{n}")
    except Exception as e:
        print(f"   ⚠️ snap: {e}")


# ============================================================
# Thumbnail
# ============================================================
class Thumb:
    @staticmethod
    def from_url(url):
        if not url:
            return None
        try:
            r = _http.get(url, timeout=15)
            if r.status_code != 200:
                return None
            img = Image.open(io.BytesIO(r.content))
            img.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.LANCZOS)
            if img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=THUMB_QUALITY, optimize=True)
            return base64.b64encode(buf.getvalue()).decode()
        except Exception:
            return None


# ============================================================
# noVNC Setup
# ============================================================
class VNCViewer:
    def __init__(self):
        self.url = None
        self.xvfb = None
        self.x11vnc = None
        self.websockify = None
        self.tunnel = None

    def start(self):
        print("🖥️ تشغيل Xvfb + x11vnc + noVNC...")
        os.environ["DISPLAY"] = ":99"
        subprocess.run("pkill Xvfb || true", shell=True)
        subprocess.run("pkill x11vnc || true", shell=True)
        subprocess.run("pkill websockify || true", shell=True)
        time.sleep(1)

        self.xvfb = subprocess.Popen(
            ["Xvfb", ":99", "-screen", "0", "1920x1080x24"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, start_new_session=True, close_fds=True)
        time.sleep(2)

        self.x11vnc = subprocess.Popen(
            ["x11vnc", "-display", ":99", "-nopw", "-forever", "-shared",
             "-rfbport", "5900", "-quiet"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, start_new_session=True, close_fds=True)
        time.sleep(2)

        novnc_path = "/usr/share/novnc"
        if not os.path.exists(novnc_path):
            for cand in ["/usr/share/novnc", "/opt/novnc", "/usr/local/novnc"]:
                if os.path.exists(cand):
                    novnc_path = cand
                    break

        self.websockify = subprocess.Popen(
            ["websockify", "--web", novnc_path, "6080", "localhost:5900"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, start_new_session=True, close_fds=True)
        time.sleep(3)

        try:
            ngrok.set_auth_token(NGROK_TOKEN)
            self.tunnel = ngrok.connect(6080, "http")
            base = self.tunnel.public_url
            if base.startswith("http://"):
                base = base.replace("http://", "https://", 1)
            self.url = f"{base}/vnc.html?autoconnect=1&resize=scale"
            print(f"✅ noVNC جاهز: {self.url}")
        except Exception as e:
            print(f"⚠️ ngrok: {e}")
            self.url = None

        return self.url

    def stop(self):
        for p in (self.websockify, self.x11vnc, self.xvfb):
            try:
                if p:
                    p.terminate()
            except Exception:
                pass
        try:
            if self.tunnel:
                ngrok.disconnect(self.tunnel.public_url)
        except Exception:
            pass


# ============================================================
# Human Behavior
# ============================================================
class HB:
    @staticmethod
    async def delay(a=1.2, b=3.5):
        await asyncio.sleep(random.uniform(a, b))

    @staticmethod
    async def scroll(page, n=3):
        for _ in range(n):
            await page.evaluate(f"window.scrollBy(0, {random.randint(300,900)});")
            await asyncio.sleep(random.uniform(0.4, 1.2))

    @staticmethod
    async def bottom(page):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
        await asyncio.sleep(random.uniform(1.5, 3.0))

    @staticmethod
    async def jiggle(page):
        try:
            await page.mouse.move(random.randint(100, 800), random.randint(100, 600))
        except Exception:
            pass


# ============================================================
# FacebookSearcher
# ============================================================
class FacebookSearcher:
    def __init__(self, email, password):
        self.email = email
        self.password = password
        self.browser = None
        self.context = None
        self.page = None
        self.playwright = None
        self.session_file = SESSION_FILE
        self.vnc = VNCViewer()
        self.vnc_url = None

    # ---------- Setup ----------
    async def setup(self):
        print("[0] إعداد البيئة...")
        self.vnc_url = self.vnc.start()

        if self.vnc_url:
            TelegramSender.send_message(
                f"🖥️ <b>noVNC جاهز</b>\n"
                f"افتح هذا الرابط في هاتفك للتدخل اليدوي:\n"
                f"<a href='{self.vnc_url}'>{self.vnc_url}</a>\n\n"
                f"<b>الخطوات:</b>\n"
                f"1. افتح الرابط\n"
                f"2. اضغط Connect\n"
                f"3. سترى شاشة المتصفح مباشرة")

        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=False,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-gpu",
                "--window-size=1920,1080",
                "--start-maximized",
                "--lang=ar,en-US;q=0.9,en;q=0.8",
            ]
        )
        await self._new_context_with_stealth()
        print("✅ المتصفح جاهز.")

    async def _new_context_with_stealth(self, storage_state=None):
        if self.context:
            try:
                await self.context.close()
            except Exception:
                pass
        kwargs = dict(
            viewport={"width": 1920, "height": 1080},
            locale="ar",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        if storage_state:
            kwargs["storage_state"] = storage_state
        self.context = await self.browser.new_context(**kwargs)
        await self.context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'languages', { get: () => ['ar','en-US','en'] });
            Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
            window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){}, app: {} };
        """)
        self.page = await self.context.new_page()

    # ---------- c_user check ----------
    async def _has_c_user(self):
        try:
            cookies = await self.context.cookies()
            for c in cookies:
                if c.get("name") == "c_user" and c.get("value"):
                    return True
        except Exception:
            pass
        return False

    async def _load_session(self):
        if not os.path.exists(self.session_file):
            return False
        print("   🔄 محاولة تحميل الجلسة من Drive...")
        try:
            await self._new_context_with_stealth(storage_state=self.session_file)
            await self.page.goto("https://www.facebook.com",
                                 wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(5)
            await snap(self.page, "session_try")

            if await self._has_c_user():
                print("   ✅ الجلسة صالحة (c_user موجود).")
                TelegramSender.send_message("✅ <b>دخول عبر الجلسة المحفوظة</b>")
                return True
            print("   ⚠️ الجلسة منتهية.")
            return False
        except Exception as e:
            print(f"   ⚠️ load session: {e}")
            return False

    async def _save_session(self):
        try:
            if not await self._has_c_user():
                print("   ⚠️ لا يوجد c_user — لن تُحفظ الجلسة")
                return False
            await self.context.storage_state(path=self.session_file)
            print(f"💾 الجلسة محفوظة: {self.session_file}")
            TelegramSender.send_message("💾 <b>تم حفظ الجلسة على Drive</b>")
            return True
        except Exception as e:
            print(f"⚠️ save session: {e}")
            return False

    # ---------- Captcha Detection ----------
    async def _has_captcha(self):
        selectors = [
            "iframe[src*='recaptcha']",
            "iframe[src*='captcha']",
            "iframe[title*='captcha']",
            "iframe[title*='reCAPTCHA']",
            "iframe[title*='robot']",
            "iframe[title*='تحقق']",
            "div#captcha",
            "div[data-testid='captcha']",
            "div[class*='captcha']",
            "div[id*='captcha']",
            "img[src*='captcha']",
        ]
        for sel in selectors:
            try:
                el = await self.page.query_selector(sel)
                if el:
                    try:
                        visible = await el.is_visible()
                    except Exception:
                        visible = True
                    if visible:
                        return True
            except Exception:
                pass
        try:
            body = await self.page.inner_text("body")
            low = body.lower()
            kws = [
                "i'm not a robot", "i’m not a robot",
                "لست روبوت", "أنا لست روبوت",
                "verify you're human", "verify you’re human",
                "تحقق من أنك لست",
                "select all images", "اختر كل الصور",
                "security check", "فحص الأمان",
                "recaptcha", "hcaptcha",
            ]
            for kw in kws:
                if kw in low:
                    return True
        except Exception:
            pass
        return False

    # ---------- Wait for captcha solve ----------
    async def _wait_for_captcha_solve(self, max_wait=CAPTCHA_TIMEOUT, tag="captcha"):
        print(f"   ⚠️ ننتظر حل {tag} (حتى {max_wait//60} دقيقة)")
        await snap(self.page, f"{tag}_appeared")

        TelegramSender.send_message(
            f"🚨 <b>مطلوب تدخل يدوي — {tag}</b>\n\n"
            f"<b>افتح noVNC:</b>\n"
            f"<a href='{self.vnc_url}'>{self.vnc_url}</a>\n\n"
            f"<b>الخطوات:</b>\n"
            f"1. اضغط Connect\n"
            f"2. حل الـ captcha / التحقق\n"
            f"3. اضغط <b>Verify</b> أو <b>تحقق</b>\n"
            f"4. انتظر — الأداة تكتشف الحل تلقائيًا\n\n"
            f"⏳ لا تغلق النافذة حتى تكتمل العملية.")

        start = time.time()
        last_notify = start
        captcha_seen = True
        captcha_gone_at = None

        while time.time() - start < max_wait:
            # 1) c_user ظهر → نجاح
            if await self._has_c_user():
                print("   ✅ c_user موجود — نجاح!")
                await asyncio.sleep(2)
                await snap(self.page, f"{tag}_solved")
                TelegramSender.send_message("✅ <b>تم الحل والدخول ناجح</b>")
                await self._save_session()
                return True

            has = await self._has_captcha()

            # 2) الكابتشا كانت موجودة والآن اختفت
            if captcha_seen and not has:
                if captcha_gone_at is None:
                    captcha_gone_at = time.time()
                    print("   ℹ️ اختفت captcha — فحص c_user...")
                # انتظر 8 ثواني ثم افحص c_user
                if time.time() - captcha_gone_at > 8:
                    if await self._has_c_user():
                        print("   ✅ دخول ناجح بعد الحل")
                        await snap(self.page, f"{tag}_solved_after_wait")
                        TelegramSender.send_message("✅ <b>دخول ناجح بعد التحقق</b>")
                        await self._save_session()
                        return True
                    # لم يظهر c_user — ربما ظهرت captcha أخرى
                    captcha_gone_at = None
                    captcha_seen = has

            # 3) عادت الكابتشا (خطوة ثانية)
            if not captcha_seen and has:
                captcha_seen = True
                captcha_gone_at = None
                print("   ⚠️ captcha جديدة/خطوة تالية")
                TelegramSender.send_message(
                    f"⚠️ <b>خطوة تحقق إضافية</b>\n"
                    f"<a href='{self.vnc_url}'>{self.vnc_url}</a>")
                await snap(self.page, f"{tag}_step2")

            await asyncio.sleep(3)

            # 4) إشعار كل دقيقتين
            if time.time() - last_notify > 120:
                last_notify = time.time()
                elapsed = int(time.time() - start)
                remaining = int(max_wait - elapsed)
                try:
                    await snap(self.page, f"{tag}_waiting_{elapsed//60}m", send=True)
                except Exception:
                    pass
                TelegramSender.send_message(
                    f"⏳ <b>بانتظار الحل</b>\n"
                    f"مضى: {elapsed//60} د | متبقٍ: {remaining//60} د\n"
                    f"<a href='{self.vnc_url}'>{self.vnc_url}</a>")

        # انتهت المدة — آخر فحص
        await snap(self.page, f"{tag}_timeout")
        if await self._has_c_user():
            TelegramSender.send_message("✅ <b>نجح الدخول في اللحظة الأخيرة</b>")
            await self._save_session()
            return True
        TelegramSender.send_message("⏰ <b>انتهت مدة الانتظار</b>")
        return False

    # ---------- Cookie consent ----------
    async def _handle_cookie_consent(self):
        selectors = [
            "button[data-cookiebanner='accept_button']",
            "button[data-cookiebanner='accept_only_essential_button']",
            "button[title*='Allow all']",
            "button[title*='Allow All']",
            "div[aria-label*='Allow all']",
        ]
        for sel in selectors:
            try:
                btn = await self.page.wait_for_selector(sel, timeout=1500)
                if btn:
                    await btn.click()
                    print(f"   ✅ قبول الكوكيز: {sel}")
                    await asyncio.sleep(2)
                    return True
            except Exception:
                continue
        return False

    # ---------- Login ----------
    async def login(self):
        print("[1] تسجيل الدخول...")
        TelegramSender.send_message("🔐 <b>بدء عملية تسجيل الدخول</b>")

        # 1) جلسة محفوظة
        if await self._load_session():
            return True

        # 2) فتح الصفحة
        try:
            await self.page.goto("https://www.facebook.com",
                                 wait_until="domcontentloaded", timeout=90000)
        except Exception as e:
            print(f"   ⚠️ goto: {e}")

        await asyncio.sleep(5)
        await snap(self.page, "home_loaded")

        # 3) كوكيز
        await self._handle_cookie_consent()
        await asyncio.sleep(2)
        await snap(self.page, "after_cookies")

        print(f"   📍 URL: {self.page.url}")

        # 4) captcha أولي
        if await self._has_captcha():
            if not await self._wait_for_captcha_solve(max_wait=CAPTCHA_TIMEOUT, tag="captcha_initial"):
                return False

        # 5) حقل البريد
        email_selectors = [
            "#email", "input[name='email']", "input[type='email']",
            "input[autocomplete='username']",
        ]
        email_field = None
        for sel in email_selectors:
            try:
                email_field = await self.page.wait_for_selector(sel, timeout=4000)
                if email_field:
                    print(f"   ✅ حقل البريد: {sel}")
                    break
            except Exception:
                continue

        if not email_field:
            await snap(self.page, "no_email_field")
            TelegramSender.send_message("❌ لم يظهر حقل البريد")
            return False

        # 6) إدخال البيانات
        try:
            await HB.delay(0.6, 1.3)
            await email_field.click()
            await email_field.fill(self.email)
            await HB.delay(0.5, 1.0)
            await snap(self.page, "email_filled")

            pass_field = None
            for sel in ["#pass", "input[name='pass']", "input[type='password']"]:
                try:
                    pass_field = await self.page.wait_for_selector(sel, timeout=3000)
                    if pass_field:
                        break
                except Exception:
                    continue

            if not pass_field:
                await snap(self.page, "no_pass_field")
                TelegramSender.send_message("❌ لم يُعثر على حقل كلمة السر")
                return False

            await pass_field.click()
            await pass_field.fill(self.password)
            await HB.delay(0.5, 1.0)
            await snap(self.page, "password_filled")

            # إرسال
            print("   ⏳ إرسال البيانات (Enter)...")
            try:
                await pass_field.press("Enter")
                print("   ✅ ضُغط Enter")
            except Exception as e:
                print(f"   ⚠️ Enter: {e}")

            # ⭐ انتظار ذكي: 30 ثانية، فحص كل 2 ثانية
            print("   ⏳ فحص الكابتشا / الدخول كل 2 ثانية لمدة 30 ثانية...")
            captcha_found = False
            login_ok = False
            for i in range(15):
                await asyncio.sleep(2)
                if await self._has_c_user():
                    login_ok = True
                    print(f"   ✅ c_user ظهر عند {(i+1)*2} ثانية")
                    break
                if await self._has_captcha():
                    captcha_found = True
                    print(f"   🚨 captcha ظهرت عند {(i+1)*2} ثانية")
                    break

            await snap(self.page, "after_login_submit")

            # ⭐ إذا ظهرت captcha — انتظر الحل
            if captcha_found:
                await snap(self.page, "captcha_after_login")
                if not await self._wait_for_captcha_solve(
                        max_wait=CAPTCHA_TIMEOUT, tag="captcha_login"):
                    # لم ينجح خلال 15 دقيقة
                    TelegramSender.send_message("❌ <b>انتهى الوقت — لم يكتمل الدخول</b>")
                    return False
                return True

            # ⭐ إذا لم تظهر captcha لكن لم يظهر c_user بعد
            if not login_ok:
                print("   ⏳ لا captcha ولا c_user — انتظار إضافي 20 ثانية...")
                for i in range(10):
                    await asyncio.sleep(2)
                    if await self._has_c_user():
                        login_ok = True
                        break
                    if await self._has_captcha():
                        captcha_found = True
                        break

                if captcha_found:
                    await snap(self.page, "captcha_late")
                    if not await self._wait_for_captcha_solve(
                            max_wait=CAPTCHA_TIMEOUT, tag="captcha_late"):
                        return False
                    return True

            # 7) فحص نهائي
            if login_ok or await self._has_c_user():
                print("   ✅ دخول ناجح.")
                await snap(self.page, "login_success")
                TelegramSender.send_message("✅ <b>تسجيل دخول ناجح</b>")
                await self._save_session()
                return True

            # 8) لم نعرف الحالة — انتظار يدوي
            print("   ⚠️ حالة غير معروفة — انتظار يدوي عبر noVNC")
            await snap(self.page, "login_unknown")
            TelegramSender.send_message(
                f"⚠️ <b>لم نكتشف حالة الدخول</b>\n\n"
                f"افتح noVNC وأكمل يدويًا:\n"
                f"<a href='{self.vnc_url}'>{self.vnc_url}</a>\n\n"
                f"⏳ لديك {MANUAL_LOGIN_TIMEOUT//60} دقيقة...")

            start = time.time()
            last_notify = start
            while time.time() - start < MANUAL_LOGIN_TIMEOUT:
                if await self._has_c_user():
                    print("   ✅ دخول ناجح يدويًا!")
                    await snap(self.page, "manual_login_success")
                    TelegramSender.send_message("✅ <b>اكتمل الدخول يدويًا</b>")
                    await self._save_session()
                    return True
                await asyncio.sleep(3)
                if time.time() - last_notify > 180:
                    last_notify = time.time()
                    remaining = int((MANUAL_LOGIN_TIMEOUT - (time.time() - start)) // 60)
                    TelegramSender.send_message(
                        f"⏳ <b>بانتظارك</b> — متبقٍ {remaining} د\n"
                        f"<a href='{self.vnc_url}'>{self.vnc_url}</a>")
                    try:
                        await snap(self.page, "waiting_manual", send=True)
                    except Exception:
                        pass

            TelegramSender.send_message("⏰ <b>انتهى الوقت — لم يكتمل الدخول</b>")
            return False

        except Exception as e:
            print(f"   ❌ login exception: {e}")
            traceback.print_exc()
            try:
                await snap(self.page, "login_exception")
            except Exception:
                pass
            TelegramSender.send_message(f"❌ <code>{html_escape(str(e)[:300])}</code>")
            return False

    # ---------- Search ----------
    async def search_user(self, keyword):
        print(f"\n[2] البحث: '{keyword}'")
        await self.page.goto(
            f"https://www.facebook.com/search/people/?q={quote(keyword)}",
            wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(5)
        await HB.scroll(self.page, 2)
        await snap(self.page, "search_results")

        if await self._has_captcha():
            if not await self._wait_for_captcha_solve(tag="captcha_search"):
                return None

        print("   ⏳ انتظار نتائج البحث...")
        selectors = [
            "a[href*='/profile.php?id=']",
            "a[href*='facebook.com/'][role='link']",
            "div[role='main'] a[href*='facebook.com/']",
            "[role='feed'] a[role='link']",
        ]
        deadline = time.time() + 30
        link = None
        while time.time() < deadline and not link:
            for sel in selectors:
                try:
                    els = await self.page.query_selector_all(sel)
                    for el in els:
                        href = await el.get_attribute("href") or ""
                        if any(x in href for x in [
                            "/search/", "/hashtag/", "/help/", "/policies/",
                            "/login", "/recover", "/settings",
                        ]):
                            continue
                        if "facebook.com" in href and (
                            "/profile.php" in href or
                            re.match(r"https?://(www\.)?facebook\.com/[^/?]+/?$", href)
                        ):
                            link = el
                            break
                    if link:
                        break
                except Exception:
                    continue
            if not link:
                await asyncio.sleep(2)

        if not link:
            await snap(self.page, "no_search_result")
            TelegramSender.send_message(f"❌ لم يُعثر على: {html_escape(keyword)}")
            return None

        url = await link.get_attribute("href")
        m = re.search(r"facebook\.com/([^/?]+)", url or "")
        username = m.group(1) if m else f"user_{int(time.time())}"
        print(f"   ✅ الهدف: {url}")
        await snap(self.page, "search_target_found")
        return {"url": url, "username": username}

    async def open_profile(self, url):
        await self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(5)
        await HB.jiggle(self.page)
        await snap(self.page, "profile_opened")

        if await self._has_captcha():
            if not await self._wait_for_captcha_solve(tag="captcha_profile"):
                return False
        return True

    # ---------- GraphQL Collect ----------
    async def collect(self, max_posts=30, max_scrolls=20):
        print(f"\n[3] تحميل المنشورات (حد {max_posts})...")
        graphql_responses = []
        collected = {}

        async def on_response(response):
            if "/api/graphql/" not in response.url:
                return
            try:
                ct = response.headers.get("content-type", "")
                if "json" not in ct and "javascript" not in ct:
                    return
                body = await response.text()
                if body.startswith("for(;;);"):
                    body = body[8:]
                data = json.loads(body)
                graphql_responses.append(data)
            except Exception:
                pass

        self.page.on("response", on_response)

        last_h, stagnant = 0, 0
        for i in range(max_scrolls):
            await HB.scroll(self.page, 2)
            await HB.jiggle(self.page)
            await HB.bottom(self.page)
            await HB.delay(1.0, 2.5)

            for resp in graphql_responses:
                for rp in self._extract_posts(resp):
                    n = self._normalize(rp)
                    if n["post_id"] and n["post_id"] not in collected:
                        collected[n["post_id"]] = n

            print(f"   [{i+1}/{max_scrolls}] فريدة: {len(collected)}")

            if (i + 1) % 3 == 0:
                await snap(self.page, f"scroll_{i+1}", send=False)

            if len(collected) >= max_posts:
                break

            nh = await self.page.evaluate("document.body.scrollHeight")
            if nh == last_h:
                stagnant += 1
                if stagnant >= 3:
                    break
            else:
                stagnant = 0
            last_h = nh

        try:
            self.page.remove_listener("response", on_response)
        except Exception:
            pass

        await snap(self.page, "collect_done")
        return list(collected.values())[:max_posts]

    @staticmethod
    def _walk(obj, pred, out):
        if isinstance(obj, dict):
            if pred(obj):
                out.append(obj)
            for v in obj.values():
                FacebookSearcher._walk(v, pred, out)
        elif isinstance(obj, list):
            for it in obj:
                FacebookSearcher._walk(it, pred, out)

    def _extract_posts(self, resp):
        posts = []

        def is_post(o):
            k = o.keys()
            return ("creation_time" in k and
                    ("message" in k or "story" in k or "attachments" in k))

        self._walk(resp, is_post, posts)
        return posts

    @staticmethod
    def _normalize(raw):
        msg = ""
        if isinstance(raw.get("message"), dict):
            msg = raw["message"].get("text", "")
        elif isinstance(raw.get("message"), str):
            msg = raw["message"]
        if not msg and isinstance(raw.get("story"), dict):
            m = raw["story"].get("message")
            msg = m.get("text", "") if isinstance(m, dict) else ""

        rc = cc = sc = 0
        br = {}
        fb = raw.get("feedback") or {}
        if isinstance(fb, dict):
            tr = fb.get("top_reactions") or {}
            if isinstance(tr, dict):
                for e in (tr.get("edges") or []):
                    n = e.get("node") or {}
                    rt = n.get("localized_name") or n.get("id") or "?"
                    try:
                        c = int(e.get("reaction_count") or 0)
                        br[rt] = c
                        rc += c
                    except Exception:
                        pass
            if isinstance(fb.get("reaction_count"), dict):
                rc = max(rc, int(fb["reaction_count"].get("count") or 0))
            if isinstance(fb.get("comment_count"), dict):
                cc = int(fb["comment_count"].get("total_count") or 0)
            if isinstance(fb.get("share_count"), dict):
                sc = int(fb["share_count"].get("count") or 0)

        media = []
        for att in (raw.get("attachments") or []):
            if isinstance(att, dict) and isinstance(att.get("media"), dict):
                u = att["media"].get("uri") or att["media"].get("image", {}).get("uri")
                if u:
                    media.append(u)

        pid = str(raw.get("post_id") or raw.get("id") or "")
        ct = raw.get("creation_time")
        try:
            t = datetime.fromtimestamp(int(ct)).isoformat(timespec="seconds") if ct else ""
        except Exception:
            t = ""

        return {
            "post_id": pid,
            "post_url": f"https://www.facebook.com/{pid}" if pid else "",
            "message": msg.strip(),
            "creation_time": t,
            "reaction_count": rc,
            "reaction_breakdown": br,
            "comment_count": cc,
            "share_count": sc,
            "media_urls": media[:10],
        }

    # ---------- Profile Info ----------
    async def profile_info(self, username, url):
        info = {"username": username, "profile_url": url, "display_name": "",
                "friend_count": 0, "follower_count": 0}
        try:
            h1 = await self.page.query_selector_all("h1")
            if h1:
                info["display_name"] = (await h1[0].inner_text()).strip()
            if not info["display_name"]:
                title = await self.page.title()
                if "|" in title:
                    info["display_name"] = title.split("|")[0].strip()

            body = await self.page.inner_text("body")
            mf = re.search(r"([\d,٫\.]+)\s*(?:صديق|friends?)", body, re.IGNORECASE)
            if mf:
                info["friend_count"] = int(re.sub(r"[^\d]", "", mf.group(1)) or 0)
            mfl = re.search(r"([\d,٫\.]+)\s*(?:متابع|followers?)", body, re.IGNORECASE)
            if mfl:
                info["follower_count"] = int(re.sub(r"[^\d]", "", mfl.group(1)) or 0)
        except Exception as e:
            print(f"   ⚠️ info: {e}")
        return info

    # ---------- Run ----------
    async def run(self, keyword, max_posts=30):
        try:
            await self.setup()
            await snap(self.page, "browser_started")

            if not await self.login():
                await snap(self.page, "login_failed_final")
                return

            target = await self.search_user(keyword)
            if not target:
                return

            if not await self.open_profile(target["url"]):
                return

            info = await self.profile_info(target["username"], target["url"])

            TelegramSender.send_message(
                f"<b>👤 معلومات البروفايل</b>\n"
                f"<b>الاسم:</b> {html_escape(info['display_name'] or '—')}\n"
                f"<b>المعرف:</b> <code>{html_escape(target['username'])}</code>\n"
                f"<b>الأصدقاء:</b> {info['friend_count']:,}\n"
                f"<b>المتابعون:</b> {info['follower_count']:,}\n"
                f'<a href="{target["url"]}">🔗 فتح</a>')

            posts = await self.collect(max_posts=max_posts)
            print(f"\n[4] إجمالي: {len(posts)}")

            if not posts:
                TelegramSender.send_message("⚠️ لا منشورات متاحة.")
                return

            TelegramSender.send_message(
                f"<b>📦 بدء الإرسال</b>\n@{html_escape(target['username'])}\n"
                f"📊 {len(posts)} منشور")

            for i, p in enumerate(posts, 1):
                print(f"   [{i}/{len(posts)}] {p['post_id']}")
                rx = " · ".join(f"{k}:{v}" for k, v in p["reaction_breakdown"].items())
                cap = (f"<b>📝 منشور {i}/{len(posts)}</b>\n"
                       f"👤 <code>{html_escape(target['username'])}</code>\n"
                       f"📅 {p['creation_time'] or '—'}\n"
                       f"❤️ {p['reaction_count']:,}  💬 {p['comment_count']:,}  "
                       f"↗️ {p['share_count']:,}\n")
                if rx:
                    cap += f"<i>{html_escape(rx)}</i>\n"
                if p["message"]:
                    cap += f"\n{html_escape(p['message'][:1500])}"
                if p["post_url"]:
                    cap += f'\n\n<a href="{p["post_url"]}">🔗 فتح</a>'
                TelegramSender.send_message(cap)

                for idx, u in enumerate(p["media_urls"][:3]):
                    try:
                        r = _http.get(u, timeout=20)
                        if r.status_code == 200:
                            fp = os.path.join(MEDIA_TMP, f"{p['post_id']}_{idx}.jpg")
                            with open(fp, "wb") as f:
                                f.write(r.content)
                            TelegramSender.send_photo(fp, f"🖼️ {idx+1} من المنشور {i}")
                            os.remove(fp)
                    except Exception:
                        pass
                await HB.delay(0.8, 1.8)

            self._csv(target["username"], posts)
            self._html(target["username"], info, posts)

            await snap(self.page, "final_state")
            TelegramSender.send_message(
                f"<b>✅ اكتمل</b>\n@{html_escape(target['username'])}\n📊 {len(posts)}")

        except Exception as e:
            traceback.print_exc()
            try:
                await snap(self.page, "fatal_exception")
            except Exception:
                pass
            TelegramSender.send_message(f"❌ <code>{html_escape(str(e)[:500])}</code>")

    # ---------- Reports ----------
    def _csv(self, username, posts):
        try:
            p = os.path.join(TMP, f"{username}.csv")
            with open(p, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["post_id", "url", "creation_time", "reactions",
                            "comments", "shares", "breakdown", "message"])
                for x in posts:
                    w.writerow([x["post_id"], x["post_url"], x["creation_time"],
                                x["reaction_count"], x["comment_count"],
                                x["share_count"],
                                json.dumps(x["reaction_breakdown"], ensure_ascii=False),
                                (x["message"] or "")[:2000]])
            TelegramSender.send_document(p, f"📊 CSV — @{username}")
        except Exception as e:
            print(f"⚠️ CSV: {e}")

    def _html(self, username, info, posts):
        try:
            cards = ""
            for p in posts:
                thumb = None
                for u in p["media_urls"][:1]:
                    thumb = Thumb.from_url(u)
                    if thumb:
                        break
                if not thumb:
                    thumb = base64.b64encode(
                        b'<svg xmlns="http://www.w3.org/2000/svg" width="150" height="150">'
                        b'<rect fill="#1e293b" width="150" height="150"/>'
                        b'<text x="75" y="95" font-size="70" text-anchor="middle" fill="#1877f2">f</text>'
                        b'</svg>').decode()
                rx = " · ".join(f"{k}:{v}" for k, v in p["reaction_breakdown"].items())
                cards += f"""<article class="post">
<img src="data:image/jpeg;base64,{thumb}" class="thumb">
<div class="body">
<div class="meta">❤️ {p['reaction_count']:,} · 💬 {p['comment_count']:,} · ↗️ {p['share_count']:,}</div>
<div class="time">📅 {html_escape(p['creation_time'] or '—')}</div>
{f'<div class="react">{html_escape(rx)}</div>' if rx else ''}
<p class="msg">{html_escape((p['message'] or '')[:600])}</p>
<a href="{html_escape(p['post_url'])}" target="_blank">🔗 فتح</a>
</div></article>"""
            html = f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8">
<title>@{html_escape(username)}</title><style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Segoe UI',Tahoma;background:#0f172a;color:#e2e8f0;padding:20px;line-height:1.6}}
.container{{max-width:1200px;margin:auto}}
.header{{background:linear-gradient(135deg,#1877f2,#0a5fd4);border-radius:24px;padding:32px;text-align:center;margin-bottom:20px}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(100px,1fr));gap:10px;margin-top:16px}}
.stats div{{background:rgba(0,0,0,.25);padding:10px;border-radius:12px;font-weight:700}}
.posts-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px}}
.post{{background:rgba(0,0,0,.22);border-radius:16px;overflow:hidden}}
.thumb{{width:100%;height:200px;object-fit:cover}}
.body{{padding:14px;display:flex;flex-direction:column;gap:8px}}
.meta{{color:#94a3b8;font-size:.85rem}}
.msg{{color:#cbd5e1;font-size:.9rem}}a{{color:#64b5f6;font-weight:700;text-decoration:none}}
</style></head><body><div class="container">
<div class="header">
<h1>{html_escape(info['display_name'] or username)}</h1>
<div>@{html_escape(username)}</div>
<div class="stats"><div>📝 {len(posts)}</div><div>👥 {info['friend_count']:,}</div><div>📢 {info['follower_count']:,}</div></div>
</div><div class="posts-grid">{cards}</div></div></body></html>"""
            p = os.path.join(TMP, f"{username}.html")
            with open(p, "w", encoding="utf-8") as f:
                f.write(html)
            TelegramSender.send_document(p, f"🌐 HTML — @{username}")
        except Exception as e:
            print(f"⚠️ HTML: {e}")

    async def close(self):
        try:
            if self.context:
                await self.context.close()
        except Exception:
            pass
        try:
            if self.browser:
                await self.browser.close()
        except Exception:
            pass
        try:
            if self.playwright:
                await self.playwright.stop()
        except Exception:
            pass
        try:
            self.vnc.stop()
        except Exception:
            pass


# ============================================================
# التشغيل
# ============================================================
keyword = input("🔍 اسم الهدف: ").strip() or "Ahmed"
max_posts = int(input("📊 حد المنشورات [30]: ").strip() or "30")

async def main():
    searcher = FacebookSearcher(EMAIL, PASSWORD)
    try:
        await searcher.run(keyword, max_posts=max_posts)
    finally:
        await searcher.close()
        print(f"\n📸 إجمالي الصور: {SHOT_COUNTER['n']}")
        print("✅ انتهى.")

await main()
