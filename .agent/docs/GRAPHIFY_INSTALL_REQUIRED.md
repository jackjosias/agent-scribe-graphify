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

Then, from the project root:

```text
.agent/workflow/scribe/scribe graph --project-build --timeout 180
.agent/workflow/scribe/scribe tenor-init --type cli --host <host-id>
```

The generated files must live in `.agent/state/outputs/graphify-out/` and include
`graph.json`, `GRAPH_REPORT.md`, `graph.html`, and the project-bound
`GRAPHIFY_READY.json` readiness manifest.
