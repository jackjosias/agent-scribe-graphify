# PRD Workflow

Version: 1.4

## Read First

Before editing code, answer this gate in writing:

```text
PRD required? yes/no
Reason: <one sentence>
Architecture risk: none | low | high
Rollback tier: none | light | full
```

Rules:

- If the answer is `yes`, write or update the PRD before implementation starts.
- Do not create a PRD after implementation to justify decisions already made.
- If implementation already started and a PRD trigger is discovered, stop,
  record the current delta, write the PRD, and resume only after the PRD defines
  the target structure and validation.
- Read the `Target Structure`, `Architecture Decision Gate`, and `Completion
  Criteria` sections before any file move, import migration, barrel, or adapter.
- For architecture refactors, the PRD is invalid until it says which old paths
  will be deleted and how deletion is proven.

## Purpose

This workflow defines how to create a complete Product Requirements Document
(PRD) for future work.

A PRD is not project memory. It is a structured process document and an
implementation plan that explains what must be built, why it must be built, how
the work should be phased, how rollback will be protected, and how success will
be verified.

The PRD workflow is portable. It can be copied to another project and reused
without depending on SCRIBE, Graphify, or any project-specific tool.

## Scope

Use this workflow when a task is large enough that direct implementation would
create ambiguity, duplicated decisions, or high regression risk.

Typical triggers:

- A refactor touches multiple files or ownership boundaries.
- A refactor is small but touches behavior that already works.
- A UI subsystem needs to be split into canonical folders.
- A folder or file architecture decision could create duplicate canonical paths.
- A feature has multiple phases or acceptance criteria.
- A bug fix requires a durable process to avoid recurrence.
- A repeated manual process should become explicit and reusable.
- The implementer has meaningful doubt about regression risk.

Do not use this workflow for trivial one-file fixes.

### Rollback Tier Selection

Rollback must match risk. Do not create ceremony to look safe.

Use `none` when the work is read-only, a trivial documentation edit, or an
isolated new file with no runtime consumer.

Use `light` when a small edit touches existing files but rollback is obvious:
record the files touched, baseline command, and restore note in the PRD or
implementation notes. A SHA-256 manifest is not required.

Use `full` when the work changes existing behavior, moves files, changes import
paths, changes rendering, changes data flow, touches persistence, or has
uncertain blast radius. Full rollback uses `_Rollback` with a manifest.

## Output Location

The workflow lives here:

```text
.agent/workflow/prd/README.md
```

Actual PRD files must live near the product or subsystem they describe. When a
subsystem owns several PRDs, group them under a local `docs/prd/` folder so the
source root stays reserved for executable code, manifests, and subsystem entry
documents.

Recommended examples:

```text
components/technical-analysis/PRD-example.md
components/technical-analysis/docs/prd/example.md
app/some-domain/docs/prd/example.md
```

Use the `PRD-` filename prefix only for a standalone PRD at a subsystem root. In
`docs/prd/`, the folder already provides the document type, so filenames should
be concise and topic-based.

Current Technical Analysis PRD collection:

```text
components/technical-analysis/docs/prd/common-ui-refactor.md
components/technical-analysis/docs/prd/modals-ui-refactor.md
components/technical-analysis/docs/prd/toolbar-ui-refactor.md
```

Do not store product PRDs inside `.agent/workflow/prd/`.

## Required PRD Structure

Every PRD must contain these sections in this order.

### 1. Header

Required fields:

```text
# PRD - <Title>

Status: Draft | Approved | Implemented | Superseded
Date: YYYY-MM-DD
Owner: <team or module>
Scope: <paths or subsystem>
```

### 2. Problem Statement

State the real problem in concrete terms.

The problem statement must answer:

- What is wrong today?
- Who or what is affected?
- Why does this need a PRD instead of a direct fix?
- What happens if the work is not done?

Avoid vague statements such as "clean up code" or "improve quality" without
specific failure modes.

### 3. Current Inventory

Describe the current state before proposing the target state.

Use a table when possible:

```text
| File or Area | Current Role | Issue | Target Direction |
| --- | --- | --- | --- |
```

Keep this factual. Do not include long implementation code.

### 3.1 Local Rollback Snapshot

Before any implementation or refactor described by the PRD, define the local
rollback barrier.

This section is mandatory when the work modifies existing behavior, touches a
working feature, changes imports, moves files, changes rendering, changes data
flow, or carries any doubt about regression risk.

Use `_Rollback` as a local filesystem safety net:

```text
_Rollback/<task-slug>-<YYYYMMDD-HHMMSS>/
  MANIFEST.md
  <copied files with parent paths preserved>
```

The snapshot must be targeted. Copy only the files that the implementation is
expected to modify or that are required to restore a working baseline. Do not
copy the whole repository by default.

The PRD must state:

- Which files will be copied before edits.
- Why each file is part of the safety baseline.
- Which command or method will preserve parent paths.
- Where the snapshot manifest will live.
- Which validation commands establish the baseline before edits.
- How the snapshot will be used if a regression appears.

Recommended manifest fields:

```text
# Rollback Snapshot Manifest

Snapshot: <task-slug>-<YYYYMMDD-HHMMSS>
Created: YYYY-MM-DD HH:MM:SS
Reason: <why this snapshot exists>

| Original path | Snapshot path | SHA-256 before edit | Reason |
| --- | --- | --- | --- |

Baseline validation:

- <command and result>

Restore rule:

- Compare current file against snapshot before restoring.
- Restore only files modified by this implementation.
- Never overwrite newer human or parallel-agent changes blindly.
```

### 4. Goals

List the positive outcomes the PRD must achieve.

Rules:

- Goals must be testable.
- Goals must be scoped.
- Goals must not hide implementation details that belong in migration phases.

### 5. Non-Goals

List what is explicitly outside the PRD.

Non-goals protect the implementation from scope creep.

Examples:

- No visual redesign.
- No database migration.
- No change to public API behavior.
- No unrelated cleanup.

### 6. Target Structure

Show the intended target structure when files, modules, or boundaries are part
of the work.

Use a tree:

```text
domain/
  feature-a/
  feature-b/
  shared/
```

The target structure must define canonical ownership.

It must also answer:

- Why this structure is necessary instead of keeping the current layout.
- Which folder owns each responsibility.
- Which files are sources of truth.
- Which old files or paths will be deleted.
- Which old files or paths remain temporarily, if any, and why.

### 6.1 Architecture Decision Gate

Any PRD that moves files, splits folders, introduces subfolders, creates
barrels, or changes import paths must include an architecture gate before the
migration plan.

This gate exists because a half-refactor is worse than no refactor. Creating
domain folders while leaving root re-export files behind creates two competing
architectures: the new canonical tree and the old zombie tree. That ambiguity
costs future implementers time and makes imports drift back to the wrong path.

The gate must include these fields:

```text
Architecture Gate:

Current dependency inventory:

| Legacy path | Current role | Imported by | Consumer count | Public API? | Decision |
| --- | --- | --- | ---: | --- | --- |

Responsibility test:

- Is the current folder too broad because it mixes different responsibilities?
- Are the responsibilities large enough to justify subfolders?
- Would five well-named files in one folder be clearer than a domain tree?

Chosen strategy:

- Keep flat and rename files only.
- Move to domain folders, migrate all imports, then delete legacy files.
- Keep a temporary public adapter with owner, expiry phase, and removal proof.

Rejected strategies:

- <strategy rejected> because <concrete reason>.

Canonical source of truth:

- <path> owns <responsibility>.

Legacy removal proof:

- `rg "<legacy path or symbol>" <scope>` must return no active code matches.
- The final tree must not contain same-name root files that only re-export domain files.
```

Rules:

- Choose one architecture and finish it.
- Do not let flat files and domain folders coexist unless the old path is a
  proven public API with a time-boxed adapter.
- Do not split a folder just because "clean architecture" sounds better.
- Do not keep a file "for compatibility" unless a real consumer needs it.
- Do not create a new folder level for a single file unless future siblings are
  concrete and named in the PRD.
- A type file above the file budget is a design smell. Split by responsibility,
  not by "all types live here".
- A root `index.ts` is not automatically valid. It is a public entrypoint only
  if consumers intentionally import the folder boundary.

## Barrel, Adapter, And Zombie File Rule

Adapters and barrels are allowed only as deliberate API boundaries or temporary
migration tools.

A file is a zombie when it exists only to preserve an old path but has no active
consumer, no public API owner, no removal phase, or no validation proving why it
must remain. Zombie files are forbidden.

Examples of forbidden states:

```text
config/
  indicators/movingAverageSeries.ts  # source of truth
  movingAverageSeries.ts             # zombie re-export
```

```text
config/
  drawing/drawingToolTypes.ts        # source of truth
  TechnicalAnalysisTypes.ts          # stale barrel that still imports old names
```

If a PRD introduces compatibility adapters, it must also include:

- Why the adapter is needed.
- Which old import path it protects.
- Which canonical path replaces it.
- The exact phase where the adapter is removed.
- The validation that proves removal is safe.
- The consumer that still needs it.
- The owner responsible for deleting it.

Permanent same-name adapters are not allowed unless the PRD declares them as a
public API surface with a long-term owner.

Internal refactors should normally migrate imports to the canonical path and
delete the old files in the same PRD. If that is too risky, the PRD must say why
and must treat the adapter as active debt, not as completed architecture.

## Requirements

### Functional Requirements

Use numbered requirements:

```text
### FR-001 - <Name>

Requirement text.

Acceptance:

- Observable criterion 1.
- Observable criterion 2.
```

Each functional requirement must be verifiable.

### Non-Functional Requirements

Include relevant constraints:

- Performance.
- Accessibility.
- Security.
- Reliability.
- Maintainability.
- Testability.
- Backward compatibility.

Do not add categories that do not apply.

## Migration Plan

Break the work into phases.

Each phase must include:

- Objective.
- Actions.
- Files or areas affected.
- Local rollback snapshot requirements.
- Exit criteria.
- Rollback strategy.

Recommended phase shape:

```text
### Phase N - <Name>

Objective:

Actions:

0. Create `_Rollback/<task-slug>-<YYYYMMDD-HHMMSS>/` for the files touched by this phase.
1. Record hashes and baseline validation in `MANIFEST.md`.
2. Move consumers to the canonical path.
3. Keep behavior unchanged while the import boundary changes.

Exit criteria:

- Typecheck, lint, build when relevant, and import search are green.
- Legacy root files scheduled for deletion are gone from the active tree.
- Any remaining adapter has a named consumer, owner, and removal phase.

Rollback:

- Compare changed files against `_Rollback/<task-slug>-<YYYYMMDD-HHMMSS>/`.
- Restore only the files changed by this phase, or patch back only the broken section.
- Rerun the same validation commands that established the baseline.
```

## Validation Plan

Every PRD must specify how completion is proven.

Validation levels:

- Static validation: typecheck, lint, formatting, dead import search.
- Architecture validation: no duplicate canonical paths, no zombie files, no
  unowned barrels, no stale root adapters.
- Runtime smoke: route loads, page renders, critical UI opens.
- Regression validation: old behavior still works.
- Build validation: production build passes when feasible.
- Data validation: generated output or transformed data is correct.

Example:

```text
Validation:

- `./node_modules/.bin/tsc --noEmit`
- `npx eslint <paths>`
- `rg "<old import path>" <scope>` returns no matches
- `rg "export \\* from" <moved-folder>` returns only intentional public barrels
- `curl -I <local route>` returns 200
- Browser smoke screenshot is non-empty
- `npm run build` passes when the change affects bundling
```

## Risk Register

List the concrete risks.

Format:

```text
| Risk | Probability | Impact | Mitigation |
| --- | --- | --- | --- |
```

Only include risks that can realistically happen.

## Rollback

Define how to undo the work safely.

Rollback must say:

- Which files or phase can be reverted.
- Which `_Rollback` snapshot preserves the pre-change state.
- Whether data migration is involved.
- Whether adapters or compatibility paths are needed.
- Whether a full-file restore is safe or a targeted patch-back is required.
- Which validation proves rollback is safe.

### Local Filesystem Rollback Policy

`_Rollback` is the required local safety barrier when an implementation can
damage behavior that already works.

Use it before:

- Any refactor, small or large, that changes existing files.
- Any implementation with uncertain blast radius.
- Any feature or fix that changes rendering, data flow, persistence, network
  behavior, public contracts, imports, or user workflows.
- Any PRD phase where the implementer cannot prove regression risk is zero.

Skip it only when the change is read-only, a new isolated file, or a trivial
documentation edit where restoring the previous file has no practical value.

Rules:

- `_Rollback` is local safety state and must stay out of product commits.
- Snapshot only the target files, preserving parent paths.
- Create a `MANIFEST.md` with hashes, reason, and baseline validation.
- Run baseline validation before editing when validation is available.
- After each phase, rerun the same validation.
- If a regression appears, stop new work and compare against the snapshot.
- Restore only the implementation delta.
- Never use global destructive rollback commands for this workflow.
- Never overwrite newer human or parallel-agent edits without a diff-based merge.

Rollback execution order:

1. Stop the refactor or implementation.
2. Identify whether the failure is pre-existing or newly introduced.
3. Diff current files against the matching `_Rollback` snapshot.
4. Revert the smallest safe unit: line patch, function patch, then file restore
   only if necessary.
5. Rerun the baseline validation and the failing regression check.
6. Update the PRD implementation notes with the rollback decision.

## Completion Criteria

A PRD is complete only when:

- All required sections are present.
- Acceptance criteria are concrete.
- The target structure has canonical ownership.
- The architecture gate chooses one strategy and rejects the others explicitly.
- Dependency inventory proves which old paths are public and which are internal.
- The local rollback snapshot strategy is explicit when existing behavior can be affected.
- The migration plan includes cleanup of temporary compatibility layers and
  deletion of zombie files.
- Validation commands are listed.
- Import search proves old internal paths are no longer active.
- Risks and rollback are documented.
- The status reflects the real lifecycle state.

## Applying a PRD

When implementing an approved PRD:

1. Read the full PRD before editing.
2. Identify canonical files and temporary compatibility paths.
3. Create the required `_Rollback` snapshot and manifest before the first edit.
4. Run baseline validation when validation is available.
5. Implement the smallest safe phase first.
6. Migrate consumers to the canonical path before deleting old files.
7. Keep behavior stable unless the PRD explicitly changes it.
8. Validate after each phase.
9. If a regression appears, use the snapshot before attempting a second refactor path.
10. Delete old internal files once their imports are migrated.
11. Update the PRD status and implementation notes.
12. Remove temporary adapters after imports migrate and validation is green.
13. Do not commit or push unless explicitly instructed.

## Anti-Patterns

Avoid these failures:

- Creating a PRD that only repeats vague goals.
- Moving files without defining canonical ownership.
- Creating domain folders while keeping root re-export files without a removal
  phase.
- Calling a zombie file "compatibility" when no active consumer needs it.
- Keeping `index.ts` because it feels tidy instead of because it is a real
  public folder boundary.
- Keeping temporary adapters as a permanent architecture.
- Splitting every type into folders while leaving large catch-all type barrels
  untouched.
- Leaving two import paths for the same source of truth.
- Mixing unrelated cleanup into a PRD implementation.
- Starting a risky edit without a targeted `_Rollback` snapshot.
- Copying the whole repository into `_Rollback` instead of the affected files.
- Restoring a file blindly when newer human or parallel-agent changes exist.
- Omitting rollback.
- Omitting validation.
- Writing product PRDs inside `.agent/workflow/prd/`.
- Treating PRD content as causal memory.

## Relationship To Other Systems

This workflow is independent.

Optional integrations:

- Use Graphify or code search to build the current inventory.
- Use SCRIBE only to record causal lessons after work is complete.
- Use local test, lint, build, or browser tools to validate the PRD.

The PRD itself remains a product/process specification, not memory storage.


## Architecture Completion Doctrine

A refactor is complete only when the architecture has one canonical shape. A
partial domain split that leaves root re-export shells, stale barrels, or
same-name adapters without a removal phase is a failed refactor, even if the
new folder tree looks cleaner.

Before moving files or creating domain folders, answer this in the PRD:

```text
Does the volume and diversity of responsibilities justify a domain split?
If yes: migrate consumers to the canonical paths, delete internal legacy files,
and prove deletion with import search.
If no: keep the folder flat and improve names instead of adding architecture.
```

Mandatory rules:

- Inventory every importer before creating subfolders.
- Choose one strategy: keep flat, or move fully and delete old internal paths.
- Treat compatibility re-exports as active debt with owner, consumer, expiry
  phase, and removal proof.
- Never mark a refactor implemented while root zombie files still mirror the
  new domain sources.
- Split large type files by responsibility when they mix tool, shape, event,
  state, and persistence contracts.
- Do not self-score the architecture; prove it with import search, typecheck,
  lint, and a final tree where source-of-truth paths are unambiguous.

Canonical proof commands for this gate:

```bash
rg "from .*<legacy-path>" <scope>
rg "export \* from" <moved-scope>
./node_modules/.bin/tsc --noEmit --pretty false
npx eslint <scope> --ext .ts,.tsx
```

If any old internal import path remains, the PRD status is not `Implemented`; it
is `Draft`, `Approved`, or `Implemented with active adapter debt`.





======

LE LLM SUPERIEUR POUR UNE MEILLEUR PRONDONDEUR FD'EXPERTISE DIT ABSOLUMENT DE FAIRE CECI::[## Verdict Brutal : C'est un refactoring à moitié fait, et c'est parfois pire que rien. ### Ce qu'il a réellement fait Il a créé des sous-dossiers domaine **corrects sur le principe**, puis au lieu de **migrer les imports** dans tout le codebase, il a laissé les anciens fichiers racine comme des **coquilles vides de re-export**. Résultat : tu as maintenant **deux architectures qui coexistent** sans que personne ne sache laquelle est canonique. ``` config/ ├── indicators/movingAverageSeries.ts  ← source de vérité └── movingAverageSeries.ts             ← fantôme d'une ligne qui dit juste "export * from..." ``` C'est le pire des deux mondes. L'arbre est visuellement propre dans les sous-dossiers, mais la racine reste un cimetière de fichiers zombies. --- ### Est-ce que j'aurais fait ça ? Non, jamais. Un dev senior face à un dossier de quelques fichiers originaux aurait fait **une chose simple** : **Étape 1 — Analyser les dépendances** : qui importe quoi, combien de consommateurs externes. **Étape 2 — Choisir UNE stratégie** et l'assumer complètement : - Soit tu crées les domaines **et tu mets à jour tous les imports** dans le codebase. Travail fini, racine propre, zéro fantôme. - Soit tu gardes tout à la racine si le dossier est petit et que la complexité ne justifie pas le découpage. **Ce qu'il ne faut JAMAIS faire** : créer la structure domaine + laisser des re-exports fantômes "pour compatibilité" sans date de suppression, sans migration planifiée. --- ### Les vrais problèmes qu'il a fabriqués **1. Ambiguïté de source de vérité** — Le prochain dev qui cherche `movingAverageSeries` trouvera deux fichiers. Il va importer le mauvais ou perdre 20 minutes à comprendre. **2. `TechnicalAnalysisTypes.ts` barrel intact** — Il l'a identifié comme problème dans son audit... et n'a rien fait. C'est exactement le genre de fichier qui grossit indéfiniment et finit par tout coupler. **3. `drawing/drawingTypes.ts` à 647 lignes** — Signalé, ignoré. Un fichier de 647 lignes de types dans un domaine dessin, c'est clairement plusieurs responsabilités mélangées (types d'outils, types de formes, types d'événements, etc.). **4. Il s'est auto-noté 7/10** — Signe classique : il a fait une refactorisation superficielle et s'en est félicité avant de terminer le travail. --- ### Ce que j'aurais fait à la place Dossier de départ avec quelques fichiers ? La question à poser **avant de toucher quoi que ce soit** : > *Est-ce que le volume et la diversité des responsabilités justifient un découpage ?* Si oui — découpage complet, migration des imports, suppression des originaux, PR atomique. Pas de fantômes. Si non — on organise proprement les fichiers existants sans over-engineering. Cinq fichiers bien nommés dans un seul dossier, c'est parfaitement lisible et maintenable. Ce qu'il a produit ressemble à un dev qui a lu un article sur la Clean Architecture un vendredi soir et a commencé à refactorer sans finir.] SANS ESCEPTION!!!

======