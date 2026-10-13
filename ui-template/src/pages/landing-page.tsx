import { BookOpen, ShieldCheck, Calendar } from "lucide-react";
import { useEffect } from "react";
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";

const FEATURES = [
  {
    icon: BookOpen,
    title: "Codebook answers with citations",
    body: "Every answer about what NHIS measures points back to the exact variable, section, and codebook page.",
  },
  {
    icon: ShieldCheck,
    title: "Verified statistics only",
    body: "Generated SQL is checked against the real codebook codes before a single number reaches you. If it fails, you see why — never a guess.",
  },
  {
    icon: Calendar,
    title: "Weighted estimates, 2017–2023",
    body: "Descriptive statistics use the survey's final annual weight and design variables across the full available year range.",
  },
];

export function LandingPage() {
  useEffect(() => {
    document.title = "DIFFER — NHIS research console";
  }, []);

  return (
    <div className="flex min-h-screen flex-col">
      <header className="flex items-center justify-between border-b border-border px-6 py-4">
        <span className="font-mono text-lg font-semibold text-foreground">DIFFER</span>
        <Button asChild>
          <Link to="/chat">Open the console</Link>
        </Button>
      </header>

      <main className="flex flex-1 flex-col items-center justify-center px-6 py-20">
        <div className="mx-auto max-w-2xl text-center">
          <p className="mb-3 text-xs font-semibold uppercase tracking-widest text-primary">
            NHIS research console
          </p>
          <h1 className="text-balance text-4xl font-semibold leading-tight text-foreground sm:text-5xl">
            Ask questions about health data. Get answers you can trust.
          </h1>
          <p className="mx-auto mt-5 max-w-xl text-balance text-base text-muted-foreground">
            DIFFER answers plain-language questions about the National Health Interview
            Survey — grounded in the codebook, verified before any number is shown.
          </p>
          <Button asChild size="lg" className="mt-8">
            <Link to="/chat">Start asking</Link>
          </Button>
        </div>

        <div className="mx-auto mt-20 grid max-w-4xl gap-5 sm:grid-cols-3">
          {FEATURES.map((feature) => (
            <div
              key={feature.title}
              className="rounded-lg border border-border bg-card p-5 text-left"
            >
              <feature.icon className="mb-3 size-5 text-primary" />
              <h3 className="text-sm font-semibold text-foreground">{feature.title}</h3>
              <p className="mt-1.5 text-sm text-muted-foreground">{feature.body}</p>
            </div>
          ))}
        </div>
      </main>

      <footer className="border-t border-border px-6 py-5 text-center text-xs text-muted-foreground">
        DIFFER — National Health Interview Survey, 2017–2023 public-use data.
      </footer>
    </div>
  );
}
