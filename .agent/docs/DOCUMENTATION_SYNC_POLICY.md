# Documentation Sync Policy — `.agent`

## Purpose

This file prevents code, prompts, generated instructions and future LLMs from operating with different versions of the `.agent` architecture.

A workflow change is incomplete until its code, executable tests, canonical documentation, generated templates and pull-request description carry the same contract.

## Canonical authority order

When documents disagree, use this order:

1. executable code and tests on the active branch;
2. `.agent/rules/tenor-init-v2.json` — machine-readable operating contract;
3. `.agent/skills/init-tenor/SKILL.md` — host-LLM initialization procedure;
4. `.agent/docs/TENOR_INIT_SINGLE_AUTHORITY.md` — architecture and proof categories;
5. `.agent/docs/V2.16_DATA_PRESERVATION.md` — runtime purge, output preservation and legacy-conflict contract;
6. `AGENTS.md` and `.agent/rules/scribe.md` — short always-on host rules;
7. `.agent/workflow/scribe/README.md` and SEL/RAG internal manuals;
8. host-specific guides under `.agent/docs/hosts/`;
9. historical reports, `.old` files and dated snapshots.

A lower-level or historical document must never override a higher-level current contract.

## Canonical V2.16 entry

Human/LLM trigger:

```text
TENOR INIT::[.agent/skills/init-tenor/SKILL.md]
```

Mechanical command:

```bash
.agent/workflow/scribe/scribe tenor-init --type <cli|extension|api|unknown>
```

The old `[[.agent/skills/init-tenor/SKILL.md]]` trigger may be recognized for compatibility but must not be emitted by new templates or docs.

`bootstrap` is internal/legacy. It is not the public installation, relocation or recovery authority in V2.16.

## Surfaces that must move together

Any change to installation, relocation, runtime purge, output migration, Graphify readiness, host binding, task workflow, memory policy or multi-agent coordination must audit and update, when relevant:

```text
README.md
AGENTS.md
.agent/rules/scribe.md
.agent/rules/tenor-init-v2.json
.agent/skills/init-tenor/SKILL.md
.agent/docs/TENOR_INIT_SINGLE_AUTHORITY.md
.agent/docs/V2.16_DATA_PRESERVATION.md
.agent/docs/V2.16_TERRAIN_FINDINGS.md
.agent/docs/hosts/README.md
.agent/docs/hosts/<ACTIVE_HOST>.md
.agent/workflow/scribe/README.md
.agent/workflow/scribe/sel/docs/AGENTS.md
.agent/workflow/scribe/sel/docs/scribe.md
.agent/workflow/scribe/sel/docs/multi-agent-installation.md
.agent/host_adapter/templates.py
.agent/workflow/scribe/sel/scripts/scribe_install_templates.py
pull-request title and body
```

Generated surfaces and their source templates must be changed in the same patch. Editing only a generated `AGENTS.md` or `.agent/rules/scribe.md` is not sufficient.

## Required update procedure

For every protocol evolution:

1. **Pre-commit scope declaration** — list code, tests, docs and generators expected to change.
2. **Implementation** — change the smallest authoritative code surface.
3. **Executable proof** — add or update tests that fail under the old contract.
4. **String drift audit** — search canonical surfaces for superseded triggers, commands, verdicts, paths and schemas.
5. **Generated-surface parity** — render or compare templates against checked-in outputs byte-for-byte.
6. **Proof classification** — label each claim as implemented, tested or terrain-proven.
7. **PR synchronization** — update the PR body so completed work is not still listed as pending.
8. **CI and terrain replay** — do not mark the PR ready until required gates are green.

## Drift patterns that must fail review

Examples of forbidden drift:

- presenting `scribe bootstrap` as the normal V2.16 start;
- using a trigger different from `TENOR INIT::[.agent/skills/init-tenor/SKILL.md]` in new docs;
- treating `server_entry.py --list-tools` as host visibility proof;
- declaring `TENOR_INIT_READY` without host visibility, root binding and bridge proof;
- documenting only `nodes + edges` after real Graphify proved NetworkX `nodes + links`;
- referring to root `graphify-out/` as canonical instead of `.agent/state/outputs/graphify-out/`;
- describing runtime purge as deletion of all `.agent/state/` after canonical outputs became preservable evidence;
- allowing legacy migration to overwrite a canonical destination instead of quarantining the conflict;
- treating a preserved Graphify output as ready without root/fingerprint revalidation;
- copying global host paths into a workspace-local guide;
- keeping a dated test count or baseline as a current architectural truth;
- documenting a fallback that bypasses an explicit safety verdict;
- updating a checked-in doc but leaving the generator on the old paradigm.

## Proof vocabulary

Every release note and PR body must distinguish:

- **Implemented** — present in code;
- **Tested** — covered by an executable test;
- **CI-proven** — passed on the referenced commit and matrix;
- **Terrain-proven** — observed in an isolated real codebase or host;
- **Not yet proven** — explicitly open gate.

Never promote one category into another.

## PR maintenance

The PR body is a live contract, not a historical first-draft note. After each major validation batch, update:

- current head scope;
- completed gates;
- terrain evidence;
- residual risks;
- exact reason the PR remains draft;
- next terminal proof.

A PR may leave draft only when its body and canonical docs agree with the code and current evidence.

## Release gate

Before merge/tag, verify:

```text
code/tests/docs/generators aligned
portable matrix green
Linux deep validation green
checkout clean after tests
runtime purge preserves canonical outputs
legacy conflicts quarantine without data loss
real Graphify proof
real codebase relocation/adoption proof
host tools visible to LLM
root binding proof
tenor_init_bridge proof
complete MCP micro-write
direct-write bypass test
multi-agent terrain test
PR body current
```

Any missing item must remain visible as an open gate; it must not be hidden by prose.
