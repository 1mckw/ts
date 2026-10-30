const REFRESH_SEC = 60;
const DATA_URL = "taiex-0050.json";
const TX_LABEL = "台指期";

let data = null;
let inputMode = "taiex";
let countdown = REFRESH_SEC;
let timer = null;

const els = {
  status: document.getElementById("status"),
  countdown: document.getElementById("countdown"),
  input: document.getElementById("price-input"),
  impliedEtf50: document.getElementById("implied-etf50"),
  impliedEtfInv: document.getElementById("implied-etf-inv"),
  inputLabel: document.getElementById("input-label"),
  basisWrap: document.getElementById("basis-wrap"),
  basis: document.getElementById("basis"),
  impliedTaiexLine: document.getElementById("implied-taiex-line"),
  impliedTaiex: document.getElementById("implied-taiex"),
  txSourceLine: document.getElementById("tx-source-line"),
  modeTabs: document.querySelectorAll(".mode-tab"),
  method: document.getElementById("method"),
  refreshBtn: document.getElementById("refresh-btn"),
  latestBtn: document.getElementById("latest-btn"),
};

function assertElements() {
  const missing = Object.entries(els)
    .filter(([key, node]) => key !== "modeTabs" && !node)
    .map(([key]) => key);
  if (missing.length) {
    throw new Error(`頁面元素缺失：${missing.join(", ")}`);
  }
}

function fmt(n, digits = 2) {
  return Number(n).toLocaleString("zh-TW", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function isTxMode() {
  return inputMode === "tx";
}

function impliedPrice(taiex, model, method) {
  const { alpha, beta, ratio, ref_taiex, ref_etf } = model;
  if (method === "ratio") return taiex * ratio;
  if (method === "regression") return alpha + beta * taiex;
  return ref_etf + beta * (taiex - ref_taiex);
}

function getRawInput() {
  const raw = parseFloat(els.input.value);
  return Number.isNaN(raw) ? null : raw;
}

function getInputTaiex() {
  const raw = getRawInput();
  if (raw === null) return data?.latest.taiex ?? 0;
  if (isTxMode()) {
    const basis = parseFloat(els.basis.value) || 0;
    return raw - basis;
  }
  return raw;
}

function applyModeUi() {
  const tx = isTxMode();
  els.modeTabs.forEach((tab) => {
    const active = tab.dataset.mode === inputMode;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", active ? "true" : "false");
  });
  els.inputLabel.textContent = tx ? `${TX_LABEL} 價格` : "TAIEX 點位";
  els.input.placeholder = tx ? "例如 44995" : "例如 23000";
  els.basisWrap.hidden = !tx;
  els.impliedTaiexLine.hidden = !tx;
  els.latestBtn.textContent = tx ? `帶入最新 ${TX_LABEL}` : "帶入最新 TAIEX";
}

function applyTxDefaults() {
  if (!data?.latest) return;
  const params = new URLSearchParams(location.search);
  if (data.latest.basis != null && !params.has("basis")) {
    els.basis.value = data.latest.basis;
  }
}

function fillLatestPrice() {
  if (!data?.latest) return;
  if (isTxMode()) {
    if (data.latest.tx != null) {
      els.input.value = data.latest.tx;
      if (data.latest.basis != null) els.basis.value = data.latest.basis;
    } else if (data.latest.taiex != null) {
      const basis = parseFloat(els.basis.value) || 0;
      els.input.value = data.latest.taiex + basis;
    }
  } else if (data.latest.taiex != null) {
    els.input.value = data.latest.taiex;
  }
  render();
}

function setInputMode(mode) {
  inputMode = mode;
  applyModeUi();
  render();
}

function render() {
  if (!data?.products) return;

  const method = els.method.value;
  const inputTaiex = getInputTaiex();
  const p0050 = data.products["0050"];
  const pInv = data.products["0050反"];

  if (p0050?.model) {
    els.impliedEtf50.textContent = fmt(impliedPrice(inputTaiex, p0050.model, method));
  }
  if (pInv?.model) {
    els.impliedEtfInv.textContent = fmt(impliedPrice(inputTaiex, pInv.model, method));
  }

  if (isTxMode()) {
    els.impliedTaiex.textContent = fmt(inputTaiex);
    if (data.latest.tx != null) {
      els.txSourceLine.textContent = `${TX_LABEL} ${fmt(data.latest.tx, 0)}（TradingView TXF1!）`;
    } else {
      els.txSourceLine.textContent = `${TX_LABEL} ${fmt(getRawInput() ?? inputTaiex, 0)}`;
    }
  }

  const updated = new Date(data.updated_at);
  const sample = `${data.sample.start} ~ ${data.sample.end}`;
  els.status.textContent = `資料更新 ${updated.toLocaleString("zh-TW", {
    hour: "2-digit",
    minute: "2-digit",
    month: "numeric",
    day: "numeric",
  })}（樣本 ${sample}）`;
}

function readUrlParams() {
  const params = new URLSearchParams(location.search);
  if (params.get("mode") === "tx") inputMode = "tx";
  const price = params.get("price");
  const basis = params.get("basis");
  if (price) els.input.value = price;
  if (basis) els.basis.value = basis;
  applyModeUi();
}

async function loadData() {
  const res = await fetch(`${DATA_URL}?t=${Date.now()}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  data = await res.json();
  if (!data.products?.["0050"] && !data.products?.["0050反"]) {
    throw new Error("換算資料格式錯誤，請重新執行 python taiex_0050.py");
  }
  applyTxDefaults();
  if (!getRawInput()) {
    if (isTxMode() && data.latest.tx != null) {
      els.input.value = data.latest.tx;
    } else if (data.latest.taiex != null) {
      els.input.value = data.latest.taiex;
    }
  }
  render();
}

function startCountdown() {
  countdown = REFRESH_SEC;
  els.countdown.textContent = `${countdown}s 後更新`;
  if (timer) clearInterval(timer);
  timer = setInterval(async () => {
    countdown -= 1;
    els.countdown.textContent = `${countdown}s 後更新`;
    if (countdown <= 0) {
      try {
        await loadData();
      } catch (err) {
        els.status.textContent = `更新失敗：${err.message}`;
      }
      countdown = REFRESH_SEC;
    }
  }, 1000);
}

try {
  assertElements();
  els.modeTabs.forEach((tab) => {
    tab.addEventListener("click", () => setInputMode(tab.dataset.mode));
  });
  ["input", "change"].forEach((evt) => {
    els.input.addEventListener(evt, render);
    els.basis.addEventListener(evt, render);
    els.method.addEventListener(evt, render);
  });
  els.refreshBtn.addEventListener("click", async () => {
    els.refreshBtn.disabled = true;
    try {
      await loadData();
      startCountdown();
    } catch (err) {
      els.status.textContent = `更新失敗：${err.message}`;
    } finally {
      els.refreshBtn.disabled = false;
    }
  });
  els.latestBtn.addEventListener("click", fillLatestPrice);
  readUrlParams();
  (async () => {
    try {
      await loadData();
      startCountdown();
    } catch (err) {
      els.status.textContent = `載入失敗：${err.message}`;
      els.countdown.textContent = "";
    }
  })();
} catch (err) {
  if (els.status) els.status.textContent = `初始化失敗：${err.message}`;
}
