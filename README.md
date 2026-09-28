# Glosswork

Issues and pull requests live on GitHub, at <https://github.com/glosswork/glosswork>.

You do not configure Glosswork. You tell your agent, in your own words, what you want to keep track of,
and it builds the thing: the object types, the fields, the enum options, and a description on every one
of them, written for the next agent to read. From then on it is one surface: your agent reads and writes
over MCP or REST, you and your team see the same records, comments and history in the web UI, and a
second agent in a different harness needs no integration code to use any of it.

Your agent can build. It cannot quietly destroy. Deleting a field or an object type never
applies on the call. It becomes a schema change proposal carrying its blast radius, the
affected record count and sample values, and a human approves it in the UI. That holds
whatever scope the caller has, and there is deliberately no MCP tool to approve one.

## Run it

```bash
docker build -t glosswork .
docker run -d --name glosswork -p 8000:8000 -v gw-data:/data \
  -e GW_BASE_URL=http://localhost:8000 \
  -e GW_BOOTSTRAP_ADMIN_EMAIL=you@example.com \
  -e GW_BOOTSTRAP_ADMIN_PASSWORD='<a real password>' \
  glosswork
```

One container, one process, one volume. Open <http://localhost:8000> and sign in with that
email and password. Search runs on an embedding model baked into the image, so it needs no
network and no API key. No versioned release image is published yet, so the build above is
how you get one. [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) has what a real deployment needs.

## Point an agent at it

Give your agent <https://glosswork.dev/install>. It takes itself from nothing to a running
workspace, then asks you what you want to keep track of. That page goes live with the site;
until then, [docs/AGENT_ONBOARDING.md](docs/AGENT_ONBOARDING.md) is the walkthrough for a
deployment that is already running.

## Why it exists

Glosswork is a schema-flexible, agent-native record store with an MCP endpoint, a REST API, and
a lightweight web UI. Humans and AI agents share one surface for tracking initiatives, tasks,
decisions, risks, and institutional memory.

Work-management SaaS fails a mixed human/agent team in three ways: automation interfaces cannot
filter or search by custom fields, every object type carries the same field set, and per-seat
licensing fits a self-hosted internal tool badly. Underneath those is a deeper gap: **an agent
cannot discover what a field means.** A field named `pri_2` with no description is unusable
without a human writing integration glue.

Three properties define the answer:

- **The schema is a first-class, editable object.** Object types and fields are created and
  modified at runtime through the same MCP and REST interfaces used to read and write records.
  Nothing is predefined at build time.
- **Every schema element carries a natural-language description.** Object types, fields, and
  enum options all have descriptions written for an agent to read. An agent orients itself with
  `describe_object_type` and constructs correct queries with no bespoke integration code.
- **Humans and agents are peers on one surface.** The same records, comments, and audit trail. A
  human editing a table cell and an agent calling `update_record` are the same operation with a
  different actor attached.

## Status

The MVP is complete: schema engine, record store and filter-AST compiler; REST and MCP over one
service layer; the React UI; identity, PAT scopes, sessions and CSRF; per-object-type access
control; hybrid search with a bundled embedding model; attachments on both surfaces; and backup,
export and operational hardening.

## License

Glosswork is **source-available**, not open source, under the
[Functional Source License 1.1 with an Apache 2.0 future licence](LICENSE) (FSL-1.1-ALv2).
You may read, run, modify and self-host it for any purpose other than a competing one,
including inside your company and commercially, and you may redistribute it as long as the
licence travels with it. A competing use is offering Glosswork, or something that
substitutes for it or does substantially the same thing, to others as a commercial product
or service. Two years after any given release ships, that release converts to Apache 2.0
and the restriction falls away. Third-party components bundled in the image are listed in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), and the licence text of every package the
image installs or bundles is in [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md),
generated from the lockfiles.

## Stack

Python 3.13, FastAPI, SQLite (with `sqlite-vec` and FTS5), Pydantic, `uv`. React and TypeScript
with Vite and Tailwind on the front end. `onnxruntime` runs a bundled `bge-small-en-v1.5` ONNX
graph on CPU for embeddings, so search is fully offline: there is no torch, no transformers, and
no network call at query time.

## Running from source

For working on it. The container above is how you run it.

```bash
uv sync
uv run python scripts/fetch_model.py          # embedding model, digest-verified, gitignored
uv run python -m glosswork.admin create-admin --email you@example.com --password '<pw>'
uv run uvicorn glosswork.app:app --reload
```

Then build the UI and open it:

```bash
npm --prefix web install
npm --prefix web run build
```

The app serves the built frontend from `web/dist` at `http://localhost:8000`.

To use the MCP endpoint, mint a token and point a client at `/mcp`:

```bash
uv run python -m glosswork.admin mint-token --name laptop --scope write
```

Set `GW_BASE_URL` to the deployment's own origin. Without it, agent-facing URLs are bare paths
and `create_attachment_upload` refuses; startup warns.

## Running the checks

```bash
uv run pytest -q                                    # backend
uv run ruff check . && uv run ruff format --check .  # lint
uv run mypy src                                      # types
npm --prefix web run test                            # frontend unit
npm --prefix web run lint && npm --prefix web run typecheck
npm --prefix web run e2e                             # Playwright, from the repo root
```

Playwright visual baselines are macOS-only and are deliberately excluded from CI. Run the visual
project locally before merging UI work.

## Documentation

| Document | Holds |
| --- | --- |
| [PRD.md](PRD.md) | Problem, thesis, goals, users, concepts, numbered functional requirements, success criteria |
| [AGENTS.md](AGENTS.md) | How to work in this repository: commands, non-negotiables, architecture invariants, traps |
| [CONTRIBUTING.md](CONTRIBUTING.md) | The change workflow, and what a pull request must carry |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | The parts of the system, how a request moves through them, and where each is specified |
| [docs/DESIGN_DECISIONS.md](docs/DESIGN_DECISIONS.md) | The key technical decisions, each as today's rule and its reason |
| [docs/DATA_MODEL.md](docs/DATA_MODEL.md) | Table schemas, field type catalog, indexing, audit and embedding design |
| [docs/MCP_TOOLS.md](docs/MCP_TOOLS.md) | Tool catalog, filter grammar, error conventions |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Config, proxy, auth modes, backup and restore, re-indexing |
| [docs/PERFORMANCE.md](docs/PERFORMANCE.md) | Every measured number, the platform, and how to reproduce it |
| [docs/AGENT_ONBOARDING.md](docs/AGENT_ONBOARDING.md) | How an AI agent onboards onto a *running deployment* over MCP |

Start with `PRD.md` for what the system must do, and `AGENTS.md` before changing anything.

## Development

This project is developed with AI coding agents as a routine part of the workflow.
[AGENTS.md](AGENTS.md) is the canonical instruction file, read natively by most agent tooling.
[CONTRIBUTING.md](CONTRIBUTING.md) describes the change discipline that keeps that workable.
