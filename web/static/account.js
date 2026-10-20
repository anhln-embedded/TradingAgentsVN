/* Shared account session. Tokens remain in HttpOnly cookies. */
(() => {
  const nativeFetch = window.fetch.bind(window);
  const account = window.TradingAccount = {user: null, csrf: null};
  window.fetch = (input, options = {}) => {
    const url = typeof input === "string" ? input : input.url;
    const sameOrigin = new URL(url, location.href).origin === location.origin;
    if (sameOrigin && !["GET", "HEAD"].includes((options.method || "GET").toUpperCase())) {
      const headers = new Headers(options.headers || {});
      headers.set("X-CSRF-Token", account.csrf || "");
      options = {...options, headers};
    }
    return nativeFetch(input, options).then(response => {
      if (sameOrigin && response.status === 401) location.assign("/login");
      return response;
    });
  };
  account.refresh = async () => {
    const response = await window.fetch("/api/auth/me");
    if (!response.ok) throw new Error("Phiên đăng nhập đã hết hạn.");
    const data = await response.json();
    account.user = data.user;
    account.csrf = data.csrf;
    const badge = document.getElementById("accountQuota");
    if (badge) {
      const quota = data.quota;
      if (data.user.role === "superadmin") {
        badge.textContent = `${data.user.email} · Không giới hạn lượt`;
        badge.title = "Tài khoản Superadmin không giới hạn phân tích";
      } else {
        badge.textContent = `${data.user.email} · ${quota.remaining} lượt còn lại · ${quota.reserved} đang giữ`;
        badge.title = quota.ends_at ? `Hạn gói: ${new Date(quota.ends_at * 1000).toLocaleString("vi-VN")}` : "Chưa có gói. Liên hệ quản trị viên để kích hoạt.";
      }
      if (badge.dataset) badge.dataset.role = data.user.role;
    }
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      window.lucide.createIcons();
    }
    for (const id of ["btnOpenConfig", "btnOpenGit", "adminLink"]) {
      const element = document.getElementById(id);
      if (element) element.hidden = data.user.role !== "superadmin";
    }
    return data;
  };
  account.request = async (url, body) => {
    const response = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Dữ liệu không hợp lệ.");
    return data;
  };
  account.ready = account.refresh();
  document.addEventListener("DOMContentLoaded", () => {
    document.getElementById("logoutButton")?.addEventListener("click", async () => {
      try { await account.request("/api/auth/logout", {}); } finally { location.assign("/login"); }
    });
  });
})();
