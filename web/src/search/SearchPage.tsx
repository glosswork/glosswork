/**
 * `/search` (FR-U5). The header's
 * `search-slot` form (`App.tsx`) only ever writes `q`; this page owns `mode`, `types`, and
 * `filter` in the URL (`searchParams.ts`) so a reload or a shared link reproduces the same
 * request. Results are grouped by object type (`groupResults.ts`), each hit's location (a field
 * or a comment) is named, and its snippet's server-supplied `<em>` markers are promoted to
 * `<mark>` only after the raw text is escaped (`renderSnippet.ts`).
 */
import { useMemo, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQueries, useQuery } from "@tanstack/react-query";
import { getObjectType, type ObjectTypeDetail } from "../api/objectTypes";
import { search, type SearchHit } from "../api/search";
import { useObjectType } from "../hooks/useObjectType";
import { useObjectTypes } from "../hooks/useObjectTypes";
import { FilterBuilder } from "../filters/FilterBuilder";
import type { FilterNode } from "../filters/types";
import { parseApiError } from "../table-view/apiErrors";
import { Alert } from "../ui/Alert";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { btnSmClass, inputClass } from "../ui/classes";
import { EmptyState } from "../ui/EmptyState";
import { Select } from "../ui/Select";
import { groupResultsByType } from "./groupResults";
import { renderSnippet } from "./renderSnippet";
import {
  DEFAULT_SEARCH_MODE,
  parseSearchParams,
  type SearchMode,
} from "./searchParams";

/** The mode fieldset's segmented-row treatment: each label is a compact, individually
 * rounded chip (the connected-row precedent `SortControls.tsx`/`GroupBySelect.tsx` set for
 * this screen's other compact controls), with `has-[:checked]` promoting the active option
 * to the accent tones DD-41 reserves for the current selection. */
const modeOptionClass =
  "inline-flex items-center gap-1.5 rounded-ctl border border-line-2 bg-surface px-3 py-1.5 " +
  "text-sm text-ink has-[:checked]:border-human-line has-[:checked]:bg-human-soft has-[:checked]:text-human-ink";

/** The snippet's `<mark>` highlight treatment (DD-41's warning token is the closest neutral
 * highlight color the semantic palette offers — accent is reserved for actions and active
 * navigation only). Targets the `<mark>` tags `renderSnippet.ts` already emits inside this
 * paragraph via a descendant arbitrary variant, since the markup itself is untouched. */
/** `max-w-[70ch]` is the measure constraint. The snippet is prose-shaped text, and without a
 * measure, at a 1280px viewport, it ran to ~155 characters a line — roughly twice a readable
 * measure. In `ch` rather than a fixed width so it tracks the type scale; 70 sits
 * mid-range of a readable 65ch-75ch. */
const snippetClass =
  "mt-1 max-w-[70ch] text-sm text-ink [&_mark]:rounded-sm [&_mark]:bg-warn-soft " +
  "[&_mark]:px-0.5 [&_mark]:font-semibold [&_mark]:not-italic [&_mark]:text-ink";

const MODE_OPTIONS: { value: SearchMode; label: string }[] = [
  { value: "hybrid", label: "Hybrid" },
  { value: "semantic", label: "Semantic" },
  { value: "keyword", label: "Keyword" },
];

const SEARCH_LIMIT = 10;

function hitSourceLabel(hit: SearchHit, detailByType: Map<string, ObjectTypeDetail | undefined>): string {
  const hitSource = hit.hit_source;
  if (hitSource.type === "comment") {
    return `comment by ${hitSource.author}`;
  }
  const detail = detailByType.get(hit.object_type);
  const field = detail?.fields.find((candidate) => candidate.key === hitSource.field_key);
  return field?.name ?? hitSource.field_key;
}

export function SearchPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const parsed = useMemo(() => parseSearchParams(searchParams), [searchParams]);
  const trimmedQuery = parsed.q.trim();

  // Seeded from the URL and owned by the box thereafter, so typing does not re-run the search on
  // every keystroke: the URL -- and the query -- change on submit.
  //
  // Reset DURING render rather than in an effect (React's "adjusting state when a prop changes"
  // pattern): an effect that calls `setState` synchronously renders twice and is what
  // `react-hooks/set-state-in-effect` flags. The tracked copy of `parsed.q` is what makes
  // arriving at a shared link fill the box with the query that link searched for.
  const [queryInput, setQueryInput] = useState(parsed.q);
  const [seededFrom, setSeededFrom] = useState(parsed.q);
  if (parsed.q !== seededFrom) {
    setSeededFrom(parsed.q);
    setQueryInput(parsed.q);
  }

  const objectTypesQuery = useObjectTypes();
  const objectTypes = useMemo(() => objectTypesQuery.data ?? [], [objectTypesQuery.data]);
  const objectTypeNames = useMemo(
    () => new Map(objectTypes.map((type) => [type.key, type.name])),
    [objectTypes],
  );

  const singleType = parsed.types.length === 1 ? parsed.types[0] : undefined;
  const singleTypeQuery = useObjectType(singleType);

  function updateParams(mutate: (next: URLSearchParams) => void) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      mutate(next);
      return next;
    });
  }

  function handleQuerySubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    // Deferred a tick rather than called directly. This paragraph travels with the search box:
    // when the box once moved from `App.tsx`, deleting the header form deleted the only copy of
    // it and the same bug came straight back in the new form.
    //
    // React 19 treats a same-tick router update inside a submit handler as a possible
    // form-action transition and tries to snapshot the submission into `FormData` for
    // `useFormStatus`, even though this form declares no `action`. In a real browser that
    // snapshot is built and silently discarded; under Vitest it THROWS, because
    // `test/setup.ts` deliberately replaces the global `FormData` with undici's (for msw's
    // fetch interceptor) and undici does not implement the DOM's two-argument
    // `new FormData(form, submitter)` overload React calls internally. The throw does not fail
    // any assertion -- every test still passes -- it surfaces as an unhandled error that exits
    // vitest non-zero, which is how it reached a green-looking suite and was caught by the
    // `verify` agent running the Accept block rather than by any run of mine.
    queueMicrotask(() =>
      updateParams((next) => {
        const trimmed = queryInput.trim();
        if (trimmed) next.set("q", trimmed);
        else next.delete("q");
      }),
    );
  }

  function handleModeChange(mode: SearchMode) {
    updateParams((next) => {
      if (mode === DEFAULT_SEARCH_MODE) {
        next.delete("mode");
      } else {
        next.set("mode", mode);
      }
    });
  }

  function handleTypesChange(types: string[]) {
    updateParams((next) => {
      if (types.length > 0) {
        next.set("types", types.join(","));
      } else {
        next.delete("types");
      }
      // A stored filter is only ever meaningful for the exact one type it was built against
      // (docs/MCP_TOOLS.md 5.1's one-type rule): any change to the type selection drops it
      // rather than silently resending it against a different (or no) type.
      next.delete("filter");
    });
  }

  function handleFilterChange(tree: FilterNode | null) {
    updateParams((next) => {
      if (tree) {
        next.set("filter", JSON.stringify(tree));
      } else {
        next.delete("filter");
      }
    });
  }

  const searchQuery = useQuery({
    queryKey: ["search", parsed.q, parsed.mode, parsed.types, parsed.filter],
    queryFn: () =>
      search({
        query: parsed.q,
        object_types: parsed.types.length > 0 ? parsed.types : null,
        filter: parsed.filter ? (parsed.filter as unknown as Record<string, unknown>) : null,
        mode: parsed.mode,
        limit: SEARCH_LIMIT,
      }),
    enabled: trimmedQuery.length > 0,
    retry: false,
  });

  const results = useMemo(() => searchQuery.data?.results ?? [], [searchQuery.data]);
  const groups = useMemo(() => groupResultsByType(results), [results]);
  const resultTypeKeys = useMemo(() => groups.map((group) => group.objectType), [groups]);

  // The set of object types present in one page of results is data-dependent, so a fixed number
  // of `useObjectType` calls can't cover it — `useQueries` is the supported way to run a
  // dynamic-length list of queries without breaking the rules of hooks. The query key matches
  // `useObjectType`'s exactly, so this shares the cache with it (and with the filter builder's
  // own `singleTypeQuery` above) rather than re-fetching a type this page already has.
  const detailQueries = useQueries({
    queries: resultTypeKeys.map((key) => ({
      queryKey: ["object-types", key, { includeSamples: false }],
      queryFn: () => getObjectType(key),
    })),
  });
  const detailByType = useMemo(() => {
    const map = new Map<string, ObjectTypeDetail | undefined>();
    resultTypeKeys.forEach((key, index) => {
      map.set(key, detailQueries[index]?.data);
    });
    return map;
  }, [resultTypeKeys, detailQueries]);

  const errorEnvelope = searchQuery.isError ? parseApiError(searchQuery.error) : null;
  const isFeatureDisabled = errorEnvelope?.code === "feature_disabled";
  const data = searchQuery.data;
  const indexLagMessage =
    data && data.index_lag.pending_jobs > 0
      ? `${data.index_lag.pending_jobs} items are still being indexed; very recent changes may ` +
        "not appear in semantic results yet."
      : null;

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold text-ink">Search</h1>

      {/*
        The query box. It lives on this page rather than in the app shell's header, and this
        page must have it: without an input of its own `q` has nowhere to be typed, and the
        product would have no way to type a search at all (FR-U5).

        It is a `<form>` so Enter submits, and it writes `q` through the same `updateParams`
        every other control on this page uses -- `mode`, `types` and `filter` stay owned here
        (`search/searchParams.ts`), so a reload or a shared link still reproduces the
        exact query.
      */}
      <form role="search" onSubmit={handleQuerySubmit} className="flex items-center gap-2">
        <input
          aria-label="Search"
          data-testid="search-page-input"
          className={inputClass}
          placeholder="Search records and comments"
          value={queryInput}
          onChange={(event) => setQueryInput(event.target.value)}
          // Focused on arrival so the `/` key lands the caret in the box rather than merely on
          // the page: the shortcut is only useful if you can start typing.
          autoFocus
        />
        <Button type="submit" className={btnSmClass}>
          Search
        </Button>
      </form>

      <fieldset className="flex flex-wrap items-center gap-3">
        <legend className="text-xs font-semibold text-ink">Mode</legend>
        {MODE_OPTIONS.map((option) => (
          <label key={option.value} className={modeOptionClass}>
            <input
              type="radio"
              name="search-mode"
              className="accent-accent"
              value={option.value}
              checked={parsed.mode === option.value}
              onChange={() => handleModeChange(option.value)}
            />
            {option.label}
          </label>
        ))}
      </fieldset>

      <Select
        multiple
        aria-label="Object types"
        value={parsed.types}
        onChange={(event) =>
          handleTypesChange(Array.from(event.target.selectedOptions).map((option) => option.value))
        }
      >
        {objectTypes.map((type) => (
          <option key={type.key} value={type.key}>
            {type.name}
          </option>
        ))}
      </Select>

      {singleType && singleTypeQuery.data && (
        <FilterBuilder
          key={singleType}
          fields={singleTypeQuery.data.fields}
          systemFields={singleTypeQuery.data.system_fields}
          initialFilter={parsed.filter}
          onChange={handleFilterChange}
        />
      )}

      {trimmedQuery.length === 0 && <EmptyState title="Enter a search term to begin." />}

      {indexLagMessage && (
        <Alert tone="info" title={indexLagMessage} data-testid="index-lag" />
      )}

      {data && data.mode_applied !== parsed.mode && (
        <Alert
          tone="info"
          title="Semantic search is disabled on this deployment; showing keyword results."
          data-testid="mode-applied"
        />
      )}

      {isFeatureDisabled && errorEnvelope && (
        <p
          role="alert"
          data-testid="feature-disabled"
          className="max-w-xl rounded-card border border-bad-line bg-bad-soft px-3.5 py-2.5 text-base text-bad"
        >
          {errorEnvelope.message}
        </p>
      )}
      {searchQuery.isError && !isFeatureDisabled && (
        <Alert tone="error" title="Search failed. Try again." error={searchQuery.error} />
      )}

      {data && results.length === 0 && <EmptyState title="No results." />}

      {groups.map((group) => {
        const typeName = objectTypeNames.get(group.objectType) ?? group.objectType;
        return (
          <section
            key={group.objectType}
            aria-label={`${typeName} results`}
            className="space-y-3"
          >
            <h2 className="text-lg font-semibold text-ink">{typeName}</h2>
            {group.hits.map((hit) => (
              <article
                key={hit.record_id}
                data-testid={`search-result-${hit.record_key}`}
                className="space-y-1 rounded-card border border-line bg-surface p-3"
              >
                <Link
                  to={`/${hit.object_type}/${hit.record_key}`}
                  className="font-mono text-sm text-human-ink hover:underline"
                >
                  {hit.record_key}
                </Link>
                <h3 className="text-base font-semibold text-ink">{hit.title}</h3>
                <p data-testid="hit-source" className="text-xs text-ink-2">
                  {hitSourceLabel(hit, detailByType)}
                </p>
                <p
                  data-testid="snippet"
                  className={snippetClass}
                  dangerouslySetInnerHTML={{ __html: renderSnippet(hit.snippet) }}
                />
                {hit.other_matches > 0 && (
                  <Badge tone="neutral" data-testid="other-matches" className="mt-1.5">
                    +{hit.other_matches} more matches
                  </Badge>
                )}
              </article>
            ))}
          </section>
        );
      })}
    </div>
  );
}
