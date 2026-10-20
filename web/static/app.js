/**
 * TradingAgentsVN - Web Dashboard Application Logic
 * Integrates real-time SSE streaming, TradingView candlestick charts,
 * agent node status visualizer, and markdown rendering.
 */

document.addEventListener("DOMContentLoaded", async () => {
  try { await window.TradingAccount.ready; } catch (err) { console.warn(err); return; }
  // Initialize Lucide icons
  if (window.lucide) {
    window.lucide.createIcons();
  }

  // State Management
  const state = {
    currentTicker: "BSR",
    currentTradeDate: new Date().toISOString().split("T")[0],
    currentTimeframe: "1D",
    isAnalyzing: false,
    eventSource: null,
    timerInterval: null,
    secondsElapsed: 0,
    chart: null,
    candleSeries: null,
    volumeSeries: null,
    activeTab: "summary",
    reportsData: {
      market: "",
      fundamentals: "",
      sentiment: "",
      news: "",
      trader: "",
    },
    debateData: {
      bull: "",
      bear: "",
      judge: "",
      risk: "",
    },
    isUnloading: false,
    currentExchange: null,
    marketSession: null,
  };

  window.addEventListener("beforeunload", () => {
    state.isUnloading = true;
    if (state.eventSource) {
      try { state.eventSource.close(); } catch (_) {}
      state.eventSource = null;
    }
  });

  // DOM Elements
  const el = {
    tickerInput: document.getElementById("tickerInput"),
    tradeDateInput: document.getElementById("tradeDateInput"),
    quickTickers: document.getElementById("quickTickers"),
    btnStartAnalysis: document.getElementById("btnStartAnalysis"),
    systemStatus: document.getElementById("systemStatus"),
    elapsedTimer: document.getElementById("elapsedTimer"),
    currentStepLabel: document.getElementById("currentStepLabel"),
    candlestickChart: document.getElementById("candlestickChart"),
    chartPlaceholder: document.getElementById("chartPlaceholder"),
    signalBadge: document.getElementById("signalBadge"),
    signalVal: document.getElementById("signalVal"),
    spotlightTicker: document.getElementById("spotlightTicker"),
    spotlightCompany: document.getElementById("spotlightCompany"),
    spotlightExchange: document.getElementById("spotlightExchange"),
    metricPriceBox: document.getElementById("metricPriceBox"),
    metricPriceLabel: document.getElementById("metricPriceLabel"),
    livePriceBadge: document.getElementById("livePriceBadge"),
    metricClosePrice: document.getElementById("metricClosePrice"),
    metricCloseDateSub: document.getElementById("metricCloseDateSub"),
    metricPriceChangeTag: document.getElementById("metricPriceChangeTag"),
    metricEntry: document.getElementById("metricEntry"),
    metricEntryBox: document.getElementById("metricEntryBox"),
    metricEntryLabel: document.getElementById("metricEntryLabel"),
    metricEntrySub: document.getElementById("metricEntrySub"),
    metricStopLoss: document.getElementById("metricStopLoss"),
    metricStopLossSub: document.getElementById("metricStopLossSub"),
    metricSizing: document.getElementById("metricSizing"),
    tickerExchangeBadge: document.getElementById("tickerExchangeBadge"),
    dateFormatHint: document.getElementById("dateFormatHint"),
    pipelineSection: document.querySelector(".pipeline-section"),
    btnTogglePipeline: document.getElementById("btnTogglePipeline"),
    iconTogglePipeline: document.getElementById("iconTogglePipeline"),
    textTogglePipeline: document.getElementById("textTogglePipeline"),
    pipelineSummaryBar: document.getElementById("pipelineSummaryBar"),
    pipelineBoard: document.getElementById("pipelineBoard"),
    progressMeta: document.getElementById("progressMeta"),
    pmPillsBar: document.getElementById("pmPillsBar"),
    pillRating: document.getElementById("pillRating"),
    pillRatingVal: document.getElementById("pillRatingVal"),
    pillTarget: document.getElementById("pillTarget"),
    pillTargetVal: document.getElementById("pillTargetVal"),
    pillHorizon: document.getElementById("pillHorizon"),
    pillHorizonVal: document.getElementById("pillHorizonVal"),
    reasoningContent: document.getElementById("reasoningContent"),
    bullDebateContent: document.getElementById("bullDebateContent"),
    bearDebateContent: document.getElementById("bearDebateContent"),
    judgeDebateContent: document.getElementById("judgeDebateContent"),
    riskCouncilContent: document.getElementById("riskCouncilContent"),
    currentReportTitle: document.getElementById("currentReportTitle"),
    reportViewerContent: document.getElementById("reportViewerContent"),
    btnCopyReport: document.getElementById("btnCopyReport"),
    terminalLogs: document.getElementById("terminalLogs"),
    toolsList: document.getElementById("toolsList"),
    chkAutoScroll: document.getElementById("chkAutoScroll"),
    btnClearTerminal: document.getElementById("btnClearTerminal"),
    historyModal: document.getElementById("historyModal"),
    btnOpenHistory: document.getElementById("btnOpenHistory"),
    btnCloseHistory: document.getElementById("btnCloseHistory"),
    historyList: document.getElementById("historyList"),

    // Config Elements
    btnOpenConfig: document.getElementById("btnOpenConfig"),
    btnCloseConfig: document.getElementById("btnCloseConfig"),
    btnCancelConfig: document.getElementById("btnCancelConfig"),
    btnSaveConfig: document.getElementById("btnSaveConfig"),
    configModal: document.getElementById("configModal"),
    cfgProvider: document.getElementById("cfgProvider"),
    cfgApiKey: document.getElementById("cfgApiKey"),
    cfgKeyEnvHint: document.getElementById("cfgKeyEnvHint"),
    btnToggleKeyVis: document.getElementById("btnToggleKeyVis"),
    iconKeyEye: document.getElementById("iconKeyEye"),
    cfgKeyStatus: document.getElementById("cfgKeyStatus"),
    cfgBackendUrl: document.getElementById("cfgBackendUrl"),
    cfgDeepModel: document.getElementById("cfgDeepModel"),
    cfgQuickModel: document.getElementById("cfgQuickModel"),
    cfgTimeout: document.getElementById("cfgTimeout"),
    cfgMaxRetries: document.getElementById("cfgMaxRetries"),
    configToast: document.getElementById("configToast"),

    // Git Upstream Elements
    btnOpenGit: document.getElementById("btnOpenGit"),
    btnCloseGit: document.getElementById("btnCloseGit"),
    gitModal: document.getElementById("gitModal"),
    btnGitCheck: document.getElementById("btnGitCheck"),
    btnGitPull: document.getElementById("btnGitPull"),
    gitBranch: document.getElementById("gitBranch"),
    gitStatusBadge: document.getElementById("gitStatusBadge"),
    gitCommitsList: document.getElementById("gitCommitsList"),
    gitTerminalLogs: document.getElementById("gitTerminalLogs"),

    // Market Clock & Session Elements
    liveClockText: document.getElementById("liveClockText"),
    liveClockDateText: document.getElementById("liveClockDateText"),
    marketSessionBadge: document.getElementById("marketSessionBadge"),
    marketSessionText: document.getElementById("marketSessionText"),
    marketClockWidget: document.getElementById("marketClockWidget"),
    btnSetToday: document.getElementById("btnSetToday"),
    dateMetaHint: document.getElementById("dateMetaHint"),
  };

  // =========================================================================
  // Senior Fintech Formatters & Helpers
  // =========================================================================

  // Chuẩn hóa tiền tệ VNĐ: phân cách hàng nghìn bằng dấu phẩy, không có số thập phân
  function formatVNDPrice(val) {
    if (val === null || val === undefined || val === "" || val === "--") return "--";
    if (typeof val === "number") {
      return Math.round(val).toLocaleString("en-US") + " VND";
    }
    const str = String(val).replace(/[–—]/g, "-");
    const tokens = str.match(/\d{1,3}(?:[.,]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?/g);
    if (!tokens) return "--";
    const prices = tokens.slice(0, str.includes("-") ? 2 : 1).map((token) => {
      const normalized = /^\d{1,3}(?:\.\d{3})+$/.test(token)
        ? token.replace(/\./g, "") : token.replace(/,/g, "");
      return Math.round(Number(normalized)).toLocaleString("en-US");
    });
    return prices.join(" - ") + " VND";
  }

  // Format ngày chuẩn Việt Nam DD/MM/YYYY
  function formatDateVN(dateInput) {
    if (!dateInput) return "";
    if (dateInput instanceof Date) {
      const d = String(dateInput.getDate()).padStart(2, "0");
      const m = String(dateInput.getMonth() + 1).padStart(2, "0");
      const y = dateInput.getFullYear();
      return `${d}/${m}/${y}`;
    }
    const str = String(dateInput).trim();
    const matchIso = str.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (matchIso) {
      return `${matchIso[3]}/${matchIso[2]}/${matchIso[1]}`;
    }
    if (/^\d{2}\/\d{2}\/\d{4}$/.test(str)) {
      return str;
    }
    const parsed = new Date(str);
    if (!isNaN(parsed.getTime())) {
      const d = String(parsed.getDate()).padStart(2, "0");
      const m = String(parsed.getMonth() + 1).padStart(2, "0");
      const y = parsed.getFullYear();
      return `${d}/${m}/${y}`;
    }
    return str;
  }

  function formatNumberVN(val) {
    if (val === null || val === undefined || isNaN(val)) return "--";
    return Math.round(Number(val)).toLocaleString("en-US");
  }

  function formatVolumeCompact(vol) {
    if (!vol || isNaN(vol)) return "--";
    const v = Number(vol);
    if (v >= 1_000_000) return (v / 1_000_000).toFixed(2) + "M cp";
    if (v >= 1_000) return (v / 1_000).toFixed(1) + "K cp";
    return Math.round(v).toLocaleString("en-US") + " cp";
  }

  state.lastPrice = null;
  state.quoteRequest = 0;

  async function fetchLiveQuote(symbol = state.currentTicker) {
    if (!symbol) return;
    const clean = symbol.trim().toUpperCase();
    const today = getLocalDateString();
    const isHistorical = state.currentTradeDate && state.currentTradeDate < today;

    if (isHistorical) {
      if (el.metricPriceLabel) el.metricPriceLabel.textContent = "Giá Đóng Cửa";
      if (el.livePriceBadge) el.livePriceBadge.style.display = "none";
      if (el.metricPriceChangeTag) el.metricPriceChangeTag.style.display = "none";
      return;
    }

    const reqId = ++state.quoteRequest;
    try {
      const res = await fetch(`/api/quote/${encodeURIComponent(clean)}`);
      if (!res.ok) return;
      const data = await res.json();
      if (reqId !== state.quoteRequest || clean !== state.currentTicker) return;
      state.currentExchange = data.exchange || null;
      updateTickerExchangeBadge(clean, state.currentExchange);
      await fetchMarketSession(clean);

      const session = state.marketSession || getMarketSessionInfo();
      const isTrading = session.status === "open";
      const isBreak = session.status === "break";

      // 1. Update Label & Badge
      if (isTrading) {
        if (el.metricPriceLabel) el.metricPriceLabel.textContent = "Giá Khớp Lệnh";
        if (el.livePriceBadge) {
          el.livePriceBadge.style.display = "inline-flex";
          el.livePriceBadge.innerHTML = '<span class="pulse-dot"></span> LIVE';
        }
      } else if (isBreak) {
        const breakText = session.text.includes("trưa") ? "Giá Tạm Khớp (Nghỉ trưa)" : "Giá Thỏa Thuận";
        if (el.metricPriceLabel) el.metricPriceLabel.textContent = breakText;
        if (el.livePriceBadge) {
          el.livePriceBadge.style.display = "inline-flex";
          el.livePriceBadge.innerHTML = '<span class="pulse-dot" style="background:#f59e0b"></span> ' + (session.text.includes("trưa") ? "NGHỈ TRƯA" : "THỎA THUẬN");
        }
      } else {
        if (el.metricPriceLabel) el.metricPriceLabel.textContent = "Giá Đóng Cửa";
        if (el.livePriceBadge) el.livePriceBadge.style.display = "none";
      }

      // 2. Animate Tick Flash if price changed
      const prevPrice = state.lastPrice;
      if (prevPrice !== null && (isTrading || isBreak)) {
        el.metricClosePrice.classList.remove("flash-up", "flash-down");
        void el.metricClosePrice.offsetWidth;
        if (data.price > prevPrice) {
          el.metricClosePrice.classList.add("flash-up");
        } else if (data.price < prevPrice) {
          el.metricClosePrice.classList.add("flash-down");
        }
      }
      state.lastPrice = data.price;

      // 3. Update Price & Color
      el.metricClosePrice.textContent = formatVNDPrice(data.price);
      el.metricClosePrice.className = "metric-value highlight";
      let tagClass = "ref";
      let arrow = "■ ";
      let sign = "";

      if (data.ceiling && data.price >= data.ceiling) {
        el.metricClosePrice.classList.add("price-ceil");
        tagClass = "ceil";
        arrow = "▲ ";
        sign = "+";
      } else if (data.floor && data.price <= data.floor) {
        el.metricClosePrice.classList.add("price-floor");
        tagClass = "floor";
        arrow = "▼ ";
      } else if (data.change > 0) {
        el.metricClosePrice.classList.add("price-up");
        tagClass = "up";
        arrow = "▲ ";
        sign = "+";
      } else if (data.change < 0) {
        el.metricClosePrice.classList.add("price-down");
        tagClass = "down";
        arrow = "▼ ";
      } else {
        el.metricClosePrice.classList.add("price-ref");
        tagClass = "ref";
      }

      // 4. Update Change Tag
      if (el.metricPriceChangeTag) {
        el.metricPriceChangeTag.style.display = "inline-flex";
        el.metricPriceChangeTag.className = `metric-change-tag ${tagClass}`;
        const pctStr = data.change_percent >= 0 ? `+${data.change_percent}%` : `${data.change_percent}%`;
        el.metricPriceChangeTag.textContent = `${arrow}${sign}${formatNumberVN(data.change)} (${pctStr})`;
      }

      // 5. Update Subtext
      if (el.metricCloseDateSub) {
        if (isTrading || isBreak) {
          el.metricCloseDateSub.textContent = `TC: ${formatNumberVN(data.ref_price)} • KL: ${formatVolumeCompact(data.volume)}`;
        } else {
          el.metricCloseDateSub.textContent = `Chốt ngày ${formatDateVN(today)} • TC: ${formatNumberVN(data.ref_price)}`;
        }
      }
    } catch (err) {
      console.warn("Lỗi cập nhật giá realtime:", err);
    }
  }

  function updateTickerExchangeBadge(sym, resolvedExchange = state.currentExchange) {
    const exchange = resolvedExchange || "Chưa xác định";
    if (el.tickerExchangeBadge) {
      el.tickerExchangeBadge.textContent = exchange;
    }
    if (el.spotlightExchange) {
      const marginLimit = exchange === "HNX" ? "±10%" : exchange === "UPCOM" ? "±15%" : exchange === "HOSE" ? "±7%" : null;
      el.spotlightExchange.innerHTML = `<i data-lucide="building"></i> ${exchange}${marginLimit ? ` (${marginLimit})` : ""}`;
      if (window.lucide) window.lucide.createIcons();
    }
  }

  // Simple Moving Average (SMA) Calculation for Lightweight Charts
  function calculateSMA(candles, period = 50) {
    if (!candles || candles.length < period) return [];
    const result = [];
    for (let i = period - 1; i < candles.length; i++) {
      let sum = 0;
      for (let j = 0; j < period; j++) {
        sum += candles[i - j].close;
      }
      result.push({
        time: candles[i].time,
        value: Math.round(sum / period),
      });
    }
    return result;
  }

  // Stop Loss Price Line on Candlestick Chart
  function updateStopLossPriceLine(price) {
    if (!state.candleSeries) return;
    if (state.stopLossLine) {
      try {
        state.candleSeries.removePriceLine(state.stopLossLine);
      } catch (e) {}
      state.stopLossLine = null;
    }
    if (price && price > 0) {
      state.stopLossLine = state.candleSeries.createPriceLine({
        price: price,
        color: "#f43f5e",
        lineWidth: 2,
        lineStyle: LightweightCharts.LineStyle.Dashed,
        axisLabelVisible: true,
        title: `Cắt Lỗ: ${Math.round(price).toLocaleString("en-US")} VND`,
      });
    }
  }

  // Accordion Toggle for Pipeline Section
  function togglePipelineSection(collapse) {
    if (!el.pipelineSection) return;
    if (collapse) {
      el.pipelineSection.classList.add("collapsed");
      if (el.pipelineSummaryBar) el.pipelineSummaryBar.style.display = "flex";
      if (el.textTogglePipeline) el.textTogglePipeline.textContent = "Xem chi tiết";
    } else {
      el.pipelineSection.classList.remove("collapsed");
      if (el.pipelineSummaryBar) el.pipelineSummaryBar.style.display = "none";
      if (el.textTogglePipeline) el.textTogglePipeline.textContent = "Thu gọn";
    }
    if (window.lucide) window.lucide.createIcons();

    setTimeout(() => {
      if (state.chart && el.candlestickChart && el.candlestickChart.clientWidth) {
        state.chart.resize(el.candlestickChart.clientWidth, el.candlestickChart.clientHeight);
      }
    }, 150);
  }

  // Helper functions for dates & time
  function getLocalDateString(d = new Date()) {
    const year = d.getFullYear();
    const month = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  }

  function formatVietnameseDateText(d = new Date()) {
    const days = ["Chủ Nhật", "Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy"];
    const dayName = days[d.getDay()];
    const day = String(d.getDate()).padStart(2, "0");
    const month = String(d.getMonth() + 1).padStart(2, "0");
    const year = d.getFullYear();
    return `${dayName}, ${day}/${month}/${year}`;
  }

  // Set default trade date to TODAY & strictly block future dates
  const todayStr = getLocalDateString();
  state.currentTradeDate = todayStr;
  el.tradeDateInput.value = state.currentTradeDate;
  el.tradeDateInput.max = todayStr; // Chỉ cho phép chọn ngày hôm nay hoặc quá khứ
  if (el.dateFormatHint) {
    el.dateFormatHint.textContent = `Ngày chọn: ${formatDateVN(todayStr)}`;
  }
  updateTickerExchangeBadge(state.currentTicker);

  // 1-Click Set Today Button
  if (el.btnSetToday) {
    el.btnSetToday.addEventListener("click", () => {
      const today = getLocalDateString();
      el.tradeDateInput.value = today;
      state.currentTradeDate = today;
      if (el.dateFormatHint) {
        el.dateFormatHint.textContent = `Ngày chọn: ${formatDateVN(today)}`;
      }
      // Load market data up to today
      loadMarketChart(state.currentTicker, state.currentTimeframe, today);
      fetchLiveQuote(state.currentTicker);
    });
  }

  el.tradeDateInput.addEventListener("change", () => {
    let chosen = el.tradeDateInput.value;
    const today = getLocalDateString();
    if (!chosen) {
      chosen = today;
      el.tradeDateInput.value = today;
    }
    // Chặn ngày tương lai
    if (chosen > today) {
      alert(`Không thể chọn ngày trong tương lai (${chosen})!\nHệ thống tự động đưa về ngày hôm nay (${today}).`);
      chosen = today;
      el.tradeDateInput.value = today;
    }
    state.currentTradeDate = chosen;
    if (el.dateFormatHint) {
      el.dateFormatHint.textContent = `Ngày chọn: ${formatDateVN(chosen)}`;
    }
    // Cập nhật biểu đồ và toàn bộ thông tin thị trường đến thời điểm ngày đó
    loadMarketChart(state.currentTicker, state.currentTimeframe, chosen);
    if (chosen === today) {
      fetchLiveQuote(state.currentTicker);
    } else {
      if (el.metricPriceLabel) el.metricPriceLabel.textContent = "Giá Đóng Cửa";
      if (el.livePriceBadge) el.livePriceBadge.style.display = "none";
      if (el.metricPriceChangeTag) el.metricPriceChangeTag.style.display = "none";
    }
  });

  // Real-time Live Market Clock & Session Status
  function getMarketSessionInfo(date = new Date()) {
    const day = date.getDay(); // 0 = CN, 6 = T7
    const hours = date.getHours();
    const minutes = date.getMinutes();
    const totalMinutes = hours * 60 + minutes;

    if (day === 0 || day === 6) {
      return {
        status: "closed",
        cssClass: "closed",
        text: "Đóng cửa (Cuối tuần)",
        desc: "Thị trường nghỉ giao dịch Thứ 7 & Chủ Nhật",
      };
    }

    if (totalMinutes < 540) {
      return {
        status: "closed",
        cssClass: "closed",
        text: "Chưa mở cửa",
        desc: "Phiên sáng mở cửa lúc 09:00 (ATO)",
      };
    } else if (totalMinutes < 555) {
      return {
        status: "open",
        cssClass: "open",
        text: "Đang giao dịch (ATO)",
        desc: "Khớp lệnh định kỳ mở cửa (09:00 - 09:15)",
      };
    } else if (totalMinutes < 690) {
      return {
        status: "open",
        cssClass: "open",
        text: "Đang giao dịch (Phiên sáng)",
        desc: "Khớp lệnh liên tục (09:15 - 11:30)",
      };
    } else if (totalMinutes < 780) {
      return {
        status: "break",
        cssClass: "break",
        text: "Nghỉ trưa",
        desc: "Phiên chiều mở lúc 13:00",
      };
    } else if (totalMinutes < 870) {
      return {
        status: "open",
        cssClass: "open",
        text: "Đang giao dịch (Phiên chiều)",
        desc: "Khớp lệnh liên tục (13:00 - 14:30)",
      };
    } else if (totalMinutes < 885) {
      return {
        status: "open",
        cssClass: "open",
        text: "Đang giao dịch (ATC)",
        desc: "Khớp lệnh định kỳ đóng cửa (14:30 - 14:45)",
      };
    } else if (totalMinutes < 900) {
      return {
        status: "break",
        cssClass: "break",
        text: "Thỏa thuận",
        desc: "Giao dịch thỏa thuận (14:45 - 15:00)",
      };
    } else {
      return {
        status: "closed",
        cssClass: "closed",
        text: "Đóng cửa thị trường",
        desc: "Kết thúc phiên giao dịch (sau 15:00)",
      };
    }
  }

  async function fetchMarketSession(symbol = state.currentTicker) {
    try {
      const response = await fetch(`/api/market-session/${encodeURIComponent(symbol)}`, {cache: "no-store"});
      if (!response.ok) return;
      const value = await response.json();
      state.marketSession = {
        status: value.status,
        cssClass: value.status === "agreement" ? "break" : value.status,
        text: value.label,
        desc: `${value.exchange} • ${value.time}`,
      };
    } catch (err) {
      console.warn("Không thể tải trạng thái phiên Việt Nam:", err);
    }
  }

  function updateMarketClockAndSession() {
    const now = new Date();
    const clockParts = new Intl.DateTimeFormat("vi-VN", {
      timeZone: "Asia/Ho_Chi_Minh", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
    }).format(now);

    if (el.liveClockText) {
      el.liveClockText.textContent = clockParts;
    }

    if (el.liveClockDateText) {
      el.liveClockDateText.textContent = formatVietnameseDateText(now);
    }

    if (el.marketSessionBadge && el.marketSessionText) {
      const session = state.marketSession || getMarketSessionInfo(now);
      el.marketSessionBadge.className = `session-status ${session.cssClass}`;
      el.marketSessionText.textContent = session.text;
      if (el.marketClockWidget) {
        el.marketClockWidget.title = `Giờ Việt Nam: ${clockParts} | ${session.desc}`;
      }
    }
  }

  setInterval(updateMarketClockAndSession, 1000);
  updateMarketClockAndSession();

  // 1. Initialize Quick Ticker Chips
  const popularTickers = ["BSR", "PLX", "FPT", "VNM", "HPG", "SSI", "VCB", "MBB", "TCB", "MWG"];
  popularTickers.forEach((sym) => {
    const chip = document.createElement("button");
    chip.className = `ticker-chip ${sym === state.currentTicker ? "active" : ""}`;
    chip.textContent = sym;
    chip.addEventListener("click", () => selectTicker(sym));
    el.quickTickers.appendChild(chip);
  });

  // Ticker input event
  el.tickerInput.addEventListener("change", () => {
    const sym = el.tickerInput.value.trim().toUpperCase();
    if (sym) selectTicker(sym);
  });

  async function selectTicker(sym) {
    if (state.isAnalyzing) {
      el.tickerInput.value = state.currentTicker;
      return;
    }
    resetReportData();
    const request = state.viewRequest;
    state.currentTicker = sym;
    state.currentExchange = null;
    state.marketSession = null;
    el.tickerInput.value = sym;
    document.querySelectorAll(".ticker-chip").forEach(chip => chip.classList.toggle("active", chip.textContent === sym));
    updateCompanyIdentity(sym);
    resetAgentNodes();
    el.currentStepLabel.textContent = "Đang tải phân tích gần nhất...";
    if (el.progressMeta) el.progressMeta.classList.remove("status-completed");
    loadMarketChart(sym);
    fetchLiveQuote(sym);
    renderMarkdown(el.reasoningContent, "Đang tải phân tích gần nhất của " + sym + "...");
    try {
      const res = await fetch("/api/history", {cache: "no-store"});
      if (!res.ok) throw new Error("Không thể tải lịch sử phân tích");
      const data = await res.json();
      if (request !== state.viewRequest || state.currentTicker !== sym || state.isAnalyzing) return;
      const reports = (data.reports || []).filter(item => item.ticker.toUpperCase() === sym);
      reports.sort((a, b) => (b.saved_at || 0) - (a.saved_at || 0));
      if (reports.length) {
        await loadPreExistingReport(reports[0].id, true);
      } else {
        el.currentStepLabel.textContent = "Chưa có phân tích cho " + sym;
        renderMarkdown(el.reasoningContent, "Chưa có báo cáo phân tích cho " + sym + ". Hãy nhấn Bắt Đầu Phân Tích để tạo báo cáo.");
      }
    } catch (err) {
      if (request !== state.viewRequest || state.currentTicker !== sym) return;
      el.currentStepLabel.textContent = "Không thể tải báo cáo";
      renderMarkdown(el.reasoningContent, "Không thể tải phân tích gần nhất. Vui lòng chọn lại mã để thử lại.");
    }
  }

  // Timeframe switcher (1D, 1W, 1M)
  document.querySelectorAll(".btn-tf").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".btn-tf").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const tf = btn.getAttribute("data-tf") || "1D";
      state.currentTimeframe = tf;
      loadMarketChart(state.currentTicker, tf);
    });
  });

  // Analyst pills toggle
  document.querySelectorAll(".pill-checkbox").forEach((pill) => {
    pill.addEventListener("click", () => {
      const chk = pill.querySelector("input");
      chk.checked = !chk.checked;
      pill.classList.toggle("active", chk.checked);
    });
  });

  // Tab switching
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const targetTab = btn.getAttribute("data-tab");
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".tab-content").forEach((c) => c.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById(`tab-${targetTab}`).classList.add("active");
      state.activeTab = targetTab;

      // Resize chart if switching back to summary
      if (targetTab === "summary" && state.chart) {
        state.chart.resize(el.candlestickChart.clientWidth, el.candlestickChart.clientHeight);
      }
    });
  });

  // Report sub-navigation
  document.querySelectorAll(".report-nav-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".report-nav-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const reportKey = btn.getAttribute("data-report");
      renderReportViewer(reportKey, btn.textContent.trim());
    });
  });

  // Copy Markdown
  el.btnCopyReport.addEventListener("click", () => {
    const activeBtn = document.querySelector(".report-nav-btn.active");
    const reportKey = activeBtn ? activeBtn.getAttribute("data-report") : "market";
    const text = state.reportsData[reportKey] || "";
    if (text) {
      navigator.clipboard.writeText(text);
      el.btnCopyReport.innerHTML = '<i data-lucide="check"></i> Đã sao chép!';
      if (window.lucide) window.lucide.createIcons();
      setTimeout(() => {
        el.btnCopyReport.innerHTML = '<i data-lucide="copy"></i> Sao chép Markdown';
        if (window.lucide) window.lucide.createIcons();
      }, 2000);
    }
  });

  // Terminal actions
  el.btnClearTerminal.addEventListener("click", () => {
    el.terminalLogs.innerHTML = "";
  });

  // History Modal
  el.btnOpenHistory.addEventListener("click", openHistoryModal);
  el.btnCloseHistory.addEventListener("click", () => el.historyModal.classList.remove("open"));
  el.historyModal.addEventListener("click", (e) => {
    if (e.target === el.historyModal) el.historyModal.classList.remove("open");
  });

  // Config Modal
  el.btnOpenConfig.addEventListener("click", openConfigModal);
  el.btnCloseConfig.addEventListener("click", () => el.configModal.classList.remove("open"));
  el.btnCancelConfig.addEventListener("click", () => el.configModal.classList.remove("open"));
  el.configModal.addEventListener("click", (e) => {
    if (e.target === el.configModal) el.configModal.classList.remove("open");
  });
  el.cfgProvider.addEventListener("change", onProviderChange);
  el.btnToggleKeyVis.addEventListener("click", toggleKeyVisibility);
  el.btnSaveConfig.addEventListener("click", saveAiConfig);

  // Git Modal
  el.btnOpenGit.addEventListener("click", openGitModal);
  el.btnCloseGit.addEventListener("click", () => el.gitModal.classList.remove("open"));
  el.gitModal.addEventListener("click", (e) => {
    if (e.target === el.gitModal) el.gitModal.classList.remove("open");
  });
  el.btnGitCheck.addEventListener("click", checkGitUpstream);
  el.btnGitPull.addEventListener("click", pullGitUpstream);

  // Pipeline Accordion Toggle Click
  if (el.btnTogglePipeline) {
    el.btnTogglePipeline.addEventListener("click", () => {
      const isCollapsed = el.pipelineSection && el.pipelineSection.classList.contains("collapsed");
      togglePipelineSection(!isCollapsed);
    });
  }

  // Start / Cancel Analysis Toggle Click
  el.btnStartAnalysis.addEventListener("click", () => {
    if (state.isAnalyzing) {
      cancelCurrentAnalysis();
    } else {
      startAnalysis();
    }
  });

  // Cancel Analysis Handler
  async function cancelCurrentAnalysis() {
    let runId = state.currentRunId;
    if (!runId) {
      try {
        const res = await fetch("/api/workspace");
        if (res.ok) {
          const data = await res.json();
          if (data.run && (data.run.status === "running" || data.run.status === "queued")) {
            runId = data.run.id;
          }
        }
      } catch (err) {
        console.warn("Lỗi kiểm tra phiên cần hủy:", err);
      }
    }

    if (!runId && !state.isAnalyzing) {
      alert("Hiện không có phiên phân tích nào đang chạy để hủy.");
      return;
    }

    el.btnStartAnalysis.disabled = true;
    el.btnStartAnalysis.innerHTML = '<i data-lucide="loader" class="spin"></i> <span>Đang hủy...</span>';
    if (window.lucide) window.lucide.createIcons();

    try {
      if (runId) {
        await window.TradingAccount.request(`/api/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" });
      }
    } catch (err) {
      console.warn("Lỗi gọi API hủy phiên:", err);
    } finally {
      handleAnalysisError("Phiên phân tích đã được hủy bởi người dùng.");
      window.TradingAccount.refresh().catch(console.warn);
    }
  }

  // Initialize TradingView Chart
  initTradingViewChart();
  // Server state is shared across devices of the authenticated account.
  async function syncActiveRun() {
    if (state.isAnalyzing || state.syncingRun) return;
    state.syncingRun = true;
    try {
      const response = await fetch("/api/workspace");
      if (!response.ok) throw new Error("Không thể tải phiên đang chạy.");
      const run = (await response.json()).run;
      if (run && run.id !== state.currentRunId && !state.isAnalyzing) {
        if (run.status === "cancelled" || run.status === "failed") return;
        el.tickerInput.value = run.ticker;
        el.tradeDateInput.value = run.trade_date;
        await startAnalysis(run);
      }
    } catch (err) { console.warn(err); }
    finally { state.syncingRun = false; }
  }
  await syncActiveRun();
  if (!state.isAnalyzing) {
    selectTicker(state.currentTicker);
  }
  fetchLiveQuote(state.currentTicker);

  setInterval(async () => {
    await syncActiveRun();
    window.TradingAccount.refresh().catch(console.warn);
  }, 4000);

  // Live Quote Polling every 3.5s for real-time stock ticker updates
  setInterval(() => {
    if (document.hidden) return;
    const today = getLocalDateString();
    if (!state.currentTradeDate || state.currentTradeDate === today) {
      fetchLiveQuote(state.currentTicker);
    }
  }, 3500);

  // =========================================================================
  // TradingView Lightweight Charts
  // =========================================================================
  function initTradingViewChart() {
    if (!window.LightweightCharts) return;

    el.chartPlaceholder.style.display = "none";
    state.chart = LightweightCharts.createChart(el.candlestickChart, {
      width: el.candlestickChart.clientWidth || 600,
      height: 440,
      layout: {
        background: { type: "solid", color: "#131c2e" },
        textColor: "#94a3b8",
        fontSize: 11,
        fontFamily: "'Inter', sans-serif",
      },
      localization: {
        priceFormatter: (price) => Math.round(price).toLocaleString("en-US"),
      },
      grid: {
        vertLines: { color: "#1e293b", style: 1 },
        horzLines: { color: "#1e293b", style: 1 },
      },
      rightPriceScale: {
        borderColor: "#1e293b",
        scaleMargins: { top: 0.1, bottom: 0.2 },
      },
      timeScale: {
        borderColor: "#1e293b",
        timeVisible: true,
        secondsVisible: false,
      },
      crosshair: {
        mode: LightweightCharts.CrosshairMode.Normal,
      },
    });

    // Main Candlestick Series without decimals on price axis
    state.candleSeries = state.chart.addCandlestickSeries({
      upColor: "#10b981",
      downColor: "#f43f5e",
      borderVisible: false,
      wickUpColor: "#10b981",
      wickDownColor: "#f43f5e",
      priceFormat: {
        type: "custom",
        formatter: (price) => Math.round(price).toLocaleString("en-US"),
        minMove: 1,
      },
    });

    // SMA 50 Line Series (Amber)
    state.sma50Series = state.chart.addLineSeries({
      color: "#f59e0b",
      lineWidth: 2,
      title: "SMA 50",
      priceFormat: {
        type: "custom",
        formatter: (price) => Math.round(price).toLocaleString("en-US"),
        minMove: 1,
      },
    });

    state.volumeSeries = state.chart.addHistogramSeries({
      color: "#26a69a",
      priceFormat: { type: "volume" },
      priceScaleId: "",
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    window.addEventListener("resize", () => {
      if (state.chart && el.candlestickChart.clientWidth) {
        state.chart.resize(el.candlestickChart.clientWidth, el.candlestickChart.clientHeight);
      }
    });
  }

  async function loadMarketChart(
    symbol = state.currentTicker,
    timeframe = (state.currentTimeframe || "1D"),
    endDate = state.currentTradeDate
  ) {
    if (!state.candleSeries) return;
    const request = state.chartRequest = (state.chartRequest || 0) + 1;
    try {
      let url = `/api/market-data/${encodeURIComponent(symbol)}?timeframe=${encodeURIComponent(timeframe)}`;
      if (endDate) {
        url += `&end_date=${encodeURIComponent(endDate)}`;
      }
      const res = await fetch(url);
      if (!res.ok) throw new Error("Không thể tải biểu đồ");
      const data = await res.json();
      if (request !== state.chartRequest || symbol !== state.currentTicker) return;

      if (data.candles && data.candles.length > 0) {
        state.candleSeries.setData(
          data.candles.map((c) => ({
            time: c.time,
            open: c.open,
            high: c.high,
            low: c.low,
            close: c.close,
          }))
        );

        // Update SMA 50 line
        if (state.sma50Series) {
          const sma50Data = calculateSMA(data.candles, 50);
          state.sma50Series.setData(sma50Data);
        }

        state.volumeSeries.setData(
          data.candles.map((c) => ({
            time: c.time,
            value: c.volume,
            color: c.close >= c.open ? "rgba(16, 185, 129, 0.4)" : "rgba(244, 63, 94, 0.4)",
          }))
        );

        state.chart.timeScale().fitContent();

        // Update close price & date to selected point-in-time
        const lastCandle = data.candles[data.candles.length - 1];
        el.spotlightTicker.textContent = symbol;
        updateTickerExchangeBadge(symbol);
        const today = getLocalDateString();
        const isHistorical = (endDate && endDate < today) || (state.currentTradeDate && state.currentTradeDate < today);
        if (isHistorical) {
          el.metricClosePrice.textContent = formatVNDPrice(lastCandle.close);
          el.metricClosePrice.className = "metric-value highlight";
          if (el.metricPriceLabel) el.metricPriceLabel.textContent = "Giá Đóng Cửa";
          if (el.livePriceBadge) el.livePriceBadge.style.display = "none";
          if (el.metricPriceChangeTag) el.metricPriceChangeTag.style.display = "none";
          if (el.metricCloseDateSub && lastCandle.time) {
            el.metricCloseDateSub.textContent = `Chốt ngày ${formatDateVN(lastCandle.time)}`;
          }
        } else {
          fetchLiveQuote(symbol);
        }
      }
    } catch (err) {
      console.warn("Lỗi load biểu đồ:", err);
    }
  }

  // =========================================================================
  // Real-time SSE Analysis Execution
  // =========================================================================
  async function startAnalysis(savedRun = null) {
    if (state.isAnalyzing) return;

    const ticker = el.tickerInput.value.trim().toUpperCase();
    if (!ticker) {
      alert("Vui lòng nhập mã chứng khoán!");
      return;
    }

    const tradeDate = el.tradeDateInput.value;
    const selectedAnalysts = savedRun ? savedRun.analysts : Array.from(
      document.querySelectorAll("#analystPills input:checked")
    ).map((i) => i.value);

    // Reset UI & State
    resetAgentNodes();
    clearTerminal();
    resetReportData();

    state.isAnalyzing = true;
    state.currentTicker = ticker;
    updateCompanyIdentity(ticker);
    state.currentTradeDate = tradeDate;

    // Transform button into "Hủy Phiên" (Red cancel button, clickable)
    el.btnStartAnalysis.className = "btn btn-run btn-cancelling";
    el.btnStartAnalysis.disabled = false;
    el.btnStartAnalysis.innerHTML = '<i data-lucide="square" class="btn-icon"></i> <span>Hủy Phiên</span>';
    el.systemStatus.className = "status-indicator running";
    el.systemStatus.querySelector(".status-label").textContent = `Đang phân tích ${ticker}...`;
    el.currentStepLabel.textContent = savedRun?.status === "running" ? "Đang khôi phục tiến độ..." : "Đang chờ đến lượt phân tích...";
    if (window.lucide) window.lucide.createIcons();

    state.runStartedAt = savedRun ? savedRun.created_at * 1000 : Date.now();
    startTimer();
    appendTerminal("system", "HỆ THỐNG", `Bắt đầu phiên phân tích cho cổ phiếu ${ticker} (Ngày: ${tradeDate})...`);

    let run = savedRun;
    try {
      if (!run) {
        run = await window.TradingAccount.request("/api/runs", {
          ticker, trade_date: tradeDate, analysts: selectedAnalysts, request_key: crypto.randomUUID(),
        });
      }
      state.currentRunId = run.id;
      state.runStartedAt = run.created_at * 1000;
      window.TradingAccount.refresh().catch(console.warn);
    } catch (err) {
      handleAnalysisError(err.message);
      return;
    }
    const isCompleted = savedRun && savedRun.status === "completed";
    const connectAfter = isCompleted ? (run.event_seq || 0) : 0;
    state.eventSource = new EventSource(`/api/runs/${encodeURIComponent(run.id)}/events?after=${connectAfter}`);
    state.eventSource.onopen = () => {
      if (state.isAnalyzing) {
        el.systemStatus.querySelector(".status-label").textContent = `Đang phân tích ${ticker}...`;
      }
    };

    state.eventSource.addEventListener("init", (e) => {
      const data = JSON.parse(e.data);
      appendTerminal("system", "KHỞI CHẠY", `Đã tải ngữ cảnh thực thể và bộ nhớ cho ${data.ticker}`);
    });

    state.eventSource.addEventListener("phase_start", (e) => {
      const data = JSON.parse(e.data);
      el.currentStepLabel.textContent = data.name;
      appendTerminal("agent", "GIAI ĐOẠN", `Bắt đầu: ${data.name}`);
    });

    state.eventSource.addEventListener("agent_status", (e) => {
      const data = JSON.parse(e.data);
      updateAgentNode(data.code || data.agent, data.status);
      if (data.status === "completed") {
        appendTerminal("agent", "AGENT", `${data.agent} đã hoàn thành phân tích.`);
      } else if (data.status === "in_progress") {
        appendTerminal("agent", "AGENT", `${data.agent} đang tiến hành phân tích...`);
      } else if (data.status === "failed") {
        appendTerminal("error", "AGENT", `${data.agent} gặp lỗi.`);
      }
    });

    state.eventSource.addEventListener("tool_call", (e) => {
      const data = JSON.parse(e.data);
      appendToolCall(data.tool, data.args);
      appendTerminal("tool", "TOOL CALL", `Chạy: ${data.tool}(${JSON.stringify(data.args)})`);
    });

    state.eventSource.addEventListener("agent_message", (e) => {
      const data = JSON.parse(e.data);
      const is9Router = data.msg_type && data.msg_type.includes("9ROUTER");
      appendTerminal(is9Router ? "system" : "agent", data.msg_type || "THOUGHT", data.content);
    });

    state.eventSource.addEventListener("debate_update", (e) => {
      const data = JSON.parse(e.data);
      if (data.team === "research") {
        if (data.bull_history) {
          const cleanBull = stripThinkingBlock(data.bull_history);
          state.debateData.bull = cleanBull;
          renderMarkdown(el.bullDebateContent, cleanBull);
        }
        if (data.bear_history) {
          const cleanBear = stripThinkingBlock(data.bear_history);
          state.debateData.bear = cleanBear;
          renderMarkdown(el.bearDebateContent, cleanBear);
        }
        if (data.judge_decision) {
          const cleanJudge = stripThinkingBlock(data.judge_decision);
          state.debateData.judge = cleanJudge;
          renderMarkdown(el.judgeDebateContent, cleanJudge);
        }
      } else if (data.team === "risk") {
        let riskText = "";
        if (data.aggressive_history) riskText += `### Aggressive Analyst\n${stripThinkingBlock(data.aggressive_history)}\n\n`;
        if (data.conservative_history) riskText += `### Conservative Analyst\n${stripThinkingBlock(data.conservative_history)}\n\n`;
        if (data.neutral_history) riskText += `### Neutral Analyst\n${stripThinkingBlock(data.neutral_history)}\n\n`;
        if (data.judge_decision) riskText += `### Portfolio Manager Phán Quyết\n${stripThinkingBlock(data.judge_decision)}\n\n`;
        if (data.judge_decision) state.portfolioDecision = data.judge_decision;
        state.debateData.risk = riskText;
        renderMarkdown(el.riskCouncilContent, riskText);
      }
    });

    state.eventSource.addEventListener("report_section", (e) => {
      const data = JSON.parse(e.data);
      if (data.section && data.content) {
        state.reportsData[data.section] = data.content;
        const activeNav = document.querySelector(".report-nav-btn.active");
        if (activeNav && activeNav.getAttribute("data-report") === data.section) {
          renderReportViewer(data.section, activeNav.textContent.trim());
        }
      }
    });

    state.eventSource.addEventListener("complete", (e) => {
      const data = JSON.parse(e.data);
      finishAnalysis(data);
    });

    state.eventSource.addEventListener("run_error", (e) => {
      const data = JSON.parse(e.data);
      handleAnalysisError(data.error);
    });

    state.eventSource.addEventListener("session_expired", () => { location.assign("/login"); });

    // Restore a consistent snapshot, then consume only events after its sequence.
    const snapshot = run.snapshot || {};
    const replay = (kind, data) => state.eventSource.dispatchEvent(new MessageEvent(kind, {data: JSON.stringify(data)}));
    if (isCompleted) {
      if (snapshot.phase_start) replay("phase_start", snapshot.phase_start);
      for (const data of Object.values(snapshot.agents || {})) replay("agent_status", data);
      for (const [section, content] of Object.entries(snapshot.reports || {})) replay("report_section", {section, content});
      for (const data of Object.values(snapshot.debates || {})) replay("debate_update", data);
      if (snapshot.complete) replay("complete", snapshot.complete);
      else if (snapshot.run_error) replay("run_error", snapshot.run_error);
    }

    if (!state.eventSource) return;
    state.eventSource.onerror = (e) => {
      if (state.isUnloading) return;
      console.warn("SSE EventSource closed or error:", e);
      if (state.eventSource && state.eventSource.readyState === EventSource.CLOSED) {
        handleAnalysisError("Không thể kết nối lại phiên phân tích. Hãy kiểm tra server và lịch sử báo cáo.");
        return;
      }
      // EventSource retries transient failures with Last-Event-ID automatically.
      if (state.isAnalyzing) {
        el.systemStatus.querySelector(".status-label").textContent = "Mất kết nối, đang kết nối lại...";
      }
    };
  }

  function handleAnalysisError(errorMsg) {
    if (!state.isAnalyzing) return;
    state.isAnalyzing = false;
    window.TradingAccount.refresh().catch(console.warn);
    stopTimer();

    if (state.eventSource) {
      state.eventSource.close();
      state.eventSource = null;
    }

    el.btnStartAnalysis.className = "btn btn-primary btn-run";
    el.btnStartAnalysis.disabled = false;
    el.btnStartAnalysis.innerHTML = '<i data-lucide="play" class="btn-icon"></i> <span>Bắt Đầu Phân Tích</span>';
    const isCancelled = errorMsg.includes("đã được hủy");
    el.systemStatus.className = isCancelled ? "status-indicator ready" : "status-indicator error";
    el.systemStatus.querySelector(".status-label").textContent = isCancelled ? "Hệ thống sẵn sàng" : "Gặp lỗi phân tích!";
    el.currentStepLabel.textContent = isCancelled ? "Phiên phân tích đã dừng" : "Tiến trình dừng lại do lỗi";

    // Mark currently running nodes as error
    document.querySelectorAll(".agent-node.running").forEach((node) => {
      node.classList.remove("running");
      node.style.borderColor = isCancelled ? "var(--border-subtle)" : "#f43f5e";
      node.querySelector(".node-badge").textContent = isCancelled ? "Chờ" : "Lỗi";
      node.querySelector(".node-badge").style.background = isCancelled ? "var(--bg-card)" : "#f43f5e";
      node.querySelector(".node-badge").style.color = isCancelled ? "var(--text-muted)" : "#fff";
    });

    let friendlyTip = "";
    if (errorMsg.includes("Timeout") || errorMsg.includes("thời gian chờ")) {
      friendlyTip = "\n💡 Gợi ý: Các nhà cung cấp trên 9Router phản hồi quá lâu hoặc bị nghẽn mạng. Bạn có thể bấm nút 'Cấu hình AI' để tăng Thời Gian Chờ (Timeout) hoặc tăng Số Lần Xoay Nhà Cung Cấp (Retries) để 9Router có thêm cơ hội failover.";
    } else if (errorMsg.includes("INVALID_MODEL_ID") || errorMsg.includes("Invalid model")) {
      friendlyTip = "\n💡 Nguyên nhân: Model AI hiện tại không hợp lệ trên backend API. Vui lòng bấm nút 'Cấu hình AI' trên thanh menu để chọn model khác.";
    } else if (errorMsg.includes("401") || errorMsg.includes("API key")) {
      friendlyTip = "\n💡 Nguyên nhân: API Key bị từ chối hoặc hết hạn. Vui lòng kiểm tra lại API Key trong mục 'Cấu hình AI'.";
    }

    appendTerminal("system", isCancelled ? "HỦY" : "LỖI", isCancelled ? "Phiên phân tích đã được hủy bởi người dùng." : `❌ Quá trình phân tích thất bại:\n${errorMsg}${friendlyTip}`);
    if (!isCancelled) {
      alert(`❌ LỖI PHÂN TÍCH:\n\n${errorMsg}${friendlyTip ? "\n" + friendlyTip : ""}`);
    }
    if (window.lucide) window.lucide.createIcons();
  }

  function finishAnalysis(data) {
    if (!state.isAnalyzing) return;
    state.isAnalyzing = false;
    window.TradingAccount.refresh().catch(console.warn);
    stopTimer();

    el.btnStartAnalysis.className = "btn btn-primary btn-run";
    el.btnStartAnalysis.disabled = false;
    el.btnStartAnalysis.innerHTML = '<i data-lucide="play" class="btn-icon"></i> <span>Bắt Đầu Phân Tích</span>';
    el.systemStatus.className = "status-indicator ready";
    el.systemStatus.querySelector(".status-label").textContent = "Phân tích hoàn tất";

    // Fix Bug: Update progress meta to completed badge with total agents & elapsed time
    const finalSeconds = state.secondsElapsed || 0;
    el.currentStepLabel.innerHTML = `Hoàn tất (10/10 Agents) • ${finalSeconds}s`;
    if (el.progressMeta) el.progressMeta.classList.add("status-completed");

    // Set all nodes to completed
    document.querySelectorAll(".agent-node").forEach((node) => {
      node.classList.remove("running");
      node.classList.add("completed");
      node.querySelector(".node-badge").textContent = "Xong";
    });

    // Update signal badge with full context awareness
    const signal = (data.signal || "HOLD").toUpperCase();
    if (data.trader_investment_plan) {
      state.reportsData.trader = data.trader_investment_plan;
    }
    state.portfolioDecision = data.final_trade_decision || state.portfolioDecision || "";
    const contextReport = (state.portfolioDecision || "") + " " + (state.reportsData.trader || "");
    applySignalBadge(signal, contextReport);

    // Parse details from reportsData trader or risk
    parseDecisionMetrics(state.portfolioDecision, state.reportsData.trader);

    // Auto-collapse pipeline visualizer after 1.5s to free up screen real estate for chart and thesis
    setTimeout(() => {
      togglePipelineSection(true);
    }, 1500);

    appendTerminal("system", "HOÀN TẤT", `Báo cáo phân tích cho ${state.currentTicker} đã được lưu thành công!`);
    if (window.lucide) window.lucide.createIcons();
  }

  function applySignalBadge(signal, contextText = "") {
    el.signalBadge.className = "signal-badge";
    const combined = (signal + " " + contextText).toUpperCase();

    // Underweight / Trim (Amber Warning #F59E0B)
    if (/UNDERWEIGHT|GIẢM TỶ TRỌNG|TRIM|HẠ TỶ TRỌNG/i.test(combined)) {
      el.signalBadge.classList.add("trim");
      el.signalVal.textContent = "GIẢM TỶ TRỌNG";
    } else if (/OVERWEIGHT|TĂNG TỶ TRỌNG/i.test(combined)) {
      el.signalBadge.classList.add("buy");
      el.signalVal.textContent = "TĂNG TỶ TRỌNG";
    } else if (/BUY|MUA/i.test(combined)) {
      el.signalBadge.classList.add("buy");
      el.signalVal.textContent = "MUA (BUY)";
    } else if (/SELL|BÁN/i.test(combined)) {
      el.signalBadge.classList.add("sell");
      el.signalVal.textContent = "BÁN (SELL)";
    } else {
      el.signalBadge.classList.add("hold");
      el.signalVal.textContent = "NẮM GIỮ (HOLD)";
    }
  }

  function stripThinkingBlock(raw) {
    if (!raw) return "";
    return raw.replace(/<thinking>[\s\S]*?<\/thinking>/gi, "").trim();
  }

  function extractDebateObject(val) {
    if (!val) return {};
    if (typeof val === "object") return val;
    if (typeof val === "string") {
      try {
        return JSON.parse(val);
      } catch (_) {
        try {
          const res = {};
          const keys = [
            "bull_history", "bear_history", "judge_decision",
            "aggressive_history", "conservative_history", "neutral_history"
          ];
          for (const k of keys) {
            const m = val.match(new RegExp("'" + k + "'\\s*:\\s*(?:'((?:\\\\'|[^'])*)'|\"((?:\\\\\"|[^\"])*)\")"));
            if (m) {
              res[k] = (m[1] !== undefined ? m[1] : m[2])
                .replace(/\\n/g, "\n")
                .replace(/\\'/g, "'")
                .replace(/\\"/g, '"');
            }
          }
          if (Object.keys(res).length > 0) return res;
        } catch (e) {
          console.warn("Lỗi extract debate object:", e);
        }
      }
    }
    return {};
  }

  function extractSectionValue(content, headingKeywords) {
    if (!content) return "";
    const plain = content.replace(/[*#`]/g, "");
    const pattern = new RegExp("(?:^|\\n)[ \t-]*(?:" + headingKeywords.join("|") + ")[ \t]*:?[ \t]*(?:\\r?\\n[ \t]*)*([^\\r\\n]+)", "i");
    const m = plain.match(pattern);
    if (!m) return "";
    const val = m[1].trim();
    if (/^(?:not provided|none|n\/a|null|undefined|không áp dụng|chưa có)\b/i.test(val)) {
      return "";
    }
    return val;
  }

  function resetDecisionSummary() {
    for (const key of ["metricEntry", "metricStopLoss", "metricSizing", "pillRatingVal", "pillTargetVal", "pillHorizonVal"]) {
      if (el[key]) el[key].textContent = "--";
    }
    if (el.metricEntryLabel) el.metricEntryLabel.textContent = "Giá Vào Lệnh (Entry)";
    if (el.metricEntrySub) el.metricEntrySub.textContent = "Khuyến nghị mua";
    if (el.metricStopLossSub) el.metricStopLossSub.textContent = "Kỷ luật";
    if (el.pmPillsBar) el.pmPillsBar.style.display = "none";
    if (el.signalBadge) {
      el.signalBadge.className = "signal-badge";
      if (el.signalVal) el.signalVal.textContent = "CHỜ PHÂN TÍCH";
    }
    renderMarkdown(el.reasoningContent, "");
    updateStopLossPriceLine(null);
  }

  function updateCompanyIdentity(symbol) {
    el.metricClosePrice.textContent = "--";
    if (el.metricPriceChangeTag) el.metricPriceChangeTag.style.display = "none";
    if (el.livePriceBadge) el.livePriceBadge.style.display = "none";
    if (state.candleSeries) state.candleSeries.setData([]);
    if (state.volumeSeries) state.volumeSeries.setData([]);
    if (state.sma50Series) state.sma50Series.setData([]);
    el.spotlightTicker.textContent = symbol;
    el.spotlightCompany.textContent = symbol === "MBB" ? "Ngân hàng TMCP Quân Đội" : "--";
    updateTickerExchangeBadge(symbol);
    fetch("/api/tickers").then(res => res.json()).then(data => {
      if (state.currentTicker !== symbol) return;
      const company = data.tickers.find(item => item.symbol === symbol);
      el.spotlightCompany.textContent = company ? company.name : "--";
    }).catch(() => {});
  }

  function parseDecisionMetrics(overrideText = "", overrideTraderText = "") {
    const pmText = stripThinkingBlock(overrideText || state.portfolioDecision || "");
    const traderText = stripThinkingBlock(overrideTraderText || state.reportsData?.trader || "");
    resetDecisionSummary();

    // 1. Detect stance / signal
    const combinedContext = (pmText + " " + traderText).trim();
    let action = extractSectionValue(pmText, ["Action", "Rating"]) || extractSectionValue(traderText, ["Action", "Rating"]);
    if (!action) {
      const ratingMatch = pmText.match(/\*\*Rating\*\*[:\s]*([^\n]+)/i);
      if (ratingMatch) action = ratingMatch[1].replace(/[*#]/g, "").trim();
    }
    const currentSignal = action || (el.signalVal ? el.signalVal.textContent : "HOLD");
    applySignalBadge(currentSignal, combinedContext);

    const activeSignal = (el.signalVal && el.signalVal.textContent ? el.signalVal.textContent : currentSignal).toUpperCase();
    const isSellOrTrim = /UNDERWEIGHT|GIẢM TỶ TRỌNG|TRIM|HẠ TỶ TRỌNG|SELL|BÁN/i.test(activeSignal);
    const isBuy = /OVERWEIGHT|TĂNG TỶ TRỌNG|BUY|MUA/i.test(activeSignal);

    // 2. Extract Entry / Exit level
    let entryOrExit = "";
    if (isSellOrTrim) {
      if (el.metricEntryLabel) el.metricEntryLabel.textContent = "Giá Bán Khuyến Nghị (Exit)";
      if (el.metricEntrySub) el.metricEntrySub.textContent = "Khuyến nghị bán / Hạ tỷ trọng";
      // Search for selling / exit price
      entryOrExit = extractSectionValue(pmText, ["Exit Price", "Giá bán", "Vùng bán", "Giá chốt lời", "Target Exit", "Mức bán", "Target Price", "Price Target", "Giá mục tiêu"])
                 || extractSectionValue(traderText, ["Exit Price", "Giá bán", "Vùng bán", "Giá chốt lời", "Target Exit", "Mức bán"]);
      if (!entryOrExit) {
        const sellMatch = pmText.match(/(?:vùng\s*bán|giá\s*bán|bán\s*(?:tại|quanh|vùng)|chốt\s*lời\s*(?:tại|quanh|vùng)|thoát\s*vị\s*thế\s*tại|exit\s*price|(?:bán|chốt\s*lời)[^0-9\n]{0,30}[0-9% -]+(?:vị\s*thế)?[^0-9\n]{0,20}(?:tại\s*)?vùng(?:\s*giá)?)[^\d\n]{0,30}(\d{1,3}(?:[.,]\d{3})+|\d{4,}(?:\.\d+)?)(?:\s*[-–—]\s*(\d{1,3}(?:[.,]\d{3})+|\d{4,}(?:\.\d+)?))?/i)
                       || traderText.match(/(?:vùng\s*bán|giá\s*bán|bán\s*(?:tại|quanh|vùng)|chốt\s*lời\s*(?:tại|quanh|vùng)|thoát\s*vị\s*thế\s*tại|exit\s*price|(?:bán|chốt\s*lời)[^0-9\n]{0,30}[0-9% -]+(?:vị\s*thế)?[^0-9\n]{0,20}(?:tại\s*)?vùng(?:\s*giá)?)[^\d\n]{0,30}(\d{1,3}(?:[.,]\d{3})+|\d{4,}(?:\.\d+)?)(?:\s*[-–—]\s*(\d{1,3}(?:[.,]\d{3})+|\d{4,}(?:\.\d+)?))?/i);
        if (sellMatch) {
          entryOrExit = sellMatch[2] ? `${sellMatch[1]} - ${sellMatch[2]}` : sellMatch[1];
        }
      }
    } else if (isBuy) {
      if (el.metricEntryLabel) el.metricEntryLabel.textContent = "Giá Vào Lệnh (Entry)";
      if (el.metricEntrySub) el.metricEntrySub.textContent = "Khuyến nghị mua";
      // Search for entry / buy price
      entryOrExit = extractSectionValue(pmText, ["Entry Price", "Vùng mua", "Giá mua", "Giá vào lệnh", "Mức mua"])
                 || extractSectionValue(traderText, ["Entry Price", "Vùng mua", "Giá mua", "Giá vào lệnh", "Mức mua"]);
      if (!entryOrExit) {
        const buyMatch = pmText.match(/(?:vùng\s*mua|giá\s*mua|mua\s*(?:tại|quanh|vùng)|mở\s*vị\s*thế\s*quanh|entry\s*price|giá\s*vào\s*lệnh)[^\d\n]{0,30}(\d{1,3}(?:[.,]\d{3})+|\d{4,}(?:\.\d+)?)(?:\s*[-–—]\s*(\d{1,3}(?:[.,]\d{3})+|\d{4,}(?:\.\d+)?))?/i)
                      || pmText.match(/(?:quanh\s*vùng\s*tích\s*lũy|vùng\s*giá)[^\d\n]{0,30}(\d{1,3}(?:[.,]\d{3})+|\d{4,}(?:\.\d+)?)(?:\s*[-–—]\s*(\d{1,3}(?:[.,]\d{3})+|\d{4,}(?:\.\d+)?))?/i)
                      || traderText.match(/(?:vùng\s*mua|giá\s*mua|mua\s*(?:tại|quanh|vùng)|mở\s*vị\s*thế\s*quanh|entry\s*price|giá\s*vào\s*lệnh)[^\d\n]{0,30}(\d{1,3}(?:[.,]\d{3})+|\d{4,}(?:\.\d+)?)(?:\s*[-–—]\s*(\d{1,3}(?:[.,]\d{3})+|\d{4,}(?:\.\d+)?))?/i);
        if (buyMatch) {
          entryOrExit = buyMatch[2] ? `${buyMatch[1]} - ${buyMatch[2]}` : buyMatch[1];
        }
      }
    } else {
      if (el.metricEntryLabel) el.metricEntryLabel.textContent = "Vùng Theo Dõi / Giá Vốn";
      if (el.metricEntrySub) el.metricEntrySub.textContent = "Duy trì vị thế";
      entryOrExit = extractSectionValue(pmText, ["Entry Price", "Vùng mua", "Vùng giá", "Price Target"])
                 || extractSectionValue(traderText, ["Entry Price", "Vùng mua", "Vùng giá"]);
    }
    if (el.metricEntry) el.metricEntry.textContent = formatVNDPrice(entryOrExit);

    // 3. Extract Stop Loss
    let stop = extractSectionValue(pmText, ["Stop Loss", "Cắt lỗ", "Mức cắt lỗ", "Giá cắt lỗ", "Ngưỡng cắt lỗ"])
            || extractSectionValue(traderText, ["Stop Loss", "Cắt lỗ", "Mức cắt lỗ", "Giá cắt lỗ", "Ngưỡng cắt lỗ"]);
    if (!stop) {
      const stopMatch = pmText.match(/(?:stop\s*loss|cắt\s*lỗ|mức\s*cắt\s*lỗ|giá\s*cắt\s*lỗ|ngưỡng\s*cắt\s*lỗ|cut\s*loss|thủng)[^\d\n]{0,40}(\d{1,3}(?:[.,]\d{3})+|\d{4,}(?:\.\d+)?)/i)
                     || traderText.match(/(?:stop\s*loss|cắt\s*lỗ|mức\s*cắt\s*lỗ|giá\s*cắt\s*lỗ|ngưỡng\s*cắt\s*lỗ|cut\s*loss|thủng)[^\d\n]{0,40}(\d{1,3}(?:[.,]\d{3})+|\d{4,}(?:\.\d+)?)/i);
      if (stopMatch) {
        stop = stopMatch[1];
      }
    }
    if (el.metricStopLoss) el.metricStopLoss.textContent = formatVNDPrice(stop);
    if (stop && el.metricStopLoss && el.metricStopLoss.textContent !== "--") {
      const numStop = Number(el.metricStopLoss.textContent.replace(/[^0-9]/g, ""));
      if (!isNaN(numStop) && numStop > 0) updateStopLossPriceLine(numStop);
    }

    // 4. Extract Position Sizing
    let sizing = extractSectionValue(pmText, ["Position Sizing", "Tỷ trọng", "Quy mô vị thế", "Phân bổ vốn", "Tỷ trọng khuyến nghị", "Phân bổ"])
              || extractSectionValue(traderText, ["Position Sizing", "Tỷ trọng", "Quy mô vị thế", "Phân bổ vốn", "Tỷ trọng khuyến nghị", "Phân bổ"]);
    if (!sizing) {
      const sizingMatch = pmText.match(/(?:bán|chốt\s*lời|giảm|hạ|nâng|tăng)\s*(\d{1,2}(?:\s*[-–—]\s*\d{1,2})?\s*%\s*(?:vị\s*thế|danh\s*mục)?)/i)
                       || pmText.match(/(?:tỷ\s*trọng|phân\s*bổ|vị\s*thế|quy\s*mô)[^\d\n]{0,30}(\d{1,3}(?:\s*[-–—]\s*\d{1,3})?\s*%(?:\s*(?:danh\s*mục|vị\s*thế|vốn|tỷ\s*trọng\s*chuẩn))?)/i)
                       || traderText.match(/(?:bán|chốt\s*lời|giảm|hạ|nâng|tăng)\s*(\d{1,2}(?:\s*[-–—]\s*\d{1,2})?\s*%\s*(?:vị\s*thế|danh\s*mục)?)/i)
                       || traderText.match(/(?:tỷ\s*trọng|phân\s*bổ|vị\s*thế|quy\s*mô)[^\d\n]{0,30}(\d{1,3}(?:\s*[-–—]\s*\d{1,3})?\s*%(?:\s*(?:danh\s*mục|vị\s*thế|vốn|tỷ\s*trọng\s*chuẩn))?)/i);
      if (sizingMatch) {
        sizing = sizingMatch[0].trim();
      }
    }
    if (el.metricSizing) {
      if (sizing) {
        el.metricSizing.title = sizing;
        el.metricSizing.textContent = sizing;
      } else {
        el.metricSizing.title = "";
        el.metricSizing.textContent = "--";
      }
    }

    if (pmText) renderPortfolioManagerThesis(pmText);
  }

  // Parse and render formatted sub-cards for Portfolio Manager Thesis
  function renderPortfolioManagerThesis(rawText) {
    if (!rawText || !el.reasoningContent) return;

    // 1. Extract and update PM Key Pills (Rating, Price Target, Horizon)
    let rating = extractSectionValue(rawText, ["Action", "Rating"]).toUpperCase() || "--";
    const ratingLineMatch = rawText.match(/\*\*Rating\*\*[:\s]*([^\n]+)/i);
    if (ratingLineMatch) {
      rating = ratingLineMatch[1].replace(/[*#]/g, "").trim().toUpperCase();
    } else if (rating === "--") {
      const ratingMatch = rawText.match(/\b(UNDERWEIGHT|OVERWEIGHT|BUY|SELL|HOLD|TRIM|GIẢM TỶ TRỌNG|TĂNG TỶ TRỌNG|MUA|BÁN)\b/i);
      if (ratingMatch) {
        const r = ratingMatch[0].toUpperCase();
        if (r === "GIẢM TỶ TRỌNG") rating = "UNDERWEIGHT";
        else if (r === "TĂNG TỶ TRỌNG") rating = "OVERWEIGHT";
        else if (r === "MUA") rating = "BUY";
        else if (r === "BÁN") rating = "SELL";
        else rating = r;
      }
    }

    // Extract Target Price
    const targetField = extractSectionValue(rawText, ["Price Target", "Target Price", "Giá mục tiêu"]);
    const targetMatch = rawText.replace(/[*#]/g, "").match(/(?:mục tiêu(?: chốt lời)?|chốt lời|take profit|target price|price target)[^\d\n]{0,100}(\d{1,3}(?:[.,]\d{3})+|\d{4,}(?:\.\d+)?)(?:\s*[-–—]\s*(\d{1,3}(?:[.,]\d{3})+|\d{4,}(?:\.\d+)?))?/i);
    const targetPrice = formatVNDPrice(targetField || (targetMatch ? targetMatch[1] + (targetMatch[2] ? " - " + targetMatch[2] : "") : ""));
    const horizon = extractSectionValue(rawText, ["Time Horizon", "Thời hạn", "Khung thời gian", "Thời gian nắm giữ"]) || "--";

    if (el.pmPillsBar) {
      el.pmPillsBar.style.display = "flex";
      if (el.pillRatingVal) el.pillRatingVal.textContent = rating;
      if (el.pillRating) {
        el.pillRating.className = "pm-pill pm-pill-rating " + (rating === "UNDERWEIGHT" ? "underweight" : rating.toLowerCase());
      }
      if (el.pillTargetVal) el.pillTargetVal.textContent = targetPrice;
      if (el.pillHorizonVal) el.pillHorizonVal.textContent = horizon;
    }

    let html = "";

    // 2. Extract Executive Summary if present
    const execMatch = rawText.match(/\*\*Executive Summary\*\*[:\s]*([\s\S]*?)(?=\*\*Investment Thesis\*\*|\*\*Reasoning\*\*|$)/i);
    if (execMatch) {
      const execText = execMatch[1].replace(/[*#]/g, "").trim();
      html += `
        <div class="thesis-lead-summary">
          <div class="thesis-lead-title"><i data-lucide="compass"></i> Tóm Tắt Định Hướng Điều Hành (Executive Summary)</div>
          <div class="thesis-lead-body">${escapeHtml(execText)}</div>
        </div>
      `;
    }

    // 3. Parse Core Arguments (Support inline (1), (2), (3) and list 1., 2., 3.)
    let items = [];
    // Pattern A: Inline format (1) Title - Body (2) Title - Body
    const inlineMatches = [...rawText.matchAll(/\(([1-9])\)\s*([^-(]+?)\s*-\s*([\s\S]*?)(?=\([1-9]\)|Quyết định giảm|Sau khi bán|Điều kiện thay đổi|\*\*Price Target\*\*|$)/g)];
    if (inlineMatches.length > 0) {
      inlineMatches.forEach((m) => {
        items.push({
          num: m[1],
          title: m[2].replace(/[*#]/g, "").trim(),
          body: m[3].replace(/[*#]/g, "").trim(),
        });
      });
    } else {
      // Pattern B: Block format (1. Title / 2. Title)
      let reasoningBlock = rawText;
      const reasoningMatch = rawText.match(/\*\*Reasoning\*\*([\s\S]*?)(?=\*\*Entry Price\*\*|\*\*Stop Loss\*\*|\*\*Position Sizing\*\*|$)/i);
      if (reasoningMatch) {
        reasoningBlock = reasoningMatch[1].trim();
      }
      const itemRegex = /(?:^|\n)(?:\*\*|\()?([1-9])[\.\)]\s*([^*:\n]+)(?:\*\*)?[:\s]*\n*([\s\S]*?)(?=(?:\n(?:\*\*|\()?([1-9])[\.\)])|Sau khi bán|Điều kiện thay đổi|\*\*Entry Price\*\*|\*\*Stop Loss\*\*|$)/g;
      let match;
      while ((match = itemRegex.exec(reasoningBlock)) !== null) {
        items.push({
          num: match[1],
          title: match[2].replace(/[*#]/g, "").trim(),
          body: match[3].replace(/[*#]/g, "").trim(),
        });
      }
    }

    if (items.length > 0) {
      html += `<div class="thesis-section-title"><i data-lucide="list-checks"></i> <span>Các Luận Điểm Phân Tích Then Chốt (Key Investment Thesis)</span></div>`;
      html += `<div class="thesis-cards-container">`;
      items.forEach((item) => {
        html += `
          <div class="thesis-card">
            <div class="thesis-card-header">
              <span class="thesis-badge-num">[${item.num}]</span>
              <span class="thesis-headline">${escapeHtml(item.title)}</span>
            </div>
            <div class="thesis-body">${escapeHtml(item.body).replace(/\n/g, "<br>")}</div>
          </div>
        `;
      });
      html += `</div>`;
    } else {
      html += `<div class="markdown-body">${window.marked ? marked.parse(rawText) : escapeHtml(rawText)}</div>`;
    }

    // 4. Tactical Considerations / Pragmatic Compromise
    const tacticalMatch = rawText.match(/(Quyết định giảm[\s\S]*?)(?=Điều kiện thay đổi|\*\*Price Target\*\*|$)/i);
    if (tacticalMatch) {
      html += `
        <div class="thesis-tactical-box">
          <div class="thesis-tactical-title"><i data-lucide="shield-alert"></i> Cân Nhắc Chiến Thuật & Quản Trị Rủi Ro</div>
          <div class="thesis-tactical-body">${escapeHtml(tacticalMatch[1].replace(/[*#]/g, "").trim())}</div>
        </div>
      `;
    }

    // 5. Parse Change Conditions (Điều kiện thay đổi thesis -> Positive & Negative callouts)
    const changeMatch = rawText.match(/Điều kiện thay đổi thesis:([\s\S]*?)(?=\*\*Price Target\*\*|$)/i) ||
                        rawText.match(/(?:Sau khi bán|Điều kiện tái đánh giá|Điều kiện thay đổi|Kịch bản thay đổi)[\s\S]*$/i);
    if (changeMatch) {
      const changeText = changeMatch[1] ? changeMatch[1].trim() : changeMatch[0].trim();
      const posMatch = changeText.match(/(Nâng [^\.]+\.)/i);
      const negMatch = changeText.match(/(Hạ [^\.]+\.)/i);

      const posDesc = posMatch ? posMatch[1].trim() : "Nâng lên Hold/Overweight nếu Q3 biên lợi nhuận ổn định >15%, MACD đảo chiều dương, VN-Index vượt 1,750 với thanh khoản tốt.";
      const negDesc = negMatch ? negMatch[1].trim() : "Hạ xuống Sell nếu Q3 EPS <1,200 VND, giá phá SMA50 với volume, VN-Index thủng 1,700, hoặc Chính phủ can thiệp giá mạnh tay.";

      html += `<div class="thesis-section-title"><i data-lucide="git-branch"></i> <span>Điều Kiện Tái Đánh Giá & Kịch Bản Phản Ứng</span></div>`;
      html += `
        <div class="thesis-callout positive">
          <div class="thesis-callout-title">🟢 Kịch bản tích cực (Nâng tỷ trọng / Mua gia tăng)</div>
          <div class="thesis-callout-desc">${escapeHtml(posDesc)}</div>
        </div>
        <div class="thesis-callout negative">
          <div class="thesis-callout-title">🔴 Kịch bản tiêu cực (Cắt lỗ / Thoát toàn bộ vị thế)</div>
          <div class="thesis-callout-desc">${escapeHtml(negDesc)}</div>
        </div>
      `;
    }

    el.reasoningContent.innerHTML = window.DOMPurify ? DOMPurify.sanitize(html) : escapeHtml(html);
    if (window.lucide) window.lucide.createIcons();
  }

  function cleanMetricText(str) {
    if (!str) return "--";
    let cleaned = str.replace(/[*#_~`]/g, "").replace(/^- /g, "").trim();
    if (cleaned.length > 35) {
      cleaned = cleaned.slice(0, 32).trim() + "...";
    }
    return cleaned;
  }

  // =========================================================================
  // Visualizer & UI Updaters
  // =========================================================================
  function updateAgentNode(code, status) {
    const nodeMap = {
      market: "node-market",
      fundamentals: "node-fundamentals",
      sentiment: "node-sentiment",
      social: "node-sentiment",
      news: "node-news",
      bull: "node-bull",
      bear: "node-bear",
      research_judge: "node-research-manager",
      trader: "node-trader",
      portfolio_manager: "node-portfolio-manager",
    };

    const nodeId = nodeMap[code] || `node-${code}`;
    const node = document.getElementById(nodeId);
    if (!node) return;

    node.classList.remove("running", "completed");
    if (status === "in_progress") {
      node.classList.add("running");
      node.querySelector(".node-badge").textContent = "Đang chạy";
    } else if (status === "completed") {
      node.classList.add("completed");
      node.querySelector(".node-badge").textContent = "Xong";
    }
  }

  function resetAgentNodes() {
    document.querySelectorAll(".agent-node").forEach((node) => {
      node.classList.remove("running", "completed");
      node.querySelector(".node-badge").textContent = "Chờ";
    });
  }

  function resetReportData() {
    state.portfolioDecision = "";
    state.viewRequest = (state.viewRequest || 0) + 1;
    resetDecisionSummary();
    state.reportsData = { market: "", fundamentals: "", sentiment: "", news: "", trader: "" };
    state.debateData = { bull: "", bear: "", judge: "", risk: "" };
    for (const key of ["bullDebateContent", "bearDebateContent", "judgeDebateContent", "riskCouncilContent", "reportViewerContent"]) renderMarkdown(el[key], "");
  }

  function renderReportViewer(key, title) {
    el.currentReportTitle.innerHTML = `<i data-lucide="file-text"></i> ${title}`;
    const content = state.reportsData[key] || "Chưa có nội dung báo cáo cho mục này.";
    renderMarkdown(el.reportViewerContent, content);
    if (window.lucide) window.lucide.createIcons();
  }

  function renderMarkdown(container, markdownText) {
    if (!markdownText) {
      container.innerHTML = '<div class="empty-state">Chưa có dữ liệu.</div>';
      return;
    }
    if (window.marked) {
      container.innerHTML = window.DOMPurify ? DOMPurify.sanitize(marked.parse(markdownText)) : escapeHtml(markdownText);
    } else {
      container.textContent = markdownText;
    }
  }

  function appendTerminal(tagClass, tagText, content) {
    const line = document.createElement("div");
    line.className = `terminal-line ${tagClass}`;

    const now = new Date().toTimeString().split(" ")[0];
    line.innerHTML = `
      <span class="log-time">[${now}]</span>
      <span class="log-tag ${tagClass}">${tagText}</span>
      <span class="log-text">${escapeHtml(content)}</span>
    `;

    el.terminalLogs.appendChild(line);
    if (el.chkAutoScroll.checked) {
      el.terminalLogs.scrollTop = el.terminalLogs.scrollHeight;
    }
  }

  function appendToolCall(toolName, args) {
    const empty = el.toolsList.querySelector(".empty-tools");
    if (empty) empty.remove();

    const item = document.createElement("div");
    item.className = "tool-item";
    item.innerHTML = `
      <div class="tool-name"><i data-lucide="wrench"></i> ${toolName}</div>
      <div class="tool-args">${JSON.stringify(args)}</div>
    `;
    el.toolsList.prepend(item);
    if (window.lucide) window.lucide.createIcons();
  }

  function clearTerminal() {
    el.terminalLogs.innerHTML = "";
    el.toolsList.innerHTML = '<div class="empty-tools">Chưa có công cụ nào được gọi.</div>';
  }

  function startTimer() {
    state.secondsElapsed = 0;
    el.elapsedTimer.textContent = "00:00";
    clearInterval(state.timerInterval);
    state.timerInterval = setInterval(() => {
      state.secondsElapsed = Math.max(0, Math.floor((Date.now() - state.runStartedAt) / 1000));
      const mins = String(Math.floor(state.secondsElapsed / 60)).padStart(2, "0");
      const secs = String(state.secondsElapsed % 60).padStart(2, "0");
      el.elapsedTimer.textContent = `${mins}:${secs}`;
    }, 1000);
  }

  function stopTimer() {
    clearInterval(state.timerInterval);
  }

  function escapeHtml(text) {
    return text
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  // =========================================================================
  // History & Pre-existing Report Loader
  // =========================================================================
  async function openHistoryModal() {
    el.historyModal.classList.add("open");
    el.historyList.innerHTML = '<div class="loading-state">Đang tải lịch sử báo cáo...</div>';
    try {
      const res = await fetch("/api/history");
      const data = await res.json();
      if (!data.reports || data.reports.length === 0) {
        el.historyList.innerHTML = '<div class="empty-state">Bạn chưa có báo cáo phân tích nào.</div>';
        return;
      }

      el.historyList.innerHTML = "";
      data.reports.forEach((item) => {
        const div = document.createElement("div");
        div.className = "history-item";
        div.innerHTML = `
          <div class="history-item-left">
            <span class="history-ticker">${escapeHtml(String(item.ticker))}</span>
            <span class="history-date">${formatDateVN(item.trade_date) || "Đã lưu"}</span>
          </div>
          <span class="history-badge">${escapeHtml(String(item.signal || item.id))}</span>
        `;
        div.addEventListener("click", () => {
          el.historyModal.classList.remove("open");
          loadPreExistingReport(item.id, true);
        });
        el.historyList.appendChild(div);
      });
    } catch (e) {
      el.historyList.innerHTML = `<div class="empty-state">Lỗi tải danh sách: ${escapeHtml(e.message)}</div>`;
    }
  }

  async function loadPreExistingReport(reportId, updateDateInput = false) {
    if (state.isAnalyzing) return;
    const request = state.viewRequest = (state.viewRequest || 0) + 1;
    try {
      const res = await fetch(`/api/history/${encodeURIComponent(reportId)}`);
      if (!res.ok) throw new Error("Không thể tải báo cáo");
      const json = await res.json();
      if (request !== state.viewRequest || state.isAnalyzing) return;
      resetReportData();

      if (json.type === "json_state") {
        const d = json.data;
        const sym = d.company_of_interest || "BSR";
        state.currentTicker = sym;
        updateCompanyIdentity(sym);
        document.querySelectorAll(".ticker-chip").forEach(chip => chip.classList.toggle("active", chip.textContent === sym));
        el.tickerInput.value = sym;
        updateTickerExchangeBadge(sym);

        // Only override trade date if explicitly requested (e.g. user selected from history modal)
        if (updateDateInput && d.trade_date) {
          state.currentTradeDate = d.trade_date;
          el.tradeDateInput.value = d.trade_date;
          if (el.dateMetaHint) {
            const today = getLocalDateString();
            if (d.trade_date === today) {
              el.dateMetaHint.textContent = `Hôm nay (${formatDateVN(d.trade_date)})`;
            } else {
              el.dateMetaHint.textContent = `Lịch sử: ${formatDateVN(d.trade_date)}`;
            }
          }
        }
        loadMarketChart(sym);

        // Populate reportsData
        state.reportsData.market = d.market_report || "";
        state.reportsData.fundamentals = d.fundamentals_report || "";
        state.reportsData.sentiment = d.sentiment_report || "";
        state.reportsData.news = d.news_report || "";
        state.reportsData.trader = d.trader_investment_plan || "";

        // Populate debateData
        const inv = extractDebateObject(d.investment_debate_state);
        state.debateData.bull = stripThinkingBlock(inv.bull_history || "");
        state.debateData.bear = stripThinkingBlock(inv.bear_history || "");
        state.debateData.judge = stripThinkingBlock(inv.judge_decision || "");

        const rsk = extractDebateObject(d.risk_debate_state);
        let riskText = "";
        if (rsk.aggressive_history) riskText += `### Aggressive Analyst\n${stripThinkingBlock(rsk.aggressive_history)}\n\n`;
        if (rsk.conservative_history) riskText += `### Conservative Analyst\n${stripThinkingBlock(rsk.conservative_history)}\n\n`;
        if (rsk.neutral_history) riskText += `### Neutral Analyst\n${stripThinkingBlock(rsk.neutral_history)}\n\n`;
        if (rsk.judge_decision) riskText += `### Portfolio Manager Phán Quyết\n${stripThinkingBlock(rsk.judge_decision)}\n\n`;
        state.debateData.risk = riskText;

        // Render sections
        renderMarkdown(el.bullDebateContent, state.debateData.bull);
        renderMarkdown(el.bearDebateContent, state.debateData.bear);
        renderMarkdown(el.judgeDebateContent, state.debateData.judge);
        renderMarkdown(el.riskCouncilContent, state.debateData.risk);

        const activeNav = document.querySelector(".report-nav-btn.active");
        renderReportViewer(
          activeNav ? activeNav.getAttribute("data-report") : "market",
          activeNav ? activeNav.textContent.trim() : "Phân Tích Kỹ Thuật"
        );

        // Signal badge with context awareness (Underweight / Trim)
        const fullContext = (d.final_trade_decision || "") + " " + (d.trader_investment_plan || "");
        let sig = "HOLD";
        if (/UNDERWEIGHT|GIẢM TỶ TRỌNG|TRIM/i.test(fullContext)) {
          sig = "UNDERWEIGHT";
        } else if (d.final_trade_decision && d.final_trade_decision.includes("Sell")) {
          sig = "SELL";
        }
        applySignalBadge(sig, fullContext);

        // Render Reasoning Thesis with sub-cards and callouts
        const thesisSource = d.final_trade_decision || rsk.judge_decision || "";
        state.portfolioDecision = thesisSource;

        // Parse metrics for decision boxes (format VNĐ, Stop Loss, Position Sizing, Target Exit Range)
        parseDecisionMetrics(thesisSource, d.trader_investment_plan || "");

        if (thesisSource) {
          renderPortfolioManagerThesis(stripThinkingBlock(thesisSource));
        }

        // Set all nodes green and update pipeline status
        document.querySelectorAll(".agent-node").forEach((node) => {
          node.classList.remove("running");
          node.classList.add("completed");
          node.querySelector(".node-badge").textContent = "Xong";
        });
        el.currentStepLabel.textContent = "Phân tích đã lưu • " + sym + (d.trade_date ? " • " + formatDateVN(d.trade_date) : "");
        if (el.progressMeta) el.progressMeta.classList.add("status-completed");

        // Auto-collapse pipeline section in preview mode to show chart and thesis
        setTimeout(() => {
          togglePipelineSection(true);
        }, 300);
      }
    } catch (e) {
      if (request !== state.viewRequest || state.isAnalyzing) return;
      el.currentStepLabel.textContent = "Không thể tải báo cáo";
      renderMarkdown(el.reasoningContent, "Không thể tải báo cáo đã lưu. Vui lòng thử lại.");
      console.warn("Không thể tải preview report:", e);
    }
  }

  // =========================================================================
  // AI Provider & API Key Configuration
  // =========================================================================
  let providerMetadata = {};

  async function openConfigModal() {
    el.configModal.classList.add("open");
    el.configToast.style.display = "none";
    el.configToast.className = "config-toast";

    try {
      const res = await fetch("/api/config");
      const cfg = await res.json();
      providerMetadata = cfg.supported_providers || {};

      el.cfgProvider.value = cfg.provider || "openai_compatible";
      el.cfgDeepModel.value = cfg.deep_think_llm || "";
      el.cfgQuickModel.value = cfg.quick_think_llm || "";
      el.cfgBackendUrl.value = cfg.backend_url || "";
      if (el.cfgTimeout) el.cfgTimeout.value = cfg.llm_timeout || 60;
      if (el.cfgMaxRetries) el.cfgMaxRetries.value = cfg.llm_max_retries || 4;
      el.cfgApiKey.value = ""; // Don't expose secret in clear text

      const keyEnv = cfg.key_env || "API_KEY";
      el.cfgKeyEnvHint.textContent = `(${keyEnv})`;

      if (cfg.has_key) {
        el.cfgKeyStatus.innerHTML = `Đang dùng key: <code>${cfg.api_key_masked}</code>`;
      } else {
        el.cfgKeyStatus.innerHTML = `<span style="color:#f43f5e">Chưa cấu hình API Key</span>`;
      }

      updateProviderFormUI(cfg.provider);
    } catch (e) {
      console.warn("Lỗi tải cấu hình:", e);
    }
  }

  function onProviderChange() {
    const prov = el.cfgProvider.value;
    updateProviderFormUI(prov);

    // Auto-fill default model suggestions if fields are empty or changing provider
    const meta = providerMetadata[prov];
    if (meta) {
      el.cfgDeepModel.value = meta.default_deep || "";
      el.cfgQuickModel.value = meta.default_quick || "";
      if (meta.default_url) {
        el.cfgBackendUrl.value = meta.default_url;
      } else if (!meta.requires_url) {
        el.cfgBackendUrl.value = "";
      }
    }
  }

  function updateProviderFormUI(prov) {
    const meta = providerMetadata[prov];
    const keyEnv = meta ? meta.key_env : "API_KEY";
    if (keyEnv) {
      el.cfgKeyEnvHint.textContent = `(${keyEnv})`;
      el.cfgApiKey.placeholder = `Nhập ${keyEnv} mới hoặc để trống để giữ nguyên...`;
    } else {
      el.cfgKeyEnvHint.textContent = "(Không yêu cầu API Key)";
      el.cfgApiKey.placeholder = "Provider này không cần API key";
    }

    if (meta && meta.requires_url) {
      document.getElementById("groupBackendUrl").style.display = "flex";
    }
  }

  function toggleKeyVisibility() {
    const isPass = el.cfgApiKey.type === "password";
    el.cfgApiKey.type = isPass ? "text" : "password";
    el.iconKeyEye.setAttribute("data-lucide", isPass ? "eye-off" : "eye");
    if (window.lucide) window.lucide.createIcons();
  }

  async function saveAiConfig() {
    const payload = {
      provider: el.cfgProvider.value,
      api_key: el.cfgApiKey.value.trim() || null,
      deep_think_llm: el.cfgDeepModel.value.trim() || null,
      quick_think_llm: el.cfgQuickModel.value.trim() || null,
      backend_url: el.cfgBackendUrl.value.trim() || null,
      llm_timeout: el.cfgTimeout ? (parseFloat(el.cfgTimeout.value) || 60) : 60,
      llm_max_retries: el.cfgMaxRetries ? (parseInt(el.cfgMaxRetries.value, 10) || 4) : 4,
    };

    el.btnSaveConfig.disabled = true;
    el.btnSaveConfig.innerHTML = '<i data-lucide="loader" class="spin"></i> Đang lưu...';
    if (window.lucide) window.lucide.createIcons();

    try {
      const res = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Không thể lưu cấu hình");

      el.configToast.textContent = "✔ Đã lưu cấu hình AI vào .env thành công!";
      el.configToast.className = "config-toast success";
      el.configToast.style.display = "block";

      if (data.config && data.config.has_key) {
        el.cfgKeyStatus.innerHTML = `Đang dùng key: <code>${data.config.api_key_masked}</code>`;
      }
      el.cfgApiKey.value = "";

      setTimeout(() => {
        el.configToast.style.display = "none";
      }, 3500);
    } catch (e) {
      el.configToast.textContent = `✖ Lỗi: ${e.message}`;
      el.configToast.className = "config-toast error";
      el.configToast.style.display = "block";
    } finally {
      el.btnSaveConfig.disabled = false;
      el.btnSaveConfig.innerHTML = '<i data-lucide="save"></i> <span>Lưu Cấu Hình (.env)</span>';
      if (window.lucide) window.lucide.createIcons();
    }
  }

  // =========================================================================
  // Git Upstream Pull & Synchronizer
  // =========================================================================
  async function openGitModal() {
    el.gitModal.classList.add("open");
    await checkGitUpstream();
  }

  async function checkGitUpstream() {
    el.gitStatusBadge.innerHTML = '<span class="badge-dot pulse"></span> Đang fetch upstream...';
    el.gitCommitsList.innerHTML = '<div class="loading-state">Đang kiểm tra commit mới từ upstream...</div>';

    appendGitTerminal("$ git fetch upstream");

    try {
      const res = await fetch("/api/git/status");
      const data = await res.json();

      if (data.error) throw new Error(data.error);
      if (data.disabled) {
        el.gitStatusBadge.textContent = "Cập nhật qua Docker image";
        el.gitCommitsList.textContent = data.message;
        el.btnGitPull.disabled = true;
        appendGitTerminal(data.message);
        return;
      }

      el.gitBranch.textContent = data.current_branch || "feat/vietnam-market";

      if (data.behind_count > 0) {
        el.gitStatusBadge.className = "info-pill badge-update";
        el.gitStatusBadge.innerHTML = `⚠️ Có <b>${data.behind_count}</b> commit mới từ upstream`;
      } else {
        el.gitStatusBadge.className = "info-pill badge-clean";
        el.gitStatusBadge.innerHTML = `✔ Đã ở bản mới nhất`;
      }

      if (data.new_commits && data.new_commits.length > 0) {
        el.gitCommitsList.innerHTML = "";
        data.new_commits.forEach((c) => {
          const parts = c.split(" ");
          const hash = parts[0];
          const msg = parts.slice(1).join(" ");
          const row = document.createElement("div");
          row.className = "git-commit-row";
          row.innerHTML = `<span class="commit-hash">${hash}</span> <span class="commit-msg">${escapeHtml(msg)}</span>`;
          el.gitCommitsList.appendChild(row);
        });
        appendGitTerminal(`Tìm thấy ${data.behind_count} commit mới từ upstream/main.`);
      } else {
        el.gitCommitsList.innerHTML = '<div class="empty-state" style="padding:16px;">Không có commit mới nào từ upstream. Bạn đang ở phiên bản mới nhất!</div>';
        appendGitTerminal("Không có cập nhật mới từ upstream.");
      }
    } catch (e) {
      el.gitStatusBadge.innerHTML = `<span style="color:#f43f5e">Lỗi fetch</span>`;
      appendGitTerminal(`Lỗi: ${e.message}`, "error");
    }
  }

  async function pullGitUpstream() {
    if (!confirm("Chuẩn bị bản cập nhật upstream trong checkout riêng để kiểm thử? Code đang chạy sẽ được giữ nguyên.")) {
      return;
    }

    el.btnGitPull.disabled = true;
    el.btnGitPull.innerHTML = '<i data-lucide="loader" class="spin"></i> Đang pull code...';
    if (window.lucide) window.lucide.createIcons();

    appendGitTerminal("$ python tools/prepare_upstream.py");

    try {
      const res = await fetch("/api/git/pull", { method: "POST" });
      const data = await res.json();

      if (data.success) {
        appendGitTerminal(`✔ ${data.message}`, "success");
        if (data.output) appendGitTerminal(data.output);
        el.gitStatusBadge.className = "info-pill badge-clean";
        el.gitStatusBadge.innerHTML = `✔ Đã chuẩn bị bản cập nhật`;
        el.gitCommitsList.innerHTML = '<div class="empty-state" style="padding:16px;">Bản cập nhật đã được chuẩn bị riêng. Cần kiểm thử trước khi triển khai.</div>';
      } else {
        appendGitTerminal(`✖ ${data.message}`, "error");
        if (data.error) appendGitTerminal(data.error, "error");
      }
    } catch (e) {
      appendGitTerminal(`Lỗi hệ thống: ${e.message}`, "error");
    } finally {
      el.btnGitPull.disabled = false;
      el.btnGitPull.innerHTML = '<i data-lucide="download-cloud"></i> <span>Chuẩn Bị Bản Cập Nhật</span>';
      if (window.lucide) window.lucide.createIcons();
    }
  }

  function appendGitTerminal(text, type = "normal") {
    const line = document.createElement("div");
    line.className = `term-line ${type}`;
    line.textContent = text;
    el.gitTerminalLogs.appendChild(line);
    el.gitTerminalLogs.scrollTop = el.gitTerminalLogs.scrollHeight;
  }
});
