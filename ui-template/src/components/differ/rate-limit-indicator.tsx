import { AlertTriangle } from "lucide-react";

import { cn } from "@/lib/utils";

const RATE_LIMIT_MAX = 50;

export function RateLimitIndicator({ used }: { used: number }) {
  const exceeded = used >= RATE_LIMIT_MAX;
  const nearLimit = used >= RATE_LIMIT_MAX * 0.8;

  return (
    <div
      className={cn(
        "flex items-center gap-1.5 text-xs",
        exceeded
          ? "text-destructive"
          : nearLimit
            ? "text-warning"
            : "text-muted-foreground",
      )}
    >
      {exceeded && <AlertTriangle className="size-3" />}
      <span className="font-mono">
        {Math.min(used, RATE_LIMIT_MAX)} of {RATE_LIMIT_MAX} requests this hour
      </span>
    </div>
  );
}

export { RATE_LIMIT_MAX };
