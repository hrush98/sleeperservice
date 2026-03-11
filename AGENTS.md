# Repo Instructions

This file is the repo-wide instruction source for Codex.

## Start Here

Use these documents in this order:
- `docs/platform/implementation-roadmap.md` - primary mutable execution document
- `docs/platform/target-architecture.md` - target system shape
- `docs/platform/engineering-improvements.md` - engineering quality reference
- `docs/research/reference-library.md` - papers, official docs, and strategy references

## Session Workflow

Before making a meaningful change:
1. Check `docs/platform/implementation-roadmap.md` and note the active phase.
2. Read the relevant sections in `target-architecture.md` and `engineering-improvements.md`.
3. If sequencing or status has changed, update `implementation-roadmap.md` first.
4. Implement in small, testable slices.
5. Run the most relevant verification commands available in the current environment.
6. If verification is blocked by environment issues, state the blocker explicitly.

## Current Planning Rules

- Treat `docs/platform/` as the primary planning surface.
- Treat `docs/architecture.md` and `docs/project-plan.md` as legacy/history unless the task is explicitly about the old implementation.
- `docs/coherence/` remains valid detailed design material until coherence is absorbed into the shared platform.
- Prefer Polymarket-native structure, execution, and market-relationship improvements before depending on fragile external live-state feeds.

## Engineering Rules

- Use the `poly` conda environment when commands depend on the project runtime.
- Do not hardcode secrets in tracked files.
- Put durable tunables and timing constants in shared configuration rather than scattering them.
- Keep new documentation in established locations such as `docs/platform/`, `docs/research/`, or `docs/adr/`.
- When architecture, runtime boundaries, or workflow expectations change, update the relevant platform docs.

## Git Workflow

- Branch feature work from `dev`.
- Merge feature branches into `dev`.
- Promote `dev` into `master`.

