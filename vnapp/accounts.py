"""Account and billing rules. No HTTP or browser state is trusted here."""
import calendar
import hashlib
import hmac
import json
import re
import secrets
import time
import threading
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update, insert, delete
from sqlalchemy.exc import IntegrityError

from vnapp.database import (metadata, transaction, users, sessions, periods, jobs,
                            ledger, audit, throttles, system_lock)

ACTIVE = ("queued", "running")
TERMINAL = ("completed", "failed", "cancelled")
PASSWORD_SLOTS = threading.BoundedSemaphore(2)


class DomainError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message
        super().__init__(message)


def uid():
    return str(uuid.uuid4())


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password, salt=None):
    if not 4 <= len(password) <= 256:
        raise DomainError(422, "Mật khẩu phải từ 4 đến 256 ký tự.")
    salt = salt or secrets.token_hex(16)
    with PASSWORD_SLOTS:
        key = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**17,
                             r=8, p=1, maxmem=256*1024*1024)
    return f"scrypt${salt}${key.hex()}"


def password_matches(password, stored):
    try:
        _, salt, _ = stored.split("$")
        return hmac.compare_digest(password_hash(password, salt), stored)
    except (ValueError, DomainError):
        return False


def public_user(row):
    return {key: row[key] for key in ("id", "email", "role", "active", "created_at")}


def require_admin(actor):
    if not actor or not actor["active"] or actor["role"] != "superadmin":
        raise DomainError(403, "Chỉ superadmin được thực hiện thao tác này.")


class AccountStore:
    def __init__(self, engine):
        self.engine = engine

    def initialize(self):
        metadata.create_all(self.engine)
        try:
            with transaction(self.engine) as connection:
                if not connection.execute(select(system_lock)).first():
                    connection.execute(insert(system_lock).values(id="server"))
        except IntegrityError:
            pass  # Another API/worker initialized the singleton concurrently.

    def _audit(self, connection, actor, action, target, details):
        connection.execute(insert(audit).values(id=uid(), actor_id=actor["id"],
            action=action, target_id=target, details=json.dumps(details, ensure_ascii=False), created_at=time.time()))

    def _ensure_unlimited_period(self, connection, user_id):
        now = time.time()
        active = connection.execute(select(periods).where(periods.c.user_id == user_id,
            periods.c.starts_at <= now, periods.c.ends_at > now)).mappings().first()
        if not active:
            existing = connection.execute(select(periods).where(periods.c.user_id == user_id)
                .order_by(periods.c.ends_at.desc())).mappings().first()
            if existing:
                connection.execute(update(periods).where(periods.c.id == existing["id"]).values(
                    ends_at=now + 100 * 365 * 86400,
                    allowance=max(existing["allowance"], existing["used"] + existing["reserved"] + 1_000_000_000)
                ))
                return connection.execute(select(periods).where(periods.c.id == existing["id"])).mappings().one()
            period_id = uid()
            connection.execute(insert(periods).values(
                id=period_id, user_id=user_id, plan="Superadmin (Không giới hạn)",
                starts_at=now, ends_at=now + 100 * 365 * 86400,
                allowance=1_000_000_000, used=0, reserved=0
            ))
            return connection.execute(select(periods).where(periods.c.id == period_id)).mappings().one()
        return active

    def bootstrap(self, email, password):
        """Operator-only setup, never exposed as a public first-user endpoint."""
        with transaction(self.engine) as connection:
            connection.execute(select(system_lock).where(system_lock.c.id == "server").with_for_update()).first()
            admin_row = connection.execute(select(users.c.id).where(users.c.role == "superadmin")).first()
            if admin_row:
                self._ensure_unlimited_period(connection, admin_row[0])
                return
            created = self._create_user(connection, email, password, "superadmin")
            self._ensure_unlimited_period(connection, created["id"])
            self._audit(connection, created, "superadmin.bootstrap", created["id"], {})

    def _create_user(self, connection, email, password, role="user"):
        email = email.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9.@_+\-]{2,159}", email):
            raise DomainError(422, "Email/tên đăng nhập không hợp lệ.")
        row = dict(id=uid(), email=email, password_hash=password_hash(password),
                   role=role, active=True, created_at=time.time())
        connection.execute(insert(users).values(**row))
        return public_user(row)

    def register(self, email, password):
        try:
            with transaction(self.engine) as connection:
                return self._create_user(connection, email, password)
        except IntegrityError:
            raise DomainError(409, "Email/tên đăng nhập đã được sử dụng.") from None

    def throttle(self, key, limit=10, seconds=900):
        original_key = key
        key, now = digest(key), time.time()
        # Rate-limit state is shared by API workers and survives restarts.
        try:
            with transaction(self.engine) as connection:
                row = connection.execute(select(throttles).where(throttles.c.key == key).with_for_update()).mappings().first()
                if not row:
                    connection.execute(insert(throttles).values(key=key, window_start=now, count=1))
                elif now - row["window_start"] >= seconds:
                    connection.execute(update(throttles).where(throttles.c.key == key).values(window_start=now, count=1))
                elif row["count"] >= limit:
                    raise DomainError(429, "Quá nhiều yêu cầu. Vui lòng thử lại sau.")
                else:
                    connection.execute(update(throttles).where(throttles.c.key == key).values(count=row["count"] + 1))
        except IntegrityError:
            return self.throttle(original_key, limit, seconds)

    def login(self, email, password):
        with self.engine.connect() as connection:
            row = connection.execute(select(users).where(users.c.email == email.strip().lower())).mappings().first()
        # Perform the same expensive hash even when the account does not exist.
        stored = row["password_hash"] if row else "scrypt$" + "00"*16 + "$" + "00"*64
        valid = password_matches(password, stored)
        if not row or not valid or not row["active"]:
            raise DomainError(401, "Thông tin đăng nhập không đúng hoặc tài khoản bị khóa.")
        token, csrf = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
        with transaction(self.engine) as connection:
            connection.execute(delete(sessions).where(sessions.c.expires_at < time.time()))
            connection.execute(insert(sessions).values(token_hash=digest(token), user_id=row["id"],
                csrf=csrf, expires_at=time.time() + 12*3600))
            if row["role"] == "superadmin":
                self._ensure_unlimited_period(connection, row["id"])
        return token, csrf, public_user(row)

    def authenticate(self, token):
        if not token:
            return None
        with self.engine.connect() as connection:
            row = connection.execute(select(users, sessions.c.csrf).join(sessions, users.c.id == sessions.c.user_id)
                .where(sessions.c.token_hash == digest(token), sessions.c.expires_at > time.time(), users.c.active.is_(True))).mappings().first()
        return {**public_user(row), "csrf": row["csrf"]} if row else None

    def logout(self, token):
        with transaction(self.engine) as connection:
            connection.execute(delete(sessions).where(sessions.c.token_hash == digest(token)))

    def change_password(self, actor, current_password, new_password):
        with transaction(self.engine) as connection:
            row = connection.execute(select(users).where(users.c.id == actor["id"]).with_for_update()).mappings().one()
            if not password_matches(current_password, row["password_hash"]):
                raise DomainError(403, "Mật khẩu hiện tại không đúng.")
            connection.execute(update(users).where(users.c.id == actor["id"]).values(password_hash=password_hash(new_password)))
            connection.execute(delete(sessions).where(sessions.c.user_id == actor["id"]))
            self._audit(connection, actor, "password.change", actor["id"], {})

    def quota(self, user_id):
        with self.engine.connect() as connection:
            user = connection.execute(select(users.c.role).where(users.c.id == user_id)).mappings().first()
            row = connection.execute(select(periods).where(periods.c.user_id == user_id,
                periods.c.starts_at <= time.time(), periods.c.ends_at > time.time())
                .order_by(periods.c.starts_at.desc()).limit(1)).mappings().first()
        if user and user["role"] == "superadmin":
            if not row:
                return {"plan": "Superadmin (Không giới hạn)", "remaining": 999999, "allowance": 1_000_000_000, "used": 0, "reserved": 0, "ends_at": None, "is_unlimited": True}
            result = dict(row)
            result["remaining"] = max(0, row["allowance"] - row["used"] - row["reserved"])
            result["is_unlimited"] = True
            return result
        return self._quota(row)

    @staticmethod
    def _quota(row):
        if not row:
            return {"plan": None, "remaining": 0, "allowance": 0, "used": 0, "reserved": 0}
        result = dict(row)
        result["remaining"] = max(0, row["allowance"] - row["used"] - row["reserved"])
        return result

    def list_users(self, actor, search="", offset=0, limit=100):
        require_admin(actor)
        query = select(users)
        if search:
            query = query.where(users.c.email.contains(search.lower(), autoescape=True))
        with self.engine.connect() as connection:
            rows = connection.execute(query.order_by(users.c.created_at.desc(), users.c.id).offset(offset).limit(limit)).mappings().all()
        return [{**public_user(row), "quota": self.quota(row["id"])} for row in rows]

    def set_subscription(self, actor, user_id, plan, allowance, reason):
        require_admin(actor)
        now = datetime.now(timezone.utc)
        month = now.month % 12 + 1
        year = now.year + (now.month == 12)
        end = now.replace(year=year, month=month, day=min(now.day, calendar.monthrange(year, month)[1]))
        with transaction(self.engine) as connection:
            owner = connection.execute(select(users).where(users.c.id == user_id).with_for_update()).mappings().first()
            if not owner:
                raise DomainError(404, "Không tìm thấy người dùng.")
            active = connection.execute(select(jobs.c.id).where(jobs.c.user_id == user_id, jobs.c.status.in_(ACTIVE))).first()
            if active:
                raise DomainError(409, "Chờ tác vụ hiện tại kết thúc trước khi thay gói.")
            connection.execute(update(periods).where(periods.c.user_id == user_id, periods.c.ends_at > now.timestamp()).values(ends_at=now.timestamp()))
            period_id = uid()
            connection.execute(insert(periods).values(id=period_id, user_id=user_id, plan=plan,
                starts_at=now.timestamp(), ends_at=end.timestamp(), allowance=allowance, used=0, reserved=0))
            connection.execute(insert(ledger).values(id=uid(), period_id=period_id, kind="subscription",
                amount=allowance, request_key="subscription:"+period_id, created_at=time.time()))
            self._audit(connection, actor, "subscription.assign", user_id,
                        {"plan": plan, "allowance": allowance, "reason": reason, "period_id": period_id})
        return self.quota(user_id)

    def adjust_quota(self, actor, user_id, delta, reason, request_key):
        require_admin(actor)
        key = f"adjust:{actor['id']}:{request_key}"
        with transaction(self.engine) as connection:
            connection.execute(select(users.c.id).where(users.c.id == user_id).with_for_update()).first()
            previous = connection.execute(select(ledger).where(ledger.c.request_key == key)).mappings().first()
            if previous:
                original = connection.execute(select(periods.c.user_id).where(periods.c.id == previous["period_id"])).scalar_one()
                if original != user_id or previous["amount"] != delta:
                    raise DomainError(409, "Mã yêu cầu đã được dùng cho thao tác khác.")
            else:
                row = connection.execute(select(periods).where(periods.c.user_id == user_id,
                    periods.c.starts_at <= time.time(), periods.c.ends_at > time.time()).with_for_update()).mappings().first()
                if not row:
                    raise DomainError(409, "Hãy cấp gói tháng trước khi điều chỉnh lượt.")
                if row["allowance"] + delta < row["used"] + row["reserved"]:
                    raise DomainError(409, "Không thể giảm dưới số lượt đã dùng/đang giữ.")
                connection.execute(update(periods).where(periods.c.id == row["id"]).values(allowance=row["allowance"] + delta))
                connection.execute(insert(ledger).values(id=uid(), period_id=row["id"], kind="admin_adjust",
                    amount=delta, request_key=key, created_at=time.time()))
                self._audit(connection, actor, "quota.adjust", user_id, {"delta": delta, "reason": reason,
                    "period_id": row["id"], "before": row["allowance"], "after": row["allowance"]+delta})
        return self.quota(user_id)

    def set_active(self, actor, user_id, active, reason):
        require_admin(actor)
        with transaction(self.engine) as connection:
            row = connection.execute(select(users).where(users.c.id == user_id).with_for_update()).mappings().first()
            if not row:
                raise DomainError(404, "Không tìm thấy người dùng.")
            if row["role"] == "superadmin":
                raise DomainError(409, "Không khóa superadmin qua giao diện.")
            connection.execute(update(users).where(users.c.id == user_id).values(active=active))
            connection.execute(delete(sessions).where(sessions.c.user_id == user_id))
            self._audit(connection, actor, "user.active", user_id, {"active": active, "reason": reason})

    def audit_log(self, actor):
        require_admin(actor)
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(select(audit).order_by(audit.c.created_at.desc()).limit(200)).mappings()]

    def record_admin_action(self, actor, action, details):
        require_admin(actor)
        with transaction(self.engine) as connection:
            self._audit(connection, actor, action, "server", details)
