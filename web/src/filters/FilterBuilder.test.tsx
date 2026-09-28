import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { renderWithProviders } from "../test/renderWithProviders";
import { FilterBuilder } from "./FilterBuilder";
import { toFilterableFields } from "./filterableFields";
import {
  filterBuilderFields,
  filterBuilderSystemFields,
} from "./__fixtures__/filterBuilderObjectType";
import type { FilterNode } from "./types";

// `owner`'s `user_ref` widget fetches the principal directory the moment the field is selected
// (every test below that selects any field, `it.each`'s operators sweep included), so this file
// needs a directory handler and a `QueryClient`, which is why it renders through
// `renderWithProviders` rather than the plain `render`.
const server = setupServer(
  http.get("/api/v1/principals/directory", () =>
    HttpResponse.json({
      principals: [
        { id: "p1", display_name: "Sarah Okonjo", email: "sarah@example.com", type: "user", is_active: true },
      ],
    }),
  ),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe("FilterBuilder: operators come entirely from a field's `operators` array", () => {
  it.each(toFilterableFields(filterBuilderFields, filterBuilderSystemFields))(
    "renders exactly $key's own operators, no more and no fewer",
    async (field) => {
      const user = userEvent.setup();
      const onChange = vi.fn();
      renderWithProviders(
        <FilterBuilder
          fields={filterBuilderFields}
          systemFields={filterBuilderSystemFields}
          initialFilter={null}
          onChange={onChange}
        />,
      );

      await user.click(screen.getByRole("button", { name: "+ Condition" }));
      const row = screen.getByTestId("filter-node-root");
      await user.selectOptions(within(row).getByLabelText("Field"), field.key);

      const operatorSelect = within(row).getByLabelText("Operator") as HTMLSelectElement;
      const renderedOperators = Array.from(operatorSelect.options).map((option) => option.value);

      expect(renderedOperators).toEqual(field.operators);
    },
  );
});

describe("FilterBuilder: and/or/not nesting round-trips through the filter-tree JSON shape", () => {
  it("builds a nested and/or/not filter in the UI matching docs/MCP_TOOLS.md section 4's shape", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderWithProviders(
      <FilterBuilder
        fields={filterBuilderFields}
        systemFields={[]}
        initialFilter={null}
        onChange={onChange}
      />,
    );

    // Start the tree as a top-level AND group.
    await user.click(screen.getByRole("button", { name: "+ Group (AND)" }));

    // Condition 0: status eq active.
    await user.click(
      within(screen.getByTestId("filter-node-root-actions")).getByRole("button", {
        name: "+ Condition",
      }),
    );
    const condition0 = screen.getByTestId("filter-node-root-0");
    await user.selectOptions(within(condition0).getByLabelText("Field"), "status");
    await user.selectOptions(within(condition0).getByLabelText("Status value"), "active");

    // Nested group (child 1), toggled from its AND default to OR.
    await user.click(
      within(screen.getByTestId("filter-node-root-actions")).getByRole("button", {
        name: "+ Group",
      }),
    );
    const nestedGroup = screen.getByTestId("filter-node-root-1");
    await user.selectOptions(within(nestedGroup).getByLabelText("Group type"), "or");

    // Nested condition 0: priority gt 5.
    await user.click(
      within(screen.getByTestId("filter-node-root-1-actions")).getByRole("button", {
        name: "+ Condition",
      }),
    );
    const nestedCondition0 = screen.getByTestId("filter-node-root-1-0");
    await user.selectOptions(within(nestedCondition0).getByLabelText("Field"), "priority");
    await user.selectOptions(within(nestedCondition0).getByLabelText("Operator"), "gt");
    await user.type(within(nestedCondition0).getByLabelText("Priority value"), "5");

    // Nested condition 1: title contains "foo".
    await user.click(
      within(screen.getByTestId("filter-node-root-1-actions")).getByRole("button", {
        name: "+ Condition",
      }),
    );
    const nestedCondition1 = screen.getByTestId("filter-node-root-1-1");
    await user.selectOptions(within(nestedCondition1).getByLabelText("Field"), "title");
    await user.selectOptions(within(nestedCondition1).getByLabelText("Operator"), "contains");
    await user.type(within(nestedCondition1).getByLabelText("Title value"), "foo");

    // Condition 2: NOT(tags has_any ["urgent"]).
    await user.click(
      within(screen.getByTestId("filter-node-root-actions")).getByRole("button", {
        name: "+ Condition",
      }),
    );
    const condition2 = screen.getByTestId("filter-node-root-2");
    await user.selectOptions(within(condition2).getByLabelText("Field"), "tags");
    await user.click(within(condition2).getByRole("checkbox", { name: "Urgent" }));
    await user.click(within(condition2).getByRole("button", { name: "NOT" }));

    const expected: FilterNode = {
      and: [
        { field: "status", op: "eq", value: "active" },
        {
          or: [
            { field: "priority", op: "gt", value: 5 },
            { field: "title", op: "contains", value: "foo" },
          ],
        },
        { not: { field: "tags", op: "has_any", value: ["urgent"] } },
      ],
    };

    expect(onChange).toHaveBeenLastCalledWith(expected);
  });
});

describe("FilterBuilder: the date value input accepts a free-text relative token", () => {
  it("lets a date field's value accept a relative token like @today-7d", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderWithProviders(
      <FilterBuilder
        fields={filterBuilderFields}
        systemFields={[]}
        initialFilter={null}
        onChange={onChange}
      />,
    );

    await user.click(screen.getByRole("button", { name: "+ Condition" }));
    const row = screen.getByTestId("filter-node-root");
    await user.selectOptions(within(row).getByLabelText("Field"), "target_date");

    const valueInput = within(row).getByLabelText("Target Date value");
    await user.type(valueInput, "@today-7d");

    expect(valueInput).toHaveValue("@today-7d");
    expect(onChange).toHaveBeenLastCalledWith({
      field: "target_date",
      op: "eq",
      value: "@today-7d",
    });
  });
});

/**
 * `user_ref` does not get the plain text box the describe block above covers — it gets
 * `FieldInput`'s directory picker, with `@me` pinned above the fetched entries.
 */
describe("FilterBuilder: the user_ref value input is a directory picker with @me pinned", () => {
  it("lets a user_ref field's value be set to @me from the pinned option", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderWithProviders(
      <FilterBuilder
        fields={filterBuilderFields}
        systemFields={[]}
        initialFilter={null}
        onChange={onChange}
      />,
    );

    await user.click(screen.getByRole("button", { name: "+ Condition" }));
    const row = screen.getByTestId("filter-node-root");
    await user.selectOptions(within(row).getByLabelText("Field"), "owner");

    const valueSelect = within(row).getByLabelText("Owner value");
    // `@me` is pinned above the directory fetch, so it is selectable immediately, without
    // waiting on the network — that pin is the whole point.
    await user.selectOptions(valueSelect, "@me");

    expect(valueSelect).toHaveValue("@me");
    expect(onChange).toHaveBeenLastCalledWith({ field: "owner", op: "eq", value: "@me" });
  });

  it("lists the fetched directory below the pinned @me entry, and commits a chosen entry's id", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderWithProviders(
      <FilterBuilder
        fields={filterBuilderFields}
        systemFields={[]}
        initialFilter={null}
        onChange={onChange}
      />,
    );

    await user.click(screen.getByRole("button", { name: "+ Condition" }));
    const row = screen.getByTestId("filter-node-root");
    await user.selectOptions(within(row).getByLabelText("Field"), "owner");

    const valueSelect = within(row).getByLabelText("Owner value");
    await waitFor(() => {
      expect(within(valueSelect).getByText("Sarah Okonjo (sarah@example.com)")).toBeInTheDocument();
    });

    await user.selectOptions(valueSelect, "p1");

    expect(onChange).toHaveBeenLastCalledWith({ field: "owner", op: "eq", value: "p1" });
  });

  it("filters created_by, a user_ref pseudo-field, through the same picker", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderWithProviders(
      <FilterBuilder
        fields={filterBuilderFields}
        systemFields={filterBuilderSystemFields}
        initialFilter={null}
        onChange={onChange}
      />,
    );

    await user.click(screen.getByRole("button", { name: "+ Condition" }));
    const row = screen.getByTestId("filter-node-root");
    await user.selectOptions(within(row).getByLabelText("Field"), "created_by");

    // A system pseudo-field's `name` is its bare `key` (`filterableFields.ts`'s
    // `toFilterableFields`), so the accessible name is "created_by value", not "Created By value".
    const valueSelect = within(row).getByLabelText("created_by value");
    await user.selectOptions(valueSelect, "@me");

    expect(onChange).toHaveBeenLastCalledWith({ field: "created_by", op: "eq", value: "@me" });
  });
});
