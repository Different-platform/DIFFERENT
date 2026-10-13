import { ChevronDown } from "lucide-react";
import { useState } from "react";

import { cn } from "@/lib/utils";
import type { Citation } from "@/lib/differ/types";

export function CitationCard({ citation }: { citation: Citation }) {
  const [open, setOpen] = useState(false);
  const hasDetail = Boolean(citation.description || citation.question_text);

  return (
    <div className="rounded-md border border-border bg-card text-card-foreground">
      <button
        type="button"
        onClick={() => hasDetail && setOpen((v) => !v)}
        className={cn(
          "flex w-full items-center justify-between gap-3 px-3 py-2 text-left",
          hasDetail && "cursor-pointer",
        )}
        aria-expanded={open}
      >
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <span className="font-mono text-sm font-semibold text-primary">
            {citation.variable_name}
          </span>
          <span className="text-xs text-muted-foreground">{citation.section}</span>
          <span className="text-xs text-muted-foreground">·</span>
          <span className="text-xs text-muted-foreground">p. {citation.page}</span>
          <span className="text-xs text-muted-foreground">·</span>
          <span className="text-xs text-muted-foreground">{citation.survey_year}</span>
        </div>
        {hasDetail && (
          <ChevronDown
            className={cn(
              "size-4 shrink-0 text-muted-foreground transition-transform",
              open && "rotate-180",
            )}
          />
        )}
      </button>
      {open && hasDetail && (
        <div className="space-y-2 border-t border-border px-3 py-2 text-xs">
          {citation.description && (
            <p className="text-foreground">{citation.description}</p>
          )}
          {citation.question_text && (
            <p className="font-mono text-muted-foreground">
              {citation.question_text}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
