from __future__ import annotations


DASHBOARD_JS = """
const state = { query: "", tier: "", collection: "", status: "", sort: "degree" };
const cards = Array.from(document.querySelectorAll("[data-entity-card]"));
const grid = document.querySelector("[data-entity-grid]");
const empty = document.querySelector("[data-empty-state]");
const visibleCount = document.querySelector("[data-visible-count]");
const payload = JSON.parse(document.getElementById("scribe-data").textContent || "{}");
const chartInstances = [];
let chartsResizeHandlerRegistered = false;
const themeStorageKey = "scribe-dashboard-theme";
const themeButtons = Array.from(document.querySelectorAll("[data-theme-option]"));
const tierLabels = { hot: "chaud", warm: "tiède", cold: "froid", "-": "-" };
const collectionLabels = {
  debts: "dettes",
  ghosts: "fantômes",
  invariants: "invariants",
  journal: "journal",
  patterns: "patterns",
  vaccins: "vaccins",
};
const statusLabels = {
  ACTIVE: "actif",
  active: "actif",
  RESOLVED: "résolu",
  OBSOLETE: "obsolète",
  SUPERSEDED_BY: "remplacé",
  "-": "non renseigné",
};

function normalize(value) {
  return String(value || "").toLowerCase().trim();
}

function matches(card) {
  const query = normalize(state.query);
  return (!query || normalize(card.dataset.search).includes(query))
    && (!state.tier || normalize(card.dataset.tier) === state.tier)
    && (!state.collection || normalize(card.dataset.collection) === state.collection)
    && (!state.status || normalize(card.dataset.status) === state.status);
}

function sortCards(visibleCards) {
  const sorted = visibleCards.slice();
  sorted.sort((a, b) => {
    if (state.sort === "title") return normalize(a.dataset.title).localeCompare(normalize(b.dataset.title));
    if (state.sort === "tier") return normalize(a.dataset.tier).localeCompare(normalize(b.dataset.tier));
    if (state.sort === "collection") return normalize(a.dataset.collection).localeCompare(normalize(b.dataset.collection));
    return Number(b.dataset.degree || 0) - Number(a.dataset.degree || 0);
  });
  return sorted;
}

function render() {
  const visibleCards = cards.filter(matches);
  cards.forEach((card) => { card.hidden = true; });
  sortCards(visibleCards).forEach((card) => {
    card.hidden = false;
    grid.appendChild(card);
  });
  visibleCount.textContent = `${visibleCards.length} visibles / ${cards.length} au total`;
  empty.classList.toggle("visible", visibleCards.length === 0);
}

function initBars() {
  document.querySelectorAll("[data-bar-width]").forEach((bar) => {
    const value = Number(bar.dataset.barWidth || 0);
    bar.style.width = `${Math.max(0, Math.min(100, value))}%`;
  });
}

document.querySelectorAll("[data-filter]").forEach((control) => {
  control.addEventListener("input", () => {
    state[control.dataset.filter] = normalize(control.value);
    render();
  });
});

document.querySelector("[data-reset]").addEventListener("click", () => {
  Object.keys(state).forEach((key) => { state[key] = key === "sort" ? "degree" : ""; });
  document.querySelectorAll("[data-filter]").forEach((control) => {
    control.value = control.dataset.filter === "sort" ? "degree" : "";
  });
  render();
});

function safeEntities() {
  return Array.isArray(payload.entities) ? payload.entities : [];
}

function countBy(items, key) {
  return items.reduce((acc, item) => {
    const name = String(item[key] || "-");
    acc[name] = (acc[name] || 0) + 1;
    return acc;
  }, {});
}

function labelFor(labels, name) {
  return labels[name] || labels[String(name).toLowerCase()] || name;
}

function cssVar(name, fallback) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

function chartTextColor() {
  return cssVar("--chart-text", "#c7d2e5");
}

function chartGridColor() {
  return cssVar("--chart-grid", "rgba(190, 204, 229, 0.14)");
}

function chartLegendOption() {
  return { bottom: 0, icon: "circle", textStyle: { color: chartTextColor() } };
}

function tooltipOption(trigger) {
  const option = {
    trigger,
    backgroundColor: cssVar("--tooltip-bg", "rgba(10, 16, 28, 0.94)"),
    borderColor: cssVar("--line", "rgba(190, 204, 229, 0.22)"),
    textStyle: { color: cssVar("--tooltip-text", chartTextColor()) },
  };
  if (trigger === "axis") option.axisPointer = { type: "shadow" };
  return option;
}

function valueAxisOption() {
  return {
    type: "value",
    axisLabel: { color: chartTextColor() },
    splitLine: { lineStyle: { color: chartGridColor() } },
  };
}

function categoryAxisOption(data) {
  return { type: "category", data, axisTick: { show: false }, axisLabel: { color: chartTextColor() } };
}

function normalizeTheme(value) {
  return value === "light" ? "light" : "dark";
}

function applyTheme(value, persist) {
  const theme = normalizeTheme(value);
  document.documentElement.dataset.theme = theme;
  if (persist) {
    try { localStorage.setItem(themeStorageKey, theme); } catch (error) {}
  }
  themeButtons.forEach((button) => {
    const active = button.dataset.themeOption === theme;
    button.setAttribute("aria-pressed", String(active));
  });
  refreshCharts();
  window.dispatchEvent(new CustomEvent("scribe-theme-change"));
}

function initThemeToggle() {
  applyTheme(document.documentElement.dataset.theme, false);
  themeButtons.forEach((button) => {
    button.addEventListener("click", () => applyTheme(button.dataset.themeOption, true));
  });
}

function tierChartData() {
  const tiers = payload.tiers || {};
  return ["hot", "warm", "cold"].map((name) => ({
    name: tierLabels[name] || name,
    value: Array.isArray(tiers[name]) ? tiers[name].length : 0,
  }));
}

function riskChartData() {
  const entities = safeEntities();
  const activeDebt = entities.filter((item) => item.collection === "debts" && item.status === "ACTIVE").length;
  const cold = Array.isArray(payload.tiers?.cold) ? payload.tiers.cold.length : 0;
  const summary = payload.summary || {};
  const retrievalSummary = payload.retrieval_quality?.summary || {};
  return [
    { name: "Avertissements", value: Number(summary.doctor_warnings || 0) },
    { name: "Erreurs", value: Number(summary.doctor_errors || 0) },
    { name: "Dette", value: activeDebt },
    { name: "Froid", value: cold },
    { name: "Eval KO", value: Number(retrievalSummary.failed_cases || 0) },
  ];
}

function edgeChartData() {
  const edges = payload.summary?.edges || {};
  return [
    { name: "Causal", value: Number(edges.causal || payload.summary?.causal_edges || 0) },
    { name: "Evidence", value: Number(edges.evidence || 0) },
    { name: "Consultation", value: Number(edges.consultation || 0) },
    { name: "Journal", value: Number(edges.journal || 0) },
  ];
}

function statusChartData() {
  return Object.entries(countBy(safeEntities(), "status"))
    .sort((left, right) => right[1] - left[1])
    .map(([name, value]) => ({ name: labelFor(statusLabels, name), value }));
}

function qualityChartData() {
  const summary = payload.retrieval_quality?.summary || {};
  const bySurface = summary.by_surface || {};
  const entries = Object.entries(bySurface);
  if (entries.length === 0) {
    const total = Number(summary.surface_cases || summary.cases || 0);
    const passed = Number(summary.passed_surface_cases || summary.passed || 0);
    return [{ name: "global", passed, failed: Math.max(0, total - passed) }];
  }
  return entries.map(([name, surface]) => {
    const total = Number(surface.cases || 0);
    const passed = Number(surface.passed || 0);
    return { name, passed, failed: Math.max(0, total - passed) };
  });
}

function renderChart(name, option) {
  const element = document.querySelector(`[data-chart="${name}"]`);
  if (!element || !window.echarts) return;
  const theme = document.documentElement.dataset.theme === "light" ? null : "dark";
  const chart = echarts.init(element, theme, { renderer: "svg" });
  chart.setOption(option);
  chartInstances.push(chart);
}

function initCharts() {
  if (!window.echarts) return;
  const collections = Object.entries(countBy(safeEntities(), "collection"))
    .sort((left, right) => right[1] - left[1]);
  const quality = qualityChartData();
  renderChart("edges", {
    color: ["#167386", "#6851a8", "#9d6500", "#16815d"],
    tooltip: tooltipOption("item"),
    legend: chartLegendOption(),
    series: [{ type: "pie", radius: ["48%", "76%"], center: ["50%", "43%"], label: { show: false }, labelLine: { show: false }, data: edgeChartData() }],
  });
  renderChart("tiers", {
    color: ["#b3261e", "#9d6500", "#16815d"],
    tooltip: tooltipOption("item"),
    legend: chartLegendOption(),
    series: [{ type: "pie", radius: ["54%", "78%"], center: ["50%", "43%"], label: { show: false }, labelLine: { show: false }, data: tierChartData() }],
  });
  renderChart("collections", {
    color: ["#167386"],
    tooltip: tooltipOption("axis"),
    grid: { top: 12, right: 14, bottom: 26, left: 80, containLabel: true },
    xAxis: valueAxisOption(),
    yAxis: categoryAxisOption(collections.map((item) => labelFor(collectionLabels, item[0]))),
    series: [{ type: "bar", barWidth: 12, data: collections.map((item) => item[1]), itemStyle: { borderRadius: [0, 6, 6, 0] } }],
  });
  renderChart("risk", {
    color: ["#9d6500", "#b3261e", "#6851a8", "#16815d", "#167386"],
    tooltip: tooltipOption("axis"),
    grid: { top: 14, right: 14, bottom: 28, left: 42, containLabel: true },
    xAxis: categoryAxisOption(riskChartData().map((item) => item.name)),
    yAxis: valueAxisOption(),
    series: [{ type: "bar", barWidth: 18, data: riskChartData().map((item) => item.value), itemStyle: { borderRadius: [6, 6, 0, 0] } }],
  });
  renderChart("statuses", {
    color: ["#16815d", "#617089", "#6851a8", "#9d6500", "#b3261e"],
    tooltip: tooltipOption("item"),
    legend: chartLegendOption(),
    series: [{ type: "pie", radius: ["46%", "74%"], center: ["50%", "43%"], label: { show: false }, labelLine: { show: false }, data: statusChartData() }],
  });
  renderChart("quality", {
    color: ["#16815d", "#b3261e"],
    tooltip: tooltipOption("axis"),
    legend: chartLegendOption(),
    grid: { top: 14, right: 16, bottom: 46, left: 42, containLabel: true },
    xAxis: categoryAxisOption(quality.map((item) => item.name)),
    yAxis: valueAxisOption(),
    series: [
      { name: "OK", type: "bar", stack: "quality", barWidth: 18, data: quality.map((item) => item.passed), itemStyle: { borderRadius: [5, 5, 0, 0] } },
      { name: "KO", type: "bar", stack: "quality", barWidth: 18, data: quality.map((item) => item.failed), itemStyle: { borderRadius: [5, 5, 0, 0] } },
    ],
  });
  if (!chartsResizeHandlerRegistered) {
    window.addEventListener("resize", () => chartInstances.forEach((chart) => chart.resize()));
    chartsResizeHandlerRegistered = true;
  }
}

function refreshCharts() {
  while (chartInstances.length > 0) {
    const chart = chartInstances.pop();
    if (chart) chart.dispose();
  }
  initCharts();
}


const particleMotionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");

function initParticleField() {
  const canvas = document.querySelector("[data-particle-field]");
  if (!canvas || !canvas.getContext) return;
  const context = canvas.getContext("2d");
  let particles = [];
  let animationId = 0;

  function createParticles(width, height) {
    const count = Math.max(42, Math.min(105, Math.floor((width * height) / 21000)));
    particles = Array.from({ length: count }, (_, index) => ({
      x: Math.random() * width,
      y: Math.random() * height,
      vx: ((index % 5) - 2) * 0.045 + (Math.random() - 0.5) * 0.12,
      vy: (((index + 2) % 5) - 2) * 0.04 + (Math.random() - 0.5) * 0.1,
      radius: 1.1 + Math.random() * 2.4,
      alpha: 0.2 + Math.random() * 0.38,
    }));
  }

  function resizeCanvas() {
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const width = window.innerWidth;
    const height = window.innerHeight;
    canvas.width = Math.floor(width * ratio);
    canvas.height = Math.floor(height * ratio);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    createParticles(width, height);
    drawParticles();
  }

  function stepParticles(width, height) {
    particles.forEach((particle) => {
      particle.x = (particle.x + particle.vx + width) % width;
      particle.y = (particle.y + particle.vy + height) % height;
    });
  }

  function drawParticles() {
    const width = window.innerWidth;
    const height = window.innerHeight;
    context.clearRect(0, 0, width, height);
    context.lineWidth = 1;
    particles.forEach((particle, index) => {
      for (let next = index + 1; next < particles.length; next += 1) {
        const other = particles[next];
        const distance = Math.hypot(particle.x - other.x, particle.y - other.y);
        if (distance < 128) {
          context.strokeStyle = `rgba(${cssVar("--particle-link-rgb", "126, 171, 238")}, ${0.11 * (1 - distance / 128)})`;
          context.beginPath();
          context.moveTo(particle.x, particle.y);
          context.lineTo(other.x, other.y);
          context.stroke();
        }
      }
      context.fillStyle = `rgba(${cssVar("--particle-fill-rgb", "109, 215, 231")}, ${particle.alpha})`;
      context.beginPath();
      context.arc(particle.x, particle.y, particle.radius, 0, Math.PI * 2);
      context.fill();
    });
  }

  function animate() {
    stepParticles(window.innerWidth, window.innerHeight);
    drawParticles();
    animationId = window.requestAnimationFrame(animate);
  }

  function start() {
    window.cancelAnimationFrame(animationId);
    if (particleMotionQuery.matches) {
      drawParticles();
      return;
    }
    animate();
  }

  window.addEventListener("resize", resizeCanvas);
  if (particleMotionQuery.addEventListener) {
    particleMotionQuery.addEventListener("change", start);
  }
  window.addEventListener("scribe-theme-change", drawParticles);
  resizeCanvas();
  start();
}

initBars();
render();
initThemeToggle();
initParticleField();
"""
