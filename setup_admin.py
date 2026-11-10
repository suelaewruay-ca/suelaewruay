"""Local-only initial setup/recovery; no passwords in command-line arguments or logs."""

import argparse
from getpass import getpass
import os
from pathlib import Path
import sys
import tomllib

from admin_store import SettingsStore
from branding import APP_NAME

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=f"ตั้งค่าผู้ดูแลระบบ {APP_NAME}")
    parser.add_argument("--reset", action="store_true", help="Reset the administrator password using server access")
    args = parser.parse_args()
    values = {}
    for path in (Path.home() / ".streamlit/secrets.toml", ROOT / ".streamlit/secrets.toml"):
        if path.exists():
            values.update(tomllib.loads(path.read_text(encoding="utf-8")))
    def setting(name, default=""):
        return str(values.get(name, os.getenv(name, default)))
    store = SettingsStore(setting("ZANI_SETTINGS_DB", str(ROOT / ".private/settings.db")))
    if store.configured() and not args.reset:
        print("Administrator already configured. Use the admin page, or --reset for local recovery.")
        return
    password = getpass("New administrator password (8-256 characters): ")
    if password != getpass("Confirm administrator password: "):
        raise ValueError("Passwords do not match.")
    if args.reset:
        store.reset_admin_locally(password)
    else:
        store.bootstrap(password, setting("APP_PASSWORD"), setting("GOOGLE_API_KEY"), setting("GEMINI_MODEL"))
    print("Saved. Open start.cmd, sign in with this password, then configure member access and AI.")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    try:
        main()
    except (ValueError, OSError, KeyboardInterrupt) as error:
        print(f"Setup not completed: {error}")
        raise SystemExit(1)
