"""Session Seeding (`scripts/seed_session.py`) — B8 / FR-11 / §12.6.

Local, headed login helper. Opens a persistent browser context at the target
profile directory, lets you log in by hand, and persists the login state into
that directory (cookies / localStorage / IndexedDB). The directory is then
uploaded to the Railway Volume at the same path so the headless web Providers
reuse the session. See docs/session_seeding.md for the full workflow.

Run locally (needs a display + `playwright install chromium`):

    python scripts/seed_session.py --site chatgpt --profile data/profiles/chatgpt_acc1

The profile path should match `providers.yaml` (e.g. chatgpt_web_1.profile).
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys

# Sites that have a web Provider module exposing a SITE config.
WEB_SITES = ("chatgpt", "claude", "deepseek", "zai", "kimi", "qwen", "gemini")


def _site_url(site: str) -> str:
    mod = importlib.import_module(f"src.providers.{site}")
    return mod.SITE["url"]


def seed(site: str, profile: str) -> int:
    if site not in WEB_SITES:
        print(f"error: --site must be one of {WEB_SITES} (API providers need no seeding)")
        return 2

    url = _site_url(site)
    os.makedirs(profile, exist_ok=True)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        # Headed + persistent: the login state is written into `profile`.
        ctx = pw.chromium.launch_persistent_context(
            profile,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(url, wait_until="domcontentloaded")

        print("\n" + "=" * 64)
        print(f"  Seeding '{site}' into: {profile}")
        print(f"  A browser window opened at {url}.")
        print("  1) Log in fully (complete any 2FA / captcha).")
        print("  2) Open a normal chat so the session is fully established.")
        print("  3) Return here and press Enter to save & close.")
        print("=" * 64)
        try:
            input("Press Enter when logged in... ")
        except (EOFError, KeyboardInterrupt):
            print("\naborted (no Enter received) — closing without confirmation")

        ctx.close()

    print(f"\ndone. Login state persisted under: {profile}")
    print("Next: archive and upload this directory to the Railway Volume at the")
    print("same path (see docs/session_seeding.md), then run the E2E smoke.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Seed a web Provider login profile.")
    parser.add_argument("--site", required=True, choices=WEB_SITES)
    parser.add_argument(
        "--profile",
        required=True,
        help="user_data_dir to persist login into (match providers.yaml)",
    )
    args = parser.parse_args(argv)
    return seed(args.site, args.profile)


if __name__ == "__main__":
    sys.exit(main())
