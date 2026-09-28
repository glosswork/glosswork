import { Navigate } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import { useObjectTypes } from "../hooks/useObjectTypes";
import { resolveIndexRoute } from "./resolveIndexRoute";
import { EmptyState } from "../ui/EmptyState";
import { FirstRun } from "./FirstRun";
import { Spinner } from "../ui/Spinner";

/**
 * Route component for `/`: redirects to the first live object type's table route, or renders an
 * empty state when no object type exists yet. The redirect decision itself is
 * `resolveIndexRoute`, a pure function tested on its own.
 *
 * The empty state cannot say "No object types have been created yet." unconditionally:
 * `list_object_types` is filtered to types the caller holds `read` on (FR-I11) and every object
 * type is closed by default, so a `member` with no grants on a deployment full of object types
 * would be told none existed. `resolveIndexRoute` cannot tell the two apart, because "none exist"
 * and "none you can see" are the same empty array; the role can.
 *
 * Everyone but a member gets the first-run screen (docs/DESIGN.md 8.5), and the role branch is
 * load-bearing for a second reason: first run hands the reader a prompt that builds a schema,
 * and a `member` cannot build one. A member reading "paste this into your agent" would be told
 * to do something their token would be refused for, which is worse than being told none
 * exist.
 */
export function IndexRoute() {
  const { data: objectTypes, isLoading } = useObjectTypes();
  const { principal } = useAuth();

  if (isLoading) {
    return <Spinner />;
  }

  const target = resolveIndexRoute(objectTypes ?? []);
  if (target) {
    return <Navigate to={target} replace />;
  }

  // A `creator` with no grants reaches first run too, and for it the screen is simply right:
  // setting up a workspace is the thing a creator is for. This accepts a small inaccuracy
  // rather than engineering around it, because distinguishing "none exist" from "none you can
  // see" needs a count of the types the caller cannot see, which is a backend change that leaks
  // the existence of exactly what it is hiding.
  if (principal?.role === "member") {
    return (
      <EmptyState title="You do not have access to any object types yet. Ask an administrator to grant you access." />
    );
  }
  return <FirstRun />;
}
