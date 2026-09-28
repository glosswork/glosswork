/**
 * App shell: a sidebar (docs/DESIGN.md 8.1 and 9).
 *
 * At or above 960px `Sidebar` renders a 224px column on `ground`; below it `ShellTopBar` renders
 * a top bar whose `Menu` reaches the same destinations. Exactly one of the two is in the DOM at
 * a time -- see `hooks/useIsWideViewport.ts` for why that matters more than it looks.
 *
 * `/login` is the one route reachable signed-out (`RequireAuth` redirects everything else there);
 * everything under it renders the signed-in principal through the attribution primitive
 * (`ui/Avatar.tsx`, DD-26) plus a sign-out control from `GET /api/v1/me`.
 *
 * **There is no search box in the header.** Search is a sidebar destination and the `/` key,
 * both reaching `/search`, which owns `q`/`mode`/`types`/`filter` from there on per
 * `search/searchParams.ts`. Only the entry point moved.
 */
import { Navigate, Route, Routes } from "react-router-dom";

import { ActivityPage } from "./activity/ActivityPage";
import { CsvImportWizardPage } from "./csv-import/CsvImportWizardPage";
import { InboxPage } from "./inbox/InboxPage";
import { IndexRoute } from "./routes/IndexRoute";
import { ObjectTypePage } from "./routes/ObjectTypePage";
import { RecordPage } from "./routes/RecordPage";
import { SchemaEditorIndexRoute } from "./schema-editor/SchemaEditorIndexRoute";
import { SchemaEditorPage } from "./schema-editor/SchemaEditorPage";
import { SearchPage } from "./search/SearchPage";
import { PeoplePage } from "./people/PeoplePage";
import { SetupPage } from "./setup/SetupPage";
import { LoginPage } from "./auth/LoginPage";
import { RequireAuth } from "./auth/RequireAuth";
import { useAuth } from "./auth/useAuth";
import { Sidebar } from "./app/Sidebar";
import { ShellTopBar } from "./app/ShellTopBar";
import { useSearchHotkey } from "./app/useSearchHotkey";
import { useIsWideViewport } from "./hooks/useIsWideViewport";
import { useObjectTypes } from "./hooks/useObjectTypes";
import { usePendingProposalCount } from "./hooks/usePendingProposalCount";
import { useWorkspace } from "./hooks/useWorkspace";

/**
 * `AuthProvider` wraps this from the outside (`main.tsx` for the real app,
 * `test/renderWithProviders.tsx` for tests) rather than being owned here, so every test renders
 * under exactly one provider whether it mounts `<App />` whole or a routed page in isolation.
 */
export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/*"
        element={
          <RequireAuth>
            <Shell />
          </RequireAuth>
        }
      />
    </Routes>
  );
}

function Shell() {
  const { data: objectTypes } = useObjectTypes();
  const { data: workspace } = useWorkspace();
  const { principal, logout } = useAuth();
  // Gated on the credential's own scope, so a member's session never fires the 403 that route
  // would return. `null` means "cannot read", which is not zero.
  const pendingCount = usePendingProposalCount(principal?.scope);
  const isWide = useIsWideViewport();
  useSearchHotkey();

  const shellProps = {
    workspace,
    objectTypes: objectTypes ?? [],
    principal,
    pendingCount,
    onSignOut: () => void logout(),
  };

  return (
    <div className={isWide ? "flex min-h-screen" : "flex min-h-screen flex-col"}>
      {isWide ? <Sidebar {...shellProps} /> : <ShellTopBar {...shellProps} />}
      {/* `min-w-0` is load-bearing beside a flex sibling: without it a wide table's intrinsic
          width pushes `main` past the viewport instead of scrolling inside its own card. */}
      <main className="min-w-0 flex-1 overflow-y-auto px-6 py-5">
        <Routes>
          <Route path="/" element={<IndexRoute />} />
          {/* Two routes, not one: below 960px `/inbox` is the list and
              `/inbox/:proposalId` is the detail, which is what makes the Back button return to
              the list rather than leave the Inbox (docs/DESIGN.md 9). It is also the URL the
              API's approval message hands to every agent, so it has to exist as a link a person
              can be sent. Both sit above `/:objectTypeKey`; React Router ranks static segments
              higher, so an object type keyed `inbox` is shadowed here exactly as one keyed
              `people`, `setup`, `search`, `activity` or `schema` already is. Splitting `settings`
              into two routes freed one word and claimed two, a net of one more reserved word;
              nothing in the backend reserves
              an object-type key (DD-20 reserves eight FIELD names, at
              `fieldtypes.validate_field_key`), so this comment is the only record of the set. */}
          <Route path="/inbox" element={<InboxPage />} />
          <Route path="/inbox/:proposalId" element={<InboxPage />} />
          <Route path="/schema" element={<SchemaEditorIndexRoute />} />
          <Route path="/schema/new" element={<SchemaEditorPage />} />
          <Route path="/schema/:objectTypeKey" element={<SchemaEditorPage />} />
          <Route path="/activity" element={<ActivityPage />} />
          {/* The screen was renamed. The old URL keeps working for the reason `/settings` does:
              it is in this repository's own comments and in whatever anyone bookmarked.
              `replace`, so Back leaves rather than bouncing through the redirect. */}
          <Route path="/audit" element={<Navigate to="/activity" replace />} />
          <Route path="/people" element={<PeoplePage />} />
          <Route path="/setup" element={<SetupPage />} />
          {/* `/settings` was split in two. The old URL keeps working because it is in
              this repository's own comments, and in whatever anyone
              bookmarked. `replace`, not a push: a pushed redirect leaves `/settings` on the
              history stack, so Back from `/people` lands on it, redirects forward again, and the
              button stops working. */}
          <Route path="/settings" element={<Navigate to="/people" replace />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/:objectTypeKey/import" element={<CsvImportWizardPage />} />
          <Route path="/:objectTypeKey" element={<ObjectTypePage />} />
          <Route path="/:objectTypeKey/:recordKey" element={<RecordPage />} />
        </Routes>
      </main>
    </div>
  );
}
