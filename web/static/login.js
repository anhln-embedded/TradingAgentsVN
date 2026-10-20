(() => {
  let register = false;
  const form = document.getElementById("authForm"),
        submit = document.getElementById("submit"),
        error = document.getElementById("authError"),
        switchBtn = document.getElementById("switchMode"),
        authTitle = document.getElementById("authTitle"),
        authSub = document.getElementById("authSub");

  function updateMode() {
    if (authTitle) authTitle.textContent = register ? "Tạo tài khoản" : "Đăng nhập";
    if (authSub) authSub.textContent = register ? "Đăng ký tài khoản mới để bắt đầu sử dụng" : "Nhập thông tin tài khoản để tiếp tục";
    if (submit) submit.textContent = register ? "Đăng ký" : "Đăng nhập";
    const pwdInput = document.getElementById("password");
    if (pwdInput) pwdInput.autocomplete = register ? "new-password" : "current-password";
    if (switchBtn) switchBtn.textContent = register ? "Đã có tài khoản? Đăng nhập" : "Chưa có tài khoản? Đăng ký";
    if (error) {
      error.textContent = "";
      error.style.display = "none";
    }
  }

  if (switchBtn) {
    switchBtn.addEventListener("click", () => {
      register = !register;
      updateMode();
    });
  }

  async function request(path, body) {
    const response = await fetch(path, {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-Requested-With": "TradingAgentsVN"},
      body: JSON.stringify(body)
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(typeof data.detail === "string" ? data.detail : "Vui lòng kiểm tra thông tin đã nhập.");
    }
    return data;
  }

  form.addEventListener("submit", async event => {
    event.preventDefault();
    submit.disabled = true;
    error.textContent = "";
    error.style.display = "none";
    const body = {
      email: document.getElementById("email").value.trim(),
      password: document.getElementById("password").value
    };
    try {
      if (register) await request("/api/auth/register", body);
      await request("/api/auth/login", body);
      location.assign("/");
    } catch (err) {
      error.textContent = err.message;
      error.style.display = "block";
    } finally {
      submit.disabled = false;
    }
  });
})();
