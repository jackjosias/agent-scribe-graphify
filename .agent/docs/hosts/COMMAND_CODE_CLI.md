# CommandCode CLI — Unresolved Host Alias

## Status

`CommandCode` is ambiguous in this repository. It may refer to Codex CLI, Claude Code or another product. No configuration may be written until the exact host, installed version and official MCP documentation are identified.

## Canonical TENOR entry

Regardless of host branding, a `.agent` session begins with:

```text
TENOR INIT::[.agent/skills/init-tenor/SKILL.md]
```

Mechanical local initialization:

```bash
.agent/workflow/scribe/scribe tenor-init --type cli
```

`bootstrap` is internal/legacy, not the public V2.16 start.

## Required resolution

Before configuration:

1. record the exact executable/product name;
2. record the installed version;
3. locate the official MCP documentation;
4. map the host to an existing guide or create a dedicated guide;
5. confirm project-local configuration support;
6. configure only the current project's `python3 .agent/mcp/server_entry.py`.

Until resolved:

```text
HOST_GUIDE_INCOMPLETE
HOST_MCP_UNBOUND
```

Do not invent a filename or reuse another host's schema.

## Required V2.16 terrain proof

```text
complete MCP tools visible to LLM
root binding by sentinel hash
tenor_init_bridge OK
TENOR_INIT_READY
complete MCP micro-write
direct-write bypass refused or detected
```

Local `--list-tools` alone is not host proof.

## Terrain verdict

```text
Exact host identity: UNKNOWN
Official MCP schema: UNKNOWN
MCP tools visible: UNKNOWN
Root binding: UNKNOWN
TENOR_INIT_BRIDGE_OK: NOT_TESTED
Complete MCP micro-write: NOT_TESTED
Direct-write bypass: NOT_TESTED
Final verdict: UNKNOWN
```

Update or retire this alias under `.agent/docs/DOCUMENTATION_SYNC_POLICY.md` once the host is identified.
