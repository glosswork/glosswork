# Architecture

A map of Glosswork for someone arriving with no context: what the parts are, how a request moves
through them, and where each part is specified. It restates nothing the specifications already
hold. The rules behind the shape are in [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md), cited here
by ID; the measured numbers are in [PERFORMANCE.md](PERFORMANCE.md).

## 1. What it is

Glosswork is one container image running one process against one data volume. The process
serves a REST API, an MCP endpoint and the web app, and keeps everything it stores in one SQLite
database file plus a directory of attachment files on the same volume (DD-5).

The same image runs self-hosted and hosted. Anything that differs between the two is
configuration of that one image, never a separate build: read-only mode, for example, is a
setting, and a deployment with it on behaves the same whoever runs it (DD-38).

## 2. The parts

| Part | Where | What it does |
| --- | --- | --- |
| Application | `src/glosswork/app.py` | Builds the FastAPI app, its lifespan (migrations, worker start and stop) and the one error handler |
| Request edge | `src/glosswork/middleware.py`, `src/glosswork/scopes.py` | Request id, body bound, credential resolution, CSRF, scope and read-only checks |
| REST adapter | `src/glosswork/routes/` | Translates HTTP into service calls and results into responses |
| MCP adapter | `src/glosswork/mcp_server/` | The tool catalog, per-call identity, and the same translation for MCP |
| Service layer | `src/glosswork/services/` | Every rule: validation, authorization, audit, version checks (DD-3) |
| Repositories | `src/glosswork/repositories/` | The interfaces and their one SQLite implementation; the only place SQL lives (DD-2) |
| Filter compiler | `src/glosswork/compiler.py`, `src/glosswork/filters.py` | Turns the filter grammar into SQL, and the only code that knows about `json_extract` |
| Migration runner | `src/glosswork/migrations.py` | Numbered migrations, applied at startup, never edited once on `main` (DD-6) |
| Embedding worker | `src/glosswork/services/embedding_worker.py` | Drains the embedding queue in the background (DD-34, DD-35) |
| Operator CLI | `src/glosswork/admin.py` | First administrator, tokens, roles and grants, from a shell |
| Web app | `web/src/` | React and TypeScript, built to static assets and served by the same process |

## 3. A request, end to end

A REST request passes through the edge before any route runs. The edge assigns a request id,
refuses a body over the configured bound, and resolves the credential: a bearer token through the
one token resolver, or, only when there is no `Authorization` header, the session cookie
(DD-8, DD-9). A cookie-authenticated write must also carry the session's CSRF token (DD-10). The
result is an actor, carrying principal, agent label, auth method, surface and request id (DD-4,
DD-17). A path that needs no credential runs as an anonymous reader (DD-15).

The route then checks the credential's scope against the scope it declares, and, when read-only
mode is on, refuses a write (DD-11, DD-38). It calls the service layer, which checks the caller's
grant on the object type, validates the input against its bounds, and checks the version the
caller last saw (DD-11, DD-18). The repository performs the write in one transaction, and the
audit rows for it are written in that same transaction. An error at any step is one error
envelope with a code, a message and details (DD-19).

An MCP tool call takes the same path from the adapter inward. The adapter resolves the bearer
token through the same resolver, gates the call on the tool's declared scope, and calls the same
service function a REST route calls (DD-8). The two surfaces agree because there is only one
place that decides (DD-3).

## 4. Data

- **Records** of every object type share one table, with user field values in a JSON column and
  an expression index for each indexed field (DD-1).
- **Object types and fields** are rows describing that JSON. A schema change an agent proposes
  is a schema proposal until an administrator approves it.
- **Relations** are rows in a link table, so many-to-many and self-referential relations need
  nothing special.
- **Comments** have their own table (DD-7).
- **Attachments** are files on the volume, referenced by rows in the database.
- **Audit events** record every change, and their ids are the cursor of the change feed agents
  read. Reverting a change is an ordinary update (DD-21), and a row stores who last changed it
  (DD-26).
- **Migrations** evolve the schema, one numbered step at a time (DD-6).

The tables, columns and indexes are in `docs/DATA_MODEL.md`.

## 5. Identity and access

A **principal** is a user or a service account, with a system role. A principal holds **access
tokens** (for agents and scripts) and, in a browser, **sessions** (DD-9). What a call may do
follows three axes, credential scope, system role and the per-type grant, and the credential
only ever narrows (DD-11). Authority always comes from the server's own records, never from the
input (DD-12). Passwords, and the credentials that die with them, follow DD-13 and DD-14.

An agent that must upload a file receives an **upload ticket**, a token narrowed to that one
upload (DD-16). An **agent label** says which agent acted; it is attribution and grants nothing
(DD-17).

## 6. Search

Search has two indexes in the same database file as the records: FTS5 for keywords and
`sqlite-vec` for vectors (DD-31). Writes enqueue embedding jobs, and the embedding worker drains
them with a model bundled in the image (DD-32). A query runs both arms and fuses the rankings, and
a committed golden set gates the quality of the result (DD-33). When a schema change makes a field
searchable, the index catches up after the change commits (DD-34).

The chunking, the fusion and the index tables are in `docs/DATA_MODEL.md` section 10.

## 7. Agents

An agent connects over MCP with a token. The tool list it sees is filtered by its credential's
scope, and the tools are generic over every object type, so an agent discovers the schema rather
than being built for it. The endpoint teaches itself: short instructions, and a
`describe_capabilities` tool that is the manual (DD-30). Files move as handles in tool calls and
as bytes over HTTP (DD-29).

The tool catalog, the filter grammar and the error codes are in `docs/MCP_TOOLS.md`; how an agent
onboards onto a running deployment is in `docs/AGENT_ONBOARDING.md`.

## 8. Operating it

Configuration is environment variables, read once at startup. At startup the process applies
migrations, and it can hand a program its first administrator's token over HTTP (DD-37). A backup
is one artifact taken from the running deployment, and restore is an operator procedure (DD-36).
An operator can freeze a workspace read-only (DD-38) and read its aggregate usage with a
credential the workspace cannot mint (DD-39). What the product does not do is written down
(DD-40).

Running, upgrading, backing up and monitoring a deployment are in `docs/DEPLOYMENT.md`.

## 9. The web app

The web app is React and TypeScript, using TanStack Query and TanStack Table, built to static
assets the same process serves. Its API types are generated from the server's OpenAPI document,
never written by hand. Every person and agent renders through one attribution component (DD-26),
a screen hides the controls the caller's level does not reach and says so once (DD-42), and the
design language is specified in one document (DD-41). The table's filters and its new-record form
follow DD-43 and DD-44.

The design system and every screen are in `docs/DESIGN.md`.

## 10. Where to read next

| Document | Read it for |
| --- | --- |
| `PRD.md` | What the system must do: goals, users, concepts, numbered requirements |
| `docs/DESIGN_DECISIONS.md` | The rules behind the design, and why |
| `docs/DATA_MODEL.md` | Tables, field types, indexing, audit and search storage |
| `docs/MCP_TOOLS.md` | The tool catalog, the filter grammar, error conventions |
| `docs/AGENT_ONBOARDING.md` | How an agent works against a running deployment |
| `docs/DEPLOYMENT.md` | Configuration, proxy, authentication, backup, read-only mode |
| `docs/PERFORMANCE.md` | Every measured number, and how to reproduce it |
| `docs/DESIGN.md` | The design system and the screens |
| `AGENTS.md` | How to change the code: commands, invariants, traps |
