"""SaaS regression tests: no LLM calls, real transactions and HTTP middleware."""
import json
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select, update, insert

from vnapp.accounts import AccountStore, DomainError, password_hash, uid
from vnapp.database import make_engine, transaction, users, jobs, periods, ledger, events
from vnapp.jobs import JobStore, run_dir
from web.api.server import create_app


class SaaSTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hashed = password_hash("test-password-123")

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"TA_ENV": "test", "TA_DATA_DIR": self.folder.name,
            "TA_BOOTSTRAP_ADMIN_PASSWORD": "", "TA_PUBLIC_ORIGIN": "", "TA_ALLOW_REGISTRATION": "true"})
        self.env.start()
        self.engine = make_engine("sqlite:///" + (Path(self.folder.name) / "db.sqlite").as_posix())
        self.accounts = AccountStore(self.engine)
        self.accounts.initialize()
        self.admin, self.alice, self.bob = [self.add_user(name, role) for name, role in
            (("superadmin", "superadmin"), ("alice@example.com", "user"), ("bob@example.com", "user"))]
        self.jobs = JobStore(self.engine)
        for user in (self.alice, self.bob):
            self.accounts.set_subscription(self.admin, user["id"], "Monthly", 2, "test subscription")

    def tearDown(self):
        self.engine.dispose()
        self.env.stop()
        self.folder.cleanup()

    def add_user(self, email, role):
        row = dict(id=uid(), email=email, role=role, active=True, password_hash=self.hashed, created_at=time.time())
        with transaction(self.engine) as connection:
            connection.execute(insert(users).values(**row))
        return {k: v for k, v in row.items() if k != "password_hash"}

    def submit(self, user=None, key=None):
        return self.jobs.submit(user or self.alice, "MBB", "2026-10-15", ["market"], key or uid())

    def test_reserve_success_refund_and_duplicate_events(self):
        job = self.submit()
        self.assertEqual(self.accounts.quota(self.alice["id"])["remaining"], 1)
        claimed = self.jobs.claim()
        self.jobs.publish(job["id"], claimed["worker_token"], "complete", {"signal": "BUY"})
        self.assertFalse(self.jobs.publish(job["id"], claimed["worker_token"], "run_error", {"error": "late"}))
        quota = self.accounts.quota(self.alice["id"])
        self.assertEqual((quota["used"], quota["reserved"], quota["remaining"]), (1, 0, 1))
        failed = self.submit()
        claimed = self.jobs.claim()
        self.jobs.publish(failed["id"], claimed["worker_token"], "run_error", {"error": "provider error"})
        quota = self.accounts.quota(self.alice["id"])
        self.assertEqual((quota["used"], quota["reserved"], quota["remaining"]), (1, 0, 1))

    def test_two_accounts_are_independent_and_idempotent(self):
        key = uid()
        one = self.submit(key=key)
        self.assertEqual(self.submit(key=key)["id"], one["id"])
        two = self.submit(self.bob, key)
        self.assertNotEqual(one["id"], two["id"])
        with self.assertRaises(DomainError) as error:
            self.jobs.get(self.bob, one["id"])
        self.assertEqual(error.exception.status, 404)
        self.assertEqual([job["id"] for job in self.jobs.list(self.bob)], [two["id"]])

    def test_atomic_simultaneous_submissions_do_not_overdraw(self):
        def attempt(_):
            try:
                return self.submit()["id"]
            except DomainError:
                return None
        with ThreadPoolExecutor(max_workers=4) as executor:
            result = list(executor.map(attempt, range(4)))
        self.assertEqual(sum(item is not None for item in result), 1)
        self.assertEqual(self.accounts.quota(self.alice["id"])["reserved"], 1)

    def test_claim_once_and_stale_worker_refunds(self):
        self.submit()
        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(executor.map(lambda _: self.jobs.claim(), range(2)))
        self.assertEqual(sum(item is not None for item in claims), 1)
        job = next(item for item in claims if item)
        with transaction(self.engine) as connection:
            connection.execute(update(jobs).where(jobs.c.id == job["id"]).values(heartbeat_at=0))
        self.jobs.fail_stale()
        self.jobs.fail_stale()
        self.assertEqual(self.jobs.get(self.alice, job["id"])["status"], "failed")
        self.assertEqual(self.accounts.quota(self.alice["id"])["remaining"], 2)
        self.assertFalse(self.jobs.publish(job["id"], job["worker_token"], "complete", {}))

    def test_quota_adjustment_is_admin_only_audited_and_idempotent(self):
        with self.assertRaises(DomainError):
            self.accounts.adjust_quota(self.alice, self.bob["id"], 100, "not allowed", uid())
        key = uid()
        for _ in range(2):
            self.accounts.adjust_quota(self.admin, self.alice["id"], 5, "bonus for customer", key)
        self.assertEqual(self.accounts.quota(self.alice["id"])["remaining"], 7)
        self.assertTrue(any(row["action"] == "quota.adjust" for row in self.accounts.audit_log(self.admin)))
        with self.assertRaises(DomainError):
            self.accounts.adjust_quota(self.admin, self.alice["id"], -10, "too much reduction", uid())

    def test_expired_subscription_denies_new_jobs(self):
        with transaction(self.engine) as connection:
            connection.execute(update(periods).where(periods.c.user_id == self.alice["id"]).values(ends_at=1))
        with self.assertRaises(DomainError) as error:
            self.submit()
        self.assertEqual(error.exception.status, 402)

    def login(self, client, email):
        response = client.post("/api/auth/login", headers={"X-Requested-With": "TradingAgentsVN"},
                               json={"email": email, "password": "test-password-123"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        return {"X-CSRF-Token": response.json()["csrf"]}

    def test_http_permissions_csrf_owner_and_no_legacy_bypass(self):
        with TestClient(create_app(self.engine)) as client:
            self.assertEqual(client.get("/api/config").status_code, 401)
            headers = self.login(client, self.alice["email"])
            for path in ("/api/config", "/api/git/status", "/api/admin/users", "/admin"):
                self.assertEqual(client.get(path).status_code, 403, path)
            self.assertEqual(client.post("/api/admin/users/anything/quota", headers=headers,
                                        json={"delta": 100, "reason": "tamper", "request_key": uid()}).status_code, 403)
            body = {"ticker": "MBB", "trade_date": "2026-10-15", "analysts": ["market"], "request_key": uid()}
            self.assertEqual(client.post("/api/runs", json=body).status_code, 403)
            response = client.post("/api/runs", json=body, headers=headers)
            self.assertEqual(response.status_code, 202, response.text)
            run_id = response.json()["id"]
            self.assertEqual(client.get("/api/analyze/stream?ticker=MBB").status_code, 410)
            self.login(client, self.bob["email"])
            for path in (f"/api/runs/{run_id}", f"/api/runs/{run_id}/events", f"/api/history/{run_id}"):
                self.assertEqual(client.get(path).status_code, 404, path)
            self.assertEqual(client.get("/api/history").json()["reports"], [])

    def test_two_devices_snapshot_replay_and_api_restart(self):
        job = self.submit()
        claimed = self.jobs.claim()
        token = claimed["worker_token"]
        self.jobs.publish(job["id"], token, "report_section", {"section": "market", "content": "analysis"})
        self.jobs.publish(job["id"], token, "complete", {"signal": "BUY"})
        expected = None
        for _ in range(2):
            # Fresh app and login simulate another device and an API restart.
            with TestClient(create_app(self.engine)) as client:
                self.login(client, self.alice["email"])
                current = client.get("/api/workspace").json()["run"]
                self.assertEqual(current["snapshot"]["reports"]["market"], "analysis")
                if expected:
                    self.assertEqual(current, expected)
                expected = current
                stream = client.get(f"/api/runs/{job['id']}/events", headers={"Last-Event-ID": "1"})
                self.assertIn("event: complete", stream.text)
                self.assertNotIn("event: report_section", stream.text)
        self.assertEqual(self.accounts.quota(self.alice["id"])["used"], 1)

    def test_disabled_user_loses_sessions_and_cannot_promote_self(self):
        token, _, _ = self.accounts.login(self.alice["email"], "test-password-123")
        self.accounts.set_active(self.admin, self.alice["id"], False, "disabled by admin")
        self.assertIsNone(self.accounts.authenticate(token))
        with TestClient(create_app(self.engine)) as client:
            response = client.post("/api/auth/register", headers={"X-Requested-With": "TradingAgentsVN"},
                json={"email": "attacker@example.com", "password": "test-password-123", "role": "superadmin"})
            self.assertEqual(response.status_code, 422)

    def test_password_change_revokes_every_device(self):
        one, _, _ = self.accounts.login(self.alice["email"], "test-password-123")
        two, _, _ = self.accounts.login(self.alice["email"], "test-password-123")
        self.accounts.change_password(self.alice, "test-password-123", "changed-password-123")
        self.assertIsNone(self.accounts.authenticate(one))
        self.assertIsNone(self.accounts.authenticate(two))
        with self.assertRaises(DomainError):
            self.accounts.login(self.alice["email"], "test-password-123")
        self.assertEqual(self.accounts.login(self.alice["email"], "changed-password-123")[2]["id"], self.alice["id"])

    def test_superadmin_config_is_validated_serialized_and_audited(self):
        with TestClient(create_app(self.engine)) as client, patch("web.api.routes.update_ai_config", return_value={"provider": "openai"}) as write:
            headers = self.login(client, self.admin["email"])
            payload = {"provider": "openai", "api_key": "test-secret-not-in-audit", "llm_timeout": 90, "llm_max_retries": 2}
            self.assertEqual(client.post("/api/config", json={**payload, "llm_timeout": -1}, headers=headers).status_code, 422)
            response = client.post("/api/config", json=payload, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            write.assert_called_once()
            audit = client.get("/api/admin/audit").json()
            self.assertIn("server.config.update", json.dumps(audit))
            self.assertNotIn(payload["api_key"], json.dumps(audit))
            self.submit()
            self.assertEqual(client.post("/api/config", json=payload, headers=headers).status_code, 409)
            write.assert_called_once()

    def test_stale_sweep_cannot_fail_a_renewed_lease(self):
        self.submit()
        job = self.jobs.claim()
        self.assertFalse(self.jobs.publish(job["id"], job["worker_token"], "run_error", {"error": "stale"}, stale_before=time.time()-90))
        self.assertEqual(self.jobs.get(self.alice, job["id"])["status"], "running")
        self.assertEqual(self.accounts.quota(self.alice["id"])["reserved"], 1)

    def test_worker_execution_uses_private_paths_and_settles_once(self):
        from vnapp.worker import execute_job
        job = self.submit()
        claimed = self.jobs.claim()
        folder = run_dir(job)
        def fake_graph(ticker, trade_date, sink, analysts):
            from vnapp.storage import save_snapshot
            self.assertEqual(Path(os.environ["TA_DATA_DIR"]), folder)
            save_snapshot(ticker, {"company_of_interest": ticker, "trade_date": trade_date})
            sink.put({"event": "complete", "data": {"signal": "BUY", "report_path": "secret/server/path"}})
            sink.put(None)
        with patch.dict(os.environ), patch("web.api.stream.run_graph_sync", side_effect=fake_graph):
            execute_job(claimed, self.engine.url.render_as_string(hide_password=False), str(folder), str(Path(self.folder.name)/"missing.env"))
        report = self.jobs.report(self.alice, job["id"])
        self.assertEqual(report["company_of_interest"], "MBB")
        self.assertNotIn("report_path", self.jobs.get(self.alice, job["id"])["snapshot"]["complete"])
        self.assertEqual(self.accounts.quota(self.alice["id"])["used"], 1)


    def test_cancel_queued_and_running_job(self):
        job = self.submit()
        self.assertEqual(self.accounts.quota(self.alice["id"])["reserved"], 1)
        cancelled = self.jobs.cancel(self.alice, job["id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(self.accounts.quota(self.alice["id"])["reserved"], 0)
        self.assertEqual(self.accounts.quota(self.alice["id"])["remaining"], 2)


if __name__ == "__main__":
    unittest.main()
