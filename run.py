import os

from app import create_app
from app.services.scheduler import init_scheduler

app = create_app()

if __name__ == "__main__":
    # Only start the background scheduler when actually serving the app —
    # not when this module is imported by `flask db migrate/upgrade` via
    # FLASK_APP=run.py, which would otherwise spin up a redundant scheduler
    # thread for a short-lived CLI process.
    init_scheduler(app)
    port = int(os.environ.get("PORT", 5000))
    # 0.0.0.0, not 127.0.0.1: the loopback address only ever accepts
    # connections from the same machine, so anyone reaching this app at its
    # real network address (e.g. http://172.17.51.13:5007/) would get
    # "connection refused" no matter what GOOGLE_OAUTH_REDIRECT_URI or
    # anything else in .env says — this is what actually makes the app
    # reachable from other machines at all.
    app.run(host="0.0.0.0", port=port, debug=app.config["DEBUG"])
