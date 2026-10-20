document.addEventListener("DOMContentLoaded", async () => {
  const account = window.TradingAccount;
  const error = document.getElementById("adminError"), message = document.getElementById("adminMessage");
  const node = (tag, text) => { const item = document.createElement(tag); if (text !== undefined) item.textContent = text; return item; };
  let userRows = [];
  let offset = 0, search = "";
  async function load() {
    const [usersResponse, auditResponse] = await Promise.all([fetch(`/api/admin/users?offset=${offset}&search=${encodeURIComponent(search)}`), fetch("/api/admin/audit")]);
    if (!usersResponse.ok || !auditResponse.ok) throw new Error("Không thể tải dữ liệu quản trị.");
    userRows = (await usersResponse.json()).users;
    document.getElementById("previousPage").disabled = offset === 0;
    document.getElementById("nextPage").disabled = userRows.length < 100;
    document.getElementById("pageLabel").textContent = `Trang ${offset / 100 + 1}`;
    const names = Object.fromEntries(userRows.map(user => [user.id, user.email]));
    const body = document.getElementById("usersBody"); body.replaceChildren();
    for (const user of userRows) {
      const quota = user.quota, row = node("tr");
      for (const text of [
        user.email,
        `${user.role} · ${user.active ? "Hoạt động" : "Đã khóa"}`,
        user.role === "superadmin" ? "Không giới hạn" : (quota.plan ? `${quota.plan} · ${new Date(quota.ends_at*1000).toLocaleDateString("vi-VN")}` : "Chưa có gói"),
        user.role === "superadmin" ? "Không giới hạn" : quota.remaining,
        user.role === "superadmin" ? `${quota.used}` : `${quota.used} / ${quota.reserved}`
      ]) row.append(node("td", text));
      const cell = node("td");
      if (user.role !== "superadmin") {
        const button = node("button", user.active ? "Khóa" : "Mở khóa"); button.className = "secondary";
        button.onclick = async () => {
          const reason = prompt("Lý do thay đổi trạng thái (ít nhất 5 ký tự):");
          if (!reason) return;
          button.disabled = true;
          try { await account.request(`/api/admin/users/${user.id}/active`, {active: !user.active, reason}); await load(); }
          catch (err) { error.textContent = err.message; button.disabled = false; }
        };
        cell.append(button);
      }
      row.append(cell); body.append(row);
    }
    for (const select of document.querySelectorAll(".user-select")) {
      const selected = select.value; select.replaceChildren();
      for (const user of userRows) { const option = node("option", user.email); option.value = user.id; select.append(option); }
      if (userRows.some(user => user.id === selected)) select.value = selected;
    }
    const auditBody = document.getElementById("auditBody"); auditBody.replaceChildren();
    for (const entry of (await auditResponse.json()).entries) {
      const row = node("tr");
      for (const text of [new Date(entry.created_at*1000).toLocaleString("vi-VN"), names[entry.actor_id] || entry.actor_id,
        entry.action, `${names[entry.target_id] || entry.target_id} · ${entry.details}`]) row.append(node("td", text));
      auditBody.append(row);
    }
    await account.refresh();
  }
  function bind(id, action) {
    document.getElementById(id).addEventListener("submit", async event => {
      event.preventDefault(); error.textContent = ""; message.textContent = "";
      const form = event.currentTarget, button = form.querySelector("button"); button.disabled = true;
      try { await action(Object.fromEntries(new FormData(form))); message.textContent = "Đã lưu thay đổi."; await load(); }
      catch (err) { error.textContent = err.message; }
      finally { button.disabled = false; }
    });
  }
  try {
    await account.ready;
    if (account.user.role !== "superadmin") { location.assign("/"); return; }
    bind("createUser", data => account.request("/api/admin/users", data));
    bind("subscriptionForm", ({userId, plan, allowance, reason}) => account.request(`/api/admin/users/${userId}/subscription`, {plan, allowance: Number(allowance), reason}));
    bind("quotaForm", ({userId, delta, reason}) => account.request(`/api/admin/users/${userId}/quota`, {delta: Number(delta), reason, request_key: crypto.randomUUID()}));
    document.getElementById("searchUsers").onsubmit = event => {
      event.preventDefault(); search = document.getElementById("userSearch").value.trim(); offset = 0;
      load().catch(err => {error.textContent = err.message;});
    };
    document.getElementById("previousPage").onclick = () => {offset = Math.max(0, offset-100); load().catch(err => {error.textContent = err.message;});};
    document.getElementById("nextPage").onclick = () => {offset += 100; load().catch(err => {error.textContent = err.message;});};
    document.getElementById("reload").onclick = () => load().catch(err => {error.textContent = err.message;});
    await load();
  } catch (err) { error.textContent = err.message; }
});
