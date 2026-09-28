/**
 * The record header at the unit level: the title, the key chip, the version, and the "last
 * touched" `Hand`.
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import type { ObjectTypeDetail } from "../api/objectTypes";
import type { RecordWithIncludes } from "../api/records";
import { RecordHeader } from "./RecordHeader";

function objectType(overrides: Partial<ObjectTypeDetail> = {}): ObjectTypeDetail {
  return {
    key: "prospects",
    name: "Prospect",
    name_plural: "Prospects",
    description: "A sales prospect.",
    key_prefix: "PROS",
    record_count: 1,
    field_count: 1,
    your_access: "write",
    display_field_key: "company",
    effective_display_field_key: "company",
    fields: [
      {
        key: "company",
        name: "Company",
        type: "short_text",
        description: "The company's legal name.",
        required: false,
        unique: false,
        indexed: false,
        embed: false,
        default: null,
        config: {},
        position: 0,
        operators: [],
        display_eligible: true,
      },
    ],
    system_fields: [],
    ...overrides,
  };
}

function record(overrides: Partial<RecordWithIncludes> = {}): RecordWithIncludes {
  return {
    id: "rec-1",
    key: "PROS-005",
    version: 4,
    created_at: "2026-09-01T10:00:00Z",
    created_by: "p-1",
    updated_at: "2026-09-12T02:45:00Z",
    updated_by: "p-1",
    updated_by_agent_label_id: null,
    deleted_at: null,
    comment_count: 0,
    last_comment_at: null,
    data: { company: "Northwind Traders" },
    principals: {
      "p-1": { display_name: "Dana Reyes", email: null, is_active: true, type: "user" },
    },
    ...overrides,
  };
}

describe("RecordHeader", () => {
  it("titles the page with the display value, not the key", () => {
    render(<RecordHeader record={record()} objectType={objectType()} />);

    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading).toHaveTextContent("Northwind Traders");
    expect(heading).not.toHaveTextContent("PROS-005");
  });

  it("is the page's only heading", () => {
    render(<RecordHeader record={record()} objectType={objectType()} />);
    expect(screen.getAllByRole("heading")).toHaveLength(1);
  });

  it("keeps the key as a mono chip beside the version", () => {
    render(<RecordHeader record={record()} objectType={objectType()} />);

    expect(screen.getByTestId("record-key-chip")).toHaveTextContent("PROS-005");
    expect(screen.getByText("Version 4")).toBeInTheDocument();
  });

  it("falls back to the record key when the display value is empty", () => {
    render(
      <RecordHeader record={record({ data: { company: "" } })} objectType={objectType()} />,
    );
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("PROS-005");
  });

  it("falls back to the record key when nothing names a display field at all", () => {
    render(
      <RecordHeader
        record={record()}
        objectType={objectType({ effective_display_field_key: null })}
      />,
    );
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("PROS-005");
  });

  it("renders last touched as a Hand, never a Pair", () => {
    render(<RecordHeader record={record()} objectType={objectType()} />);

    expect(screen.getByTestId("hand")).toHaveTextContent("Dana Reyes");
    expect(screen.queryByTestId("pair")).not.toBeInTheDocument();
    expect(screen.getByTestId("avatar").dataset.kind).toBe("person");
  });

  it("names the credential an agent acted on, through Hand's own 'for' clause", () => {
    render(
      <RecordHeader
        record={record({
          updated_by_agent_label_id: "a-1",
          agent_labels: { "a-1": { label: "sales-agent", display_name: null } },
        })}
        objectType={objectType()}
      />,
    );

    expect(screen.getByTestId("hand")).toHaveTextContent("sales-agent");
    expect(screen.getByTestId("hand")).toHaveTextContent("for Dana Reyes");
    expect(screen.getByTestId("avatar").dataset.kind).toBe("agent");
  });

  it("renders the last-touched time through ui/datetime.ts, not as a raw ISO string", () => {
    render(<RecordHeader record={record()} objectType={objectType()} />);

    const time = screen.getByText((_, element) => element?.tagName === "TIME") as HTMLElement;
    expect(time).toHaveAttribute("dateTime", "2026-09-12T02:45:00Z");
    expect(time.textContent).not.toBe("2026-09-12T02:45:00Z");
  });
});
