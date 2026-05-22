const usage = window.AI_TOKEN_USAGE || window.CODEX_TOKEN_USAGE || {
  generatedAt: null,
  ownerHandle: null,
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
  metric: "total",
  heatmapPeriod: "12mo",
  showSessionLine: true,
  isolatedModel: null,
  hoveredIndex: null,
  pointerX: 0,
  pointerY: 0,
  chartGeometry: null,
};

const METRIC_META = {
  total: {
    buttonLabel: "TOTAL",
    label: "total tokens (incl. cache reads)",
    short: "total",
    unit: "total tokens",
    description: "Everything including cache reads. Inflated by 1M-context cache replays.",
    chartTitle: "Total Token Load",
    rangeLabel: "total tokens (incl. cache reads)",
    recordLabel: "Most total",
    tooltipLabel: "total tokens (incl. cache reads)",
  },
  new: {
    buttonLabel: "NEW",
    label: "new tokens (excl. cache reads)",
    short: "new",
    unit: "new tokens",
    description: "Output + fresh input + cache creation. Excludes cache reads.",
    chartTitle: "New Token Load",
    rangeLabel: "new tokens (excl. cache reads)",
    recordLabel: "Most new",
    tooltipLabel: "new tokens (excl. cache reads)",
  },
  output: {
    buttonLabel: "OUTPUT",
    label: "output tokens",
    short: "output",
    unit: "output tokens",
    description: "Tokens Claude actually generated. Full price. The honest number.",
    chartTitle: "Output Token Load",
    rangeLabel: "output tokens",
    recordLabel: "Most output",
    tooltipLabel: "output tokens",
  },
  cost: {
    buttonLabel: "$ COST",
    label: "estimated USD",
    short: "cost",
    unit: "spend",
    unitSuffix: "(est.)",
    description: "Approximate USD spend per day using data/pricing.js rates.",
    chartTitle: "Total Cost (est.)",
    rangeLabel: "estimated USD",
    recordLabel: "Most spend",
    tooltipLabel: "estimated USD",
  },
};

const PRICING = window.AI_PRICING || { default: { input: 0, cacheRead: 0, output: 0 }, models: {} };
const PLAN = window.AI_PLAN || { usdPerMonth: 0, label: "" };

function metricMeta(metric = state.metric) {
  return METRIC_META[metric] || METRIC_META.total;
}

function metricLabel(metric = state.metric) {
  return metricMeta(metric).label;
}

function metricSentenceLabel(metric = state.metric) {
  return metricLabel(metric).replace(/^./, (char) => char.toUpperCase());
}

function metricTooltipLabelHtml(metric = state.metric) {
  const meta = metricMeta(metric);
  if (metric === "total") return 'total tokens <small>(incl. cache reads)</small>';
  if (metric === "new") return 'new tokens <small>(excl. cache reads)</small>';
  return escapeHtml(meta.tooltipLabel);
}

function metricShort(metric = state.metric) {
  return metricMeta(metric).short;
}

function metricUnitHtml(metric = state.metric) {
  const meta = metricMeta(metric);
  const unit = escapeHtml(meta.unit);
  if (!meta.unitSuffix) return unit;
  return `${unit} <span class="hero-estimate">${escapeHtml(meta.unitSuffix)}</span>`;
}

function planCostForRange(days) {
  const perMonth = Number(PLAN.usdPerMonth || 0);
  if (!perMonth || !days?.length) return 0;
  return (perMonth / 30) * days.length;
}

function formatMultiplier(value) {
  if (!Number.isFinite(value)) return "--";
  if (value >= 100) return `${Math.round(value)}x`;
  if (value >= 10) return `${value.toFixed(0)}x`;
  return `${value.toFixed(1)}x`;
}
const pricingCache = new Map();

function lookupPricing(modelName) {
  if (!modelName) return PRICING.default;
  if (pricingCache.has(modelName)) return pricingCache.get(modelName);
  const models = PRICING.models || {};
  const lower = String(modelName).toLowerCase();
  let hit = models[modelName] || models[lower];
  if (!hit) {
    let bestKey = null;
    for (const key of Object.keys(models)) {
      const keyLower = key.toLowerCase();
      if (lower.startsWith(keyLower) && (!bestKey || keyLower.length > bestKey.length)) {
        bestKey = keyLower;
        hit = models[key];
      }
    }
  }
  const resolved = hit || PRICING.default;
  pricingCache.set(modelName, resolved);
  return resolved;
}

function costForUsage(usage = {}, pricing = PRICING.default) {
  const inputRate = Number(pricing.input || 0);
  const cacheReadRate = Number(pricing.cacheRead || 0);
  const cacheWriteRate = pricing.cacheWrite != null
    ? Number(pricing.cacheWrite)
    : inputRate * 1.25;
  const outputRate = Number(pricing.output || 0);
  const fresh = Number(usage.freshInputTokens || 0);
  const cacheCreation = Number(usage.cacheCreationTokens || 0);
  const rawInput = Math.max(fresh - cacheCreation, 0);
  const cacheRead = Number(usage.cachedInputTokens || 0);
  const output = Number(usage.outputTokens || 0) + Number(usage.reasoningOutputTokens || 0);
  return (
    rawInput * inputRate
    + cacheCreation * cacheWriteRate
    + cacheRead * cacheReadRate
    + output * outputRate
  ) / 1_000_000;
}

function costForModelRow(row = {}) {
  return costForUsage(row, lookupPricing(row.name));
}

function costForDay(day = {}) {
  const models = day.models || [];
  if (models.length) {
    return models.reduce((acc, model) => acc + costForModelRow(model), 0);
  }
  return costForUsage(day, PRICING.default);
}

function costForTotals(totals, days) {
  if (Array.isArray(days)) {
    return days.reduce((acc, day) => acc + costForDay(day), 0);
  }
  return costForUsage(totals, PRICING.default);
}

function metricValue(thing = {}, metric = state.metric) {
  if (metric === "cost") {
    if (Array.isArray(thing?.models) && thing.models.length) return costForDay(thing);
    if (thing && typeof thing.name === "string") return costForModelRow(thing);
    return costForUsage(thing, PRICING.default);
  }
  if (metric === "output") {
    return Number(thing.outputTokens || 0) + Number(thing.reasoningOutputTokens || 0);
  }
  if (metric === "new") {
    return (
      Number(thing.freshInputTokens || 0)
      + Number(thing.outputTokens || 0)
      + Number(thing.reasoningOutputTokens || 0)
    );
  }
  return Number(thing.totalTokens || 0);
}

const usdFull = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const usdCompact = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 2,
});

function formatMetric(value, mode = "compact") {
  if (state.metric === "cost") {
    return mode === "full" ? usdFull.format(value) : usdCompact.format(value);
  }
  return mode === "full" ? fullNumber(value) : compactNumber(value);
}

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
const OTHER_MODEL_LABEL = "unattributed";
const OTHER_MODEL_COLOR = "#8a9692";
(usage.models || []).forEach((model, index) => {
  modelColors.set(model.name, modelPalette[index % modelPalette.length]);
});

const els = {
  generatedDate: document.querySelector("#generatedDate"),
  generatedTime: document.querySelector("#generatedTime"),
  ownerHandle: document.querySelector("#ownerHandle"),
  totalTokens: document.querySelector("#totalTokens"),
  dateSpan: document.querySelector("#dateSpan"),
  todayTokens: document.querySelector("#todayTokens"),
  todayCalls: document.querySelector("#todayCalls"),
  durationValue: document.querySelector("#durationValue"),
  durationDate: document.querySelector("#durationDate"),
  topModel: document.querySelector("#topModel"),
  topModelShare: document.querySelector("#topModelShare"),
  chartTitle: document.querySelector("#chartTitle"),
  rangeCaption: document.querySelector("#rangeCaption"),
  tableCaption: document.querySelector("#tableCaption"),
  chart: document.querySelector("#dailyChart"),
  tooltip: document.querySelector("#chartTooltip"),
  sessionToggle: document.querySelector("#sessionToggle"),
  dailyRows: document.querySelector("#dailyRows"),
  modelMix: document.querySelector("#modelMix"),
  highlightGrid: document.querySelector("#highlightGrid"),
  captureMeta: document.querySelector("#captureMeta"),
  heroTotal: document.querySelector("#heroTotal"),
  heroTotalUnit: document.querySelector("#heroTotalUnit"),
  heroCaption: document.querySelector("#heroCaption"),
  planPill: document.querySelector("#planPill"),
  heatmapScroll: document.querySelector(".heatmap-scroll"),
  heatmapWrap: document.querySelector("#heatmapWrap"),
  heatmapMonths: document.querySelector("#heatmapMonths"),
  heatmapCaption: document.querySelector("#heatmapCaption"),
  heatmapLegend: document.querySelector("#heatmapLegend"),
  heatmapPeriod: document.querySelector("#heatmapPeriod"),
  heatmapTip: document.querySelector("#heatmapTip"),
  heatmapPanel: document.querySelector(".pattern-heatmap"),
  hoursWrap: document.querySelector("#hoursWrap"),
  hoursCaption: document.querySelector("#hoursCaption"),
  hoursTip: document.querySelector("#hoursTip"),
  hoursPanel: document.querySelector(".pattern-hours"),
  subagentWrap: document.querySelector("#subagentWrap"),
  subagentCaption: document.querySelector("#subagentCaption"),
  historyScroll: document.querySelector("#historyScroll"),
  historyCaption: document.querySelector("#historyCaption"),
  historyLegend: document.querySelector("#historyLegend"),
  historyTip: document.querySelector("#historyTip"),
  historyPanel: document.querySelector(".pattern-history"),
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
  if (name === OTHER_MODEL_LABEL) return OTHER_MODEL_COLOR;
  if (modelColors.has(name)) return modelColors.get(name);
  let hash = 0;
  for (const char of String(name)) {
    hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  }
  const color = modelPalette[hash % modelPalette.length];
  modelColors.set(name, color);
  return color;
}

const projectPalette = [
  "#62d8ff",
  "#f6bf63",
  "#ed6a8f",
  "#a78bfa",
  "#5ff0b2",
  "#ff8f5f",
  "#75a7ff",
  "#d6f35a",
  "#58d6c9",
  "#c991ff",
];
const projectColors = new Map();
let projectColorIndex = 0;

// Sequential assignment (not hashing) so distinct projects get distinct colors.
// Seed in token order first so the heaviest projects claim the clearest hues.
function colorForProject(name) {
  const key = name || "unknown";
  if (projectColors.has(key)) return projectColors.get(key);
  if (!name) {
    projectColors.set(key, "#7f8f8b");
    return "#7f8f8b";
  }
  const color = projectPalette[projectColorIndex % projectPalette.length];
  projectColorIndex += 1;
  projectColors.set(key, color);
  return color;
}

function isVisibleModelName(name) {
  return String(name || "").toLowerCase() !== "unknown";
}

function visibleModels(models = []) {
  return models.filter((model) => isVisibleModelName(model.name));
}

function hiddenModelTotals(days = []) {
  return days.reduce((totals, day) => {
    (day.models || []).forEach((model) => {
      if (isVisibleModelName(model.name)) return;
      addTotals(totals, model);
    });
    return totals;
  }, emptyTotals());
}

function chartSegmentsForDay(day, orderedModels, dayTotal) {
  const visible = visibleModels(day.models || []);
  const modelMap = new Map(visible.map((model) => [model.name, model]));
  const segments = [];
  let visibleTotal = 0;
  orderedModels.forEach((modelName) => {
    const modelEntry = modelMap.get(modelName);
    const value = modelEntry ? metricValue(modelEntry) : 0;
    if (!value) return;
    visibleTotal += value;
    segments.push({ name: modelName, value, color: colorForModel(modelName) });
  });
  const remainder = Math.max(Number(dayTotal || 0) - visibleTotal, 0);
  if (remainder > Math.max(Number(dayTotal || 0) * 0.001, 0.01)) {
    segments.push({ name: OTHER_MODEL_LABEL, value: remainder, color: OTHER_MODEL_COLOR });
  }
  return segments;
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

function formatGeneratedDate(value) {
  if (!value) return "--";
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
  }).format(new Date(value));
}

function formatGeneratedTime(value) {
  if (!value) return "--";
  return new Intl.DateTimeFormat("en-US", {
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

function ownerHandleLabel(value) {
  const handle = String(value || "").trim().replace(/^@+/, "");
  if (!handle) return "";
  return `@${handle}`;
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

function formatMoneyCompact(value = 0) {
  const amount = Number(value || 0);
  if (!Number.isFinite(amount) || amount <= 0) return "$0";
  if (amount >= 1_000_000) return `$${(amount / 1_000_000).toFixed(amount >= 10_000_000 ? 0 : 1)}M`;
  if (amount >= 1_000) return `$${(amount / 1_000).toFixed(amount >= 10_000 ? 0 : 1)}K`;
  return `$${Math.round(amount)}`;
}

// Street value reuses the single pricing source (data/pricing.js via
// costForModelRow), so the highlight and the $ Cost metric mode always agree.
function estimateStreetValue(models = usage.models || []) {
  return models.reduce((sum, model) => sum + costForModelRow(model), 0);
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

const SUBAGENT_ELIGIBLE_PROVIDERS = new Set(["codex", "claude code", "opencode"]);

function isSubagentEligibleProvider(model = {}) {
  return SUBAGENT_ELIGIBLE_PROVIDERS.has(String(providerLabel(model)).trim().toLowerCase());
}

function eligibleSubagentProviderTotal(days = []) {
  return days.reduce((total, day) => {
    const models = day.models || [];
    return total + models.reduce(
      (dayTotal, model) => dayTotal + (isSubagentEligibleProvider(model) ? metricValue(model) : 0),
      0,
    );
  }, 0);
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
    .sort((a, b) => metricValue(b) - metricValue(a));
}

function topModelForDay(day) {
  const models = visibleModels(day.models || []);
  if (!models.length) return { name: "unattributed", totalTokens: 0 };
  return [...models].sort((a, b) => metricValue(b) - metricValue(a))[0];
}

function maxDayBy(days, key) {
  const getValue = key === "totalTokens"
    ? (day) => metricValue(day)
    : (day) => Number(day?.[key] || 0);
  return days.reduce((best, day) => {
    const value = getValue(day);
    const bestValue = best ? getValue(best) : -Infinity;
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
    (a, b) => metricValue(b) - metricValue(a),
  );
  const unknownModel = models.find((model) => model.name === "unknown");
  const unknownModelCalls = Number(
    usage.stats?.unknownModelEvents || unknownModel?.modelCalls || 0,
  );
  const leader = models[0] || null;
  const runnerUp = models[1] || null;
  const leaderValue = leader ? metricValue(leader) : 0;
  const runnerUpValue = runnerUp ? metricValue(runnerUp) : 0;
  const gapTokens = leader && runnerUp ? leaderValue - runnerUpValue : 0;

  return {
    days,
    totalTokens: metricValue(usage.totals || {}),
    totalCalls: Number(usage.totals?.modelCalls || 0),
    peakDay: maxDayBy(days, "totalTokens"),
    longestDay: maxDayBy(days, "sessionDurationSeconds"),
    callsDay: maxDayBy(days, "modelCalls"),
    billionDays: days.filter((day) => metricValue(day) >= billionTokens),
    halfBillionDays: days.filter((day) => metricValue(day) >= halfBillionTokens),
    unknownModelCalls,
    modelRace: {
      leader,
      runnerUp,
      gapTokens,
      gapShare: runnerUpValue ? gapTokens / runnerUpValue : 0,
    },
  };
}

let moments = buildMoments();

// Highlights are computed from the selected date range (getRangeDays), the same
// per-day fields the importer attaches, so they track the chart's range selector
// instead of always showing all-time records.
function maxDayByField(days, field) {
  return days.reduce((best, day) => {
    const value = Number(day?.[field] || 0);
    if (value <= 0) return best;
    if (!best || value > Number(best[field] || 0)) return day;
    return best;
  }, null);
}

function buildHighlightItems() {
  const days = getRangeDays();
  const first = days[0]?.date;
  const last = days[days.length - 1]?.date;

  const street = estimateStreetValue(sumModels(days));
  const peak = maxDayByField(days, "totalTokens");

  const rangeSessions = (usage.sessions?.list || []).filter(
    (s) => first && last && s.startDate >= first && s.startDate <= last,
  );
  const longestSession = rangeSessions.reduce(
    (best, s) => (!best || Number(s.durationSeconds || 0) > Number(best.durationSeconds || 0) ? s : best),
    null,
  );
  const longestSessionSeconds = longestSession ? Number(longestSession.durationSeconds || 0) : 0;

  const concDay = maxDayByField(days, "peakConcurrentTerminals");
  const taskDay = maxDayByField(days, "longestTaskTurnSeconds");
  const toolDay = maxDayByField(days, "toolCallPileup");

  return [
    {
      label: "Street Value",
      value: formatMoneyCompact(street),
      detail: "API-grade tokens, rack-rate contraband.",
    },
    {
      label: "Terminal Swarm",
      value: concDay ? fullNumber(concDay.peakConcurrentTerminals) : "N/A",
      detail: concDay
        ? `${formatDateLong(concDay.date)} had the most AI coding terminals active at once.`
        : "Peak simultaneous Codex, Claude Code, and OpenCode lanes.",
    },
    {
      label: "Peak Day",
      value: peak ? compactNumber(peak.totalTokens) : "--",
      detail: peak ? `${formatDateLong(peak.date)} burned the most tokens.` : "Most tokens in one day.",
    },
    {
      label: "Longest Session",
      value: longestSessionSeconds > 0 ? durationHoursLabel(longestSessionSeconds) : "--",
      detail: longestSession
        ? `${formatDateLong(longestSession.startDate)} ran longest without a 2h+ break.`
        : "Longest continuous session.",
    },
    {
      label: "Longest Task Turn",
      value: taskDay ? durationLabel(taskDay.longestTaskTurnSeconds) : "N/A",
      detail: taskDay
        ? `${formatDateLong(taskDay.date)} had one agent on the clock.`
        : "Longest single agent run with a start and finish.",
    },
    {
      label: "Tool Pileup",
      value: toolDay ? fullNumber(toolDay.toolCallPileup) : "N/A",
      detail: toolDay
        ? `${formatDateLong(toolDay.date)} packed the most tool calls into one session.`
        : "Most tool calls packed into one session.",
    },
  ];
}

function updateHighlights() {
  if (!els.highlightGrid) return;
  setHtml(
    els.highlightGrid,
    buildHighlightItems()
      .map(
        (item) => `
          <article class="highlight-card">
            <span>${escapeHtml(item.label)}</span>
            <strong>${escapeHtml(item.value)}</strong>
            <p>${escapeHtml(item.detail)}</p>
          </article>
        `,
      )
      .join(""),
  );
}

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
  return `${formatMetric(moments.totalTokens)} ${metricLabel()} across ${fullNumber(moments.days.length)} logged days.`;
}

function incidentTone(day) {
  if (!day) return "No incident selected.";
  if (state.metric === "cost") {
    const usd = metricValue(day);
    if (usd >= 1000) return `This day cleared four figures of model spend. The CFO ticked.`;
    if (usd >= 250) return `This day spent more on tokens than on lunch.`;
    if (usd >= 50) return `A solid weekday at the model meter.`;
    return `A quiet day at the meter.`;
  }
  const value = metricValue(day);
  if (value >= billionTokens) {
    return `This day cleared 1B ${metricShort()} tokens. The y-axis needed a meeting.`;
  }
  if (value >= halfBillionTokens) {
    return `This day cleared 500M ${metricShort()} tokens and still tried to look casual.`;
  }
  if (Number(day.sessionDurationSeconds || 0) >= 20 * 60 * 60) {
    return "The active time nearly ate the whole calendar square.";
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
  const dayValue = metricValue(day);
  const topValue = metricValue(top);
  const tokenLabel = state.metric === "cost"
    ? "Spend"
    : (metricShort() === "total" ? "Tokens" : `${metricShort().replace(/^./, (c) => c.toUpperCase())} tokens`);
  const topLabel = state.metric === "cost" ? "Top Spend" : `Top ${tokenLabel}`;
  setHtml(
    els.selectedIncident,
    `
      <article class="incident-card">
        <span class="incident-source">${escapeHtml(source)}</span>
        <strong>${formatDateLong(day.date)}</strong>
        <p>${escapeHtml(incidentTone(day))}</p>
        <dl class="incident-stats">
          <div><dt>${tokenLabel}</dt><dd>${formatMetric(dayValue, "full")}</dd></div>
          <div><dt>Session</dt><dd>${durationLabel(day.sessionDurationSeconds)}</dd></div>
          <div><dt>Calls</dt><dd>${fullNumber(day.modelCalls)}</dd></div>
          <div><dt>Top Model</dt><dd>${escapeHtml(top.name)}</dd></div>
          <div><dt>${topLabel}</dt><dd>${formatMetric(topValue)}</dd></div>
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
      value: formatMetric(metricValue(peak)),
      detail: `${formatDate(peak.date)} carried ${formatMetric(metricValue(peak), "full")} ${metricLabel()}.`,
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
      title: state.metric === "output"
        ? "Output Stack"
        : state.metric === "new"
          ? "New-Tokens Stack"
          : state.metric === "cost"
            ? "Approx. Spend"
            : "Total Stack",
      value: formatMetric(moments.totalTokens),
      detail: `The receipt is measured in ${metricLabel()}.`,
    },
    peak && {
      title: "Peak Day",
      value: formatMetric(metricValue(peak)),
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
  const rangeDays = getRangeDays();

  setText(els.heroTotal, formatMetric(moments.totalTokens));
  setHtml(els.heroTotalUnit, metricUnitHtml());

  if (els.planPill) {
    const perMonth = Number(PLAN.usdPerMonth || 0);
    if (perMonth > 0 && rangeDays.length > 0) {
      const rangeCost = rangeDays.reduce((acc, day) => acc + metricValue(day, "cost"), 0);
      const planCost = planCostForRange(rangeDays);
      const ratio = planCost > 0 ? rangeCost / planCost : 0;
      const planLabel = PLAN.label || "plan";
      els.planPill.textContent = `${formatMultiplier(ratio)} your ${usdFull.format(perMonth)}/mo ${planLabel}`;
      els.planPill.hidden = false;
    } else {
      els.planPill.hidden = true;
    }
  }
  setText(els.heroCaption, headlineLine());
  setText(els.heroPeakTokens, peak ? formatMetric(metricValue(peak)) : "--");
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
  const sortedModels = [...(usage.models || [])].sort(
    (a, b) => metricValue(b) - metricValue(a),
  );
  const topModel = sortedModels[0];
  const totalTokens = metricValue(usage.totals || {});

  setText(els.generatedDate, formatGeneratedDate(usage.generatedAt));
  setText(els.generatedTime, formatGeneratedTime(usage.generatedAt));
  const ownerHandle = ownerHandleLabel(usage.ownerHandle);
  if (els.ownerHandle) {
    setText(els.ownerHandle, ownerHandle);
    els.ownerHandle.hidden = !ownerHandle;
  }
  setText(els.totalTokens, formatMetric(totalTokens));
  setText(els.dateSpan, `${formatDateLong(usage.firstDate)} to ${formatDateLong(usage.lastDate)}`);
  setText(els.todayTokens, latest ? formatMetric(metricValue(latest)) : "--");
  setText(els.todayCalls, latest ? `${fullNumber(latest.modelCalls)} calls` : "--");
  setText(els.durationValue, latest ? durationLabel(latest.sessionDurationSeconds) : "--");
  setText(els.durationDate, latest ? formatDateLong(latest.date) : "--");
  setText(els.topModel, topModel ? topModel.name : "--");
  setText(
    els.topModelShare,
    topModel ? `${percentLabel(metricValue(topModel), totalTokens)} of ${metricShort()}` : "--",
  );
}

function updateMetricControls() {
  document.querySelectorAll("[data-metric]").forEach((button) => {
    const meta = METRIC_META[button.dataset.metric];
    if (!meta) return;
    setText(button, meta.buttonLabel);
    button.title = meta.description;
    button.setAttribute("aria-label", `${meta.buttonLabel}: ${meta.description}`);
    button.classList.toggle("active", button.dataset.metric === state.metric);
  });
  setText(els.chartTitle, metricMeta().chartTitle);
}

function updateModelMix(days) {
  const modelRows = sumModels(days);
  const unattributedTotals = hiddenModelTotals(days);
  const unattributedValue = metricValue(unattributedTotals);
  const rowsWithFallback = unattributedValue > 0
    ? [
        ...modelRows,
        {
          ...unattributedTotals,
          name: OTHER_MODEL_LABEL,
          provider: "Unattributed",
          synthetic: true,
        },
      ].sort((a, b) => metricValue(b) - metricValue(a))
    : modelRows;
  const total = state.metric === "cost"
    ? rowsWithFallback.reduce((acc, m) => acc + metricValue(m), 0)
    : metricValue(sumDays(days));
  const availableHeight = Number(els.modelMix?.clientHeight || 0);
  const rowBudget = availableHeight ? Math.max(Math.floor(availableHeight / 43), 1) : 8;
  const visibleCount = Math.min(rowsWithFallback.length, rowBudget, 8);
  const visibleRows = rowsWithFallback.slice(0, visibleCount);

  if (els.modelMix) {
    els.modelMix.classList.toggle("sparse", visibleRows.length > 0 && visibleRows.length <= 3);
    els.modelMix.classList.toggle("single-model", visibleRows.length === 1);
    els.modelMix.classList.toggle("fill-space", visibleRows.length > 3);
    els.modelMix.classList.toggle("has-isolation", Boolean(state.isolatedModel));
  }

  setHtml(
    els.modelMix,
    visibleRows
      .map((model) => {
        const value = metricValue(model);
        const width = total ? Math.max((value / total) * 100, 1) : 0;
        const provider = providerLabel(model);
        const isIsolated = state.isolatedModel === model.name;
        const isSynthetic = Boolean(model.synthetic);
        const rowClass = `model-row${isIsolated ? " isolated" : ""}${isSynthetic ? " synthetic" : ""}`;
        const rowAttrs = isSynthetic
          ? `aria-disabled="true" title="Usage counted from logs that did not carry a model label"`
          : `data-model="${escapeHtml(model.name)}" role="button" tabindex="0" aria-pressed="${isIsolated}" title="Click to isolate ${escapeHtml(model.name)} in the chart"`;
        return `
          <div class="${rowClass}" ${rowAttrs}>
            <div>
              <span title="${escapeHtml(modelTitle(model))}">
                <i style="background:${colorForModel(model.name)}"></i>
                <span class="model-label">${escapeHtml(model.name)}</span>
              </span>
              <strong>${formatMetric(value)}</strong>
            </div>
            <div class="track"><b style="width:${width}%; background:${colorForModel(model.name)}"></b></div>
            <small>${provider ? `${escapeHtml(provider)} &middot; ` : ""}${percentLabel(value, total)} &middot; ${fullNumber(model.modelCalls)} calls</small>
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
  const dpr = window.devicePixelRatio || 1;
  const cssWidth = Math.max(canvas.clientWidth, 1);
  const cssHeight = Math.max(canvas.clientHeight, 1);
  const backingWidth = Math.max(Math.round(cssWidth * dpr), 1);
  const backingHeight = Math.max(Math.round(cssHeight * dpr), 1);
  if (canvas.width !== backingWidth) canvas.width = backingWidth;
  if (canvas.height !== backingHeight) canvas.height = backingHeight;
  const ctx = canvas.getContext("2d");
  ctx.imageSmoothingEnabled = false;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, width: cssWidth, height: cssHeight };
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

function px(value) {
  return Math.round(value) + 0.5;
}

function whole(value) {
  return Math.round(value);
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

function resolvePillLabelRect(ctx, text, x, y, options = {}) {
  const {
    align = "left",
    bounds = null,
    star = false,
  } = options;
  ctx.font = "900 12px SFMono-Regular, Consolas, monospace";
  const paddingX = 8;
  const starSpace = star ? 18 : 0;
  const metrics = ctx.measureText(text);
  const width = Math.ceil(metrics.width + paddingX * 2 + starSpace);
  const height = 24;
  let left = x;
  if (align === "right") left = x - width;
  if (align === "center") left = x - width / 2;
  if (bounds) {
    const maxLeft = Math.max(bounds.left, bounds.right - width);
    left = Math.min(Math.max(left, bounds.left), maxLeft);
    y = Math.min(Math.max(y, bounds.top + height / 2), bounds.bottom - height / 2);
  }
  return {
    left: whole(left),
    right: whole(left) + width,
    top: whole(y - height / 2),
    bottom: whole(y - height / 2) + height,
    width,
    height,
    y: whole(y),
    paddingX,
    starSpace,
  };
}

function rectsOverlap(a, b, gap = 6) {
  if (!a || !b) return false;
  return !(
    a.right + gap <= b.left
    || b.right + gap <= a.left
    || a.bottom + gap <= b.top
    || b.bottom + gap <= a.top
  );
}

function drawPillLabel(ctx, text, x, y, options = {}) {
  const {
    color = "#d7ff45",
    background = "rgba(8, 10, 10, 0.82)",
  } = options;
  ctx.save();
  const rect = resolvePillLabelRect(ctx, text, x, y, options);
  roundedRect(ctx, rect.left, rect.top, rect.width, rect.height, 5);
  ctx.fillStyle = background;
  ctx.fill();
  ctx.strokeStyle = rgba(color, 0.5);
  ctx.stroke();
  if (options.star) {
    drawStar(ctx, rect.left + rect.paddingX + 6, rect.y, 5.5, 2.6);
    ctx.fillStyle = color;
    ctx.fill();
  }
  ctx.fillStyle = color;
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  ctx.fillText(text, rect.left + rect.paddingX + rect.starSpace, rect.y);
  ctx.restore();
  return rect;
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
  const showActiveTime = state.showSessionLine;
  const pad = compact
    ? { top: showActiveTime ? 34 : 24, right: showActiveTime ? 44 : 18, bottom: 54, left: 54 }
    : { top: showActiveTime ? 38 : 24, right: showActiveTime ? 72 : 22, bottom: 62, left: 76 };
  const plotW = Math.max(width - pad.left - pad.right, 1);
  const plotH = Math.max(height - pad.top - pad.bottom, 1);
  const step = plotW / days.length;
  const columnWidth = Math.max(Math.min(step * 0.62, compact ? 14 : 22), days.length > 80 ? 2 : 5);
  const isolated = state.isolatedModel;
  const dayChartValue = (day) => {
    if (!isolated) return metricValue(day);
    const entry = (day.models || []).find((model) => model.name === isolated);
    return entry ? metricValue(entry) : 0;
  };
  const maxTokens = Math.max(...days.map(dayChartValue), 1);
  const tokenMax = Math.max(maxTokens * (showActiveTime ? 1.025 : 1.005), 1);
  const maxDuration = Math.max(...days.map((day) => day.sessionDurationSeconds || 0), 60 * 60);
  const durationMax = Math.max(maxDuration, 24 * 60 * 60);
  const orderedModels = isolated ? [isolated] : modelRows.map((model) => model.name);
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
    const y = px(pad.top + (plotH / 4) * i);
    const tokenValue = tokenMax - (tokenMax / 4) * i;
    ctx.beginPath();
    ctx.moveTo(px(pad.left), y);
    ctx.lineTo(px(width - pad.right), y);
    ctx.stroke();
    ctx.fillText(formatMetric(tokenValue), whole(pad.left - 10), whole(y));
  }

  [halfBillionTokens, billionTokens].forEach((threshold) => {
    if (threshold >= tokenMax) return;
    const y = px(chartY(threshold, tokenMax, pad, plotH));
    ctx.save();
    ctx.setLineDash([7, 7]);
    ctx.strokeStyle = threshold >= billionTokens
      ? "rgba(215, 255, 69, 0.58)"
      : "rgba(255, 191, 71, 0.42)";
    ctx.beginPath();
    ctx.moveTo(px(pad.left), y);
    ctx.lineTo(px(width - pad.right), y);
    ctx.stroke();
    ctx.restore();
  });

  if (showActiveTime) {
    ctx.textBaseline = "middle";
    ctx.textAlign = "left";
    ctx.fillStyle = "#93a29f";
    ctx.fillText(durationLabel(durationMax), whole(width - pad.right + 12), whole(pad.top));
    ctx.fillText("0m", whole(width - pad.right + 12), whole(pad.top + plotH));
  }

  days.forEach((day, index) => {
    const x = whole(pad.left + index * step + (step - columnWidth) / 2);
    const barWidth = Math.max(whole(columnWidth), days.length > 80 ? 2 : 5);
    let yBase = pad.top + plotH;
    const dayTotal = dayChartValue(day);
    const segments = isolated
      ? chartSegmentsForDay({ ...day, models: (day.models || []).filter((model) => model.name === isolated) }, orderedModels, dayTotal)
      : chartSegmentsForDay(day, orderedModels, dayTotal);

    ctx.save();
    roundedRect(ctx, x, whole(pad.top), barWidth, whole(plotH), barWidth / 2);
    ctx.fillStyle = "rgba(255, 255, 255, 0.025)";
    ctx.fill();
    ctx.restore();

    segments.forEach((segment) => {
      const value = segment.value;
      const segmentHeight = Math.max((value / tokenMax) * plotH, 1.25);
      yBase -= segmentHeight;
      const segmentY = whole(yBase);
      const crispSegmentHeight = Math.max(whole(segmentHeight), 1);
      const color = segment.color;
      ctx.save();
      ctx.shadowColor = rgba(color, index === hoverIndex ? 0.46 : 0.18);
      ctx.shadowBlur = index === hoverIndex ? 7 : 2.5;
      const barGradient = ctx.createLinearGradient(0, segmentY, 0, segmentY + crispSegmentHeight);
      barGradient.addColorStop(0, rgba(color, index === hoverIndex ? 1 : 0.96));
      barGradient.addColorStop(1, rgba(color, index === hoverIndex ? 0.82 : 0.68));
      roundedRect(ctx, x, segmentY, barWidth, crispSegmentHeight, Math.min(4, barWidth / 2));
      ctx.fillStyle = barGradient;
      ctx.fill();
      ctx.restore();
    });

    if (index === hoverIndex) {
      ctx.save();
      roundedRect(ctx, x - 3, whole(yBase) - 3, barWidth + 6, whole(pad.top + plotH - yBase) + 6, 6);
      ctx.strokeStyle = "rgba(245, 241, 232, 0.72)";
      ctx.lineWidth = 1.5;
      ctx.stroke();
      ctx.restore();
    }
  });

  const sessionPoints = days.map((day, index) => ({
    x: whole(pad.left + index * step + step / 2),
    y: whole(chartY(day.sessionDurationSeconds || 0, durationMax, pad, plotH)),
    day,
  }));

  if (showActiveTime) {
    ctx.save();
    ctx.strokeStyle = "rgba(255, 191, 71, 0.18)";
    ctx.lineWidth = compact ? 4.5 : 5.5;
    ctx.shadowColor = "rgba(255, 191, 71, 0.24)";
    ctx.shadowBlur = 7;
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
  }

  const recordBounds = {
    left: pad.left + 8,
    right: width - pad.right - 8,
    top: pad.top + 8,
    bottom: pad.top + plotH - 8,
  };
  const rangePeakDay = days.reduce((best, day) => {
    const value = dayChartValue(day);
    if (value <= 0) return best;
    if (!best || value > dayChartValue(best)) return day;
    if (value === dayChartValue(best) && String(day?.date || "") > String(best?.date || "")) return day;
    return best;
  }, null);
  const rangeLongestDay = showActiveTime ? maxDayBy(days, "sessionDurationSeconds") : null;
  const peakIndex = days.findIndex((day) => day.date === rangePeakDay?.date);
  let metricRecordRect = null;
  if (!compact && peakIndex >= 0 && rangePeakDay && dayChartValue(rangePeakDay) > 0) {
    const day = days[peakIndex];
    const label = metricMeta().recordLabel;
    const peakX = pad.left + peakIndex * step + step / 2;
    const peakY = chartY(dayChartValue(day), tokenMax, pad, plotH);
    metricRecordRect = drawPillLabel(ctx, `${label}: ${formatMetric(dayChartValue(day))}`, peakX, peakY - 17, {
      align: "center",
      color: "#d7ff45",
      background: "rgba(8, 10, 10, 0.88)",
      bounds: recordBounds,
      star: true,
    });
  }

  const longestIndex = days.findIndex((day) => day.date === rangeLongestDay?.date);
  if (showActiveTime && !compact && longestIndex >= 0 && rangeLongestDay) {
    const day = days[longestIndex];
    const activeText = `Most active day: ${durationHoursLabel(day.sessionDurationSeconds)}`;
    const activeX = pad.left + longestIndex * step + step / 2;
    const activePoint = sessionPoints[longestIndex];
    const activePointY = activePoint?.y || recordBounds.top;
    const activeRectProbe = resolvePillLabelRect(ctx, activeText, activeX, activePointY, {
      align: "center",
      bounds: recordBounds,
      star: true,
    });
    const candidatePoints = [
      [activeX, activePointY + 26],
      [activeX, activePointY - 26],
      [activeX, activePointY + 54],
      [activeX, activePointY - 54],
      [activeX - activeRectProbe.width / 2 - 16, activePointY + 8],
      [activeX + activeRectProbe.width / 2 + 16, activePointY + 8],
      [recordBounds.left, activePointY + 8, "left"],
      [recordBounds.right, activePointY + 8, "right"],
    ];
    let activeLabelX = activeX;
    let activeLabelY = activePointY + 26;
    let activeAlign = "center";
    let activeRect = null;
    for (const [candidateX, candidateY, candidateAlign = "center"] of candidatePoints) {
      const candidateRect = resolvePillLabelRect(ctx, activeText, candidateX, candidateY, {
        align: candidateAlign,
        bounds: recordBounds,
        star: true,
      });
      if (!rectsOverlap(metricRecordRect, candidateRect, 4)) {
        activeRect = candidateRect;
        activeLabelX = candidateX;
        activeLabelY = candidateY;
        activeAlign = candidateAlign;
        break;
      }
    }
    if (activeRect) {
      drawPillLabel(ctx, activeText, activeLabelX, activeLabelY, {
        align: activeAlign,
        color: "#ffbf47",
        background: "rgba(8, 10, 10, 0.88)",
        bounds: recordBounds,
        star: true,
      });
    }
  }

  if (hoverIndex !== null && days[hoverIndex]) {
    const x = pad.left + hoverIndex * step + step / 2;
    ctx.strokeStyle = "rgba(215, 255, 69, 0.62)";
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    ctx.moveTo(x, pad.top);
    ctx.lineTo(x, pad.top + plotH);
    ctx.stroke();
    if (showActiveTime) {
      const y = chartY(days[hoverIndex].sessionDurationSeconds || 0, durationMax, pad, plotH);
      ctx.fillStyle = "#fff3b0";
      ctx.beginPath();
      ctx.arc(x, y, 6, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  ctx.fillStyle = "#8fa09c";
  ctx.font = "700 12px SFMono-Regular, Consolas, monospace";
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  const tickCount = Math.min(compact ? 4 : 6, days.length);
  for (let i = 0; i < tickCount; i += 1) {
    const index = Math.round((i / Math.max(tickCount - 1, 1)) * (days.length - 1));
    const x = whole(pad.left + index * step + step / 2);
    ctx.fillText(formatDate(days[index].date), x, whole(height - 28));
  }

  setText(
    els.rangeCaption,
    isolated
      ? `${fullNumber(days.length)} days | isolated: ${isolated} (click it again to clear)`
      : `${fullNumber(days.length)} days | bars = ${metricMeta().rangeLabel}`,
  );
  els.rangeCaption.title = "Active Time Per Day: Length of time where any tool was generating tokens without a 2 hour gap.";
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
  const tooltipModels = state.isolatedModel
    ? visibleModels(day.models || []).filter((model) => model.name === state.isolatedModel)
    : visibleModels(day.models || []);
  const tooltipTotal = state.isolatedModel
    ? metricValue(tooltipModels[0] || {})
    : metricValue(day);
  const visibleTotal = tooltipModels.reduce((sum, model) => sum + metricValue(model), 0);
  const remainder = state.isolatedModel
    ? 0
    : Math.max(tooltipTotal - visibleTotal, 0);
  const rows = [...tooltipModels]
    .sort((a, b) => metricValue(b) - metricValue(a))
    .slice(0, 5)
    .map(
      (model) => `
        <span class="tip-row" title="${escapeHtml(modelTitle(model))}">
          <i style="background:${colorForModel(model.name)}"></i>
          <em>${escapeHtml(model.name)}</em>
          <b>${formatMetric(metricValue(model))}</b>
        </span>
      `,
    )
    .join("");
  const otherRow = remainder > Math.max(tooltipTotal * 0.001, 0.01)
    ? `
        <span class="tip-row" title="Unattributed or hidden model usage included in the daily total">
          <i style="background:${OTHER_MODEL_COLOR}"></i>
          <em>${OTHER_MODEL_LABEL}</em>
          <b>${formatMetric(remainder)}</b>
        </span>
      `
    : "";

  els.tooltip.hidden = false;
  els.tooltip.innerHTML = `
    <span class="tip-date">${formatDateLong(day.date)}</span>
    <strong class="tip-total">${formatMetric(tooltipTotal, "full")} ${metricTooltipLabelHtml()}</strong>
    <div class="tip-metrics">
      <span><b>${durationLabel(day.sessionDurationSeconds)}</b><em>Active Time</em></span>
      <span><b>${fullNumber(day.modelCalls)}</b><em>calls</em></span>
    </div>
    <div class="tip-models">${rows}${otherRow}</div>
  `;

  // Place above the pointer by default, but flip below when a tall peak leaves
  // no room above (the chart clips overflow, which was cutting the tooltip off).
  const margin = 14;
  const pointerY = state.pointerY || 80;
  const tipHeight = els.tooltip.offsetHeight;
  const flipBelow = pointerY - tipHeight - margin < 0;
  let top;
  if (flipBelow) {
    top = Math.min(pointerY, chartRect.height - tipHeight - margin);
    els.tooltip.style.transform = `translate(-50%, ${margin}px)`;
  } else {
    top = pointerY;
    els.tooltip.style.transform = `translate(-50%, calc(-100% - ${margin}px))`;
  }
  els.tooltip.style.left = `${left}px`;
  els.tooltip.style.top = `${Math.max(top, 0)}px`;
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
            <td>${formatMetric(metricValue(day), "full")}</td>
            <td>${durationLabel(day.sessionDurationSeconds)}</td>
            <td><span class="table-model"><i style="background:${colorForModel(top.name)}"></i>${escapeHtml(top.name)}</span></td>
            <td>${formatMetric(metricValue(top), "full")}</td>
            <td>${fullNumber(day.modelCalls)}</td>
          </tr>
        `;
      })
      .join(""),
  );
}

function heatColor(intensity) {
  if (intensity <= 0) return "rgba(245, 241, 232, 0.06)";
  const adjusted = Math.pow(Math.min(Math.max(intensity, 0), 1), 0.72);
  const ramp = [
    [0.08, "rgb(30, 50, 54)"],
    [0.18, "rgb(32, 112, 124)"],
    [0.30, "rgb(98, 216, 255)"],
    [0.44, "rgb(117, 167, 255)"],
    [0.60, "rgb(100, 240, 174)"],
    [0.76, "rgb(215, 255, 69)"],
    [0.90, "rgb(255, 191, 71)"],
    [1.00, "rgb(255, 95, 135)"],
  ];
  for (const [limit, color] of ramp) {
    if (adjusted <= limit) return color;
  }
  return ramp[ramp.length - 1][1];
}

const DOW_LONG = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function isoLocal(date) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function heatmapYears() {
  const years = new Set((usage.days || []).map((day) => String(day.date).slice(0, 4)));
  return [...years].filter(Boolean).sort().reverse();
}

function heatmapWindow() {
  const today = new Date();
  today.setHours(12, 0, 0, 0);
  if (state.heatmapPeriod === "12mo") {
    const start = new Date(today);
    start.setFullYear(start.getFullYear() - 1);
    start.setDate(start.getDate() + 1);
    return { start, end: today };
  }
  const year = Number(state.heatmapPeriod);
  const start = new Date(year, 0, 1, 12, 0, 0, 0);
  const end = new Date(year, 11, 31, 12, 0, 0, 0);
  return { start, end };
}

function renderHeatmapControls() {
  if (!els.heatmapPeriod) return;
  const options = [{ key: "12mo", label: "12 mo" }, ...heatmapYears().map((y) => ({ key: y, label: y }))];
  if (!options.some((opt) => opt.key === state.heatmapPeriod)) {
    state.heatmapPeriod = options[0].key;
  }
  els.heatmapPeriod.innerHTML = options
    .map(
      (opt) =>
        `<button type="button" class="${opt.key === state.heatmapPeriod ? "active" : ""}" data-heatmap-period="${opt.key}">${escapeHtml(opt.label)}</button>`,
    )
    .join("");
}

function updateHeatmap() {
  if (!els.heatmapWrap) return;
  const byDate = new Map((usage.days || []).map((day) => [day.date, day]));
  const { start, end } = heatmapWindow();
  // Align the grid so the first column starts on Sunday.
  const gridStart = new Date(start);
  gridStart.setDate(gridStart.getDate() - gridStart.getDay());

  const windowValues = [];
  for (const day of usage.days || []) {
    const date = new Date(`${day.date}T12:00:00`);
    if (date >= start && date <= end) windowValues.push(metricValue(day));
  }
  const maxValue = Math.max(...windowValues, 1);
  const activeDays = windowValues.filter((value) => value > 0).length;

  const cells = [];
  const monthCols = [];
  const cursor = new Date(gridStart);
  let column = 0;
  let lastMonthLabeled = -1;
  while (cursor <= end) {
    if (cursor.getDay() === 0) {
      // Top of a new week column: label it if it introduces a new month.
      const month = cursor.getMonth();
      if (month !== lastMonthLabeled && cursor >= start) {
        monthCols[column] = MONTH_ABBR[month];
        lastMonthLabeled = month;
      }
      column += 1;
    }
    const iso = isoLocal(cursor);
    const day = byDate.get(iso);
    const value = day ? metricValue(day) : 0;
    const inRange = cursor >= start && cursor <= end;
    if (!inRange) {
      cells.push(`<div class="heatmap-cell" data-empty="true"></div>`);
    } else if (value <= 0) {
      cells.push(`<div class="heatmap-cell" data-date="${iso}" data-value="0"></div>`);
    } else {
      const intensity = Math.min(value / maxValue, 1);
      const color = heatColor(intensity);
      cells.push(
        `<div class="heatmap-cell" data-date="${iso}" data-active="true" style="background:${color};border-color:transparent"></div>`,
      );
    }
    cursor.setDate(cursor.getDate() + 1);
  }

  els.heatmapWrap.innerHTML = cells.join("");

  if (els.heatmapMonths) {
    const totalCols = column;
    const monthCells = [];
    for (let i = 0; i < totalCols; i += 1) {
      monthCells.push(
        monthCols[i]
          ? `<div class="heatmap-month"><span>${monthCols[i]}</span></div>`
          : `<div class="heatmap-month"></div>`,
      );
    }
    els.heatmapMonths.innerHTML = monthCells.join("");
  }

  const periodLabel = state.heatmapPeriod === "12mo" ? "trailing 12 months" : state.heatmapPeriod;
  setText(
    els.heatmapCaption,
    `${fullNumber(activeDays)} active days in ${periodLabel} · color = ${metricLabel()}`,
  );
  if (els.heatmapLegend) {
    const ramp = [0.05, 0.25, 0.5, 0.75, 1].map((step) => `<i style="background:${heatColor(step)}"></i>`).join("");
    els.heatmapLegend.innerHTML = `less ${ramp} more`;
  }
}

function scrollHeatmapToTodayOnMobile(behavior = "smooth") {
  if (!els.heatmapScroll || !els.heatmapWrap) return;
  if (!window.matchMedia?.("(max-width: 620px)").matches) return;

  const todayIso = isoLocal(new Date());
  let target = els.heatmapWrap.querySelector(`.heatmap-cell[data-date="${todayIso}"]`);
  if (!target) {
    const datedCells = [...els.heatmapWrap.querySelectorAll(".heatmap-cell[data-date]")];
    for (let i = datedCells.length - 1; i >= 0; i -= 1) {
      if (datedCells[i].dataset.date <= todayIso) {
        target = datedCells[i];
        break;
      }
    }
    target = target || datedCells[datedCells.length - 1];
  }
  if (!target) return;

  const rightEdge = target.offsetLeft + target.offsetWidth;
  const desiredLeft = rightEdge - els.heatmapScroll.clientWidth + 28;
  const maxLeft = els.heatmapScroll.scrollWidth - els.heatmapScroll.clientWidth;
  const nextLeft = Math.max(0, Math.min(desiredLeft, maxLeft));
  els.heatmapScroll.scrollTo({ left: nextLeft, behavior });
}

function settleHeatmapToTodayOnMobile() {
  requestAnimationFrame(() => scrollHeatmapToTodayOnMobile("auto"));
  requestAnimationFrame(() => requestAnimationFrame(() => scrollHeatmapToTodayOnMobile("auto")));
  window.setTimeout(() => scrollHeatmapToTodayOnMobile("auto"), 120);
}

function showHeatmapTip(cell) {
  if (!els.heatmapTip || !els.heatmapPanel) return;
  const iso = cell.dataset.date;
  if (!iso) return;
  const day = (usage.days || []).find((d) => d.date === iso);
  const date = new Date(`${iso}T12:00:00`);
  const value = day ? metricValue(day) : 0;
  const calls = day ? Number(day.modelCalls || 0) : 0;
  els.heatmapTip.innerHTML = `
    <span class="tip-dow">${DOW_LONG[date.getDay()]}</span>
    <span class="tip-date">${formatDateLong(iso)}</span>
    <span class="tip-value">${value > 0 ? `${formatMetric(value, "full")} ${metricLabel()}` : "no activity"}</span>
    ${value > 0 ? `<span class="tip-sub">${fullNumber(calls)} calls</span>` : ""}
  `;
  const panelRect = els.heatmapPanel.getBoundingClientRect();
  const cellRect = cell.getBoundingClientRect();
  const left = cellRect.left - panelRect.left + cellRect.width / 2;
  const top = cellRect.top - panelRect.top;
  els.heatmapTip.style.left = `${left}px`;
  els.heatmapTip.style.top = `${top}px`;
  els.heatmapTip.hidden = false;
  cell.classList.add("is-hovered");
}

function hideHeatmapTip() {
  if (els.heatmapTip) els.heatmapTip.hidden = true;
  els.heatmapWrap?.querySelectorAll(".heatmap-cell.is-hovered").forEach((c) => c.classList.remove("is-hovered"));
}

function hourLabel(hour) {
  return `${String(hour).padStart(2, "0")}:00`;
}

function positionHoursTip(bar, event) {
  if (!els.hoursTip || !els.hoursPanel) return;
  const panelRect = els.hoursPanel.getBoundingClientRect();
  const barRect = bar.getBoundingClientRect();
  const margin = 12;
  const tipWidth = els.hoursTip.offsetWidth || 260;
  const tipHeight = els.hoursTip.offsetHeight || 100;
  const pointerX = event?.clientX || (barRect.left + barRect.width / 2);
  const minLeft = margin + tipWidth / 2;
  const maxLeft = Math.max(minLeft, panelRect.width - margin - tipWidth / 2);
  const left = Math.min(Math.max(pointerX - panelRect.left, minLeft), maxLeft);
  const barTop = barRect.top - panelRect.top;
  const barBottom = barRect.bottom - panelRect.top;
  const aboveFits = barTop - tipHeight - margin >= margin;
  let top = barTop;
  if (aboveFits) {
    els.hoursTip.style.transform = `translate(-50%, calc(-100% - ${margin}px))`;
  } else {
    const maxTop = Math.max(margin, panelRect.height - tipHeight - margin * 2);
    top = Math.min(Math.max(barBottom, margin), maxTop);
    els.hoursTip.style.transform = `translate(-50%, ${margin}px)`;
  }
  els.hoursTip.style.left = `${left}px`;
  els.hoursTip.style.top = `${top}px`;
}

function showHoursTip(bar, hour, value, rangeTotal, event) {
  if (!els.hoursTip || !els.hoursPanel) return;
  const metricText = metricMeta().tooltipLabel || metricLabel();
  els.hoursTip.innerHTML = `
    <span class="tip-hour">${escapeHtml(hourLabel(hour))}</span>
    <span class="tip-label">${escapeHtml(metricText)}</span>
    <strong class="tip-value">${formatMetric(value, "full")}</strong>
    <span class="tip-share">${percentLabel(value, rangeTotal)} Of Total</span>
  `;
  els.hoursTip.hidden = false;
  els.hoursWrap?.querySelectorAll(".hour-bar.is-hovered").forEach((item) => item.classList.remove("is-hovered"));
  bar.classList.add("is-hovered");
  positionHoursTip(bar, event);
}

function hideHoursTip() {
  if (els.hoursTip) els.hoursTip.hidden = true;
  els.hoursWrap?.querySelectorAll(".hour-bar.is-hovered").forEach((item) => item.classList.remove("is-hovered"));
}

function updateHoursHistogram(days) {
  if (!els.hoursWrap) return;
  // Sum per-day hour buckets across the visible range so the histogram
  // follows the chart's range selector. Falls back to the all-time
  // hoursOfDay aggregate if per-day data isn't present (older bundles).
  const values = Array(24).fill(0);
  let havePerDay = false;
  (days || []).forEach((day) => {
    const hours = day.hours;
    if (!hours) return;
    havePerDay = true;
    Object.entries(hours).forEach(([hour, usageRow]) => {
      const idx = Number(hour);
      if (idx >= 0 && idx < 24) values[idx] += metricValue(usageRow);
    });
  });
  if (!havePerDay) {
    const fallback = usage.hoursOfDay || [];
    if (!fallback.length) {
      els.hoursWrap.innerHTML = "";
      hideHoursTip();
      setText(els.hoursCaption, "Hour data not in this build.");
      return;
    }
    fallback.forEach((bucket) => {
      const idx = Number(bucket.hour);
      if (idx >= 0 && idx < 24) values[idx] = metricValue(bucket);
    });
  }
  const max = Math.max(...values, 1);
  const total = values.reduce((a, b) => a + b, 0);
  const rangeTotal = havePerDay
    ? (days || []).reduce((acc, day) => acc + metricValue(day), 0)
    : total;
  const peakHour = values.indexOf(Math.max(...values));
  const bars = values.map((value, hour) => {
    const heightPct = (value / max) * 100;
    const displayHour = hourLabel(hour);
    const shareTotal = rangeTotal > 0 ? rangeTotal : total;
    const share = percentLabel(value, shareTotal);
    const label = value > 0
      ? `${displayHour} · ${formatMetric(value, "full")} ${metricLabel()} · ${share} Of Total`
      : `${displayHour} · quiet · ${share} Of Total`;
    const tick = hour % 6 === 0 ? String(hour).padStart(2, "0") : "";
    return `
      <div class="hour-bar" data-hour="${tick}" data-hour-index="${hour}" aria-label="${escapeHtml(label)}">
        <b style="height:${heightPct}%"></b>
      </div>
    `;
  }).join("");
  els.hoursWrap.innerHTML = bars;
  hideHoursTip();
  els.hoursWrap.querySelectorAll(".hour-bar").forEach((bar) => {
    const hour = Number(bar.dataset.hourIndex);
    const value = values[hour] || 0;
    const shareTotal = rangeTotal > 0 ? rangeTotal : total;
    bar.addEventListener("pointerenter", (event) => showHoursTip(bar, hour, value, shareTotal, event));
    bar.addEventListener("pointermove", (event) => positionHoursTip(bar, event));
    bar.addEventListener("pointerleave", hideHoursTip);
  });
  if (total > 0) {
    setText(
      els.hoursCaption,
      `Peak hour: ${hourLabel(peakHour)} · ${formatMetric(values[peakHour])} ${metricLabel()}`,
    );
  } else {
    setText(els.hoursCaption, "No counted activity in any hour yet.");
  }
}

function updateSubagentShare(days) {
  if (!els.subagentWrap) return;
  const eligibleTotal = eligibleSubagentProviderTotal(days);
  const subagentTotal = days.reduce(
    (acc, day) => acc + metricValue(day.subagentUsage || {}),
    0,
  );
  const rangeTotal = Math.max(eligibleTotal, subagentTotal);
  const mainTotal = Math.max(rangeTotal - subagentTotal, 0);
  if (rangeTotal <= 0) {
    els.subagentWrap.innerHTML = "";
    setText(els.subagentCaption, "No counted Codex or Claude Code activity in range.");
    return;
  }
  const subagentPct = (subagentTotal / rangeTotal) * 100;
  const mainPct = (mainTotal / rangeTotal) * 100;
  els.subagentWrap.innerHTML = `
    <div class="subagent-bar" title="${escapeHtml(`Subagents: ${formatMetric(subagentTotal, "full")} ${metricLabel()}`)}">
      <b class="subagent-segment" style="width:${subagentPct.toFixed(1)}%"></b>
      <i class="main-segment" style="left:${subagentPct.toFixed(1)}%"></i>
    </div>
    <div class="subagent-stats">
      <div>
        <span>Subagent</span>
        <strong>${formatMetric(subagentTotal)}</strong>
        <small>${subagentPct < 1 && subagentTotal > 0 ? "<1" : Math.round(subagentPct)}%</small>
      </div>
      <div>
        <span>Main thread</span>
        <strong>${formatMetric(mainTotal)}</strong>
        <small>${mainPct < 1 && mainTotal > 0 ? "<1" : Math.round(mainPct)}%</small>
      </div>
    </div>
  `;
  setText(
    els.subagentCaption,
    `${metricSentenceLabel()} across all tools`,
  );
}

function secondsIntoDay(iso) {
  const t = String(iso).slice(11, 19).split(":");
  return (Number(t[0]) || 0) * 3600 + (Number(t[1]) || 0) * 60 + (Number(t[2]) || 0);
}

function nextDateKey(dateKey) {
  const d = new Date(`${dateKey}T12:00:00`);
  d.setDate(d.getDate() + 1);
  return d.toISOString().slice(0, 10);
}

// Split a session into per-local-day segments with start/width as fractions of
// a 24h day, so each segment can be drawn on its day's track.
function sessionDaySegments(session) {
  const startDate = String(session.start).slice(0, 10);
  const endDate = String(session.end).slice(0, 10);
  const startSec = secondsIntoDay(session.start);
  const endSec = secondsIntoDay(session.end);
  const segments = [];
  let dateKey = startDate;
  for (let guard = 0; guard < 400; guard += 1) {
    const from = dateKey === startDate ? startSec : 0;
    const to = dateKey === endDate ? endSec : 86400;
    segments.push({ dateKey, from, to });
    if (dateKey === endDate) break;
    dateKey = nextDateKey(dateKey);
  }
  return segments;
}

const DOW_SHORT = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function updateSessionHistory() {
  if (!els.historyScroll) return;
  // Per-project sessions: one row per (day, project) so concurrent work on
  // different repos shows as separate lanes under the same day.
  const sessions = (usage.sessions?.byProject || []).filter((s) => s.start);
  if (!sessions.length) {
    els.historyScroll.innerHTML = "<p class=\"history-empty\">No sessions recorded yet.</p>";
    setText(els.historyCaption, "No sessions yet.");
    if (els.historyLegend) els.historyLegend.innerHTML = "";
    return;
  }

  // Token totals per project; seed colors in descending order so the heaviest
  // projects get the most distinct palette slots (and the legend matches).
  const projectTokens = new Map();
  sessions.forEach((s) => {
    const key = s.project || "unknown";
    projectTokens.set(key, (projectTokens.get(key) || 0) + Number(s.totalTokens || 0));
  });
  const rankedProjects = [...projectTokens.entries()].sort((a, b) => b[1] - a[1]);
  rankedProjects.forEach(([name]) => colorForProject(name === "unknown" ? "" : name));

  // day -> project -> segments
  const byDay = new Map();
  sessions.forEach((session, index) => {
    const project = session.project || "unknown";
    sessionDaySegments(session).forEach((seg) => {
      if (!byDay.has(seg.dateKey)) byDay.set(seg.dateKey, new Map());
      const projectMap = byDay.get(seg.dateKey);
      if (!projectMap.has(project)) projectMap.set(project, []);
      projectMap.get(project).push({ ...seg, index });
    });
  });

  const segSpan = (segs) => segs.reduce((acc, seg) => acc + (seg.to - seg.from), 0);
  const dayKeys = [...byDay.keys()].sort().reverse(); // newest first
  const rows = [];
  dayKeys.forEach((dateKey) => {
    const date = new Date(`${dateKey}T12:00:00`);
    const projectMap = byDay.get(dateKey);
    const projects = [...projectMap.keys()].sort((a, b) => segSpan(projectMap.get(b)) - segSpan(projectMap.get(a)));
    projects.forEach((project, lane) => {
      const color = colorForProject(project === "unknown" ? "" : project);
      const blocks = projectMap.get(project)
        .sort((a, b) => a.from - b.from)
        .map((seg) => {
          const left = (seg.from / 86400) * 100;
          const width = Math.max(((seg.to - seg.from) / 86400) * 100, 0.5);
          return `<div class="history-block" style="left:${left}%;width:${width}%;background:${color}" data-i="${seg.index}"></div>`;
        })
        .join("");
      rows.push(`
        <div class="history-row">
          <span class="history-date">${lane === 0 ? `${DOW_SHORT[date.getDay()]} ${formatDate(dateKey)}` : ""}</span>
          <span class="history-proj" title="${escapeHtml(project)}"><i style="background:${color}"></i>${escapeHtml(project)}</span>
          <div class="history-track">${blocks}</div>
        </div>
      `);
    });
  });
  els.historyScroll.innerHTML = rows.join("");
  els.historyScroll._sessions = sessions;

  setText(
    els.historyCaption,
    `${fullNumber(sessions.length)} project-sessions across ${fullNumber(dayKeys.length)} days · 2h+ gap = new session · one lane per project`,
  );

  if (els.historyLegend) {
    els.historyLegend.innerHTML = rankedProjects
      .slice(0, 7)
      .map(([name]) => `<span><i style="background:${colorForProject(name === "unknown" ? "" : name)}"></i>${escapeHtml(name)}</span>`)
      .join("");
  }
}

function showHistoryTip(block) {
  if (!els.historyTip || !els.historyPanel) return;
  const sessions = els.historyScroll?._sessions || [];
  const session = sessions[Number(block.dataset.i)];
  if (!session) return;
  const date = new Date(`${String(session.start).slice(0, 10)}T12:00:00`);
  const startClock = String(session.start).slice(11, 16);
  const endClock = String(session.end).slice(11, 16);
  els.historyTip.innerHTML = `
    <span class="tip-dow">${DOW_SHORT[date.getDay()]} · ${escapeHtml(session.project || session.topProject || "unknown project")}</span>
    <span class="tip-date">${startClock}–${endClock} · ${durationLabel(session.durationSeconds)}</span>
    <span class="tip-value">${compactNumber(session.totalTokens)} tokens · ${session.topModel || "—"}</span>
    <span class="tip-sub">${fullNumber(session.modelCalls)} calls</span>
  `;
  const panelRect = els.historyPanel.getBoundingClientRect();
  const blockRect = block.getBoundingClientRect();
  els.historyTip.style.left = `${blockRect.left - panelRect.left + blockRect.width / 2}px`;
  els.historyTip.style.top = `${blockRect.top - panelRect.top}px`;
  els.historyTip.hidden = false;
}

function render() {
  const days = getRangeDays();
  state.hoveredIndex = null;
  moments = buildMoments();
  updateMetricControls();
  updatePersonalityLayer();
  updateSummary();
  updateModelMix(days);
  updateHighlights();
  updateCapture(days);
  updateTable(days);
  updateHeatmap();
  updateHoursHistogram(days);
  updateSubagentShare(days);
  updateSessionHistory();
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

document.querySelectorAll("[data-metric]").forEach((button) => {
  button.addEventListener("click", () => {
    if (!METRIC_META[button.dataset.metric]) return;
    state.metric = button.dataset.metric;
    render();
  });
});

if (els.heatmapPeriod) {
  els.heatmapPeriod.addEventListener("click", (event) => {
    const button = event.target.closest("[data-heatmap-period]");
    if (!button) return;
    state.heatmapPeriod = button.dataset.heatmapPeriod;
    renderHeatmapControls();
    hideHeatmapTip();
    updateHeatmap();
    requestAnimationFrame(() => scrollHeatmapToTodayOnMobile());
  });
}

if (els.heatmapWrap) {
  els.heatmapWrap.addEventListener("mouseover", (event) => {
    const cell = event.target.closest(".heatmap-cell[data-date]");
    if (!cell) return;
    showHeatmapTip(cell);
  });
  els.heatmapWrap.addEventListener("mouseout", (event) => {
    const cell = event.target.closest(".heatmap-cell[data-date]");
    if (cell) cell.classList.remove("is-hovered");
    if (!event.relatedTarget || !event.relatedTarget.closest?.(".heatmap-cell[data-date]")) {
      hideHeatmapTip();
    }
  });
}

if (els.sessionToggle) {
  els.sessionToggle.addEventListener("click", () => {
    state.showSessionLine = !state.showSessionLine;
    els.sessionToggle.classList.toggle("active", state.showSessionLine);
    els.sessionToggle.setAttribute("aria-pressed", String(state.showSessionLine));
    drawChart();
  });
}

function toggleIsolatedModel(name) {
  if (!name) return;
  state.isolatedModel = state.isolatedModel === name ? null : name;
  render();
}

if (els.historyScroll) {
  els.historyScroll.addEventListener("mouseover", (event) => {
    const block = event.target.closest(".history-block[data-i]");
    if (block) showHistoryTip(block);
  });
  els.historyScroll.addEventListener("mouseout", (event) => {
    if (!event.relatedTarget || !event.relatedTarget.closest?.(".history-block[data-i]")) {
      if (els.historyTip) els.historyTip.hidden = true;
    }
  });
  els.historyScroll.addEventListener("scroll", () => {
    if (els.historyTip) els.historyTip.hidden = true;
  });
}

if (els.modelMix) {
  els.modelMix.addEventListener("click", (event) => {
    const row = event.target.closest(".model-row[data-model]");
    if (row) toggleIsolatedModel(row.dataset.model);
  });
  els.modelMix.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const row = event.target.closest(".model-row[data-model]");
    if (!row) return;
    event.preventDefault();
    toggleIsolatedModel(row.dataset.model);
  });
}

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

renderHeatmapControls();
render();
settleHeatmapToTodayOnMobile();
window.addEventListener("load", settleHeatmapToTodayOnMobile, { once: true });
