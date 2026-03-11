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
- Run the most relevant tests or verification commands after changes.
- Add tests when behavior changes and the codebase has a sensible place for them.
- Do not hardcode secrets in tracked files.
- Put durable tunables and timing constants in shared configuration rather than scattering them.
- Do not create ad hoc markdown notes for implementation work. Put durable documentation in the existing docs structure.
- Keep new documentation in established locations such as `docs/platform/`, `docs/research/`, or `docs/adr/`.
- When architecture, runtime boundaries, or workflow expectations change, update the relevant platform docs.

## Tooling Notes

- Cursor is no longer the canonical workflow for this repo.
- Do not add or maintain `.cursor/` rule files as a source of truth.
- Keep agent-facing repo guidance in `AGENTS.md`.
- Prefer generic repo logging and debug paths over editor-specific directories.

## Git Workflow

- Branch feature work from `dev`.
- Merge feature branches into `dev`.
- Promote `dev` into `master`.
