# Glosswork: MCP Tool Catalog and Query Grammar

Normative companion to [PRD.md](../PRD.md). This document specifies the interface that directly
addresses the failure that motivated the project: **an agent must be able to filter and search by
user-defined fields, and must be able to work out what those fields mean without a human writing
integration code.**

The same operations exist in REST at `/api/v1/*` over the same service layer (DD-3, FR-A1).
The tool names below map one-to-one onto REST endpoints.

---

## 1. Connection and authentication

- **Transport:** streamable HTTP, served from the same process and port as the API, at `/mcp`.
- **Auth:** `Authorization: Bearer gw_pat_...` (DD-8)
- **Agent label:** `X-Agent-Label: <string>` sets a default label for the whole connection. Any
  tool call may override it with an `agent` parameter, and the token itself carries the label it
  was minted for. Per-call value first, then the header, then the token's label (FR-M5).

Example Claude Code configuration, one entry per agent so each writes under its own label:

```json
{
  "mcpServers": {
    "tracker-planner": {
      "type": "http",
      "url": "https://tracker.internal/mcp",
      "headers": {
        "Authorization": "Bearer gw_pat_xxxxxxxx",
        "X-Agent-Label": "claude-code-planner"
      }
    }
  }
}
```

**A harness that cannot send that header is still attributed (DD-17).** Claude's
connector dialog accepts only header names Anthropic has approved, and `X-Agent-Label` is not one
of them, so a connector added there sends no label at all; some harnesses cannot send an extra
header under any name. Mint that tool its own token with an agent label and every call it makes is
attributed to that label with no header anywhere. One token per tool is the low-friction path, and
it is what makes revocation granular: the token is minted for one named place, so revoking it
stops that one tool.

**The agent label is descriptive metadata, not a security boundary.** Authorization derives
entirely from the token's principal and scope. Anyone holding the token can send any label. This
is stated in the tool descriptions so an agent does not treat it as an access control mechanism.

**Personal access tokens (DD-8, FR-I4).** The bearer value is a personal
access token: `gw_pat_` followed by 32 random characters, minted by an administrator over
`POST /api/v1/access-tokens` or out of band with `python -m glosswork.admin mint-token`. It is
shown exactly once, at mint time, and only its sha256 is stored, so a lost token is replaced
rather than recovered. Each token carries one scope (`read`, `write`, or `admin`) and optionally
an expiry and an agent label (FR-I4). A label that is blank or longer than
`agent_label_max_length`, which `describe_capabilities` publishes, is refused at mint.

The token is resolved on every call: unknown, revoked, expired, or belonging to a deactivated
principal is refused before dispatch with a JSON-RPC `INVALID_REQUEST` error saying which. **A
request carrying no credential is refused on every request method that returns data — the
`initialize` handshake included, not merely every call after it.**

The rule is stated as the server rather than as a list (DD-15), because a list of gated methods is
how an open one gets missed: **every request method this server registers demands a resolvable
credential.** The method that matters most is `server/discover`, which would otherwise answer a
request carrying no `Authorization` header at all with the capability set and the complete
onboarding instructions -- the same disclosure `initialize` is gated against, reached by a
different method. So a session is refused **at discovery**, before the handshake, rather than at
the handshake. A client holding a valid token is unaffected: it connects, negotiates the protocol
version and lists its tools.

**The refusal's shape.** The JSON-RPC error code is `-32600` on both wires. The HTTP status is not
the same on both: a legacy JSON-RPC request is refused with **HTTP 200** carrying that error, and a
request using the modern per-request `_meta` envelope is refused with **HTTP 400** carrying it. The
split is a property of the transport layer, not of the gate. `server/discover` exists only on the
modern wire, so a refused discovery is always the 400 form.

Notifications are not gated and must not be: they carry no request id and have no response, so a
compliant client that sends one before it sends a token would see a protocol error it could not
act on.

**`GET /mcp` does not exist on this deployment.** That verb is the SSE stream, and this deployment
is stateless with `json_response=True` and never sends a server-initiated message, so the handler
served nothing while holding a connection open indefinitely for any anonymous caller. The router
refuses it with 405 (`Allow: POST, DELETE`), which is the spec-permitted answer. The SDK client
never issues it, because it short-circuits while there is no session id and stateless mode issues
none. A hand-rolled client that opens the stream unconditionally will see the 405.

## 2. Scopes and tool visibility

| Scope | Sees |
| --- | --- |
| `read` | Discovery and read tools only |
| `write` | Read tools plus record, comment, and link mutations |
| `admin` | Everything, including schema mutation |

Tools outside the token's scope are **omitted from `tools/list` entirely**, not merely refused
(FR-M4). A write-scoped task agent cannot discover that `delete_field` exists.

**`describe_capabilities` is `read` scope (DD-30).** It mutates nothing, reads no principal,
token, grant or record, and returns the same static platform description to every caller. That
matters because it is the endpoint's *manual*, and the agent that most needs it -- the one
uploading a file -- holds `write`. The server `instructions` are an index that names it rather than a place detail is carried, because a tool
result is never truncated and `instructions` demonstrably is.

**Scope is a ceiling, not the whole answer (DD-11).** A principal also holds a per-object-type
*level*, and what a call may actually do is
`min(credential scope, granted level)`. The credential can only ever narrow: a `read` token held
by the administrator of a type still only reads it.

**The catalog is deliberately not filtered by grants (DD-8).** `tools/list` stays a
pure function of the token's scope (`ToolCatalog.is_visible`), exactly as DD-8 specifies, so a
`write`-scoped agent still sees `create_record` even when every type is read-only to it. Filtering
`tools/list` per caller would turn a per-process registration filtered by one integer comparison
into a grant query issued on every listing, to answer a question `list_object_types` already
answers: **call it and read `your_access`** (section 3), or make the call and read the `forbidden`
error, which names the type, the level you hold, the level needed, and who can grant it.

**Two refusals, two different meanings.** `insufficient_scope` is the credential: your token does
not reach this tool anywhere. `forbidden` is the grant: the token reaches the tool and your level
on *this type* does not. The distinction matters most on the three grant tools (section 5.3),
where an `admin`-scoped token that holds `admin` on one type administers that type and is
`forbidden` on every other.

## 3. The orientation pattern

Agents are expected to follow this sequence rather than being pre-programmed with schema
knowledge:

1. `list_object_types()` to see what exists and what each type is for, **and what you may do to
   each** -- every entry carries `your_access` (`none` | `read` | `write` | `admin`). Types you
   cannot read are omitted rather than refused, so this is also how you learn what you have.
2. `describe_object_type("initiative")` to get every field, its type, its description, its enum
   options with their descriptions, its valid filter operators, and `your_access` again.
3. Construct a query using the returned field keys and operators.

`your_access` carries the **already composed** value, never the raw grant: an agent holding a
`read` token on a type it administers is told `read`, which is the truth about what it can do
right now. It is what makes the tool catalog honest (section 2) without a per-request grant query
on every listing.

`describe_object_type` is the load-bearing tool. Its response is the entire reason the generic
tool set works, and it is why tool-per-object-type generation was rejected (FR-M3).

## 4. Filter grammar

Used identically by `query_records`, `search`, `bulk_update_records`, and CSV export.

### Structure

```json
{
  "and": [
    {"field": "status", "op": "in", "value": ["active", "at_risk"]},
    {"field": "target_date", "op": "lte", "value": "@today+30d"},
    {"or": [
      {"field": "owner", "op": "eq", "value": "@me"},
      {"field": "sponsor", "op": "is_null"}
    ]},
    {"not": {"field": "tags", "op": "has_any", "value": ["deprioritized"]}}
  ]
}
```

`and`, `or`, and `not` nest **up to 32 filter nodes deep** (DD-18; read the exact
number from `describe_capabilities` under `limits.filter_max_depth` rather than from here). Past
that is `validation_failed` naming the cap. Flatten instead of nesting: a single `and` or `or`
takes a list of any length, so real filters rarely reach three levels. A bare condition object is
a valid filter. An empty or omitted filter matches all live records.

### Operators by field type

| Field type | Operators |
| --- | --- |
| all types | `is_null`, `is_not_null` |
| `short_text`, `long_text`, `url` | `eq`, `neq`, `contains`, `not_contains`, `starts_with`, `ends_with`, `in` |
| `integer`, `decimal` | `eq`, `neq`, `gt`, `gte`, `lt`, `lte`, `between`, `in` |
| `boolean` | `eq` |
| `date`, `datetime` | `eq`, `neq`, `gt`, `gte`, `lt`, `lte`, `between` |
| `single_select` | `eq`, `neq`, `in`, `not_in` |
| `multi_select` | `has_any`, `has_all`, `has_none`, `is_empty`, `is_not_empty` |
| `user_ref` | `eq`, `neq`, `in` |
| `relation` | `linked_to`, `linked_to_any`, `has_links`, `has_no_links` |
| `attachment` | `is_empty`, `is_not_empty` |

`between` takes a two-element array, inclusive on both ends.

Every `user_ref` comparison value -- for `eq`, for `neq`, and for each member of an `in` list --
accepts a principal id, an email address, an exact display name, or `@me`, and is resolved
server-side (DD-24). A display name matching more than one principal is `validation_failed`
naming the candidates, never a guess, and a value that names nobody is `validation_failed` rather
than an empty result set. Unlike a write, a filter **does** resolve a deactivated principal: finding
a departed colleague's still-open work is the handover query. Use `find_principals` to look an id
up when a name is ambiguous.

### System pseudo-fields

Queryable on every object type alongside user-defined fields (FR-R7):

| Pseudo-field | Type | Notes |
| --- | --- | --- |
| `key` | text | `INIT-014` |
| `created_at`, `updated_at` | datetime | |
| `created_by`, `updated_by` | user_ref | Accepts `@me`, an email address, and an exact display name, exactly as a `user_ref` field does. |
| `comment_count` | integer | Enables "records with no discussion" |
| `last_comment_at` | datetime | Enables "records with no recent discussion" |
| `deleted_at` | datetime | Only meaningful with `include_deleted: true` |

### Tokens

Resolved server-side so agents never compute dates or look up their own identity (FR-R8, FR-R9):

- `@me` in any `user_ref` comparison resolves to the calling principal -- and in a
  `user_ref` **value** on `create_record`, `update_record` and `bulk_update_records` too.
- Date tokens: `@today`, `@now`, `@start_of_week`, `@start_of_month`, `@start_of_quarter`,
  `@start_of_year`, and offsets such as `@today-7d`, `@today+30d`, `@now-12h`, `@start_of_month-1M`.
  Units: `d`, `w`, `M`, `y`, `h`, `m`.

### Sorting and pagination

```json
{
  "sort": [{"field": "status", "dir": "asc"}, {"field": "target_date", "dir": "desc"}],
  "limit": 50,
  "cursor": "eyJrIjoi...",
  "fields": ["key", "name", "status", "owner", "target_date"]
}
```

`fields` projects the response. Omitting it returns a compact default (key, the type's display
field, and all indexed fields) rather than every field, to protect the agent's context window
(FR-M8). Pass `"fields": "*"` for everything.

`limit` is capped per tool and each tool's parameter description carries its own number:
`query_records` at 1,000, `get_record_history` and `list_comments` at 500, `search` at 50,
`list_changes` at 1,000, `search_audit` at 500. Over the cap is `validation_failed` carrying
`details.max_limit`, identically on REST and on MCP, because the cap is enforced in the service
both surfaces call rather than declared as a schema bound on one of them (DD-18).
`describe_capabilities` publishes all of them under `limits`.

Cursor pagination is keyset-based over the sort keys plus `id`. Cursors are opaque: pass
`next_cursor` back unmodified. A cursor whose values have been altered is `validation_failed`,
not an error the deployment treats as its own fault — cursors are unsigned by decision, because
both decoders re-derive scope and filter from the request, so a cursor can only ever move the
boundary within what the caller may already read.

## 5. Tool catalog

### 5.1 Discovery and read (scope: `read`)

#### `list_object_types()`
Returns every object type with `key`, `name`, `description`, `key_prefix`, `record_count`,
`field_count`, and `your_access`. The starting point for any agent.

Only types you hold at least `read` on are returned (DD-11). A type you cannot read is
**omitted, not refused**, because an orientation call is how you find out what exists and
answering it with an error because one type is closed would make the surface unusable to a
narrowly granted agent.

#### `describe_object_type(object_type, include_samples=false)`
The orientation tool. Returns:

```json
{
  "key": "initiative",
  "name": "Initiative",
  "description": "A funded, sponsored workstream in the organisation's portfolio. Initiatives contain tasks and produce decisions.",
  "key_prefix": "INIT",
  "record_count": 47,
  "field_count": 2,
  "your_access": "write",
  "display_field_key": "status",
  "effective_display_field_key": "status",
  "fields": [
    {
      "key": "status",
      "name": "Status",
      "type": "single_select",
      "description": "Current delivery health. Set 'at_risk' when the target date is threatened but recoverable; use 'blocked' only when work has actually stopped.",
      "required": true,
      "unique": false,
      "indexed": true,
      "default": "proposed",
      "display_eligible": true,
      "operators": ["eq", "neq", "in", "not_in", "is_null", "is_not_null"],
      "options": [
        {"value": "proposed", "label": "Proposed", "description": "Not yet funded or sponsored."},
        {"value": "active", "label": "Active", "description": "Funded, sponsored, and being worked."},
        {"value": "at_risk", "label": "At Risk", "description": "Target date threatened but recoverable."},
        {"value": "blocked", "label": "Blocked", "description": "Work has stopped pending an external dependency."},
        {"value": "complete", "label": "Complete", "description": "Delivered and accepted by the sponsor."}
      ]
    },
    {
      "key": "parent_initiative",
      "name": "Parent Initiative",
      "type": "relation",
      "description": "The larger initiative this one rolls up into. Self-referential; use for portfolio hierarchy.",
      "target_type_key": "initiative",
      "cardinality": "one",
      "inverse_field_key": "child_initiatives",
      "display_eligible": false,
      "operators": ["linked_to", "linked_to_any", "has_links", "has_no_links"]
    }
  ],
  "system_fields": [ "... pseudo-fields with their operators ..." ]
}
```

Returning `operators` per field means the agent never has to infer which comparisons are legal.
`include_samples=true` adds up to three example values per field, drawn from live data, which
materially improves an agent's first-attempt query accuracy on free-text fields.

**Which field names a record (DD-23, FR-S11).** Two keys, because one value cannot serve both
readers:

- `display_field_key` is the **stored** column, verbatim. `null` means nobody has chosen. An
  editor showing the current setting needs to see that null so that saving an unrelated setting
  cannot silently convert an implicit null into a pin the user never made.
- `effective_display_field_key` is what the system **actually resolves to** -- the chosen key when
  it names a live, display-eligible field, and otherwise the position-derived fallback (the first
  non-relation field). `null` only when the type has no eligible field at all. **Read this one.**
  It is here precisely so you never have to re-derive the rule from `position`.

That is the field whose value appears as a search hit's `title`, as the record's entry in a
compact projection, and as `display` beside every link to it.

`display_eligible` on each field says whether it may be that field. It is false for `relation`,
`attachment` and `user_ref` and true for everything else, `date` and `integer` included. It is
computed from one predicate (`fieldtypes.py::is_display_eligible`) and put on the wire so no
client restates the list.

#### `query_records(object_type, filter?, sort?, limit?, cursor?, fields?, expand_relations?, include_deleted?)`
The core read tool. `expand_relations` accepts a list of relation field keys to resolve one level
deep, returning `{key, id, display}` for each linked record, where `display` is the value of the
**target type's** display field (DD-23). It is `null` when the target type has no eligible
field or that record's value is empty; fall back to the key.

Response includes `records`, `total_count`, `next_cursor`, and `truncated` with guidance when a
result set was clipped, plus `principals` -- see "The `principals` sidecar" below.

**Access:** needs `read` on `object_type`; refused with `forbidden` naming that type.

#### `get_record(record, include?)`
`record` accepts either the human key (`INIT-014`) or the UUID. `include` accepts any of
`comments`, `links`, `history`, `attachments`.

**Access:** needs `read` on the record's own type; refused with `forbidden` naming it.
`include=links` returns `{key, id, display}` per entry, the same shape and the same
target-type-resolved `display` as `expand_relations` (DD-23). It does not escalate the check:
an entry whose target type you cannot read comes back as exactly `{"redacted": true}` rather than
raising or being dropped (DD-11), and gains no `display` key at all -- `display` is a title. `include=attachments`
resolves each attachment id only if you hold `read` on the type of some record referencing it, or
you are its uploader; other ids are silently omitted rather than refused.

**The write side of the same rule (DD-12).** A record write naming an attachment you cannot
read is refused `forbidden`, naming the attachment rather than an object type — an attachment has
no object type of its own. So `create_record`, `update_record` and `bulk_update` can return
`forbidden` against an *attachment* as well as against the record's type. The two cases an agent
will actually meet: an id it did not upload and cannot reach through any type it can read, and an
attachment somebody else uploaded and has not yet referenced anywhere (the uploader has to make
the first reference). An id that resolves to no attachment row is unaffected — attachment field
values are opaque ids validated for shape, and such an id is stored, never joined, and authorizes
nothing.

#### `search(query, object_types?, filter?, mode?, limit?)`
Hybrid keyword plus semantic search (FR-Q1 through FR-Q5). `mode` is `hybrid` (default),
`semantic`, or `keyword`. `filter` applies structured constraints on top of relevance, and when
`object_types` names exactly one type the filter may reference that type's fields.

Each result identifies where it matched:

```json
{
  "results": [
    {
      "record_key": "DEC-031",
      "object_type": "decision",
      "title": "Consolidate regional pricing authority",
      "score": 0.83,
      "hit_source": {"type": "comment", "comment_id": "...", "author": "Dana Reyes", "created_at": "2026-03-03T14:22:00Z"},
      "snippet": "...pushed back on the <em>sales pricing</em> exception process because regional GMs...",
      "other_matches": 2
    },
    {
      "record_key": "INIT-014",
      "object_type": "initiative",
      "title": "Commercial operating model",
      "score": 0.71,
      "hit_source": {"type": "field", "field_key": "scope_summary"},
      "snippet": "...harmonize <em>sales pricing</em> governance across the three regions...",
      "other_matches": 0
    }
  ],
  "index_lag": {"pending_jobs": 0}
}
```

`index_lag` is how an agent knows whether very recent writes are searchable yet. The rules
(DD-33, DD-40; docs/DATA_MODEL.md section 10): `limit` defaults to 10 and is capped at
50, with no cursor, so an agent that needs more narrows with `object_types` or `filter`; `score`
is normalized so a record ranked first by both arms scores 1.0; `index_lag` also carries
`failed_jobs`; the response carries `mode_applied`, which differs from `mode` only when semantic
search is disabled on the deployment and `hybrid` degraded to keyword-only (`mode=semantic` is then
`feature_disabled`, HTTP 409, with `details.use_instead` naming `keyword`); with several or no
`object_types`, `filter` may reference only the system pseudo-fields, and a user-field reference is
`validation_failed` naming the one-type rule. Use `mode=keyword` for exact identifiers
(`PO-88213`); the semantic arm does not see them. Each whitespace-separated word of the query
becomes one phrase of its alphanumeric runs, so `PO-88213` matches only text carrying that
identifier and not every text that says "PO"; a fixed list of query-side stopwords is dropped;
whatever remains is data, never FTS5 syntax. Record keys such as `INIT-014` are not indexed text:
use `get_record`. `hit_source` is the record's best keyword-matched location when it has one, with
an FTS5 snippet, otherwise the passage nearest the query; `other_matches` counts the record's
additional keyword-matched locations (a field or a comment each count once) and is 0 for a purely
semantic hit. An empty query is `validation_failed`; a query made only of stopwords returns no
keyword results and, in `hybrid`, semantic results alone.

**Access (DD-11):** omitted `object_types` searches only types you hold `read` on, silently --
the same "omitted, not refused" rule `list_object_types` follows. Naming a type explicitly that you
cannot read is `forbidden`, because you named it. The restriction is applied before either arm
runs, not as a post-filter on results, so it does not starve the semantic arm's KNN.

#### `list_comments(record, limit?, cursor?)`
Chronological comments with author principal, agent label if any, timestamps, and edited flag.

**Access:** needs `read` on the record's type -- comments have no grants of their own.

#### `get_record_history(record, field_key?, limit?, cursor?)`
Field-level audit for one record: who, when, from what, to what, via which surface and agent
label. This is what lets an agent answer "who changed this status and why" without UI access.

**Access:** needs `read` on the record's type.

#### `find_principals(query?, type?, include_inactive?, limit?)`
The people directory (FR-I16, DD-24). `query` is a case-insensitive substring of a display
name **or** an email address; `type` is `user` or `service_account`; `include_inactive` defaults to
false; `limit` defaults to 50 and is capped at 200.

Each entry carries exactly five keys:

```json
{"id": "9f3c...a1", "display_name": "Sarah Okonjo", "email": "sarah@example.com", "type": "user", "is_active": true}
```

`role`, `auth_provider`, `external_id` and `description` are **not** part of it. That fixed, narrow
projection is what lets this tool be readable at `read` scope with no system role, unlike every
other principal route, which stays `admin`-gated.

You do **not** have to call it to write a `user_ref` field: an email address, an exact display
name, and `@me` all resolve on the way in. Call it when a display name is ambiguous (the write is
refused with the candidates named, and this is how you find the id that disambiguates), or to check
how a name is spelled before you use it.

**Access:** `read` scope. No object type is involved, so no grant is consulted.

#### `get_attachment(attachment_id)`
One attachment's metadata (DD-29): `id`, `filename`, `content_type`, `byte_size`, `sha256`,
`uploaded_at`, `uploaded_by`, and `download_url`. Exactly the document
`GET /api/v1/attachments/{attachment_id}` returns and exactly the document each entry of
`get_record include=attachments` carries, because all three call one serializer.

The result also carries one `resource_link` content block -- `uri: attachment://{id}`,
`name`, `mimeType`, `size` -- alongside the structured content. The structured content is
identical on both surfaces; the link is an MCP-only affordance, and `size` is published so your
host can weigh the read before making it.

**The bytes are not here.** Read them with `resources/read` on that link, which needs no
credential of your own; or, if you hold a personal access token outside this connection, fetch
`download_url` over HTTPS with it. Do not go looking for the token this connection authenticates
with -- it belongs to your MCP client and no tool will ever return it (DD-16).

**Access (DD-12):** the same read rule as every other attachment path -- you hold `read` on
the type of some record referencing it, or you are its uploader. Anything else is `forbidden`.

#### `resources/read` of `attachment://{attachment_id}`
Not a tool: a request method, listed by `resources/templates/list` as the template
`attachment://{attachment_id}` and gated on the same bearer token every tool call needs.

Returns the attachment's own content type. A `text` content for `text/*`, `application/json`,
`application/xml` and their `+json` and `+xml` suffixed forms, decoded as UTF-8; a `blob` content
for everything else, which your host may hand to the model as a document block (a PDF costs
thousands of tokens per page, against roughly one token per byte for base64 as text) or may
ignore. What the host does with a blob is the host's decision, which is the point of reading bytes
here rather than in a tool result.

Errors have one shape here, the JSON-RPC error, because `resources/read` has no `is_error` result:
the message is the service's own and the `{code, message, details}` envelope rides in `data`.
`not_found` comes back at `-32602`, `forbidden` at `-32600`.

There is **no cap** on this read. No stored attachment exceeds `GW_MAX_ATTACHMENT_BYTES`
(published as `limits.attachment_max_bytes`), the link publishes `size`, and the read is your
informed choice.

#### `list_changes_since(cursor, object_types?, limit?)`
Pull-based change feed (FR-M7). Returns changes ordered by the audit event id, with a
`next_cursor`. Passing no cursor returns the current cursor without data, so an agent can start
watching from now. This is the intended alternative to polling every record.

**Access (DD-11):** every returned event's type is one you hold `read` on, filtered in SQL so
pagination stays correct across a boundary where rows are dropped. Naming an unreadable type in
`object_types` is `forbidden`; omitting it returns only readable types. Events with no object type
(principal, token, and session changes) are visible only to a `role == 'admin'` principal -- a
fail-closed heuristic, since that column carries no foreign key and is populated only when the
writing call site remembers to.

### The `principals` and `agent_labels` sidecars

Every response document that carries a record -- `query_records`, `get_record`, `create_record`,
`update_record`, `delete_record`, `restore_record`, on MCP and REST alike -- also carries a
`principals` map (DD-25):

```json
{
  "records": [ ... ],
  "principals": {
    "9f3c...a1": {
      "display_name": "Sarah Okonjo",
      "email": "sarah@example.com",
      "is_active": true,
      "type": "user"
    }
  },
  "agent_labels": {
    "7c9b...75e": {"label": "sales-agent", "display_name": null}
  }
}
```

It covers every `user_ref` field value in the document plus every `created_by` and `updated_by`, so
you never have to look an id up to render or reason about who someone is. `role` is not in it, for
the same reason it is not in `find_principals`. `type` **is**: a service account and a
person are different kinds of counterpart, and telling them apart should not need a second call.

**`agent_labels` rides alongside it**, resolving each record's
`updated_by_agent_label_id` -- the agent label of the write that last changed one of its *values*
-- to a name a person can read. Two keys and no more: it deliberately does not say whose label it
is, how often it has been used, or when it was last seen (DD-25). A record last changed by a
person has `updated_by_agent_label_id: null` and contributes nothing to the map.

The same resolution reaches the audit and comment envelopes as `agent_label`, beside
`principal_display_name` (DD-25). Without it an agent label would leave the deployment only as
a UUID.

The stored value is still a bare principal id inside `data`. A client that ignores this map loses
only the names.

`bulk_update_records` and `search` do **not** carry it, because neither response contains a record,
a field value, or a principal id.

One document that carries no record carries the map anyway: `list_object_type_grants`
(DD-25). Its rows are *about* principals, so the same map rides along, covering every
`principal_id`, `created_by` and `updated_by` the rows mention. It is built from the rows' own
ids, so unlike `find_principals` it has no cap and no active-only filter: a grant naming someone
who has since left is still named.

#### `describe_capabilities()`
**This endpoint's manual. Call it first, before anything else.** Returns the deployment's field
type catalog with the operators legal on each, the system pseudo-fields, the filter grammar, the
date and identity tokens, key naming rules, every limit you can hit, and the whole file recipe.
Takes no arguments. `read` scope (DD-30), so every credential that can call anything can
call this.

`key_rules` carries `object_type_and_field_keys` (the key pattern), `key_prefix`, and
`reserved_field_keys` -- the eight names a field key may not take, read from the same dict the
refusal reads, so a future pseudo-field is published here by being reserved. An agent that reads
the pattern and nothing else would name a field `created_by` and take a refusal it could have
avoided.

The `attachments` block carries the file recipe: `upload_route`,
`upload_url` (absolute when `GW_BASE_URL` is set), `upload_field`, `ticket_tool` naming
`create_attachment_upload`, `download_url_pattern`, `resource_uri_template`, `text_content_types`,
a `credential` sentence, and a `note`. The `credential` sentence exists because a plain upload
recipe names something no agent can obtain: the bearer this connection authenticates with belongs to your **MCP
client**, not to this server, so no tool will ever return it and you cannot upload with it. Mint one
with `ticket_tool` instead.

`limits` publishes `attachment_max_bytes` and `upload_ticket_ttl_seconds`. These are the only two
**settings** in a block that is otherwise named constants (DD-18), because an agent about to upload
has to know both the ceiling and how long its credential lives before it reads a file.

This tool is **MCP-only** and has no REST route, which is a declared exception in section 8 rather
than a parity gap: a REST caller reads `/openapi.json`.

### 5.2 Write (scope: `write`)

Every tool in this section accepts an optional `agent` string overriding the connection label.

#### `create_record(object_type, values, agent?)`
Returns the created record including its assigned `key` and `version`, and the `principals`
sidecar, so you can confirm who you assigned in the same turn. Validation errors name the
offending field, state the rule, and list valid options for select fields.

A `user_ref` value may be a principal id, an email address, an exact display name, or `@me`
(DD-24); what is stored is always the resolved principal id. An ambiguous display name is
`validation_failed` naming every candidate with its email address -- call `find_principals` and
pass the id. A deactivated principal cannot be assigned.

**Access:** needs `write` on `object_type`; refused with `forbidden` naming it.

#### `update_record(record, values, expected_version?, force?, agent?)`
Partial update: only supplied keys change. Omitting `expected_version` when the record has changed
since the agent last read it is the common failure; the error explains how to resolve it.

**Access:** needs `write` on the record's type; refused with `forbidden` naming it.

On version conflict, returns a structured `409`:

```json
{
  "error": {
    "code": "version_conflict",
    "message": "Record INIT-014 is at version 9; you supplied 7. Re-read the record and retry, or pass force=true to overwrite.",
    "current_version": 9,
    "conflicting_fields": {
      "status": {"your_value": "active", "current_value": "at_risk"}
    },
    "changed_since_your_version": ["status", "target_date"]
  }
}
```

#### `delete_record(record, force?, agent?)`
Soft delete. Blocked by inbound relations unless `force: true`, in which case the blocking record
keys are returned in the response for transparency.

**Access:** needs `write` on the record's type.

#### `restore_record(record, agent?)`

**Access:** needs `write` on the record's type.

#### `bulk_update_records(object_type, filter, values, dry_run?, agent?)`
Applies one value patch to every match. `dry_run: true` returns the affected count and up to
twenty sample keys without writing. **Agents should dry-run first**, and the tool description says
so.

**Access:** needs `write` on `object_type`, including for `dry_run` calls.

#### `link_records(from_record, field_key, to_records, agent?)`
#### `unlink_records(from_record, field_key, to_records, agent?)`
`to_records` accepts keys or UUIDs. Inverse links are maintained automatically.

**Access:** `link_records` needs `write` on `from_record`'s type and `read` on each target's
type -- you may not link a record into a type you cannot see, and the `forbidden` names whichever
type fell short. `unlink_records` needs only `write` on `from_record`'s type.

#### `add_comment(record, body, agent?)`
Markdown body. The comment is attributed to the token's principal and the supplied agent label.

**Access:** needs `write` on the record's type -- comments have no grants of their own.

#### `update_comment(comment_id, body, agent?)`
#### `delete_comment(comment_id, agent?)`
Authors may edit and delete their own comments; a type's administrators may delete any (FR-C5).
Both are audited with the full prior body retained.

**Access (DD-11):** both need `write` on the parent record's type, checked
*before* the FR-C5 authorship rule, so a caller without `write` on the type gets `forbidden` even
for their own comment. "Administrators" in FR-C5's rule is the **`admin` level on that object
type**, asked of the access service like every other authority question — not the scope the
credential happens to carry, which DD-11 makes a ceiling and never a grant. So a non-author's
refused `delete_comment` comes back `forbidden` when the grant fell short and `insufficient_scope`
when the credential did, and a system `admin` may moderate any type because its grant is
unrestricted. `update_comment` has
no administrator path at all and still refuses a non-author with `validation_failed`.

#### `create_text_attachment(filename, text, content_type?, agent?)`
Stores a document **you wrote** and returns the same attachment document `get_attachment` returns,
resource link included (DD-29). `content_type` defaults to `text/markdown` and must be one of
`application/json`, `application/xml`, `text/csv`, `text/html`, `text/markdown`, `text/plain`;
anything else is `validation_failed` naming the parameter, listing the set, and naming the
multipart route. The set is published as `attachments.text_content_types`.

This is the **only** payload allowed to travel inside a tool call, and the reason is that you were
emitting those characters anyway. Never base64-encode a file into a tool argument: it costs
roughly one output token per raw byte, no model can emit more than about 130 KB that way, and a
long base64 run drifts, so the server would hash whatever arrived and store a corrupt file under a
valid sha256. For any other file, including binary:

```bash
curl -H "Authorization: Bearer $GW_TOKEN" -F "file=@report.pdf" \
  https://glosswork.example.com/api/v1/attachments
```

The response carries the `id`. Either way, the id is attached to nothing until you write it onto a
record's attachment field with `update_record` -- and **build that array from the ids already
stored on the field**, not from the attachments you can see resolved (DD-27). An id withheld by
the read rule is omitted from what you see and would be destroyed by a rebuild from what rendered.

**Access:** `write` scope, and no object-type level, matching `POST /api/v1/attachments` exactly:
an upload names no record, so there is no type to check. The gate is at reference time, when the
id is written onto a record, and that is a real check.

**Cap:** `GW_MAX_ATTACHMENT_BYTES`, published as `limits.attachment_max_bytes`. Over it is
`validation_failed` naming the setting. No new cap was added for this tool: the 4 MiB request-body
cap already bounds the call at the edge, and no model emits 4 MiB in one call.

#### `create_attachment_upload(filename, content_type?)`
Get a ready-to-use URL and a single-use credential for uploading **one** file over HTTP
(DD-16). Use it for anything you did not write as text yourself: a PDF, an image, a spreadsheet, an
archive.

This exists because a plain upload recipe needs two things you cannot obtain. The bearer token
this connection authenticates with belongs to your **MCP client**, not to this server, and no part
of the protocol hands a model its own connection credential; the endpoint's origin is not visible
to you either. So the endpoint supplies both:

```json
{"name": "create_attachment_upload",
 "arguments": {"filename": "quarterly.pdf", "content_type": "application/pdf"}}
```

returns `upload_url` (absolute), `method`, `field`, `authorization` (a ready
`Bearer gw_upl_...` string), `expires_at`, `max_bytes`, `filename`, `content_type`, `single_use`,
and a `curl` line you complete with your local path. Every interpolated value in that line is
shell-quoted.

The credential is narrowed on every axis: `write` scope and never `admin`, one capability, one
route, one filename, one use, and `limits.upload_ticket_ttl_seconds` of life. It is refused on
every other REST route and on `/mcp` itself, so it can do nothing your own token cannot. **Mint it
when you are ready to send, do not cache it, and mint a fresh one if a request fails** -- a refused
upload does not burn a ticket, but a successful one spends it.

The ticket's `filename` and `content_type` are **authoritative**: the filename in your multipart
request is ignored, so point the upload at whatever local path holds the bytes. The response to
that POST carries an `id`, which is attached to nothing until you write it onto a record with
`update_record` -- building the new array from the ids already stored on the field, not from the
attachments you can see resolved (DD-27).

Refused with `validation_failed` naming `GW_BASE_URL` when this deployment has not configured its
own origin, because a relative URL is useless to you. Startup logs a warning in that case.

This tool is **MCP-only**: a REST caller already holds a PAT that reaches the upload route, so a
twin would serve nobody. Declared in section 8, not omitted.

### 5.3 Schema administration (scope: `admin`)

These tools are invisible to `read` and `write` tokens. Every tool in this section that appends an
audit event also accepts an optional `agent` string overriding the connection label, the same rule
as 5.2's write tools. `list_schema_proposals` and `list_object_type_grants` do not: they are reads
and append no audit event a label would attribute.

#### `create_object_type(key, name, name_plural, description, key_prefix, fields?, display_field_key?, agent?)`
Creates a type and optionally its initial fields in one call. `description` is required and
rejected if empty; the error explains that descriptions are how agents interpret the schema.

**Eight field keys are reserved** (DD-20): `key`, `created_at`, `updated_at`, `created_by`,
`updated_by`, `deleted_at`, `comment_count`, `last_comment_at`. Naming a field after one of them
is `validation_failed`; the message names the whole set and says why, because a field of that
name would shadow the system pseudo-field in filters and sorts and the same filter would then
mean two different things depending on where it was asked. Suffix instead: `created_by_name` is
accepted. The rule is on **field** keys only -- an object type keyed `key` is legal -- and it also
covers a relation's `config.inverse_field_key`, refused naming `inverse_field_key` rather than the
field it would have created. Read the set from `describe_capabilities` under
`key_rules.reserved_field_keys` rather than from here.

`display_field_key` names which of `fields` labels a record for a human (DD-23, FR-S11). It
must name one of them and that field must be display-eligible -- not a `relation`, `attachment` or
`user_ref` -- or the call is `validation_failed` listing the eligible keys. Omitted, it defaults to
the first eligible field in the list; omitted with no fields, it stores `null`. Read the resolved
answer back as `describe_object_type`'s `effective_display_field_key`.

**Access:** needs system role `creator` or `admin` (`principals.role`), checked with
`validation_failed`, not `forbidden` -- this is the role axis, not a grant, and there is nothing to
hold a grant on yet. In practice a `member` principal cannot even mint or hold an `admin`-scoped
credential (`role_scope(member) = "write"`, and the mint ceiling refuses `admin` above that), so
this check is defense-in-depth for a route that itself declares only `require_scope("admin")`,
with no `require_role`. Creating the type inserts an `admin` grant for the caller on it in the same
transaction (section 2), so the caller can immediately `add_field` on what it just created.

#### `update_object_type(object_type, changes, agent?)`
`changes` may carry `name`, `name_plural`, `description`, `icon`, `default_level` and
`display_field_key`. `key` and `key_prefix` are immutable after creation and are rejected.

**Access:** needs `admin` on `object_type`. This is also the tool that sets
`changes.default_level`, the level a principal with no grant row gets on this type.

`changes.display_field_key` (DD-23) changes which field labels a record. It is validated
against the type's **live** fields, so a deleted field's key is rejected on the way in, and an
ineligible one is `validation_failed`. An explicit `null` returns the type to the derived rule.
This raises **no schema proposal** and is not destructive: no stored record value changes, only
how one is labeled.

#### `add_field(object_type, key, name, type, description, config?, required?, unique?, indexed?, embed?, default?, agent?)`
Additive, applies immediately (FR-S5). For `relation`, `config` carries `target_type_key`,
`cardinality`, and optionally `inverse_field_key`. For select types, `config.options` carries
option values, labels, and per-option descriptions.

`key` is lowercase snake_case and may not be one of the eight reserved names listed under
`create_object_type` above (DD-20); so may `config.inverse_field_key` not be. Both are
`validation_failed` naming the set.

**Access:** needs `admin` on `object_type`.

#### `update_field(object_type, field_key, changes, agent?)`
Applies immediately when the change is additive: renaming, editing descriptions, adding enum
options, relaxing `required`, toggling `indexed` or `embed`. **Automatically routes to a proposal**
when the change is destructive: type change, removing an in-use enum option, or tightening a
constraint that existing data violates. The response tells the agent which path was taken.

**Access:** needs `admin` on `object_type`, checked before FR-S6 decides whether the change
routes to a proposal -- a narrower credential never reaches the routing decision at all.

#### `propose_schema_change(change_type, object_type, field_key?, payload?, reason?, agent?)`
Explicit entry point for `delete_field` and `delete_object_type`, which never apply directly.
It takes `object_type` plus an optional `field_key` rather than one `target` parameter, because
a single `target` string would be ambiguous between a type and a field, and the REST body
(`POST /api/v1/schema-proposals`) takes the same two explicit keys, so the surfaces cannot drift
(FR-A1).
Returns a proposal id and a computed impact:

```json
{
  "proposal_id": "prop_9f2c",
  "status": "pending_human_approval",
  "change_type": "delete_field",
  "impact": {
    "affected_records": 312,
    "non_empty_values": 287,
    "sample_values": ["Q3 pricing pilot", "Regional GM alignment", "..."]
  },
  "message": "This change requires human approval. An administrator must approve proposal prop_9f2c in the Glosswork UI, under Inbox > /inbox/prop_9f2c. The affected data will be snapshotted before the change is applied."
}
```

Agents cannot approve proposals. There is deliberately no `approve_schema_change` tool at any
scope (FR-S6).

**Access:** needs `write` on `object_type`. Read literally this looks like it admits a
weaker credential than the `admin`-scoped tool already requires -- it does not: every caller here
already cleared `admin` scope to reach the tool at all, and this only narrows *which admin-scoped
types* the caller may propose against. It does not let a `write`-only token call this tool.

#### `list_schema_proposals(status?, limit?, cursor?)`
So an agent can report back on whether the change it requested has been approved.

Newest first. Each proposal carries a **`target`** naming what it is about in words, and the
response carries the two sidecars naming whoever raised it, so a report does not have to be
written in UUIDs:

```json
{
  "proposals": [
    {
      "id": "prop_9f2c",
      "status": "pending",
      "change_type": "delete_field",
      "target_type_id": "26f8e1d5-…",
      "target_field_id": "a08f588e-…",
      "target": {
        "object_type_key": "prospect",
        "object_type_name": "Prospect",
        "field_key": "notes",
        "field_name": "Notes",
        "field_type": "long_text"
      },
      "proposed_by": "9f3c…a1",
      "proposed_agent": "2a8ea7e2-…",
      "…": "…"
    }
  ],
  "next_cursor": null,
  "total_count": 1,
  "principals": {"9f3c…a1": {"display_name": "Dana Chen", "email": "dana@example.com", "is_active": true, "type": "user"}},
  "agent_labels": {"2a8ea7e2-…": {"label": "claude-code", "display_name": null}}
}
```

`target` is resolved from the ids the proposal stored, not from the live schema, so an approved
`delete_field` still names the field it removed. An id naming no row yields nulls rather than an
error (DD-27). `total_count` is the total you could reach, not the length of this page.

`limit` defaults to 50 and is capped at 200; both are published in `describe_capabilities` under
`limits`. Follow `next_cursor` for older proposals.

**Access:** returns only proposals whose target type you hold `write` on -- the same
narrowing as `propose_schema_change`, not a lowering of the tool's own `admin` scope requirement.
The page and `total_count` are both computed under that filter, so the count never reports a
proposal you could not have been shown.

#### `list_object_type_grants(object_type)`
Who has been granted explicit access to one object type, what level each holds, and what the
type's `default_level` is for everyone with no row. Returns the grant rows plus a `principals`
map naming every id they mention, so you can report who holds what without a second call:

```json
{
  "object_type": "initiative",
  "default_level": "none",
  "grants": [{"principal_id": "9f3c...a1", "level": "write", "created_by": "…", "...": "..."}],
  "principals": {"9f3c...a1": {"display_name": "Dana Chen", "email": "dana@example.com", "is_active": true}}
}
```

**Access:** needs `admin` on `object_type` -- the *level*, not a system role. A
principal that defined this type holds `admin` on it and can administer its grants without
administering the deployment.

#### `set_object_type_grant(object_type, principal_id, level, agent?)`
Grant a principal a level on one object type, or change the level it already has. Creates the row
if there is none and overwrites it if there is, so calling it twice is safe.

`level` is `read`, `write`, `admin`, or `none`. `none` is an **explicit deny**: it overrides the
type's `default_level` and keeps overriding it if that default is later widened, which is a
different fact from having no row at all.

`principal_id` is a UUID. Call `find_principals` first if all you have is a name or an email
address -- grants deliberately do not resolve names, because a display name can be ambiguous and
who may use an object type is not a question to answer by guessing.

**Access:** needs `admin` on `object_type`.

#### `revoke_object_type_grant(object_type, principal_id, agent?)`
Remove the principal's row entirely, returning it to the type's `default_level` -- now and after
any later change to that default. This is **not** the same as `set_object_type_grant(..., "none")`,
which is a deny that survives the default being widened.

**Access:** needs `admin` on `object_type`.

## 6. Error conventions

Errors are written to be actionable by an agent, not just diagnostic (FR-M6). Every error carries
a machine-readable `code`, a message that says what to do next, and structured `details`.

| Code | Meaning | Message tells the agent to |
| --- | --- | --- |
| `unknown_object_type` | Bad type key | Call `list_object_types`; the message lists valid keys |
| `unknown_field` | Bad field key | Call `describe_object_type`; the message lists valid keys and flags near-misses |
| `invalid_operator` | Operator illegal for that field type | Use one of the listed valid operators for that type |
| `validation_failed` | Value rejected | Fix the named field; select errors list valid options |
| `version_conflict` | Stale write | Re-read and retry, or pass `force` |
| `relation_blocked` | Delete blocked by inbound links | Unlink the listed records or pass `force` |
| `requires_approval` | Destructive schema change | Await human approval of the returned proposal id |
| `insufficient_scope` | The *credential's* scope is too low | Request a token with the named scope from an administrator |
| `forbidden` | The credential's scope was sufficient and the *principal's grant* on the object type was not (DD-11) | Ask an administrator of the named type, or a system administrator, to grant it; `details` carries `object_type`, `held`, and `required` |
| `not_found` | No such record or comment | Verify the key |
| `feature_disabled` | The deployment has this capability turned off (`details.feature`, `details.setting`) | Use `details.use_instead` when present, or ask an administrator to enable it; the call's arguments were valid (DD-19) |
| `workspace_read_only` | The deployment runs with `GW_READ_ONLY` on, so this write changed nothing (DD-38). Reads, search and export still work. `details` carries `subscribe_url` (or `null`), `setting`, and `attempted` (the tool name) | Tell the person the workspace is read-only and, when `details.subscribe_url` is set, that subscribing there makes changes possible again; without one, an administrator of the deployment turned writes off. Retrying, or another token, never helps |
| `payload_too_large` | The request body exceeds this deployment's limit (DD-18). One setting, `GW_MAX_REQUEST_BYTES`, governs the cap on both surfaces, so the refusal is identical on REST and on MCP | Send less data; `details` carries `limit_bytes` and the `limit_name` an administrator would raise |
| `internal_error` | Something the system could not classify (DD-19) | Retry once; if it repeats, quote `details.request_id` to an administrator, who can find the traceback in the application log |

**`internal_error` is the one error that deliberately tells an agent nothing about what went
wrong.** Every other code exists because some code decided the caller could act on it; this one is
raised by nobody and is constructed only where an unclassified exception would otherwise escape.
Without that seam the SDK's catch-all would return the exception's own message in the tool
result -- a `sqlite3.OperationalError` would hand back real table names and the real database
path -- while REST returns an opaque 500 for the same bug, so the two surfaces would disagree
about the same failure. Both return this code carrying only `details.request_id`, which is the
one thing both safe to disclose and actually useful: it is what an operator greps the application
log for, where the full traceback was written.

Near-miss suggestion on `unknown_field` matters more than it looks: the single most common agent
error is a plausible-but-wrong field key, and returning "did you mean `target_date`?" converts a
retry loop into a single correction.

**`insufficient_scope` versus `forbidden` (DD-11) is the thing that tells an agent whether to
ask for a credential or for a grant.** Both are 403s and both can fire on the same call, but they
name different axes and have different remedies:

- `insufficient_scope` means the *credential's* scope is too low for the operation, full stop --
  the fix is a stronger token (`read` -> `write` -> `admin`), and no grant on any object type
  changes the answer. The code and its meaning come from DD-8. It is raised from a route's or a
  tool's own scope declaration and also from inside a service, for the ceiling half of
  `effective(credential, T) = min(credential.scope, granted(principal, T))` (section 1 of
  DD-11). One refusal carries this code
  without being about scope: `POST /api/v1/me/password` accepts only a browser session
  (DD-13), so a personal access token of any scope is refused with `details.required_auth_method`
  and `details.actual_auth_method`. No stronger token fixes it, and no tool reaches that route.
- `forbidden` means the credential's scope was sufficient and the *principal's grant* on the named
  object type was not -- the fix is an administrator of that type, or a system administrator,
  granting it; a new token never fixes it. `details` carries exactly three keys:
  `object_type` (the type key, or an attachment reference when no single type applies), `held`
  (the level the caller has now, possibly `"none"`), and `required` (the level the call needed).

A caller that is refused should read the code before deciding what to do next: retrying a
`forbidden` with a different token is never the fix, and requesting a grant in response to an
`insufficient_scope` asks the wrong person. `require_level` always reports whichever half of the
`min` actually fell short -- a principal granted `admin` on a type but holding only a `read` token
gets `insufficient_scope`, not `forbidden`, because the grant was never the problem.

**`workspace_read_only` is about the deployment, not the caller (DD-19).** It is a 409 on
REST and a tool result with `is_error` on MCP, never a JSON-RPC error, so the model reads the
message and the subscribe URL in it. A tool is a write when it declares a scope above `read`,
except `list_schema_proposals` and `list_object_type_grants`, which only read; a refused call runs
no tool body and registers no agent label. `tools/list` does not change while the workspace is
read-only: every write tool stays listed and is refused on call. The order of refusals is
credential, scope, read-only, then role and grant, so a `read` token on a write tool still gets
`insufficient_scope`, while a caller whose scope suffices but whose grant does not is told the
workspace is read-only, and hears about the grant only once it is writable again.

## 7. Worked example

The scenario the product exists to serve.

**Human asks:** "Which active initiatives are at risk, owned by Finance, with no discussion in the
last three weeks? And has anyone raised sales pricing concerns on them?"

```
1. list_object_types()
   -> initiative, task, decision, risk, macro_process, micro_process

2. describe_object_type("initiative")
   -> learns: status is single_select with an 'at_risk' option described as
      "target date threatened but recoverable"; owning_function is single_select;
      last_comment_at is a queryable system pseudo-field.

3. query_records(
     object_type = "initiative",
     filter = {"and": [
       {"field": "status", "op": "eq", "value": "at_risk"},
       {"field": "owning_function", "op": "eq", "value": "finance"},
       {"or": [
         {"field": "last_comment_at", "op": "lt", "value": "@today-21d"},
         {"field": "last_comment_at", "op": "is_null"}
       ]}
     ]},
     sort = [{"field": "target_date", "dir": "asc"}],
     fields = ["key", "name", "target_date", "owner", "last_comment_at"]
   )
   -> 4 records

4. search(
     query = "sales pricing concerns",
     object_types = ["initiative", "decision", "risk"],
     mode = "hybrid"
   )
   -> hits including a comment body on DEC-031 and a scope_summary field on INIT-014

5. add_comment(
     record = "INIT-014",
     body = "Flagging for the Monday review: at risk, Finance-owned, no discussion in 24 days. Related pricing concern raised in DEC-031.",
     agent = "claude-code-portfolio-review"
   )
```

Step 3 is the capability that was missing: a filter over three user-defined fields plus a system
pseudo-field, expressed without any integration code. Step 4 reaches into comment bodies, not just
structured fields. Step 5 writes back under an identifiable agent label, and the resulting audit
row records the human principal, the agent label, the MCP surface, and the exact content.

## 8. REST parity

| MCP tool | REST |
| --- | --- |
| `list_object_types` | `GET /api/v1/object-types` |
| `describe_object_type` | `GET /api/v1/object-types/{key}` |
| `query_records` | `POST /api/v1/object-types/{key}/query` |
| `get_record` | `GET /api/v1/records/{key}` |
| `create_record` | `POST /api/v1/object-types/{key}/records` |
| `update_record` | `PATCH /api/v1/records/{key}` |
| `delete_record` | `DELETE /api/v1/records/{key}` |
| `bulk_update_records` | `POST /api/v1/object-types/{key}/bulk-update` |
| `link_records` / `unlink_records` | `POST` / `DELETE /api/v1/records/{key}/links/{field}` |
| `add_comment` | `POST /api/v1/records/{key}/comments` |
| `update_comment` / `delete_comment` | `PATCH` / `DELETE /api/v1/comments/{id}` |
| `search` | `POST /api/v1/search` |
| `get_record_history` | `GET /api/v1/records/{key}/history` |
| `list_changes_since` | `GET /api/v1/changes?cursor=` |
| `find_principals` | `GET /api/v1/principals/directory` |
| `add_field` / `update_field` | `POST` / `PATCH /api/v1/object-types/{key}/fields` |
| `propose_schema_change` | `POST /api/v1/schema-proposals` |
| `list_schema_proposals` | `GET /api/v1/schema-proposals` |
| `list_object_type_grants` | `GET /api/v1/object-types/{key}/grants` |
| `set_object_type_grant` | `PUT /api/v1/object-types/{key}/grants/{principal_id}` |
| `revoke_object_type_grant` | `DELETE /api/v1/object-types/{key}/grants/{principal_id}` |
| `get_attachment` | `GET /api/v1/attachments/{attachment_id}` |
| `create_text_attachment` | `POST /api/v1/attachments` (multipart) |
| `create_object_type` | `POST /api/v1/object-types` |
| `update_object_type` | `PATCH /api/v1/object-types/{key}` |
| `restore_record` | `POST /api/v1/records/{key}/restore` |
| `list_comments` | `GET /api/v1/records/{key}/comments` |
| (MCP only) `describe_capabilities` | -- |
| (MCP only) `create_attachment_upload` | -- |
| `resources/read` of `attachment://{id}` | `GET /api/v1/attachments/{attachment_id}/download` |
| (REST only) upload a binary file | `POST /api/v1/attachments` (multipart) |
| (UI only) approve proposal | `POST /api/v1/schema-proposals/{id}/approve` |
| (UI only) browse audit events | `GET /api/v1/audit-events` |
| (UI only) revert a field change | `POST /api/v1/audit-events/{event_id}/revert` |
| (UI only) revert a record to a prior version | `POST /api/v1/records/{ref}/revert-to-version` |
| (UI only) search index status | `GET /api/v1/admin/search-index` |
| (UI only) trigger a full re-index | `POST /api/v1/admin/search-index/reindex` |
| (UI only) change your own password | `POST /api/v1/me/password` |
| (UI only) reset a user's password | `POST /api/v1/principals/{principal_id}/password` |
| (operator only) take a backup | `POST /api/v1/admin/backup` |
| (operator only) export the deployment | `GET /api/v1/admin/export` |
| (operator only) sweep orphan blobs | `POST /api/v1/admin/blobs/sweep` |
| (operator only) read usage counts | `GET /api/v1/usage` |
| (operator only) take a backup with the operator token | `POST /api/v1/operator/backup` |

`POST` is used for queries because filter trees exceed practical query-string limits. Proposal
approval is deliberately absent from the MCP catalog at every scope. Audit browsing and revert
(DD-21) are REST/UI-only for the same reason: `get_record_history` already gives an agent
the one MCP-visible audit read it's documented to have, and revert is a human-driven action FR-D6
frames as happening through the audit browser. Re-index and index status (FR-Q9) are
REST/UI-only for the same reason: an agent reads `index_lag` on every `search` response, and a
full re-index is an operator action with a real CPU cost. Neither password route is a tool
(DD-13): a self-change is accepted over a browser session only, so an agent's token could not use
one, and a reset is an administrator acting on a person's credentials, which the agent surface has
no reason to hold.

`GET /api/v1/usage` (DD-39, FR-P10) is REST-only for a stronger reason than the rows above
it: the other three operator routes are reachable with a workspace `admin` token, and this one is
not reachable with any tenant credential at all. It reads `X-Operator-Token` and compares it with
`GW_OPERATOR_TOKEN`, so an agent holding the highest token a workspace can mint is refused 401
`operator_token_refused` -- the same answer a caller presenting nothing gets, and the same answer a
deployment that never set the variable gives, byte for byte. A tool would have had to be visible to
the credential that is refused, which is a contradiction rather than a parity gap.

`POST /api/v1/operator/backup` (DD-39, FR-P8) is REST-only for the same reason. It streams the
backup artifact to the holder of `GW_OPERATOR_TOKEN`, and only on a deployment that set
`GW_OPERATOR_BACKUP`; every tenant credential, and the operator token itself where the setting is
off, gets the same 401 `operator_token_refused`.

The three grant endpoints are MCP tools too (DD-11), and the rows above are parity rows. The
authority is the `admin` **level on the object type**, which is the only thing the three grant
routes check. A `member` is out, with no clause written to keep it out, because
`role_scope(member)` caps `your_access` below `admin` (DD-11).

**Two tools are MCP-only, and they are declared rather than omitted (DD-16).**
`describe_capabilities` describes the platform, and a REST caller reads `/openapi.json`;
`create_attachment_upload` mints a credential for the upload route, and a REST caller already holds
a PAT that reaches it, so a twin would exist to serve nobody. Both are named in
`tests/test_mcp_catalog.py::MCP_ONLY_TOOLS`, and the parity walk asserts that **every**
registered tool is either a row in this table or a member of that set. A walk that iterates only
the rows it is given leaves a tool in neither silently unchecked, which is how
`create_object_type`, `update_object_type`, `restore_record` and `list_comments` once had REST
twins and no row here. The same class of gap once left three attachment routes out of a table
whose whole purpose is to make drift visible (DD-29).

The three attachment rows are the two shapes DD-29 settles. Reading an attachment's
bytes has no tool and never will: a tool result is the model's context, so bytes are read through
the `resources/read` request method on the `attachment://{attachment_id}` template, where the
**host** decides what to do with them and the resource link's `size` is how it decides. Uploading
a binary file has no tool for the mirror-image reason: a tool argument is the model's *output*,
base64 tokenizes at roughly one token per raw byte, and a model emitting 100,000 tokens of base64
drifts silently -- the server would hash whatever arrived and store a corrupt file under a valid
sha256. Binary upload is therefore the REST multipart route -- reached (DD-16) with a credential from
`create_attachment_upload` rather than with the bearer token this connection uses, which belongs to
the *client* and is unobtainable by the model. `create_text_attachment`
covers the one payload that may ride inside a tool call: text the model wrote, which it was
emitting anyway.

The three operator actions (FR-P8, FR-E5, FR-P2) have no MCP tool at any scope, for a
different reason than the UI-only rows above: they act on the deployment rather than on anything
in it. A backup and an export both stream an artifact measured in hundreds of megabytes, which is
not a shape a tool result can carry, and the orphan sweep deletes bytes on the volume. All three
require `admin` and are things an operator does, not things an agent does on an operator's behalf.
Restore is not on this table at all because it is not an endpoint: DD-36 makes it a documented
procedure, since a restore API would have to overwrite the database serving it.

CSV endpoints (`POST /api/v1/object-types/{key}/import`, `GET .../export`) are REST-only at MVP;
agents that need bulk writes use `bulk_update_records` or iterate `create_record`.

An exported CSV cell is **neutralized** (DD-20): a `short_text`, `long_text`,
`single_select` or `multi_select` cell beginning with `=`, `+`, `-`, `@`, a tab or a carriage
return is prefixed with one `'`, so a spreadsheet shows the text instead of executing it. Import
strips one apostrophe back under the matching pattern, so the round trip is exact. An agent
reading an exported file directly, rather than importing it, should expect that apostrophe;
`docs/DATA_MODEL.md` section 4 carries the rule and its three stated costs.
