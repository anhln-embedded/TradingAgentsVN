"""TradingAgentsVN - Web Dashboard Launcher.

Starts the FastAPI server with real-time SSE streaming and opens the web browser.
Completely non-invasive: does not modify any core files in tradingagents/.
"""

import os
import sys
import webbrowser
from threading import Timer

if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

def open_browser():
    webbrowser.open("http://127.0.0.1:8000")

def main():
    try:
        import uvicorn
        from web.api.server import app
    except ImportError as e:
        print(f"[LỖI] Thiếu thư viện phụ trợ: {e}")
        print("Vui lòng chạy lệnh sau để cài đặt:")
        print("  pip install -r web/requirements.txt")
        sys.exit(1)

    host = "127.0.0.1"
    port = 8000
    print("=" * 65)
    print("  🚀 TRADINGAGENTS-VN: KHỞI CHẠY GIAO DIỆN WEB DASHBOARD")
    print(f"  🌐 URL: http://{host}:{port}")
    print("  ✨ Kiến trúc: Non-invasive Overlay (Giữ nguyên vẹn Core Upstream)")
    print("=" * 65)

    # Open browser automatically after 1.2 seconds
    Timer(1.2, open_browser).start()

    # Run Uvicorn server
    uvicorn.run("web.api.server:app", host=host, port=port, reload=True, log_level="info")

if __name__ == "__main__":
    main()
