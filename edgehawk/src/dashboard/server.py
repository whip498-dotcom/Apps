"""Local Flask app — single-page dashboard mirroring the live tile.

  python -m src.cli dashboard
        starts the server on http://127.0.0.1:DASHBOARD_PORT

  python -m src.cli scan --dashboard
        runs the scanner and the dashboard in the same process

The page polls /api/state every 3 seconds. Layout mirrors the Discord
live tile so you can use them side by side.
"""
from __future__ import annotations

import threading
from pathlib import Path

from flask import Flask, jsonify, render_template

from ..config import CONFIG
from .state import read_state

TEMPLATES_DIR = Path(__file__).parent / "templates"


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(TEMPLATES_DIR),
    )

    @app.route("/")
    def index():
        return render_template("dashboard.html")

    @app.route("/api/state")
    def api_state():
        return jsonify(read_state())

    @app.route("/health")
    def health():
        return jsonify({"ok": True})

    return app


def serve(port: int | None = None, host: str = "127.0.0.1") -> None:
    app = create_app()
    p = port or CONFIG.dashboard_port
    print(f"[dashboard] http://{host}:{p}")
    app.run(host=host, port=p, debug=False, use_reloader=False)


def start_in_thread(port: int | None = None, host: str = "127.0.0.1") -> int:
    """Start the Flask app in a daemon thread. Returns the port."""
    p = port or CONFIG.dashboard_port
    app = create_app()
    t = threading.Thread(
        target=lambda: app.run(host=host, port=p, debug=False, use_reloader=False),
        daemon=True,
        name="dashboard",
    )
    t.start()
    return p
