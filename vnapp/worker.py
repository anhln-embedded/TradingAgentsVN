"""Independent durable queue consumer: python -m vnapp.worker.

Each job runs in a fresh spawned process to isolate core globals, log handlers,
report/checkpoint paths and environment. Scale by running more worker processes.
"""
import logging
import multiprocessing
import os
import time

from vnapp.accounts import AccountStore
from vnapp.database import make_engine
from vnapp.jobs import JobStore, run_dir
from vnapp.settings import load_runtime_env, runtime_env_path

logger = logging.getLogger(__name__)


def execute_job(job, database_url, folder, config_file):
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    os.environ["TA_DATA_DIR"] = folder
    os.environ["TA_RUNTIME_ENV_FILE"] = config_file
    load_runtime_env()
    # UI-managed server settings must override the worker's original environment.
    from dotenv import load_dotenv
    load_dotenv(config_file, override=True)
    os.environ["TA_DATA_DIR"] = folder
    engine = make_engine(database_url)
    store = JobStore(engine)

    class Sink:
        terminal = False

        def put(self, item):
            if item is None:
                return
            kind, payload = item["event"], dict(item.get("data", {}))
            payload.pop("report_path", None)
            if kind == "run_error":
                orig_err = payload.get("error") or ""
                payload = {
                    "error": orig_err if orig_err else "Phân tích thất bại. Lượt đã được hoàn lại. Vui lòng thử lại hoặc liên hệ hỗ trợ.",
                    "reference": job["id"],
                    "error_type": payload.get("error_type", "ExecutionError"),
                }
            accepted = store.publish(job["id"], job["worker_token"], kind, payload)
            if not accepted:
                raise RuntimeError("Job lease is no longer active")
            if kind in ("complete", "run_error"):
                self.terminal = True

    sink = Sink()
    try:
        from web.api.stream import run_graph_sync
        run_graph_sync(job["ticker"], job["trade_date"], sink, job["analysts"])
    except BaseException:
        logger.exception("Job failed: %s", job["id"])
    finally:
        if not sink.terminal:
            store.publish(job["id"], job["worker_token"], "run_error",
                          {"error": "Tác vụ bị gián đoạn. Lượt phân tích đã được hoàn lại."})
        engine.dispose()


def work_once(store, max_seconds=3600):
    store.fail_stale()
    job = store.claim()
    if not job:
        return False
    folder = run_dir(job)
    folder.mkdir(parents=True, exist_ok=True)
    # Render URL with password only for IPC to our own child process, never log it.
    database_url = store.engine.url.render_as_string(hide_password=False)
    child = multiprocessing.get_context("spawn").Process(target=execute_job,
        args=(job, database_url, str(folder), str(runtime_env_path())))
    started = time.monotonic()
    try:
        child.start()
        while child.is_alive():
            store.fail_stale()
            if time.monotonic()-started > max_seconds:
                child.terminate()
                break
            if not store.heartbeat(job["id"], job["worker_token"]):
                child.join(2)
                if child.is_alive():
                    child.terminate()
                break
            child.join(5)
    finally:
        if child.pid:
            if child.is_alive():
                child.terminate()
            child.join(10)
            if child.is_alive():
                child.kill()
                child.join(5)
        store.publish(job["id"], job["worker_token"], "run_error",
                      {"error": "Worker dừng hoặc hết thời gian xử lý. Lượt phân tích đã được hoàn lại."})
    return True


def main():
    logging.basicConfig(level=logging.INFO)
    load_runtime_env()
    engine = make_engine()
    store = AccountStore(engine)
    store.initialize()
    queue = JobStore(engine)
    while True:
        try:
            if not work_once(queue, int(os.environ.get("TA_JOB_TIMEOUT", "3600"))):
                time.sleep(1)
        except KeyboardInterrupt:
            break
        except Exception:
            logger.exception("Queue consumer error")
            time.sleep(5)


if __name__ == "__main__":
    main()
