document.addEventListener("DOMContentLoaded", async () => {
  const form = document.getElementById("passwordForm"), error = document.getElementById("passwordError");
  let account;
  try { account = await window.TradingAccount.ready; } catch (err) { error.textContent = err.message; return; }
  form.addEventListener("submit", async event => {
    event.preventDefault(); const button = form.querySelector("button"); button.disabled = true;
    try {
      await window.TradingAccount.request("/api/auth/password", Object.fromEntries(new FormData(form)));
      location.assign("/login");
    } catch (err) { error.textContent = err.message; button.disabled = false; }
  });
  if (account.user.role === "superadmin") {
    const section = document.getElementById("ssiIntegration");
    const status = document.getElementById("ssiStatus");
    const connect = document.getElementById("ssiConnect");
    const disconnect = document.getElementById("ssiDisconnect");
    const ssiError = document.getElementById("ssiError");
    section.hidden = false;
    const refresh = async () => {
      const response = await fetch("/api/integrations/ssi/status", {cache: "no-store"});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Không đọc được trạng thái SSI.");
      status.textContent = data.connected || data.debug_token ? "Đã đăng nhập SSI; SSI đang là nguồn giá ưu tiên." : "Chưa đăng nhập SSI; hệ thống đang dùng Vietcap/DNSE dự phòng.";
      connect.hidden = Boolean(data.connected || data.debug_token);
      disconnect.hidden = !data.connected;
    };
    disconnect.addEventListener("click", async () => {
      try { await window.TradingAccount.request("/api/integrations/ssi/disconnect", {}); await refresh(); }
      catch (err) { ssiError.textContent = err.message; }
    });
    try { await refresh(); } catch (err) { ssiError.textContent = err.message; }
  }
});
