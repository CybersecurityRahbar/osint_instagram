# OSINT Instagram — Cumulative Project Context

## 2026-10-05 — Login failure investigation and repair

### Repository
- GitHub: CybersecurityRahbar/osint_instagram
- Default branch: main
- Main implementation: osintinstagram.py

### User-reported runtime failure
- set_app_version warning: Client object has no attribute set_app_version
- instagrapi Status 429: Too many requests
- 429 comes from the Instagram CAA login endpoint
- The same login attempt repeats after a POST /start request
- InstagramSearcher then reports failure to obtain an Instagram client

### Root cause identified
1. The project called Client.set_app_version(...). Current instagrapi 3.x no longer exposes that method.
2. The project called client.set_settings(saved, override_app_version=True). In current instagrapi, override_app_version belongs to load_settings(path, override_app_version=True); set_settings() does not take that keyword.
3. instagrapi 3.x changed the default login flow to CAA and changed private transport behavior. The official migration guide recommends loading old sessions with override_app_version=True and migrating old saved sessions to the current transport.
4. The project hard-coded an old Instagram app version 345.0.0.38.107. The current instagrapi configuration uses the supported 449.0.0.52.84 profile, so the project should not hard-code its own obsolete app version.
5. The repository had no dependency pin. An unpinned Colab install can silently move to a newer incompatible instagrapi API. The latest release checked on 2026-10-05 is 3.0.20.
6. get_client() previously returned None after login failure but Flask/ngrok still started. Later /start requests caused another login attempt, potentially amplifying a server-side 429.

### Changes committed
- Replaced obsolete set_app_version usage with load_settings(..., override_app_version=True) for saved sessions.
- Saved-session login now uses login(username, password), allowing instagrapi to validate/reuse a valid session or refresh it when required.
- Saved sessions are migrated to private_transport=curl when supported.
- Added a 15-minute login failure cooldown so 429 does not cause repeated login attempts.
- refresh_if_needed() now treats 429 and other validation failures as failures instead of incorrectly returning True.
- Improved client-init error reporting.
- Added story_url to the stories schema and a migration for existing databases.
- Added startup printing of the installed instagrapi version.
- Added requirements.txt pinning instagrapi==3.0.20.
- Updated README.md with reproducible Colab setup and 429 handling notes.

### Current verification status
- Repository inspection and API compatibility review completed.
- Live Instagram authentication cannot be tested from this environment because the user's Google Drive session and account are not available here.
- The first live validation should use the pinned dependency and the existing Drive session file; do not repeatedly retry while Instagram is returning 429.

### Security note
- Never commit real Instagram passwords, Telegram bot tokens, or ngrok auth tokens to this public repository. Supply them at runtime through Colab/secret storage.

## Future log
Append every future investigation, decision, code change, test result, and unresolved issue here so the project history remains cumulative.


## 2026-10-05 — Secondary audit after login repair

### Additional request-volume and data-integrity findings
- Smart Merge (newest-first) made a redundant second `user_medias()` request after already fetching enough media. This was unnecessary request volume and could worsen Instagram throttling.
- The Posts + Videos mode could return up to roughly twice the requested count because it concatenated `count` posts and `count` clips. It now merges, deduplicates, sorts, and enforces the requested final count.
- The UI exposed a Mention Hunter checkbox, but the backend ignored it: `UniversalSearcher.search()` always ran Mention Hunter. Mention Hunter makes additional hashtag and comment requests. The backend now honors the checkbox.
- `accounts` used `INSERT OR REPLACE`, which can replace the row and change its primary key. It now uses SQLite `ON CONFLICT(username) DO UPDATE`, preserving the account row identity and its foreign-key relationships.

### Remaining important findings not yet fully refactored
- `followers.username` is globally UNIQUE, so the same follower cannot be stored independently for multiple target accounts.
- `fetch_highlights()` currently stores highlight metadata but does not actually download highlight items despite the UI wording suggesting full highlight retrieval.
- The ngrok/Flask interface is publicly reachable and has no application-level authentication. Anyone who obtains the ngrok URL can submit `/start` or `/search` while the process owns an authenticated Instagram session. This should be fixed before treating the tool as safe for long-lived/public deployment.
- The thumbnail cache is labeled LRU but is implemented as insertion-order eviction rather than true least-recently-used behavior.

### Current validation
- Static verification after the second patch confirms:
  - no remaining `set_app_version` call;
  - no remaining `set_settings(..., override_app_version=...)` call;
  - saved sessions use `load_settings(..., override_app_version=True)`;
  - private transport migration to curl is present when supported;
  - dependency is pinned to `instagrapi==3.0.20`;
  - 429 login cooldown is present;
  - Mention Hunter checkbox is honored;
  - redundant Smart Merge request was removed;
  - story_url migration is present.
- Live Instagram authentication remains unverified here because the user's Colab runtime, Google Drive session file, and account are not accessible to this environment.

### Current upstream note
- As of 2026-10-05, instagrapi 3.0.20 is the latest release. Its release notes include use of the native Android 449 profile and preservation of CAA login context. The v3 migration guide documents `load_settings(..., override_app_version=True)`, CAA login, and curl-based private HTTP/2 transport migration.

### Next user test
Do not delete the existing Drive session before testing. First restart/clean the Colab runtime, install the pinned dependency, verify the installed version, confirm the session file exists, and run the script once. If Instagram still returns HTTP 429 from the CAA login endpoint, stop retrying and treat it as an Instagram-side throttle rather than a Python exception to brute-force around.

## 2026-10-05 — Single-cell Colab dependency workflow

### User workflow decision
- The user runs the entire Instagram OSINT tool by pasting the complete `osintinstagram.py` content into ONE Google Colab cell.
- The user does not want `requirements.txt` or other dependency files added to the Colab workflow.
- Dependency-installation commands will be run manually by the user in a separate Colab cell.

### Implementation change
- Removed `requirements.txt` from the repository.
- Embedded `REQUIRED_INSTAGRAPI_VERSION = "3.0.20"` directly inside `osintinstagram.py`.
- `osintinstagram.py` now checks the installed instagrapi version before importing it.
- If the required version is missing or different, the script stops with a clear message telling the user to install the required version in the separate installation cell.
- This avoids silent API changes while preserving the user's preferred one-cell tool workflow.

### Current Colab installation command
```python
!pip install -q --force-reinstall instagrapi==3.0.20
```

### Validation note
- The repository now contains no external requirements file for this workflow.
- The main tool remains a single-file/single-cell implementation.

## 2026-10-05 — Instagram authentication 429 after dependency repair

### New runtime evidence
- With instagrapi==3.0.20 installed, the tool no longer shows the old `set_app_version` compatibility error.
- A fresh runtime with no saved Instagram session now reaches the current CAA login endpoint and receives HTTP 429:
  `https://b.i.instagram.com/api/v1/bloks/async_action/com.bloks.www.bloks.caa.login.async.send_login_request/`
- Therefore the remaining failure is not the previous Python API mismatch. It is an Instagram-side throttling/anti-abuse response on the login path.

### Upstream verification
- Current instagrapi documentation states that login() uses the CAA flow by default in v3.
- Current best-practices documentation treats HTTP 429 as throttling related to current IP/request pattern and recommends stopping the burst, backing off, reducing concurrency, and avoiding blind retries.
- Current instagrapi exposes `login_by_sessionid()` as a lightweight compatibility login path.
- Current release history shows 3.0.20 as the latest release checked on 2026-10-05, so simply upgrading again is not a credible fix for this specific 429.

### Engineering decision
- Do NOT solve the 429 by repeatedly calling `login()`, `login_legacy()`, rotating requests rapidly, or shortening the cooldown.
- The tool now supports an alternate bootstrap path using a Session ID obtained from the user's own already-authenticated Instagram browser session.
- `IG_SESSIONID = ""` was added as an optional runtime setting; the value must never be committed to GitHub.
- The tool also checks environment variable `IG_SESSIONID`.
- If a Session ID is provided, it is tried before password/CAA login.
- If password/CAA login returns 429, the tool does not retry the password endpoint. It optionally prompts once for a Session ID through hidden `getpass()` input and attempts that alternate path.
- A successful Session ID login is persisted to the normal Google Drive `ig_tool_session.json` file, so subsequent runs can use the saved instagrapi session.
- A configured Session ID is also allowed to recover the session while the password-login cooldown is active.

### Important limitation
- A 429 is imposed by Instagram and cannot be guaranteed to disappear through Python code alone. If Instagram rejects both the CAA login and the supplied Session ID, the remaining issue is the account/IP/session identity rather than the library version.
- Do not delete a valid saved session and do not repeatedly retry while Instagram is throttling.

### Current validation status
- Code path reviewed after patch.
- Static API compatibility remains consistent with instagrapi 3.x: saved sessions use `load_settings(..., override_app_version=True)` and private transport migration to curl when supported.
- Live authentication is still not testable from this environment because the user's Instagram account and Colab runtime are private.

### Session ID fallback hardening
- Session ID recovery is throttled separately so an invalid Session ID cannot create a new rapid retry loop.
- During the password-login cooldown, a configured Session ID may still be attempted as the alternate authentication path, but only under its own cooldown.
- Saved Drive sessions remain the preferred persistent session source; Session ID is a bootstrap/recovery mechanism.

## 2026-10-05 — Session authentication succeeded; post-login API mismatch found

### New runtime evidence
- The password/CAA login returned HTTP 429 as before.
- The new Session ID fallback then returned:
  `✅ تم إنشاء جلسة Instagram من Session ID بعد 429`
- Therefore Instagram authentication itself succeeded through the Session ID path.
- Immediately afterward, the tool's session validation raised:
  `AttributeError: 'Client' object has no attribute 'user_timeline'`
- The error appeared twice during the `/start` processing.

### Root cause
- `user_timeline()` is an obsolete API call in this project and is not present in instagrapi 3.0.20.
- Current instagrapi 3.0.20 exposes `account_info()` for retrieving/validating the authenticated account. The 3.0.20 documentation also documents current timeline methods such as `get_timeline_feed()`; `user_timeline()` is not the current validation API.

### Fix
- Replaced `refresh_if_needed()` validation call from `user_timeline(amount=1)` to `account_info()`.
- Added a stronger saved-session path: after loading `ig_tool_session.json`, the tool validates it with `account_info()`. If valid, it immediately reuses the session and does NOT call `login(username, password)` again.
- If saved-session validation itself returns 429, the tool now stops rather than falling through to another password/CAA login.
- This prevents unnecessary login requests after a successful Session ID bootstrap and reduces the chance of repeated throttling.

### Current expected successful flow
1. Existing saved session is loaded.
2. `account_info()` validates it.
3. If valid: continue directly with the authenticated client.
4. Only if no valid saved session exists: use configured Session ID.
5. Only if no usable Session ID exists: attempt password/CAA once.
6. On CAA 429: never blindly retry password/CAA.

### Validation status
- Static scan confirms the functional `user_timeline()` call was removed; only a comment mentions the obsolete name.
- Current session validation now targets `account_info()`.
- Live Colab execution is still the final required validation step because the actual Instagram session is private.

## 2026-10-05 — Media completeness, session resilience, direct-profile lookup, and Telegram modernization

### User runtime evidence
- Instagram authentication now succeeds through the saved Session ID.
- The profile task selected the requested 10 publications, but only four video files reached Telegram; several image/video downloads failed with `TooManyRedirects`.
- The same first task later produced HTTP 429 during a session validation call.
- A second task was perceived as having an ended session because the previous code treated that 429 as a disconnected/invalid session.
- The direct profile workflow also logged `search_users: TypeError`, despite the user not using the explicit Search tab.

### Root causes
1. Media downloading used raw `requests.get()` against Instagram CDN URLs. This can fail with redirect churn and bypasses the downloader logic shipped with instagrapi.
2. The code used `ThreadPoolExecutor` to download multiple Instagram media objects concurrently, increasing burstiness and request pressure.
3. `save_post_if_not_exists()` returned no ID when a post already existed. A post whose database row was created before a failed download could therefore be skipped forever on later runs.
4. The session validation path made a network `account_info()` request too frequently. A 429 from that validation was incorrectly recorded as a login failure.
5. Direct username lookup unnecessarily used `search_users()`. In instagrapi 3.x the method signature is `search_users(query)`, so passing a count caused the observed TypeError; moreover, Search is unnecessary for a direct username target.
6. The explicit Universal Search helpers still used old count arguments for `search_users`, `search_hashtags`, `search_reels`, and `search_music`.

### Engineering changes
- Normal profile scraping now uses `user_medias()` as the primary feed source and no longer calls `user_clips()` in the normal Smart Merge path. This avoids the observed `clips: login_required` request and reduces API volume. instagrapi documentation describes `user_medias()` as the user's feed media and includes photo/video/album media types.
- Media download now uses instagrapi's native `photo_download()`, `video_download()`, and `album_download()` helpers, with a single `*_download_by_url()` fallback.
- Instagram media downloads are now sequential rather than concurrent.
- Existing database post rows can now be reprocessed, so a previous failed download can be retried and completed later.
- Post delivery to Telegram is numbered `#01 / N`, includes metadata, an Instagram link button, and uses HTML captions with caption-above-media.
- Profile cards now use `user_info_by_username()` directly, include profile photo plus public account statistics, verification/privacy/account type/category/external URL when available, and are sent as modern Telegram content.
- The tool now supports Telegram Rich Messages through Bot API `sendRichMessage`, with an HTML fallback to `sendMessage`.
- Telegram media messages use the current `show_caption_above_media` option and inline URL buttons.
- 429 handling is now a separate throttle state. A 429 does not clear the client, does not mark the session as logged out, and causes the current task to stop safely until the cooldown expires.
- Session validation is cached for 10 minutes and is skipped during an active 429 cooldown.
- The current task saves the session before entering the 429 cooldown.
- Explicit Search helpers were updated to call current one-argument search methods and slice the returned results locally.

### Telegram current-version verification
- Telegram Bot API 10.1 introduced Rich Messages on June 11, 2026.
- Bot API 10.2 expanded Rich Messages with structured blocks and media support on July 14, 2026.
- Bot API 10.3 on August 24, 2026 added expandable block quotations and additional Rich Message features.
- The project therefore now targets the current Telegram Bot API capabilities available through raw HTTPS calls without requiring a Telegram Python SDK.

### Validation status
- Static verification after this patch confirms:
  - direct profile flow no longer calls `search_users()`;
  - obsolete `user_timeline()` call is absent;
  - normal Smart Merge no longer invokes `user_clips()`;
  - native media download helpers are present;
  - concurrent media download block is absent from the processing path;
  - existing post rows are retryable;
  - 429 has a dedicated throttle state separate from login failure;
  - Telegram Rich Message endpoint and caption-above-media support are present.
- Live Colab validation remains the final test for actual Instagram CDN downloads, media completeness, and Telegram rendering.

## 2026-10-05 — Critical account-security incident and change of direction

### User runtime evidence
- A browser-derived Session ID was accepted by instagrapi and initially allowed profile/media extraction, including photos, videos, comments and likes.
- During the same workflow, follower-list retrieval and Story/Highlights retrieval hit Instagram public/GraphQL challenge paths and returned HTML challenge pages instead of JSON.
- Instagram subsequently returned `ChallengeRequired` with the message:
  `Manual verification required via Instagram native challenge flow...`
- The user reports that the Instagram account was logged out on laptop and phone and the account displayed a security warning requesting email/contact-point changes.
- The user confirms this pattern had happened repeatedly in older versions of the tool when copying a browser session.

### Updated diagnosis
1. Browser Session ID cloning is not a stable authentication mechanism for this project. The observed logout/security checkpoint is strong evidence that moving a live browser session into Colab is triggering Instagram's account/session integrity checks.
2. The current `accounts/update_risky_contactpoint` and native challenge responses show an account-security checkpoint, not a normal scraper API error.
3. The fresh password CAA path independently remains unreliable: instagrapi currently has an open issue for fresh login returning HTTP 429 at `send_login_request` (#2852, opened 2026-10-02), even on current 3.0.20.
4. Therefore there is no responsible code-only guarantee that can restore the old high-volume password-login workflow against Instagram's current server-side anti-abuse system.

### Safety/architecture decision
- Stop using browser Session IDs in the tool. The Session ID prompt/bootstrap was disabled.
- Do not attempt to bypass Native/Bloks challenge flows, rotate identities aggressively, or keep retrying login after 429.
- The tool now treats `ChallengeRequired` as a hard circuit-breaker and preserves the existing session state instead of attempting further authentication.
- Continue to preserve completed media locally so interrupted jobs can be resumed without re-downloading successful files.
- For reliable authorized access to accounts the user controls, investigate Meta's official Instagram Platform API.
- For arbitrary public OSINT targets, document that Stories/follower lists and other protected surfaces are not guaranteed through the official API; the current unofficial private/web endpoints may trigger account challenges and should not be forced.

### Immediate account-protection action
- The affected personal Instagram account should be secured through Instagram's official recovery/security UI before any more automation is attempted. Do not reuse the browser Session ID in Colab.

## 2026-10-05 — Rebuild around the proven legacy password-login architecture

### User requirement reaffirmed
- The user confirms the original password/username version previously fetched profile data, posts/photos, videos, Stories, Highlights, comments, likes, views, and a requested follower sample (for example 100 names).
- The old implementation remains in Git history and is the baseline evidence that these features worked together in a real Colab session.
- The objective is therefore compatibility repair and architecture preservation, not declaring the feature set impossible.

### Upstream 3.0.20 capabilities verified
- `Client.login_legacy()` is an official entry point that explicitly selects the previous Instagram login flow.
- The v3 code supports selecting `private_transport="requests"` for compatibility with the previous private transport.
- Private Mobile API helpers exist for `user_stories_v1()`, `user_followers_v1()`, and `user_highlights_v1()`.
- Highlight extraction in 3.0.20 includes its story items.
- Native media download helpers include `photo_download()`, `video_download()`, `album_download()`, and `story_download()`.
- Media objects expose `like_count`, `comment_count`, `view_count`, and `play_count`.

### Implementation changes
- The project now sets `LOGIN_STRATEGY = "legacy_first"`.
- Fresh password authentication now uses `login_legacy(username, password)` when available.
- The tool uses `private_transport="requests"` for the legacy strategy, including saved sessions, so the authentication/data path stays internally consistent.
- The old browser Session ID bootstrap remains disabled.
- A new tool-owned session file is used:
  `/content/drive/MyDrive/ig_tool_session_legacy_v3.json`
  This intentionally avoids reusing the previously cloned browser-session file.
- Direct username resolution now prefers `user_info_by_username_v1()` and stays inside private API paths.
- Stories prefer `user_stories_v1()` and use native `story_download()`.
- Followers prefer `user_followers_v1()`; the requested number is saved and sent to Telegram in numbered chunks of 20.
- Highlights prefer `user_highlights_v1()`, and the returned highlight `items` are downloaded and stored.
- Comments prefer `media_comments_v1()`.
- Media records store view/play counts in SQLite and Telegram/HTML output.
- Existing follower records are migrated from global `username UNIQUE` to composite `UNIQUE(account_id, username)`.
- Existing media DB rows remain retryable after failed downloads.
- Normal profile media collection uses `user_medias()`; the separate clips endpoint is not used in normal scraping.

### Concurrency decision
- The original user workflow used aggressive parallelism and was capable of very high-volume scraping.
- The current implementation intentionally does not restore 10 simultaneous Instagram requests. The observed 429/Challenge account events show that server-side anti-abuse is now sensitive to burst patterns.
- Controlled Instagram request sequencing is used for reliability. Local file/Telegram work can be optimized separately without multiplying private API requests.

### Expected authentication model
- First run: username/password → legacy private login → save tool-owned session.
- Later runs: load the same saved settings → validate with `account_info()` only when needed → reuse session without fresh login.
- 429 or ChallengeRequired: stop the affected task and preserve the session; do not attempt to evade the challenge.

### Validation status
- Static source review completed for login strategy, private v1 story/follower/highlight paths, native story/media download, view/play metrics, follower DB migration, and legacy-session isolation.
- Live authentication/data collection still requires testing by the user in Colab with an authorized account after the account-security warning is fully resolved.

### Final hardening in this iteration
- The legacy strategy uses a new session filename `ig_tool_session_legacy_v3.json` so the next test does not reuse the browser-cloned session file that caused the account security incident.
- `login_legacy()` is given `private_transport="requests"` consistently for both initial and saved-session clients.
- Optional 2FA/TOTP/SMS/backup-code entry is handled through `getpass()` when instagrapi raises `TwoFactorRequired`; no codes are stored in GitHub.
- Instagram jobs are serialized with a process-wide lock so simultaneous `/start` and `/search` requests cannot create bursts against one authenticated session.
- Follower names are sent in numbered groups of 20, preserving all requested entries without Telegram's 4096-character truncation.
- The old browser Session ID code path remains disabled.

## 2026-10-06 — اكتشاف السبب الفعلي لفشل legacy_first وتغييره إلى Legacy مباشر

### دليل التشغيل الذي قدمه المستخدم
- في Google Colab، بعد تثبيت `instagrapi 3.0.20` وتشغيل النسخة الحالية، ظهرت:
  `Status 429: Too many requests`
  ثم رسالة الأداة: `Instagram أعاد 429 من CAA أثناء تسجيل الدخول`.
- المستخدم استخدم حساب Instagram جديد مخصص للأداة واسم المستخدم/كلمة المرور، ولم توجد جلسة محفوظة بعد.

### السبب المؤكد من مصدر instagrapi 3.0.20
- مراجعة `instagrapi 3.0.20` أظهرت أن `Client.login_legacy()` يبدأ من `accounts/login/`، لكن عند استجابة `BadPassword` بدون سياق 2FA يستدعي `_try_caa_login()`.
- كما أن `UnknownError` من نوع `needs_upgrade` يؤدي أيضًا إلى `_try_caa_login()`.
- لذلك إعداد المشروع `LOGIN_STRATEGY = legacy_first` لم يكن Legacy فقط بصورة صارمة؛ كان يسمح للمكتبة نفسها بالانتقال إلى CAA.
- هذا يفسر ظهور CAA في سجل المستخدم رغم أن الأداة كانت تستدعي `login_legacy()`.

### التغيير في 2026-10-06
- تغيير الاستراتيجية إلى `LOGIN_STRATEGY = strict_legacy`.
- إضافة `_strict_legacy_password_login()` داخل `osintinstagram.py`.
- التدفق الجديد للدخول الأول: `pre_login_flow` → `accounts/login/` → استخراج authorization → `login_flow` → حفظ جلسة الأداة.
- إذا أعاد `pre_login_flow` خطأ 429، يستمر التدفق مثل login القديم بدلاً من الانتقال إلى CAA.
- لا يوجد CAA fallback في هذا المسار.
- 2FA يستخدم `accounts/two_factor_login/` مباشرة في هذا المسار، ولا يُحوَّل تلقائيًا إلى Bloks/CAA.
- عند نجاح الدخول، تُحفظ إعدادات الجلسة المملوكة للأداة في Google Drive: `/content/drive/MyDrive/ig_tool_session_legacy_v3.json`.
- عند 429 في النسخة الجديدة، الرسالة تذكر صراحة أن 429 من `pre_login` أو `accounts/login`، وليس CAA.

### ماذا سيحسم الاختبار القادم
- إذا نجح `accounts/login/`: تم فعليًا استرجاع مسار كلمة المرور القديم وإنشاء جلسة مستقلة للأداة.
- إذا عاد 429 من `accounts/login/`: المشكلة ليست CAA؛ تكون في قبول Instagram نفسه لإنشاء جلسة جديدة من بيئة Colab، وسجل الخطأ الجديد سيكشف المرحلة بدقة.
- إذا ظهر `needs_upgrade` أو رفض مشابه من `accounts/login/`: سيظهر الآن الخطأ الأصلي بدلاً من إخفائه خلف CAA.
- إذا نجح أول دخول، يجب إعادة استخدام ملف الجلسة وعدم تنفيذ password login في كل تشغيل.

### ملاحظة مهمة
- هذا التغيير لا ينسخ Session ID من المتصفح ولا يحاول تجاوز تحدي Instagram أو 429.
- لا يوجد ضمان أن Instagram سيقبل إنشاء جلسة جديدة من Colab؛ الهدف هنا إزالة التحويل الخفي إلى CAA واستعادة السلوك القديم الذي كان المستخدم يريده، مع تشخيص صريح للرد الحقيقي من endpoint القديم.

### قاعدة البيانات
- لا تغيير في مسار قاعدة البيانات أو حذف السجلات.
- النسخة الاحتياطية التلقائية قبل migrations ما زالت مفعلة.

## 2026-10-06 — اختبار strict_legacy كشف رفض ملف التطبيق 449

### نتيجة المستخدم بعد strict_legacy
- أصبح سجل الدخول:
  `🔐 Legacy direct: accounts/login/ فقط — بدون CAA fallback`
  ثم:
  `Your version of Instagram is out of date. Please upgrade your app to log in to Instagram.`
- هذا يثبت أن الطلب وصل إلى مسار `accounts/login/` فعلاً، وأن سبب فشل CAA السابق لم يعد يخفي النتيجة الحقيقية.

### التحقق من الحالة الحالية
- إصدار `instagrapi 3.0.20` يستخدم ملف Android افتراضياً لـ Instagram 449.
- صفحة إصدارات Instagram الحالية التي تم العثور عليها في 2026-10-06 تعرض `450.0.0.50.77` كإصدار Android الأحدث المنشور في 2026-10-05، مع version code `385611438`.
- upstream instagrapi لديه بالفعل issue مفتوح حول `needs_upgrade` أثناء username/password login، لذلك انتقالنا إلى strict_legacy كشف المشكلة بدلاً من إخفائها خلف CAA.

### التعديل في هذه النسخة
- إضافة:
  `LEGACY_APP_VERSION = "450.0.0.50.77"`
  `LEGACY_APP_VERSION_CODE = "385611438"`
- قبل `accounts/login/` يتم تطبيق app_version/version_code صراحةً على عميل Legacy باستخدام `set_device()`.
- يتم تطبيق الملف نفسه على العميل بعد تحميل الجلسة أيضاً، حتى لا تعيد `override_app_version=True` الملف تلقائياً إلى 449 قبل الاستخدام.
- لا توجد محاولة CAA أو Session ID في هذا المسار.

### تفسير الاختبار القادم
- نجاح الدخول بعد تحديث الملف إلى 450 سيعني أن سبب `needs_upgrade` كان profile قديم في بيئة الأداة.
- استمرار `needs_upgrade` بعد 450 سيعني أن Instagram يرفض الحساب/مسار `accounts/login/` نفسه رغم ملف التطبيق الحالي، وحينها لن نستمر في تغيير أرقام الإصدارات عشوائياً.

## 2026-10-06 — توسعة osintinstagram2.py إلى Anonymous + Login

### الهدف
- الاحتفاظ بـ `instaharvest-v2 1.1.88` كقاعدة النسخة الثانية لأن المستخدم اختبرها عملياً ونجحت في جلب منشورات الحسابات العامة بدون تسجيل دخول.
- عدم استبدال المسار Anonymous الذي نجح.
- إضافة Login Mode قابل للتبديل من صفحة التحكم نفسها.
- استخدام جلسة مملوكة للأداة ومحفوظة في Google Drive، بدلاً من Session ID من المتصفح.

### ما أضيف
- تبديل حي من صفحة Flask عبر `/mode` بين:
  - `anonymous`: لا يحتاج تسجيل دخول، للبيانات العامة.
  - `login`: يستعمل `IG_USERNAME/IG_PASSWORD` أو الجلسة المحفوظة.
- حالة حيّة للعميل عبر `/status` مع polling من صفحة التحكم.
- حفظ جلسة Login في:
  `/content/drive/MyDrive/Instagram_Scraper_DB/instaharvest_session.json`
- تحميل الجلسة والتحقق منها قبل إعادة استخدام كلمة المرور.
- عند فشل Login يعود العميل إلى Anonymous حتى لا يتوقف السيرفر.
- Stories عبر `stories.get_user_stories` في Login Mode مع fallback العام عند توفره.
- Followers عبر `friendships.get_all_followers`، مع fallback لـ GraphQL عند توفره.
- Following عبر `friendships.get_all_following`.
- Highlights عبر `stories.get_highlights_tray` مع fallback العام.
- تنزيل الوسائط باستخدام downloader الخاص بالمكتبة في Login Mode أولاً، ثم URL fallback.
- Comments من الـpublic API أو `media.get_comments_parsed`.
- Mention Hunter موسع على الهاشتاج/الكابشن والتعليقات، مع تخزين النتائج في SQLite.
- البحث في Login Mode يستخدم `search_users` و`search_hashtags`، مع public fallback.
- CSV يشمل likes/comments/views/plays.
- HTML report يشمل profile/stories/highlights/followers/following/posts.
- Following وHighlights أصبحت لها جداول تخزين تراكمية.
- بيانات المنشور الموجود في SQLite يتم تحديث إحصاءاته عند إعادة العثور عليه بدلاً من تجاهله تماماً.

### قاعدة البيانات
- نفس `instagram_data.db` السابق ما زال مستخدماً.
- تمت إضافة أعمدة profile إضافية إلى `accounts` بطريقة ALTER TABLE فقط.
- تمت إضافة جدول `following` مع uniqueness لكل account.
- قبل migration توجد نسخة احتياطية تلقائية عندما تكون تغييرات schema مطلوبة.
- لا يتم حذف المنشورات أو التعليقات أو القصص القديمة بسبب هذه التوسعة.

### التشغيل
- الوضع الافتراضي يبقى Anonymous.
- المستخدم يستطيع فتح صفحة التحكم على عنوان ngrok الحالي، اختيار Login، الضغط على "تطبيق الوضع"، وانتظار ظهور الحالة `Login — العميل جاهز`.
- بعد ذلك يمكن تفعيل Stories وFollowers وFollowing وHighlights من نموذج السكراب.
- الرجوع إلى Anonymous لا يحذف ملف جلسة Login.

### ملاحظة الاختبار
- الاختبار الواقعي النهائي يجب أن يكون في Colab نفسه، لأن قبول Instagram لعملية Login والجلسة لا يمكن محاكاته محلياً.
- لا ينبغي إعادة استخدام Session ID من المتصفح بعد حادثة تسجيل الخروج/التحقق السابقة.
- `instaharvest-v2` يعلن رسمياً في وثائقه عن دعم login/save_session/load_session وStories/Highlights وFollowers وDownload، وهو سبب اعتماد هذه الواجهة على تلك المسارات.

## 2026-10-06 — تصحيح توسعة osintinstagram2.py بعد فشل Anonymous

### ملاحظة المستخدم
- بعد التوسعة الأولى، Anonymous كان يعرض صورة البروفايل ومعلومات الحساب فقط، بينما لم تظهر المنشورات/بقية البيانات.
- المستخدم لم يختبر Login Mode بعد.

### السبب الهندسي
- التوسعة الأولى وسّعت دورة `scrape` وعميل Instagram أكثر مما يلزم بدلاً من إبقاء مسار Anonymous الذي تم اختباره ميدانياً كما هو.
- أضيفت مراحل مثل Reels وLogin-aware downloader وإدارة العميل قبل طبقة المنشورات، وبعض الاستثناءات كانت تتحول إلى قائمة فارغة، مما جعل الفشل غير واضح.
- لذلك كان القرار الصحيح هو عزل المسار الأساسي للمنشورات عن الميزات الإضافية.

### التصحيح
- أعيدت Anonymous user resolution إلى `public.get_profile`.
- أعيد مسار المنشورات الأساسي إلى `public.get_posts(username, max_count=...)` مباشرة.
- أضيفت رسائل تشخيص صريحة:
  - عدد المنشورات التي أعادها `public.get_posts`.
  - عدد المنشورات التي ستتم معالجتها.
  - الكود الحالي لكل منشور أثناء المعالجة.
- أعيد تنزيل Anonymous إلى المسار المباشر من `video_url/display_url/thumbnail_url` الذي كان مستخدماً في النسخة الأصلية.
- Reels وStories وHighlights وFollowers وFollowing أصبحت طبقات مستقلة ولا تمنع معالجة المنشورات الأساسية.
- لم يتم حذف أي ميزة Login التي سبق إضافتها؛ Login session switching ما زال موجوداً.

## 2026-10-06 — Comprehensive audit: asynchronous Login + email/SMS verification + zero automatic polling

### User-reported issues
- The current repository version was incomplete from the user's perspective and contained runtime errors.
- Opening the ngrok control panel produced repeated HTTP 200 requests in the Colab cell until a scrape/search was started.
- Selecting Login from the control panel did not reliably activate the Login client.
- Instagram sends a verification code to the user's phone/email during authentication, but the tool had no web-based place to enter that code.
- The authenticated session must persist in Google Drive and be reused for later requests/searches instead of asking for Login every time.

### Root causes found in osintinstagram2.py
1. Login switching was synchronous inside the /mode HTTP request. A verification challenge could block or fail the request before a user could provide a code.
2. There was no bridge between instaharvest-v2's supported challenge_callback and the web control panel.
3. The UI still invoked refreshStatus() once when the page loaded; the revised implementation removes even that automatic status request so page-open activity is not a source of repeated /status traffic.
4. The UI did not have a robust state model for an in-progress Login challenge.

### Changes committed
- Login is now an asynchronous background operation.
- The active Anonymous client is preserved until Login actually succeeds.
- Login state is exposed in /status as starting, loading_session, authenticating, waiting_code, verifying, ready, error or cancelled.
- Added Instagram(challenge_callback=instagram_challenge_callback).
- Added a RAM-only verification-code bridge with /auth/code and /auth/cancel.
- Verification codes are never written to Drive or GitHub.
- The control panel shows the code field when the Login challenge is waiting.
- Removed the page-load refreshStatus() call; status refresh is manual only.
- Added /favicon.ico returning HTTP 204.
- Login first tries instaharvest_session.json from Google Drive and reuses the resulting active client for later scrapes/searches.
- A successful password + verification login saves the session to the same Drive file.
- A 429 while validating a saved session does not fall through immediately into another password login.
- Scrape/search endpoints reject requests while Login is in its authentication or verification state.

### Upstream API verification
- PyPI documentation for instaharvest-v2 1.1.88 explicitly documents login(username, password), auth.save_session(), auth.load_session(), and challenge_callback for email/SMS challenges.
- The same documentation confirms anonymous Instagram.anonymous(unlimited=True), Stories, Followers, Following, Highlights and download modules.
- Source checked: PyPI instaharvest-v2 1.1.88 on 2026-10-06.

### Validation status
- GitHub source review completed after the patch.
- Static marker checks confirm the challenge callback, background Login worker, persistent session load/save, /auth/code, /auth/cancel and removal of automatic status polling.
- Actual Instagram authentication still requires the user's Colab runtime because the live account, network identity and Google Drive session are not accessible from this environment.

### Important limitation
- The revised control panel itself makes no background /status requests. If the exact new revision still produces repeated HTTP 200 lines after opening the page, those requests are originating from another client/tab/proxy layer or from an older server instance, not from the current page JavaScript.

## 2026-10-06 — Final control-panel security hardening

- The ngrok control panel is publicly reachable, so leaving control endpoints unauthenticated was an unsafe design.
- Added a per-Colab-runtime control token using Python `secrets`. If `CONTROL_TOKEN` is not supplied, the tool generates one and prints it once in Colab.
- Protected `/status`, `/mode`, `/auth/code`, `/auth/cancel`, `/start`, and `/search` with the `X-Control-Token` header.
- The browser stores the token only in localStorage; it is not written to Google Drive or GitHub.
- `/`, `/health`, and `/favicon.ico` remain public so the panel can load and the browser does not create unnecessary authenticated noise.
- The new token does not create background polling; status remains manual.

## 2026-10-06 — Fixed ngrok domain and secret handling decision

- User requested restoring a fully visible configuration block and keeping the ngrok URL permanently unchanged.
- The code now has a visible configuration block at the top of `osintinstagram2.py` containing the names of the ngrok, Instagram and Telegram settings.
- Real passwords/tokens are intentionally not committed to the public GitHub repository. They remain runtime/Colab variables. The old exposed credentials must be rotated because Git history may still contain them.
- The ngrok startup path now explicitly requests `https://yin-spender-percent.ngrok-free.dev` and does not fall back to a random URL. If the domain is not available to the authenticated ngrok account, startup fails loudly instead of producing a different address.
- Current ngrok documentation describes account development domains and fixed/custom endpoint URLs; exact availability of this specific domain can only be confirmed from the user's ngrok account at runtime.
