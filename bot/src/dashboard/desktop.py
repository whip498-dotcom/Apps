"""Desktop window wrapper around the Flask dashboard.

Uses PyWebView to render the same dashboard HTML in a native window —
not a browser tab. The window:

  - Has its own taskbar entry
  - Resizable, minimizable, with native title bar
  - Optional always-on-top via the menu or --always-on-top flag
  - Cross-platform (Windows uses Edge WebView2, macOS WKWebView,
    Linux WebKitGTK)

Architecture: Flask runs in a daemon thread, PyWebView creates the
window and points it at http://127.0.0.1:DASHBOARD_PORT.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from ..config import CONFIG
from .server import create_app


def _start_flask(port: int) -> None:
    app = create_app()
    # Run silently — quieter logs, no reloader, single thread
    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False, threaded=True)


def launch(
    always_on_top: bool = False,
    port: Optional[int] = None,
    width: int = 1280,
    height: int = 900,
) -> None:
    """Open the dashboard in a native desktop window."""
    import webview  # imported lazily so tests / CI without GUI deps still load module

    p = port or CONFIG.dashboard_port

    flask_thread = threading.Thread(target=_start_flask, args=(p,), daemon=True, name="dashboard-flask")
    flask_thread.start()
    # Wait up to 5s for Flask to be ready
    deadline = time.time() + 5
    import urllib.request
    import urllib.error
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{p}/health", timeout=0.3)
            break
        except (urllib.error.URLError, ConnectionResetError):
            time.sleep(0.15)

    window = webview.create_window(
        "Premarket Live Status",
        f"http://127.0.0.1:{p}/",
        width=width,
        height=height,
        min_size=(900, 650),
        on_top=always_on_top,
        resizable=True,
        background_color="#0e1116",
    )

    def _toggle_on_top():
        window.on_top = not window.on_top

    # Add a small menu to toggle always-on-top at runtime
    try:
        from webview.menu import Menu, MenuAction
        menu_items = [
            Menu("View", [
                MenuAction("Toggle Always On Top", _toggle_on_top),
            ]),
        ]
        webview.start(menu=menu_items)
    except Exception:
        # Menu API is platform-dependent — fall back to a plain window
        webview.start()
