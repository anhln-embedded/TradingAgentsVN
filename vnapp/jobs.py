"""Database-backed queue, ownership checks and transactional quota settlement."""
import json
import time
from pathlib import Path

from sqlalchemy import select, update, insert

from vnapp.accounts import ACTIVE, TERMINAL, DomainError, uid
from vnapp.database import transaction, users, periods, jobs, events, ledger, system_lock
from vnapp.settings import data_dir


class JobStore:
    def __init__(self, engine):
        self.engine = engine

    @staticmethod
    def serialize(row):
        result = dict(row)
        for key in ("analysts", "snapshot"):
            result[key] = json.loads(result[key])
        result.pop("worker_token", None)
        result.pop("request_key", None)
        return result

    def get(self, actor, job_id):
        with self.engine.connect() as connection:
            row = connection.execute(select(jobs).where(jobs.c.id == job_id)).mappings().first()
        if not row or (row["user_id"] != actor["id"] and actor["role"] != "superadmin"):
            raise DomainError(404, "Không tìm thấy phiên phân tích.")
        return self.serialize(row)

    def list(self, actor, active_only=False, limit=100, status=None):
        query = select(jobs).where(jobs.c.user_id == actor["id"])
        if active_only:
            query = query.where(jobs.c.status.in_(ACTIVE))
        if status:
            query = query.where(jobs.c.status == status)
        with self.engine.connect() as connection:
            rows = connection.execute(query.order_by(jobs.c.created_at.desc()).limit(limit)).mappings().all()
        return [self.serialize(row) for row in rows]

    def submit(self, actor, ticker, trade_date, analysts, request_key):
        with transaction(self.engine) as connection:
            # Serialize job admission with server configuration changes.
            connection.execute(select(system_lock).where(system_lock.c.id == "server").with_for_update()).first()
            owner = connection.execute(select(users).where(users.c.id == actor["id"]).with_for_update()).mappings().one()
            if not owner["active"]:
                raise DomainError(403, "Tài khoản đã bị khóa.")
            previous = connection.execute(select(jobs).where(jobs.c.user_id == actor["id"], jobs.c.request_key == request_key)).mappings().first()
            if previous:
                if (previous["ticker"], previous["trade_date"], json.loads(previous["analysts"])) != (ticker, trade_date, analysts):
                    raise DomainError(409, "Mã yêu cầu đã được dùng cho phiên khác.")
                return self.serialize(previous)
            current = connection.execute(select(jobs.c.id).where(jobs.c.user_id == actor["id"], jobs.c.status.in_(ACTIVE))).first()
            if current:
                raise DomainError(409, "Bạn đang có một phiên chờ/chạy. Hãy theo dõi phiên hiện tại.")
            period = connection.execute(select(periods).where(periods.c.user_id == actor["id"],
                periods.c.starts_at <= time.time(), periods.c.ends_at > time.time()).with_for_update()).mappings().first()
            if owner["role"] == "superadmin":
                now = time.time()
                if not period:
                    existing = connection.execute(select(periods).where(periods.c.user_id == actor["id"]).order_by(periods.c.ends_at.desc()).with_for_update()).mappings().first()
                    if existing:
                        connection.execute(update(periods).where(periods.c.id == existing["id"]).values(
                            ends_at=now + 100 * 365 * 86400,
                            allowance=max(existing["allowance"], existing["used"] + existing["reserved"] + 1_000_000_000)
                        ))
                        period = connection.execute(select(periods).where(periods.c.id == existing["id"]).with_for_update()).mappings().one()
                    else:
                        period_id = uid()
                        connection.execute(insert(periods).values(
                            id=period_id, user_id=actor["id"], plan="Superadmin (Không giới hạn)",
                            starts_at=now, ends_at=now + 100 * 365 * 86400,
                            allowance=1_000_000_000, used=0, reserved=0
                        ))
                        period = connection.execute(select(periods).where(periods.c.id == period_id).with_for_update()).mappings().one()
                elif period["allowance"] <= period["used"] + period["reserved"]:
                    connection.execute(update(periods).where(periods.c.id == period["id"]).values(
                        allowance=period["used"] + period["reserved"] + 1_000_000_000
                    ))
                    period = connection.execute(select(periods).where(periods.c.id == period["id"]).with_for_update()).mappings().one()
            elif not period or period["allowance"] <= period["used"] + period["reserved"]:
                raise DomainError(402, "Gói đã hết hạn hoặc hết lượt phân tích. Vui lòng liên hệ quản trị viên.")
            job_id = uid()
            row = dict(id=job_id, user_id=actor["id"], period_id=period["id"], request_key=request_key,
                ticker=ticker, trade_date=trade_date, analysts=json.dumps(analysts), status="queued",
                created_at=time.time(), event_seq=0, snapshot="{}")
            connection.execute(insert(jobs).values(**row))
            connection.execute(update(periods).where(periods.c.id == period["id"]).values(reserved=period["reserved"]+1))
            connection.execute(insert(ledger).values(id=uid(), period_id=period["id"], job_id=job_id,
                kind="reserve", amount=1, request_key="reserve:"+job_id, created_at=time.time()))
        # Query once to return all nullable columns too.
        return self.get(actor, job_id)

    def claim(self):
        with transaction(self.engine) as connection:
            row = connection.execute(select(jobs).where(jobs.c.status == "queued")
                .order_by(jobs.c.created_at).limit(1).with_for_update(skip_locked=True)).mappings().first()
            if not row:
                return None
            token = uid()
            connection.execute(update(jobs).where(jobs.c.id == row["id"]).values(status="running",
                started_at=time.time(), heartbeat_at=time.time(), worker_token=token))
            return {**dict(row), "worker_token": token, "analysts": json.loads(row["analysts"])}

    def heartbeat(self, job_id, token):
        with transaction(self.engine) as connection:
            return connection.execute(update(jobs).where(jobs.c.id == job_id,
                jobs.c.worker_token == token, jobs.c.status == "running").values(heartbeat_at=time.time())).rowcount == 1

    def publish(self, job_id, token, kind, data, stale_before=None):
        with transaction(self.engine) as connection:
            row = connection.execute(select(jobs).where(jobs.c.id == job_id).with_for_update()).mappings().one()
            if row["status"] in TERMINAL or row["worker_token"] != token:
                return False
            if stale_before is not None and row["heartbeat_at"] >= stale_before:
                return False  # Worker renewed its lease after the sweep query.
            seq = row["event_seq"]+1
            snapshot = json.loads(row["snapshot"])
            if kind == "agent_status":
                snapshot.setdefault("agents", {})[data.get("code", data.get("agent", ""))] = data
            elif kind == "report_section":
                snapshot.setdefault("reports", {})[data.get("section", "")] = data.get("content", "")
            elif kind == "debate_update":
                snapshot.setdefault("debates", {})[data.get("team", "")] = data
            elif kind in ("complete", "run_error", "phase_start", "init"):
                snapshot[kind] = data
            values = dict(event_seq=seq, snapshot=json.dumps(snapshot, ensure_ascii=False))
            if kind in ("complete", "run_error"):
                values.update(status="completed" if kind == "complete" else "failed", finished_at=time.time())
                period = connection.execute(select(periods).where(periods.c.id == row["period_id"]).with_for_update()).mappings().one()
                connection.execute(update(periods).where(periods.c.id == period["id"]).values(
                    reserved=period["reserved"]-1, used=period["used"] + (kind == "complete")))
                connection.execute(insert(ledger).values(id=uid(), period_id=period["id"], job_id=job_id,
                    kind="consume" if kind == "complete" else "refund", amount=1,
                    request_key="settle:"+job_id, created_at=time.time()))
            connection.execute(insert(events).values(job_id=job_id, seq=seq, kind=kind,
                data=json.dumps(data, ensure_ascii=False, default=str), created_at=time.time()))
            connection.execute(update(jobs).where(jobs.c.id == job_id).values(**values))
            return True

    def read_events(self, actor, job_id, cursor, limit=100):
        self.get(actor, job_id)
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(select(events).where(events.c.job_id == job_id,
                events.c.seq > cursor).order_by(events.c.seq).limit(limit)).mappings()]

    def fail_stale(self, timeout=180):
        cutoff = time.time()-timeout
        with self.engine.connect() as connection:
            rows = connection.execute(select(jobs.c.id, jobs.c.worker_token).where(jobs.c.status == "running",
                jobs.c.heartbeat_at < cutoff)).mappings().all()
        for row in rows:
            self.publish(row["id"], row["worker_token"], "run_error",
                         {"error": "Worker bị gián đoạn. Lượt phân tích đã được hoàn lại; bạn có thể chạy lại."}, stale_before=cutoff)

    def cancel(self, actor, job_id):
        with transaction(self.engine) as connection:
            row = connection.execute(select(jobs).where(jobs.c.id == job_id).with_for_update()).mappings().first()
            if not row or (row["user_id"] != actor["id"] and actor["role"] != "superadmin"):
                raise DomainError(404, "Không tìm thấy phiên phân tích.")
            if row["status"] in TERMINAL:
                return self.serialize(row)
            seq = row["event_seq"] + 1
            snapshot = json.loads(row["snapshot"])
            cancel_data = {"error": "Phiên phân tích đã được hủy.", "cancelled": True}
            snapshot["run_error"] = cancel_data
            values = dict(
                status="cancelled",
                event_seq=seq,
                finished_at=time.time(),
                snapshot=json.dumps(snapshot, ensure_ascii=False)
            )
            period = connection.execute(select(periods).where(periods.c.id == row["period_id"]).with_for_update()).mappings().one()
            connection.execute(update(periods).where(periods.c.id == period["id"]).values(reserved=max(0, period["reserved"] - 1)))
            connection.execute(insert(ledger).values(id=uid(), period_id=period["id"], job_id=job_id,
                kind="refund", amount=1, request_key="cancel:" + job_id, created_at=time.time()))
            connection.execute(insert(events).values(job_id=job_id, seq=seq, kind="run_error",
                data=json.dumps(cancel_data, ensure_ascii=False), created_at=time.time()))
            connection.execute(update(jobs).where(jobs.c.id == job_id).values(**values))
            return self.serialize({**row, **values})

    def report(self, actor, job_id):
        job = self.get(actor, job_id)
        path = run_dir(job) / "results" / f"{job['ticker'].lower()}_analysis" / "final_state.json"
        if job["status"] != "completed" or not path.exists():
            raise DomainError(404, "Báo cáo chưa sẵn sàng.")
        return json.loads(path.read_text(encoding="utf-8"))


def run_dir(job):
    # IDs originate exclusively on the server, never from paths supplied by clients.
    return data_dir() / "tenants" / job["user_id"] / "runs" / job["id"]
