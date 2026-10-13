import { AlertTriangle, CheckCircle2, HelpCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { Verdict } from "@/lib/differ/types";

const VERDICT_CONFIG: Record<
  Verdict,
  { label: string; variant: "success" | "warning" | "muted"; icon: typeof CheckCircle2 }
> = {
  MEASURED: { label: "MEASURED", variant: "success", icon: CheckCircle2 },
  RELATED: { label: "RELATED", variant: "warning", icon: AlertTriangle },
  ABSENT: { label: "ABSENT · Not measured in NHIS", variant: "muted", icon: HelpCircle },
};

export function VerdictBadge({ verdict }: { verdict: Verdict }) {
  const config = VERDICT_CONFIG[verdict];
  const Icon = config.icon;
  return (
    <Badge variant={config.variant}>
      <Icon className="size-3" />
      {config.label}
    </Badge>
  );
}
