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
