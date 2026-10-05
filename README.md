# Instagram OSINT Scraper

Colab-based Instagram OSINT utility using an authenticated instagrapi session.

## Google Colab setup

1. Install the pinned dependency:
   ```python
   !pip install -q -r requirements.txt
   ```
2. Keep real credentials out of GitHub. Put them into the runtime/secret mechanism instead of committing them.
3. Run osintinstagram.py.
4. The tool stores the Instagram session and data on Google Drive so the session can be reused across Colab runtimes.

## Session compatibility

The project targets instagrapi==3.0.20. The code uses load_settings(..., override_app_version=True) for older saved sessions and does not hard-code an Instagram app version.

## 429 handling

HTTP 429 from Instagram is a server-side throttle. The tool does not blindly retry the login endpoint; it enters a cooldown and reports the reason. Avoid repeatedly restarting login attempts while the endpoint is throttled.

## Project context

See PROJECT_CONTEXT.md for the cumulative investigation log and engineering decisions.
