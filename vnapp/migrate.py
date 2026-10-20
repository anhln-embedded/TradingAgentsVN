"""Initialize the first SaaS schema before API/worker services start."""
import os
from vnapp.accounts import AccountStore
from vnapp.database import make_engine
from vnapp.settings import load_runtime_env


def main():
    load_runtime_env()
    engine = make_engine()
    accounts = AccountStore(engine)
    accounts.initialize()
    password = os.environ.get("TA_BOOTSTRAP_ADMIN_PASSWORD")
    if password:
        accounts.bootstrap(os.environ.get("TA_BOOTSTRAP_ADMIN_EMAIL", "superadmin"), password)
    engine.dispose()


if __name__ == "__main__":
    main()
