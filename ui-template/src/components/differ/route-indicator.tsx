import { Loader2 } from "lucide-react";

import type { RouteIntent } from "@/lib/differ/types";

const ROUTE_LABEL: Record<RouteIntent, string> = {
  codebook: "Checking codebook…",
  data: "Querying data…",
  both: "Checking codebook and data…",
};

export function RouteIndicator({
  route,
  resolved,
}: {
  route: RouteIntent;
  /** When true, render as a settled, muted line rather than an active spinner. */
  resolved?: boolean;
}) {
  return (
    <div className="flex items-center gap-2 text-xs text-muted-foreground">
      {!resolved && <Loader2 className="size-3 animate-spin" />}
      <span className="font-mono">{ROUTE_LABEL[route]}</span>
    </div>
  );
}
