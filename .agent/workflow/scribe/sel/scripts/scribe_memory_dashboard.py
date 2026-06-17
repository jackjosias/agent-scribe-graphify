from __future__ import annotations

import argparse
import hashlib
import json
import time
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from scribe_dashboard_assets import DASHBOARD_CSS
from scribe_dashboard_assets_js import DASHBOARD_JS
import scribe_dashboard_view as view
from scribe_index import ensure_quick_index
from scribe_memory_admin import export_payload, json_default
from scribe_store import load_scribe


DEFAULT_DASHBOARD_PATH = Path("scribe-out") / "scribe-dashboard.html"
DEFAULT_DASHBOARD_DATA_PATH = Path("scribe-out") / "scribe-dashboard-data.json"
ECHARTS_PATH = Path(__file__).resolve().parents[1] / "vendor" / "echarts" / "echarts.min.js"


def cmd_dashboard(args: argparse.Namespace) -> int:
    if args.serve:
        return serve_dashboard(args)

    payload = dashboard_payload(args)
    html = render_dashboard(payload)
    write_dashboard_files(args, payload, html)

    summary = payload.get("summary", {})
    entity_count = summary.get("entities", 0)
    doctor_errors = summary.get("doctor_errors", 0)
    doctor_warnings = summary.get("doctor_warnings", 0)
    print("SCRIBE DASHBOARD")
    print(f"  html: {Path(args.output)}")
    if not args.no_data:
        print(f"  data: {Path(args.data_output)}")
    print(f"  entities: {entity_count}")
    print(f"  doctor: {doctor_errors} error(s), {doctor_warnings} warning(s)")
    return 0


def dashboard_payload(args: argparse.Namespace) -> dict[str, Any]:
    scribe_path = Path(args.scribe)
    if args.include_values:
        store = load_scribe(scribe_path)
        payload = export_payload(store, include_values=True)
    else:
        payload = slim_dashboard_payload(ensure_quick_index(scribe_path).payload)
    payload.update(scribe_file_state(scribe_path))
    return payload


def write_dashboard_files(args: argparse.Namespace, payload: dict[str, Any], html: str) -> None:
    output_path = Path(args.output)
    data_path = Path(args.data_output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")

    if not args.no_data:
        data_path.parent.mkdir(parents=True, exist_ok=True)
        data_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=json_default) + "\n", encoding="utf-8")


def scribe_file_state(scribe_path: Path) -> dict[str, Any]:
    raw = scribe_path.read_bytes()
    stat = scribe_path.stat()
    return {
        "source_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "source_mtime_ns": stat.st_mtime_ns,
        "source_line_count": len(raw.decode("utf-8").splitlines()),
        "generated_at_unix": int(time.time()),
    }


def scribe_stat_fingerprint(scribe_path: Path) -> tuple[int, int]:
    stat = scribe_path.stat()
    return (stat.st_mtime_ns, stat.st_size)


def serve_dashboard(args: argparse.Namespace) -> int:
    scribe_path = Path(args.scribe)
    poll_interval_ms = max(1000, int(args.poll_interval_ms))

    class DashboardRequestHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *values: object) -> None:
            return

        def do_GET(self) -> None:
            route = urlparse(self.path).path
            if route in {"/", "/dashboard", "/dashboard.html"}:
                self.send_dashboard_html()
                return
            if route == "/api/scribe-state":
                self.send_json(scribe_file_state(scribe_path))
                return
            if route == "/api/scribe-events":
                self.send_scribe_events()
                return
            if route == "/api/dashboard-data":
                self.send_json(dashboard_payload(args))
                return
            body = b"Not found\n"
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return

        def send_dashboard_html(self) -> None:
            payload = dashboard_payload(args)
            html = render_dashboard(payload, live_poll_interval_ms=poll_interval_ms).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            try:
                self.wfile.write(html)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return

        def send_json(self, payload: dict[str, Any]) -> None:
            body = (json.dumps(payload, ensure_ascii=False, sort_keys=True, default=json_default) + "\n").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return

        def send_scribe_events(self) -> None:
            interval_seconds = poll_interval_ms / 1000
            last_fingerprint: tuple[int, int] | None = None
            last_keepalive = time.monotonic()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            while True:
                try:
                    fingerprint = scribe_stat_fingerprint(scribe_path)
                    if fingerprint != last_fingerprint:
                        last_fingerprint = fingerprint
                        self.write_sse_event("state", scribe_file_state(scribe_path))
                    elif time.monotonic() - last_keepalive >= 30:
                        last_keepalive = time.monotonic()
                        self.write_sse_comment("keepalive")
                    time.sleep(interval_seconds)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return

        def write_sse_event(self, event: str, payload: dict[str, Any]) -> None:
            data = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=json_default)
            self.wfile.write(f"event: {event}\ndata: {data}\n\n".encode("utf-8"))
            self.wfile.flush()

        def write_sse_comment(self, comment: str) -> None:
            self.wfile.write(f": {comment}\n\n".encode("utf-8"))
            self.wfile.flush()

    server = ThreadingHTTPServer((args.host, args.port), DashboardRequestHandler)
    server.daemon_threads = True
    host, port = server.server_address
    print("SCRIBE DASHBOARD LIVE", flush=True)
    print(f"  url: http://{host}:{port}/", flush=True)
    print(f"  source: {scribe_path}", flush=True)
    print("  reload: SSE on file change", flush=True)
    print(f"  watch: {poll_interval_ms}ms server-side", flush=True)
    print("  stop: Ctrl+C", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nSCRIBE DASHBOARD LIVE stopped")
    finally:
        server.server_close()
    return 0


def slim_dashboard_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": payload.get("source"),
        "schema_version": payload.get("schema_version"),
        "source_sha256": payload.get("source_sha256"),
        "source_mtime_ns": payload.get("source_mtime_ns"),
        "source_line_count": payload.get("source_line_count"),
        "summary": payload.get("summary", {}),
        "tiers": payload.get("tiers", {}),
        "collections": payload.get("collections", {}),
        "statuses": payload.get("statuses", {}),
        "doctor_findings": payload.get("doctor_findings", []),
        "retrieval_quality": payload.get("retrieval_quality", {}),
        "recommendations": payload.get("recommendations", []),
        "entities": [slim_entity(entity) for entity in view.as_entities(payload.get("entities"))],
    }


def slim_entity(entity: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": entity.get("id"),
        "collection": entity.get("collection"),
        "tier": entity.get("tier"),
        "status": entity.get("status"),
        "title": entity.get("title"),
        "abstract": entity.get("abstract"),
        "outgoing": entity.get("outgoing", []),
        "incoming": entity.get("incoming", []),
    }


def render_dashboard(payload: dict[str, Any], live_poll_interval_ms: int | None = None) -> str:
    summary = payload.get("summary", {})
    entities = sorted(view.as_entities(payload.get("entities")), key=view.entity_priority)
    tiers = payload.get("tiers", {}) if isinstance(payload.get("tiers"), dict) else {}
    tier_counts = {tier: len(view.as_string_list(tiers.get(tier))) for tier in ("hot", "warm", "cold")}
    collections = view.count_by_key(entities, "collection")
    statuses = view.count_by_key(entities, "status")
    edge_counts = view.edge_counts(summary)
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=json_default).replace("</", "<\\/")
    echarts_source = ECHARTS_PATH.read_text(encoding="utf-8").replace("</", "<\\/").replace("https://", "https:\\/\\/")
    return f"""<!DOCTYPE html>
<html lang="fr" data-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Tableau de bord SCRIBE</title>
  <script>
    try {{
      const storedTheme = localStorage.getItem("scribe-dashboard-theme");
      document.documentElement.dataset.theme = storedTheme === "light" ? "light" : "dark";
    }} catch (error) {{
      document.documentElement.dataset.theme = "dark";
    }}
  </script>
  <style>{DASHBOARD_CSS}</style>
</head>
<body>
  <canvas class="particle-field" data-particle-field aria-hidden="true"></canvas>
  <main class="shell">
    <header class="hero">
      <section class="hero-main">
        <div class="hero-top">
          <p class="eyebrow">Mémoire SCRIBE/TENOR</p>
          <div class="theme-toggle" role="group" aria-label="Thème du tableau de bord">
            <button type="button" data-theme-option="dark" aria-pressed="true">Sombre</button>
            <button type="button" data-theme-option="light" aria-pressed="false">Clair</button>
          </div>
        </div>
        <h1>Tableau de bord SCRIBE</h1>
        <p class="lede">Vue opérationnelle de la mémoire causale : santé, niveaux, pression de dette et entrées consultables depuis l’export SCRIBE déterministe.</p>
        <div class="source-line">
          <span class="pill">Source : {escape(str(payload.get("source") or "-"))}</span>
          <span class="pill">Schéma : {escape(str(payload.get("schema_version") or "-"))}</span>
        </div>
      </section>
      {view.health_card(summary)}
    </header>
    <section class="overview">
      <article class="ops-panel">
        <p class="eyebrow">Synthèse opérationnelle</p>
        <h2>Contrôle mémoire</h2>
        <div class="ops-grid">
          {view.ops_metric("Entrées", summary.get("entities", 0), "indexées")}
          {view.ops_metric("Causal", edge_counts["causal"], "douleur → règle")}
          {view.ops_metric("Evidence", edge_counts["evidence"], "preuve réelle")}
          {view.ops_metric("Consultation", edge_counts["consultation"], "mémoire lue")}
          {view.ops_metric("Journal", edge_counts["journal"], "session → delta")}
          {view.ops_metric("Chaud", tier_counts["hot"], "lecture prioritaire")}
          {view.ops_metric("Qualité", view.retrieval_quality_label(payload), view.retrieval_quality_detail(payload))}
        </div>
        <div class="ops-lines">
          <div><span>Diagnostic</span><strong>{escape(view.health_label(summary))}</strong></div>
          <div><span>Densité</span><strong>{escape(view.density_label(summary))}</strong></div>
          <div><span>Dette active</span><strong>{view.active_debt_count(entities)}</strong></div>
        </div>
      </article>
      <section class="chart-zone">
        {view.chart_card("Liens par type", "causalité, preuve, consultation, journal", "edges")}
        {view.chart_card("Répartition des niveaux", "pression de récupération chaud / tiède / froid", "tiers")}
        {view.chart_card("Collections", "volume mémoire par collection", "collections")}
        {view.chart_card("Risque", "avertissements, dette active, backlog froid", "risk")}
        {view.chart_card("Statuts", "entrées actives, héritées ou résolues", "statuses")}
        {view.chart_card("Qualité retrieval", "smoke eval par surface SCRIBE", "quality")}
      </section>
    </section>
    <section class="panel action-panel">
      <div class="panel-head">
        <div>
          <h2>Actions recommandées</h2>
          <p class="panel-note">Priorités calculées depuis doctor, eval, dettes et audit causal.</p>
        </div>
      </div>
      <div class="action-grid">{view.recommendation_cards(payload.get("recommendations"))}</div>
    </section>
    <section class="workspace">
      <aside class="sidebar" aria-label="Distributions de la mémoire SCRIBE">
        <article class="panel">
          <div class="panel-head">
            <div>
              <h2>Niveaux mémoire</h2>
              <p class="panel-note">Pression de récupération par température.</p>
            </div>
          </div>
          <div class="distribution">{view.distribution_rows(tier_counts, view.TIER_LABELS)}</div>
        </article>
        <article class="panel">
          <div class="panel-head">
            <div>
              <h2>Collections</h2>
              <p class="panel-note">Répartition du signal mémoire.</p>
            </div>
          </div>
          <div class="distribution">{view.distribution_rows(collections, view.COLLECTION_LABELS)}</div>
        </article>
        <article class="panel">
          <div class="panel-head">
            <div>
              <h2>Statuts</h2>
              <p class="panel-note">État opérationnel des entrées.</p>
            </div>
          </div>
          <div class="distribution">{view.distribution_rows(statuses, view.STATUS_LABELS)}</div>
        </article>
      </aside>
      <section class="panel">
        <div class="panel-head">
          <div>
            <h2>Explorateur mémoire</h2>
            <p class="panel-note">Recherche et filtrage sans serveur ; toutes les données sont intégrées dans ce fichier.</p>
          </div>
          <span class="counter" data-visible-count>{len(entities)} visibles / {len(entities)} au total</span>
        </div>
        <div class="controls">
          <input class="control" data-filter="query" type="search" placeholder="Rechercher ID, titre, résumé...">
          {view.select_control("tier", "Tous les niveaux", sorted(tier_counts), view.TIER_LABELS)}
          {view.select_control("collection", "Toutes les collections", sorted(collections), view.COLLECTION_LABELS)}
          {view.select_control("status", "Tous les statuts", sorted(statuses), view.STATUS_LABELS)}
          {view.sort_control()}
          <button class="reset-button" type="button" data-reset>Réinitialiser</button>
        </div>
        <div class="entity-grid" data-entity-grid>{view.entity_rows(entities)}</div>
        <div class="empty-state" data-empty-state>Aucune mémoire ne correspond aux filtres actuels.</div>
      </section>
    </section>
  </main>
  <script type="application/json" id="scribe-data">{payload_json}</script>
  <script>{echarts_source}</script>
  <script>{DASHBOARD_JS}</script>
  {live_reload_script(payload, live_poll_interval_ms)}
</body>
</html>
"""


def live_reload_script(payload: dict[str, Any], poll_interval_ms: int | None) -> str:
    if poll_interval_ms is None:
        return ""
    interval = max(1000, int(poll_interval_ms))
    source_hash = json.dumps(str(payload.get("source_sha256") or ""))
    return f"""<script>
(() => {{
  if (!window.location.protocol.startsWith("http")) return;
  const eventEndpoint = "/api/scribe-events";
  const fallbackEndpoint = "/api/scribe-state";
  const pollIntervalMs = {interval};
  const fallbackIntervalMs = Math.max(30000, pollIntervalMs * 10);
  let currentHash = {source_hash};
  let fallbackTimer = null;

  function applyState(state) {{
    if (!state || typeof state !== "object") return;
    if (state.source_sha256 && currentHash && state.source_sha256 !== currentHash) {{
      window.location.reload();
      return;
    }}
    if (state.source_sha256) currentHash = state.source_sha256;
  }}

  async function checkFreshnessFallback() {{
    try {{
      const response = await fetch(fallbackEndpoint, {{ cache: "no-store" }});
      if (!response.ok) return;
      applyState(await response.json());
    }} catch (error) {{}}
  }}

  function startFallback() {{
    if (fallbackTimer !== null) return;
    fallbackTimer = window.setInterval(checkFreshnessFallback, fallbackIntervalMs);
  }}

  if (!("EventSource" in window)) {{
    startFallback();
    return;
  }}

  const source = new EventSource(eventEndpoint);
  source.addEventListener("state", (event) => {{
    try {{
      applyState(JSON.parse(event.data));
    }} catch (error) {{}}
  }});
  source.onerror = () => {{
    source.close();
    startFallback();
  }};
  window.addEventListener("beforeunload", () => source.close(), {{ once: true }});
}})();
</script>"""
