/**
 * **An incomplete condition never reaches the network.**
 *
 * *Why this file lives in `table-view/` and not in `filters/`.* The assertion is about the
 * network, and `web/src/filters/` has no network at all: `FilterBuilder` "never calls the query
 * API itself" (its own header comment) and its unit tests render it with a bare `onChange` spy.
 * The only place a filter tree becomes an HTTP request is `TableView` -> `useTableRecordsQuery`
 * -> `POST /api/v1/object-types/{key}/query`, so the msw handler this criterion turns into a
 * tripwire can only be installed around a `TableView` render. The command
 * (`npm --prefix web run test -- filters table-view`) selects this file through its
 * `table-view` term.
 *
 * *What the handler does.* The `/query` handler below is not a stub that returns rows: it walks
 * the `filter` tree of every request that arrives and **fails the test** if any condition in it
 * is incomplete. When it trips it also answers with the real 422 envelope the server sends
 * (captured verbatim from a real server), so the component under test sees production's actual
 * response rather than a success it would never get.
 *
 * *The rule is IMPORTED, and that is the property this criterion is really about.* Before
 * `web/src/filters/completeness.ts` existed this file transcribed `filters.py`'s `NO_VALUE_OPS`,
 * `LIST_OPS` and the branches of `_parse_condition` into a local predicate, because importing a
 * module that did not exist would have made the file fail on module resolution, which proves
 * nothing (AGENTS.md, Traps). The transcription is gone: the tripwire judges an arriving request by
 * **the same rule the product gates on**, so a request that passes this handler is one the builder
 * believed was sendable, and a rule that drifts drifts for both at once.
 *
 * *A consequence for anyone mutating this, and it cost a measurement to find.* Because the judge
 * and the product now share one rule, **this file measures the GATES and `completeness.test.ts`
 * measures the RULE, and the two need different mutations**: breaking `isConditionComplete` makes
 * this file report **all 6 passed** (the handler stops recognising offenders at the same moment
 * the product stops holding them back) while `completeness.test.ts` reports dozens of failures —
 * so a green run here after that mutation is not evidence a gate is untested.
 *
 * *There are TWO gates, and each of them is measured from a different half of this file.* The
 * table page's filter is docs/DESIGN.md 7.4's chips, not the always-open tree builder, so an
 * incomplete condition can be held back in either of two places, and
 * bypassing one does not fail the tests that drive the other:
 *
 * | Bypass | Which cases fail |
 * | --- | --- |
 * | `ConditionPopover`'s `commitDraft` completeness check — **the chip gate** | the four condition cases |
 * | `FilterBuilder`'s `if (isFilterComplete(next))` — **the tree gate**, still what `/search` uses | the two group cases, which are the only ones a chip cannot express |
 *
 * Both numbers below the table are measured, not counted. The chip cases drive the popover
 * because that is where a condition is now built; the group cases drive the tree builder behind
 * the `Advanced` chip because `and`/`or` has no chip form at all, which is what keeps
 * `FilterBuilder`'s gate — the one both screens share — asserted from this file rather than
 * only from `/search`'s own tests. (Every mutation must be typechecked before its result is
 * believed: deleting a helper's body fails `tsc` on an unused function, and a mutation that does
 * not build measures nothing.)
 *
 * *Why each case closes the popover rather than pressing Apply.* `Apply` is **disabled** while
 * the draft is incomplete — that is 7.4's "an incomplete condition is shown in the popover" — so
 * a case that pressed it would do nothing whether or not the gate existed, and would be
 * unfailable by construction. Closing is a commit point (Apply, Enter, and close all are), so it
 * is the route by which an ungated draft would actually reach the network.
 *
 * The four incomplete states a condition can hold, one test each: no value at all, an empty `in`
 * list, a one-sided `between`, and a `linked_to_any` that carries no list.
 *
 * **And a fifth case, which is not a condition at all.** `+ Group (AND)` composed `{"and": []}`
 * and committed it, producing the same defect — the query fires, the server rejects it,
 * a red alert appears and the table empties — from a different button and a different 422
 * (`'and' requires a non-empty list of filters.`, `filters.py:160`). Same defect, same screen,
 * so it is gated by the same rule and asserted here beside the other four.
 */
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { renderWithProviders } from "../test/renderWithProviders";
import type { ObjectTypeDetail } from "../api/objectTypes";
import type { RecordDoc } from "../api/records";
import { TableView } from "./TableView";
import { incompleteNodes } from "../filters/completeness";
import { isAndNode, isOrNode, type FilterCondition, type FilterNode } from "../filters/types";

/**
 * Every unsendable node anywhere in one arriving request body's filter tree.
 *
 * The walk and the predicate both come from `web/src/filters/completeness.ts` — the module
 * `FilterBuilder`'s gate calls — so there is exactly one answer to "would the server refuse this?"
 * in the tree. The only thing this wrapper adds is the cast at the boundary: what msw hands
 * back is parsed JSON, and `FilterNode` is the wire shape that JSON is supposed to have
 * (`filters/types.ts`, docs/MCP_TOOLS.md section 4).
 */
function incompleteNodesInBody(filter: unknown): FilterNode[] {
  if (filter === null || filter === undefined || typeof filter !== "object") return [];
  return incompleteNodes(filter as FilterNode);
}

/**
 * The 422 the real server answers an unsendable node with, reproduced. Neither message is composed
 * here: the condition form is a real server's response captured verbatim, and the group form is
 * `src/glosswork/filters.py:160`'s `f"{kind!r} requires a non-empty list of filters."`. A group
 * refusal carries no `field_key`, because `_parse_node` never reaches a field to name.
 */
function refusal(node: FilterNode): { message: string; details: Record<string, string> } {
  if (isAndNode(node) || isOrNode(node)) {
    const kind = isAndNode(node) ? "and" : "or";
    return { message: `'${kind}' requires a non-empty list of filters.`, details: {} };
  }
  const condition = node as FilterCondition;
  return {
    message: `Operator '${condition.op}' requires a value.`,
    details: { field_key: condition.field },
  };
}

const objectType: ObjectTypeDetail = {
  key: "initiative",
  name: "Initiative",
  name_plural: "Initiatives",
  description: "A funded, sponsored workstream.",
  key_prefix: "INIT",
  record_count: 1,
  field_count: 4,
  your_access: "admin",
  display_field_key: null,
  effective_display_field_key: null,
  fields: [
    {
      key: "name",
      name: "Name",
      type: "short_text",
      description: "Short name.",
      required: true,
      unique: false,
      indexed: true,
      embed: false,
      default: null,
      config: {},
      position: 0,
      operators: ["eq", "neq", "contains", "is_null"],
      display_eligible: true,
    },
    {
      key: "status",
      name: "Status",
      type: "single_select",
      description: "Where the initiative stands.",
      required: true,
      unique: false,
      indexed: true,
      embed: false,
      default: null,
      config: {},
      position: 1,
      operators: ["eq", "neq", "in"],
      display_eligible: true,
      options: [
        { value: "on_track", label: "On Track", description: "Progressing." },
        { value: "at_risk", label: "At Risk", description: "Needs attention." },
      ],
    },
    {
      key: "amount",
      name: "Amount",
      type: "integer",
      description: "Contract value in whole units.",
      required: false,
      unique: false,
      indexed: true,
      embed: false,
      default: null,
      config: {},
      position: 2,
      // `between` is the point of this field: no other operator takes a two-element array.
      operators: ["eq", "gt", "gte", "lt", "lte", "between"],
      display_eligible: true,
    },
    {
      key: "owner",
      name: "Owner",
      type: "relation",
      description: "Who owns this initiative.",
      required: false,
      unique: false,
      indexed: false,
      embed: false,
      default: null,
      config: {},
      position: 3,
      // The real `relation` operator list (`src/glosswork/fieldtypes.py:76` plus the null
      // checks), which is what puts `linked_to_any` — a LIST_OPS member — on a field whose
      // value widget is a single text box.
      operators: ["linked_to", "linked_to_any", "has_links", "has_no_links"],
      display_eligible: false,
      target_type_key: "person",
      cardinality: "one",
    },
  ],
  system_fields: [
    { key: "key", type: "short_text", description: "Human key.", operators: ["eq", "in"] },
  ],
};

const rows: RecordDoc[] = [
  {
    id: "INIT-1-id",
    key: "INIT-1",
    version: 1,
    created_at: "2026-08-20T09:00:00",
    created_by: "principal-1",
    updated_at: "2026-08-20T09:00:00",
    updated_by: "principal-1",
    updated_by_agent_label_id: null,
    deleted_at: null,
    comment_count: 0,
    last_comment_at: null,
    data: { name: "Alpha", status: "on_track", amount: 68000 },
  },
];

/** Every unsendable node that reached the network, in arrival order. Each case asserts this is
 * empty; the test name says what the builder was doing while it filled up. */
let incompleteRequests: FilterNode[] = [];

/** The `filter` of every request that arrived, sendable or not. The last case below reads this to
 * assert that gating the *empty* group did not stop the *populated* one from being sent. */
let sentFilters: unknown[] = [];

const server = setupServer(
  http.post("/api/v1/object-types/:key/query", async ({ request }) => {
    const body = (await request.json()) as { filter?: unknown };
    sentFilters.push(body.filter ?? null);
    const offenders = incompleteNodesInBody(body.filter);
    if (offenders.length > 0) {
      incompleteRequests.push(...offenders);
      // The response the real server sends for this node, reproduced (see `refusal`).
      const { message, details } = refusal(offenders[0]);
      return HttpResponse.json(
        { error: { code: "validation_failed", message, details } },
        { status: 422 },
      );
    }
    return HttpResponse.json({
      records: rows,
      total_count: rows.length,
      next_cursor: null,
      truncated: false,
    });
  }),
  http.get("/api/v1/object-types/:key/saved-views", () => HttpResponse.json([])),
  http.get("/api/v1/principals/directory", () => HttpResponse.json({ principals: [] })),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => {
  incompleteRequests = [];
  sentFilters = [];
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

/** The first page has settled: a rendered row means the initial unfiltered query returned. */
async function waitForFirstPage(): Promise<void> {
  await screen.findByRole("button", { name: "Edit Name for INIT-1" });
}

/** The condition popover one filter chip opens (docs/DESIGN.md 7.4). */
function conditionPopover() {
  return screen.getByTestId("condition-popover");
}

/** The tree builder behind the `Advanced` chip — the same `FilterBuilder` `/search` renders. */
async function openAdvanced(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await user.click(screen.getByRole("button", { name: "Advanced" }));
  await screen.findByTestId("filter-builder");
}

/** Dismiss the open popover. Escape is a close, and a close is a commit point, so this
 * is the step an ungated draft would ride out on. */
async function closePopover(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await user.keyboard("{Escape}");
  await waitFor(() =>
    expect(screen.queryByTestId("condition-popover")).not.toBeInTheDocument(),
  );
}

describe("an incomplete condition issues no request — the chips", () => {
  it("holds while a condition has no value at all", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TableView objectType={objectType} />);
    await waitForFirstPage();

    // The state the defect was born in: a field and an operator, and no value. Ungated, one
    // click on `+ Condition` composed exactly this, `commit` called `onChange`, and `filter` is
    // part of the records query's identity (useTableRecordsQuery.ts:21-28), so the request went
    // out at once and came back 422.
    await user.click(screen.getByRole("button", { name: "+ Add filter" }));
    await user.selectOptions(within(conditionPopover()).getByLabelText("Field"), "status");
    await closePopover(user);

    expect(incompleteRequests).toEqual([]);
  });

  it("holds while an `in` list is empty", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TableView objectType={objectType} />);
    await waitForFirstPage();

    await user.click(screen.getByRole("button", { name: "+ Add filter" }));
    await user.selectOptions(within(conditionPopover()).getByLabelText("Field"), "status");
    await user.selectOptions(within(conditionPopover()).getByLabelText("Operator"), "is any of");
    // Ticked, then unticked: the list is now `[]`, which `filters.py:198-203` refuses as
    // "takes a non-empty array of values" — a state the person can reach on purpose. A checkbox
    // cannot be half-typed, so both clicks are commit points; the first sends a
    // sendable tree and the second must send nothing at all.
    const onTrack = within(conditionPopover()).getByLabelText("On Track");
    await user.click(onTrack);
    await waitFor(() => expect(sentFilters.at(-1)).toEqual({
      field: "status",
      op: "in",
      value: ["on_track"],
    }));
    await user.click(onTrack);
    await closePopover(user);

    expect(incompleteRequests).toEqual([]);
  });

  it("holds while a `between` has only one side", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TableView objectType={objectType} />);
    await waitForFirstPage();

    await user.click(screen.getByRole("button", { name: "+ Add filter" }));
    await user.selectOptions(within(conditionPopover()).getByLabelText("Field"), "amount");
    await user.selectOptions(within(conditionPopover()).getByLabelText("Operator"), "is between");
    await user.type(within(conditionPopover()).getByLabelText("Amount lower bound"), "1000");
    // `[1000, null]`: one step past what `filters.py:191-197` literally checks, and justified —
    // every branch of `_resolve_scalar` is an `isinstance` check, so the server refuses the null
    // half for all thirteen field types.
    await closePopover(user);

    expect(incompleteRequests).toEqual([]);
  });

  it("holds while `linked_to_any` carries no list", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TableView objectType={objectType} />);
    await waitForFirstPage();

    await user.click(screen.getByRole("button", { name: "+ Add filter" }));
    await user.selectOptions(within(conditionPopover()).getByLabelText("Field"), "owner");
    await user.selectOptions(
      within(conditionPopover()).getByLabelText("Operator"),
      "is linked to any of",
    );
    // Closed with the box empty — the state that precedes the first character of every relation
    // filter anyone will ever type.
    await closePopover(user);
    expect(incompleteRequests).toEqual([]);

    // And the other half, which is about the *widget* rather than the gate: this operator
    // is a `LIST_OPS` member, and unfixed the relation branch handed back a single scalar text
    // box, so what went out was a string where `filters.py` requires a non-empty array. The same
    // keystrokes now compose an array.
    await user.click(screen.getByRole("button", { name: "+ Add filter" }));
    await user.selectOptions(within(conditionPopover()).getByLabelText("Field"), "owner");
    await user.selectOptions(
      within(conditionPopover()).getByLabelText("Operator"),
      "is linked to any of",
    );
    await user.type(within(conditionPopover()).getByLabelText("Owner value"), "PER-1");
    await user.click(within(conditionPopover()).getByRole("button", { name: "Apply" }));

    await waitFor(() =>
      expect(sentFilters.at(-1)).toEqual({
        field: "owner",
        op: "linked_to_any",
        value: ["PER-1"],
      }),
    );
    expect(incompleteRequests).toEqual([]);
  });
});

/**
 * The same criterion against the tree builder, which is the table page's `Advanced` chip and
 * `/search`'s whole filter UI. A group has no chip form — the chip row is deliberately a flat
 * AND — so these two cases are the ones that still drive `FilterBuilder`'s own gate, and
 * they are what makes a bypass of it fail here rather than only on the other screen.
 */
describe("an incomplete group issues no request — the tree behind Advanced", () => {
  it("holds while an and/or group has no children (filters.py:158-160)", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TableView objectType={objectType} />);
    await waitForFirstPage();
    await openAdvanced(user);

    // One click composed `{"and": []}` and committed it, and the server answered
    // `'and' requires a non-empty list of filters.` — the same defect, reached without touching
    // a condition at all.
    await user.click(screen.getByRole("button", { name: "+ Group (AND)" }));

    await waitFor(() =>
      expect(screen.getByTestId("filter-node-root-actions")).toBeInTheDocument(),
    );
    expect(incompleteRequests).toEqual([]);
  });

  it("sends the group as soon as it holds one complete condition, and sends exactly that tree", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TableView objectType={objectType} />);
    await waitForFirstPage();
    await openAdvanced(user);

    // The behaviour the group gate must not break, and the one a naive fix would: a group is held
    // back while it is empty, not abandoned. Finishing a condition inside it sends the tree the
    // builder composed, unchanged — the gate does not alter the filter AST on the wire.
    await user.click(screen.getByRole("button", { name: "+ Group (AND)" }));
    await user.click(
      within(screen.getByTestId("filter-node-root-actions")).getByRole("button", {
        name: "+ Condition",
      }),
    );
    await user.type(
      within(screen.getByTestId("filter-node-root-0")).getByLabelText("Name value"),
      "Alpha",
    );

    await waitFor(() =>
      expect(sentFilters.at(-1)).toEqual({ and: [{ field: "name", op: "eq", value: "Alpha" }] }),
    );
    expect(incompleteRequests).toEqual([]);
  });
});
