from __future__ import annotations


DASHBOARD_CSS = """
:root {
  color-scheme: dark;
  --ink: #f4f7fb;
  --muted: #aebad0;
  --soft: rgba(117, 139, 172, 0.24);
  --line: rgba(190, 204, 229, 0.22);
  --paper: #090e19;
  --panel: rgba(15, 23, 38, 0.68);
  --panel-2: rgba(28, 39, 59, 0.72);
  --teal: #6dd7e7;
  --green: #6ee0a9;
  --amber: #f0bd68;
  --red: #ff8378;
  --violet: #aa9bff;
  --shadow: 0 26px 76px rgba(0, 0, 0, 0.34);
  --glass-shadow: 0 24px 76px rgba(0, 0, 0, 0.34), inset 0 1px 0 rgba(255, 255, 255, 0.10);
  --body-background: linear-gradient(135deg, rgba(8, 13, 24, 0.98), rgba(18, 28, 48, 0.90) 42%, rgba(37, 27, 44, 0.86)), conic-gradient(from 205deg at 18% 12%, rgba(77, 166, 255, 0.24), rgba(255, 255, 255, 0), rgba(84, 232, 185, 0.16), rgba(255, 184, 112, 0.15), rgba(77, 166, 255, 0.24)), linear-gradient(180deg, #070b14 0, var(--paper) 54%, #0d1424 100%);
  --body-overlay: linear-gradient(115deg, rgba(255, 255, 255, 0.08), rgba(255, 255, 255, 0.02) 42%, rgba(255, 255, 255, 0.06)), repeating-linear-gradient(120deg, rgba(255, 255, 255, 0.035) 0 1px, transparent 1px 28px);
  --metric-background: linear-gradient(180deg, rgba(255, 255, 255, 0.08), rgba(118, 142, 176, 0.08));
  --control-background: rgba(13, 21, 35, 0.78);
  --entity-background: rgba(18, 28, 46, 0.72);
  --body-copy: #d8e1ef;
  --chart-text: #c7d2e5;
  --chart-grid: rgba(190, 204, 229, 0.14);
  --tooltip-bg: rgba(10, 16, 28, 0.94);
  --tooltip-text: #f4f7fb;
  --particle-fill-rgb: 109, 215, 231;
  --particle-link-rgb: 126, 171, 238;
}
html[data-theme="light"] {
  color-scheme: light;
  --ink: #121a2a;
  --muted: #5b6981;
  --soft: rgba(235, 240, 247, 0.84);
  --line: rgba(183, 197, 216, 0.58);
  --paper: #f5f7fb;
  --panel: rgba(255, 255, 255, 0.74);
  --panel-2: rgba(251, 253, 255, 0.82);
  --teal: #167386;
  --green: #16815d;
  --amber: #9d6500;
  --red: #b3261e;
  --violet: #6851a8;
  --shadow: 0 24px 70px rgba(27, 42, 71, 0.13);
  --glass-shadow: 0 20px 60px rgba(57, 72, 103, 0.12), inset 0 1px 0 rgba(255, 255, 255, 0.72);
  --body-background: linear-gradient(135deg, rgba(255, 255, 255, 0.92), rgba(246, 250, 255, 0.66) 42%, rgba(255, 248, 241, 0.68)), conic-gradient(from 205deg at 18% 12%, rgba(64, 156, 255, 0.18), rgba(255, 255, 255, 0), rgba(124, 224, 191, 0.18), rgba(255, 193, 138, 0.18), rgba(64, 156, 255, 0.18)), linear-gradient(180deg, #f9fbff 0, var(--paper) 48%, #eef3fb 100%);
  --body-overlay: linear-gradient(115deg, rgba(255, 255, 255, 0.68), rgba(255, 255, 255, 0.12) 42%, rgba(255, 255, 255, 0.58)), repeating-linear-gradient(120deg, rgba(255, 255, 255, 0.16) 0 1px, transparent 1px 28px);
  --metric-background: linear-gradient(180deg, rgba(255, 255, 255, 0.82), rgba(248, 251, 255, 0.64));
  --control-background: rgba(255, 255, 255, 0.82);
  --entity-background: rgba(255, 255, 255, 0.76);
  --body-copy: #2c3749;
  --chart-text: #5b6981;
  --chart-grid: #eef2f6;
  --tooltip-bg: rgba(255, 255, 255, 0.96);
  --tooltip-text: #121a2a;
  --particle-fill-rgb: 28, 115, 134;
  --particle-link-rgb: 74, 126, 180;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  min-height: 100vh;
  margin: 0;
  background: var(--body-background);
  color: var(--ink);
  font: 14px/1.5 Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  overflow-x: hidden;
}
body::before {
  content: "";
  position: fixed;
  inset: 0;
  z-index: 0;
  pointer-events: none;
  background: var(--body-overlay);
  -webkit-backdrop-filter: blur(18px);
  backdrop-filter: blur(18px);
}
.particle-field {
  position: fixed;
  inset: 0;
  z-index: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}
button, input, select { font: inherit; }
.shell { position: relative; z-index: 1; width: min(1280px, calc(100vw - 32px)); margin: 0 auto; padding: 28px 0 44px; }
.hero {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 280px;
  gap: 18px;
  align-items: stretch;
  margin-bottom: 16px;
}
.hero-main, .health-card, .panel, .metric {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  box-shadow: var(--glass-shadow);
  -webkit-backdrop-filter: blur(22px) saturate(1.18);
  backdrop-filter: blur(22px) saturate(1.18);
}
.hero-main { position: relative; padding: 24px; }
.hero-top { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; }
.theme-toggle {
  display: inline-grid;
  grid-template-columns: repeat(2, minmax(68px, 1fr));
  gap: 3px;
  min-width: 148px;
  padding: 3px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: var(--panel-2);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.14);
}
.theme-toggle button {
  min-height: 30px;
  border: 0;
  border-radius: 999px;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  font-size: 12px;
  font-weight: 800;
}
.theme-toggle button[aria-pressed="true"] {
  background: var(--ink);
  color: var(--paper);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.16);
}
.theme-toggle button:focus-visible { outline: 2px solid var(--teal); outline-offset: 2px; }
.eyebrow {
  margin: 0 0 8px;
  color: var(--teal);
  font-size: 12px;
  font-weight: 800;
  text-transform: uppercase;
}
h1 { margin: 0; font-size: clamp(28px, 4vw, 48px); line-height: 1.02; letter-spacing: 0; }
.lede { max-width: 760px; margin: 14px 0 0; color: var(--muted); font-size: 16px; }
.source-line { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 18px; }
.pill {
  display: inline-flex;
  min-height: 28px;
  align-items: center;
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 3px 10px;
  background: var(--panel-2);
  color: var(--muted);
  font-size: 12px;
  font-weight: 700;
}
.health-card { padding: 18px; display: flex; flex-direction: column; justify-content: space-between; gap: 16px; }
.health-card strong { display: block; font-size: 24px; line-height: 1.1; }
.health-card span { color: var(--muted); }
.health-card.ok strong { color: var(--green); }
.health-card.warn strong { color: var(--amber); }
.health-card.bad strong { color: var(--red); }
.overview {
  display: grid;
  grid-template-columns: 360px minmax(0, 1fr);
  gap: 12px;
  align-items: stretch;
  margin-bottom: 12px;
}
.ops-panel, .chart-card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  box-shadow: var(--glass-shadow);
  -webkit-backdrop-filter: blur(20px) saturate(1.16);
  backdrop-filter: blur(20px) saturate(1.16);
}
.ops-panel { padding: 18px; }
.ops-panel h2 { margin-bottom: 16px; font-size: 22px; }
.ops-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.ops-metric {
  min-height: 92px;
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 12px;
  background: var(--metric-background);
}
.ops-metric span, .ops-lines span { display: block; color: var(--muted); font-size: 11px; font-weight: 800; text-transform: uppercase; }
.ops-metric strong { display: block; margin-top: 5px; font-size: 30px; line-height: 1; }
.ops-metric small { display: block; margin-top: 8px; color: var(--muted); }
.ops-lines { display: grid; gap: 8px; margin-top: 14px; }
.ops-lines div { display: flex; justify-content: space-between; gap: 12px; border-top: 1px solid var(--line); padding-top: 9px; }
.ops-lines strong { color: var(--ink); }
.chart-zone {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  grid-auto-rows: minmax(320px, auto);
  gap: 12px;
  align-items: stretch;
}
.chart-zone, .chart-card { min-width: 0; }
.chart-card {
  min-height: 320px;
  padding: 16px 16px 12px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
.chart-card > div:first-child { flex: 0 0 auto; }
.chart { width: 100%; flex: 1 1 auto; min-height: 230px; margin-top: 8px; }
.action-panel { margin-bottom: 12px; }
.action-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
.action-card { border: 1px solid var(--line); border-radius: 8px; background: var(--metric-background); padding: 12px; min-width: 0; }
.action-card h3 { margin: 8px 0 0; font-size: 15px; line-height: 1.25; }
.action-card p { margin: 8px 0 0; color: var(--muted); }
.action-card code { display: block; margin-top: 10px; color: var(--teal); font-weight: 800; overflow-wrap: anywhere; }
.action-priority { display: inline-flex; align-items: center; min-height: 22px; border-radius: 999px; padding: 2px 8px; background: var(--soft); color: var(--muted); font-size: 11px; font-weight: 900; }
.action-priority.p0 { color: var(--red); background: rgba(179, 38, 30, 0.1); }
.action-priority.p1 { color: var(--amber); background: rgba(157, 101, 0, 0.12); }
.action-priority.p2 { color: var(--teal); background: rgba(22, 115, 134, 0.1); }
.action-priority.ok { color: var(--green); background: rgba(22, 129, 93, 0.1); }
.panel { padding: 16px; }
.panel-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 14px; }
h2 { margin: 0; font-size: 18px; line-height: 1.2; }
.panel-note { margin: 5px 0 0; color: var(--muted); font-size: 13px; }
.workspace { display: grid; grid-template-columns: 340px minmax(0, 1fr); gap: 12px; align-items: start; }
.sidebar { display: grid; gap: 12px; position: sticky; top: 12px; }
.controls {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) repeat(4, minmax(120px, 160px)) auto;
  gap: 8px;
  align-items: center;
  margin-bottom: 12px;
}
.control, .reset-button {
  width: 100%;
  min-height: 40px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--control-background);
  color: var(--ink);
}
.control { padding: 0 11px; }
.reset-button { cursor: pointer; font-weight: 800; color: var(--teal); }
.reset-button:hover { background: var(--soft); }
.counter { color: var(--muted); font-weight: 700; white-space: nowrap; }
.distribution { display: grid; gap: 10px; }
.dist-row { display: grid; grid-template-columns: 74px 1fr auto; gap: 10px; align-items: center; }
.dist-row strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bar { height: 8px; border-radius: 999px; background: var(--soft); overflow: hidden; }
.bar span { display: block; width: 0; height: 100%; border-radius: inherit; background: var(--teal); }
.entity-grid { display: grid; gap: 10px; }
.entity-card {
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--entity-background);
  padding: 14px;
  box-shadow: 0 12px 32px rgba(57, 72, 103, 0.06);
  -webkit-backdrop-filter: blur(16px) saturate(1.12);
  backdrop-filter: blur(16px) saturate(1.12);
  transition: border-color 0.16s ease, box-shadow 0.16s ease, transform 0.16s ease;
}
.entity-card:hover { border-color: rgba(22, 115, 134, 0.55); box-shadow: var(--shadow); transform: translateY(-1px); }
.entity-top { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; }
.entity-title { min-width: 0; }
.entity-title h3 { margin: 0; font-size: 16px; line-height: 1.25; }
.entity-title code { display: inline-block; margin-top: 5px; color: var(--teal); font-weight: 800; }
.entity-card p { margin: 10px 0 0; color: var(--body-copy); }
.meta { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }
.tag { border: 1px solid var(--line); border-radius: 999px; padding: 3px 9px; background: var(--panel-2); color: var(--muted); font-size: 12px; font-weight: 700; }
.tag.hot { color: var(--red); border-color: rgba(179, 38, 30, 0.26); }
.tag.warm { color: var(--amber); border-color: rgba(157, 101, 0, 0.26); }
.tag.cold { color: var(--violet); border-color: rgba(104, 81, 168, 0.26); }
.tag.active { color: var(--green); border-color: rgba(22, 129, 93, 0.26); }
.degree { color: var(--muted); font-weight: 800; white-space: nowrap; }
.empty-state { display: none; padding: 24px; text-align: center; color: var(--muted); border: 1px dashed var(--line); border-radius: 8px; }
.empty-state.visible { display: block; }
@media (max-width: 1040px) {
  .hero, .overview, .workspace { grid-template-columns: 1fr; }
  .chart-zone { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .action-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .sidebar { position: static; }
  .controls { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .counter { grid-column: 1 / -1; }
}
@media (max-width: 680px) {
  .shell { width: min(100vw - 20px, 1280px); padding-top: 14px; }
  .hero-main { padding: 18px; }
  .hero-top { display: grid; grid-template-columns: 1fr; }
  .theme-toggle { width: 100%; max-width: 220px; }
  .ops-grid, .chart-zone, .controls, .action-grid { grid-template-columns: 1fr; }
  .ops-lines div { display: grid; grid-template-columns: 1fr; }
  .ops-lines strong { overflow-wrap: anywhere; }
  .chart-zone { grid-auto-rows: minmax(300px, auto); }
  .chart-card { min-height: 300px; }
  .chart { min-height: 214px; }
  .entity-top { display: block; }
  .degree { display: inline-block; margin-top: 8px; }
}
"""
