# Contributing

Contributions are very much welcome — FSAtlas is a small, single-maintainer project, and
third-party bug reports, fixes, and data contributions all genuinely help.

## Reporting bugs

Found something broken? Please open a [GitHub Issue](https://github.com/Leofric99/fsatlas/issues)
with:

- What you did, what you expected, and what happened instead.
- Which interface you were using — the newer dashboard (`python -m run` / Docker) or the
  classic one (`uv run fsatlas`) — since they're still separate codebases mid-migration
  (see the [homepage](../index.md#project-layout-at-a-glance)).
- Your platform (OS, browser) and how you're running FSAtlas (Docker / `uv` / packaged
  executable).

Check [Troubleshooting](../reference/troubleshooting.md) first in case it's a known,
non-bug behaviour.

## Contributing flight data

If you have flight data that could be incorporated into the project more broadly, that
would be most welcome — please open an issue to start the conversation before sending a
large dataset, so licensing/format/sourcing can be discussed up front.

## Contributing code

1. Fork the repository and create a new branch for your change.
2. Make your change. A few things worth knowing before you do:
   - There's **no CI and no automated test suite** (and no linter config) — changes are
     verified manually against a real running dev server. Start it with
     `python -m run.webapp --debug --no-browser` (autoreloads on template/static/code
     changes) and exercise the feature you touched in a browser before opening a PR.
   - FSAtlas currently has **two parallel frontends** (`run/webapp/`, the actively-
     developed one this wiki documents, and `run/web_gui.py` + `run/html/map.html`, the
     original implementation kept as a fallback during migration). If a task doesn't say
     which one, assume `run/webapp/`, and don't "fix" the other one opportunistically
     while you're in there — they're intentionally kept independent for now.
   - Shared backend logic (`run/data_loader.py`, `run/filtering.py`, `run/config.py`) is
     used by **both** frontends — a change there affects both, so check call sites in
     both before assuming a change is isolated.
3. Open a pull request describing what changed and how you tested it.

## Project conventions

- No build step for the frontend — `run/webapp/static/app.js` is one plain IIFE (no
  bundler/framework), and `app.css` uses CSS custom properties for theming. Please don't
  introduce a bundler/framework without discussing it first.
- Keep changes scoped — this is a small project maintained by one person with AI-agent
  assistance; large, sweeping refactors are harder to review and more likely to regress
  something that can't be caught by CI.

## Code of Conduct

By participating, you're expected to follow the project's
[Code of Conduct](code-of-conduct.md).
