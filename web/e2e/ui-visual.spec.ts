/**
 * Visual regression for the design system: committed screenshot baselines so migrating one
 * screen cannot silently wreck another. Baselines are platform-specific (`-darwin` suffix on this
 * machine); a first run on a new platform creates its own set.
 *
 * Determinism notes: this spec runs as the `visual` Playwright project against ITS OWN app
 * server and database (`playwright.config.ts`), so every pixel — including the
 * object-type nav and the header's wrap height — is a function of this file's fixtures alone;
 * the functional specs' racing seeds never reach it. The viewport is pinned; `toHaveScreenshot`
 * disables animations and waits for stable frames; fonts are bundled Plex (no network
 * variance). The table shots click a Name sort first, because the default query has no ORDER BY
 * and SQLite's row order is not stable run to run. The fixture is idempotent: a retry lands in
 * a fresh worker whose beforeAll runs again against the same database.
 */
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, request as apiRequestModule, type APIRequestContext } from "@playwright/test";
import {
  VISUAL_AUTH_HEADER,
  VISUAL_BASE_URL,
  VISUAL_PRINCIPAL_ID,
  signInAsE2eAdmin,
} from "./constants";

const HERE = path.dirname(fileURLToPath(import.meta.url));

test.use({ viewport: { width: 1280, height: 800 } });

/** The table-shot fixture type. NOTHING in this spec ever mutates its records, so a retried
 * table shot stays true no matter what ran before it; the conflict test mutates its own
 * separate type below. Keys are per-type counters (`allocate_key_seq`), so VIS-00N is
 * deterministic on the run's fresh database regardless of what other specs seed. */
const OBJECT_TYPE_KEY = "vis_initiative";

/** The merge-conflict fixture type: its one record is raced and renamed, which is why it gets
 * a type of its own instead of poisoning the table shots' rows. */
const CONFLICT_TYPE_KEY = "vis_conflict";

/** The record-detail fixture types. Both are separate from the table types
 * (`vis_initiative`, `vis_conflict`), which are frozen: record detail needs a relation field,
 * and adding one to `vis_initiative` would add a column to the table view and repaint the
 * committed table baselines. `DETAIL_TYPE_KEY` is the screen under shot;
 * `DETAIL_TARGET_TYPE_KEY` exists only to be linked to. */
const DETAIL_TYPE_KEY = "vis_detail";
const DETAIL_TARGET_TYPE_KEY = "vis_detail_target";
const DETAIL_RECORD_KEY = "VDET-001";
const DETAIL_TARGET_RECORD_KEY = "VDT-001";

/**
 * The attachment fixture, and the reason it is a **second record** of `vis_detail` rather than a
 * value on VDET-001.
 *
 * The attachment FIELD goes on the existing type (a new visual fixture type is never free),
 * which repaints exactly one baseline: `record-detail-fields`, where VDET-001
 * grows an empty-state Files row. Putting a *value* on VDET-001 would repaint a second:
 * `create_record` appends one audit event per key in the create payload
 * (`services/records.py::create_record`), the collapsed write row reads "Created N fields"
 * (`record-detail/auditGroups.ts::summarizeWrite`), and N would move from 3 to 4 in
 * `record-detail-audit-timeline`. A second record carries its own audit trail and leaves
 * VDET-001's alone. `vis_detail` has no table baseline (see this file's header at
 * DETAIL_TYPE_KEY), so the extra row repaints nothing there either.
 *
 * It holds TWO ids: one real, and one that resolves to no attachment row. The second is the
 * placeholder row, which is the sharpest rule here: the number of rows always equals the number of
 * ids stored, so the screen never under-reports what the record holds. Neither surfaces a UUID,
 * so this shot needs no mask.
 */
const DETAIL_ATTACHMENT_RECORD_KEY = "VDET-002";
const DETAIL_ATTACHMENT_FILENAME = "runbook-outline.txt";
/** Fixed bytes, so the rendered size ("1.2 KB") is deterministic and can stay pixel-checked. */
const DETAIL_ATTACHMENT_BODY = "Runbook outline.\n".repeat(75);
/** A well-formed id that names no `attachments` row. `fieldtypes.py` validates an attachment
 * value for shape only, and `_check_attachment_refs` stores an unresolved id rather than
 * refusing it, so this is a value the product genuinely produces. */
const DETAIL_MISSING_ATTACHMENT_ID = "00000000-0000-4000-8000-0000000000ff";

/** The schema-editor fixture type, a type of its own per the frozen-fixture rule. Its
 * `legacy_note` field is the one the blast-radius shot's test deletes: the two records below
 * give that deletion a real, non-zero affected-records count instead of an empty one. The
 * proposal the deletion creates is rejected via the API in the same test (never approved), so
 * the field and the schema are left exactly as this fixture leaves them for the next run or
 * the other three shots in this suite. */
const SCHEMA_TYPE_KEY = "vis_schema";

/** The pending-proposal fixture type, a type of its own per the frozen-fixture rule. Its
 * `legacy_note` field gets a `delete_field` proposal that is left pending forever (never
 * approved or rejected), the one deterministic row the "Pending schema proposals" panel shot needs; `PROPOSAL_REASON`
 * is how `ensureSettingsFixtureSeeded` tells idempotently whether that proposal already
 * exists on a retry, since the frontend has no route that returns a proposal's numeric
 * object-type/field ids to compare against directly. */
const SETTINGS_TYPE_KEY = "vis_settings";
const SETTINGS_PROPOSAL_REASON =
  "The pending-proposals panel's visual-baseline fixture, left waiting.";

/** The search fixture types, types of their own per the frozen-fixture rule. Two types so
 * the grouped-results shot has two `<section>`s; both carry the exact same pinned two-word
 * phrase ("photon ledger", checked against every other string literal in this file for
 * uniqueness) in a `long_text` field, whose `embed` defaults to `true`
 * (`services/schema.py::embed_default = field_type == "long_text"`, unlike the `short_text`
 * `legacy_note` fields the schema/settings fixtures above use precisely because they must NOT
 * enter the search index). The primary record's phrase also appears in a comment, so it
 * keyword-matches two locations and the "other matches" `Badge` has a real, non-zero count to
 * render — the same shape `e2e/comment-search-end-to-end.spec.ts` uses for its own
 * comment-search fixture.
 * Every search shot below pins `mode=keyword`: FTS5 bm25 ranking over fixed text is exact, so
 * this sidesteps the embedding model's cross-run floating-point ranking risk the hybrid golden
 * report (docs/PERFORMANCE.md) exists to measure, which this visual suite has no business
 * re-litigating. */
const SEARCH_TYPE_KEY = "vis_search";
const SEARCH_SECONDARY_TYPE_KEY = "vis_search_secondary";
const SEARCH_RECORD_KEY = "VSRCH-001";
const SEARCH_QUERY_PHRASE = "photon ledger";

/** The activity fixture type, a type of its own per the frozen-fixture rule. Two field
 * updates on one record produce two revertible changes with real old->new values, alongside the
 * record's own non-revertible "create". The screen it feeds was once the audit browser and is
 * now `/activity`; the type key stays `vis_audit` because renaming a seeded
 * fixture type would repaint every baseline that frames the sidebar. */
const AUDIT_TYPE_KEY = "vis_audit";
const AUDIT_RECORD_KEY = "VAUD-001";

/** The CSV import wizard fixture type, a type of its own per the frozen-fixture rule. Only
 * the type is seeded here; the wizard's own upload/dry-run/commit flow is what creates rows,
 * driven by the committed fixture CSV below: the shot drives a committed fixture CSV under
 * e2e/ through upload and dry-run against this fixture type. */
const IMPORT_TYPE_KEY = "vis_import";

/** `e2e/fixtures/vis-import.csv`: three rows, one with a `status` value ("not_a_real_status")
 * that is not one of the fixture type's two options — a real, backend-produced dry-run error
 * for the danger-toned errors-table shot, without any mocked response. */
const IMPORT_FIXTURE_CSV = path.join(HERE, "fixtures", "vis-import.csv");

/** The wide-table fixture type, a type of its own per the frozen-fixture rule: widening
 * `vis_initiative` would conflate a fixture change with the layout change in the same diff
 * image. Twelve fields (36 + 12x180 = 2196px of columns) so the table overflows the 1280px
 * viewport into `tableWrapClass`'s own scroller, `ColumnPicker` renders twelve rows, and the
 * controls stack was once tall enough to push the table below the fold (it is now one row,
 * which the two geometry scenarios below measure). `notes` is the `long_text` field whose long
 * values once widened their column, and VWID-001 carries the deliberately long single value;
 * VWID-002 and VWID-003 carry short ones, so the display assertion can compare a long-valued
 * `td` against a short-valued one in the same column.
 *
 * `long_text` fields default to `embed: true` (`services/schema.py`), so this text does enter
 * the search index. It is checked against `SEARCH_QUERY_PHRASE` ("photon ledger") for
 * non-overlap, so the search shots' pinned keyword queries are unaffected. */
const WIDE_TYPE_KEY = "vis_wide";
const WIDE_LONG_RECORD_KEY = "VWID-001";
const WIDE_SHORT_RECORD_KEY = "VWID-002";
/** VWID-002 and VWID-003's `notes`. Named rather than repeated because the keyboard-save
 * scenario below writes over VWID-002's and restores it, and a restore that drifts from the
 * seed would silently repaint `table-wide.png` on the NEXT run. */
const WIDE_SHORT_NOTES = "Short note.";

/** The fixture's saved view: a realistic name at a realistic length, because the one-row
 * toolbar scenario measures the toolbar with it in the View menu's trigger. */
const WIDE_SAVED_VIEW_NAME = "Active cutovers";

/** The fixture's field keys, in declaration order (thirteen; the type's own comment above says
 * "twelve fields" and means the twelve that follow `name`). The saved view's column order is the
 * default one, so loading it moves nothing but the trigger's text. */
const WIDE_FIELD_KEYS = [
  "name",
  "status",
  "notes",
  "budget",
  "owner",
  "region",
  "phase",
  "risk",
  "sponsor",
  "vendor",
  "milestone",
  "reference",
  "workstream",
];

/**
 * The record-detail comment's current body, written as markdown. An ATX heading, a paragraph and a
 * bullet list — the three constructs a real observed comment used — so one shot proves heading
 * appearance, list rendering and the DD-41 type scale at once.
 */
const DETAIL_MARKDOWN_COMMENT =
  "## Kickoff\n\n"
  + "Scope confirmed with stakeholders after the review call.\n\n"
  + "- vendor window closes first\n"
  + "- manual journal for the residue";

const WIDE_LONG_NOTES =
  "A single deliberately long value, held in one unbroken run of prose so that nothing in it " +
  "offers the browser a wrapping opportunity it could use to keep this column inside its " +
  "declared width: the cutover depends on the vendor's reconciliation window closing before " +
  "the quarter does, and on the finance team accepting a manual journal for the residue.";

let apiContext: APIRequestContext;

/** Idempotent per type: a retry lands in a fresh worker whose beforeAll runs again against the
 * same database, so an existing type is left exactly as it stands. */
async function ensureSeeded(
  typePayload: Record<string, unknown> & { key: string },
  rows: Record<string, unknown>[],
): Promise<void> {
  const existing = await apiContext.get(`/api/v1/object-types/${typePayload.key}`);
  if (existing.ok()) {
    return;
  }
  const createType = await apiContext.post("/api/v1/object-types", { data: typePayload });
  expect(createType.ok(), await createType.text()).toBeTruthy();
  for (const row of rows) {
    const created = await apiContext.post(`/api/v1/object-types/${typePayload.key}/records`, {
      data: row,
    });
    expect(created.ok(), await created.text()).toBeTruthy();
  }
}

/**
 * One named saved view on the wide fixture, marked default so the page loads it.
 *
 * **It exists to keep the one-row toolbar scenario honest.** The View menu's trigger names the
 * current view, so a fixture with no saved view renders the trigger at its shortest and that
 * scenario measures the easy case — one row at 800px on a page state a person rarely has — while
 * the real one wraps. The name is a realistic length (15 characters), not a worst case: the worst
 * case is answered by the trigger's own `max-w-28` and its `title`, not by choosing a short
 * fixture.
 *
 * **The config is the default one**, field for field, so applying it changes the View trigger's
 * text and nothing else about the rendered table: no filter, no sort, no grouping, every column
 * visible in declaration order. `table-wide.png` therefore shows the toolbar's trigger text and
 * nothing else of the view.
 */
async function ensureWideSavedViewSeeded(): Promise<void> {
  const existing = await apiContext.get(`/api/v1/object-types/${WIDE_TYPE_KEY}/saved-views`);
  expect(existing.ok(), await existing.text()).toBeTruthy();
  if (((await existing.json()) as unknown[]).length > 0) return;

  const created = await apiContext.post(`/api/v1/object-types/${WIDE_TYPE_KEY}/saved-views`, {
    data: {
      name: WIDE_SAVED_VIEW_NAME,
      mode: "table",
      is_default: true,
      config: {
        filter: null,
        sort: [],
        groupBy: null,
        columns: { order: WIDE_FIELD_KEYS, visibility: {}, sizing: {} },
        mode: "table",
      },
    },
  });
  expect(created.ok(), await created.text()).toBeTruthy();
}

/**
 * The record-detail fixture: `ensureSeeded` above is idempotent per *type*, but
 * this fixture also needs one-time side effects (a link, a comment, an edit, a field update)
 * that must not repeat on a retry. Gated the same way `ensureSeeded` gates row creation: check
 * for the main type first, and skip everything — types, rows, and side effects alike — when it
 * already exists, so a retried worker leaves the database exactly as it stands.
 *
 * This fixture seeds no agent-labeled comment, and since DD-17 that is a choice rather than a
 * limitation. REST used to drop attribution outright — `middleware.py` hardcoded
 * `agent_label_id=None` — so an agent-labeled comment was unreachable over the `apiContext` REST
 * calls the rest of this file uses. It is reachable now: sending `X-Agent-Label` on the seeding
 * request would produce one. The fixture deliberately does not, because adding a chip to the
 * comment-thread shot would repaint a committed baseline for a treatment this spec is not about.
 * That treatment stays proven by the existing component test (`RecordDetailView.test.tsx`,
 * "lists comments chronologically with author, agent label, and an edited marker"), which
 * exercises the identical `CommentItem` code path against mocked data.
 */
async function ensureDetailFixtureSeeded(): Promise<void> {
  const existing = await apiContext.get(`/api/v1/object-types/${DETAIL_TYPE_KEY}`);
  if (existing.ok()) {
    return;
  }

  await ensureSeeded(
    {
      key: DETAIL_TARGET_TYPE_KEY,
      name: "Visual Detail Target",
      name_plural: "Visual Detail Targets",
      description: "The link-target fixture type for the record-detail relation shot.",
      key_prefix: "VDT",
      // Passed **explicitly** rather than left to the creation default, so the shot proves a
      // *chosen* display field and not merely the derived one. This type has exactly one
      // field, so both rules agree on it and no second field is added: the override where the
      // two rules disagree is asserted in a backend test, where it costs no pixels (a new
      // visual fixture is never free). Only meaningful because `CreateObjectTypeBody` declares
      // the key: without it, Pydantic's `extra="ignore"` would discard it silently.
      display_field_key: "name",
      fields: [
        {
          key: "name",
          name: "Name",
          type: "short_text",
          description: "Short display name.",
          required: true,
        },
      ],
    },
    [{ name: "Atlas platform team" }],
  );

  // Uploaded BEFORE the rows are created, so the id can ride in VDET-002's create payload
  // rather than arriving by a later PATCH: a PATCH would append a whole write row to that
  // record's audit trail, and `owner_principal` below is valued the same way for the same
  // reason.
  const attachmentUpload = await apiContext.post("/api/v1/attachments", {
    multipart: {
      file: {
        name: DETAIL_ATTACHMENT_FILENAME,
        mimeType: "text/plain",
        buffer: Buffer.from(DETAIL_ATTACHMENT_BODY, "utf-8"),
      },
    },
  });
  expect(attachmentUpload.ok(), await attachmentUpload.text()).toBeTruthy();
  const attachmentId = ((await attachmentUpload.json()) as { id: string }).id;

  await ensureSeeded(
    {
      key: DETAIL_TYPE_KEY,
      name: "Visual Detail",
      name_plural: "Visual Details",
      description: "The record-detail visual-baseline fixture type.",
      key_prefix: "VDET",
      fields: [
        {
          key: "name",
          name: "Name",
          type: "short_text",
          description: "Short display name.",
          required: true,
        },
        {
          key: "status",
          name: "Status",
          type: "single_select",
          description: "Where the work stands.",
          config: {
            options: [
              { value: "on_track", label: "On track", description: "Progressing." },
              { value: "at_risk", label: "At risk", description: "Needs attention." },
            ],
          },
        },
        {
          key: "owner",
          name: "Owner",
          type: "relation",
          description: "Who owns this record.",
          config: {
            target_type_key: DETAIL_TARGET_TYPE_KEY,
            cardinality: "one",
            inverse_field_key: "owned_details",
          },
        },
        // The suite's only `user_ref` fixture, so the field-list shot can carry a rendered
        // display name rather than a raw UUID.
        //
        // Seeded HERE, in the type payload, and valued in the row below rather than through a
        // later POST-then-PATCH: the run's data directory is a fresh `mkdtempSync` per
        // invocation (`e2e/constants.ts::prepareCredential`), so there is no cross-run
        // database to retrofit, and `ensureSeeded`'s early return only ever skips a *retried
        // worker within one run*, by which point this fixture is already fully seeded. Doing
        // it the other way would cost an audit event, which would repaint the audit-timeline
        // baseline for no gain.
        {
          key: "owner_principal",
          name: "Owner (person)",
          type: "user_ref",
          description: "Which person is accountable, as opposed to which team owns it.",
        },
        // The suite's only `attachment` fixture. On the existing type rather than a new one,
        // because a new visual fixture type is never free.
        {
          key: "files",
          name: "Files",
          type: "attachment",
          description: "Supporting documents for this record.",
        },
      ],
    },
    // `VISUAL_PRINCIPAL_ID`'s display name is the fixed literal "E2E Admin"
    // (`constants.ts`), so the cell renders deterministic text and needs no mask.
    [
      // VDET-001, the field-list / comments / audit-timeline / linked-records fixture. It gets
      // NO `files` value on purpose: an extra key here would move the audit timeline's
      // "Created 3 fields" and repaint a second baseline (see DETAIL_ATTACHMENT_RECORD_KEY).
      // Its Files row renders the empty state, which is the other half of the widget.
      {
        name: "Runbook rewrite",
        status: "on_track",
        owner_principal: VISUAL_PRINCIPAL_ID,
      },
      // VDET-002, the attachment shot's own record: one resolvable id and one that is not.
      {
        name: "Vendor contract",
        files: [attachmentId, DETAIL_MISSING_ATTACHMENT_ID],
      },
    ],
  );

  const link = await apiContext.post(
    `/api/v1/records/${DETAIL_RECORD_KEY}/links/owner`,
    { data: { to_records: [DETAIL_TARGET_RECORD_KEY] } },
  );
  expect(link.ok(), await link.text()).toBeTruthy();

  const commentCreated = await apiContext.post(`/api/v1/records/${DETAIL_RECORD_KEY}/comments`, {
    data: { body: "Kickoff notes: scope agreed with stakeholders on the call." },
  });
  expect(commentCreated.ok(), await commentCreated.text()).toBeTruthy();
  const comment = (await commentCreated.json()) as { id: string };

  // A second body sets `edited: true` (FR-C6), the "edited" quiet-button treatment's fixture.
  //
  // That second body is MARKDOWN, so the comment shot carries a rendered heading and a
  // rendered list, and the claim that comment markdown inherits DD-41 has a pixel record. The
  // FIRST body is left as prose on purpose — it is the prior version behind the "edited"
  // control, so the pair also shows one comment's history rendering through the same component.
  //
  // It is preferred to the alternatives because it adds no object type (a ninth type once
  // wrapped the shell header and repainted six unrelated baselines) and no extra comment (which
  // would have added an audit event and repainted the timeline shot too).
  const commentEdited = await apiContext.patch(`/api/v1/comments/${comment.id}`, {
    data: { body: DETAIL_MARKDOWN_COMMENT },
  });
  expect(commentEdited.ok(), await commentEdited.text()).toBeTruthy();

  // A field update at the record's initial version (1) produces the audit timeline's one
  // field-level "update" row, with a real old value and new value for the mono-face shot.
  const statusUpdate = await apiContext.patch(`/api/v1/records/${DETAIL_RECORD_KEY}`, {
    data: { values: { status: "at_risk" }, expected_version: 1 },
  });
  expect(statusUpdate.ok(), await statusUpdate.text()).toBeTruthy();
}

/** Idempotent the same way `ensureDetailFixtureSeeded` is: gated on the type's own existence,
 * since `ensureSeeded`'s per-type gate already covers the type-and-rows step here (there are
 * no further one-time side effects to guard separately, unlike the record-detail fixture). */
async function ensureSchemaFixtureSeeded(): Promise<void> {
  await ensureSeeded(
    {
      key: SCHEMA_TYPE_KEY,
      name: "Visual Schema",
      name_plural: "Visual Schemas",
      description: "The schema editor's visual-baseline fixture type, as seeded.",
      key_prefix: "VSCH",
      fields: [
        {
          key: "name",
          name: "Name",
          type: "short_text",
          description: "Short display name.",
          required: true,
        },
        {
          key: "status",
          name: "Status",
          type: "single_select",
          description: "Where the work stands.",
          config: {
            options: [
              { value: "on_track", label: "On track", description: "Progressing." },
              { value: "at_risk", label: "At risk", description: "Needs attention." },
            ],
          },
        },
        {
          key: "legacy_note",
          name: "Legacy note",
          type: "short_text",
          description: "A retired free-text note, kept only for the blast-radius shot's delete.",
        },
      ],
    },
    [
      { name: "Warehouse rollout", status: "on_track", legacy_note: "Retire after Q3." },
      { name: "Vendor onboarding refresh", status: "at_risk", legacy_note: "Awaiting sign-off." },
    ],
  );
}

/** Idempotent in two steps: `ensureSeeded` gates the type-and-row step on the type's own
 * existence; the proposal step below is gated separately (by `reason`) because it must
 * survive even on a retry that lands after the type already existed from a prior run. */
async function ensureSettingsFixtureSeeded(): Promise<void> {
  await ensureSeeded(
    {
      key: SETTINGS_TYPE_KEY,
      name: "Visual Settings",
      name_plural: "Visual Settings Records",
      description: "The pending-proposal visual-baseline fixture type.",
      key_prefix: "VSET",
      fields: [
        {
          key: "name",
          name: "Name",
          type: "short_text",
          description: "Short display name.",
          required: true,
        },
        {
          key: "legacy_note",
          name: "Legacy note",
          type: "short_text",
          description: "A retired free-text note, kept only for the pending-proposal shot.",
        },
      ],
    },
    [{ name: "Retention policy review", legacy_note: "Retire after the audit." }],
  );

  const pending = await apiContext.get("/api/v1/schema-proposals?status=pending");
  expect(pending.ok(), await pending.text()).toBeTruthy();
  const { proposals } = (await pending.json()) as { proposals: { reason: string | null }[] };
  if (proposals.some((proposal) => proposal.reason === SETTINGS_PROPOSAL_REASON)) {
    return;
  }
  // Left pending forever — never approved or rejected — so the panel always has one row.
  const proposed = await apiContext.post("/api/v1/schema-proposals", {
    data: {
      change_type: "delete_field",
      object_type: SETTINGS_TYPE_KEY,
      field_key: "legacy_note",
      reason: SETTINGS_PROPOSAL_REASON,
    },
  });
  expect(proposed.ok(), await proposed.text()).toBeTruthy();
}

/** Idempotent the same way `ensureDetailFixtureSeeded` is: gated on the primary type's own
 * existence, since the one-time side effect below (the comment) must not repeat on a retry. */
async function ensureSearchFixtureSeeded(): Promise<void> {
  const existing = await apiContext.get(`/api/v1/object-types/${SEARCH_TYPE_KEY}`);
  if (existing.ok()) {
    return;
  }

  await ensureSeeded(
    {
      key: SEARCH_TYPE_KEY,
      name: "Visual Search",
      name_plural: "Visual Searches",
      description: "The primary search visual-baseline fixture type.",
      key_prefix: "VSRCH",
      fields: [
        {
          key: "name",
          name: "Name",
          type: "short_text",
          description: "Short display name.",
          required: true,
        },
        {
          key: "summary",
          name: "Summary",
          type: "long_text",
          description:
            "A longer narrative field, embedded for keyword and semantic search by default.",
        },
      ],
    },
    [
      {
        name: "Photon ledger rollout",
        summary: "The photon ledger integration needs a runbook before the audit window closes.",
      },
    ],
  );

  await ensureSeeded(
    {
      key: SEARCH_SECONDARY_TYPE_KEY,
      name: "Visual Search Secondary",
      name_plural: "Visual Search Secondaries",
      description:
        "The second search fixture type, so the grouped-results shot has two sections.",
      key_prefix: "VSRCH2",
      fields: [
        {
          key: "name",
          name: "Name",
          type: "short_text",
          description: "Short display name.",
          required: true,
        },
        {
          key: "summary",
          name: "Summary",
          type: "long_text",
          description:
            "A longer narrative field, embedded for keyword and semantic search by default.",
        },
      ],
    },
    [
      {
        name: "Photon ledger pilot",
        summary: "Coordinating the photon ledger pilot across three regions this quarter.",
      },
    ],
  );

  // A second keyword-matched location (a comment) on the primary record, so it has a real,
  // non-zero `other_matches` count for the Badge shot below.
  const commentCreated = await apiContext.post(`/api/v1/records/${SEARCH_RECORD_KEY}/comments`, {
    data: { body: "Ops note: the photon ledger rollout still needs sign-off from finance." },
  });
  expect(commentCreated.ok(), await commentCreated.text()).toBeTruthy();
}

/** Idempotent the same way `ensureDetailFixtureSeeded` is: gated on the type's own existence,
 * since the two field-update side effects below must not repeat on a retry — the second would
 * still succeed against a higher `expected_version`, but would leave a *third* revertible row
 * with a different old->new pair than this fixture's baseline expects. */
async function ensureAuditBrowserFixtureSeeded(): Promise<void> {
  const existing = await apiContext.get(`/api/v1/object-types/${AUDIT_TYPE_KEY}`);
  if (existing.ok()) {
    return;
  }

  await ensureSeeded(
    {
      key: AUDIT_TYPE_KEY,
      name: "Visual Audit",
      name_plural: "Visual Audits",
      description: "The activity screen's visual-baseline fixture type.",
      key_prefix: "VAUD",
      fields: [
        {
          key: "name",
          name: "Name",
          type: "short_text",
          description: "Short display name.",
          required: true,
        },
        {
          key: "status",
          name: "Status",
          type: "single_select",
          description: "Where the work stands.",
          config: {
            options: [
              { value: "on_track", label: "On track", description: "Progressing." },
              { value: "at_risk", label: "At risk", description: "Needs attention." },
            ],
          },
        },
      ],
    },
    [{ name: "Ledger reconciliation", status: "on_track" }],
  );

  // Two field updates at ascending expected_versions produce two revertible "update" audit rows
  // with real, fixed old->new values — the events table's mono-face shot.
  const firstUpdate = await apiContext.patch(`/api/v1/records/${AUDIT_RECORD_KEY}`, {
    data: { values: { status: "at_risk" }, expected_version: 1 },
  });
  expect(firstUpdate.ok(), await firstUpdate.text()).toBeTruthy();

  const secondUpdate = await apiContext.patch(`/api/v1/records/${AUDIT_RECORD_KEY}`, {
    data: { values: { status: "on_track" }, expected_version: 2 },
  });
  expect(secondUpdate.ok(), await secondUpdate.text()).toBeTruthy();
}

/** Idempotent the same way `ensureSchemaFixtureSeeded` is: gated on `ensureSeeded`'s own
 * per-type existence check. No rows are seeded here — the CSV wizard's own upload/dry-run/commit
 * flow creates every row this fixture type ever gets, once per test that runs it. */
async function ensureImportFixtureSeeded(): Promise<void> {
  await ensureSeeded(
    {
      key: IMPORT_TYPE_KEY,
      name: "Visual Import",
      name_plural: "Visual Imports",
      description: "The CSV import wizard's visual-baseline fixture type.",
      key_prefix: "VIMP",
      fields: [
        {
          key: "name",
          name: "Name",
          type: "short_text",
          description: "Short display name.",
          required: true,
        },
        {
          key: "status",
          name: "Status",
          type: "single_select",
          description: "Where the work stands.",
          config: {
            options: [
              { value: "on_track", label: "On track", description: "Progressing." },
              { value: "at_risk", label: "At risk", description: "Needs attention." },
            ],
          },
        },
      ],
    },
    [],
  );
}

/** Search shots poll `GET /api/v1/admin/search-index` until `pending_jobs == 0` first: every
 * fixture type in this shared visual database (record detail's comments, the
 * search fixtures' `long_text` fields) feeds the same embedding queue, so a search shot taken
 * before the worker drains it would render a live, run-dependent count in the index-lag
 * `Alert`. Same shape as `e2e/comment-search-end-to-end.spec.ts`'s own poll. */
async function waitForSearchIndexDrained(): Promise<void> {
  await expect
    .poll(
      async () => {
        const status = await apiContext.get("/api/v1/admin/search-index");
        expect(status.ok(), await status.text()).toBeTruthy();
        const body = (await status.json()) as { pending_jobs: number };
        return body.pending_jobs;
      },
      { timeout: 30_000 },
    )
    .toBe(0);
}

test.beforeAll(async () => {
  apiContext = await apiRequestModule.newContext({
    baseURL: VISUAL_BASE_URL,
    extraHTTPHeaders: VISUAL_AUTH_HEADER,
  });
  await ensureSeeded(
    {
      key: OBJECT_TYPE_KEY,
      name: "Visual Initiative",
      name_plural: "Visual Initiatives",
      description: "The table's visual-baseline fixture type, seeded fresh each run.",
      key_prefix: "VIS",
      fields: [
        {
          key: "name",
          name: "Name",
          type: "short_text",
          description: "Short display name.",
          required: true,
        },
        {
          key: "status",
          name: "Status",
          type: "single_select",
          description: "Where the initiative stands.",
          config: {
            options: [
              { value: "on_track", label: "On track", description: "Progressing." },
              { value: "at_risk", label: "At risk", description: "Needs attention." },
            ],
          },
        },
        {
          key: "budget",
          name: "Budget",
          type: "integer",
          description: "Approved budget in whole dollars.",
        },
      ],
    },
    [
      { name: "Meridian ERP cutover", status: "at_risk", budget: 250000 },
      { name: "Returns handling backlog", status: "at_risk", budget: 40000 },
      { name: "Vendor consolidation, wave 2", status: "on_track", budget: 120000 },
      { name: "Sales pricing playbook", status: "on_track", budget: 15000 },
      { name: "Month-end close automation", status: "on_track", budget: 98000 },
    ],
  );
  await ensureSeeded(
    {
      key: CONFLICT_TYPE_KEY,
      name: "Visual Conflict",
      name_plural: "Visual Conflicts",
      description: "The merge-conflict-dialog fixture type; its one record gets raced.",
      key_prefix: "VISC",
      fields: [
        {
          key: "name",
          name: "Name",
          type: "short_text",
          description: "Short display name.",
          required: true,
        },
      ],
    },
    [{ name: "Vendor consolidation, wave 2" }],
  );
  await ensureSeeded(
    {
      key: WIDE_TYPE_KEY,
      name: "Visual Wide",
      name_plural: "Visual Wides",
      description: "The wide-table fixture type: twelve fields and one very long value.",
      key_prefix: "VWID",
      fields: [
        {
          key: "name",
          name: "Name",
          type: "short_text",
          description: "Short display name.",
          required: true,
        },
        {
          key: "status",
          name: "Status",
          type: "single_select",
          description: "Where the initiative stands.",
          config: {
            options: [
              { value: "on_track", label: "On track", description: "Progressing." },
              { value: "at_risk", label: "At risk", description: "Needs attention." },
            ],
          },
        },
        {
          key: "notes",
          name: "Notes",
          type: "long_text",
          description: "Free-form notes; the long value the display rule is about lives here.",
        },
        {
          key: "budget",
          name: "Budget",
          type: "integer",
          description: "Approved budget in whole dollars.",
        },
        {
          key: "owner",
          name: "Owner",
          type: "short_text",
          description: "Who owns this initiative.",
        },
        {
          key: "region",
          name: "Region",
          type: "short_text",
          description: "Operating region.",
        },
        {
          key: "phase",
          name: "Phase",
          type: "short_text",
          description: "Delivery phase.",
        },
        {
          key: "risk",
          name: "Risk",
          type: "short_text",
          description: "Top standing risk.",
        },
        {
          key: "sponsor",
          name: "Sponsor",
          type: "short_text",
          description: "Executive sponsor.",
        },
        {
          key: "vendor",
          name: "Vendor",
          type: "short_text",
          description: "Primary vendor.",
        },
        {
          key: "milestone",
          name: "Milestone",
          type: "short_text",
          description: "Next milestone.",
        },
        {
          key: "reference",
          name: "Reference",
          type: "short_text",
          description: "External reference code.",
        },
        {
          key: "workstream",
          name: "Workstream",
          type: "short_text",
          description: "Parent workstream.",
        },
      ],
    },
    [
      {
        name: "Meridian ERP cutover",
        status: "at_risk",
        notes: WIDE_LONG_NOTES,
        budget: 250000,
        owner: "A. Okafor",
        region: "EMEA",
        phase: "Build",
        risk: "Vendor slip",
        sponsor: "R. Baptiste",
        vendor: "Northwind",
        milestone: "Cutover rehearsal",
        reference: "REF-4417",
        workstream: "Core finance",
      },
      {
        name: "Returns handling backlog",
        status: "at_risk",
        notes: WIDE_SHORT_NOTES,
        budget: 40000,
        owner: "J. Prentice",
        region: "AMER",
        phase: "Discovery",
        risk: "Scope",
        sponsor: "R. Baptiste",
        vendor: "Alderman",
        milestone: "Scope sign-off",
        reference: "REF-4418",
        workstream: "Supply chain",
      },
      {
        name: "Vendor consolidation, wave 2",
        status: "on_track",
        notes: WIDE_SHORT_NOTES,
        budget: 120000,
        owner: "M. Adeyemi",
        region: "APAC",
        phase: "Build",
        risk: "None standing",
        sponsor: "T. Vance",
        vendor: "Northwind",
        milestone: "Wave 2 contract",
        reference: "REF-4419",
        workstream: "Procurement",
      },
    ],
  );
  await ensureWideSavedViewSeeded();
  await ensureDetailFixtureSeeded();
  await ensureSchemaFixtureSeeded();
  await ensureSettingsFixtureSeeded();
  await ensureSearchFixtureSeeded();
  await ensureAuditBrowserFixtureSeeded();
  await ensureImportFixtureSeeded();
});

test.afterAll(async () => {
  await apiContext.dispose();
});

test.describe("design-system visuals", () => {
  test("login page", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByTestId("login-submit")).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    await expect(page).toHaveScreenshot("login.png");
  });

  /**
   * The shell has no `<header>`: it is a 224px sidebar at or above 960px and a top bar with a
   * `Menu` below it (docs/DESIGN.md 8.1, 9).
   *
   * Two shots, because the shell now has two forms and the narrow one is where DD-42, applied to
   * viewports, is actually at risk. Both are ELEMENT shots: framing the whole page would embed every
   * screen's content in a picture of the chrome.
   */
  test("app shell sidebar", async ({ page }) => {
    await signInAsE2eAdmin(page);
    await page.evaluate(() => document.fonts.ready);
    await expect(page.getByTestId("sidebar")).toHaveScreenshot("shell-sidebar.png");
  });

  test("app shell top bar below the breakpoint, menu open", async ({ page }) => {
    await signInAsE2eAdmin(page);
    await page.setViewportSize({ width: 800, height: 800 });
    await page.getByRole("button", { name: "Menu" }).click();
    await expect(page.getByTestId("shell-menu")).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    await expect(page.getByTestId("shell-top-bar")).toHaveScreenshot("shell-top-bar.png");
  });

  test("table view: default, grouped, and a selected row", async ({ page }) => {
    await signInAsE2eAdmin(page);
    await page.goto(`/${OBJECT_TYPE_KEY}`);
    await expect(page.getByTestId("row-VIS-001")).toBeVisible();
    // The default query carries no sort, and SQLite's unordered row order is not stable across
    // runs — pin it through the UI itself (one header click: Name ascending).
    await page.getByRole("button", { name: /^Name/ }).click();
    await expect(page.getByTestId("records-table")).toContainText("Meridian ERP cutover");
    await page.evaluate(() => document.fonts.ready);
    // Scoped to an explicit name, not `main section`. A `main section`
    // locator that matches two elements makes `toHaveScreenshot` pass VACUOUSLY against any
    // existing baseline — measured on @playwright/test 1.62.1 — so the assertion would cease
    // to assert the moment a route grew a second section, while still reporting green. The
    // `toHaveCount(1)` beside each shot is what makes the re-scoping provable.
    const section = page.getByRole("region", { name: "Records" });
    await expect(section).toHaveCount(1);
    await expect(section).toHaveScreenshot("table-default.png");

    // `Group by` is behind the `Group` chip and keeps its name. The popover is dismissed before the
    // shot: it is a transient anchored surface, and a baseline with one open in it would be a
    // baseline of a hover state.
    await page.getByTestId("group-chip").getByRole("button").first().click();
    await page.getByLabel("Group by").selectOption("status");
    await page.keyboard.press("Escape");
    await expect(page.getByLabel("Group by")).toHaveCount(0);
    await expect(page.getByTestId("group-header").first()).toBeVisible();
    // **Blur before the shot, and this line is load-bearing rather than defensive.** `Popover`
    // deliberately returns focus to its trigger on close (asserted at the primitive), and the
    // `Escape` above is a keyboard interaction — so the chip is left `:focus-visible` and the ring
    // paints into the baseline. Measured here rather than assumed: at this point
    // `document.activeElement` is the `Group by Status` trigger with
    // `matches(":focus-visible") === true`, while the other three table shots hold focus on a mouse-clicked control and are
    // `false`, which is why only this one blurs.
    //
    // A baseline should be a picture of the PAGE, not of the interaction that reached it. Left
    // in, the ring couples this table baseline to `docs/DESIGN.md` 3's focus styling: a later
    // change to the ring's colour, width or offset would repaint a table shot for a reason that
    // has nothing to do with the table, and the next person would have to work out why.
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
    await expect(section).toHaveScreenshot("table-grouped.png");

    await page.getByLabel("Select row VIS-002").check();
    await expect(page.getByTestId("bulk-toolbar")).toBeVisible();
    await expect(section).toHaveScreenshot("table-selected.png");
  });

  test("table view: merge-conflict dialog on a real 409", async ({ page }) => {
    await signInAsE2eAdmin(page);
    await page.goto(`/${CONFLICT_TYPE_KEY}`);
    await expect(page.getByTestId("row-VISC-001")).toBeVisible();

    // Race the row from the API so the browser's copy goes stale: the next inline edit's
    // expected_version conflicts and FR-U10's dialog opens — the real path, not a mock. The
    // dialog must render deterministic values, so the racing write pins both sides to fixed
    // strings regardless of how many retries came before it (your value vs. current value).
    const current = await apiContext.get(`/api/v1/records/VISC-001`);
    expect(current.ok()).toBeTruthy();
    const { version } = (await current.json()) as { version: number };
    // Same field on both sides, so `conflicting_fields` is guaranteed a row for the dialog.
    const racingPatch = await apiContext.patch(`/api/v1/records/VISC-001`, {
      data: { values: { name: "Vendor consolidation, wave 2 (renamed)" }, expected_version: version },
    });
    expect(racingPatch.ok(), await racingPatch.text()).toBeTruthy();

    await page.getByRole("button", { name: "Edit Name for VISC-001" }).click();
    await page.getByLabel("Name value for VISC-001").fill("Vendor consolidation, wave 3");
    await page.keyboard.press("Tab");

    const dialog = page.getByRole("dialog", { name: "Resolve version conflict" });
    await expect(dialog).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    // The intro sentence renders the record's live version number, which increments again on
    // every retry's racing patch (including the config's worker-crash retry) — masked so a
    // retry cannot false-fail; the conflict table and actions stay pixel-checked.
    await expect(dialog).toHaveScreenshot("merge-conflict-dialog.png", {
      mask: [dialog.locator("p").first()],
    });
  });

  /**
   * The create-record dialog (DD-44).
   *
   * **On an existing fixture type, opened and never submitted**, and both halves of that are
   * deliberate. A twelfth object type on this database would repaint `shell-sidebar.png` and
   * `shell-top-bar.png`, which frame the nav and its per-type counts — the trap AGENTS.md records
   * from the run where a ninth type repainted six unrelated baselines. And submitting would move
   * `vis_initiative`'s `record_count`, which this page's own title line renders and
   * `table-default.png` therefore contains.
   */
  test("table view: the create dialog", async ({ page }) => {
    await signInAsE2eAdmin(page);
    await page.goto(`/${OBJECT_TYPE_KEY}`);
    await expect(page.getByTestId("row-VIS-001")).toBeVisible();

    await page.getByTestId("new-record").click();
    const dialog = page.getByTestId("new-record-dialog");
    await expect(dialog).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    // Blurred before the shot for the same reason `table-grouped.png` is: the first field carries
    // `autoFocus`, and a focus ring in the baseline would couple this picture to docs/DESIGN.md 3's
    // focus styling, so a later change to the ring's colour or offset would repaint a dialog shot
    // for a reason that has nothing to do with the dialog.
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
    await expect(dialog).toHaveScreenshot("new-record-dialog.png");
  });
});

test.describe("wide table geometry", () => {
  /**
   * jsdom implements no layout — every `getBoundingClientRect` there is zeroes — so a long
   * value's column width and where the table starts cannot be proven by a component test at
   * all, and neither can the one-row toolbar or the chip and pill heights. These are geometry
   * assertions on real boxes in a real browser, at the pinned 1280x800 viewport, against the
   * twelve-field `vis_wide` fixture. Every title carries the literal string `vis_wide` so
   * `-g "vis_wide"` selects exactly them.
   *
   * **There is no scenario bounding the column picker's height.** One asserting that
   * `column-picker`'s `boundingBox().height` was `<= 160px` on this fixture would measure a
   * locator timeout rather than a footprint: `docs/DESIGN.md` 8.2 puts the picker behind a
   * popover, so the element is unmounted until its chip is clicked, and a popover has no
   * footprint on the page to bound. The reasoning, and what now holds the bound such a scenario
   * protected, is in `src/table-view/ColumnPicker.tsx`'s comment on the picker's height.
   */

  /** The `td` in `column` for the row keyed `recordKey`, addressed by DOM position: the table has
   * no per-cell test id, and the selection column is at index 0, so the cell
   * index is the field's position in the header row plus one. Resolved from the header rather
   * than hardcoded, so a fixture field reorder cannot make this silently address the wrong
   * column. */
  async function cellBox(page: import("@playwright/test").Page, recordKey: string, header: string) {
    const table = page.getByTestId("records-table");
    const headers = table.locator("thead th");
    const count = await headers.count();
    let index = -1;
    for (let i = 0; i < count; i += 1) {
      if (((await headers.nth(i).textContent()) ?? "").trim().toUpperCase().startsWith(header.toUpperCase())) {
        index = i;
        break;
      }
    }
    expect(index, `no <th> starting with "${header}"`).toBeGreaterThanOrEqual(0);
    const th = await headers.nth(index).boundingBox();
    const td = await page.getByTestId(`row-${recordKey}`).locator("td").nth(index).boundingBox();
    expect(th).not.toBeNull();
    expect(td).not.toBeNull();
    return { th: th!, td: td! };
  }

  test("vis_wide: a long value cannot widen its column", async ({ page }) => {
    await signInAsE2eAdmin(page);
    await page.goto(`/${WIDE_TYPE_KEY}`);
    await expect(page.getByTestId(`row-${WIDE_LONG_RECORD_KEY}`)).toBeVisible();
    // Same reason the table shots above click a Name sort: the default query carries no ORDER BY.
    await page.getByRole("button", { name: /^Name/ }).click();
    await expect(page.getByTestId("records-table")).toContainText("Meridian ERP cutover");

    const long = await cellBox(page, WIDE_LONG_RECORD_KEY, "Notes");
    const short = await cellBox(page, WIDE_SHORT_RECORD_KEY, "Notes");
    const peer = await cellBox(page, WIDE_LONG_RECORD_KEY, "Status");

    // The cross-COLUMN comparison is the one that proves the defect, and the obvious assertion (a
    // `td` against its own `th` and against the same column's short-valued `td`) does not: cells in
    // one column always share that column's rendered width, in fixed AND auto layout alike, so
    // those two equalities hold even while the long value is dragging the whole column wide.
    // Measured on pre-fix source: they passed. `Notes` and `Status` are both declared `size: 180`
    // (`columns.tsx`), so under a real width constraint they render identically wide; under auto
    // layout the long value widens `Notes` alone. Measured pre-fix: Notes 2044px against Status
    // 180px.
    expect(Math.abs(long.td.width - peer.td.width)).toBeLessThanOrEqual(1);

    // Kept as corroboration, not as the proof: they confirm the `th` width TanStack sets from
    // `header.getSize()` is the width the body cells actually got.
    expect(Math.abs(long.td.width - long.th.width)).toBeLessThanOrEqual(1);
    expect(Math.abs(long.td.width - short.td.width)).toBeLessThanOrEqual(1);

    await page.evaluate(() => document.fonts.ready);
    // Every value is this fixture's own fixed text — no UUID or live timestamp surface — so no
    // mask is needed. The table overflows 1280px into `tableWrapClass`'s scroller, so the shot
    // captures the clipped viewport width, which is the point.
    const section = page.getByRole("region", { name: "Records" });
    await expect(section).toHaveCount(1);
    await expect(section).toHaveScreenshot("table-wide.png");
  });

  test("vis_wide: the table starts in the lower half of the window", async ({ page }) => {
    await signInAsE2eAdmin(page);
    await page.goto(`/${WIDE_TYPE_KEY}`);
    // The FIRST data row by DOM position, not a named record: the default query carries no
    // ORDER BY and SQLite's row order is not stable run to run (this spec's header comment), so
    // anchoring on a record key measures whichever row it landed in. Caught by running it —
    // `row-VWID-001` reported 518px in a full-suite run and 486px alone, a one-row difference.
    await expect(page.getByTestId(`row-${WIDE_LONG_RECORD_KEY}`)).toBeVisible();
    const firstRow = page.getByTestId("records-table").locator("tbody tr").first();
    await expect(firstRow).toBeVisible();

    // This is a chosen target, not a rediscovered symptom: "above the fold" was observed on the
    // owner's own shorter viewport with a twelve-column type, and a three-field type would pass
    // a naive in-viewport assertion at 1280x800 today.
    //
    // **The bound is 250px.** The history of this bound, kept because the reasoning that set
    // each number is what says whether the next one is reachable (AGENTS.md, Traps: a deletion
    // must not take a workaround's only explanation with it):
    //
    //   - **971px**, pre-fix on this fixture: the table did not merely start low, it began
    //     entirely below the 800px fold.
    //   - **486px**, after the first fix (a bounded ColumnPicker plus layout wrapper divs). The
    //     target had been 400px; the bound was set to **500px** after measuring, the product
    //     owner's decision, because the remaining 86px was not available then: above the table
    //     sat the app header (93px), the h1 and description (70px), the control rows and the
    //     filter builder, and both ways to reach 400 were ruled out — a disclosure on
    //     ColumnPicker (it would break `table-end-to-end.spec.ts`'s real-browser uncheck), and
    //     dropping the page heading, a copy change that was out of scope.
    //   - **454px**, measured before the toolbar was rebuilt: still under the standing 500, which
    //     is why that bound had to be retightened rather than left to pass unchanged
    //     (`docs/changes/README.md`: retire superseded assertions).
    //
    // What makes 250 reachable is the removal of the constraint that ruled 400 out:
    // `docs/DESIGN.md` 8.2 makes the column picker a POPOVER, so the real-browser uncheck
    // happens inside it and the disclosure objection no longer applies. The description sits
    // behind an About disclosure and the filter builder is chips, so neither is in the flow
    // either. The arithmetic is measured parts rather than an invented stack: `sectionTop` 20 +
    // h1 25 + three 12px `space-y-3` gaps + one 34px toolbar row + the 34px table header lands
    // near 150, leaving room for the title line's count and the About control. 250 is an
    // assumption that was never put to the maintainer, and is a number to tighten or loosen on
    // sight of the built screen.
    //
    // Measured against the tree before the toolbar was rebuilt: **425.69**, so this failed
    // honestly.
    //
    // NOT 454, a number this comment once carried in error. 454 was measured at 1280x900 against
    // a separate twelve-field probe type; this scenario pins 1280x800 against `vis_wide`, whose
    // description is shorter. Both miss 250 and nothing about the argument changes, but 454 is
    // not a number this assertion has ever read, and this comment is the only surviving
    // explanation of the bound.
    const box = await firstRow.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.y).toBeLessThanOrEqual(250);
  });

  /**
   * **The toolbar is one row at 1280 and at 800.**
   *
   * `docs/DESIGN.md` 8.2 says one row; an earlier toolbar measured eleven controls wrapped to
   * two rows, 72px tall, at 1280 — which is most of what the table-start bound above is
   * fighting.
   *
   * Asserted at 800 as well as at 1280, not because 800 is a second guess but because
   * `docs/DESIGN.md` 9 puts the wrap breakpoint at **640**: 800 is still one row, and an assertion
   * that permitted wrapping there would contradict that rule while going un-failable at the one
   * narrow viewport `shell.spec.ts` already exercises. Below 640 it wraps, and nothing may go
   * missing when it does (DD-42 applied to viewports) — that is a separate assertion, not this one.
   *
   * **Measured from `boundingBox()`, never from a class name.** `flex-wrap` is on the toolbar
   * whether or not it has wrapped, so reading the class says nothing about the rendered result;
   * only the children's `y` does. Same reason the table-start bound above is a geometry
   * assertion: layout is never proven in jsdom (AGENTS.md, Traps).
   *
   * **The anchor, and why it moved.** This assertion first addressed the toolbar as the PARENT of
   * `Import CSV`, which had no test id of its own — and that anchor carried a vacuity risk: wrap
   * `Import CSV` in a grouping `<div>` and the "parent" becomes the wrapper, whose single child
   * shares one `y` with itself. The toolbar now carries `data-testid="table-toolbar"` and is
   * addressed by it, so the element under assertion is named rather than inferred. `Import CSV` is
   * still a direct child of it, deliberately (`TableView.tsx`), so the older anchor would still
   * resolve to the same element — the test id is what stops that from being a coincidence a later
   * edit can revoke in silence.
   *
   * The assertion is **not** vacuous under the new toolbar, checked rather than assumed: the
   * child count is asserted to be more than one, every control in it is a direct child (no
   * cluster wrapper that could wrap inside a box whose `y` never moves), and the failure was
   * measured by setting the toolbar to `flex-col`.
   */
  test("vis_wide: the toolbar is one row at 1280 and at 800", async ({ page }) => {
    await signInAsE2eAdmin(page);
    await page.goto(`/${WIDE_TYPE_KEY}`);
    await expect(page.getByTestId(`row-${WIDE_LONG_RECORD_KEY}`)).toBeVisible();

    const toolbar = page.getByTestId("table-toolbar");
    const children = toolbar.locator("> *");

    // The two anchors are the SAME element, asserted rather than assumed. This is the vacuity
    // risk described above: wrap `Import CSV` in a grouping `<div>` and the
    // old anchor (its parent) silently becomes that wrapper, whose only child shares one `y`
    // with itself. Stating it here means a later edit that adds the wrapper fails this line
    // instead of passing the scenario.
    const importParent = await page
      .getByRole("link", { name: "Import CSV" })
      .evaluate((el) => el.parentElement?.getAttribute("data-testid") ?? null);
    expect(importParent, "Import CSV is no longer a direct child of the toolbar").toBe(
      "table-toolbar",
    );

    for (const width of [1280, 800]) {
      await page.setViewportSize({ width, height: 800 });

      /**
       * **Wait for the shell to finish swapping before measuring anything.**
       *
       * These two widths sit either side of the 960px shell breakpoint, so stepping from 1280 to
       * 800 unmounts a 224px sidebar. That swap is a React render driven by a `matchMedia`
       * subscription, so it lands a tick after the resize — and the row assertion below cannot
       * wait for it, because the row is visible in *both* shells. Measured mid-swap, the toolbar
       * reads 528px (`800 − 48` of `main` padding `− 224` of a sidebar that is on its way out)
       * against 716.8px of children, and this scenario reports a wrap that the settled page does
       * not have.
       *
       * The race was there long before it was found: nothing here ever waited for the shell,
       * and it had been winning on timing. Adding a button to the title line and an extra
       * subscription to this screen was enough to lose it — reported as a toolbar wrap in a row
       * that button does not touch. `shell.spec.ts` already waits exactly this way.
       */
      if (width >= 960) {
        await expect(page.getByTestId("sidebar")).toBeVisible();
      } else {
        await expect(page.getByTestId("shell-top-bar")).toBeVisible();
      }
      await expect(page.getByTestId(`row-${WIDE_LONG_RECORD_KEY}`)).toBeVisible();

      const count = await children.count();
      // A one-child toolbar would satisfy "every child shares one y" vacuously.
      expect.soft(count, `toolbar children at ${width}px`).toBeGreaterThan(1);

      // Addressed by `nth(i)` rather than snapshotted with `.all()`: `.all()` hands back one
      // handle per match and a re-render mid-loop leaves the later ones unresolvable, which
      // reports a test timeout instead of the assertion the test is about (AGENTS.md, Traps).
      const rows: string[] = [];
      for (let i = 0; i < count; i += 1) {
        const child = children.nth(i);
        const box = await child.boundingBox();
        expect(box, `toolbar child ${i} at ${width}px has no box`).not.toBeNull();
        const label = ((await child.textContent()) ?? "").trim().slice(0, 24);
        rows.push(`${i} "${label}" y=${box!.y}`);
      }

      const ys = rows.map((row) => Number(row.slice(row.lastIndexOf("y=") + 2)));
      // Soft, so the 800px pass is MEASURED even when the 1280px pass fails: an assertion that
      // only ever executes after a failing one has never been measured (AGENTS.md, Traps), and a
      // hard failure on the first width would leave the narrow viewport — the one DD-42,
      // applied to viewports, is about — unasserted.
      expect
        .soft(
          Math.max(...ys) - Math.min(...ys),
          `toolbar wrapped at ${width}px:\n  ${rows.join("\n  ")}`,
          // 1px of slack for subpixel layout, not for a second row: the toolbar's controls are
          // 28px tall (docs/DESIGN.md 7.1's `sm`), which the scenario below asserts for the
          // chips among them, so a second row is 29px away and not 1px away.
        )
        .toBeLessThanOrEqual(1);
    }
  });

  /**
   * The chip and pill heights, measured.
   *
   * `docs/DESIGN.md` 7.4 gives the filter chip a height of **28px** and 7.3 gives the pill
   * **24px**. Elsewhere both numbers live only in a class list (`h-7`, `h-6`):
   * `Chip.test.tsx` and `Pill.test.tsx` read class strings, and both module headers say in so
   * many words that they prove no pixel, because `getBoundingClientRect` is zeroes under jsdom
   * (AGENTS.md, Traps). So the real assertion sits here, beside the one-row toolbar scenario,
   * which already reads `boundingBox()` on this page.
   *
   * **Not a screenshot baseline, deliberately.** `toHaveScreenshot` runs at
   * `maxDiffPixelRatio: 0.001`, which on a 1280x800 shot permits about 1,024 differing pixels —
   * more ink than a short chip contains, so a 4px height change on `+ Add filter` would pass a
   * baseline comparison unseen (AGENTS.md, Traps). A number is asserted as a number.
   *
   * Chips are addressed by the test ids their call sites already carry (`group-chip`,
   * `sort-chip`, `columns-chip`, `filter-chip-*`) rather than by a new attribute on the `Chip`
   * primitive itself.
   */
  test("vis_wide: chips are 28px and pills are 24px (docs/DESIGN.md 7.3, 7.4)", async ({
    page,
  }) => {
    await signInAsE2eAdmin(page);
    await page.goto(`/${WIDE_TYPE_KEY}`);
    await expect(page.getByTestId(`row-${WIDE_LONG_RECORD_KEY}`)).toBeVisible();

    // `filter-chip-` with the trailing hyphen: the row's own container is `filter-chips`, and a
    // bare `^="filter-chip"` matched it too — measured, because it appeared in the mutation run
    // as `chip 0 "+ Add filterAdvanced"`. It passed only because a row of 28px chips is 28px
    // tall, which is a coincidence rather than the assertion.
    const chips = page.locator('[data-testid$="-chip"], [data-testid^="filter-chip-"]');
    const chipCount = await chips.count();
    // A zero-chip page would satisfy "every chip is 28px" vacuously. The toolbar carries at
    // least group, sort, columns, `+ Add filter` and `Advanced`.
    expect.soft(chipCount, "chips on the table page").toBeGreaterThanOrEqual(5);

    for (let i = 0; i < chipCount; i += 1) {
      const chip = chips.nth(i);
      const box = await chip.boundingBox();
      expect(box, `chip ${i} has no box`).not.toBeNull();
      const label = ((await chip.textContent()) ?? "").trim().slice(0, 24);
      expect
        .soft(box!.height, `chip ${i} "${label}" is not 28px (docs/DESIGN.md 7.4)`)
        .toBeCloseTo(28, 0);
    }

    const pills = page.getByTestId("pill");
    const pillCount = await pills.count();
    // `vis_wide` carries `single_select` and `multi_select` columns, so the rendered page has
    // pills; zero of them would make the loop below vacuous the same way.
    expect.soft(pillCount, "pills on the table page").toBeGreaterThan(0);

    for (let i = 0; i < pillCount; i += 1) {
      const pill = pills.nth(i);
      const box = await pill.boundingBox();
      expect(box, `pill ${i} has no box`).not.toBeNull();
      const label = ((await pill.textContent()) ?? "").trim().slice(0, 24);
      expect
        .soft(box!.height, `pill ${i} "${label}" is not 24px (docs/DESIGN.md 7.3)`)
        .toBeCloseTo(24, 0);
    }
  });

  /**
   * The long-text pop-out. There is no "the in-edit textarea fills its cell" scenario, because
   * an editor that fills its cell is still a 180px-wide editor: filling it only makes the
   * cramped box fill the cramped cell. The pop-out is the answer, and asserting both would
   * assert two contradictory shapes for one click.
   *
   * jsdom proves none of this: `getBoundingClientRect` is zeroes there, so
   * "sized for real writing" and "the row does not move" are geometry in a real browser or they
   * are not proven at all. The component tests in `TableView.test.tsx` carry the state machine;
   * these carry the boxes and the two baselines.
   */
  test("vis_wide: the pop-out opens in view mode with the whole value", async ({
    page,
  }) => {
    await signInAsE2eAdmin(page);
    await page.goto(`/${WIDE_TYPE_KEY}`);
    await expect(page.getByTestId(`row-${WIDE_LONG_RECORD_KEY}`)).toBeVisible();

    const before = await cellBox(page, WIDE_LONG_RECORD_KEY, "Notes");
    await page.getByRole("button", { name: `Edit Notes for ${WIDE_LONG_RECORD_KEY}` }).click();

    const dialog = page.getByTestId("long-text-cell-dialog");
    await expect(dialog).toBeVisible();
    // View mode, not the text box: the value is rendered, and no editor exists yet anywhere on
    // the page. Pre-fix this click produced the in-cell textarea and no dialog at all.
    await expect(dialog.getByTestId("long-text-cell-value")).toContainText(
      "accepting a manual journal for the residue.",
    );
    await expect(page.getByLabel(`Notes value for ${WIDE_LONG_RECORD_KEY}`)).toHaveCount(0);

    // The whole point of the one-line display is that rows keep a uniform height, so opening
    // the pop-out must not move the table underneath it.
    const after = await cellBox(page, WIDE_LONG_RECORD_KEY, "Notes");
    expect(Math.abs(after.td.width - before.td.width)).toBeLessThanOrEqual(1);
    expect(Math.abs(after.td.height - before.td.height)).toBeLessThanOrEqual(1);

    // The rendered value wraps inside the dialog rather than running off its edge. Not a
    // hypothetical — this failed once exactly here. `white-space` inherits, this dialog is rendered
    // from inside the `<td class="truncate">` (`white-space: nowrap`) it opens from, and a
    // `whitespace-pre-wrap` removed from this container had been what overrode that inheritance.
    // Geometry, because jsdom cannot see it and because the pixel diff reports it as "some pixels
    // differ" rather than as "the text does not wrap".
    const overflow = await dialog
      .getByTestId("long-text-cell-value")
      .evaluate((el) => el.scrollWidth - el.clientWidth);
    expect(overflow, "the rendered value overflows its box horizontally").toBeLessThanOrEqual(1);

    await page.evaluate(() => document.fonts.ready);
    // Every string in the shot is this fixture's own fixed text — the field name, the record
    // key, and WIDE_LONG_NOTES — so no mask is needed. Rendering this value through markdown
    // did not repaint it, which is itself a finding: this fixture's value is plain prose, and
    // plain prose rendered through markdown is pixel-identical to the same string under
    // `whitespace-pre-wrap`.
    await expect(dialog).toHaveScreenshot("long-text-popout-view.png");
  });

  test("vis_wide: the Edit control switches that same pop-out to a text box sized for writing", async ({
    page,
  }) => {
    await signInAsE2eAdmin(page);
    await page.goto(`/${WIDE_TYPE_KEY}`);
    await expect(page.getByTestId(`row-${WIDE_LONG_RECORD_KEY}`)).toBeVisible();

    const cell = await cellBox(page, WIDE_LONG_RECORD_KEY, "Notes");
    await page.getByRole("button", { name: `Edit Notes for ${WIDE_LONG_RECORD_KEY}` }).click();
    const dialog = page.getByTestId("long-text-cell-dialog");
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "Edit" }).click();

    // ONE surface, not two: the text box is inside the dialog that was showing
    // the value, and there is still exactly one dialog on the page.
    const textarea = dialog.getByLabel(`Notes value for ${WIDE_LONG_RECORD_KEY}`);
    await expect(textarea).toBeVisible();
    await expect(page.getByRole("dialog")).toHaveCount(1);
    await expect(dialog.getByTestId("long-text-cell-value")).toHaveCount(0);

    const box = await textarea.boundingBox();
    expect(box).not.toBeNull();
    // "Sized for real writing" is the decision, and the cell is what it is being sized against:
    // `Notes` renders at its declared 180px (a long value cannot widen it), so an in-cell editor
    // could never exceed ~156px of content box no matter how well it filled the cell. Measured
    // here: 534px wide and 256px tall, inside `ui/Dialog`'s own `max-w-xl`. The multiplier is
    // 2.5 and not 3 because 534 is what the shipped dialog actually renders — a first pass
    // asserted 3x against a `max-w-2xl` override that never applied (see the component's note).
    expect(box!.width).toBeGreaterThanOrEqual(cell.td.width * 2.5);
    expect(box!.width).toBeGreaterThanOrEqual(400);
    expect(box!.height).toBeGreaterThanOrEqual(200);

    await page.evaluate(() => document.fonts.ready);
    // The textarea carries the same fixture-fixed text, so no mask.
    await expect(dialog).toHaveScreenshot("long-text-popout-edit.png");
  });

  test("vis_wide: the pop-out opens and dismisses by keyboard alone, and writes nothing", async ({
    page,
  }) => {
    await signInAsE2eAdmin(page);
    await page.goto(`/${WIDE_TYPE_KEY}`);
    await expect(page.getByTestId(`row-${WIDE_LONG_RECORD_KEY}`)).toBeVisible();

    // Keyboard reachability is not decoration here: hover-to-expand was the rejected
    // alternative, and it was rejected on exactly this ground. The cell affordance is a `<button>`,
    // so focus and Enter are the whole path — no pointer event is issued in this test.
    const trigger = page.getByRole("button", { name: `Edit Notes for ${WIDE_LONG_RECORD_KEY}` });
    await trigger.focus();
    await page.keyboard.press("Enter");

    const dialog = page.getByTestId("long-text-cell-dialog");
    await expect(dialog).toBeVisible();
    // `showModal()` puts focus inside the dialog; the engine, not a hand-rolled trap, does it.
    await expect(dialog.getByRole("button", { name: "Edit" })).toBeFocused();

    await page.keyboard.press("Enter");
    const textarea = dialog.getByLabel(`Notes value for ${WIDE_LONG_RECORD_KEY}`);
    await expect(textarea).toBeFocused();
    await page.keyboard.type(" A keystroke that must not survive the dismissal.");

    // Escape is the native `<dialog>` close request. It dismisses the whole pop-out, and the
    // draft dies with it: the cell still shows the stored value, unedited.
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(trigger).not.toContainText("A keystroke that must not survive");

    // And the write really did not happen — re-read from the server, not from the rendered row.
    const stored = await apiContext.get(`/api/v1/records/${WIDE_LONG_RECORD_KEY}`);
    expect(stored.ok(), await stored.text()).toBeTruthy();
    const { data } = (await stored.json()) as { data: Record<string, unknown> };
    expect(data.notes).toBe(WIDE_LONG_NOTES);
  });

  test("vis_wide: the pop-out's Save fires by keyboard alone, and the write really lands", async ({
    page,
  }) => {
    await signInAsE2eAdmin(page);
    await page.goto(`/${WIDE_TYPE_KEY}`);
    await expect(page.getByTestId(`row-${WIDE_SHORT_RECORD_KEY}`)).toBeVisible();

    // The dismissal scenario above proves Escape; this proves the other half of "saves and
    // dismisses by keyboard alone". They are separate tests because a save mutates
    // the fixture and a dismissal must not, and this one has to put the row back.
    const KEYBOARD_SAVED_NOTES = "Saved without touching the mouse.";
    const trigger = page.getByRole("button", {
      name: `Edit Notes for ${WIDE_SHORT_RECORD_KEY}`,
    });

    try {
      await trigger.focus();
      await page.keyboard.press("Enter");
      const dialog = page.getByTestId("long-text-cell-dialog");
      await expect(dialog).toBeVisible();

      await page.keyboard.press("Enter");
      const textarea = dialog.getByLabel(`Notes value for ${WIDE_SHORT_RECORD_KEY}`);
      await expect(textarea).toBeFocused();
      await page.keyboard.press("ControlOrMeta+a");
      await page.keyboard.type(KEYBOARD_SAVED_NOTES);

      // Enter inside a textarea is a newline the user meant to type (`FieldInput`'s own rule),
      // so Save is reached by Tab — which is the assertion that matters here: an explicit Save
      // button is only an improvement over the in-cell editor if it is in the tab order.
      await page.keyboard.press("Tab");
      await expect(dialog.getByRole("button", { name: "Save" })).toBeFocused();
      await page.keyboard.press("Enter");

      await expect(dialog).toHaveCount(0);
      await expect(trigger).toHaveText(KEYBOARD_SAVED_NOTES);

      // Read it back from the server, not from the rendered row: the claim is that the write
      // landed, not that React re-rendered.
      const stored = await apiContext.get(`/api/v1/records/${WIDE_SHORT_RECORD_KEY}`);
      expect(stored.ok(), await stored.text()).toBeTruthy();
      const { data } = (await stored.json()) as { data: Record<string, unknown> };
      expect(data.notes).toBe(KEYBOARD_SAVED_NOTES);
    } finally {
      // Left as found, the same discipline the blast-radius scenario uses. This row's Notes
      // cell is inside `table-wide.png`, and the visual project's database survives the run,
      // so a fixture left mutated here would repaint a committed baseline on the next run —
      // in a different test, which is the worst place to discover it.
      const current = await apiContext.get(`/api/v1/records/${WIDE_SHORT_RECORD_KEY}`);
      expect(current.ok(), await current.text()).toBeTruthy();
      const { version } = (await current.json()) as { version: number };
      const restored = await apiContext.patch(`/api/v1/records/${WIDE_SHORT_RECORD_KEY}`, {
        data: { values: { notes: WIDE_SHORT_NOTES }, expected_version: version, force: false },
      });
      expect(restored.ok(), await restored.text()).toBeTruthy();
    }
  });

  test("vis_wide: every other field type still edits in place", async ({
    page,
  }) => {
    await signInAsE2eAdmin(page);
    await page.goto(`/${WIDE_TYPE_KEY}`);
    await expect(page.getByTestId(`row-${WIDE_LONG_RECORD_KEY}`)).toBeVisible();

    // A regression guard, not a defect proof: this passed against the unfixed tree, and must,
    // because it asserts the behaviour that does NOT change. Recorded as such rather than
    // presented as a fourth failing-first assertion.
    const nameCell = await cellBox(page, WIDE_LONG_RECORD_KEY, "Name");
    await page.getByRole("button", { name: `Edit Name for ${WIDE_LONG_RECORD_KEY}` }).click();
    const input = page.getByLabel(`Name value for ${WIDE_LONG_RECORD_KEY}`);
    await expect(input).toBeVisible();
    await expect(page.getByRole("dialog")).toHaveCount(0);

    // In place: the editor's box sits within the cell's own box.
    const box = await input.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.width).toBeLessThanOrEqual(nameCell.td.width);
    expect(box!.x).toBeGreaterThanOrEqual(nameCell.td.x - 1);
  });
});

/**
 * The record page.
 *
 * A baseline whose framing locator no longer exists is deleted, never re-recorded: a file under
 * the old name would be a picture of something else wearing that name. Playwright does not
 * delete orphaned snapshots, so they are deleted by hand.
 *
 * `record-detail-comment-markdown` frames `[data-testid^='body-']`, the comment body itself,
 * which the Activity card renders through `ui/Markdown.tsx`.
 */
test.describe("record page", () => {
  /** The display value of VDET-001, which is the page's `<h1>` (DD-23: the display field is
   * chosen, not guessed, and `name` is this type's first display-eligible field). */
  const DETAIL_RECORD_TITLE = "Runbook rewrite";

  test("the header names the thing, with the key as a chip beside the version", async ({
    page,
  }) => {
    await signInAsE2eAdmin(page);
    await page.goto(`/${DETAIL_TYPE_KEY}/${DETAIL_RECORD_KEY}`);

    // Asserted as text before the shot, because a passing visual suite is evidence about layout
    // only: `toHaveScreenshot` runs at a tolerance that hides a whole heading's worth of ink,
    // which is how a renamed product once survived 38 green baselines.
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(DETAIL_RECORD_TITLE);
    await expect(page.getByTestId("record-key-chip")).toHaveText(DETAIL_RECORD_KEY);
    await page.evaluate(() => document.fonts.ready);

    // Masked: the last-touched timestamp. The hand is left pixel-checked — it is the fixture's
    // own principal display name, and whether it renders as a name rather than a UUID is the
    // point of DD-25.
    await expect(page.getByTestId("record-header")).toHaveScreenshot("record-header.png", {
      mask: [page.getByTestId("record-header").locator("time")],
    });
  });

  test("the Details card carries every field, its value and its gloss control", async ({
    page,
  }) => {
    await signInAsE2eAdmin(page);
    await page.goto(`/${DETAIL_TYPE_KEY}/${DETAIL_RECORD_KEY}`);
    const details = page.getByRole("region", { name: "Details" });
    await expect(page.getByTestId("field-list")).toBeVisible();
    await expect(page.getByTestId("relation-field-owner")).toContainText(DETAIL_TARGET_RECORD_KEY);
    // One gloss control per field. The count comes
    // from the page rather than from a constant here because `record-page.spec.ts` already
    // proves it equals the API's field count on a fixture it owns; this is the framing check.
    await expect(details.locator('[data-testid^="gloss-toggle-"]')).toHaveCount(5);
    await page.evaluate(() => document.fonts.ready);

    // No mask: every value is fixture-fixed text and the card surfaces no timestamp or id.
    await expect(details).toHaveScreenshot("record-details-card.png");
  });

  test("a field's gloss opens beneath it, in the panel 7.11 defines", async ({ page }) => {
    await signInAsE2eAdmin(page);
    await page.goto(`/${DETAIL_TYPE_KEY}/${DETAIL_RECORD_KEY}`);
    const details = page.getByRole("region", { name: "Details" });
    const toggle = details.getByTestId("gloss-toggle-status");
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await toggle.click();
    await expect(details.getByTestId("gloss-panel-status")).toBeVisible();
    await page.evaluate(() => document.fonts.ready);

    // The whole card, not the panel alone: what this baseline is about is the panel sitting
    // *beneath the row it explains* and pushing the rest of the card down, which a shot of the
    // panel by itself would not show.
    await expect(details).toHaveScreenshot("record-details-gloss-open.png");
  });

  /**
   * The attachment widget keeps a baseline of its own, framed on the Details card that contains it.
   * Without it there would be no picture of DD-27's placeholder row — an id the sidecar did not
   * resolve, standing in the list so the count still equals what the record stores.
   */
  test("the attachment rows keep one row per stored id, resolved or not", async ({ page }) => {
    await signInAsE2eAdmin(page);
    await page.goto(`/${DETAIL_TYPE_KEY}/${DETAIL_ATTACHMENT_RECORD_KEY}`);
    const widget = page.getByTestId("attachment-field-files");
    await expect(widget.getByRole("link", { name: DETAIL_ATTACHMENT_FILENAME })).toBeVisible();
    // Two ids stored, one resolved: two rows. Asserted before the shot so a regression that
    // dropped the placeholder reads as this assertion failing rather than as a pixel diff.
    await expect(widget.getByRole("listitem")).toHaveCount(2);
    await page.evaluate(() => document.fonts.ready);

    await expect(page.getByRole("region", { name: "Details" })).toHaveScreenshot(
      "record-details-attachments.png",
    );
  });

  test("the Activity card is one feed of comments and versions, newest first", async ({
    page,
  }) => {
    await signInAsE2eAdmin(page);
    await page.goto(`/${DETAIL_TYPE_KEY}/${DETAIL_RECORD_KEY}`);
    const activity = page.getByRole("region", { name: "Activity" });
    await expect(activity.getByRole("button", { name: "edited" })).toBeVisible();
    // The composer's copy, asserted as text for the reason the header's title is: the visual
    // tolerance would not catch it changing.
    await expect(activity.getByPlaceholder("Reply. Agents read this too.")).toBeVisible();
    await expect(activity.getByRole("button", { name: "Post" })).toBeVisible();
    await page.evaluate(() => document.fonts.ready);

    // Masked: each event's principal id (the display name renders, but the raw UUID is still
    // the fallback and the mask covers either) and every `<time>`. Left pixel-checked:
    // the change pills and their old -> new values, the per-change and per-version revert
    // controls, the comment bodies, the "edited" marker, and the card's header hint.
    await expect(activity).toHaveScreenshot("record-activity-card.png", {
      mask: [activity.getByText(VISUAL_PRINCIPAL_ID), activity.locator("time")],
    });
  });

  /**
   * Comment markdown, in a real browser because neither claim is observable in jsdom:
   * `getComputedStyle` there returns nothing a type scale can be read off, and "renders as a
   * heading without being one" is a statement about the real document. The assertions are
   * scoped to the Activity card, which holds the comment body.
   */
  test("rendered markdown sits in the DD-41 type scale and emits no heading element", async ({
    page,
  }) => {
    await signInAsE2eAdmin(page);
    await page.goto(`/${DETAIL_TYPE_KEY}/${DETAIL_RECORD_KEY}`);
    const section = page.getByRole("region", { name: "Activity" });
    await expect(section.getByRole("button", { name: "edited" })).toBeVisible();

    const body = section.locator("[data-testid^='body-']").first();
    await expect(body).toBeVisible();

    // The heading renders at a scale step, not at a library default. `## ` maps to the
    // `text-base` recipe. DD-41 keeps that step at 14px and states the line height as a
    // ratio rather than a pixel value (docs/DESIGN.md 2.2: 1.35 at and below 14px), so the
    // computed value is 18.9px.
    const heading = body.locator(".md-h");
    await expect(heading).toHaveText("Kickoff");
    await expect(heading).toHaveCSS("font-size", "14px");
    await expect(heading).toHaveCSS("line-height", "18.9px");
    await expect(heading).toHaveCSS("font-weight", "600");

    // It LOOKS like a heading and is not one. The record page's outline is h1 (the display
    // value) then h2 ("Details", "Activity"); a real heading emitted from user text would land
    // under those and break the page's heading outline through content rather than through
    // code.
    await expect(body.locator("h1, h2, h3, h4, h5, h6")).toHaveCount(0);

    // The list is rendered structure with a real marker, not the literal "- " of the source.
    await expect(body.locator("ul > li")).toHaveCount(2);
    await expect(body.locator("ul")).toHaveCSS("list-style-type", "disc");
    await expect(body).not.toContainText("- vendor window");

    await page.evaluate(() => document.fonts.ready);
    await expect(body).toHaveScreenshot("record-detail-comment-markdown.png");
  });
});

test.describe("schema editor", () => {
  test("schema index and the edit screen: no UUID or timestamp surface, no mask needed", async ({
    page,
  }) => {
    await signInAsE2eAdmin(page);
    await page.goto("/schema");
    // Two "Visual Schema" links exist once this fixture is seeded: the shell's own object-type
    // nav, and this screen's list — scope to the latter (`main`).
    const indexList = page.getByRole("main").getByRole("link", { name: "Visual Schema" });
    await expect(indexList).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    // Every fixture type's key, field count, and record count are fixed, deterministic values
    // (the type list itself sorts by key — `list_object_types`, `ORDER BY key` — not by a
    // timestamp), so this full-page shot needs no mask.
    const indexSection = page.getByRole("region", { name: "Schema" });
    await expect(indexSection).toHaveCount(1);
    await expect(indexSection).toHaveScreenshot("schema-index.png");

    await indexList.click();
    await expect(page.getByRole("heading", { name: "Visual Schema" })).toBeVisible();
    await expect(page.getByTestId("field-row-legacy_note")).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    // Same reasoning: key, key prefix, name, description, and the fields table are all fixture
    // text, not live state.
    const editSection = page.getByRole("region", { name: "Object type schema" });
    await expect(editSection).toHaveCount(1);
    await expect(editSection).toHaveScreenshot("schema-edit.png");
  });

  test("the permissions panel: default access, the creator's one grant row, and the picker", async ({
    page,
  }) => {
    await signInAsE2eAdmin(page);
    await page.goto(`/schema/${SCHEMA_TYPE_KEY}`);

    // `create_object_type` inserts an `admin` grant for the creating principal in the same
    // transaction — including when that principal is already an `admin`, a deliberate
    // redundancy — so this fixture type has exactly one grant row with no new fixture
    // and no second principal. The row's `principal_id` is a per-database UUID and would need a
    // mask; it does not appear, because the panel resolves it to a display name through
    // `listPrincipals()`. The only other values on screen are static option labels.
    const panel = page.getByRole("region", { name: "Permissions" });
    await expect(panel).toHaveCount(1);
    await expect(panel.getByText("E2E Admin")).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    await expect(panel).toHaveScreenshot("permissions-panel.png");
  });

  test("field editor open: the FR-U4 guidance panel, the Agent-facing badge, and the relabeled embed checkbox", async ({
    page,
  }) => {
    await signInAsE2eAdmin(page);
    await page.goto(`/schema/${SCHEMA_TYPE_KEY}`);
    await page.getByRole("button", { name: "Add field" }).click();
    const form = page.getByRole("form", { name: "Add field" });
    await expect(form).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    // Static form markup only — no fixture data rendered here at all — so no mask.
    await expect(form).toHaveScreenshot("schema-field-editor.png");
  });

  test("blast radius: a real destructive delete_field proposal, proposal id masked, rejected via the API so the schema is left as found", async ({
    page,
  }) => {
    await signInAsE2eAdmin(page);
    await page.goto(`/schema/${SCHEMA_TYPE_KEY}`);
    const row = page.getByTestId("field-row-legacy_note");
    await expect(row).toBeVisible();
    await row.getByRole("button", { name: "Delete" }).click();

    const panel = page.getByTestId("blast-radius-panel");
    await expect(panel).toBeVisible();
    await expect(panel.getByTestId("blast-radius-affected-records")).toHaveText("2");
    await page.evaluate(() => document.fonts.ready);
    // Masked: both `<p>` lines — the server's own `message` (which spells the proposal id
    // inline: "approve proposal prop-... in the Glosswork UI...") and the "Proposal <id> is
    // pending" sentence below the `dl`; the id is a fresh uuid every run, in both places. Left
    // pixel-checked: the danger-led title and border (the one place danger color leads), the
    // change-type/affected-records/sample-values `dl`, and the Acknowledge
    // button.
    await expect(panel).toHaveScreenshot("schema-blast-radius.png", {
      mask: [panel.locator("p")],
    });

    // Never approved: reject the real proposal this test just created over the API, so the
    // `legacy_note` field — and this fixture's field/record shape for the other three shots
    // above — is left exactly as `ensureSchemaFixtureSeeded` leaves it, retry or not. The
    // panel already rendered the real id (the `<strong>` the mask above covers); read it
    // straight from the page rather than searching the proposal list by payload shape.
    const proposalId = (await panel.locator("strong").innerText()).trim();
    const rejected = await apiContext.post(`/api/v1/schema-proposals/${proposalId}/reject`, {
      data: { decision_note: "Visual-baseline fixture cleanup after the blast-radius shot." },
    });
    expect(rejected.ok(), await rejected.text()).toBeTruthy();
  });
});

/**
 * The Inbox.
 *
 * It reuses the ONE proposal `ensureSettingsFixtureSeeded` already leaves pending forever rather
 * than seeding its own. That is a budget decision, not a convenience: `shell-sidebar.png` and
 * `shell-top-bar.png` both frame the Inbox count badge, so a second pending proposal on this
 * database would repaint two baselines that have nothing to do with the Inbox.
 *
 * That fixture's proposal is seeded over REST with no `X-Agent-Label`, so `proposed_agent` is
 * null and these shots render a **person** hand rather than the agent square — docs/DESIGN.md
 * 6.5 working correctly, and stated here so a reader of the baseline does not conclude the
 * attribution is broken. The agent square is asserted by locator in `e2e/inbox.spec.ts`, which
 * seeds its own labelled proposal.
 */
test.describe("inbox", () => {
  /** The proposal's id is a fresh uuid per database, and it is in the URL. Found the way
   * `ensureSettingsFixtureSeeded` finds it for its own idempotence: by `reason`. */
  async function pendingProposalId(): Promise<string> {
    const response = await apiContext.get("/api/v1/schema-proposals?status=pending");
    expect(response.ok(), await response.text()).toBeTruthy();
    const { proposals } = (await response.json()) as {
      proposals: { id: string; reason: string | null }[];
    };
    const seeded = proposals.find((proposal) => proposal.reason === SETTINGS_PROPOSAL_REASON);
    expect(seeded, "the settings fixture's pending proposal must exist").toBeTruthy();
    return seeded!.id;
  }

  test("the list pane, with one proposal waiting", async ({ page }) => {
    await signInAsE2eAdmin(page);
    await page.goto("/inbox");
    const list = page.getByTestId("inbox-list");
    await expect(list.getByText(SETTINGS_PROPOSAL_REASON)).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    // Masked: each item's third line carries a relative time ("today at 09:28"), which is not
    // deterministic across days.
    await expect(list).toHaveScreenshot("inbox-list.png", {
      mask: [list.locator("p").filter({ hasText: /·/ })],
    });
  });

  test("the proposal detail: headline sentence, impact tiles, and struck-through samples", async ({
    page,
  }) => {
    await signInAsE2eAdmin(page);
    const proposalId = await pendingProposalId();
    await page.goto(`/inbox/${proposalId}`);

    const detail = page.getByTestId(`proposal-detail-${proposalId}`);
    await expect(detail.getByTestId("proposal-headline")).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    // Masked: the raised-at line, which says "today", and the id footer, which is a
    // fresh uuid per database. Left pixel-checked: the headline sentence, the quoted rationale,
    // the three tiles, the paragraph and the struck-through values — everything the screen is
    // actually about.
    await expect(detail).toHaveScreenshot("inbox-proposal-detail.png", {
      mask: [detail.getByTestId("proposal-raised-at"), detail.locator("p.font-mono").last()],
    });
  });
});

/**
 * `/people` and `/setup`.
 *
 * **What `people-agent-labels` does and does not picture, stated rather than discovered later.**
 * No REST-seeded fixture in this suite creates an agent label, and that is deliberate: `agents`
 * in the workspace document counts registered labels (`services/workspace.py`), so seeding one
 * moves the sidebar's "N people · M agents" line, which `shell-sidebar.png` and
 * `shell-top-bar.png` frame whole. The baseline therefore frames the card, its header and its
 * empty state. The group headers, the `Verified` column and the inline rename are proven by
 * `people/AgentLabelsTable`'s component tests and by `e2e/people-and-setup.spec.ts`, which
 * drives them against a real server. This is the same trade docs/DESIGN.md 8.5 records for the
 * first-run screen.
 */
test.describe("people and setup", () => {
  test("People: the one bootstrap admin, the only-active-administrator explanation under the table, no timestamp column", async ({
    page,
  }) => {
    await signInAsE2eAdmin(page);
    await page.goto("/people");
    const card = page.getByRole("region", { name: "People" });
    await expect(card.getByText("This is the only active administrator.")).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    // The table renders no date cell at all, so this is deterministic on this fixture without a
    // mask.
    await expect(card).toHaveScreenshot("people-table.png");
  });

  test("Agent labels: empty on this fixture, which is what the block comment above is about", async ({
    page,
  }) => {
    await signInAsE2eAdmin(page);
    await page.goto("/people");
    const card = page.getByRole("region", { name: "Agent labels" });
    await expect(card.getByText("No agent labels yet.")).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    await expect(card).toHaveScreenshot("people-agent-labels.png");
  });

  test("Service accounts: the deployment's own bootstrap account, drawn as an agent (6.5)", async ({
    page,
  }) => {
    await signInAsE2eAdmin(page);
    await page.goto("/people");
    const card = page.getByRole("region", { name: "Service accounts" });
    // Not empty on any real deployment: the app seeds a "bootstrap" service account (DD-4)
    // before this suite's fixtures ever run. Its row renders no date cell, so no mask.
    await expect(card.getByText("bootstrap", { exact: true })).toBeVisible();
    await expect(card).toContainText(
      "The deployment's own account: writes made before anyone signs in are attributed to it.",
    );
    await page.evaluate(() => document.fonts.ready);
    await expect(card).toHaveScreenshot("people-service-accounts.png");
  });

  test("Invite dialog: new markup in the top layer, and the only dialog baseline besides the merge conflict", async ({
    page,
  }) => {
    await signInAsE2eAdmin(page);
    await page.goto("/people");
    await page.getByRole("region", { name: "People" }).getByRole("button", { name: "Invite" }).click();
    const dialog = page.getByTestId("invite-user-dialog");
    await expect(dialog).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    await expect(dialog).toHaveScreenshot("people-invite-dialog.png");
  });

  test("Personal access tokens: the seeding PAT's prefix and last-used cell masked (this suite's own admin auth token)", async ({
    page,
  }) => {
    await signInAsE2eAdmin(page);
    await page.goto("/setup");
    const card = page.getByRole("region", { name: "Personal access tokens" });
    const row = card.locator("tbody tr").first();
    await expect(row).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    // The only token in this fixture is the PAT `VISUAL_AUTH_HEADER` authenticates every seeding
    // call with (`e2e/constants.ts`): its `token_prefix` is random per fresh database, and its
    // Last used cell updates on every request this suite makes, including the page load above.
    // Two cells now rather than a whole `<dl>`, which is the table paying for itself.
    //
    // **The indices are positional, and adding a column moves them.** When the Agent column went
    // in as the second column, `nth(1)` became Agent and `nth(4)` became Expires: the mask covered
    // two stable cells and left the random prefix and the live Last used timestamp pixel-checked,
    // which would have committed a baseline that fails on the next fresh database. Prefix is
    // `nth(2)` and Last used is `nth(5)` now. A column added to this table moves them again.
    await expect(card).toHaveScreenshot("setup-access-tokens.png", {
      mask: [row.locator("td").nth(2), row.locator("td").nth(5)],
    });
  });

  test("Search index: the dl's live counts masked, embedding model and semantic-search state stay pixel-checked", async ({
    page,
  }) => {
    await signInAsE2eAdmin(page);
    await page.goto("/setup");
    const card = page.getByRole("region", { name: "Search index" });
    await expect(card.getByText("bge-small-en-v1.5")).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    // Pending/running/indexed/stale counts depend on this shared visual database's background
    // indexing backlog at shot time (every other fixture type in this file feeds it), so the
    // whole `dl` is masked rather than picking apart which cell might be mid-flight this run;
    // the embedding model name and the "Re-index everything" button stay pixel-checked.
    await expect(card).toHaveScreenshot("setup-search-index.png", {
      mask: [card.locator("dl")],
    });
  });

  test("Export: a link, not a button, and the sentence saying where backup lives", async ({
    page,
  }) => {
    await signInAsE2eAdmin(page);
    await page.goto("/setup");
    const card = page.getByRole("region", { name: "Export" });
    await expect(card.getByTestId("export-link")).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    // Wholly static: no counts, no timestamps, no fixture data. No mask.
    await expect(card).toHaveScreenshot("setup-export.png");
  });

  test("Password: the local account's form, the revocation sentence before the button, no fixture data", async ({
    page,
  }) => {
    await signInAsE2eAdmin(page);
    await page.goto("/setup");
    const card = page.getByRole("region", { name: "Password" });
    await expect(card.getByRole("button", { name: "Change password" })).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    // Wholly static: no counts, no timestamps, no fixture data. The E2E admin is a local
    // account, so the card renders its form. No mask.
    await expect(card).toHaveScreenshot("setup-password.png");
  });
});

test.describe("search", () => {
  test("grouped results across two object types: hit-source, the snippet <mark>, and the other-matches Badge (pinned keyword query)", async ({
    page,
  }) => {
    await signInAsE2eAdmin(page);
    await waitForSearchIndexDrained();

    await page.goto(`/search?q=${encodeURIComponent(SEARCH_QUERY_PHRASE)}&mode=keyword`);

    const primarySection = page.getByRole("region", { name: "Visual Search results" });
    const secondarySection = page.getByRole("region", {
      name: "Visual Search Secondary results",
    });
    await expect(primarySection).toBeVisible();
    await expect(secondarySection).toBeVisible();

    // The primary record's phrase appears in both its `summary` field and a comment
    // (`ensureSearchFixtureSeeded`), so it has a real, non-zero `other_matches` count.
    const primaryResult = primarySection.getByTestId(`search-result-${SEARCH_RECORD_KEY}`);
    await expect(primaryResult.getByTestId("other-matches")).toBeVisible();
    expect(await primaryResult.locator("mark").count()).toBeGreaterThanOrEqual(1);
    await page.evaluate(() => document.fonts.ready);

    // Full-page shot: the segmented mode row (Keyword checked), the scope multi-select, both
    // grouped `<section>`s, the hit-source line, the snippet's `<mark>` highlight, and the
    // other-matches `Badge`. Every value on screen is this fixture's own fixed text — no UUID
    // or live timestamp surface (record keys are a fresh database's deterministic per-type
    // counter, same reasoning as the schema-index shot) — so no mask is needed.
    await expect(page.locator("main")).toHaveScreenshot("search-grouped-results.png");
  });

  test("narrowing the object-types scope to one type drops the other section", async ({
    page,
  }) => {
    await signInAsE2eAdmin(page);
    await waitForSearchIndexDrained();

    await page.goto(`/search?q=${encodeURIComponent(SEARCH_QUERY_PHRASE)}&mode=keyword`);
    await expect(
      page.getByRole("region", { name: "Visual Search Secondary results" }),
    ).toBeVisible();

    // `getByLabel` alone is ambiguous here: the app shell's own nav
    // (`app/ObjectTypeNav.tsx`) carries the identical `aria-label="Object types"`, invisible to
    // the component suite (which never renders the shell) but real once this runs in a browser
    // with the full `App` mounted. Scope by role to reach the `<select>`, not the `<nav>`.
    await page.getByRole("listbox", { name: "Object types" }).selectOption([SEARCH_TYPE_KEY]);

    await expect(
      page.getByRole("region", { name: "Visual Search Secondary results" }),
    ).not.toBeVisible();
    await expect(page.getByRole("region", { name: "Visual Search results" })).toBeVisible();
    await page.evaluate(() => document.fonts.ready);

    // Narrowing to one type also opens the FilterBuilder — deterministic static markup, no
    // fixture-dependent text of its own, so still no mask.
    await expect(page.locator("main")).toHaveScreenshot("search-scoped.png");
  });
});

/**
 * `/activity`.
 *
 * **Why the feed shot is scoped to one record**, written here because it is the only written
 * record of it: `search_audit_events` also returns every fixture type's own
 * schema-definition events (`entity_type` of `object_type` or `field`, from each
 * `ensureSeeded` creating its type), which carry no `record_id`. An unscoped feed therefore
 * renders a function of eight fixtures' seeding order, at a width nothing controls. Scoping to
 * the fixture record's own key returns exactly its three record-level events: one create and two
 * updates.
 */
test.describe("activity", () => {
  test("filter chips at rest: static markup, no fixture data rendered, no mask needed", async ({
    page,
  }) => {
    await signInAsE2eAdmin(page);
    await page.goto("/activity");
    const chips = page.getByTestId("activity-filters");
    await expect(chips.getByRole("button", { name: "Add filter" })).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    // An empty chip row is 7.4's dashed add-chip alone: no fixture text of any kind reaches this
    // shot, however many object types this shared visual database accumulates.
    await expect(chips).toHaveScreenshot("activity-filters.png");
  });

  test("the agent picker open: the screen's headline, and the one shot of a popover in the top layer", async ({
    page,
  }) => {
    await signInAsE2eAdmin(page);
    await page.goto("/activity");
    const chips = page.getByTestId("activity-filters");
    await chips.getByRole("button", { name: "Add filter" }).click();
    await page.getByRole("menuitem", { name: "Agent" }).click();
    const options = page.getByRole("listbox", { name: "Agent options" });
    await expect(options).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    // Deterministic without a mask: the directory is scoped to the labels on audit events this
    // caller may read, and this suite's REST fixtures register none, so it frames the empty
    // state. That sentence is the shot's whole point -- "no agent has written anything you can
    // see" is a different claim from "no agents exist", and it is the scoping made visible.
    await expect(options).toHaveScreenshot("activity-agent-picker.png");
  });

  test("the feed scoped to one record: one entry per write, change pills with formatted values, revert as a quiet danger Button", async ({
    page,
  }) => {
    await signInAsE2eAdmin(page);
    await page.goto("/activity");
    const chips = page.getByTestId("activity-filters");
    await chips.getByRole("button", { name: "Add filter" }).click();
    await page.getByRole("menuitem", { name: "Record" }).click();
    await page.getByRole("textbox", { name: "Record" }).fill(AUDIT_RECORD_KEY);
    await page.getByRole("button", { name: "Apply" }).click();

    const feed = page.getByTestId("activity-feed");
    // **"At risk", not "at_risk"**, and that difference is the point. The fixture's `status` is
    // a `single_select`, and printing `JSON.stringify(new_value)` would show the option's key, in
    // the mono face. A change pill prints the option's display label
    // (docs/DESIGN.md 5's vocabulary layer). It is one update's new value and the next one's old
    // value, so `.first()` rather than a strict-mode match.
    await expect(feed.getByText("At risk").first()).toBeVisible();
    await expect(feed.getByRole("button", { name: /^Revert/ }).first()).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    // Masked: the principal-id fallback (a raw UUID, the rendering DD-25 falls back to for a referent
    // with no display name) and every `<time>`, which is relative to the run. Left pixel-checked:
    // the grouped entries, the change pills, and the quiet-danger Revert controls.
    await expect(feed).toHaveScreenshot("activity-feed.png", {
      mask: [feed.getByText(VISUAL_PRINCIPAL_ID), feed.locator("time")],
    });
  });
});

test.describe("csv import wizard", () => {
  test("column mapping and a failing dry run: the h2 step sequence, the mapping table, and per-row errors in danger tokens", async ({
    page,
  }) => {
    await signInAsE2eAdmin(page);
    await page.goto(`/${IMPORT_TYPE_KEY}/import`);
    await page.getByLabel("Upload CSV file").setInputFiles(IMPORT_FIXTURE_CSV);

    // Auto-suggested by name similarity, no user action (`suggestFieldForHeader`) — the mapping
    // table's default state.
    await expect(page.getByLabel("Column mapping for Name")).toHaveValue("name");
    await expect(page.getByLabel("Column mapping for Status")).toHaveValue("status");

    await page.getByRole("button", { name: "Run dry-run" }).click();
    const errorsTable = page.getByTestId("dry-run-errors");
    await expect(errorsTable).toBeVisible();
    // Row 3 (the header is row 1, "Ledger sync runbook" row 2) carries the fixture's one
    // invalid status value — real, backend-produced validation output, not a mock.
    await expect(page.getByTestId("dry-run-error-0")).toContainText("3");
    await expect(page.getByRole("button", { name: "Commit" })).toBeDisabled();
    await page.evaluate(() => document.fonts.ready);

    // Every value on screen is this fixture's own fixed CSV text and static option labels — no
    // UUID or live timestamp surface (the fixture-choice rule) — so no mask is
    // needed, and idempotent across retries: nothing here is written to the database.
    const section = page.getByRole("region", { name: "CSV import" });
    await expect(section).toHaveCount(1);
    await expect(section).toHaveScreenshot("csv-import-mapping-errors.png");
  });

  test("a clean dry run and commit, after skipping the invalid column: the success Alerts replacing the raw ApiError.body render", async ({
    page,
  }) => {
    await signInAsE2eAdmin(page);
    await page.goto(`/${IMPORT_TYPE_KEY}/import`);
    await page.getByLabel("Upload CSV file").setInputFiles(IMPORT_FIXTURE_CSV);

    // Excluding the Status column sidesteps its one invalid value, so every row of this fixed
    // three-row fixture validates cleanly. Mode stays "create" (the default).
    //
    // The two assertions below once recorded a defect as intent: the dry run used to report
    // zero counts whatever it validated, so this screen said "0 would be created" and then
    // "3 created" one click later. A dry run is a prediction of the commit, so the two numbers
    // must agree, and this scenario is where that agreement is proved end to end through the
    // real wizard.
    await page.getByLabel("Column mapping for Status").selectOption("");

    await page.getByRole("button", { name: "Run dry-run" }).click();
    await expect(
      page.getByText("Dry run passed: 3 would be created, 0 would be updated."),
    ).toBeVisible();

    await page.getByRole("button", { name: "Commit" }).click();
    await expect(page.getByText("Import complete: 3 created, 0 updated.")).toBeVisible();
    await page.evaluate(() => document.fonts.ready);

    // No UUID or live timestamp surface here either — the commit result renders only this call's
    // own fixed counts — so no mask is needed.
    const section = page.getByRole("region", { name: "CSV import" });
    await expect(section).toHaveCount(1);
    await expect(section).toHaveScreenshot("csv-import-commit.png");
  });
});

test.describe("index and routes", () => {
  // Neither not-found state depends on any fixture object type existing (`getObjectType` and
  // `getRecord` 404 independently on a key nobody ever creates), so both shots need no seeding
  // and stay idempotent across retries. The no-object-types first-run screen is NOT
  // deterministically reachable here — the visual database always has fixture types by the
  // time this suite runs — so it is proven instead by the component test
  // `src/routes/IndexRoute.test.tsx`, not a shot.
  //
  // There is a second reason worth writing down. The screen IS reachable on the
  // functional server, by signing in as a grantless `creator` (`first-run.spec.ts`), and that
  // route is available here too — but a principal seeded into THIS database moves the
  // sidebar's "N people · M agents" line, which `shell-sidebar.png` and `shell-top-bar.png`
  // both frame whole. One new baseline would cost two repaints of baselines about something
  // else. Mocking the response instead would make it the only mocked baseline in the suite,
  // against a project whose whole point is that every pixel is a function of these fixtures.
  // So: no first-run baseline, deliberately.
  const BOGUS_TYPE_KEY = "nonexistent-type-xyz";
  const BOGUS_RECORD_KEY = "BOGUS-999";

  test("object type not found: the bare-text line onto EmptyState", async ({ page }) => {
    await signInAsE2eAdmin(page);
    await page.goto(`/${BOGUS_TYPE_KEY}`);
    await expect(page.getByText("Object type not found.")).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    await expect(page.locator("main")).toHaveScreenshot("object-type-not-found.png");
  });

  test("record not found: the bare-text line onto EmptyState", async ({ page }) => {
    await signInAsE2eAdmin(page);
    await page.goto(`/${BOGUS_TYPE_KEY}/${BOGUS_RECORD_KEY}`);
    await expect(page.getByText("Record not found.")).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    await expect(page.locator("main")).toHaveScreenshot("record-not-found.png");
  });
});

test.describe("design-review findings", () => {
  /**
   * The design review's non-pixel assertions. They live in the `visual` project because they
   * need this file's fixtures (a selected object type in the nav, the long search snippet, five
   * empty settings panels) and a real browser: sidebar contrast is a computed-style question
   * and the snippet's measure is a geometry question, and jsdom answers neither
   * (`getBoundingClientRect` is zeroes there, and jsdom resolves no cascade). None of them takes
   * a screenshot.
   */

  /** WCAG 2.x relative luminance, then the contrast ratio, from two opaque sRGB triples. */
  function contrastRatio(fg: [number, number, number], bg: [number, number, number]): number {
    const channel = (value: number) => {
      const s = value / 255;
      return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
    };
    const luminance = (c: [number, number, number]) =>
      0.2126 * channel(c[0]) + 0.7152 * channel(c[1]) + 0.0722 * channel(c[2]);
    const [lighter, darker] = [luminance(fg), luminance(bg)].sort((a, b) => b - a);
    return (lighter + 0.05) / (darker + 0.05);
  }

  /**
   * The colours the browser ACTUALLY paints, never the ones the stylesheet asks for: the sidebar
   * contrast defect was a cascade collision (`text-accent` and `text-ink-muted` are both
   * single-class selectors, so source order decides), so a class-list assertion would be incapable
   * of failing here — the markup is already correct. Background is resolved by walking ancestors
   * until one is opaque, because a link with no background of its own inherits whatever it sits on.
   */
  async function paintedColors(locator: import("@playwright/test").Locator) {
    return locator.evaluate((el) => {
      const parse = (value: string): [number, number, number, number] => {
        const parts = value.match(/[\d.]+/g)?.map(Number) ?? [];
        return [parts[0] ?? 0, parts[1] ?? 0, parts[2] ?? 0, parts[3] ?? 1];
      };
      const fg = parse(getComputedStyle(el).color);
      let bg: [number, number, number, number] = [255, 255, 255, 1];
      let node: Element | null = el;
      while (node) {
        const candidate = parse(getComputedStyle(node).backgroundColor);
        if (candidate[3] > 0) {
          bg = candidate;
          break;
        }
        node = node.parentElement;
      }
      return {
        fg: [fg[0], fg[1], fg[2]] as [number, number, number],
        bg: [bg[0], bg[1], bg[2]] as [number, number, number],
      };
    });
  }

  /**
   * The shell is one sidebar whose Tracking and Workspace sections are lists inside a single
   * `<nav>`.
   *
   * The contrast rule matters more here, not less. The defect was never about a wrong colour:
   * it was a cascade collision, `text-human-ink` and `text-ink-2` both being single-class
   * selectors, so source order decided and the selected link painted 4.44:1. `ui/classes.ts`'s
   * sidebar recipes keep the resting colour in a third class for that reason, and this is what
   * checks that they still do.
   *
   * Three rows are measured rather than one, because the sidebar has three kinds and they sit on
   * different backgrounds: an active Tracking row, an active Workspace row, and a RESTING row --
   * the last sits on a different ground (`ground` rather than `surface`), where `ink-2`
   * measures 5.50:1 rather than 5.89:1.
   */
  test("sidebar rows meet WCAG AA, active and resting", async ({ page }) => {
    await signInAsE2eAdmin(page);
    const sidebar = page.getByTestId("sidebar");

    await page.goto(`/${OBJECT_TYPE_KEY}`);
    const activeType = sidebar.locator('a[aria-current="page"]');
    await expect(activeType).toBeVisible();
    const activeTypeColors = await paintedColors(activeType);
    expect(
      contrastRatio(activeTypeColors.fg, activeTypeColors.bg),
      `active Tracking row: rgb(${activeTypeColors.fg}) on rgb(${activeTypeColors.bg})`,
    ).toBeGreaterThanOrEqual(4.5);

    // A resting row on the same screen: same recipe, the other half of the mutually exclusive
    // pair.
    const restingRow = sidebar.locator('a:not([aria-current="page"])').first();
    await expect(restingRow).toBeVisible();
    const restingColors = await paintedColors(restingRow);
    expect(
      contrastRatio(restingColors.fg, restingColors.bg),
      `resting sidebar row: rgb(${restingColors.fg}) on rgb(${restingColors.bg})`,
    ).toBeGreaterThanOrEqual(4.5);

    await page.goto("/people");
    const activeWorkspace = sidebar.locator('a[aria-current="page"]').first();
    await expect(activeWorkspace).toBeVisible();
    const workspaceColors = await paintedColors(activeWorkspace);
    expect(
      contrastRatio(workspaceColors.fg, workspaceColors.bg),
      `active Workspace row: rgb(${workspaceColors.fg}) on rgb(${workspaceColors.bg})`,
    ).toBeGreaterThanOrEqual(4.5);
  });

  /**
   * A contrast rule rather than a one-off fix. docs/DESIGN.md 4.3 once set the workspace's
   * "N people · M agents" line in `ink-3`; measured against the shipped tokens that is 2.79:1 on
   * `ground` in light and 4.00:1 in dark, against 2.1's own stated floor of 4.5:1 for any text
   * pair. It went unnoticed because 2.1's measured list covers five pairs and `ink-3` is not one
   * of them.
   *
   * Asserting the PAINTED colour rather than the class is the whole point: a class assertion
   * would pass against `text-ink-2` today and say nothing about whether the pixels clear the
   * floor if a token moves.
   */
  test("the workspace line clears the contrast floor on `ground`", async ({ page }) => {
    await signInAsE2eAdmin(page);

    const line = page.getByTestId("workspace-people-agents");
    await expect(line).toBeVisible();
    const colors = await paintedColors(line);
    expect(
      contrastRatio(colors.fg, colors.bg),
      `workspace line: rgb(${colors.fg}) on rgb(${colors.bg})`,
    ).toBeGreaterThanOrEqual(4.5);
  });

  /**
   * The same rule as the workspace line above, one screen over.
   *
   * `docs/DESIGN.md` 8.2 once set the table title's count and view name in **`ink-3`**, and 10
   * says 2.1's contrast floors hold in both themes. Measured against the shipped tokens, `ink-3`
   * on `ground` is **2.79:1** light and **4.00:1** dark, and this is 13.5px text, so the floor
   * is the full 4.5:1 with no large-text allowance. The maintainer's answer to that
   * contradiction is `ink-2` (5.50:1 light, 7.73:1 dark), which 8.2 now states.
   *
   * **Both themes, because the tokens invert and a light-only assertion would have missed the
   * dark failure entirely** — `ink-3` is the lighter `#8f95a6` on white grounds and the darker
   * `#6f7587` on dark ones, so the two are separate measurements rather than one. `emulateMedia`
   * is `theme.spec.ts`'s own idiom; it is per-page, and every baseline in this file is taken by
   * a different test on a different page, so no screenshot sees dark.
   *
   * Asserting the PAINTED ratio, not the class, for the workspace line's reason: a class
   * assertion passes
   * against `text-ink-2` today and says nothing about the pixels if a token moves.
   */
  test("the table title's count clears the contrast floor in both themes", async ({
    page,
  }) => {
    await signInAsE2eAdmin(page);

    for (const colorScheme of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme });
      await page.goto(`/${OBJECT_TYPE_KEY}`);

      const meta = page.getByTestId("table-title-meta");
      await expect(meta).toBeVisible();
      const colors = await paintedColors(meta);
      // Soft, so the dark measurement is TAKEN even when light fails: an assertion that only
      // ever runs after a failing one has never been measured (AGENTS.md, Traps), and these two
      // are a pair that has to hold together.
      expect
        .soft(
          contrastRatio(colors.fg, colors.bg),
          `title count in ${colorScheme}: rgb(${colors.fg}) on rgb(${colors.bg})`,
        )
        .toBeGreaterThanOrEqual(4.5);
    }
  });

  test("the search snippet's measure is constrained", async ({ page }) => {
    await signInAsE2eAdmin(page);
    await waitForSearchIndexDrained();
    await page.goto(`/search?q=${encodeURIComponent(SEARCH_QUERY_PHRASE)}&mode=keyword`);

    const snippet = page.getByTestId("snippet").first();
    await expect(snippet).toBeVisible();

    // Measured in `ch` against the snippet's OWN font rather than in pixels, so the assertion
    // states the decision (a max-width in the 65ch to 75ch range) instead of a px number
    // that goes stale when a type scale moves. A `<p>` is a block box, so this measures the
    // constraint rather than the fixture's text length. Pre-fix on this fixture: ~159ch.
    const measure = await snippet.evaluate((el) => {
      const probe = document.createElement("span");
      probe.style.cssText = "position:absolute;visibility:hidden;width:10ch";
      el.appendChild(probe);
      const ch = probe.getBoundingClientRect().width / 10;
      probe.remove();
      return el.getBoundingClientRect().width / ch;
    });
    expect(measure).toBeLessThanOrEqual(75);
    // The other end: a constraint that collapsed the box would also satisfy the line above.
    expect(measure).toBeGreaterThanOrEqual(50);
  });

  test("no empty state carries its own border inside a card", async ({ page }) => {
    await signInAsE2eAdmin(page);
    await page.goto("/people");

    // The positive control first: this fixture's screen really does render an empty state
    // inside a card, so a zero count below cannot come from a screen that failed to load.
    //
    // **One, not two.** The agent labels are one grouped table, so the sentence appears once. The
    // count is the assertion's whole positive half, so it moves with the markup rather than being
    // loosened to `toBeVisible()`.
    //
    // `toHaveCount`, not `expect(await ....count())`: waiting for the *first* empty state and then
    // counting immediately can count fewer than are coming. The web-first assertion retries; the
    // bare `count()` does not. The same applies, more sharply, to the zero-count below -- a
    // non-retrying count of 0 passes vacuously before anything has rendered, which is the failure
    // mode that reports green.
    await expect(page.getByText("No agent labels yet.")).toHaveCount(1);

    // The structure the design review measured, stated as a selector: a bordered card holding
    // the `EmptyState` primitive's own bordered card. Pre-fix on this fixture: 5.
    //
    // This is the DASHED half only. The wider nesting -- any card inside a card, dashed or not --
    // is asserted for these two screens in `e2e/people-and-setup.spec.ts`, with its own positive
    // control on a page that still has one. Two selectors because they are two rules.
    const nested = page.locator(
      "section.rounded-card.border .rounded-card.border.border-dashed",
    );
    await expect(nested).toHaveCount(0);

    // ...and the converse, so the fix cannot be "delete the border everywhere": an EmptyState
    // that is NOT inside a card keeps it.
    await page.goto("/nonexistent-type-xyz");
    const standalone = page.getByText("Object type not found.").locator("..");
    await expect(standalone).toHaveClass(/border-dashed/);
  });
});
