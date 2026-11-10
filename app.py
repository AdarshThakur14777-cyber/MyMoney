"""Run the local app or create its first private account."""
from __future__ import annotations

import argparse
import getpass

from mymoney import create_app

app = create_app()


def init_user() -> None:
    from mymoney.auth import create_user
    from mymoney.db import q

    with app.app_context():
        if q("SELECT id FROM users LIMIT 1", one=True):
            raise SystemExit("An account already exists. Change its details in Settings.")
        username = input("Username: ").strip()
        if not 3 <= len(username) <= 40:
            raise SystemExit("Username must be 3 to 40 characters.")
        password = getpass.getpass("Password (at least 8 characters): ")
        confirm = getpass.getpass("Confirm password: ")
        if len(password) < 8 or password != confirm:
            raise SystemExit("Password is too short or the passwords do not match.")
        create_user(username, password)
        print("Account created. You can now sign in.")


def main() -> None:
    parser = argparse.ArgumentParser(description="My Money web app")
    parser.add_argument("command", nargs="?", choices=["init-user"])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()
    if args.command == "init-user":
        init_user()
    else:
        app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
