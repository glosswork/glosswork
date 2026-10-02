# Glosswork: Product Requirements Document

**Status:** MVP shipped. Durable specification; changes run through GitHub issues and pull
requests (see [CONTRIBUTING.md](CONTRIBUTING.md)).
**Companion docs:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/DESIGN_DECISIONS.md](docs/DESIGN_DECISIONS.md),
[docs/DATA_MODEL.md](docs/DATA_MODEL.md), [docs/MCP_TOOLS.md](docs/MCP_TOOLS.md)
**How to work here:** [AGENTS.md](AGENTS.md), [CONTRIBUTING.md](CONTRIBUTING.md)

---

## 1. Problem statement

Builders and collaborative teams need to track initiatives, tasks, decisions, risks,
processes, and institutional memory. That work is increasingly performed by a mix of humans
and AI agents operating on the same set of records. Existing SaaS work-management tools fail
this use case in three specific ways:

1. **Query expressiveness through automation interfaces.** The MCP tooling of the
   work-management tools such teams use today typically cannot filter or search by custom
   fields. Since nearly all of a team's meaningful data lives in custom fields, an agent cannot
   answer basic questions such as "which initiatives are at risk and owned by Finance."
2. **Uniform object shape.** Every object type carries the same field set regardless of whether
   it is a task, a decision, or a process. The needs here are simultaneously simpler (fewer
   fields) and more varied (different fields per type).
3. **Cost and control.** A per-seat SaaS license is a poor fit for a system that should be
   trivially deployable, self-hosted, and extended by the team that uses it.

There is also a deeper gap that no incumbent addresses: **agents cannot discover what a field
means.** A field named `pri_2` with no description is unusable to an agent without a human
writing custom integration glue. Making field semantics machine-readable is the differentiating
capability of this product.

## 2. Product thesis

Glosswork is a **schema-flexible, agent-native record store with a human UI on top.**

Three properties define it:

- **The schema is a first-class, editable object.** Object types and their fields are created
  and modified at runtime through the same MCP and REST interfaces used to read and write
  records. Nothing is predefined at build time.
- **Every schema element carries a natural-language description.** Object types, fields, and
  enum options all have descriptions written for an agent to read. An agent orients itself by
  calling `describe_object_type` and can then construct correct queries and writes with no
  bespoke integration code.
- **Humans and agents are peers on one surface.** The same records, the same comments, the same
  audit trail. A human editing a cell in a table view and an agent calling `update_record` are
  the same operation with different actors attached.

## 3. Goals and non-goals

### Goals (MVP)

- Runtime-definable object types and fields, via MCP and via UI, with descriptions on every element.
- Expressive filtering, sorting, and pagination over user-defined fields, exposed identically
  through REST and MCP.
- Typed relations between records, including many-to-many and self-referential.
- Threaded-free running comments on every record as the primary human/agent communication channel.
- Semantic and hybrid search across long-text fields and comments, fully offline.
- Complete field-level audit of every change, attributable to a human, an agent label, or a
  service account.
- Okta OIDC SSO plus a standalone local-account mode, selected by configuration.
- Personal access tokens with `read` / `write` / `admin` scopes for API and MCP access.
- CSV import with upsert and validation preview, and CSV export.
- Table view with inline editing, filtering, sorting, grouping, and saved views; plus a card view.
- Ships as a single container image with a single mounted data volume.

### Non-goals (MVP)

- Computed, formula, or rollup fields.
- Kanban, timeline, Gantt, or calendar views.
- Real-time collaborative editing over websockets.
- Per-record or per-object-type access control lists.
- Multi-workspace tenancy (the column is reserved; the feature is not built).
- Webhooks and push notifications (a pull-based change feed is provided instead).
- Read auditing (who viewed what).
- Text extraction, OCR, or semantic indexing of file attachment contents.
- Native mobile applications.

### Explicitly deferred but designed for

- Postgres and other backing stores behind the storage abstraction.
- Standalone (non-human-operated) agents via service-account principals.
- Multi-workspace tenancy.
- Webhook delivery layered on top of the existing change feed.

## 4. Users

| Persona | Access path | Primary needs |
| --- | --- | --- |
| **Contributor** (human) | UI via SSO or local login | Fast table editing, filtering, comments, CSV import/export |
| **Administrator** (human) | UI via SSO or local login | Schema design, approving destructive schema changes, user and PAT management, viewing all agent labels, audit review |
| **Attributed agent** | MCP or REST with a human's PAT plus an agent label | Read/write records and comments while remaining individually identifiable in the audit trail |
| **Standalone agent** *(post-MVP)* | MCP or REST with a service-account PAT | Same as above, with the service account itself as the actor |

### Scale assumptions

Under 25 human users, under 50 distinct agent labels, under 40 object types, and low hundreds of
thousands of records total. These assumptions justify SQLite as the MVP store and justify not
building sharding, caching tiers, or a background job cluster.

## 5. Core concepts

- **Principal.** Any actor that can hold credentials. Either a `user` (can log in) or a
  `service_account` (cannot log in, holds PATs only). All authorship, ownership, and audit
  attribution points at a principal.
- **Agent label.** An optional free-text string a human's agent passes with each call
  (for example `claude-code-planner`). Labels are scoped to the principal that used them, are
  auto-registered on first use, and are renameable by that user. Administrators can view all
  labels across all users. **A label is descriptive metadata, not a security boundary.**
- **Object type.** A user-defined record class, for example `initiative`, `decision`,
  `macro_process`. Carries a name, key, description, and a key prefix.
- **Field.** A typed, described attribute on an object type.
- **Record.** One instance of an object type. Carries a UUID, a human-readable key
  (`INIT-014`), a version counter, and a JSON value document.
- **Relation.** A directed link between two records, declared by a relation field on an object
  type and stored in a link table.
- **Comment.** An append-only-by-default note on a record, editable and soft-deletable by its
  author, with full audit history. The primary narrative channel for humans and agents.
- **Saved view.** A named, shareable combination of object type, filter, sort, grouping,
  visible columns, and display mode.
- **Audit event.** An immutable record of one change to one thing, including field-level before
  and after values.

## 6. Functional requirements

### 6.1 Schema engine

- **FR-S1.** Administrators may create, read, update, and delete object types via UI and via
  `admin`-scoped MCP/REST calls. **A field key may not be one of the eight system pseudo-field
  names** (DD-20): `key`, `created_at`, `updated_at`, `created_by`, `updated_by`,
  `deleted_at`, `comment_count`, `last_comment_at` (FR-R7). A field of that name would shadow the
  system one in filters and sorts while a projection and a multi-type search read the real column,
  so one filter would mean two things depending on where it was asked. Object type keys are not
  covered. The set is published in `describe_capabilities` as `key_rules.reserved_field_keys`, and
  a collision that already exists in a database is reported at startup and never rewritten.
- **FR-S2.** Every object type has: `key` (stable machine identifier, immutable after creation),
  `name`, `description` (required, written for agent consumption), `key_prefix` (used for
  human-readable record keys), and an ordered list of fields.
- **FR-S3.** Every field has: `key` (immutable), `name`, `type`, `description` (required),
  `required`, `unique`, `default`, `indexed`, and type-specific configuration (enum options with
  their own descriptions, relation target, numeric precision). A field's own bound on its values
  — `max_length` on text, `min`/`max` on numbers — stays a **schema design choice** and gets no
  platform-wide default: the platform bounds the request, not the
  field's meaning, and [docs/AGENT_ONBOARDING.md](docs/AGENT_ONBOARDING.md) already tells agents to set `max_length`.
- **FR-S4.** Supported field types are exactly: `short_text`, `long_text`, `integer`, `decimal`,
  `boolean`, `date`, `datetime`, `single_select`, `multi_select`, `user_ref`, `relation`, `url`,
  `attachment`. Enum options on select fields carry their own descriptions.
- **FR-S5.** **Additive changes apply immediately.** Creating an object type, adding a field,
  adding an enum option, editing any name or description, and relaxing a constraint all take
  effect on the call.
- **FR-S6.** **Destructive changes require human confirmation.** Deleting a field, deleting an
  object type, changing a field's type, removing an enum option that is in use, and tightening a
  constraint that existing data violates all create a **schema change proposal** rather than
  applying. The call returns a proposal ID. A human administrator reviews and approves it in the
  UI. This holds regardless of caller scope. Rationale: schema mutation is the only operation in
  the system that can silently destroy data across thousands of records, and it is deliberately
  being exposed to non-technical users through agents.
- **FR-S7.** Approving a destructive proposal writes a snapshot of all affected data to the audit
  store before applying, so the change is reversible.
- **FR-S8.** A proposal shows its blast radius: affected record count, sample affected values,
  and for type changes, a per-row coercion dry-run listing values that would fail to convert.
- **FR-S9.** Field type changes apply only where every existing value coerces cleanly, unless the
  approver explicitly elects to null non-coercible values.
- **FR-S10.** Constraints supported at MVP: `required`, `unique`, and `default`. Min/max and
  regex validation are out of scope.
- **FR-S11.** An object type declares which of its fields labels its records for a human: the
  value that appears as a search hit's title, as the record's entry in a compact projection, and
  beside every link to it. It defaults to the type's first display-eligible field and is
  changeable at any time without a schema proposal, since no stored value moves. A `relation`,
  `attachment` or `user_ref` field may not be one. When nothing is chosen, or the chosen field is
  deleted, the system falls back to the first non-relation field by declared position.
  (DD-23.)

### 6.2 Records and querying

- **FR-R1.** Records support create, read, update, soft delete, and restore.
- **FR-R2.** Every record has an immutable UUID and a human-readable key of the form
  `<PREFIX>-<n>` (for example `INIT-014`), unique within the deployment, assigned transactionally
  at creation and never reused. Both are accepted anywhere a record is referenced.
- **FR-R3.** Every record carries a monotonically increasing `version` integer.
- **FR-R4.** **Writes are version-checked.** Updates accept `expected_version`. A mismatch returns
  HTTP 409 with the current version and the conflicting fields, so the caller can merge. A
  `force: true` flag bypasses the check for cases where last-write-wins is acceptable. Rationale:
  agents will race humans and each other, and silent overwrites of a status field are exactly the
  failure mode that erodes trust in a shared surface.
- **FR-R5.** The query interface supports a nested boolean filter tree (`and` / `or` / `not`)
  over any field including user-defined ones, with per-type operators. See
  [docs/MCP_TOOLS.md](docs/MCP_TOOLS.md) for the complete grammar.
- **FR-R6.** Queries support multi-key sorting, cursor pagination, and field projection.
- **FR-R7.** System pseudo-fields are queryable alongside user fields: `key`, `created_at`,
  `updated_at`, `created_by`, `updated_by`, `deleted_at`, `comment_count`. Every paginated read is
  bounded (DD-18): `limit` is capped per surface-independent constant — query at
  1,000, history and comments at 500, search at 50, changes at 1,000, audit at 500 — and a filter
  nests at most 32 nodes deep. All of them are published in `describe_capabilities`, enforced in
  the service layer so REST and MCP refuse identically, and a cursor whose values have been
  altered is `validation_failed` rather than a 500.
- **FR-R8.** Relative date tokens are accepted in date filters (`@today`, `@today-7d`,
  `@start_of_month`, `@now+30d`) so agents can express recency without computing dates.
- **FR-R9.** A `@me` token resolves to the calling principal in `user_ref` filters.
- **FR-R10.** Bulk update accepts a filter plus a value patch, returns affected count, and
  supports dry-run.
- **FR-R11.** Soft-deleted records are excluded by default and retrievable with
  `include_deleted: true`.

### 6.3 Relations

- **FR-L1.** A `relation` field declares a target object type and a cardinality
  (`one` or `many`).
- **FR-L2.** Many-to-many and self-referential relations are supported. Self-referential
  relations are the mechanism for hierarchies such as micro-processes belonging to a macro
  process.
- **FR-L3.** Relations may declare an inverse field on the target type, kept consistent
  automatically.
- **FR-L4.** Deleting a record that is the target of any relation is **blocked by default**,
  returning the blocking records. A `force: true` flag removes the links and proceeds.
- **FR-L5.** Filters support `linked_to`, `linked_to_any`, `has_links`, and `has_no_links`.
- **FR-L6.** Record reads may optionally expand linked records one level deep, returning key,
  name, and a caller-specified field subset.

### 6.4 Comments

- **FR-C1.** Every record supports an ordered list of comments.
- **FR-C2.** Comments are creatable, editable, and deletable through UI, REST, and MCP alike.
- **FR-C3.** Comment bodies are markdown.
- **FR-C4.** A comment's author is the principal that created it, plus the agent label if one was
  supplied. Comments display the acting agent, not just the human behind it.
- **FR-C5.** Authors may edit and delete their own comments. Administrators may delete any
  comment. *(Assumption, ratified as a decision in DD-11: non-admins cannot delete others'
  comments.)* Under DD-11, the administrator meant here is a principal
  holding the `admin` **level on the record's object type** (section 6.9a), not one presenting a
  credential whose scope happens to be `admin` — DD-11 makes the credential a ceiling and never a
  grant. A system `admin` still moderates everything, because its grant is unrestricted. A
  non-author's refused delete is `forbidden` when the grant fell short and `insufficient_scope`
  when the credential did; editing has no administrator path at all and still refuses a non-author
  with `validation_failed`. Enforced and tested at the service layer as well as over HTTP; the
  service-layer half is where the two-principal fixture lives, because it predates any
  principal-management surface.
- **FR-C6.** Deletes are soft. Edits retain full prior-body history in the audit store. The UI
  shows an "edited" marker with the ability to view history.
- **FR-C7.** Comment bodies are included in the semantic and keyword search index.
- **FR-C8.** `comment_count` and `last_comment_at` are queryable pseudo-fields, so an agent can
  ask for records with no recent discussion.

### 6.5 Search

- **FR-Q1.** Hybrid search combines keyword (SQLite FTS5) and vector similarity, fused with
  reciprocal rank fusion. Callers may force `semantic`, `keyword`, or `hybrid` mode.
- **FR-Q2.** Search runs across all object types by default and may be scoped to a subset.
- **FR-Q3.** Indexed content is: all `long_text` fields, all `short_text` fields where the field
  opts in, and all comment bodies. Each field carries an `embed` boolean, defaulting to true for
  `long_text` and false for `short_text`. The operative rule is the flag, not the type. A source is
  eligible when its type is `short_text` or `long_text` **and** `embed` is true, and `embed` gates
  the keyword index as well as the vector index. The defaults above supply the sentence's original
  behavior, but `embed` is a real setting on every field (`services/schema.py` accepts a change to
  it on any field, of any type), so turning it off on a `long_text` field must actually turn
  indexing off, and turning it on for a `number` field must not index anything. docs/DATA_MODEL.md
  section 10 states the rule normatively.
- **FR-Q4.** Search results identify the **hit source**: which record, and which specific field or
  which comment matched, with a highlighted snippet.
- **FR-Q5.** Search accepts a structured filter alongside the query text, so an agent can ask for
  "mentions of sales pricing on active initiatives owned by Finance."
- **FR-Q6.** Embeddings are produced by a model bundled inside the container image. **No network
  call leaves the deployment for embedding or search.**
- **FR-Q7.** Embedding generation is asynchronous and durable: writes enqueue a job in the
  database, an in-process worker drains the queue, and pending work survives a restart. A stop
  finishes the source being embedded and returns the rest of the batch to the queue; an unclean
  exit is recovered at the next start. Search reports index lag when non-trivial.
- **FR-Q8.** The embedding provider is an interface. Swapping to a larger local model or a hosted
  API is a configuration change plus a re-index, not a code change.
- **FR-Q9.** A full re-index is triggerable by an administrator and runs without downtime.
- **FR-Q10.** A `user_ref` value accepts a principal id, an email address, an exact display name,
  or `@me`, on the write path and in filters alike, resolved at one funnel and **stored as a bare
  principal id** (DD-24). An ambiguous display name is refused with the candidates named, never
  guessed. A filter resolves a deactivated principal; a write does not, except for an id already
  stored on the field. Every response document carrying a record carries a `principals` map
  resolving each referenced id to a display name.

### 6.6 MCP interface

- **FR-M1.** MCP is served over streamable HTTP from the same container and port as the REST API,
  at a distinct path.
- **FR-M2.** Authentication is a PAT in an `Authorization: Bearer` header.
- **FR-M3.** The tool set is **generic, not generated per object type**. Roughly twenty stable
  tools operate over any schema. Rationale: the tool list stays small and constant, agents do not
  need to reconnect when a schema changes, and schema evolution never breaks an agent's tool
  bindings. Discoverability is solved by `describe_object_type` returning rich descriptions
  rather than by tool-name proliferation.
- **FR-M4.** Tools are gated by token scope. A `read` or `write` token does not merely refuse
  schema-mutation tools, it **does not list them at all.** An everyday task-tracking agent cannot
  see that `delete_field` exists. The gate covers every request method that returns data,
  including the `initialize` handshake, so the capability set and the onboarding instructions
  need a credential too (DD-15); notifications are excluded, having no response. The
  SSE stream endpoint is not served, this deployment being stateless.
- **FR-M5.** Every tool that appends an audit event accepts an optional `agent` parameter
  carrying the agent label; an `X-Agent-Label` header sets a default for the whole connection;
  and an access token carries the label it was minted for. The first non-blank of the three wins.
- **FR-M6.** Tool descriptions, parameter descriptions, and error messages are written for agent
  consumption. Errors state what was wrong and what to call instead.
- **FR-M7.** `list_changes_since(cursor)` provides a pull-based change feed so agents can react to
  activity without polling every record.
- **FR-M8.** Query results are token-aware: responses default to a compact projection and include
  a `truncated` indicator with instructions to narrow the filter or request specific fields.
- **FR-M9.** Attachment bytes never travel in a tool argument or a tool result. Handles do:
  `get_attachment` and `get_record include=attachments` return an attachment document with a
  `download_url` and a `resource_link`, and the bytes are read through `resources/read` on the
  `attachment://{attachment_id}` template, where the host decides what to do with them. Text an
  agent authored is the one payload allowed inside a call (`create_text_attachment`); uploading a
  binary file is REST multipart with the same bearer token.

Full tool catalog: [docs/MCP_TOOLS.md](docs/MCP_TOOLS.md).

### 6.7 REST API

- **FR-A1.** Every capability available via MCP is available via REST. MCP is a thin adapter over
  the same service layer; the two cannot drift.
- **FR-A2.** OpenAPI 3.1 schema published at `/openapi.json` with an interactive browser at
  `/docs`.
- **FR-A3.** Authentication by PAT bearer token, or by session cookie for the UI's own calls. A
  bearer request carries `X-Agent-Label` exactly as MCP does; a cookie request does not.
- **FR-A4.** Consistent error envelope with a machine-readable `code`, human `message`, and
  `details`. This holds for **unclassified** failures too (DD-19): anything neither
  surface classified becomes `internal_error` carrying only a request id, identically on REST and
  MCP, rather than an opaque plain-text 500 on one and the exception's own message on the other.
  The detail goes to the application log, where the request id finds it. It also holds for
  **request validation** (DD-19): FastAPI's own `{"detail": [...]}` shape is replaced
  by the project envelope, the offending input is never echoed back — a value in the password
  position used to come back verbatim — and only the location and the reason of each failure ride
  in `details.errors`. And the classification is now part of the contract: input a caller can
  correct is a **4xx naming what to fix**, never a 500. A body over `GW_MAX_REQUEST_BYTES` is
  `payload_too_large` (413) with the same envelope on both surfaces.

### 6.8 User interface

- **FR-U1.** **Table view.** Inline cell editing, column show/hide/reorder/resize, multi-column
  sort, a filter builder mirroring the API grammar, grouping by any select or relation field, and
  row multi-select for bulk edit and delete. Every row leads its fields with the record's key,
  linked to that record's detail view, and the table pages through the whole filtered set with
  next/previous over the query's keyset cursor rather than stopping at one page. **A person can
  create a record here** (DD-44): a primary `New <type>` on the title line, over the
  fields the create route accepts, landing on the new record's page; relations and attachments
  are refused at create and are managed on the record page (FR-U2).
- **FR-U2.** **Card/detail view.** Full record display with all fields, editable in place through
  the same write path the table's inline cell edit uses, plus the comment thread, linked records,
  and the audit timeline. **Attachment fields are managed here** (DD-27): upload, download
  and remove sit on the card, saved through that same write path, with one row per stored id so
  an id the caller may not read is visible as withheld rather than dropped. The table cell shows how
  many files a record holds and stays read-only; filenames in the table are deliberately out,
  because `query_records` has no attachment sidecar and adding one is a read-path change with its
  own performance question.
- **FR-U3.** **Saved views.** Named, persisted, shareable across users, with one markable as the
  object type's default.
- **FR-U4.** **Schema editor.** Create and edit object types and fields with the same power as the
  admin API, including a description field prominently presented and labeled as agent-facing.
  Destructive edits route through the proposal flow with blast-radius display.
- **FR-U5.** **Search.** Global search bar returning results grouped by object type, showing the
  matching field or comment snippet.
- **FR-U6.** **CSV import wizard.** Upload, column mapping (with automatic suggestions by name
  similarity), upsert key selection, dry-run validation report listing per-row errors, then commit.
- **FR-U7.** **CSV export.** Exports the current view honoring active filters, sorts, and column
  selection. **Exported cells are inert** (DD-20): a text or select cell that would be
  read as a formula by a spreadsheet is neutralized, so opening the file cannot execute what a
  writer stored.
- **FR-U8.** **Activity.** Every write as a feed at `/activity`, filtered by chips over record,
  person, agent label, object type, field or time range; person and agent are pickers, not ids.
- **FR-U9.** **People & agents, and Setup.** Two screens, not one Settings page, and `/settings`
  redirects to `/people` (`docs/DESIGN.md` 8.6). **`/people`:** the people directory, each row's
  role settable to any of the three, its status shown, and an active local account's password
  resettable by an administrator other than its owner (FR-I17); service accounts; and every agent label
  grouped by owner, the caller's own renamable and the rest not, with verification as a column
  rather than a warning. **`/setup`:** personal access tokens, your own password (FR-I17), the search index and its re-index
  trigger, and the full-deployment export as a download; a backup is not offered in the browser
  (DD-36, and 8.6 says why). The PAT scope selector offers what role and credential both permit,
  so a `creator` mints the `admin` token the schema routes need. Proposals are the Inbox (8.4).
- **FR-U10.** No websockets. Views refresh on save and on an interval. Stale writes surface the
  409 conflict clearly with a side-by-side merge prompt.
- **FR-U11.** **Per-object-type access in the browser.** Every screen about an object type shows
  only the controls the caller's `your_access` level reaches, and carries exactly one statement of
  that level so the absence is explained once rather than N times or not at all (DD-42). **A
  principal holding `admin` on a type** manages that type's access from a permissions panel on
  `/schema/{key}`: its default access, one row per explicit grant with its level and a remove
  control, and a picker to grant someone new. The threshold is the level on the type, not a
  system role (DD-11), so a `creator` administering the type it defined has the panel too. Names
  on existing rows come from the response's own `principals` sidecar and the picker from the
  directory of FR-I16, because a row must name whoever it already names, live or not, while a
  picker offers only active people. A grant at `none` is shown as an explicit denial, not as an
  absent row. A refusal
  the client did not anticipate renders the server's own `forbidden` message verbatim and
  refetches the caller's access, so the affordances correct themselves without a reload.

### 6.9 Identity, authentication, and attribution

- **FR-I1.** Two authentication modes selected by configuration: `oidc` (Okta, authorization code
  flow with PKCE) and `standalone` (local email plus password with a bootstrap admin created on
  first run). Both may be enabled simultaneously. The first administrator comes from the operator
  CLI, from `GW_BOOTSTRAP_ADMIN_*` at first start, or, where `GW_BOOTSTRAP_SECRET` is set, from a
  single `POST /api/v1/bootstrap` that answers with an `admin` token and this deployment's MCP and
  sign-in addresses, so a program that provisioned the container can connect an agent without
  running a command inside it (DD-37). The local login endpoint is rate limited on
  **two** windows over one period (`GW_LOGIN_WINDOW_SECONDS`): one keyed on the (email, source
  address) pair, so an attacker cannot lock a real owner out from everywhere, and one keyed on
  the **source address alone** (DD-14), because varying the email would otherwise buy a fresh
  bucket and a full Argon2id verification on every request. Both are counted before
  the credential is looked up, so neither can become an account-existence oracle; a successful
  login clears the pair window only. A hosted workspace may sign people in by emailed code instead
  (FR-I18); password sign-in is then off, and the bootstrap claim's password is one nobody holds.
- **FR-I2.** OIDC group-to-role mapping is configurable, so Okta group membership can grant the
  administrator role (`GW_OIDC_ADMIN_GROUPS`) and the creator role
  (`GW_OIDC_CREATOR_GROUPS`); `admin` wins when an identity is in both, and an unmatched identity
  is `member`. The mapping is re-evaluated on **every** login, so removing someone from a group
  demotes them at their next sign-in. That is what makes the identity provider authoritative, and
  it is why a role set out of band with `set-role` is not durable for an OIDC principal.
- **FR-I3.** Roles at MVP: `admin` and `member`. Members read and write all records. Only admins
  mutate schema, manage principals, and approve proposals. **Superseded in part by section 6.9a:** a
  third role, `creator`, exists, and what a member may read and write is
  decided per object type rather than deployment-wide.
- **FR-I4.** PATs are minted by a signed-in user, displayed exactly once, stored as a hash, carry
  a scope (`read`, `write`, `admin`), an optional expiry, an optional agent label, and a
  last-used timestamp, and are individually revocable. No PAT's scope exceeds its minter's role.
- **FR-I5.** PATs belong to a **principal**, not to a user row. Service-account principals are
  designed into the schema at MVP even though their management UI is post-MVP.
- **FR-I6.** Agent labels auto-register on first use against the calling principal. The user sees
  new labels in their settings and may assign a display name and description. Unknown labels are
  accepted, never rejected, but are flagged `unverified` until the user names them. Rationale:
  rejecting unknown labels would cause an agent's write to fail for a typo, which is a worse
  failure than a slightly messy label list.
- **FR-I7.** Administrators can view every agent label across all principals, with first-seen,
  last-seen, and call-count.

### 6.9a Per-object-type access (DD-11)

FR-I3's two roles and FR-I4's scopes are two axes. These add a third: which object types a
principal may touch, and how. Superseding FR-I3's "members read and write all records".

- **FR-I8.** A principal holds a **level** on each object type -- `none`, `read`, `write`, or
  `admin` -- from an `object_type_grants` row when one exists, or from that type's
  `default_level` when none does. `none` is an explicit deny that overrides a permissive
  default. An `admin`-role principal is implicitly `admin` on every type.
- **FR-I9.** What a call may do is `min(credential scope, granted level)`. **The credential is a
  ceiling and can only ever narrow**: a `read` PAT held by the administrator of a type still only
  reads it, so FR-I4's scopes keep their exact meaning.
- **FR-I10.** Roles are `admin`, `creator`, and `member`, ordered. A `creator` may define its own
  object types and receives an `admin` grant on each type it creates. Only an `admin`-role
  principal reaches `/api/v1/admin/*` and `/api/v1/principals*`, which declare a required role in
  addition to their unchanged `admin` scope. The last active administrator cannot be demoted to
  any role, or deactivated.
- **FR-I11.** Every path that names an object type is enforced at the service layer: schema reads
  and writes, record reads and writes, comments (at their parent record's type), saved views, CSV
  import and export, search, the audit feed, and the change feed. A type the caller cannot read is
  **omitted** from `list_object_types` and from search results; a type the caller **names** and
  cannot read is `forbidden`.
- **FR-I12.** `list_object_types` and `describe_object_type` carry `your_access`, the already
  composed effective level, so an agent can orient itself in one call rather than by probing.
- **FR-I13.** A link into a type the caller cannot read is **redacted, not hidden**: the entry
  comes back as `{"redacted": true}` with no key, id, title, object type, or field values, so
  relation counts stay truthful across callers with different grants.
- **FR-I14.** An attachment is readable by a caller holding `read` on the object type of any
  record referencing it, or by its uploader. Upload names no record and is gated by credential
  scope alone.
- **FR-I15.** Grants are managed on **every surface**, each gated identically at `admin` scope
  plus `admin` level on the type (DD-11): over REST (`GET`/`PUT`/`DELETE
  /api/v1/object-types/{key}/grants[/{principal_id}]`), over MCP (`list_object_type_grants`,
  `set_object_type_grant`, `revoke_object_type_grant`), in the browser (FR-U11's panel), and by
  operator CLI (`grant`, `revoke`, `list-grants`, `set-role`). The MCP tools take a
  `principal_id`, not a name: `find_principals` resolves a name to an id in one prior call, and
  who may use an object type is not a question to answer by guessing. The listing response
  carries a `principals` sidecar naming every id its rows mention. Every grant change is audited
  at `entity_type = 'object_type_grant'`.
- **FR-I16.** Any authenticated principal may read a **directory** of display names and email
  addresses (`GET /api/v1/principals/directory`, `find_principals`), with no system role required.
  Its projection is fixed at `id`, `display_name`, `email`, `type`, `is_active`: `role`,
  `auth_provider`, `external_id` and `description` are not part of it, which is what separates it
  from the `admin`-gated management routes rather than widening them (DD-25). Both names were
  already published to every reader on comment and audit rows (DD-25).
- **FR-I17.** **Passwords change in the browser.** A signed-in person with a local password changes
  it on `/setup` with the current password, from a browser session only; an administrator resets an
  active local account's from its row on `/people`, never their own. Every change revokes every token
  and every other session the account holds, and a self-change loses a race with a reset (DD-13). An
  identity-provider account sees a sentence instead of a form.
- **FR-I18.** **A hosted workspace signs people in by emailed code** (DD-45). With `GW_RELAY_URL`
  and `GW_RELAY_TOKEN` set, a person enters an email address on the sign-in page and signs in with
  a six-digit code the workspace asks the hosting control plane's relay to email. A code works for
  ten minutes, once; five wrong guesses spend an address's live codes; an address is sent at most
  five codes an hour and twenty a day, counted in the database; requests and verifications count
  in the login limiter's two windows. The request answers the same whether or not the address can
  sign in. The relay is the only sender: there is no SMTP option, and a workspace without the two
  settings behaves exactly as before. `clear-sign-in-codes` resets an address's count. A code is
  stored keyed with a secret no copy of the database holds, so a backup carries no usable code.
- **FR-I19.** **An administrator invites a person by email and role** from People & agents, on a
  workspace that signs people in by code. An invite is its own list: no person exists until the
  address first signs in with a code, which creates them (or reactivates the same principal, for a
  removed local person) with the invited role. An invite lives fourteen days and only while its
  inviter is an active administrator, and can be revoked, including while the workspace is
  read-only. Removing a person ends their sessions and revokes their tokens at once, and a code
  sent before the removal no longer signs them in.

### 6.10 Audit

- **FR-D1.** Every mutation writes an immutable audit event. Audit rows are never updated or
  deleted by application code.
- **FR-D2.** Record updates write **one audit row per changed field**, with `old_value` and
  `new_value`.
- **FR-D3.** Audited entities: records, comments, relations, object types, fields, principals,
  PATs, and schema proposals.
- **FR-D4.** Every event captures: timestamp, principal, principal type, agent label, auth method
  (`session` or `pat`), surface (`ui`, `api`, or `mcp`), action, entity, field, before, after, and
  a request ID correlating all rows from one call.
- **FR-D5.** Audit history is readable through the UI, REST, and a read-scoped MCP tool, so an
  agent can answer "who last changed this status and when."
- **FR-D6.** Revert is available for a single field change or a full record version, and is
  implemented as a new forward-audited write, never as a deletion of history.
- **FR-D7.** Audit rows are retained indefinitely at MVP. An archival policy is a later concern.

### 6.11 Import and export

- **FR-E1.** CSV import supports create-only and upsert modes. Upsert matches on record key or on
  any field marked unique.
- **FR-E2.** Import is transactional per batch: a dry-run reports all validation errors, and the
  commit either fully succeeds or fully rolls back. **A dry run is a prediction of the commit**
  (DD-22): it reports the `created` and `updated` counts the commit will produce, and
  zero for both when it found any error, because a commit with any error writes nothing. "Fully
  rolls back" covers a row that fails at *write* time and not only one caught in validation: the
  whole batch runs in one transaction, so a uniqueness collision at row *n* leaves rows 1 to *n-1*
  unwritten. Rows are also validated against each other, so two rows of one file competing for the
  same unique value are a dry-run error rather than a surprise at commit.
- **FR-E3.** Import can create missing enum options when explicitly permitted, otherwise unknown
  option values are validation errors.
- **FR-E4.** Import resolves relation columns by target record key. **Import is add-only for
  relation columns: a blank cell never clears an existing link.** This is a product-level
  limitation, not a gap — clearing a link requires an explicit write.
- **FR-E5.** A full-deployment export produces schema plus all records, comments, relations, and
  audit as JSON, suitable for backup and for migrating to a future backing store.
- **FR-E6.** **CSV cells are content, never formulas** (DD-20). A `short_text`,
  `long_text`, `single_select` or `multi_select` cell beginning with `=`, `+`, `-`, `@`, a tab or
  a carriage return is prefixed with one apostrophe on export, and import strips one back under
  the matching pattern, so an export is safe to open and an export-then-import round trip is still
  exact. The escape counts the apostrophe run, which is what keeps it injective. Numeric, date,
  boolean, `url`, `user_ref`, relation and `key` cells are untouched, and the JSON export is out of
  scope: it is a document, not a spreadsheet. See docs/DATA_MODEL.md section 4.
- **FR-E7.** **CSV carries no attachment data in either direction** (DD-22). Export
  omits every `attachment` column, and an import whose header names one is **refused**, naming the
  column and pointing at `POST /api/v1/attachments`. The refusal is unconditional rather than
  triggered by a populated cell, so the rule is learnable once rather than discovered when somebody
  fills one in. *(Accepting the column and dropping its values would report `created: N` with
  `errors: []` and the ids silently gone.)*

### 6.12 Deployment and operations

- **FR-P1.** A single container image, a single process, and a single mounted volume. No sidecar
  database, no external service dependency, no network egress required at runtime.
- **FR-P2.** All state (SQLite database, attachments, vector index) lives under one volume path,
  so a volume snapshot is a complete backup.
- **FR-P3.** Configuration entirely by environment variable, documented in a shipped
  `.env.example`.
- **FR-P4.** `/healthz` liveness and `/readyz` readiness endpoints. Readiness accounts for
  pending migrations.
- **FR-P5.** Structured JSON logs to stdout including request ID, principal, agent label, and
  surface.
- **FR-P6.** Schema migrations run automatically at startup and are idempotent.
- **FR-P7.** Runs correctly behind a TLS-terminating reverse proxy on an internal VM, honoring
  standard forwarded headers.
- **FR-P8.** An operator-triggered backup endpoint produces a consistent database snapshot without
  stopping writes. An administrator takes it, or the operator's token where that is on (DD-39).
- **FR-P9.** With `GW_READ_ONLY` on, every REST and MCP write is refused with an error naming
  `GW_SUBSCRIBE_URL`, while reads, export, sign-in and five named calls keep working (DD-38).
- **FR-P10.** With `GW_OPERATOR_TOKEN` set, `GET /api/v1/usage` returns aggregate counts to an
  operator holding it and one identical refusal to everybody else, including a workspace `admin`
  token. No field carries tenant content, and unset means off (DD-39). The token opens nothing
  else unless `GW_OPERATOR_BACKUP` is on, and then only `POST /api/v1/operator/backup` (FR-P8).

## 7. Architecture decisions

The system's design is in **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**, and its key technical decisions,
each as today's rule and its reason, are in **[docs/DESIGN_DECISIONS.md](docs/DESIGN_DECISIONS.md)**.

## 8. How work is tracked

Work is planned and tracked in GitHub issues and pull requests.
[CONTRIBUTING.md](CONTRIBUTING.md) describes how a change is planned, reviewed and merged.

## 9. Success criteria

The MVP is successful when:

1. A non-technical administrator, working only through conversation with an agent, can define a
   new object type with appropriate fields and descriptions, without a developer.
2. An agent given only MCP access and no bespoke prompt engineering about the schema can answer a
   natural-language question requiring a filter over user-defined fields. This is the direct
   remedy for the first failure in section 1.
3. A search for a concept returns records where that concept appears only in a comment.
4. For any field on any record, a user can see who changed it, when, from what, to what, and via
   which agent.
5. A team's existing data has been migrated in by CSV and the tool is in daily use.
6. The whole system runs from one `docker run` with one volume.

## 10. Risks

| Risk | Mitigation |
| --- | --- |
| Agent-driven schema changes corrupt or orphan data | Proposal flow for all destructive changes, blast-radius preview, pre-change snapshot, revert |
| JSON-column query performance degrades at scale | Expression indexes on fields marked indexed; performance gate measured on a seeded large dataset; Postgres path preserved by DD-1 |
| Bundled embedding model quality proves inadequate | Provider interface plus hybrid search, so keyword recall backstops semantic recall; swapping models is config plus re-index |
| Agent labels are mistaken for a security control | Documented explicitly in the UI and API as descriptive metadata; authorization derives only from the PAT's principal and scope |
| MCP tool surface confuses agents as schemas grow | Generic tool set of fixed size; discoverability delegated to descriptions rather than tool count |
| Schema descriptions are written for humans, not agents | UI labels the description field as agent-facing with inline guidance and examples |

## 11. Open items

None. Four behaviours that could read as open are settled:

- Deleting another principal's comment needs the `admin` level on the record's object type,
  not only an `admin` credential (FR-C5, DD-11).
- Each attachment is limited by `GW_MAX_ATTACHMENT_BYTES`, enforced on every upload. The
  bytes an upload spools to the container's temporary directory before any route reads
  them are limited by the proxy's `client_max_body_size`.
- Audit history is kept indefinitely: `audit_events.id` is the FR-M7 change-feed cursor,
  and a purge would have to preserve its order (DD-40).
- Saved views are shared; there are no private saved views (DD-40).
