import { CitationCard } from "@/components/differ/citation-card";
import { ResultCard } from "@/components/differ/result-card";
import { RouteIndicator } from "@/components/differ/route-indicator";
import { SqlPanel } from "@/components/differ/sql-panel";
import { VerdictBadge } from "@/components/differ/verdict-badge";
import { cn } from "@/lib/utils";
import type { Message } from "@/lib/differ/types";

export function MessageBubble({ message }: { message: Message }) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[75%] rounded-lg bg-secondary px-4 py-2.5 text-sm text-secondary-foreground">
          {message.content}
        </div>
      </div>
    );
  }

  const isStreaming = message.phase === "routing" || message.phase === "streaming";

  return (
    <div className="flex flex-col gap-2.5">
      {message.route && (
        <RouteIndicator route={message.route} resolved={message.phase !== "routing"} />
      )}

      {message.verdict && <VerdictBadge verdict={message.verdict} />}

      {message.citations && message.citations.length > 0 && (
        <div className="flex flex-col gap-2">
          {message.citations.map((citation) => (
            <CitationCard key={citation.variable_name} citation={citation} />
          ))}
        </div>
      )}

      {message.sql && <SqlPanel sql={message.sql} />}

      {message.result && <ResultCard result={message.result} />}

      {message.content && (
        <p
          className={cn(
            "max-w-[85ch] text-sm leading-relaxed text-foreground",
            isStreaming && message.phase === "streaming" && "typing-cursor",
          )}
        >
          {message.content}
        </p>
      )}
    </div>
  );
}
