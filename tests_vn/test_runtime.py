import asyncio
import base64
import json
import os
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from vnapp.core_adapter import DashboardGraph, build_config, decision_signal, normalize_decision, stream_states
from vnapp.runtime import EventBuffer, RUN_LOCK
from vnapp.storage import report_folder, save_snapshot
from web.api.server import create_app
from web.api.stream import run_graph_sync, stream_analysis_events
from web.api import config_helper, git_helper


class CoreContractTests(unittest.TestCase):
    def test_real_core_exposes_required_adapter_contract(self):
        for method in ("_get_provider_kwargs", "create_run_state", "begin_checkpoint", "checkpoint_input", "end_checkpoint", "record_decision", "clear_checkpoint_on_success", "save_reports"):
            self.assertTrue(callable(getattr(DashboardGraph, method, None)), method)

    def test_timeout_is_application_owned(self):
        graph = object.__new__(DashboardGraph)
        graph.config = {"llm_provider": "openai_compatible", "llm_timeout": 45}
        self.assertEqual(graph._get_provider_kwargs()["timeout"], 45)
        for invalid in (True, 0, -1, "nan", "inf"):
            graph.config["llm_timeout"] = invalid
            with self.assertRaises(ValueError):
                graph._get_provider_kwargs()

    def test_legacy_and_new_stream_preserve_final_state(self):
        legacy = Mock(spec=["graph"])
        legacy.graph.stream.return_value = iter([{"market_report": "market"}, {"final_trade_decision": "**Action**: Buy"}])
        modern = Mock(spec=["stream_run"])
        modern.stream_run.return_value = iter([([], {"market_report": "market"}), ([], {"final_trade_decision": "**Action**: Buy"})])
        for graph in (legacy, modern):
            states = list(stream_states(graph, {}, {}))
            self.assertEqual(states[-1]["market_report"], "market")
            self.assertEqual(decision_signal(states[-1]), "BUY")

    def test_new_core_decisions_reach_legacy_ui(self):
        state = normalize_decision({"risk_debate_state": {}, "investment_debate_state": {}, "final_trade_decision": "Buy", "investment_plan": "plan"})
        self.assertEqual(state["risk_debate_state"]["judge_decision"], "Buy")
        self.assertEqual(state["investment_debate_state"]["judge_decision"], "plan")


class StorageTests(unittest.TestCase):
    def test_ui_config_persists_outside_source_tree_and_round_trips_quotes(self):
        from dotenv import dotenv_values
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}), patch.object(config_helper, "DEFAULT_CONFIG", {}):
            target = Path(directory) / "config" / "runtime.env"
            with patch.object(config_helper, "ENV_PATH", target):
                config_helper.update_ai_config("openai_compatible", api_key="test'key#with spaces", deep_think_llm="test-model")
            self.assertEqual(dotenv_values(target)["OPENAI_COMPATIBLE_API_KEY"], "test'key#with spaces")
            self.assertEqual(os.environ["TRADINGAGENTS_DEEP_THINK_LLM"], "test-model")

    def test_snapshot_native_json_atomic_replace_and_persistent_paths(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"TA_DATA_DIR": directory}):
            state = {"company_of_interest": "MBB", "risk_debate_state": {"judge_decision": "buy"}}
            path = save_snapshot("MBB", state)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), state)
            save_snapshot("MBB", {"final_trade_decision": "new"})
            self.assertEqual(json.loads(path.read_text())["final_trade_decision"], "new")
            self.assertEqual(list(path.parent.glob("*.tmp")), [])
            self.assertEqual(Path(build_config()["results_dir"]), Path(directory).resolve() / "results")
            self.assertEqual(report_folder("mbb_analysis"), path.parent.resolve())
            for value in ("../.env", "../../outside", str(Path(directory).parent)):
                with self.assertRaises(ValueError):
                    report_folder(value)


class RuntimeTests(unittest.TestCase):
    def test_buffer_is_bounded_and_disconnect_does_not_block_producer(self):
        buffer = EventBuffer()
        for item in range(1000):
            buffer.put(item)
        self.assertEqual(buffer.qsize(), 256)
        buffer.detached.set()
        buffer.put("ignored")
        self.assertEqual(buffer.qsize(), 256)

    def test_saving_failure_emits_error_not_complete(self):
        graph = Mock()
        graph.propagator.get_graph_args.return_value = {}
        graph.begin_checkpoint.return_value = None
        graph.graph.stream.return_value = iter([])
        with patch("web.api.stream.DashboardGraph", return_value=graph), patch("web.api.stream.stream_states", return_value=iter([{"final_trade_decision": "**Action**: Buy"}])), patch("web.api.stream.save_snapshot", side_effect=OSError("disk full")):
            events = queue.Queue()
            run_graph_sync("MBB", "2026-10-14", events)
        items = list(events.queue)
        self.assertTrue(any(item and item["event"] == "run_error" for item in items))
        self.assertFalse(any(item and item["event"] == "complete" for item in items))
        graph.end_checkpoint.assert_called_once()

    def test_disconnect_keeps_worker_running_and_blocks_second_run(self):
        started, finish = threading.Event(), threading.Event()

        def worker(ticker, date, events, analysts):
            events.put({"event": "init"})
            started.set()
            finish.wait(5)
            events.put(None)

        async def exercise():
            with patch("web.api.stream.run_graph_sync", side_effect=worker):
                events = stream_analysis_events("MBB", "2026-10-14")
                self.assertTrue(started.wait(2))
                try:
                    await anext(events)
                    await events.aclose()
                    self.assertTrue(RUN_LOCK.locked())
                    with self.assertRaises(RuntimeError):
                        stream_analysis_events("BSR", "2026-10-14")
                finally:
                    finish.set()
                    acquired = await asyncio.to_thread(RUN_LOCK.acquire, True, 3)
                    self.assertTrue(acquired)
                    if acquired:
                        RUN_LOCK.release()
        asyncio.run(exercise())


class ServerTests(unittest.TestCase):
    def test_production_requires_password(self):
        with patch.dict(os.environ, {"TA_ENV": "production", "TA_WEB_PASSWORD": ""}):
            with self.assertRaises(RuntimeError):
                create_app()

    def test_auth_health_and_disabled_git(self):
        from fastapi.testclient import TestClient
        with patch.dict(os.environ, {"TA_ENV": "production", "TA_WEB_PASSWORD": "test-only", "TA_WEB_USERNAME": "admin", "TA_ALLOW_GIT_UPDATES": "false"}):
            with TestClient(create_app()) as client:
                self.assertEqual(client.get("/api/health").status_code, 200)
                self.assertEqual(client.get("/").status_code, 401)
                headers = {"Authorization": "Basic " + base64.b64encode(b"admin:test-only").decode()}
                self.assertEqual(client.get("/", headers=headers).status_code, 200)
                self.assertTrue(client.get("/api/git/status", headers=headers).json()["disabled"])
                self.assertEqual(client.post("/api/git/pull", headers=headers).status_code, 403)
                self.assertEqual(client.get("/api/config", headers={**headers, "Origin": "https://other.example"}).status_code, 403)


class UpdateTests(unittest.TestCase):
    def test_fetch_failure_cannot_be_reported_up_to_date(self):
        with patch.object(git_helper, "run_git_cmd", return_value=(1, "", "fetch failed")):
            status = git_helper.check_upstream_status()
        self.assertFalse(status["is_up_to_date"])
        self.assertEqual(status["error"], "fetch failed")

    def test_untracked_app_blocks_upgrade_until_saved(self):
        with patch.object(git_helper, "check_upstream_status", return_value={"has_uncommitted_tracked_changes": False, "untracked_files": ["?? web/"], "modified_files": []}), patch.object(git_helper.subprocess, "run") as run:
            self.assertFalse(git_helper.pull_upstream_code()["success"])
            run.assert_not_called()

    def test_web_upgrade_invokes_candidate_tool_not_live_merge(self):
        with patch.object(git_helper, "check_upstream_status", return_value={"has_uncommitted_tracked_changes": False, "untracked_files": [], "modified_files": [], "behind_count": 135}), patch.object(git_helper.subprocess, "run", return_value=Mock(returncode=0, stdout="prepared", stderr="")) as run:
            result = git_helper.pull_upstream_code()
            self.assertTrue(result["success"])
            self.assertIn("prepare_upstream.py", run.call_args.args[0][1])
            self.assertNotIn("merge", run.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
