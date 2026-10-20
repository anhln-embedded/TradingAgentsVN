"""TradingAgentsVN - Web Dashboard Launcher.

Starts the FastAPI server with real-time SSE streaming and opens the web browser.
Completely non-invasive: does not modify any core files in tradingagents/.
"""

import atexit
import os
import subprocess
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
    print("  ✨ Tự động khởi chạy Worker xử lý Multi-Agent AI song song")
    print("=" * 65)

    # Automatically start worker in background for local execution
    worker_proc = None
    if os.getenv("TA_STANDALONE_WORKER", "false").lower() != "true":
        worker_env = os.environ.copy()
        worker_env["PYTHONUNBUFFERED"] = "1"
        worker_proc = subprocess.Popen([sys.executable, "-m", "vnapp.worker"], env=worker_env)

    def cleanup_worker():
        nonlocal worker_proc
        if worker_proc and worker_proc.poll() is None:
            try:
                worker_proc.terminate()
                worker_proc.wait(timeout=3)
            except Exception:
                try:
                    worker_proc.kill()
                except Exception:
                    pass
            worker_proc = None

    atexit.register(cleanup_worker)

    # Open browser automatically after 1.2 seconds
    Timer(1.2, open_browser).start()

    # Run Uvicorn server
    try:
        uvicorn.run("web.api.server:app", host=host, port=port,
                    reload=os.getenv("TA_WEB_RELOAD", "false").lower() == "true",
                    log_level="info")
    finally:
        cleanup_worker()

if __name__ == "__main__":
    main()
