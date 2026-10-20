import type { QueryClient } from "@tanstack/react-query";
import { createRootRouteWithContext, createRoute, createRouter, lazyRouteComponent } from "@tanstack/react-router";
import { Layout, NotFound } from "@/routes/layout";
import { validateOpeningsSearch, validateOverviewSearch } from "@/lib/search";

// Code-based routes: two pages, each its own chunk (ECharts loads with the overview);
// filter state lives in the URL's search params.

const rootRoute = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  component: Layout,
  notFoundComponent: NotFound,
});

const overviewRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  validateSearch: validateOverviewSearch,
  component: lazyRouteComponent(() => import("@/routes/overview"), "OverviewPage"),
});

const openingsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/openings",
  validateSearch: validateOpeningsSearch,
  component: lazyRouteComponent(() => import("@/routes/openings"), "OpeningsPage"),
});

const routeTree = rootRoute.addChildren([overviewRoute, openingsRoute]);

export function makeRouter(queryClient: QueryClient) {
  return createRouter({
    routeTree,
    context: { queryClient },
    defaultPreload: "intent",
    scrollRestoration: true,
  });
}

declare module "@tanstack/react-router" {
  interface Register {
    router: ReturnType<typeof makeRouter>;
  }
}
