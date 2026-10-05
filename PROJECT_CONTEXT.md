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

