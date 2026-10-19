/* PINHANHA 2.0 — SPA do painel de sinais Deriv */
const $ = id => document.getElementById(id);
let TOKEN = localStorage.p2tok || "";
let STRATS = {}, ASSETS = [];
let _tradesCache = [];

async function api(path, body) {
  const r = await fetch(path, {
    method: body ? "POST" : "GET",
    headers: { "x-token": TOKEN, "content-type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (r.status === 401) { showLogin(); throw new Error("401"); }
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || "HTTP " + r.status);
  return d;
}
function showLogin() { $("loginScr").style.display = "flex"; $("app").style.display = "none"; }
async function login() {
  const r = await fetch("https://stqhksrmcysuuigwzaoa.supabase.co/functions/v1/p2-panel/api/auth/login", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ password: $("pw").value }),
  });
  if (!r.ok) { $("loginErr").textContent = "password errada"; return; }
  TOKEN = (await r.json()).token; localStorage.p2tok = TOKEN;
  $("loginScr").style.display = "none"; $("app").style.display = "flex";
  boot();
}

const PAGES = {
  dash:   ["Dashboard", "Monitoriza o motor de sinais e o desempenho medido"],
  strats: ["Biblioteca de Estratégias", "Todas as estratégias do motor — com acerto medido ao vivo"],
  hist:   ["Histórico de Sinais", "Sinais avaliados — live e importados do PINHANHA 1"],
  anal:   ["Análise de Mercado", "Gráfico, indicadores, padrões e dígitos por ativo"],
  lab:    ["Laboratório", "1222 variantes, análise de ticks e sequências"],
  exec:   ["Execução Automática", "Conta Deriv, limites de risco e operações reais"],
  cfg:    ["Definições", "Ligação Deriv e varredura"],
};
function go(p) {
  document.querySelectorAll("nav button").forEach(b => b.classList.toggle("on", b.dataset.p === p));
  document.querySelectorAll(".page").forEach(s => s.classList.toggle("on", s.id === "p-" + p));
  $("pgTitle").textContent = PAGES[p][0]; $("pgSub").textContent = PAGES[p][1];
  if (p === "strats") renderStrats();
  if (p === "hist") loadHistory();
  if (p === "anal") loadAnalysis();
  if (p === "lab") { loadLab(); loadLabFx(); }
  if (p === "exec") loadExec();
  if (p === "cfg") loadCfg();
}

async function boot() {
  restoreCache();
  await refresh();
  setInterval(loadSignals, 5000);
  setInterval(loadStats, 8000);
  setInterval(loadStatus, 6000);
}

function restoreCache() {
  // pinta a última resposta guardada — a página abre instantânea
  try {
    const c = JSON.parse(localStorage.p2cache || "{}");
    if (c.signals) renderSignals(c.signals);
    if (c.sigMeta) $("sigMeta").textContent = c.sigMeta;
    if (c.kWr != null) { $("kWr").textContent = c.kWr; $("kN").textContent = c.kN ?? "—"; $("kPend").textContent = c.kPend ?? "—"; }
  } catch (e) {}
}
function saveCache(p) {
  try {
    const c = JSON.parse(localStorage.p2cache || "{}");
    Object.assign(c, p);
    localStorage.p2cache = JSON.stringify(c);
  } catch (e) {}
}

// ------------------------------------------------------- motor / estado
async function loadStatus() {
  try {
    const st = await api("https://stqhksrmcysuuigwzaoa.supabase.co/functions/v1/p2-panel/api/status");
    const conn = st.connected;
    $("connDot").className = "dot " + (conn ? "on" : "off");
    $("connLbl").textContent = conn ? "Ligado" : "Desligado";
    $("connHost").textContent = "Deriv · " + (st.host || "—");
    $("connMode").textContent = `modo: ${st.mode || "—"} · ${st.streamed || 0} stream / ${st.polled || 0} poll`;
    const ph = st.phase;
    const pill = $("enginePill");
    pill.textContent = ph === "RUNNING" ? "Live" : ph === "PAUSED" ? "Pausado" : "Stopped";
    pill.className = "pill " + (ph === "RUNNING" ? "on" : ph === "PAUSED" ? "paused" : "off");
  } catch (e) {}
}
async function engineCtl(a) {
  $("enginePill").textContent = "…";
  await api("https://stqhksrmcysuuigwzaoa.supabase.co/functions/v1/p2-panel/api/engine/" + a, {}); setTimeout(loadStatus, 400);
}

// ------------------------------------------------------- sinais
async function loadSignals() {
  try {
    const mc = +($("minConf")?.value || 50);
    const d = await api("https://stqhksrmcysuuigwzaoa.supabase.co/functions/v1/p2-panel/api/signals?min_conf=" + mc);
    renderSignals(d.signals || []);
    const meta = `${d.scanned || 0} mercados varridos · ${d.cached || 0} com dados` +
      (d.host ? ` · ${d.host}` : "") + (d.feed_mode ? ` · ${d.feed_mode}` : "") +
      (d.connected ? "" : " · feed desligado");
    $("sigMeta").textContent = meta;
    saveCache({ signals: d.signals || [], sigMeta: meta });
  } catch (e) {}
}

function renderSignals(rows) {
  const g = $("sigGrid");
  if (!rows.length) {
    g.innerHTML = `<div class="card sk" style="grid-column:1/-1">Sem sinais de momento — o motor continua a varrer. Os candidatos abaixo do corte aparecem como OBSERVAÇÃO.</div>`;
    return;
  }
  g.innerHTML = rows.map(s => `
    <div class="sigcard ${s.signal}">
      <span class="${s.watch ? "obsb" : "liveb"}">${s.watch ? "OBSERVAÇÃO" : s.grade_label || "LIVE"}</span>
      <div style="display:flex;justify-content:space-between;align-items:center">
        <b>${s.asset}</b><span class="pill ${s.signal}">${s.signal.toUpperCase()}</span>
      </div>
      <div class="sigdir ${s.signal}">${s.signal === "call" ? "CALL ↑" : "PUT ↓"}</div>
      <div class="sigprice">${s.price} · exp ${s.suggested_expiry || 60}s · ${s.cls}</div>
      <div class="confbar"><i style="width:${s.confidence}%"></i></div>
      <div style="display:flex;justify-content:space-between;font-size:11px;color:var(--mut)">
        <span>confiança calibrada</span><b style="color:var(--txt)">${s.confidence}%</b></div>
      <div class="tiles">
        <div class="tile">Tendência<b>${s.trend || "—"}</b></div>
        <div class="tile">Momentum<b>${s.momentum || "—"}</b></div>
        <div class="tile">Volatilidade<b>${s.volatility || "—"}</b></div>
        <div class="tile">Padrão<b style="font-size:10.5px">${s.pattern || "—"}</b></div>
        <div class="tile">Votos<b>${s.votes || "—"}</b></div>
        <div class="tile">RSI<b>${s.rsi ?? "—"}</b></div>
      </div>
      <div class="sig">${(s.reasons || []).slice(0, 3).map(r => `<span class="tag">${r}</span>`).join("")}</div>
    </div>`).join("");
}

// ------------------------------------------------------- stats + charts
async function loadStats() {
  try {
    const s = await api("https://stqhksrmcysuuigwzaoa.supabase.co/functions/v1/p2-panel/api/sigstats");
    const t = s.total || {};
    saveCache({ kWr: t.wr, kN: t.n, kPend: s.pending });
    $("kWr").textContent = t.wr != null ? t.wr + "%" : "—";
    $("kWr").className = "v " + (t.wr == null ? "" : t.wr >= 53.5 ? "pos" : "neg");
    $("kWrS").textContent = t.n ? `${t.wins}W / ${t.n - t.wins}L medidos` : "sem sinais fechados ainda";
    $("kN").textContent = t.n || 0;
    $("kNS").textContent = `+${s.void || 0} void`;
    $("kPend").textContent = s.pending || 0;
    drawCum(s.recent || []);
    drawDay(s.recent || []);
    renderRecent(s.recent || []);
  } catch (e) {}
  try {
    const st = await api("https://stqhksrmcysuuigwzaoa.supabase.co/functions/v1/p2-panel/api/status");
    $("kMk").textContent = st.assets_cached || 0;
    $("kMkS").textContent = `${st.symbols || 0} símbolos Deriv`;
  } catch (e) {}
}

function renderRecent(rows) {
  const b = $("recentBody");
  const rs = rows.slice(-10).reverse();
  if (!rs.length) { b.innerHTML = `<tr><td colspan="7" class="sk">sem dados ainda</td></tr>`; return; }
  b.innerHTML = rs.map(r => `<tr>
    <td class="mono">${r.emit_ts ? new Date(r.emit_ts * 1000).toLocaleTimeString() : "—"}</td>
    <td>${r.asset}</td>
    <td class="${r.signal === "call" ? "win" : "loss"}">${(r.signal || "").toUpperCase()}</td>
    <td class="mono">${r.conf}%</td>
    <td class="mono">${r.entry}</td><td class="mono">${r.exit ?? "—"}</td>
    <td class="${r.result === "win" ? "win" : r.result === "loss" ? "loss" : "void"}">${(r.result || "").toUpperCase()}</td></tr>`).join("");
}

function drawCum(rows) {
  const cv = $("cvCum"), ctx = cv.getContext("2d");
  const W = cv.width = cv.offsetWidth * 2, H = cv.height = 380;
  ctx.clearRect(0, 0, W, H);
  const rl = rows.filter(r => r.result === "win" || r.result === "loss");
  if (rl.length < 2) { ctx.fillStyle = "#7d8ab0"; ctx.font = "22px sans-serif"; ctx.fillText("sem avaliações ainda — o acerto aparece aqui", 20, H / 2); return; }
  let w = 0; const ys = rl.map((r, i) => { if (r.result === "win") w++; return 100 * w / (i + 1); });
  const min = Math.min(...ys) - 4, max = Math.max(...ys) + 4;
  const X = i => 30 + i / Math.max(1, ys.length - 1) * (W - 60);
  const Y = v => H - 25 - (v - min) / (max - min || 1) * (H - 60);
  // linha breakeven 53.5%
  if (53.5 >= min && 53.5 <= max) {
    ctx.strokeStyle = "rgba(255,176,32,.5)"; ctx.setLineDash([6, 5]);
    ctx.beginPath(); ctx.moveTo(30, Y(53.5)); ctx.lineTo(W - 30, Y(53.5)); ctx.stroke();
    ctx.setLineDash([]); ctx.fillStyle = "#ffb020"; ctx.font = "18px sans-serif";
    ctx.fillText("breakeven 53.5%", 36, Y(53.5) - 6);
  }
  ctx.beginPath(); ys.forEach((v, i) => i ? ctx.lineTo(X(i), Y(v)) : ctx.moveTo(X(i), Y(v)));
  ctx.strokeStyle = "#22e58c"; ctx.lineWidth = 3; ctx.stroke();
  ctx.lineTo(X(ys.length - 1), H - 25); ctx.lineTo(X(0), H - 25); ctx.closePath();
  const grad = ctx.createLinearGradient(0, 0, 0, H);
  grad.addColorStop(0, "rgba(34,229,140,.28)"); grad.addColorStop(1, "transparent");
  ctx.fillStyle = grad; ctx.fill();
  const last = ys[ys.length - 1];
  $("cumLbl").textContent = last.toFixed(1) + "%";
  $("cumLbl").className = last >= 53.5 ? "pos" : "neg";
}

function drawDay(rows) {
  const cv = $("cvDay"), ctx = cv.getContext("2d");
  const W = cv.width = cv.offsetWidth * 2, H = cv.height = 380;
  ctx.clearRect(0, 0, W, H);
  const by = {};
  rows.forEach(r => {
    if (r.result !== "win" && r.result !== "loss") return;
    const d = new Date((r.emit_ts || 0) * 1000).toISOString().slice(5, 10);
    by[d] = (by[d] || 0) + (r.result === "win" ? 1 : -1);
  });
  const ks = Object.keys(by).slice(-10);
  if (!ks.length) { ctx.fillStyle = "#7d8ab0"; ctx.font = "22px sans-serif"; ctx.fillText("sem dias avaliados", 20, H / 2); return; }
  const mx = Math.max(3, ...ks.map(k => Math.abs(by[k])));
  const bw = (W - 60) / ks.length;
  const y0 = H / 2;
  ctx.strokeStyle = "#1c2740"; ctx.beginPath(); ctx.moveTo(20, y0); ctx.lineTo(W - 20, y0); ctx.stroke();
  ks.forEach((k, i) => {
    const v = by[k], h = v / mx * (H / 2 - 40);
    ctx.fillStyle = v >= 0 ? "#22e58c" : "#ff5c6c";
    ctx.fillRect(30 + i * bw + 6, v >= 0 ? y0 - h : y0, bw - 12, Math.abs(h) || 2);
    ctx.fillStyle = "#7d8ab0"; ctx.font = "17px sans-serif"; ctx.textAlign = "center";
    ctx.fillText(k, 30 + i * bw + bw / 2, H - 8);
    ctx.fillStyle = "#e8eefc"; ctx.fillText((v > 0 ? "+" : "") + v, 30 + i * bw + bw / 2, v >= 0 ? y0 - h - 6 : y0 + Math.abs(h) + 18);
    ctx.textAlign = "left";
  });
}

// ------------------------------------------------------- estratégias
async function renderStrats() {
  if (!Object.keys(STRATS).length) { try { STRATS = await api("https://stqhksrmcysuuigwzaoa.supabase.co/functions/v1/p2-panel/api/strategies"); } catch (e) { return; } }
  const q = ($("stratSearch").value || "").toLowerCase();
  const g = $("stratGrid");
  g.innerHTML = Object.entries(STRATS)
    .filter(([k, s]) => !q || k.includes(q) || s.name.toLowerCase().includes(q) || s.desc.toLowerCase().includes(q))
    .map(([k, s]) => `<div class="strat">
      <h4>${s.name}</h4><p>${s.desc}</p>
      <div class="meta">
        ${s.voter ? '<span class="stbadge g">VOTANTE</span>' : '<span class="stbadge">catálogo</span>'}
        <span class="stbadge dim">min ${s.min_candles} velas</span>
        ${s.measured_n ? `<span class="stbadge ${s.measured_wr >= 53.5 ? "g" : ""}">medido: ${s.measured_wr}% (${s.measured_n})</span>` : '<span class="stbadge dim">sem dados medidos</span>'}
      </div>
      <button class="b ghost" style="margin-top:10px;width:100%" onclick="quickBt('${k}')">▶ Backtest (dados Deriv)</button>
    </div>`).join("");
}
async function quickBt(sid) {
  const asset = (ASSETS.find(a => a.is247) || {}).asset || "V100";
  const r = await api("https://stqhksrmcysuuigwzaoa.supabase.co/functions/v1/p2-panel/api/backtest", { strategy: sid, asset, n_candles: 500 });
  if (r.ok) alert(`${STRATS[sid].name} @ ${asset} (Deriv real)\ntrades: ${r.trades} · win-rate: ${r.win_rate}% · P&L: ${r.pnl}\n${r.disclaimer}`);
  else alert("Backtest: " + (r.error || "falhou"));
}

// ------------------------------------------------------- histórico
async function loadHistory() {
  const d = await api("https://stqhksrmcysuuigwzaoa.supabase.co/functions/v1/p2-panel/api/history/signals?limit=300");
  const f = $("histF").value, src = $("histSrc").value;
  let rows = d.signals || [];
  if (f) rows = rows.filter(r => r.result === f);
  if (src) rows = rows.filter(r => (r.source || "live") === src);
  $("histBody").innerHTML = rows.length ? rows.map(r => `<tr>
    <td class="mono">${r.emit_ts ? new Date(r.emit_ts * 1000).toLocaleString() : "—"}</td>
    <td>${r.asset}</td><td class="${r.signal === "call" ? "win" : "loss"}">${(r.signal || "").toUpperCase()}</td>
    <td class="mono">${r.conf ?? "—"}</td>
    <td style="font-size:11px;color:var(--mut)">${(r.voters || []).slice(0, 3).join(", ") || "—"}</td>
    <td class="mono">${r.entry ?? "—"}</td><td class="mono">${r.exit ?? "—"}</td>
    <td class="${r.result === "win" ? "win" : r.result === "loss" ? "loss" : "void"}">${(r.result || "").toUpperCase()}</td>
    <td>${r.source === "import" ? '<span class="stbadge">import</span>' : '<span class="stbadge g">live</span>'}</td></tr>`).join("")
    : `<tr><td colspan="9" class="sk">sem registos</td></tr>`;
}

// ------------------------------------------------------- análise
async function loadAnalysis() {
  if (!ASSETS.length) { try { ASSETS = await api("https://stqhksrmcysuuigwzaoa.supabase.co/functions/v1/p2-panel/api/assets"); } catch (e) { return; }
    $("analAsset").innerHTML = ASSETS.map(a => `<option value="${a.asset}">${a.asset} — ${a.label}</option>`).join("");
    $("analAsset").value = "V100"; }
  const a = $("analAsset").value || "V100";
  const cd = await api(`https://stqhksrmcysuuigwzaoa.supabase.co/functions/v1/p2-panel/api/candles/${a}?n=150`);
  $("analTitle").textContent = a + " — velas 1m";
  $("analSub").textContent = cd.candles?.length ? `${cd.candles.length} velas Deriv · última ${cd.candles.at(-1).close}` : "sem dados";
  drawCandles(cd.candles || []);
  const an = await api(`https://stqhksrmcysuuigwzaoa.supabase.co/functions/v1/p2-panel/api/analysis/${a}`);
  $("analTiles").innerHTML = an.error ? "sem dados" : `
    <div class="tile">Decisão<b>${an.decision || "—"}</b></div>
    <div class="tile">Tendência<b>${an.trend || "—"}</b></div>
    <div class="tile">Momentum<b>${an.momentum || "—"}</b></div>
    <div class="tile">Volatilidade<b>${an.volatility || "—"}</b></div>
    <div class="tile">RSI<b>${an.rsi ?? "—"}</b></div>
    <div class="tile">MACD<b>${an.macd || "—"}</b></div>
    <div class="tile">EMA<b>${an.ema || "—"}</b></div>
    <div class="tile">Consenso<b>${an.signal_agreement || "—"}</b></div>`;
  const p = await api(`https://stqhksrmcysuuigwzaoa.supabase.co/functions/v1/p2-panel/api/patterns/${a}`);
  $("patList").innerHTML = (p.patterns || []).length
    ? p.patterns.slice(-14).reverse().map(x => `<div style="padding:5px 0;border-bottom:1px solid var(--line);font-size:12.5px">
        <span class="${x.direction === "call" ? "win" : "loss"}">${x.direction === "call" ? "▲" : "▼"}</span>
        <b>${x.pattern || x.name}</b> <span style="color:var(--mut)">· força ${x.strength?.toFixed(2) ?? x.strength}</span></div>`).join("")
    : `<span class="sk">sem padrões detectados nas últimas velas</span>`;
  loadDigits(a);
}
async function loadDigits(a) {
  const d = await api(`https://stqhksrmcysuuigwzaoa.supabase.co/functions/v1/p2-panel/api/digits/${a}?n=200`);
  if (!d.available) { $("digGrid").innerHTML = `<span class="sk" style="grid-column:1/-1">${d.reason || "indisponível"}</span>`; $("digLast").innerHTML = ""; return; }
  const mx = Math.max(...Object.values(d.counts));
  $("digGrid").innerHTML = Object.entries(d.counts).map(([k, v]) =>
    `<div class="dcell"><b style="color:${v === mx ? "var(--cy)" : "var(--txt)"}">${k}</b><span>${v}</span></div>`).join("");
  $("digLast").innerHTML = d.last.map(x => `<i>${x}</i>`).join("");
}
async function loadInsight() {
  const a = $("analAsset").value || "V100";
  $("aiTxt").textContent = "a gerar…";
  const r = await api(`https://stqhksrmcysuuigwzaoa.supabase.co/functions/v1/p2-panel/api/ai/insight/${a}`);
  $("aiTxt").innerHTML = (r.text || "sem análise") + ` <span class="tag">${r.source || ""}</span>`;
}
function drawCandles(cds) {
  const cv = $("cvChart"), ctx = cv.getContext("2d");
  const W = cv.width = cv.offsetWidth * 2, H = cv.height = 460;
  ctx.clearRect(0, 0, W, H);
  if (!cds.length) { ctx.fillStyle = "#7d8ab0"; ctx.font = "22px sans-serif"; ctx.fillText("sem dados", 20, H / 2); return; }
  const hi = Math.max(...cds.map(c => c.high)), lo = Math.min(...cds.map(c => c.low));
  const X = i => 40 + i * (W - 70) / cds.length, bw = Math.max(2, (W - 70) / cds.length - 3);
  const Y = v => 20 + (hi - v) / (hi - lo || 1) * (H - 60);
  ctx.strokeStyle = "#1c2740";
  for (let i = 0; i < 5; i++) { const y = 20 + i * (H - 60) / 4; ctx.beginPath(); ctx.moveTo(30, y); ctx.lineTo(W - 20, y); ctx.stroke(); }
  cds.forEach((c, i) => {
    const up = c.close >= c.open; ctx.strokeStyle = ctx.fillStyle = up ? "#22e58c" : "#ff5c6c";
    ctx.beginPath(); ctx.moveTo(X(i) + bw / 2, Y(c.high)); ctx.lineTo(X(i) + bw / 2, Y(c.low)); ctx.stroke();
    ctx.fillRect(X(i), Y(Math.max(c.open, c.close)), bw, Math.max(2, Math.abs(Y(c.open) - Y(c.close))));
  });
  ctx.fillStyle = "#7d8ab0"; ctx.font = "18px sans-serif";
  ctx.fillText(hi.toPrecision(6), 34, 18); ctx.fillText(lo.toPrecision(6), 34, H - 40);
}

// ------------------------------------------------------- definições
async function loadCfg() {
  try {
    const st = await api("https://stqhksrmcysuuigwzaoa.supabase.co/functions/v1/p2-panel/api/deriv/status");
    $("cfgTbl").innerHTML = `
      <tr><th>Host</th><td class="mono">${st.host || "—"}</td></tr>
      <tr><th>Modo</th><td>${st.mode || "—"}</td></tr>
      <tr><th>App ID</th><td class="mono">${st.app_id}</td></tr>
      <tr><th>Símbolos</th><td>${st.symbols} (${st.streamed} stream / ${st.polled} poll)</td></tr>
      <tr><th>Última msg</th><td class="mono">${st.last_msg_ts ? new Date(st.last_msg_ts * 1000).toLocaleTimeString() : "—"}</td></tr>
      <tr><th>Último erro</th><td style="font-size:11.5px">${st.last_error || "—"}</td></tr>`;
    $("cfgNote").textContent = "Sem token: apenas dados públicos de mercado (sinais). Para saldo/ordens seria preciso token Deriv — fora desta fase.";
  } catch (e) {}
  try {
    const as = await api("https://stqhksrmcysuuigwzaoa.supabase.co/functions/v1/p2-panel/api/assets");
    $("assetBody").innerHTML = as.map(a => `<tr>
      <td>${a.asset}</td><td class="mono" style="font-size:11.5px">${a.symbol}</td>
      <td>${a.cls}</td><td>${a.schedule}</td>
      <td><span class="pill ${a.mode === "stream" ? "on" : a.mode === "poll" ? "cy" : "off"}">${a.mode}</span></td>
      <td style="font-size:11px;color:var(--mut)">${a.err || ""}</td></tr>`).join("");
  } catch (e) {}
}
async function loadDiag() {
  $("diagOut").textContent = "a carregar diagnóstico…";
  const d = await api("https://stqhksrmcysuuigwzaoa.supabase.co/functions/v1/p2-panel/api/sigdiag");
  $("diagOut").textContent = JSON.stringify(d, null, 1).slice(0, 4000);
}

// --------------------------------------------------------- execução auto
const LIM_FIELDS = [
  ["stake", "Stake por operação"], ["stake_min", "Stake mín."],
  ["stake_max", "Stake máx."], ["max_open", "Máx. abertas"],
  ["max_per_day", "Máx. ops/dia"], ["daily_loss_limit", "Perda diária máx."],
  ["consec_loss_limit", "Perdas seguidas máx."], ["daily_exposure", "Exposição diária máx."],
  ["min_payout", "Payout mín."], ["min_conf", "Conf. mín. p/ executar"],
  ["duration_s", "Duração (s)"], ["pause_after_errors", "Pausa após falhas"],
  ["profit_target", "META lucro/dia (0=off)"], ["max_per_minute", "Máx. entradas/minuto"],
];
const VOTERS = ["ai_turbo","multi_indicator","rsi_momentum","macd_momentum",
  "momentum","bible_sr","price_action","breakout","bible_engulfing",
  "bible_pinbar","q_reversao","q_pullback","q_breakout_retest",
  "q_range_fade","q_mtf"];
let _execT = null, _zoom = 100, _st = null, _digAssets = [];

async function loadExec() {
  try {
    const st = await api("https://stqhksrmcysuuigwzaoa.supabase.co/functions/v1/p2-panel/api/autotrade/status");
    _st = st; renderExec(st);
    refreshRealOps();
    const tr = await api("https://stqhksrmcysuuigwzaoa.supabase.co/functions/v1/p2-panel/api/trades?limit=60");
    _tradesCache = tr.trades || [];
    renderTrades(_tradesCache);
    refreshRealOps();
    if (!_digAssets.length) {
      const as = await api("https://stqhksrmcysuuigwzaoa.supabase.co/functions/v1/p2-panel/api/assets");
      _digAssets = (as || []).filter(a => a.cls === "synthetic");
      $("pcDigAsset").innerHTML = _digAssets.map(a =>
        `<option value="${a.asset}">${a.label}</option>`).join("");
      $("pcStrat").innerHTML =
        '<option value="">Selecionar Robô (consenso 15)</option>' +
        VOTERS.map(v => `<option value="${v}">${v}</option>`).join("");
      const sf = st.limits?.strategy_filter;
      if (sf) $("pcStrat").value = sf;
      loadDigits();
    }
    if (!_execT) _execT = setInterval(async () => {
      if (!document.getElementById("p-exec").classList.contains("on")) return;
      try { _st = await api("https://stqhksrmcysuuigwzaoa.supabase.co/functions/v1/p2-panel/api/autotrade/status"); renderExec(_st);
            refreshRealOps();
            _tradesCache = (await api("https://stqhksrmcysuuigwzaoa.supabase.co/functions/v1/p2-panel/api/trades?limit=60")).trades || [];
            renderTrades(_tradesCache);
            loadDigits(); } catch (e) {}
    }, 5000);
  } catch (e) { $("exErr").textContent = String(e); }
}

// ---------- dados 100% reais da conta Deriv ----------
async function refreshRealOps() {
  try {
    const [pf, pt] = await Promise.all([
      api("https://stqhksrmcysuuigwzaoa.supabase.co/functions/v1/p2-panel/api/deriv/portfolio"), api("https://stqhksrmcysuuigwzaoa.supabase.co/functions/v1/p2-panel/api/deriv/profit-table?limit=40")]);
    renderDerivOps(pf.contracts || [], pt.transactions || []);
  } catch (e) {}
}

function shortToType(sc) {
  // "CALL_R_100_1.89_…_S0P_0" → "CALL · R_100"
  const p = (sc || "").split("_");
  return p.length >= 2 ? `${p[0]} · ${p[1]}` : (sc || "—");
}

function tradeTypeFor(cid) {
  // profit_table não devolve shortcode — cruzar com o pipeline interno
  const t = (_tradesCache || []).find(x => String(x.contract_id) === String(cid));
  return t ? `${(t.signal || "").toUpperCase()} · ${t.asset}` : null;
}

function renderDerivOps(contracts, txs) {
  const rows = [];
  for (const c of contracts)
    rows.push({type: shortToType(c.shortcode) || tradeTypeFor(c.contract_id) || c.contract_type,
               ts: c.purchase_time, price: c.buy_price,
               res: c.profit != null ? +c.profit : null, open: true});
  for (const t of txs)
    rows.push({type: shortToType(t.shortcode) || tradeTypeFor(t.contract_id),
               ts: t.sell_time || t.purchase_time,
               price: t.buy_price,
               res: t.sell_price != null ? +(t.sell_price - t.buy_price).toFixed(2) : null,
               open: false});
  rows.sort((a, b) => (b.ts || 0) - (a.ts || 0));
  $("pcNoTr").style.display = rows.length ? "none" : "block";
  $("pcTxO").textContent = contracts.length;
  $("pcTxT").textContent = contracts.length + txs.length;
  const pl = txs.reduce((s, t) => s + ((t.sell_price ?? 0) - (t.buy_price ?? 0)), 0)
           + contracts.reduce((s, c) => s + (+(c.profit || 0)), 0);
  $("pcPl").textContent = "$ " + pl.toFixed(2);
  $("pcPl").classList.toggle("neg", pl < 0);
  // fases
  $("ph1").classList.toggle("on", true);
  $("ph2").classList.toggle("on", contracts.length > 0);
  $("ph3").classList.toggle("on", !contracts.length && txs.length > 0);
  $("pcTrBody").innerHTML = rows.map(r => `<tr>
    <td>${r.type}</td>
    <td>${r.ts ? new Date(r.ts * 1000).toLocaleTimeString("pt-PT") : "—"}${r.open ? ' <span class="pill cy" style="padding:1px 7px;font-size:9px">aberto</span>' : ""}</td>
    <td class="mono">${r.price != null ? (+r.price).toFixed(2) : "—"}</td>
    <td class="${r.res == null ? "" : r.res >= 0 ? "res-w" : "res-l"}">${r.res != null ? (r.res > 0 ? "+" : "") + r.res.toFixed(2) : (r.open ? "…" : "—")}</td>
  </tr>`).join("");
}

// ---------- dígitos + gráfico ----------
async function loadDigits() {
  const a = $("pcDigAsset").value;
  if (!a) return;
  try {
    const d = await api(`https://stqhksrmcysuuigwzaoa.supabase.co/functions/v1/p2-panel/api/digits/${a}?n=200`);
    if (!d.available) { $("pcDigPct").innerHTML = `<span class="disc">${d.reason || "sem dados"}</span>`; return; }
    const tot = Object.values(d.counts).reduce((s, x) => s + x, 0) || 1;
    const maxD = Object.keys(d.counts).reduce((m, k) => d.counts[k] > d.counts[m] ? k : m, "0");
    $("pcDigPct").innerHTML = [...Array(10)].map((_, i) =>
      `<div>${(100 * d.counts[String(i)] / tot).toFixed(1)}%</div>`).join("");
    $("pcDigN").textContent = `${d.n} ${d.source === "candles" ? "candles" : "ticks"}`;
    $("pcLastDig").textContent = d.last?.[0] ?? "—";
    $("pcSymPrice").textContent = d.last_price != null ? (+d.last_price).toFixed(2) : "—";
    $("pcSymName").textContent = _digAssets.find(x => x.asset === a)?.label || a;
    drawDigBars(d.counts, maxD);
    drawPcChart(a, d);
  } catch (e) {}
}

function drawDigBars(counts, maxD) {
  const cv = $("pcDigBars"), ctx = cv.getContext("2d");
  const W = cv.width = cv.offsetWidth || 420, H = cv.height = 130;
  ctx.clearRect(0, 0, W, H);
  const tot = Object.values(counts).reduce((s, x) => s + x, 0) || 1;
  const bw = W / 10;
  for (let i = 0; i < 10; i++) {
    const v = counts[String(i)] / tot, h = Math.max(6, v * (H - 26));
    const hot = String(i) === maxD;
    ctx.fillStyle = hot ? "#22e58c" : "#3d4a66";
    const x = i * bw + bw * 0.16;
    roundRect(ctx, x, H - 18 - h, bw * 0.68, h, 4); ctx.fill();
    ctx.fillStyle = "#7d8ab0"; ctx.font = "11px system-ui"; ctx.textAlign = "center";
    ctx.fillText(String(i), i * bw + bw / 2, H - 4);
  }
}
function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath(); ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
}

async function drawPcChart(asset, d) {
  try {
    const cd = await api(`https://stqhksrmcysuuigwzaoa.supabase.co/functions/v1/p2-panel/api/candles/${asset}?period=60&n=60`);
    const candles = cd.candles || [];
    const cv = $("pcChart"), ctx = cv.getContext("2d");
    const W = cv.width = cv.offsetWidth || 420, H = cv.height = 90;
    ctx.clearRect(0, 0, W, H);
    if (!candles.length) return;
    const closes = candles.map(c => c.close), mn = Math.min(...closes), mx = Math.max(...closes);
    const X = i => 8 + i * (W - 16) / (closes.length - 1);
    const Y = v => 8 + (mx - v) / ((mx - mn) || 1) * (H - 30);
    ctx.strokeStyle = "#e8eefc"; ctx.lineWidth = 1.4; ctx.beginPath();
    closes.forEach((c, i) => i ? ctx.lineTo(X(i), Y(c)) : ctx.moveTo(X(i), Y(c)));
    ctx.stroke();
    // marcadores de dígitos por baixo (como no painel de referência)
    const pip = 2, ds = closes.map(c => Math.round(c * 10 ** pip) % 10);
    const step = Math.max(1, Math.floor(closes.length / 25));
    ctx.font = "9px system-ui"; ctx.textAlign = "center";
    for (let i = 0; i < closes.length; i += step) {
      ctx.fillStyle = "#0e1526"; ctx.beginPath();
      ctx.arc(X(i), H - 10, 8, 0, 7); ctx.fill();
      ctx.strokeStyle = "#1c2740"; ctx.stroke();
      ctx.fillStyle = "#e8eefc"; ctx.fillText(String(ds[i]), X(i), H - 7);
    }
    ctx.fillStyle = "#22d3ee"; ctx.beginPath();
    ctx.arc(X(closes.length - 1), Y(closes[closes.length - 1]), 3.5, 0, 7); ctx.fill();
  } catch (e) {}
}

// ---------- controlos ----------
function pcZoom(d) {
  _zoom = d === 0 ? 100 : Math.min(150, Math.max(60, _zoom + d * 10));
  $("pcZoom").textContent = _zoom + "%";
  document.getElementById("p-exec").style.zoom = _zoom / 100;
}
function pcTab(w) {
  $("tabTrader").classList.toggle("on", w === "trader");
  $("tabNotif").classList.toggle("on", w === "notif");
  $("paneTrader").style.display = w === "trader" ? "block" : "none";
  $("paneNotif").style.display = w === "notif" ? "block" : "none";
}
function pcToggle() { autoCtl(!(_st && _st.enabled)); }
async function pcSwitchAcc(loginid) {
  if (!loginid) return;
  const x = (_st?.accounts || []).find(a => a.loginid === loginid);
  if (!x) return;
  await pickAcc(loginid, !x.is_virtual);
  refreshRealOps();
}
async function pcSetStrat(v) {
  try {
    const st = await api("https://stqhksrmcysuuigwzaoa.supabase.co/functions/v1/p2-panel/api/autotrade/limits",
                         {limits: {strategy_filter: v || null}});
    _st = st;
  } catch (e) { $("exErr").textContent = String(e); }
}
function notif(msg, bad) {
  const d = document.createElement("div");
  d.style.cssText = `padding:6px 0;border-bottom:1px solid var(--line);color:${bad ? "var(--r)" : "var(--mut)"}`;
  d.textContent = `${new Date().toLocaleTimeString("pt-PT")} — ${msg}`;
  $("notifList").prepend(d);
  $("notifN").textContent = $("notifList").childElementCount;
}

function renderExec(st) {
  const a = st.account;
  // cartão de ligação — sempre visível nas Definições
  $("accCard").style.display = "block";
  $("accState").innerHTML = st.authorized
    ? `<span class="pill on">${a?.is_virtual ? "DEMO" : "REAL"}</span> <b>${a?.loginid || "?"}</b> · ${a?.currency || ""}`
    : `<span class="pill off">não ligado</span>`;
  $("accForm").style.display = st.authorized ? "none" : "block";
  $("accPick").style.display = (st.accounts || []).length ? "block" : "none";
  const hasReal = (st.accounts || []).some(x => !x.is_virtual);
  $("realCk").style.display = hasReal ? "block" : "none";
  // SALDO + tipo de conta
  $("pcBal").textContent = st.authorized && a
    ? `$ ${(+ (a.balance ?? 0)).toFixed(2)} ${a.currency || "USD"}` : "$ — USD";
  const cur = a?.loginid || "";
  $("pcAcc").innerHTML = (st.accounts || []).map(x =>
    `<option value="${x.loginid}" ${x.loginid === cur ? "selected" : ""}>${x.is_virtual ? "Conta Virtual" : "Conta Real"} — ${x.loginid} (${x.currency} ${x.balance ?? "?"})</option>`).join("")
    || '<option value="">— ligar conta —</option>';
  // estado
  const ex = [];
  ex.push(st.enabled ? '<span class="pill on">EXECUÇÃO ATIVA</span>'
                     : '<span class="pill off">EXECUÇÃO OFF</span>');
  if (st.emergency) ex.push('<span class="pill" style="background:var(--r);color:#000">EMERGÊNCIA</span>');
  if (st.mode) ex.push(`<span class="pill cy">${st.mode.toUpperCase()}</span>`);
  if (st.host) ex.push(`<span class="disc" style="margin:0">${st.host}</span>`);
  $("exState").innerHTML = ex.join(" ");
  $("exErr").textContent = st.last_error || "";
  $("pcPlay").textContent = st.enabled ? "■ PARAR" : "▶ OPERAR";
  $("pcPlay").className = "b " + (st.enabled ? "stop" : "go");
  const ls = st.last_signal;
  $("lastSig").textContent = ls
    ? `último sinal: ${ls.asset || "?"} ${ls.signal || ""} · conf ${ls.conf ?? "—"}% · votos ${ls.voters ?? 0} · ${ls.why || "avaliado"}`
    : "";
  const lo = st.last_order;
  $("lastOrder").textContent = lo
    ? `última ordem: ${lo.symbol} ${lo.type} stake ${lo.stake} contrato ${lo.contract_id || "—"} · estado ${lo.state || ""}`
    : "";
  $("limGrid").innerHTML = LIM_FIELDS.map(([k, lbl]) =>
    `<div><label style="font-size:11px">${lbl}</label>
     <input id="lim_${k}" type="number" step="any" value="${st.limits?.[k] ?? ""}"></div>`).join("");
  if (st.last_error) notif(st.last_error, true);
}

function renderTrades(trades) {
  const fmt = t => new Date((t.emit_ts || 0) * 1000).toLocaleTimeString("pt-PT");
  const stc = {settled: "var(--g)", open: "var(--cy)", rejected: "var(--r)",
               buy_sent: "#eab308", confirmed: "#eab308", proposal: "#eab308",
               validated: "var(--mut)", candidate: "var(--mut)"};
  $("trBody").innerHTML = trades.length ? trades.map(t => `<tr>
    <td>${fmt(t)}</td><td class="mono">${t.symbol || t.asset || ""}</td>
    <td>${t.contract_type || t.signal || ""}</td><td>${t.stake ?? ""}</td>
    <td><span class="pill" style="color:${stc[t.state] || "var(--mut)"}">${t.state}</span></td>
    <td class="mono" style="font-size:11px">${t.contract_id || ""}</td>
    <td style="color:${(t.profit || 0) >= 0 ? "var(--g)" : "var(--r)"}">${t.profit != null ? t.profit.toFixed(2) : ""}</td>
    <td style="font-size:11px;color:var(--mut)">${t.error || ""}</td></tr>`).join("")
    : `<tr><td colspan="8" class="sk">sem operações</td></tr>`;
}

async function connectDeriv() {
  const tok = $("dToken").value.trim();
  if (!tok) { $("exErr").textContent = "cola o API token"; return; }
  $("exErr").textContent = "a autorizar…";
  try {
    const st = await api("https://stqhksrmcysuuigwzaoa.supabase.co/functions/v1/p2-panel/api/deriv/connect", {token: tok});
    $("dToken").value = "";
    renderExec(st); refreshRealOps();
  } catch (e) { $("exErr").textContent = String(e); }
}

async function pickAcc(loginid, needReal) {
  if (needReal && !$("ckReal").checked) {
    $("accCard").style.display = "block";
    $("exErr").textContent = "conta REAL — marca a confirmação primeiro";
    return;
  }
  try {
    const st = await api("https://stqhksrmcysuuigwzaoa.supabase.co/functions/v1/p2-panel/api/deriv/select",
                         {loginid, confirm_real: needReal});
    _st = st; renderExec(st);
  } catch (e) { $("exErr").textContent = String(e); }
}

async function disconnectDeriv() {
  try { await api("https://stqhksrmcysuuigwzaoa.supabase.co/functions/v1/p2-panel/api/deriv/disconnect", {}); loadExec(); }
  catch (e) { $("exErr").textContent = String(e); }
}

async function autoCtl(on) {
  $("exErr").textContent = "";
  try {
    const st = await api("https://stqhksrmcysuuigwzaoa.supabase.co/functions/v1/p2-panel/api/autotrade/enable", {on});
    _st = st; renderExec(st);
    notif(on ? "execução automática ativada" : "execução pausada");
  } catch (e) { $("exErr").textContent = String(e); notif(String(e), true); }
}

async function emergency() {
  try {
    const st = await api("https://stqhksrmcysuuigwzaoa.supabase.co/functions/v1/p2-panel/api/autotrade/emergency", {on: true});
    _st = st; renderExec(st);
    notif("PARAGEM DE EMERGÊNCIA — novas entradas bloqueadas", true);
  } catch (e) { $("exErr").textContent = String(e); }
}

async function saveLimits() {
  const patch = {};
  LIM_FIELDS.forEach(([k]) => {
    const v = parseFloat($("lim_" + k).value);
    if (!isNaN(v)) patch[k] = v;
  });
  try {
    const st = await api("https://stqhksrmcysuuigwzaoa.supabase.co/functions/v1/p2-panel/api/autotrade/limits", {limits: patch});
    _st = st; renderExec(st);
    $("limMsg").textContent = "guardado ✓";
    setTimeout(() => $("limMsg").textContent = "", 2500);
  } catch (e) { $("exErr").textContent = String(e); }
}

async function refresh() { await Promise.all([loadStatus(), loadSignals(), loadStats()]); }
if (!TOKEN) showLogin(); else { boot(); }

// ---------- LABORATÓRIO ----------
let _labT = null;
async function loadLab() {
  try {
    const [v, s, t] = await Promise.all([
      api("https://stqhksrmcysuuigwzaoa.supabase.co/functions/v1/p2-panel/api/variants"), api("https://stqhksrmcysuuigwzaoa.supabase.co/functions/v1/p2-panel/api/streaks"), api("https://stqhksrmcysuuigwzaoa.supabase.co/functions/v1/p2-panel/api/tickmind")]);
    $("labKpis").innerHTML = `
      <div class="card"><label>VARIANTES</label><div class="pc-big">${v.total ?? "—"}</div>
        <span class="disc">${v.families ?? "—"} famílias · ${v.evaluated ?? 0} avaliadas</span></div>
      <div class="card"><label>WR MEDIDO</label><div class="pc-big">${s.wr != null ? (s.wr*100).toFixed(1)+"%" : "—"}</div>
        <span class="disc">${s.n ?? 0} sinais avaliados</span></div>
      <div class="card"><label>MAIOR SEQUÊNCIA</label><div class="pc-big">${s.max_win_streak ?? "—"}</div>
        <span class="disc">wins seguidas · P(20): ${(s.p20_given_wr ?? 0).toExponential(1)}</span></div>`;
    $("labVarInfo").textContent = v.ts
      ? `actualizado ${new Date(v.ts*1000).toLocaleTimeString()} — rankeado nas velas reais Deriv`
      : "a calcular… (ranking corre a cada ~15min no worker)";
    $("labVarBody").innerHTML = (v.top || []).map(x =>
      `<tr><td style="font-family:monospace;font-size:11px">${x.id}</td><td>${x.family}</td>
       <td>${x.asset}</td><td>${x.n}</td>
       <td style="color:${x.wr>=0.55?"var(--g)":x.wr<0.48?"var(--r)":"inherit"}">${(x.wr*100).toFixed(1)}%</td></tr>`).join("")
      || '<tr><td colspan="5" class="sk">a calcular…</td></tr>';
    const cls = Object.entries(s.per_class || {}).map(([k,x]) =>
      `<div>${k}: ${(x.wr*100).toFixed(1)}% (${x.n}) · max seq ${x.max_win_streak}</div>`).join("");
    $("labStreaks").innerHTML = s.n ? `
      <div>Vitórias seguidas máx.: <b>${s.max_win_streak}</b> · Derrotas: <b>${s.max_loss_streak}</b></div>
      <div>Sequência actual: ${s.current?.dir} ×${s.current?.n}</div>
      <div>WR: ${(s.wr*100).toFixed(1)}% [${(s.wr_ci95?.[0]*100).toFixed(0)}–${(s.wr_ci95?.[1]*100).toFixed(0)}%]</div>
      <div>P(20 wins): ${(s.p20_given_wr ?? 0).toExponential(2)}</div>
      <div style="margin-top:8px;color:var(--mut)">${cls}</div>
      <div class="disc" style="margin-top:8px">${s.note || ""}</div>`
      : "sem resultados avaliados ainda";
    const assets = Object.keys(t || {});
    if (assets.length && !$("labTickAsset").options.length)
      $("labTickAsset").innerHTML = assets.map(a => `<option>${a}</option>`).join("");
    _labT = t;
    renderLabTicks();
  } catch (e) {}
}
function renderLabTicks() {
  const a = $("labTickAsset").value;
  const d = (_labT || {})[a];
  if (!d) { $("labTicks").textContent = "sem dados de ticks"; return; }
  const w = d.windows || {};
  $("labTicks").textContent =
    `${a} · ${d.n_ticks} ticks · último ${w["5"]?.last_px ?? "—"}\n` +
    Object.entries(w).map(([k, x]) =>
      `${k.padStart(4)}t ↑${x.up} ↓${x.down} p_up ${x.p_up}±${x.se} streak ${x.max_streak} acel ${x.accel}`).join("\n") +
    `\nregime: ${d.regime?.detected ? "MUDANÇA" : "estável"} (vol×${d.regime?.vol_ratio ?? "?"})` +
    `\nfalso rompimento: ↑${d.false_break?.up?"sim":"não"} ↓${d.false_break?.down?"sim":"não"}`;
}

async function loadLabFx() {
  try {
    const [sy, lab] = await Promise.all([
      api("https://stqhksrmcysuuigwzaoa.supabase.co/functions/v1/p2-panel/api/forex_symbols"), api("https://stqhksrmcysuuigwzaoa.supabase.co/functions/v1/p2-panel/api/forex_lab")]);
    $("labFxInfo").textContent = sy.n != null
      ? `${sy.n} pares Forex validados via active_symbols (market=forex) · ${lab.n_variants ?? "?"} variantes forex`
      : "a descobrir pares…";
    const lines = Object.entries(lab.assets || {}).map(([a, r]) => {
      if (r.verdict !== "ENTRADA")
        return `${a}: SEM ENTRADA — ${r.reason}`;
      return `${a}: ` + r.signals.map(s =>
        `${s.signal.toUpperCase()} ${s.id} (wr ${(s.wr*100).toFixed(0)}% n${s.n})`).join(" · ");
    });
    $("labFx").textContent = lines.join("\n") || "a calcular…";
  } catch (e) {}
}
