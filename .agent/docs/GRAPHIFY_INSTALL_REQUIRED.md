# Graphify Installation Required

Graphify is structural context compression for the LLM workflow. A write is
blocked unless its outputs are valid, bound to this project root, non-stub and
current for the workspace.

## Platform

- OS: `linux`
- Host: `opencode`
- Python required: `3.10+`
- Runtime policy: project-local `graphifyy==0.9.26`

No global Graphify or package-manager setup is required. From the project root,
rerun canonical TENOR INIT. It provisions the pinned runtime under
`.agent/state/runtime/toolchains/`, verifies the Graphify wheel SHA-256, builds
the graph under the shared bound and continues without a second user turn:

```text
.agent/workflow/scribe/scribe tenor-init --type cli --host <host-id>
```

The explicit `scribe graph --project-build --timeout 180` command is reserved
for a human or CI maintenance operation outside host-driven TENOR INIT. A host
model must not request it or retry it with a different bound.

The generated files must live in `.agent/state/outputs/graphify-out/` and include
`graph.json`, `GRAPH_REPORT.md`, `graph.html`, and the project-bound
`GRAPHIFY_READY.json` readiness manifest.
