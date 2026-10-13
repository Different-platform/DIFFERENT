import { AlertTriangle, CheckCircle2, ChevronDown } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { SqlResult } from "@/lib/differ/types";

/** Minimal, dependency-free SQL token highlighting for a research-tool feel. */
function highlightSql(sql: string): string {
  const escaped = sql
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");

  const keywordPattern =
    /\b(SELECT|FROM|WHERE|AND|OR|NOT|IN|GROUP BY|AS|SUM|CASE|WHEN|THEN|ELSE|END|IS|NULL)\b/gi;

  return escaped
    .replace(/(--.*$)/gm, '<span class="text-muted-foreground">$1</span>')
    .replace(
      keywordPattern,
      '<span class="text-primary font-semibold">$1</span>',
    )
    .replace(/(\b\d+(\.\d+)?\b)/g, '<span class="text-foreground/70">$1</span>');
}

export function SqlPanel({ sql }: { sql: SqlResult }) {
  const [open, setOpen] = useState(true);

  return (
    <div className="rounded-md border border-border bg-card">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-3 px-3 py-2"
        aria-expanded={open}
      >
        <span className="text-xs font-medium text-muted-foreground">
          Generated SQL
        </span>
        <div className="flex items-center gap-2">
          {sql.verified ? (
            <Badge variant="success">
              <CheckCircle2 className="size-3" />
              Verified
            </Badge>
          ) : (
            <Badge variant="destructive">
              <AlertTriangle className="size-3" />
              Not verified
            </Badge>
          )}
          <ChevronDown
            className={cn(
              "size-4 text-muted-foreground transition-transform",
              open && "rotate-180",
            )}
          />
        </div>
      </button>
      {open && (
        <div className="border-t border-border">
          <pre className="overflow-x-auto px-3 py-3 text-xs leading-relaxed">
            <code
              className="font-mono"
              // eslint-disable-next-line react/no-danger -- static, locally-generated highlighting only
              dangerouslySetInnerHTML={{ __html: highlightSql(sql.sql) }}
            />
          </pre>
          {!sql.verified && sql.problems.length > 0 && (
            <div className="space-y-1.5 border-t border-border bg-destructive/5 px-3 py-2.5">
              <p className="text-xs font-medium text-destructive">
                Verification problems
              </p>
              <ul className="list-inside list-disc space-y-1 text-xs text-destructive/90">
                {sql.problems.map((problem) => (
                  <li key={problem}>{problem}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
