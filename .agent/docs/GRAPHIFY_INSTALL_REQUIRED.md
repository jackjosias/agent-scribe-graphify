# Graphify Installation Required

Graphify is structural context compression for the LLM workflow. A write is
blocked unless its outputs are valid, bound to this project root, non-stub and
current for the workspace.

## Platform

- OS: `linux`
- Host: `opencode`
- Python required: `3.10+`

```text
python3 -m pip install --user pipx
pipx install graphifyy
```

Then, from the project root, rerun canonical TENOR INIT. It owns a required
bounded Graphify rebuild and continues without a second user turn:

```text
.agent/workflow/scribe/scribe tenor-init --type cli --host <host-id>
```

The explicit `scribe graph --project-build --timeout 180` command is reserved
for a human or CI maintenance operation outside host-driven TENOR INIT. A small
model must not request it or retry it with a different bound.

The generated files must live in `.agent/state/outputs/graphify-out/` and include
`graph.json`, `GRAPH_REPORT.md`, `graph.html`, and the project-bound
`GRAPHIFY_READY.json` readiness manifest.
