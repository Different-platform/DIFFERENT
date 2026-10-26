"use client";

import { useEffect, useRef, useState } from "react";
import { ask, type StreamEvent } from "@/lib/differ/stream";
import { SURVEY_YEARS, type Message } from "@/lib/differ/types";

const EXAMPLES = [
  "How is food insecurity measured?",
  "What is the average age of adults?",
  "What percentage of adults own a firearm?",
  "Does NHIS measure loneliness?",
];

interface HealthInfo {
  status: string;
  llm_model?: string;
  survey_years_loaded?: number[];
}

export default function Home() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [year, setYear] = useState<number>(2023);
  const [busy, setBusy] = useState(false);
  const [health, setHealth] = useState<HealthInfo | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetch("/api/health")
      .then((r) => r.json())
      .then(setHealth)
      .catch(() => setHealth({ status: "unreachable" }));
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function submit(question: string) {
    if (!question.trim() || busy) return;
    setBusy(true);
    setInput("");

    const userMsg: Message = {
      id: crypto.randomUUID(),
      role: "user",
      content: question,
    };
    const assistantId = crypto.randomUUID();
    const assistant: Message = {
      id: assistantId,
      role: "assistant",
      content: "",
      phase: "routing",
    };
    setMessages((m) => [...m, userMsg, assistant]);

    const patch = (fn: (prev: Message) => Message) =>
      setMessages((m) => m.map((msg) => (msg.id === assistantId ? fn(msg) : msg)));

    try {
      for await (const ev of ask({ question, year })) {
        applyEvent(ev, patch);
      }
    } catch (err) {
      patch((prev) => ({ ...prev, error: String(err), phase: "done" }));
    } finally {
      patch((prev) => ({ ...prev, phase: "done" }));
      setBusy(false);
    }
  }

  return (
    <div className="wrap">
      <h1>DIFFER</h1>
      <p className="sub">
        Codebook Q&amp;A and verified descriptive statistics over NHIS public-use data.
      </p>

      <div className="status">
        <span className={`dot ${health?.status === "ok" ? "ok" : ""}`} />
        {health
          ? health.status === "ok"
            ? `backend up · model ${health.llm_model} · years ${(health.survey_years_loaded ?? []).join(", ")}`
            : `backend ${health.status}`
          : "checking backend…"}
      </div>

      {messages.length === 0 && (
        <div style={{ marginBottom: 20 }}>
          {EXAMPLES.map((ex) => (
            <div
              key={ex}
              className="citation"
              style={{ cursor: "pointer" }}
              onClick={() => submit(ex)}
            >
              {ex}
            </div>
          ))}
        </div>
      )}

      {messages.map((m) =>
        m.role === "user" ? (
          <div key={m.id} className="msg user">
            <span className="q">{m.content}</span>
          </div>
        ) : (
          <AssistantMessage key={m.id} m={m} />
        ),
      )}
      <div ref={bottomRef} />

      <form
        className="row"
        style={{ position: "fixed", bottom: 0, left: 0, right: 0, background: "var(--bg)", padding: "16px 20px", maxWidth: 820, margin: "0 auto" }}
        onSubmit={(e) => {
          e.preventDefault();
          submit(input);
        }}
      >
        <select value={year} onChange={(e) => setYear(Number(e.target.value))}>
          {SURVEY_YEARS.map((y) => (
            <option key={y} value={y}>
              {y}
            </option>
          ))}
        </select>
        <input
          type="text"
          placeholder="Ask about what NHIS measures, or for a weighted statistic…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={busy}
        />
        <button type="submit" disabled={busy || !input.trim()}>
          {busy ? "…" : "Ask"}
        </button>
      </form>
    </div>
  );
}

function applyEvent(ev: StreamEvent, patch: (fn: (p: Message) => Message) => void) {
  switch (ev.type) {
    case "route":
      patch((p) => ({ ...p, route: ev.route }));
      break;
    case "verdict":
      patch((p) => ({ ...p, verdict: ev.verdict, phase: "streaming" }));
      break;
    case "citations":
      patch((p) => ({ ...p, citations: ev.citations }));
      break;
    case "sql":
      patch((p) => ({ ...p, sql: ev.sql }));
      break;
    case "result":
      patch((p) => ({ ...p, result: ev.result }));
      break;
    case "token":
      patch((p) => ({ ...p, content: p.content + ev.text }));
      break;
    case "error":
      patch((p) => ({ ...p, error: ev.message }));
      break;
    case "done":
      patch((p) => ({ ...p, phase: "done" }));
      break;
  }
}

function AssistantMessage({ m }: { m: Message }) {
  return (
    <div className="msg">
      {m.route && <span className="pill route">{m.route}</span>}
      {m.verdict && <span className={`pill ${m.verdict}`}>{m.verdict}</span>}

      {m.citations?.map((c) => (
        <div className="citation" key={`${c.variable_name}-${c.survey_year}`}>
          <span className="name">{c.variable_name}</span>{" "}
          <span className="meta">
            · {c.section} · p.{c.page} · {c.survey_year}
          </span>
          {c.description && <div>{c.description}</div>}
        </div>
      ))}

      {m.sql && (
        <div>
          <div className={`verify-tag ${m.sql.verified ? "ok" : "bad"}`}>
            {m.sql.verified ? "✓ verified against the codebook" : "✗ failed verification — no number shown"}
          </div>
          <div className={`sql ${m.sql.verified ? "verified" : "unverified"}`}>{m.sql.sql}</div>
          {m.sql.problems.map((p, i) => (
            <div className="problem" key={i}>
              • {p}
            </div>
          ))}
        </div>
      )}

      {m.result && (
        <table>
          <thead>
            <tr>
              {m.result.columns.map((c) => (
                <th key={c}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {m.result.rows.map((row, i) => (
              <tr key={i}>
                {row.map((cell, j) => (
                  <td key={j}>{String(cell)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {m.content && <div className="answer">{m.content}</div>}
      {m.error && <div className="err">⚠ {m.error}</div>}
      {m.phase === "routing" && !m.route && <div className="meta">thinking…</div>}
    </div>
  );
}
