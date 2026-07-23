import { useCallback, useEffect, useState } from "react";
import type { View } from "./components/Sidebar";

/**
 * Path-based navigation without a router dependency.
 *
 * Five flat routes, no params and no nesting, so react-router would add a
 * package and a provider tree to do what `history.pushState` already does. The
 * part worth having is not the state change — the app managed that fine — but
 * the URL itself: a workspace can be linked to, reopened after a refresh, and
 * reached with back/forward.
 *
 * Paths follow the labels in the rail rather than the internal view names,
 * which are historical: `tasks` is "Action Proposals" and `actions` is
 * "Approvals". A URL is user-facing, so it reads the way the nav does.
 */
export const VIEW_PATHS: Record<View, string> = {
  dashboard: "/",
  insights: "/insights",
  tasks: "/proposals",
  actions: "/approvals",
  tickets: "/tickets",
  emails: "/emails",
  trace: "/trace",
  eval: "/eval",
};

const PATH_VIEWS = new Map<string, View>(
  (Object.entries(VIEW_PATHS) as [View, string][]).map(([view, path]) => [path, view]),
);

/** Unknown paths fall back to the dashboard rather than rendering nothing. */
export function viewFromPath(pathname: string): View {
  const normalised =
    pathname.length > 1 && pathname.endsWith("/") ? pathname.slice(0, -1) : pathname;
  return PATH_VIEWS.get(normalised) ?? "dashboard";
}

export function useRoute(): [View, (view: View) => void] {
  const [view, setView] = useState<View>(() => viewFromPath(window.location.pathname));

  // Back and forward change the URL without going through navigate(), so the
  // view has to follow the address bar, not only drive it.
  useEffect(() => {
    const onPopState = () => setView(viewFromPath(window.location.pathname));
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const navigate = useCallback((next: View) => {
    const path = VIEW_PATHS[next];
    if (window.location.pathname !== path) {
      window.history.pushState(null, "", path);
    }
    setView(next);
  }, []);

  return [view, navigate];
}
