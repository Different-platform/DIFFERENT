import type { NumericResult } from "@/lib/differ/types";

export function ResultCard({ result }: { result: NumericResult }) {
  return (
    <div className="overflow-hidden rounded-md border border-border bg-card">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border bg-muted/60">
            {result.columns.map((col) => (
              <th
                key={col}
                className="px-3 py-2 text-left text-xs font-medium text-muted-foreground"
              >
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {result.rows.map((row, i) => (
            <tr key={i} className="border-b border-border last:border-0">
              {row.map((cell, j) => (
                <td
                  key={j}
                  className={
                    j === 0
                      ? "px-3 py-2 text-foreground"
                      : "px-3 py-2 font-mono font-medium text-foreground"
                  }
                >
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="border-t border-border bg-muted/40 px-3 py-1.5 text-xs text-muted-foreground">
        Weight variable: <span className="font-mono">{result.weight_variable}</span>
      </div>
    </div>
  );
}
