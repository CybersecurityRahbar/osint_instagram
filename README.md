# Instagram OSINT Scraper

Colab-based Instagram OSINT utility using an authenticated instagrapi session.

## Google Colab setup

1. In a separate Colab cell, install the exact Instagram client version required by the tool:
   ```python
   !pip install -q --force-reinstall instagrapi==3.0.20
   ```
2. The main tool is a single Python file/cell. Paste the complete `osintinstagram.py` code into one Colab cell and run it.
3. No `requirements.txt` or other dependency file is required by the tool.
4. Keep real credentials out of GitHub. Put them into the runtime/secret mechanism instead of committing them.
5. The tool stores the Instagram session and data on Google Drive so the session can be reused across Colab runtimes.

## Dependency compatibility

The required instagrapi version (`3.0.20`) is declared inside `osintinstagram.py`. At startup the script checks the installed version and stops with a clear message if it does not match. This keeps the one-cell runtime deterministic without requiring an external requirements file.

## Session compatibility

The project targets instagrapi==3.0.20. The code uses load_settings(..., override_app_version=True) for older saved sessions and does not hard-code an Instagram app version.

## 429 handling

HTTP 429 from Instagram is a server-side throttle. The tool does not blindly retry the login endpoint; it enters a cooldown and reports the reason. Avoid repeatedly restarting login attempts while the endpoint is throttled.

## Project context

See PROJECT_CONTEXT.md for the cumulative investigation log and engineering decisions.
