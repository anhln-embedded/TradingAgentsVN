"""Operator-only bootstrap/recovery; passwords are entered without echo."""
import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select, update, delete
from vnapp.accounts import AccountStore, password_hash
from vnapp.database import make_engine, transaction, users, sessions
from vnapp.settings import load_runtime_env


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["bootstrap", "reset-password"])
    parser.add_argument("--email", default="superadmin")
    args = parser.parse_args()
    password = getpass.getpass("Password (4+ characters): ")
    if password != getpass.getpass("Confirm password: "):
        raise SystemExit("Passwords do not match")
    load_runtime_env()
    engine = make_engine()
    store = AccountStore(engine)
    store.initialize()
    if args.action == "bootstrap":
        store.bootstrap(args.email, password)
    else:
        with transaction(engine) as connection:
            row = connection.execute(select(users).where(users.c.email == args.email.lower()).with_for_update()).mappings().first()
            if not row:
                raise SystemExit("Account not found")
            connection.execute(update(users).where(users.c.id == row["id"]).values(password_hash=password_hash(password)))
            connection.execute(delete(sessions).where(sessions.c.user_id == row["id"]))
    engine.dispose()
    print("Account operation completed. Existing admin passwords are never overwritten by bootstrap.")


if __name__ == "__main__":
    main()
