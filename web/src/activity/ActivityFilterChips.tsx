/**
 * `/activity`'s chip row (docs/DESIGN.md 7.4, 8.7).
 *
 * Six dimensions, each a sentence chip with a popover, plus 7.4's dashed `+ Add filter`. The two
 * that used to be free-text UUID fields — `Person` and `Agent` — are pickers over
 * `GET /principals/directory` and `GET /agent-labels/directory`, which is the whole of the
 * issue: the screen this replaces asked for an identifier nobody has, so neither filter was
 * usable from the UI at all.
 *
 * **The agent directory is scoped to what the caller can see**, server-side, so this component
 * offers every option it is given without a second gate: an option the caller could not use is
 * one the route never returned.
 *
 * **Geometry is unproven here.** 7.4's 28px chip and section 9's wrap at 640px are class lists;
 * `getBoundingClientRect` returns zeroes under jsdom (`AGENTS.md`), so nothing in this directory
 * claims to measure either. `e2e/activity.spec.ts` does.
 */
import { useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";

import { fetchAgentLabelDirectory } from "../api/agentLabels";
import { fetchPrincipalDirectory } from "../api/principals";
import type { ObjectTypeSummary } from "../api/objectTypes";
import { Button } from "../ui/Button";
import { Chip } from "../ui/Chip";
import { Hand } from "../ui/Avatar";
import { agentName } from "../ui/attributionDerivation";
import { btnSmClass, fieldLabelClass, inputClass } from "../ui/classes";
import {
  DIMENSION_LABELS,
  chipSentence,
  isComplete,
  setDimensions,
  unsetDimensions,
  type ActivityDateValue,
  type ActivityDimension,
  type ActivityFilterValue,
  type ActivityFilters,
} from "./activityFilters";

interface ActivityFilterChipsProps {
  filters: ActivityFilters;
  onChange: (filters: ActivityFilters) => void;
  objectTypes: ObjectTypeSummary[];
}

/** A popover body that commits on submit and never before (7.4). */
function TextEditor({
  label,
  initial,
  onCommit,
}: {
  label: string;
  initial: string;
  onCommit: (value: ActivityFilterValue) => void;
}) {
  const [value, setValue] = useState(initial);
  return (
    <form
      aria-label={`${label} filter`}
      className="w-64 space-y-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (value.trim()) onCommit({ value: value.trim(), display: value.trim() });
      }}
    >
      <label className="flex flex-col gap-1">
        <span className={fieldLabelClass}>{label}</span>
        <input
          className={inputClass}
          value={value}
          autoFocus
          onChange={(event) => setValue(event.target.value)}
        />
      </label>
      <Button type="submit" variant="primary" className={btnSmClass} disabled={!value.trim()}>
        Apply
      </Button>
    </form>
  );
}

/** The two pickers. Options come from a directory; the chip stores the id and the name. */
function PrincipalPicker({ onCommit }: { onCommit: (value: ActivityFilterValue) => void }) {
  const { data } = useQuery({
    queryKey: ["principal-directory", "activity"],
    queryFn: () => fetchPrincipalDirectory(),
  });
  return (
    <div className="max-h-64 w-64 overflow-y-auto" role="listbox" aria-label="Person options">
      {(data ?? []).map((entry) => (
        <button
          key={entry.id}
          type="button"
          role="option"
          aria-selected={false}
          className="flex w-full items-center gap-2 rounded-ctl px-2 py-1.5 text-left text-sm hover:bg-ground"
          onClick={() => onCommit({ value: entry.id, display: entry.display_name })}
        >
          <Hand principal={{ display_name: entry.display_name, type: entry.type }} size="row" />
        </button>
      ))}
      {data !== undefined && data.length === 0 && (
        <p className="px-2 py-1.5 text-sm text-ink-2">Nobody has written anything yet.</p>
      )}
    </div>
  );
}

function AgentPicker({ onCommit }: { onCommit: (value: ActivityFilterValue) => void }) {
  const { data } = useQuery({
    queryKey: ["agent-label-directory", "activity"],
    queryFn: () => fetchAgentLabelDirectory(),
  });
  return (
    <div className="max-h-64 w-64 overflow-y-auto" role="listbox" aria-label="Agent options">
      {(data ?? []).map((entry) => (
        <button
          key={entry.id}
          type="button"
          role="option"
          aria-selected={false}
          className="flex w-full items-center gap-2 rounded-ctl px-2 py-1.5 text-left text-sm hover:bg-ground"
          // `agentName`, not `entry.label`: the option beside it renders through `Hand`, which
          // shows a named label by its display name (docs/DESIGN.md 6.1), and a chip that spelled
          // the same agent differently from the row it was picked from would be two names for one
          // thing on one screen.
          onClick={() => onCommit({ value: entry.id, display: agentName(entry) })}
        >
          <Hand agentLabel={{ label: entry.label, display_name: entry.display_name }} size="row" />
        </button>
      ))}
      {/* Not "no agents exist": the directory is scoped to the events this caller may read, so
          an empty list means no agent has written anything they can see. */}
      {data !== undefined && data.length === 0 && (
        <p className="px-2 py-1.5 text-sm text-ink-2">
          No agent has written anything you can see.
        </p>
      )}
    </div>
  );
}

function ObjectTypePicker({
  objectTypes,
  onCommit,
}: {
  objectTypes: ObjectTypeSummary[];
  onCommit: (value: ActivityFilterValue) => void;
}) {
  return (
    <div className="w-64" role="listbox" aria-label="Type options">
      {objectTypes.map((objectType) => (
        <button
          key={objectType.key}
          type="button"
          role="option"
          aria-selected={false}
          className="block w-full rounded-ctl px-2 py-1.5 text-left text-sm hover:bg-ground"
          onClick={() => onCommit({ value: objectType.key, display: objectType.name })}
        >
          {objectType.name}
        </button>
      ))}
    </div>
  );
}

function DateEditor({
  initial,
  onCommit,
}: {
  initial: ActivityDateValue;
  onCommit: (value: ActivityDateValue) => void;
}) {
  const [draft, setDraft] = useState(initial);
  const complete = isComplete("date", draft);
  return (
    <form
      aria-label="Date filter"
      className="w-64 space-y-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (complete) onCommit(draft);
      }}
    >
      <label className="flex flex-col gap-1">
        <span className={fieldLabelClass}>Since</span>
        <input
          type="date"
          className={inputClass}
          value={draft.since}
          onChange={(event) => setDraft({ ...draft, since: event.target.value })}
        />
      </label>
      <label className="flex flex-col gap-1">
        <span className={fieldLabelClass}>Until</span>
        <input
          type="date"
          className={inputClass}
          value={draft.until}
          onChange={(event) => setDraft({ ...draft, until: event.target.value })}
        />
      </label>
      {/* 7.4: an incomplete condition is shown in the popover, not in the chip row, and never
          produces a server error. */}
      {!complete && <p className="text-xs text-ink-2">Give at least one end of the range.</p>}
      <Button type="submit" variant="primary" className={btnSmClass} disabled={!complete}>
        Apply
      </Button>
    </form>
  );
}

/**
 * 7.4's `+ Add filter`: choose a dimension, then fill it in, both inside the one popover.
 *
 * Two steps in one surface rather than two: writing the chosen dimension into the filter state
 * first would put an empty condition in the chip row, which is exactly what 7.4 forbids ("an
 * incomplete condition is shown in the popover, not in the chip row"). Nothing reaches the
 * filter state, or the network, until an editor commits.
 */
function AddFilterPopover({
  dimensions,
  editorFor,
}: {
  dimensions: ActivityDimension[];
  editorFor: (dimension: ActivityDimension) => ReactNode;
}) {
  const [chosen, setChosen] = useState<ActivityDimension | null>(null);
  if (chosen !== null) return <>{editorFor(chosen)}</>;
  return (
    <div className="w-48" role="menu" aria-label="Add filter">
      {dimensions.map((dimension) => (
        <button
          key={dimension}
          type="button"
          role="menuitem"
          className="block w-full rounded-ctl px-2 py-1.5 text-left text-sm hover:bg-ground"
          onClick={() => setChosen(dimension)}
        >
          {DIMENSION_LABELS[dimension]}
        </button>
      ))}
    </div>
  );
}

export function ActivityFilterChips({
  filters,
  onChange,
  objectTypes,
}: ActivityFilterChipsProps) {
  function editorFor(dimension: ActivityDimension, close: () => void) {
    const commit = (value: ActivityFilterValue | ActivityDateValue) => {
      onChange({ ...filters, [dimension]: value });
      close();
    };
    switch (dimension) {
      case "person":
        return <PrincipalPicker onCommit={commit} />;
      case "agent":
        return <AgentPicker onCommit={commit} />;
      case "objectType":
        return <ObjectTypePicker objectTypes={objectTypes} onCommit={commit} />;
      case "date":
        return <DateEditor initial={filters.date ?? { since: "", until: "" }} onCommit={commit} />;
      default:
        return (
          <TextEditor
            label={DIMENSION_LABELS[dimension]}
            initial={filters[dimension]?.value ?? ""}
            onCommit={commit}
          />
        );
    }
  }

  const unset = unsetDimensions(filters);

  return (
    <div data-testid="activity-filters" className="flex flex-wrap items-center gap-2">
      {setDimensions(filters).map((dimension) => (
        <Chip
          key={dimension}
          data-testid={`activity-chip-${dimension}`}
          label={chipSentence(dimension, filters)}
          popover={(close) => editorFor(dimension, close)}
          onRemove={() => {
            const next = { ...filters };
            delete next[dimension];
            onChange(next);
          }}
          removeLabel={`Remove ${DIMENSION_LABELS[dimension]} filter`}
        >
          {chipSentence(dimension, filters)}
        </Chip>
      ))}

      {unset.length > 0 && (
        <Chip
          variant="add"
          data-testid="activity-add-filter"
          label="Add filter"
          popover={(close) => (
            <AddFilterPopover
              dimensions={unset}
              editorFor={(dimension) => editorFor(dimension, close)}
            />
          )}
        >
          + Add filter
        </Chip>
      )}
    </div>
  );
}
