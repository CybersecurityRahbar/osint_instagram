# Instagram OSINT Scraper

Colab-based Instagram OSINT utility built around instaharvest-v2 1.1.88.

## Current tool

The current expanded tool is osintinstagram2.py.
It keeps the tested Anonymous public-data path separate from the authenticated Login path.

## Google Colab setup

1. In a separate Colab cell, install the exact version required by the tool:

       !pip install -q --force-reinstall instaharvest-v2==1.1.88

2. Set the runtime variables in Colab/Secrets, then paste the complete osintinstagram2.py file into one Colab code cell and run it.
3. No requirements.txt or extra Python module is required by the main tool.
4. Google Drive is used for the cumulative database, media and the persistent Login session.

### Runtime variables

Use these only in the Colab runtime/Secrets area, never in GitHub. The configuration names remain visible at the top of `osintinstagram2.py`, but real secret values are not committed:

       import os
       os.environ['NGROK_AUTH_TOKEN'] = '...'
       os.environ['IG_USERNAME'] = '...'
       os.environ['IG_PASSWORD'] = '...'
       os.environ['TELEGRAM_TOKEN'] = '...'
       os.environ['TELEGRAM_CHAT_ID'] = '...'

The tool does not commit these secrets.

## Anonymous / Login architecture

### Anonymous

Anonymous mode uses the public library path:

       Instagram.anonymous(unlimited=True)
       public.get_profile()
       public.get_posts()
       public.get_reels()
       public search/comments/highlights where supported

The normal profile post path is isolated from the authenticated collectors so optional features cannot prevent basic public scraping.

### Login

Login mode is a separate state machine:

1. The tool first looks for its own saved session in Google Drive.
2. A valid saved session is loaded and reused.
3. Password login starts only when no usable saved session exists.
4. If Instagram requests an email/SMS verification code, the Login worker pauses at the library challenge callback.
5. The control panel exposes a verification-code field. The code is kept in RAM only and is not written to GitHub or Drive.
6. After successful authentication, the tool saves the session back to Drive.
7. While the session stays valid, later scrapes and searches reuse the active client instead of logging in again.

Session file:

       Google Drive/Instagram_Scraper_DB/instaharvest_session.json

## Control panel and request volume

The control panel has no automatic status polling loop and makes no page-load /status request.
The ngrok control panel is also protected by a runtime access token printed once in Colab; the browser stores it only in localStorage.
Status refresh is manual. Login start, status refresh, verification-code submission and cancellation are individual requests.
This is intentional so keeping the ngrok page open does not generate hundreds of repeated HTTP 200 status requests in Colab.

## Data collected

Depending on the selected options and what Instagram returns, the tool can collect and store:

- profile information
- posts, photos, videos and carousels
- Reels
- comments
- Stories (Login)
- Highlights
- follower samples (Login)
- following samples (Login)
- like/comment/view/play counters when available
- cumulative SQLite storage
- HTML and CSV reports
- Telegram delivery
- user/hashtag search
- optional Mention Hunter

## Data safety

The existing database path is preserved:

       Google Drive/Instagram_Scraper_DB/instagram_data.db

The normal migrations do not delete the existing database. A database backup is created before schema migration changes when required.

## Security

Never commit Instagram passwords, Telegram bot tokens, ngrok tokens, session files, or verification codes to GitHub.
Browser Session ID cloning remains disabled because it caused account-security problems during earlier testing.

## Project history

See PROJECT_CONTEXT.md for the cumulative debugging history, architecture decisions, runtime evidence and unresolved limitations.
