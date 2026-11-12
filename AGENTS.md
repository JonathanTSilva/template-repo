# AGENTS.md

Instructions for AI coding agents working in this repository. This file is the single source; `CLAUDE.md` only imports it.

## Overview

template-repo: the Copier template that every active repository of JonathanTSilva is rendered from. Everything under `template/` is what rendered repositories get. Everything at the root is this repository's own tooling and tests, and never leaks into a render (`_subdirectory: template` in `copier.yml`).

- Kind: `template`
- Stack: Python (uv, ruff, pyright, pytest), used for the tests only
- Visibility: public
- License: mit

`.copier-answers.yml` is not needed at the root: this repository is the template, not a render of it.

## Setup

```sh
uv sync
pre-commit install --install-hooks
```

## Commands

| Task | Command |
|---|---|
| Test | `uv run pytest -q` |
| Lint | `uv run ruff check` and `uv run pyright` |
| Format | `uv run ruff format` |

The tests render the template from the committed `HEAD`, so commit before running them. They read `standard.yml` from `../.github` (or `JTS_STANDARD_DIR`) and fall back to the vendored copy `tests/fixtures/standard.yml`. When `standard.yml` changes in `.github`, copy it over; a test fails when the two differ.

CI runs the same checks through the shared workflows in [`.github`](https://github.com/JonathanTSilva/.github). The single required check is `ci`.

## Conventions

- Branches: `<type>/<issue>-<slug>`, for example `feat/12-add-parser`. See [branches and commits](https://github.com/JonathanTSilva/.github/blob/main/handbook/branches-and-commits.md).
- Commits and PR titles: Conventional Commits. Squash-merge only.
- Template files are Jinja (`.jinja` suffix) and render with `trim_blocks` and `lstrip_blocks`. Re-render for every stack after a change and run the tests.
- A new rendered file means a test for it and, when the audit expects it, an entry in `standard.yml` in the `.github` repository.
- Whole handbook: [handbook](https://github.com/JonathanTSilva/.github/blob/main/handbook/README.md).

## Boundaries

- Never commit secrets, tokens, `.env` files or real data.
- Never name a private repository in the template, the tests or a commit message: this repository is public.
- Never push to `main`. Work on a branch and open a pull request.
- Never force-push.
- Do not rename, archive, delete or change the visibility of this repository.
- Do not edit `LICENSE`.

## Model routing

Three subagents in `.claude/agents/` carry fixed models, so routing is built in.

| Agent | Model | Use for |
|---|---|---|
| `architect` | Opus | Thinking: designs, plans, trade-offs, reviews, anything ambiguous or irreversible |
| `builder` | Sonnet | Building: implementing a scoped task from a plan, writing tests, refactors |
| `mechanic` | Haiku | Mechanical work: precise, checklist-driven, repetitive edits with a verification command |
