# Glosswork: Agent Onboarding Guide

This is the guide an AI agent reads to start working against Glosswork over MCP, with no
prior knowledge of the schema and no bespoke integration code. It is a path through
[docs/MCP_TOOLS.md](MCP_TOOLS.md), the normative tool catalog, not a copy of it: read that
document for the full tool list, the complete operator table, and error conventions. This guide
covers the five things an agent needs before its first real call.

Its central claim is tested, not asserted: `tests/test_agents_guide.py` drives a real MCP session
through the exact discovery path documented below, derives a filter from nothing but what the
tools return, and checks that every tool name mentioned here actually exists on the server. If
this document and the tool surface ever disagree, that test fails.

## 1. Connecting with a personal access token

Glosswork's MCP endpoint is served from the same process as the REST API, at `/mcp`, over
streamable HTTP. Every call carries a personal access token (a PAT):

```
Authorization: Bearer gw_pat_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

An administrator mints the token (`POST /api/v1/access-tokens` or the operator CLI's
`mint-token`). It is shown once, at mint time, and cannot be recovered afterward, only replaced.

A token carries exactly one scope, and scopes are strictly ordered:

| Scope | Unlocks |
| --- | --- |
| `read` | Discovery and read tools: `describe_capabilities`, listing and describing object types, querying and fetching records, search, comments, history, the change feed, finding principals (`find_principals`), and attachment metadata (`get_attachment`). |
| `write` | Everything `read` sees, plus record, comment, and link mutations: create, update, delete, restore, bulk update, link/unlink, comment CRUD; and uploads (`create_text_attachment`, `create_attachment_upload`). |
| `admin` | Everything, plus schema administration: creating and modifying object types and fields, proposing destructive schema changes and listing proposals (`list_schema_proposals`), and listing, setting and revoking object-type grants (`list_object_type_grants`, `set_object_type_grant`, `revoke_object_type_grant`). |

Scope is enforced by omission, not just by refusal: a tool outside your token's scope does not
appear in `tools/list` at all. A `write`-scoped agent cannot even discover that a tool like
`propose_schema_change` exists, let alone call it.

**A request with no credential is refused, not downgraded.** There is no default scope and no
implicit `admin` fallback. An MCP call with a missing, unknown, revoked, or expired token, or one
belonging to a deactivated principal, is refused before dispatch with a JSON-RPC invalid-request
error naming the problem. If your first call fails this way, the fix is a valid token, not a
retry.

Optionally, set a default agent label for the whole connection:

```
X-Agent-Label: claude-code-portfolio-review
```

Any call that appends an audit event may override it with its own `agent` parameter; the per-call
value wins. See the next section for what this label is, and is not, for.

**The same header works on the REST API.** Send `X-Agent-Label` alongside your
`Authorization: Bearer` on any `/api/v1/*` request and your writes are attributed exactly as they
would be over MCP, through the same registry and the same rules. It is honored for a bearer token
only: the browser UI's own session-cookie calls are a human at a keyboard and carry no label. A
label that is blank, or longer than the `agent_label_max_length` reported by
`describe_capabilities`, is refused with a 422 rather than silently dropped — an *unknown* label
is still always accepted.

## 2. The agent-label convention

The first time a given label is used by a given token, Glosswork registers it automatically.
There is no separate registration call and nothing to configure ahead of time.

Its only purpose is attribution. Every write you make is recorded in the audit trail against the
token's principal and the agent label that made the call, so a human reviewing "who changed this
status" sees not just which credential was used but which agent process was behind it. If you run
several distinct workflows against the same token (a planner, a reviewer, a nightly sweep), giving
each its own label is how their writes stay distinguishable later, in `get_record_history` and in
the change feed.

**The agent label is descriptive metadata. It is never a security control.** Nothing checks a
label against a permission, and nothing prevents a token from sending any label it likes,
including one that names a different agent than the one actually calling. Authorization derives
entirely from the bearer token's own scope, full stop. Do not use a label to imply a permission
level, and do not treat a label you receive from elsewhere in the system as proof of who made a
call; the token is the only thing that proves that. This is a named risk in the product's own
design document, not an incidental gap: label spoofing is possible and accepted, because the
mitigation is that authorization never looks at the label to begin with.

## 3. The discovery path

The schema is not fixed at build time. Object types and fields are created and edited at runtime,
so the platform gives you three tools to orient yourself instead of expecting you to already know
its shape. Call them in this order.

**1. `describe_capabilities()`**, read once, early in a session. It returns the parts of the
platform that hold true across every object type: the full field-type catalog with each type's
legal operators, the filter grammar's structure (`and`/`or`/`not`, the shape of a condition, how
`between` works), the relative date tokens and how to use `@me`, and current deployment limits
(query and search defaults, whether semantic search is enabled). Reading this first means you
never have to guess at the grammar while you are also trying to learn a specific schema.

The `limits` block also carries every **ceiling** you can hit, so you can size a page before
you ask for one rather than discovering the cap by being refused:
`query_max_limit`, `history_max_limit`, `comment_max_limit` and `filter_max_depth`, alongside the
search and changes limits. Every one of them is enforced in the service
layer, so REST and MCP refuse identically with `validation_failed` carrying `details.max_limit`.
Read the numbers from this document rather than from prose anywhere else, including this one: a
deployment can change them. `describe_capabilities` is `read` scope (DD-30), so it is in
`tools/list` for a `read`, `write` or `admin` token, and it returns the same document whichever
of them asks.

**2. `list_object_types()`**, see what exists **and what you may do to it**. Returns every object
type with its key, name, description, key prefix, record count, field count, and `your_access`.
Read the descriptions; they say what each type is for, not just what it is called.

`your_access` is one of `none`, `read`, `write`, `admin`, and it is the answer to "what can this
token actually do to this type right now" -- already composed from your credential's scope and
your principal's grant, so you never have to reason about the two separately. **Types you cannot
read are simply not in the list.** A short list is not a broken deployment; it is the deployment
telling you what you were given.

This matters because the tool list does not narrow with it: a `write`-scoped token still sees
`create_record` in `tools/list` even when every type is read-only to you. That is deliberate --
the catalog is a function of your token, not of your grants -- so **read `your_access` before
assuming a write will land**. If you attempt one anyway you get a `forbidden` error naming the
type, the level you hold, the level needed, and who can grant it; that is a fine way to find out,
but one orientation call is cheaper than a failed write.

**3. `describe_object_type(object_type)`**, the load-bearing call. For the one type you are about
to work with, it returns every field: its key, name, type, agent-facing description, whether it
is required or unique, whether it is indexed, its default, and, critically, the exact filter
operators legal for that field. Select fields also return every option's value, label, and
description. Relation fields return their target type, cardinality, and inverse field. Pass
`include_samples=true` to also get up to three real example values per field, drawn from live
data; this materially improves your first attempt at a free-text filter or a value you have not
seen written before.

The order matters because each step narrows what you need to know next: capabilities are
platform-wide and worth reading once, the object type list tells you which schema to read, and
`describe_object_type` is the only call that gives you the field keys, option values, and
operators you are allowed to put in a query. Never guess a field key or an option value; every
one you use in a `query_records` filter or a `create_record`/`update_record` values document
should trace back to a `describe_object_type` response you actually read in this session.

## 4. What you may and may not see

Seeing an object type in the schema and being able to do something to it are different
questions, and this platform answers them separately. This section is what you need to
reason about the difference without guessing.

### `your_access`, composed

Every entry from `list_object_types()` and every response from `describe_object_type()` carries
`your_access`, one of `"none"`, `"read"`, `"write"`, `"admin"`. A `list_object_types()` entry
looks like this (the real field set, from the server, not illustrative):

```json
{
  "key": "decision",
  "name": "Decision",
  "description": "A recorded decision with its rationale, alternatives considered, and owner.",
  "key_prefix": "DEC",
  "record_count": 12,
  "field_count": 5,
  "your_access": "read"
}
```

`your_access` is not your credential's scope and it is not your principal's raw grant on the
type; it is the two of them **already composed** -- the smaller of what your token can do
anywhere and what your principal is allowed to do to this specific type. A read-scoped PAT held
by the principal who administers `decision` is told `your_access: "read"` here, because a read
token cannot exercise an admin grant no matter who holds it. Your credential is a ceiling: it can
only narrow what your grant would otherwise let you do, never raise it. Do not try to infer your
scope from `your_access`, and do not assume a `write` or `admin` value here means your token can
reach every write or schema-admin tool -- it means only that *this type* is that open to you.

### A short list is not a broken deployment

`list_object_types()` returns only the types you hold at least `read` on. A type you cannot read
at all is **omitted**, not returned with `your_access: "none"` and not refused with an error. If
you expected a dozen types and see three, that is the deployment telling you what you were given,
not a misconfigured server. The same omission rule applies inside `search` results (section 6):
a type you cannot read never appears among the hits, silently. What is *not* silent is naming a
type you cannot read explicitly -- see the next point.

### The two 403 codes, and how to tell them apart

A refused call comes back with one of two error codes, and which one you got tells you what to do
next. This is the single most useful thing in this section: **do not treat them as interchangeable
"permission denied."**

**`insufficient_scope`** means your credential itself -- the token's scope -- is too low for the
call, independent of any grant. The remedy is a stronger credential. A real message, produced when
a principal holding `admin` on `decision` calls a schema-admin operation with only a `read`-scoped
PAT:

> Admin access to object type 'decision' requires the 'admin' scope; your credential has the
> 'read' scope. Use a credential with the 'admin' scope, or ask an administrator for one.

**`forbidden`** means your credential's scope was high enough, and your principal's *grant* on
this specific object type was not. The remedy is someone granting you access, not a different
token. A real message, produced when a `write`-scoped PAT holder has no grant at all on
`decision` (`default_level` is `"none"`) and calls something that needs `read`:

> You have no grant on object type 'decision'. You hold 'none'; this call needs at least 'read'.
> Ask an administrator of 'decision', or a system administrator, to grant it.

When the caller already holds some level below what is needed, the wording changes to name what
is held instead of "no grant":

> Your access to object type 'decision' is 'read'; this call needs at least 'write'. Ask an
> administrator of 'decision', or a system administrator, to raise it.

Both errors name the object type, the level required, and (for `forbidden`) who can fix it. Read
the code before you decide what to do: `insufficient_scope` sends you to reconnect with a
different token; `forbidden` sends you to a human with a request, never to a retry.

### `{"redacted": true}` in a relation

A record you can read may link to a record whose type you cannot. The link itself is never
hidden; only the target is. `get_record(record, include=["links"])` returns each relation field as
a list of entries, and an entry you cannot see comes back as:

```json
{"redacted": true}
```

carrying no key, no id, no title, nothing else. A readable target's entry, by contrast, carries
`{"key": "TASK-14", "id": "..."}`. Two things follow, and both matter more than they look:

- **The count is still truthful.** `has_links` / `has_no_links` and the length of the list you get
  back agree with what a system administrator sees. A record that is genuinely linked to three
  things always shows three entries to every caller; some of those entries are just opaque to you.
  Nothing here is a filter lying about what exists.
- **There is nothing to retry.** A redacted entry is not a transient failure, a pagination
  boundary, or a hint that a different call would resolve it. The correct response is to report to
  your human that the link exists and that you cannot see what it points to -- not to guess the
  target's key and call `get_record` on it (that call, if the key happens to be right, still comes
  back `forbidden`), and not to try `linked_to`/`linked_to_any` with a guessed key either: naming a
  key in a type you cannot read is itself `forbidden`, not an empty result, because you named the
  thing.

### The tool list is not filtered by grants

`tools/list` is a pure function of your token's *scope* (section 1), never of your grants.
Seeing `create_record` or `add_field` in your tool list tells you your token's scope permits that
kind of call somewhere; it tells you nothing about which object types you can actually use it on.
A `write`-scoped token still sees every write tool even on a deployment where every type is
read-only to it. **Read `your_access` on the type you are about to touch before attempting a
write** -- it is one orientation call, and it is cheaper than a failed one.

This cuts both ways on the three grant tools (DD-11). Seeing `set_object_type_grant` in your
list means your token carries `admin` scope, not that you may permission anything: you may
permission exactly the object types whose `your_access` is `"admin"`, which may be one of them or
none.

### What to do when you are refused

If a call comes back `forbidden`, stop. Do not retry the same call, do not try the same operation
against an adjacent or similarly-named type on the theory that one of them will work, and do not
look for a different tool or write path that might reach the same data by another route -- if one
existed it would be gated the same way, and hunting for a bypass is not a legitimate use of a
`forbidden` response. Report to your human, plainly: the object type you were refused, the level
you were told you hold, and the level the call said it needs -- all three are in the error's
`details`. That is a request a human can act on in one step (grant, promote, or tell you to stop);
a retry loop is not.

**One case where the answer is not "stop and report" (DD-11).** If the refusal is about a
type whose `your_access` is `"admin"` -- because you defined it, or because someone granted you
`admin` on it -- then you *are* the administrator that message is telling the caller to ask, and
the refusal is about someone else. That is the case where `list_object_type_grants` and
`set_object_type_grant` are yours to use: read who holds what on the type, and grant the level
that is actually needed.

Two limits on that, and neither is negotiable. You may only permission types you hold `admin` on;
every other type answers `forbidden` exactly as before. And granting is a decision about who may
see and change data, so **do it when a human asked you to, and report what you changed** -- never
raise your own level, and never widen a type's `default_level` to route around a refusal you were
handed. Adding a grant nobody asked for is the same failure as hunting for a bypass, with an
audit row attached.

## 5. The filter grammar by example

The same filter grammar is used by `query_records`, `search`, `bulk_update_records`, and CSV
export. It is a small tree of conditions and combinators, always JSON, never a query string.

A single condition is a complete, valid filter on its own:

```json
{"field": "status", "op": "eq", "value": "active"}
```

Combine conditions with `and` (or `or`), and use `in` for a set of allowed values:

```json
{
  "and": [
    {"field": "status", "op": "in", "value": ["active", "at_risk"]},
    {"field": "owning_function", "op": "eq", "value": "finance"}
  ]
}
```

`and`, `or`, and `not` nest arbitrarily. This is the shape of a real multi-condition question
("at-risk or blocked initiatives, target date within 30 days, owned by me or with no sponsor set,
not tagged deprioritized"):

```json
{
  "and": [
    {"field": "status", "op": "in", "value": ["at_risk", "blocked"]},
    {"field": "target_date", "op": "lte", "value": "@today+30d"},
    {"or": [
      {"field": "owner", "op": "eq", "value": "@me"},
      {"field": "sponsor", "op": "is_null"}
    ]},
    {"not": {"field": "tags", "op": "has_any", "value": ["deprioritized"]}}
  ]
}
```

Every field type has its own legal operator set (`describe_object_type` returns it per field, so
never memorize this table; it is here to show the shapes):

- Text fields (`short_text`, `long_text`, `url`): `eq`, `neq`, `contains`, `starts_with`, `in`,
  and so on.
- `single_select`: `eq`, `neq`, `in`, `not_in`.
- `multi_select`: `has_any`, `has_all`, `has_none`, `is_empty`, `is_not_empty`, for example
  `{"field": "tags", "op": "has_any", "value": ["deprioritized"]}` above.
- `date`/`datetime`: `eq`, `neq`, `gt`, `gte`, `lt`, `lte`, `between` (a two-element array,
  inclusive on both ends): `{"field": "target_date", "op": "between", "value": ["@start_of_month", "@today"]}`.
- `user_ref`: `eq`, `neq`, `in`, and accepts `@me`, resolved server-side to the calling principal
  so you never have to look up your own identity.
- `relation`: `linked_to`, `linked_to_any`, `has_links`, `has_no_links`.
- Every field type, plus every system pseudo-field, also accepts `is_null` / `is_not_null`.

Date tokens are resolved server-side, so you never compute a calendar date yourself: `@today`,
`@now`, `@start_of_week`, `@start_of_month`, `@start_of_quarter`, `@start_of_year`, each with an
optional signed offset such as `@today-7d`, `@today+30d`, `@start_of_month-1M`, or `@now-12h`
(units: `d`, `w`, `M`, `y`, `h`, `m`).

Filters also reach system pseudo-fields that exist on every object type without being declared as
a field: `key`, `created_at`, `updated_at`, `created_by`, `updated_by`, `comment_count`,
`last_comment_at`, `deleted_at`. This is how "records with no discussion in three weeks" becomes
a real filter, not a manual scan:

```json
{"or": [
  {"field": "last_comment_at", "op": "lt", "value": "@today-21d"},
  {"field": "last_comment_at", "op": "is_null"}
]}
```

**Those eight names are reserved as field keys.** When you design a schema, do not name a field
`key`, `created_at`, `updated_at`, `created_by`, `updated_by`, `comment_count`, `last_comment_at`
or `deleted_at`: `create_object_type` and `add_field` both refuse it with `validation_failed`
naming the whole set, and so does a relation's `config.inverse_field_key`. The reason is the
paragraph above. A field of the same name would shadow the system one, so `created_by eq @me`
would match an attacker-writable value on that type and the real column everywhere else, and one
filter would mean two different things depending on where it was asked. Suffix instead:
`created_by_name` is accepted. Object type keys are not covered; a type keyed `key` is legal.
`describe_capabilities` publishes the set as `key_rules.reserved_field_keys`, so read it from
there rather than from this list.

An empty or omitted filter matches every live record. Everything above is validated against the
schema on the server: an unknown field key or an illegal operator for that field's type comes
back as a structured error naming the mistake and, for a near-miss field key, suggesting the
correct one, so a wrong guess is a one-step correction rather than a retry loop.

## 6. `search` finds records; `get_record` fetches them

This is the single easiest wrong assumption to make, so it gets its own section: **`search` does
not return full records, and you cannot read a field value out of a search hit.**

`search(query, ...)` returns ranked results, each with `record_key`, `object_type`, `title`, a
relevance `score`, a `hit_source` (which field, or which comment, actually matched, with the
comment's author and timestamp when it was a comment), a `snippet` with the matching text
highlighted, and `other_matches` counting additional matches on the same record. It does not
carry the record's data document, its version, its comments, or its links. Use `search` when you
know roughly what something is about but not which record or field holds it, including when the
answer might live only in a comment body that no structured filter could reach.

`search` narrows itself to what you may read the same way `list_object_types` does (section 4): if
you omit `object_types`, the results silently cover only the types you hold at least `read` on --
a type you cannot read never produces a hit, and you get no signal that anything was left out.
Naming a type explicitly in `object_types` is different: if that type exists but you cannot read
it, the call returns `forbidden` rather than an empty or partial result set, because you named the
thing. Do not read a short or empty `results` list as evidence the corpus has nothing on your
query; it may mean only that the types holding the answer are closed to you.

To act on a hit, whether that means reading the rest of the record, checking its current version
before an update, or pulling its comment thread, call **`get_record(record)`** with the
`record_key` the search hit gave you. `get_record` accepts either the human-readable key
(`INIT-014`) or the record's UUID, and returns the full data document plus `version`, with
`comments`, `links`, `history`, and `attachments` available on request via `include`.

A record's key such as `INIT-014` is not itself indexed, searchable text: `search("INIT-014")`
will not reliably find that record by treating the key as a query term. If you already know the
key, skip search entirely and call `get_record` directly.

Also read `index_lag` on every `search` response (`{"pending_jobs": ..., "failed_jobs": ...}`): a
non-zero `pending_jobs` means a very recent write may not be semantically searchable yet, even
though `query_records` and `get_record` see it immediately. Keyword matches are unaffected by
this lag.

## 7. Files: handles, never bytes

**Read this from the endpoint, not from here.** `describe_capabilities` publishes all of it as an
`attachments` block, and that tool is `read` scope (DD-30), so every credential that can
call anything can call it. What follows is the same recipe in prose; where the two disagree, the
tool is right, because it is generated from the code that serves the routes.

An attachment is a **handle**. The id travels through tools; the bytes travel over HTTP or through
`resources/read`. Nothing in this product will ever ask you to emit a file as base64 in a tool
argument, and you should not do it: base64 costs roughly one output token per raw byte, so a
100 KB PDF is about 100,000 output tokens, no model can emit more than about 130 KB that way, and
a long base64 run drifts. The server would hash whatever arrived and store a corrupt file under a
perfectly valid sha256, and nobody would find out.

### The credential question, answered once

**The bearer token this connection authenticates with belongs to your MCP client, not to this
server.** No tool will ever return it, and no part of the protocol hands a model its own connection
credential. If you find yourself looking through tool output for a token, or reaching outside the
session for one, stop: that is a dead end, and the recipe below is the way out of it.

For an upload, call `create_attachment_upload` and it gives you a credential you *can* see. For a
download you need no credential at all if you use `resources/read`. If you happen to hold a
personal access token of your own, outside this connection, you may use that for either.

### Reading a file

`get_record(record, include=['attachments'])` and `get_attachment(attachment_id)` both return, per
attachment, its `filename`, `content_type`, `byte_size`, `sha256`, `uploaded_at`, `uploaded_by`
and `download_url` -- the same document on both surfaces -- plus one `resource_link` content block
carrying `uri`, `name`, `mimeType` and `size`. Read `size` before you fetch anything.

The way that needs nothing from you is `resources/read` on `attachment://<id>`. Text types come
back as text; everything else comes back as a blob your host may render as a document or may
ignore. In Claude Code you can mention it directly:

```
@glosswork:attachment://0d5b7c2a-1111-2222-3333-444455556666
```

If you have a shell **and** a token of your own, `download_url` over `curl` costs no context at all
and lets you grep the file instead of reading it. `download_url` is absolute when the deployment
has set `GW_BASE_URL` and a bare path when it has not; read it from the response rather than
assembling it, and read `attachments.download_url_pattern` from `describe_capabilities` if you want
to know which shape this deployment serves.

### Writing a file

If you wrote the document yourself -- a summary, a report, a CSV extract, JSON -- use the tool:

```json
{"name": "create_text_attachment",
 "arguments": {"filename": "q3-summary.md", "text": "# Q3\n\n..."}}
```

`content_type` defaults to `text/markdown` and must be one of the types in
`attachments.text_content_types`.

For anything else, including every binary format, ask the endpoint for an upload:

```json
{"name": "create_attachment_upload",
 "arguments": {"filename": "report.pdf", "content_type": "application/pdf"}}
```

The result carries `upload_url`, `method`, `field`, `authorization`, `expires_at`, `max_bytes` and
a ready `curl` line. Use the values it returned, verbatim -- do not assemble a URL from a host you
saw somewhere, and do not substitute a different credential:

```bash
curl -X POST -H "$AUTHORIZATION" -F "file=@/local/path/whatever.pdf" "$UPLOAD_URL"
```

That credential is **single use** and lives for `limits.upload_ticket_ttl_seconds` (five minutes by
default). Mint it when you are ready to send, do not cache it, and mint a fresh one if a request
fails. It reaches the upload route and nothing else, so it is not worth keeping. The **filename and
content type you named at mint are what gets stored**, so point `curl` at whatever local path holds
the bytes and do not worry about matching names.

If the mint is refused naming `GW_BASE_URL`, this deployment has not been told its own origin and
cannot give you an absolute URL. That is an operator fix, not something to route around.

Both paths return an `id`. **The id is attached to nothing until you write it onto a record.** Do
that with `update_record` on the attachment field:

```json
{"name": "update_record",
 "arguments": {"record": "BRF-004", "values": {"files": ["<existing id>", "<new id>"]}}}
```

Build that array from the ids **already stored on the field**, which you get from
`get_record(record)`'s `data`, and not from the attachments you can see resolved under
`include=['attachments']`. Those two lists are not the same: an attachment you are not allowed to
read is silently omitted from the resolved list, and rebuilding the array from what rendered would
destroy an id you cannot see (DD-27).

### What you cannot do

If you have no shell -- a chat host such as Claude Desktop or claude.ai -- you cannot upload a
user's local file through this server, and you cannot do it through any other MCP server today
either; the specification has no upload primitive (SEP-2631 is an unsponsored draft, and what it
proposes is out-of-band HTTPS transfer anyway). An upload ticket does not change that: it hands you
a credential, and you still need something that can read the file and make an HTTP request. The
human path is the Glosswork UI, which has an attachment widget on every record. Say so plainly
rather than attempting a workaround.

The server also will not fetch a URL for you. FR-P1 promises no network egress at runtime, the
target deployment sits behind an egress-blocking firewall, and a server that fetches
caller-supplied URLs is an SSRF surface.

## Where to go next

`describe_capabilities`, `list_object_types`, and `describe_object_type` are enough to write a
correct first `query_records` or `search` call with no prior knowledge of the schema. Start with
`describe_capabilities`: it is `read` scope, takes no arguments, and is this endpoint's manual
(DD-30). The server `instructions` you received on connecting are an index that names it, not a
place detail is carried, because that string reaches you through a delivery window this server
cannot see or control. For the
complete tool catalog (all 32 tools across discovery, write, and schema administration), the full
operator table, error codes and what each one tells you to do next, and the REST endpoint each
tool maps to, read [docs/MCP_TOOLS.md](MCP_TOOLS.md).
