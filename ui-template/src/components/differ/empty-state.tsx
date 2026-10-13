import { EMPTY_EXAMPLE_QUESTIONS } from "@/lib/differ/mock-data";

export function EmptyState({ onPick }: { onPick: (question: string) => void }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center px-6 py-16 text-center">
      <h2 className="text-lg font-semibold text-foreground">
        Ask DIFFER about NHIS
      </h2>
      <p className="mt-2 max-w-md text-sm text-muted-foreground">
        You can ask about a variable — what it measures, how it's worded — or ask for a
        verified statistic computed from the survey data.
      </p>
      <div className="mt-6 flex max-w-lg flex-col gap-2">
        {EMPTY_EXAMPLE_QUESTIONS.map((question) => (
          <button
            key={question}
            type="button"
            onClick={() => onPick(question)}
            className="rounded-md border border-border bg-card px-4 py-2.5 text-left text-sm text-foreground transition-colors hover:border-primary/40 hover:bg-accent"
          >
            {question}
          </button>
        ))}
      </div>
    </div>
  );
}
