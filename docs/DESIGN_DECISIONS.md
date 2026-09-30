# Design decisions

The key technical decisions behind Glosswork, each stated as the rule that holds today and the
reason for it. They are decided, so a change does not relitigate one without saying so: a change
that alters a rule edits its entry here in the same pull request. For how the system fits
together, read [ARCHITECTURE.md](ARCHITECTURE.md) first.

**IDs are permanent.** A decision keeps its number for as long as it exists, and a number is
never given to a different decision. A decision that stops being true is removed, and its number
stays unused, so the IDs are unique and ascending but may have gaps. Code, tests and documents
cite a decision as `DD-n`, and `tests/test_documentation_structure.py` fails if a citation names
an ID that is not here.

**An entry is the current rule.** A change that establishes or alters a rule edits the entry in
place, as the rule with its reason. It does not append a dated note.

Each entry has four parts: the rule, **Why**, **Held by** (the tests or modules that enforce
it), and **See** (where the detail is specified).

## Foundation

### DD-1: Records live in one table, with user field values in a JSON column

Every record of every object type is a row in `records`. User-defined field values live in its
`data` JSON document, and filtering and sorting read them with `json_extract`. When a field is
marked indexed, the system creates a partial expression index on that extraction, scoped to the
field's object type.

**Why.** An agent's schema change never alters a live table. Adding, retyping or removing a
field is a change to metadata and to indexes, never an `ALTER TABLE` under load.

**Held by.** `tests/test_indexing.py`, `tests/test_schema_engine.py`.

**See.** `docs/DATA_MODEL.md` sections 1 and 5.

### DD-2: Raw SQL lives only behind repository interfaces, and they have one implementation

Repository interfaces (records, schema, audit, search, blobs and the rest) are implemented once,
for SQLite, in `repositories/sqlite.py`. No SQL appears in a service, a route or a tool, and
filters compile through the filter compiler, the one module that knows about `json_extract`. The
search repository is the only module that names the vector and full-text tables.

**Why.** The service layer stays free of SQL, which keeps the storage replaceable without
building a second driver that nothing needs.

**Held by.** `tests/test_access_completeness.py`, `tests/test_search_guards.py`.

**See.** `docs/DATA_MODEL.md` section 12.

### DD-3: Business logic lives in the service layer, and REST and MCP are adapters over it

Validation, authorization, audit emission and version checking live in `src/glosswork/services/`.
The REST routes and the MCP tools translate a request into a service call and a result into a
response, and contain no logic of their own. In the web app the equivalent rule holds: logic lives
in hooks and utilities, not in a component's render body.

**Why.** Parity between the two surfaces is structural rather than a promise: both call the same
function, so they cannot disagree.

**Held by.** `tests/test_mcp_catalog.py`, `tests/test_access_completeness.py`.

**See.** `PRD.md` FR-A1; `docs/MCP_TOOLS.md` section 8.

### DD-4: Every write takes a request-scoped actor, built at the edge, with no default

An `ActorContext` carries the principal, the agent label, the auth method, the surface and the
request id. It is built at the request edge and passed into every service call, and every write
path requires it. There is no default actor a write can fall back to.

**Why.** Attribution added afterwards leaves gaps, and an audit trail with gaps is trusted
wrongly.

**Held by.** `tests/test_audit.py`, `tests/test_record_agent_label.py`.

**See.** `docs/DATA_MODEL.md` section 2, "ActorContext".

### DD-5: One process, one image, on a verified stack

The backend is Python with FastAPI, synchronous SQLAlchemy Core over `sqlite3`, Pydantic and the
official MCP SDK. The frontend is React and TypeScript, built to static assets and served by the
same process. Everything ships as one container image. No dependency version is pinned from
memory: each is verified against its registry before it is pinned, and what resolved is checked.

**Why.** `sqlite3` is synchronous, so an async data layer adds complexity without adding
concurrency. One process and one image is the deployment model. A pin from memory is a pin to a
version that may not exist or may not be current.

**Held by.** `tests/test_supply_chain.py`.

**See.** `AGENTS.md`, "Non-negotiables"; `docs/DEPLOYMENT.md` section 1.

### DD-6: Schema changes are numbered migrations, and a migration on `main` is never edited

An in-house runner applies numbered migrations at startup and records each in
`schema_migrations`. A migration already on `main` is never edited, including whitespace and SQL
comments inside a statement; a schema change is a new migration appended to the list. Each
migration's digest is pinned in `tests/migration_hashes.txt`, and a new migration appends its own
line.

**Why.** The runner recognizes an applied migration by its number alone, so an edit would never
reach a database that already ran it, and deployed schemas would silently fork from fresh
installs.

**Held by.** `tests/test_migrations_are_forward_only.py`.

**See.** `CONTRIBUTING.md`, "Migrations"; `PRD.md` FR-P6.

### DD-7: Comments live in their own table

Comments are rows in their own table, with their own audit and indexing paths, not the value of a
long-text field.

**Why.** A comment needs its own author, its own edit history and its own order, none of which a
field value provides.

**Held by.** `tests/test_comments.py`.

**See.** `docs/DATA_MODEL.md` section 7.

## Identity and access

### DD-8: One resolver turns a bearer credential into the actor, for REST and MCP alike

A bearer credential is resolved by one resolver, and both surfaces build the request's actor from
what it returns. Each MCP tool declares its required scope once, where it is registered. The tool
list a caller sees is filtered by the credential's scope, and every tool call is checked against
the same declaration again.

**Why.** One seam serves both surfaces. A tool left out of the list is still callable, so the
listing is a convenience and the call gate is the enforcement.

**Held by.** `tests/test_pat_resolver.py`, `tests/test_mcp_scope.py`,
`tests/test_rest_scope_enforcement.py`.

**See.** `docs/MCP_TOOLS.md` sections 1 and 2.

### DD-9: A browser session is a server-side row found by an opaque cookie

A browser session is a row in `sessions`, found by an opaque random cookie of which only a hash
is stored. The cookie is consulted only when the request carries no `Authorization` header. A
session is bounded by an absolute lifetime and an idle lifetime, and the principal's role and
active state are read again on every request.

**Why.** Sign-out, deactivation and demotion take effect on the next request, for sessions and
tokens alike, with no signed cookie that stays valid after the facts behind it change.

**Held by.** `tests/test_sessions.py`, `tests/test_rest_actor_resolution.py`.

**See.** `docs/DATA_MODEL.md` section 2, "sessions"; `docs/DEPLOYMENT.md` section 5.

### DD-10: A cookie-authenticated write needs SameSite=Lax and a session-bound CSRF token

The session cookie is `SameSite=Lax`, and every cookie-authenticated write also carries a CSRF
token bound to the session. Requests authenticated by a bearer token are exempt. The sign-in
callback is protected by its `state` parameter. No CORS middleware is registered.

**Why.** Each layer covers the other's gap. `Strict` would break the redirect back from the
identity provider.

**Held by.** `tests/test_sessions.py`, `tests/test_oidc_flow.py`, `web/src/api/client.test.ts`.

**See.** `docs/DEPLOYMENT.md` sections 4 and 5.

### DD-11: Access has three axes, and the credential is only ever a ceiling

Credential scope (`access_tokens.scope`, or the scope a session's role implies) says how much a
credential may do anywhere. The system role (`member < creator < admin`) says whether a principal
administers the deployment and may define object types. The grant (`object_type_grants.level`,
defaulting to the type's `default_level`) says how much a principal may do to one object type.
What a call may do is the lower of the credential scope and the granted level; the credential
only ever narrows, and scope alone is never authority.

A grant of `none` is an explicit deny. A refusal names the axis that fell short:
`insufficient_scope` for the credential, `forbidden` for the grant. A link into a type the caller
cannot read is returned redacted, never omitted. An attachment is readable through any
referencing type the caller can read, or by its uploader. A type's `admin` level is its authority
on every surface, including moderating comments: a principal without `admin` on the record's type
cannot delete another principal's comment.

**Why.** Sharing can be narrower than all or nothing, without any credential ever widening what
its principal may do.

**Held by.** `services/access.py` (`AccessService`, the only place the question is answered),
`tests/test_access_completeness.py`, `tests/test_object_type_access.py`,
`tests/test_delegated_permissions.py`.

**See.** `docs/DATA_MODEL.md` section 2, "object_type_grants"; `docs/AGENT_ONBOARDING.md`
section 4; `docs/DEPLOYMENT.md` section 3a.

### DD-12: Authority is never taken from something the caller supplies

A reference is authorized when it is written, and is not revoked when the writer's access later
narrows. Acting on another principal's tokens is an administrator's delegation, decided in one
function. Index names derive from the object type's id, never from a key the caller chose.

**Why.** Anything the caller controls can be forged, so the server decides authority and never
reads it off the input.

**Held by.** `tests/test_attachments.py`, `tests/test_delegated_permissions.py`,
`tests/test_indexing.py`.

**See.** `docs/DATA_MODEL.md` sections 5 and 8.

### DD-13: Setting a password revokes the account's credentials

Setting a password revokes every token and session the account holds, except the session making
a change to its own password. Changing your own password needs a browser session and the current
password, and commits only against the state it verified. An administrator's reset needs neither.

**Why.** A password reset is containment. And the credential handed to an agent must never be
able to lock its human out.

**Held by.** `tests/test_own_password.py`, `tests/test_local_accounts.py`.

**See.** `docs/DEPLOYMENT.md` section 5.

### DD-14: Sign-in and password-change attempts are rate limited before the credential is checked

Attempts are limited per account and source address, and per source address alone. They are
counted before the credential is checked, and a window that is blocking is never evicted.

**Why.** Password hashing work is bounded per address, without the limiter becoming an oracle for
which accounts exist or a way around itself.

**Held by.** `tests/test_login_rate_limit.py`, `tests/test_own_password.py`.

**See.** `docs/DEPLOYMENT.md` section 5.

### DD-15: The request edge fails closed

A request on a credential-exempt path runs as an anonymous reader. The lists of exempt paths are
pinned by equality. Every MCP request method the server registers is gated, and the unused SSE
stream is not served. The MCP host and origin allowlists derive from the same settings, so
enabling one never disables the other.

**Why.** The boundary has to constrain the next route or tool someone adds, not only describe the
ones that exist.

**Held by.** `tests/test_rest_scope_enforcement.py`, `tests/test_mcp_transport.py`,
`tests/test_api_workspace.py`.

**See.** `docs/DEPLOYMENT.md` section 4; `docs/MCP_TOOLS.md` section 1.

### DD-16: An upload ticket is a credential narrowed to one upload

An upload ticket is an ordinary access token narrowed to one route, one filename, one use and a
few minutes. A credential that carries a capability is refused on every other route by default,
at one predicate, and exactly one route opts back in.

**Why.** An agent has to make the upload itself, over HTTP, so it gets the narrowest credential
that can.

**Held by.** `scopes.refuse_capability_credential`, `tests/test_upload_tickets.py`.

**See.** `docs/MCP_TOOLS.md` section 5.2; `docs/AGENT_ONBOARDING.md` section 7.

### DD-17: One function resolves an agent label

An agent label is resolved in one function, `actor.resolve_agent_label_id`, in this order: the
per-call value, then the request header, then the label stored on the token. A label is honoured
on every bearer-authenticated request and never on a browser session. A label is attribution and
grants nothing.

**Why.** One precedence rule, not one per surface.

**Held by.** `tests/test_one_agent_label_resolver.py`, `tests/test_mcp_agent_label.py`,
`tests/test_rest_agent_label.py`.

**See.** `docs/AGENT_ONBOARDING.md` section 2; `PRD.md` FR-I6.

## Inputs and errors

### DD-18: Every input has a bound

Every input has a bound, and each bound is a setting or a named constant enforced in the service,
so both surfaces inherit it and neither declares it again. Every bound an agent can hit is
published in the capabilities document. Every list read is bounded by a page size or a cap. An
upload spools to disk before any in-app cap applies, so a reverse proxy's body limit is the
control in front of it.

**Why.** Both surfaces refuse the same input the same way, and no read grows without limit.

**Held by.** `tests/test_input_bounds.py`, `tests/test_proposal_target_projection.py`.

**See.** `docs/MCP_TOOLS.md` section 6; `docs/DEPLOYMENT.md` section 4.

### DD-19: A client-correctable input is a 4xx that says what to fix

Input a client can correct is a 4xx naming what to fix, and a 422 never echoes the request body.
A correct request against a deployment in the wrong state is a 409: `feature_disabled`,
`workspace_read_only` and the other codes in that family name the state and what to do instead. Any
failure nobody classified is `internal_error`, carrying only a request id, with the detail in the
server log.

**Why.** The caller gets a remedy it can act on, and never an internal detail.

**Held by.** `tests/test_input_bounds.py`, `tests/test_mcp_transport.py`,
`tests/test_search_surfaces.py`.

**See.** `docs/MCP_TOOLS.md` section 6.

### DD-20: User content cannot act as anything else

The system pseudo-field names are reserved and refused as field keys. A CSV cell a spreadsheet
would run as a formula is escaped on export and restored on import, by a run-length escape that
round-trips exactly. Collisions that already exist are reported at startup, never rewritten.

**Why.** A field could otherwise shadow a system column, and a cell could execute on someone
else's machine.

**Held by.** `tests/test_csv_import_export.py`, `tests/test_schema_engine.py`.

**See.** `docs/DATA_MODEL.md` section 4, "CSV cells".

## Records and documents

### DD-21: Revert is an ordinary update with a note, and audit browsing is a filtered read

Reverting a field change or a whole record to an earlier version is an ordinary update that
carries a note, through the same write path, validation and version check as any edit. Browsing
audit history is a filtered, keyset-paginated read. Both are REST and UI only.

**Why.** One write path keeps one set of validation and version checks.

**Held by.** `tests/test_audit_browse_and_revert.py`, `tests/test_api_audit.py`.

**See.** `PRD.md` FR-D6 and FR-U8; `docs/MCP_TOOLS.md` section 8.

### DD-22: A CSV import validates every row, predicts exactly, and commits in one transaction

A CSV import validates every row, against the data and against earlier rows in the same file. A
dry run reports exactly what the commit would do, and the commit is one transaction. A multi-row
write goes through the batch methods of the record service rather than nesting writes.

**Why.** An import that half lands, or that predicts wrongly, is worse than a refusal.

**Held by.** `tests/test_csv_import_export.py`.

**See.** `PRD.md` FR-U7; `docs/DATA_MODEL.md` section 4.

### DD-23: A record's label is the field its object type chooses

`object_types.display_field_key` names the field whose value labels a record. When it names a
live field that can be displayed, it is used; otherwise the label falls back to the first
non-relation field by position. Relation, attachment and user_ref fields cannot be chosen. One
function in the service layer holds the rule, and both answers are sent to the client.

**Why.** A record must always be labelable, by a value a person can read.

**Held by.** `services/base.py` (`display_field`), `tests/test_display_field.py`,
`web/src/api/oneDisplayFieldRule.test.ts`.

**See.** `docs/DATA_MODEL.md` section 3, "object_types".

### DD-24: A person is named the way people name them, and resolved by one function

A user_ref value accepts `@me`, a principal id, an email or a display name, resolved by one
function in that order. An ambiguous display name is refused with every candidate named, never
picked. A filter may name an inactive principal; a new write may not.

**Why.** People are named the way people name them, and a name is never guessed.

**Held by.** `services/principals.py` (`resolve_principal_ref`),
`tests/test_one_principal_resolver.py`, `tests/test_principal_resolution.py`.

**See.** `docs/DATA_MODEL.md` section 4, "`user_ref` references".

### DD-25: Every document names what it refers to

A document that carries a foreign id also carries its label. The label is joined in the
repository query that reads the row, or supplied by one batched sidecar per document (the
`principals`, `agent_labels` and grant sidecars, and a proposal's target). A sidecar has a fixed
projection that withholds privileged keys.

**Why.** The client resolves nothing itself, and the cost is one query per document rather than
one per row.

**Held by.** `tests/test_envelope_labels.py`, `tests/test_principal_sidecar.py`,
`tests/test_proposal_target_projection.py`.

**See.** `docs/MCP_TOOLS.md` section 5.1, "The `principals` and `agent_labels` sidecars".

### DD-26: A row stores who last changed it, and one component renders every person and agent

A record row stores the principal and agent label of its last change. The web app renders every
person and every agent through one attribution component.

**Why.** Tables lead with who changed a row, and a second rendering of a person or an agent is
where attribution goes wrong.

**Held by.** `web/src/ui/oneAttributionPrimitive.test.ts`, `tests/test_record_agent_label.py`.

**See.** `docs/DATA_MODEL.md` section 5, "The hand that last changed a row"; `docs/DESIGN.md`
section 6.

### DD-27: A client composes references from what is stored, never from what it could resolve

A client that edits a list of references composes the new list from the stored ids, never from
the rows it managed to resolve. An id it cannot resolve is shown as withheld, and the number of
rows shown always equals the number of ids stored.

**Why.** An id that names no row and an id the reader may not see look the same to the client, so
rebuilding from resolved rows would silently destroy references the person cannot see.

**Held by.** `tests/test_attachment_surface.py`, `web/src/inbox/proposalSentence.test.ts`.

**See.** `docs/DESIGN.md` section 8.3.

### DD-28: The workspace document is one bounded read

The workspace document carries the deployment's name, the count of its active people, the count
of its registered agent labels, and the MCP URL built from the configured base URL.

**Why.** The browser orients itself in one call, and a count is not a list.

**Held by.** `tests/test_api_workspace.py`.

**See.** `docs/DESIGN.md` section 8.1.

## Agents and files

### DD-29: Files travel as handles in tool calls, and bytes travel over HTTP

A tool call's arguments and its result carry file handles, never file bytes. Bytes travel over
HTTP or through `resources/read`. Text an agent wrote itself is the one payload allowed inside a
tool call.

**Why.** Tool arguments and results are model tokens. Bytes encoded there cost roughly a token
per byte and are easily corrupted.

**Held by.** `tests/test_attachment_surface.py`, `tests/test_upload_tickets.py`.

**See.** `docs/AGENT_ONBOARDING.md` section 7; `docs/MCP_TOOLS.md` section 5.

### DD-30: The endpoint teaches itself

The MCP `instructions` are a short index under a tested character budget. The manual is the
`describe_capabilities` tool, readable at `read` scope, which returns the same bytes to every
caller.

**Why.** Hosts truncate instructions, and an agent connected to a deployment has no checkout of
this repository.

**Held by.** `tests/test_endpoint_teaches_itself.py`.

**See.** `docs/AGENT_ONBOARDING.md`; `docs/MCP_TOOLS.md` section 3.

## Search

### DD-31: Vectors and full text live in the same database file as the records

Vectors live in a `sqlite-vec` table and full text in an FTS5 table, both in the same database
file as the records they index.

**Why.** One file and one container, with content and its index kept consistent in one
transaction, and a backup that copies both without a separate mechanism.

**Held by.** `tests/test_search_storage_and_status.py`, `tests/test_search_guards.py`.

**See.** `docs/DATA_MODEL.md` section 10.

### DD-32: The embedding model is bundled into the image, pinned, and never fetched at runtime

The embedding model is a small English model run through ONNX Runtime. It is added to the image
at build time, pinned by revision and by digest, and loaded from a configured directory; nothing
fetches it at runtime. The tokenizer's hub client is installed as a dependency but guarded so it
cannot reach the network. A test that needs the real model fails, rather than skips, when the
model is absent.

**Why.** A deployment may have no network egress, and the bytes that run must be the bytes that
were recorded.

**Held by.** `tests/test_search_guards.py`, `tests/test_fetch_model_script.py`, `Dockerfile`.

**See.** `docs/DATA_MODEL.md` section 10; `docs/DEPLOYMENT.md` section 1.

### DD-33: Retrieval quality is gated by a committed golden set

Retrieval quality is measured against a committed golden set, and its floors cannot be lowered to
make a run pass. Chunking, the fusion constants and the query prefix are fixed together with it,
and change only with a recorded golden run. The cases the ranking does not reach are strict
expected failures, so an accidental improvement fails the suite rather than passing silently.

**Why.** Search can be wrong in ways every functional test passes.

**Held by.** `tests/test_search_golden.py`, `tests/test_search_golden_corpus.py`.

**See.** `docs/DATA_MODEL.md` section 10, "Chunking policy" and "Hybrid ranking";
`docs/PERFORMANCE.md`.

### DD-34: Index maintenance follows the data

When a schema change makes a field searchable, its index fan-out runs after the change commits,
in batches with a pause between them. De-indexing a field that became ineligible, and a field
deletion's rewrite of the data, stay inside the schema change's own transaction. With embedding
turned off there is no worker, semantic search is refused with `feature_disabled`, and hybrid
search falls back to keyword.

**Why.** One transaction over a large object type would hold the single writer lock for seconds
and fail other writes. A field that is de-indexed must stop matching the moment the change
commits.

**Held by.** `tests/test_search_index_maintenance.py`, `tests/test_embedding_worker.py`,
`tests/test_search_surfaces.py`.

**See.** `docs/DATA_MODEL.md` sections 10 and 13; `docs/PERFORMANCE.md`, "Schema-change fan-out".

### DD-35: A graceful stop releases the embedding worker's batch without charging an attempt

On a graceful stop the embedding worker finishes the source it holds and returns the rest of its
batch to the queue without charging an attempt. On start it reclaims every job left running.

**Why.** A workspace that stops often must not spend its retry budget on stops.

**Held by.** `tests/test_embedding_worker.py`, `container_tests/test_clean_shutdown.py`.

**See.** `docs/DEPLOYMENT.md` section 2a.

## Operations

### DD-36: A backup is one ordered artifact, and restore is proven by equivalence

A backup is one artifact: the database snapshot first, then the attachment tree. Restore is an
operator procedure, not an endpoint, and it is proven by a restored deployment answering the same
as the original, including a write. The full export shares the backup's response envelope and its
scope, not its format.

**Why.** Taking the snapshot first guarantees every attachment the snapshot references is in the
copy.

**Held by.** `tests/test_backup.py`, `tests/test_export.py`.

**See.** `docs/DEPLOYMENT.md` section 6; `PRD.md` FR-P8 and FR-E5.

### DD-37: A fresh deployment can hand back its first administrator's token over HTTP, once

A deployment configured with a bootstrap secret hands back its first administrator's token over
HTTP exactly once, and the endpoint closes as soon as any user exists.

**Why.** A program that provisions a container cannot always run a command inside it.

**Held by.** `tests/test_bootstrap_handoff.py`, `container_tests/test_bootstrap_handoff.py`.

**See.** `docs/DEPLOYMENT.md` section 3, "Bootstrapping over HTTP"; `PRD.md` FR-I1.

### DD-38: A workspace can be frozen read-only by configuration

With read-only mode on, every write on both surfaces is refused with 409, naming a subscribe URL
when one is configured. One predicate decides it. Reads, export and six named security and
recovery writes stay open.

**Why.** The same image serves a workspace its operator has paused and a self-hosted deployment
in a migration window.

**Held by.** `tests/test_one_read_only_predicate.py`, `tests/test_read_only_mode.py`,
`tests/test_mcp_read_only.py`.

**See.** `docs/DEPLOYMENT.md` section 6a; `PRD.md` FR-P9.

### DD-39: An operator reads usage counts with a credential the workspace cannot mint

Aggregate usage counts are read with a configured operator credential that no principal in the
workspace can mint. Anyone else gets one identical refusal, and no string the workspace chose
comes back in the response.

**Why.** Usage reporting must not become a way to read or probe a workspace.

**Held by.** `tests/test_operator_usage.py`, `tests/test_one_usage_counter.py`.

**See.** `docs/DATA_MODEL.md` section 2, "usage_counters and usage_meta"; `PRD.md` FR-P10.

### DD-40: What the product does not do today

The product does not serve under a sub-path, purge audit history, keep private saved views,
paginate search results, use an embedding model of other than 384 dimensions, search non-English
text semantically, extract text from attachments, or renew a sign-in silently. Each is current
behaviour with a stated cost, not a commitment either way.

**Why.** Each is written down so that none is rediscovered as a bug.

**Held by.** `tests/test_search_chunking_and_provider.py` (the dimension check at startup).

**See.** `docs/DEPLOYMENT.md` sections 4 and 8; `docs/MCP_TOOLS.md` section 5.1.

## Interface

### DD-41: The UI design language is specified in one document

The design language is `docs/DESIGN.md`: people are blue and agents amber, comfortable density is
the default, there are light and dark themes, fonts ship in the bundle, dialogs are the native
`<dialog>` element, nothing reachable on a wide screen is unreachable on a narrow one, and tests
address the DOM by role, label and text rather than by class.

**Why.** Attribution is visible in every list, and nothing loads from the network at runtime.

**Held by.** `web/src/ui/noOrphanedTokens.test.ts`, `web/src/ui/density.test.ts`,
`web/e2e/theme.spec.ts`.

**See.** `docs/DESIGN.md`.

### DD-42: A control the caller's level does not reach is hidden, and each screen says why once

A screen about an object type renders only the controls the caller's level reaches, and carries
exactly one banner naming the level held and who can raise it. The server remains the boundary.

**Why.** One explanation per screen, instead of screens that fail silently or refuse noisily.

**Held by.** `web/src/access/hidingIsNeverTheOnlySignal.test.ts`,
`web/e2e/access-levels.spec.ts`.

**See.** `docs/DESIGN.md` section 7.5.

### DD-43: The filter chip row is a flat AND, and the full grammar sits behind Advanced

The chip row expresses a flat AND of conditions. A filter the row cannot express opens as one
Advanced filter. An incomplete condition never reaches the network.

**Why.** Everyday filters read as sentences, without losing the full grammar.

**Held by.** `web/src/table-view/FilterChipRow.test.tsx`,
`web/src/table-view/TableView.incompleteFilter.test.tsx`.

**See.** `docs/DESIGN.md` section 7.4; `docs/MCP_TOOLS.md` section 4.

### DD-44: The new-record form offers what the create path accepts

The new-record form offers exactly the fields the create path accepts, sends only the fields the
person changed, and opens from the title line.

**Why.** A client mirror of a server rule must never refuse what the server accepts, or overwrite
a default the server would have applied.

**Held by.** `web/src/table-view/NewRecordDialog.test.tsx`, `web/e2e/table-create.spec.ts`.

**See.** `docs/DESIGN.md` section 8.2; `PRD.md` FR-U1.

### DD-45: A hosted workspace signs people in by emailed code, through the relay only

With `GW_RELAY_URL` and `GW_RELAY_TOKEN` set, people sign in with a six-digit emailed code and
password sign-in is off. The workspace asks the hosting control plane's relay to send each message,
naming one of two templates with typed fields; there is no other sender. The request answers the
same for every address, a row is written for every address, and the per-address limits are counted
in the database so a restart keeps them. No request cancels a live code.

**Why.** A hosted person holds no password, and a workspace that could send free text would be a
spam relay. What remains is accepted: anyone who knows an address can spend its allowance and keep
that person out for up to a day, recovered by an operator's `clear-sign-in-codes`, and the guessing
odds are about 1 in 10,000 per targeted address per day.

**Held by.** `tests/test_sign_in_codes.py`, `tests/test_invites.py`, `tests/test_relay_driver.py`,
`tests/test_relay_definition.py`, `tests/test_email_code_off.py`, `web/e2e/email-code.spec.ts`.

**See.** `docs/DEPLOYMENT.md` section 5a; `PRD.md` FR-I18, FR-I19.

