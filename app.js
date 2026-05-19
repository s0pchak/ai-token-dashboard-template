const usage = window.AI_TOKEN_USAGE || window.CODEX_TOKEN_USAGE || {
  generatedAt: null,
  firstDate: null,
  lastDate: null,
  timezone: "America/New_York",
  totals: {},
  models: [],
  days: [],
  stats: {},
};

const state = {
  range: "all",
  hoveredIndex: null,
  pointerX: 0,
  pointerY: 0,
  chartGeometry: null,
};

const modelPalette = [
  "#5ff0b2",
  "#75a7ff",
  "#f6bf63",
  "#ed6a8f",
  "#a78bfa",
  "#62d8ff",
  "#d6f35a",
  "#ff8f5f",
  "#58d6c9",
  "#c991ff",
];

const modelColors = new Map();
(usage.models || []).forEach((model, index) => {
  modelColors.set(model.name, modelPalette[index % modelPalette.length]);
});

const els = {
  generatedAt: document.querySelector("#generatedAt"),
  totalTokens: document.querySelector("#totalTokens"),
  dateSpan: document.querySelector("#dateSpan"),
  todayTokens: document.querySelector("#todayTokens"),
  todayCalls: document.querySelector("#todayCalls"),
  durationValue: document.querySelector("#durationValue"),
  durationDate: document.querySelector("#durationDate"),
  topModel: document.querySelector("#topModel"),
  topModelShare: document.querySelector("#topModelShare"),
  rangeCaption: document.querySelector("#rangeCaption"),
  tableCaption: document.querySelector("#tableCaption"),
  chart: document.querySelector("#dailyChart"),
  tooltip: document.querySelector("#chartTooltip"),
  dailyRows: document.querySelector("#dailyRows"),
  modelMix: document.querySelector("#modelMix"),
  captureMeta: document.querySelector("#captureMeta"),
  heroTotal: document.querySelector("#heroTotal"),
  heroCaption: document.querySelector("#heroCaption"),
  heroPeakTokens: document.querySelector("#heroPeakTokens"),
  heroPeakDate: document.querySelector("#heroPeakDate"),
  heroLongestDuration: document.querySelector("#heroLongestDuration"),
  heroLongestDate: document.querySelector("#heroLongestDate"),
  heroCalls: document.querySelector("#heroCalls"),
  heroCallCaption: document.querySelector("#heroCallCaption"),
  incidentTicker: document.querySelector("#incidentTicker"),
  selectedIncident: document.querySelector("#selectedIncident"),
  achievementGrid: document.querySelector("#achievementGrid"),
  achievementCaption: document.querySelector("#achievementCaption"),
};

const billionTokens = 1_000_000_000;
const halfBillionTokens = 500_000_000;
const globalModelMeta = new Map();

(usage.models || []).forEach((model) => {
  if (model?.name) globalModelMeta.set(model.name, model);
});

function setText(element, value) {
  if (element) element.textContent = value;
}

function setHtml(element, value) {
  if (element) element.innerHTML = value;
}

function colorForModel(name) {
  if (modelColors.has(name)) return modelColors.get(name);
  let hash = 0;
  for (const char of String(name)) {
    hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  }
  const color = modelPalette[hash % modelPalette.length];
  modelColors.set(name, color);
  return color;
}

function isVisibleModelName(name) {
  return String(name || "").toLowerCase() !== "unknown";
}

function visibleModels(models = []) {
  return models.filter((model) => isVisibleModelName(model.name));
}

function compactNumber(value = 0) {
  return new Intl.NumberFormat("en-US", {
    notation: "compact",
    maximumFractionDigits: value >= 10_000_000 ? 1 : 2,
  }).format(value);
}

function fullNumber(value = 0) {
  return new Intl.NumberFormat("en-US").format(value);
}

function formatDate(date) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
  }).format(new Date(`${date}T12:00:00`));
}

function formatDateLong(date) {
  if (!date) return "--";
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(new Date(`${date}T12:00:00`));
}

function formatGenerated(value) {
  if (!value) return "--";
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

function durationLabel(seconds = 0) {
  if (!seconds) return "0m";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const r = minutes % 60;
  return r ? `${hours}h ${r}m` : `${hours}h`;
}

function durationHoursLabel(seconds = 0) {
  const hours = Math.round(Number(seconds || 0) / 3600);
  if (hours < 1) return durationLabel(seconds);
  return `${hours} ${hours === 1 ? "hour" : "hours"}`;
}

function percentLabel(value = 0, total = 0) {
  if (!total) return "0%";
  const raw = (value / total) * 100;
  if (value > 0 && raw < 1) return "<1%";
  return `${Math.round(raw)}%`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function providerFromValue(value) {
  if (!value) return "";
  if (typeof value === "string") return value;
  return value.name || value.label || value.id || value.provider || value.providerName || "";
}

function providerFromRegistry(modelName) {
  const registry = usage.providers || usage.providerMetadata || usage.modelProviders;
  if (!registry) return "";
  if (Array.isArray(registry)) {
    const match = registry.find((entry) => entry.name === modelName || entry.model === modelName || entry.modelName === modelName);
    return providerFromValue(match);
  }
  return providerFromValue(registry[modelName]);
}

function providerLabel(model = {}) {
  const globalModel = globalModelMeta.get(model.name);
  return (
    providerFromValue(model.provider || model.providerName)
    || providerFromValue(globalModel?.provider || globalModel?.providerName)
    || providerFromRegistry(model.name)
  );
}

function modelTitle(model = {}) {
  const provider = providerLabel(model);
  return provider ? `${model.name} (${provider})` : model.name;
}

function emptyTotals() {
  return {
    totalTokens: 0,
    inputTokens: 0,
    cachedInputTokens: 0,
    freshInputTokens: 0,
    outputTokens: 0,
    reasoningOutputTokens: 0,
    modelCalls: 0,
  };
}

function addTotals(target, source = {}) {
  Object.keys(emptyTotals()).forEach((key) => {
    target[key] += Number(source[key] || 0);
  });
  return target;
}

function getRangeDays() {
  const days = usage.days || [];
  if (state.range === "all") return days;
  return days.slice(-Number(state.range));
}

function sumDays(days) {
  return days.reduce((acc, day) => addTotals(acc, day), emptyTotals());
}

function sumModels(days) {
  const byModel = new Map();
  days.forEach((day) => {
    visibleModels(day.models || []).forEach((model) => {
      const provider = providerLabel(model);
      if (!byModel.has(model.name)) {
        byModel.set(model.name, { ...emptyTotals(), provider });
      } else if (provider && byModel.get(model.name).provider && byModel.get(model.name).provider !== provider) {
        byModel.get(model.name).provider = "Mixed";
      }
      addTotals(byModel.get(model.name), model);
    });
  });
  return [...byModel.entries()]
    .map(([name, totals]) => ({ name, ...totals }))
    .sort((a, b) => b.totalTokens - a.totalTokens);
}

function topModelForDay(day) {
  return visibleModels(day.models || [])[0] || { name: "unattributed", totalTokens: 0 };
}

function maxDayBy(days, key) {
  return days.reduce((best, day) => {
    const value = Number(day?.[key] || 0);
    const bestValue = Number(best?.[key] || 0);
    if (!best || value > bestValue) return day;
    if (value === bestValue && String(day?.date || "") > String(best?.date || "")) return day;
    return best;
  }, null);
}

function pluralWord(value, singular, plural = `${singular}s`) {
  return value === 1 ? singular : plural;
}

function buildMoments() {
  const days = usage.days || [];
  const models = [...(usage.models || [])].sort(
    (a, b) => Number(b.totalTokens || 0) - Number(a.totalTokens || 0),
  );
  const unknownModel = models.find((model) => model.name === "unknown");
  const unknownModelCalls = Number(
    usage.stats?.unknownModelEvents || unknownModel?.modelCalls || 0,
  );
  const leader = models[0] || null;
  const runnerUp = models[1] || null;
  const gapTokens = leader && runnerUp
    ? Number(leader.totalTokens || 0) - Number(runnerUp.totalTokens || 0)
    : 0;

  return {
    days,
    totalTokens: Number(usage.totals?.totalTokens || 0),
    totalCalls: Number(usage.totals?.modelCalls || 0),
    peakDay: maxDayBy(days, "totalTokens"),
    longestDay: maxDayBy(days, "sessionDurationSeconds"),
    callsDay: maxDayBy(days, "modelCalls"),
    billionDays: days.filter((day) => Number(day.totalTokens || 0) >= billionTokens),
    halfBillionDays: days.filter((day) => Number(day.totalTokens || 0) >= halfBillionTokens),
    unknownModelCalls,
    modelRace: {
      leader,
      runnerUp,
      gapTokens,
      gapShare: runnerUp?.totalTokens ? gapTokens / Number(runnerUp.totalTokens) : 0,
    },
  };
}

const moments = buildMoments();

function shortDateList(days, limit = 3) {
  if (!days.length) return "none";
  const names = days.slice(0, limit).map((day) => formatDate(day.date));
  const remaining = days.length - names.length;
  return remaining > 0 ? `${names.join(", ")} and ${remaining} more` : names.join(", ");
}

function modelRaceLine(race = moments.modelRace) {
  if (!race.leader) return "No model race yet.";
  if (!race.runnerUp) return `${race.leader.name} is running unopposed.`;
  const gapPercent = race.gapShare > 0 && race.gapShare < 0.01
    ? "<1"
    : String(Math.round(race.gapShare * 100));
  return `${race.leader.name} leads ${race.runnerUp.name} by ${compactNumber(race.gapTokens)} tokens (${gapPercent}% over second place).`;
}

function headlineLine() {
  const peak = moments.peakDay;
  if (!peak) return "No token events yet. Suspense is cheap.";
  return `${compactNumber(moments.totalTokens)} tokens across ${fullNumber(moments.days.length)} logged days.`;
}

function incidentTone(day) {
  if (!day) return "No incident selected.";
  if (Number(day.totalTokens || 0) >= billionTokens) {
    return "This day cleared 1B tokens. The y-axis needed a meeting.";
  }
  if (Number(day.totalTokens || 0) >= halfBillionTokens) {
    return "This day cleared 500M tokens and still tried to look casual.";
  }
  if (Number(day.sessionDurationSeconds || 0) >= 20 * 60 * 60) {
    return "The session length nearly ate the whole calendar square.";
  }
  if (Number(day.modelCalls || 0) >= 5000) {
    return "Call volume crossed into queue-management territory.";
  }
  return "A smaller day by this chart's standards, which is already a strange sentence.";
}

function updateSelectedIncident(day = moments.peakDay, source = "Peak Day") {
  if (!els.selectedIncident) return;
  if (!day) {
    setHtml(els.selectedIncident, "<p>No token incidents found.</p>");
    return;
  }

  const top = topModelForDay(day);
  const cachedShare = percentLabel(day.cachedInputTokens, day.inputTokens);
  setHtml(
    els.selectedIncident,
    `
      <article class="incident-card">
        <span class="incident-source">${escapeHtml(source)}</span>
        <strong>${formatDateLong(day.date)}</strong>
        <p>${escapeHtml(incidentTone(day))}</p>
        <dl class="incident-stats">
          <div><dt>Tokens</dt><dd>${fullNumber(day.totalTokens)}</dd></div>
          <div><dt>Session</dt><dd>${durationLabel(day.sessionDurationSeconds)}</dd></div>
          <div><dt>Calls</dt><dd>${fullNumber(day.modelCalls)}</dd></div>
          <div><dt>Top Model</dt><dd>${escapeHtml(top.name)}</dd></div>
          <div><dt>Top Tokens</dt><dd>${compactNumber(top.totalTokens)}</dd></div>
          <div><dt>Cached Input</dt><dd>${cachedShare}</dd></div>
        </dl>
      </article>
    `,
  );
}

function tickerItems() {
  const peak = moments.peakDay;
  const longest = moments.longestDay;
  const calls = moments.callsDay;
  return [
    peak && {
      label: "Peak day",
      value: compactNumber(peak.totalTokens),
      detail: `${formatDate(peak.date)} carried ${fullNumber(peak.totalTokens)} tokens.`,
    },
    longest && {
      label: "Longest session",
      value: durationLabel(longest.sessionDurationSeconds),
      detail: `${formatDate(longest.date)} held the line the longest.`,
    },
    calls && {
      label: "Most calls",
      value: fullNumber(calls.modelCalls),
      detail: `${formatDate(calls.date)} logged the busiest call count.`,
    },
    {
      label: "Billion-token days",
      value: fullNumber(moments.billionDays.length),
      detail: shortDateList(moments.billionDays),
    },
    {
      label: "500M+ days",
      value: fullNumber(moments.halfBillionDays.length),
      detail: shortDateList(moments.halfBillionDays),
    },
    {
      label: "Model race",
      value: moments.modelRace.leader?.name || "--",
      detail: modelRaceLine(),
    },
    {
      label: "Unknown calls",
      value: fullNumber(moments.unknownModelCalls),
      detail: moments.unknownModelCalls
        ? "Attribution leaked through a crack."
        : "Every counted call has a model label.",
    },
  ].filter(Boolean);
}

function updateIncidentTicker() {
  if (!els.incidentTicker) return;
  setHtml(
    els.incidentTicker,
    tickerItems()
      .map(
        (item) => `
          <div class="ticker-item">
            <span>${escapeHtml(item.label)}</span>
            <strong>${escapeHtml(item.value)}</strong>
            <small>${escapeHtml(item.detail)}</small>
          </div>
        `,
      )
      .join(""),
  );
}

function achievementItems() {
  const peak = moments.peakDay;
  const longest = moments.longestDay;
  const calls = moments.callsDay;
  const unknownDetail = moments.unknownModelCalls
    ? `${fullNumber(moments.unknownModelCalls)} calls escaped model attribution.`
    : "No unknown-model calls in the counted set.";

  return [
    {
      title: "Total Stack",
      value: compactNumber(moments.totalTokens),
      detail: "The receipt has reached budget-review length.",
    },
    peak && {
      title: "Peak Day",
      value: compactNumber(peak.totalTokens),
      detail: `${formatDateLong(peak.date)} put the chart on notice.`,
    },
    longest && {
      title: "Longest Session",
      value: durationLabel(longest.sessionDurationSeconds),
      detail: `${formatDateLong(longest.date)} nearly became a full calendar block.`,
    },
    calls && {
      title: "Call Spike",
      value: fullNumber(calls.modelCalls),
      detail: `${formatDateLong(calls.date)} had the busiest model-call queue.`,
    },
    {
      title: "Billion Days",
      value: fullNumber(moments.billionDays.length),
      detail: `${shortDateList(moments.billionDays)} cleared the top threshold.`,
    },
    {
      title: "500M+ Days",
      value: fullNumber(moments.halfBillionDays.length),
      detail: `${fullNumber(moments.halfBillionDays.length)} ${pluralWord(moments.halfBillionDays.length, "day")} crossed half a billion tokens.`,
    },
    {
      title: "Top Model Race",
      value: moments.modelRace.leader?.name || "--",
      detail: modelRaceLine(),
    },
    {
      title: "Unknown Model Calls",
      value: fullNumber(moments.unknownModelCalls),
      detail: unknownDetail,
    },
  ].filter(Boolean);
}

function updateAchievements() {
  if (!els.achievementGrid) return;
  const items = achievementItems();
  setText(
    els.achievementCaption,
    `${fullNumber(items.length)} receipts unlocked from ${fullNumber(moments.days.length)} logged days.`,
  );
  setHtml(
    els.achievementGrid,
    items
      .map(
        (item) => `
          <article class="achievement-card">
            <span>${escapeHtml(item.title)}</span>
            <strong>${escapeHtml(item.value)}</strong>
            <p>${escapeHtml(item.detail)}</p>
          </article>
        `,
      )
      .join(""),
  );
}

function updateHeroReceipts() {
  const peak = moments.peakDay;
  const longest = moments.longestDay;
  const calls = moments.callsDay;

  setText(els.heroTotal, compactNumber(moments.totalTokens));
  setText(els.heroCaption, headlineLine());
  setText(els.heroPeakTokens, peak ? compactNumber(peak.totalTokens) : "--");
  setText(els.heroPeakDate, peak ? formatDateLong(peak.date) : "--");
  setText(
    els.heroLongestDuration,
    longest ? durationLabel(longest.sessionDurationSeconds) : "--",
  );
  setText(els.heroLongestDate, longest ? formatDateLong(longest.date) : "--");
  setText(els.heroCalls, calls ? fullNumber(calls.modelCalls) : compactNumber(moments.totalCalls));
  setText(
    els.heroCallCaption,
    calls ? `Peak call day on ${formatDateLong(calls.date)}` : "--",
  );
}

function updatePersonalityLayer() {
  updateHeroReceipts();
  updateIncidentTicker();
  updateAchievements();
  updateSelectedIncident(moments.peakDay, "Peak Day");
}

function updateSummary() {
  const days = usage.days || [];
  const latest = days.at(-1);
  const topModel = (usage.models || [])[0];
  const totalTokens = Number(usage.totals?.totalTokens || 0);

  setText(els.generatedAt, formatGenerated(usage.generatedAt));
  setText(els.totalTokens, compactNumber(totalTokens));
  setText(els.dateSpan, `${formatDateLong(usage.firstDate)} to ${formatDateLong(usage.lastDate)}`);
  setText(els.todayTokens, latest ? compactNumber(latest.totalTokens) : "--");
  setText(els.todayCalls, latest ? `${fullNumber(latest.modelCalls)} calls` : "--");
  setText(els.durationValue, latest ? durationLabel(latest.sessionDurationSeconds) : "--");
  setText(els.durationDate, latest ? formatDateLong(latest.date) : "--");
  setText(els.topModel, topModel ? topModel.name : "--");
  setText(
    els.topModelShare,
    topModel ? `${percentLabel(topModel.totalTokens, totalTokens)} of total` : "--",
  );
}

function updateModelMix(days) {
  const modelRows = sumModels(days);
  const total = sumDays(days).totalTokens;

  setHtml(
    els.modelMix,
    modelRows
      .slice(0, 8)
      .map((model) => {
        const width = total ? Math.max((model.totalTokens / total) * 100, 1) : 0;
        const provider = providerLabel(model);
        return `
          <div class="model-row">
            <div>
              <span title="${escapeHtml(modelTitle(model))}">
                <i style="background:${colorForModel(model.name)}"></i>
                <span class="model-label">${escapeHtml(model.name)}</span>
              </span>
              <strong>${compactNumber(model.totalTokens)}</strong>
            </div>
            <div class="track"><b style="width:${width}%; background:${colorForModel(model.name)}"></b></div>
            <small>${provider ? `${escapeHtml(provider)} &middot; ` : ""}${percentLabel(model.totalTokens, total)} &middot; ${fullNumber(model.modelCalls)} calls</small>
          </div>
        `;
      })
      .join(""),
  );
}

function updateCapture(days) {
  const rangeTotals = sumDays(days);
  const items = [
    ["Logged Days", fullNumber(days.length)],
    ["Model Calls", fullNumber(rangeTotals.modelCalls)],
    ["Session Files", fullNumber(usage.stats?.sessionFiles || 0)],
    ["Counted Events", fullNumber(usage.stats?.countedModelCalls || 0)],
    ["Deduped Events", fullNumber(usage.stats?.duplicateCumulativeEvents || 0)],
    ["Unknown Models", fullNumber(usage.stats?.unknownModelEvents || 0)],
  ];

  setHtml(
    els.captureMeta,
    items
      .map(
        ([label, value]) => `
          <div>
            <dt>${label}</dt>
            <dd>${value}</dd>
          </div>
        `,
      )
      .join(""),
  );
}

function setupCanvas() {
  const canvas = els.chart;
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(Math.floor(rect.width * dpr), 1);
  canvas.height = Math.max(Math.floor(rect.height * dpr), 1);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, width: rect.width, height: rect.height };
}

function drawEmptyChart(ctx, width, height) {
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#11181d";
  ctx.fillRect(0, 0, width, height);
  ctx.fillStyle = "#8da09b";
  ctx.font = "14px system-ui, sans-serif";
  ctx.textAlign = "center";
  ctx.fillText("No token events found", width / 2, height / 2);
}

function rgba(hex, alpha) {
  const clean = String(hex).replace("#", "");
  const value = Number.parseInt(clean.length === 3
    ? clean.split("").map((char) => char + char).join("")
    : clean, 16);
  const red = (value >> 16) & 255;
  const green = (value >> 8) & 255;
  const blue = value & 255;
  return `rgba(${red}, ${green}, ${blue}, ${alpha})`;
}

function roundedRect(ctx, x, y, width, height, radius) {
  const r = Math.min(radius, width / 2, height / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + width - r, y);
  ctx.quadraticCurveTo(x + width, y, x + width, y + r);
  ctx.lineTo(x + width, y + height - r);
  ctx.quadraticCurveTo(x + width, y + height, x + width - r, y + height);
  ctx.lineTo(x + r, y + height);
  ctx.quadraticCurveTo(x, y + height, x, y + height - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

function chartY(value, maxValue, pad, plotH) {
  return pad.top + plotH - (Number(value || 0) / maxValue) * plotH;
}

function drawStar(ctx, cx, cy, outerRadius = 6, innerRadius = 2.8) {
  ctx.beginPath();
  for (let point = 0; point < 10; point += 1) {
    const angle = -Math.PI / 2 + point * (Math.PI / 5);
    const radius = point % 2 === 0 ? outerRadius : innerRadius;
    const x = cx + Math.cos(angle) * radius;
    const y = cy + Math.sin(angle) * radius;
    if (point === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.closePath();
}

function drawPillLabel(ctx, text, x, y, options = {}) {
  const {
    align = "left",
    color = "#d7ff45",
    background = "rgba(8, 10, 10, 0.82)",
    bounds = null,
    star = false,
  } = options;
  ctx.save();
  ctx.font = "800 11px SFMono-Regular, Consolas, monospace";
  const paddingX = 8;
  const paddingY = 5;
  const starSpace = star ? 18 : 0;
  const metrics = ctx.measureText(text);
  const width = metrics.width + paddingX * 2 + starSpace;
  const height = 22;
  let left = align === "right" ? x - width : x;
  if (bounds) {
    left = Math.min(Math.max(left, bounds.left), bounds.right - width);
    y = Math.min(Math.max(y, bounds.top + height / 2), bounds.bottom - height / 2);
  }
  roundedRect(ctx, left, y - height / 2, width, height, 5);
  ctx.fillStyle = background;
  ctx.fill();
  ctx.strokeStyle = rgba(color, 0.5);
  ctx.stroke();
  if (star) {
    drawStar(ctx, left + paddingX + 6, y, 5.5, 2.6);
    ctx.fillStyle = color;
    ctx.fill();
  }
  ctx.fillStyle = color;
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  ctx.fillText(text, left + paddingX + starSpace, y);
  ctx.restore();
}

function drawChart() {
  const days = getRangeDays();
  const modelRows = sumModels(days);
  const { ctx, width, height } = setupCanvas();

  if (!days.length) {
    drawEmptyChart(ctx, width, height);
    els.tooltip.hidden = true;
    return;
  }

  const compact = width < 620;
  const pad = compact
    ? { top: 44, right: 48, bottom: 54, left: 54 }
    : { top: 54, right: 82, bottom: 62, left: 76 };
  const plotW = Math.max(width - pad.left - pad.right, 1);
  const plotH = Math.max(height - pad.top - pad.bottom, 1);
  const step = plotW / days.length;
  const columnWidth = Math.max(Math.min(step * 0.62, compact ? 14 : 22), days.length > 80 ? 2 : 5);
  const maxTokens = Math.max(...days.map((day) => day.totalTokens || 0), 1);
  const tokenMax = Math.max(maxTokens * 1.08, 1);
  const maxDuration = Math.max(...days.map((day) => day.sessionDurationSeconds || 0), 60 * 60);
  const durationMax = Math.max(maxDuration, 24 * 60 * 60);
  const orderedModels = modelRows.map((model) => model.name);
  const hoverIndex = state.hoveredIndex;

  state.chartGeometry = { pad, plotW, plotH, step, columnWidth };

  ctx.clearRect(0, 0, width, height);
  const canvasGradient = ctx.createLinearGradient(0, 0, width, height);
  canvasGradient.addColorStop(0, "#0c1418");
  canvasGradient.addColorStop(0.58, "#071015");
  canvasGradient.addColorStop(1, "#050708");
  ctx.fillStyle = canvasGradient;
  ctx.fillRect(0, 0, width, height);

  const plotGradient = ctx.createLinearGradient(0, pad.top, 0, pad.top + plotH);
  plotGradient.addColorStop(0, "rgba(98, 216, 255, 0.075)");
  plotGradient.addColorStop(0.52, "rgba(95, 240, 178, 0.025)");
  plotGradient.addColorStop(1, "rgba(0, 0, 0, 0.22)");
  roundedRect(ctx, pad.left, pad.top, plotW, plotH, 8);
  ctx.fillStyle = plotGradient;
  ctx.fill();

  if (hoverIndex !== null && days[hoverIndex]) {
    const hoverX = pad.left + hoverIndex * step;
    const hoverGradient = ctx.createLinearGradient(hoverX, pad.top, hoverX + step, pad.top);
    hoverGradient.addColorStop(0, "rgba(215, 255, 69, 0)");
    hoverGradient.addColorStop(0.5, "rgba(215, 255, 69, 0.13)");
    hoverGradient.addColorStop(1, "rgba(215, 255, 69, 0)");
    ctx.fillStyle = hoverGradient;
    ctx.fillRect(hoverX, pad.top, step, plotH);
  }

  ctx.strokeStyle = "rgba(137, 158, 159, 0.2)";
  ctx.lineWidth = 1;
  ctx.fillStyle = "#7f8f8b";
  ctx.font = "700 12px SFMono-Regular, Consolas, monospace";
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";

  for (let i = 0; i <= 4; i += 1) {
    const y = pad.top + (plotH / 4) * i;
    const tokenValue = tokenMax - (tokenMax / 4) * i;
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(width - pad.right, y);
    ctx.stroke();
    ctx.fillText(compactNumber(tokenValue), pad.left - 10, y);
  }

  [halfBillionTokens, billionTokens].forEach((threshold) => {
    if (threshold >= tokenMax) return;
    const y = chartY(threshold, tokenMax, pad, plotH);
    ctx.save();
    ctx.setLineDash([7, 7]);
    ctx.strokeStyle = threshold >= billionTokens
      ? "rgba(215, 255, 69, 0.58)"
      : "rgba(255, 191, 71, 0.42)";
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(width - pad.right, y);
    ctx.stroke();
    ctx.restore();
  });

  ctx.textBaseline = "middle";
  ctx.textAlign = "left";
  ctx.fillStyle = "#93a29f";
  ctx.fillText(durationLabel(durationMax), width - pad.right + 12, pad.top);
  ctx.fillText("0m", width - pad.right + 12, pad.top + plotH);

  days.forEach((day, index) => {
    const x = pad.left + index * step + (step - columnWidth) / 2;
    let yBase = pad.top + plotH;
    const modelMap = new Map(visibleModels(day.models || []).map((model) => [model.name, model]));

    ctx.save();
    roundedRect(ctx, x, pad.top, columnWidth, plotH, columnWidth / 2);
    ctx.fillStyle = "rgba(255, 255, 255, 0.025)";
    ctx.fill();
    ctx.restore();

    orderedModels.forEach((modelName) => {
      const value = Number(modelMap.get(modelName)?.totalTokens || 0);
      if (!value) return;
      const segmentHeight = Math.max((value / tokenMax) * plotH, 1.25);
      yBase -= segmentHeight;
      const color = colorForModel(modelName);
      ctx.save();
      ctx.shadowColor = rgba(color, index === hoverIndex ? 0.7 : 0.3);
      ctx.shadowBlur = index === hoverIndex ? 16 : 8;
      const barGradient = ctx.createLinearGradient(0, yBase, 0, yBase + segmentHeight);
      barGradient.addColorStop(0, rgba(color, index === hoverIndex ? 1 : 0.9));
      barGradient.addColorStop(1, rgba(color, index === hoverIndex ? 0.72 : 0.55));
      roundedRect(ctx, x, yBase, columnWidth, segmentHeight, Math.min(4, columnWidth / 2));
      ctx.fillStyle = barGradient;
      ctx.fill();
      ctx.restore();
    });

    if (index === hoverIndex) {
      ctx.save();
      roundedRect(ctx, x - 3, yBase - 3, columnWidth + 6, pad.top + plotH - yBase + 6, 6);
      ctx.strokeStyle = "rgba(245, 241, 232, 0.72)";
      ctx.lineWidth = 1.5;
      ctx.stroke();
      ctx.restore();
    }
  });

  const sessionPoints = days.map((day, index) => ({
    x: pad.left + index * step + step / 2,
    y: chartY(day.sessionDurationSeconds || 0, durationMax, pad, plotH),
    day,
  }));

  ctx.save();
  ctx.strokeStyle = "rgba(255, 191, 71, 0.22)";
  ctx.lineWidth = compact ? 6 : 8;
  ctx.shadowColor = "rgba(255, 191, 71, 0.42)";
  ctx.shadowBlur = 18;
  ctx.beginPath();
  sessionPoints.forEach(({ x, y }, index) => {
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
  ctx.restore();

  ctx.strokeStyle = "#ffd16a";
  ctx.lineWidth = compact ? 2.4 : 3.2;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.beginPath();
  sessionPoints.forEach(({ x, y }, index) => {
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();

  sessionPoints.forEach(({ x, y, day }) => {
    if (Number(day.sessionDurationSeconds || 0) < 20 * 60 * 60 && day !== days[hoverIndex]) return;
    ctx.beginPath();
    ctx.fillStyle = "#ffd16a";
    ctx.strokeStyle = "rgba(6, 7, 9, 0.85)";
    ctx.lineWidth = 2;
    ctx.arc(x, y, day === days[hoverIndex] ? 6 : 4.5, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  });

  const recordBounds = {
    left: pad.left + 8,
    right: width - pad.right - 8,
    top: pad.top + 8,
    bottom: pad.top + plotH - 8,
  };
  const rangePeakDay = maxDayBy(days, "totalTokens");
  const rangeLongestDay = maxDayBy(days, "sessionDurationSeconds");
  const peakIndex = days.findIndex((day) => day.date === rangePeakDay?.date);
  if (!compact && peakIndex >= 0 && rangePeakDay) {
    const day = days[peakIndex];
    drawPillLabel(ctx, `Most tokens: ${compactNumber(day.totalTokens)}`, recordBounds.right, recordBounds.top + 18, {
      align: "right",
      color: "#d7ff45",
      background: "rgba(8, 10, 10, 0.88)",
      bounds: recordBounds,
      star: true,
    });
  }

  const longestIndex = days.findIndex((day) => day.date === rangeLongestDay?.date);
  if (!compact && longestIndex >= 0 && rangeLongestDay) {
    const day = days[longestIndex];
    drawPillLabel(ctx, `Longest session: ${durationHoursLabel(day.sessionDurationSeconds)}`, recordBounds.right, recordBounds.top + 46, {
      align: "right",
      color: "#ffbf47",
      background: "rgba(8, 10, 10, 0.88)",
      bounds: recordBounds,
      star: true,
    });
  }

  if (hoverIndex !== null && days[hoverIndex]) {
    const x = pad.left + hoverIndex * step + step / 2;
    const y = chartY(days[hoverIndex].sessionDurationSeconds || 0, durationMax, pad, plotH);
    ctx.strokeStyle = "rgba(215, 255, 69, 0.62)";
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    ctx.moveTo(x, pad.top);
    ctx.lineTo(x, pad.top + plotH);
    ctx.stroke();
    ctx.fillStyle = "#fff3b0";
    ctx.beginPath();
    ctx.arc(x, y, 6, 0, Math.PI * 2);
    ctx.fill();
  }

  ctx.fillStyle = "#8fa09c";
  ctx.font = "700 12px SFMono-Regular, Consolas, monospace";
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  const tickCount = Math.min(compact ? 4 : 6, days.length);
  for (let i = 0; i < tickCount; i += 1) {
    const index = Math.round((i / Math.max(tickCount - 1, 1)) * (days.length - 1));
    const x = pad.left + index * step + step / 2;
    ctx.fillText(formatDate(days[index].date), x, height - 28);
  }

  setText(els.rangeCaption, `${fullNumber(days.length)} days | Session length: first token to last token each day`);
  els.rangeCaption.title = "Session length is the time between the first counted token event and the last counted token event in each local day.";
  updateTooltip(days);
}

function updateTooltip(days) {
  if (state.hoveredIndex === null || !days[state.hoveredIndex]) {
    els.tooltip.hidden = true;
    return;
  }

  const day = days[state.hoveredIndex];
  const chartRect = els.chart.getBoundingClientRect();
  const left = Math.min(Math.max(state.pointerX || chartRect.width / 2, 190), chartRect.width - 190);
  const top = Math.min(Math.max(state.pointerY || 80, 130), chartRect.height - 28);
  const rows = visibleModels(day.models || [])
    .slice(0, 5)
    .map(
      (model) => `
        <span class="tip-row" title="${escapeHtml(modelTitle(model))}">
          <i style="background:${colorForModel(model.name)}"></i>
          <em>${escapeHtml(model.name)}</em>
          <b>${compactNumber(model.totalTokens)}</b>
        </span>
      `,
    )
    .join("");

  els.tooltip.hidden = false;
  els.tooltip.style.left = `${left}px`;
  els.tooltip.style.top = `${top}px`;
  els.tooltip.innerHTML = `
    <span class="tip-date">${formatDateLong(day.date)}</span>
    <strong class="tip-total">${fullNumber(day.totalTokens)} tokens</strong>
    <div class="tip-metrics">
      <span><b>${durationLabel(day.sessionDurationSeconds)}</b><em>session length</em></span>
      <span><b>${fullNumber(day.modelCalls)}</b><em>calls</em></span>
    </div>
    <div class="tip-models">${rows}</div>
  `;
}

function updateTable(days) {
  if (!els.dailyRows) return;
  setText(els.tableCaption, `${fullNumber(days.length)} logged days, newest first`);
  setHtml(
    els.dailyRows,
    [...days]
      .reverse()
      .map((day) => {
        const top = topModelForDay(day);
        return `
          <tr data-date="${day.date}">
            <td>${formatDateLong(day.date)}</td>
            <td>${fullNumber(day.totalTokens)}</td>
            <td>${durationLabel(day.sessionDurationSeconds)}</td>
            <td><span class="table-model"><i style="background:${colorForModel(top.name)}"></i>${escapeHtml(top.name)}</span></td>
            <td>${fullNumber(top.totalTokens)}</td>
            <td>${fullNumber(day.modelCalls)}</td>
          </tr>
        `;
      })
      .join(""),
  );
}

function render() {
  const days = getRangeDays();
  state.hoveredIndex = null;
  updatePersonalityLayer();
  updateSummary();
  updateModelMix(days);
  updateCapture(days);
  updateTable(days);
  drawChart();
}

document.querySelectorAll("[data-range]").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll("[data-range]").forEach((btn) => btn.classList.remove("active"));
    button.classList.add("active");
    state.range = button.dataset.range;
    render();
  });
});

els.chart.addEventListener("mousemove", (event) => {
  const days = getRangeDays();
  const geometry = state.chartGeometry;
  if (!geometry || !days.length) return;

  const rect = els.chart.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  const index = Math.floor((x - geometry.pad.left) / geometry.step);
  state.pointerX = x;
  state.pointerY = y;
  state.hoveredIndex = index < 0 || index >= days.length ? null : index;
  drawChart();
});

els.chart.addEventListener("mouseleave", () => {
  state.hoveredIndex = null;
  drawChart();
});

if (els.dailyRows) {
  els.dailyRows.addEventListener("mouseover", (event) => {
    const row = event.target.closest("tr");
    if (!row) return;
    document.querySelectorAll("tbody tr").forEach((item) => item.classList.remove("selected"));
    row.classList.add("selected");
  });
}

window.addEventListener("resize", () => {
  drawChart();
});

render();
