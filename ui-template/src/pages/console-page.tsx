import { ChevronDown, Menu, Send } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { ConversationSidebar } from "@/components/differ/conversation-sidebar";
import { EmptyState } from "@/components/differ/empty-state";
import { MessageBubble } from "@/components/differ/message-bubble";
import { RateLimitIndicator, RATE_LIMIT_MAX } from "@/components/differ/rate-limit-indicator";
import { YearSelector } from "@/components/differ/year-selector";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import { MOCK_CONVERSATIONS } from "@/lib/differ/mock-data";
import { mockAsk } from "@/lib/differ/mock-stream";
import type { Conversation, Message } from "@/lib/differ/types";

function newId(prefix: string) {
  return `${prefix}-${Math.random().toString(36).slice(2, 9)}`;
}

function createBlankConversation(defaultYear: number | null): Conversation {
  return {
    id: newId("conv"),
    title: "New conversation",
    default_year: defaultYear,
    created_at: new Date().toISOString(),
    messages: [],
  };
}

export function ConsolePage() {
  const [conversations, setConversations] = useState<Conversation[]>(() => [
    ...MOCK_CONVERSATIONS,
  ]);
  const [activeId, setActiveId] = useState<string>(MOCK_CONVERSATIONS[0].id);
  const [selectedYear, setSelectedYear] = useState<number | null>(
    MOCK_CONVERSATIONS[0].default_year,
  );
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [rateLimitUsed, setRateLimitUsed] = useState(0);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);

  const threadEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    document.title = "DIFFER — Console";
  }, []);

  const activeConversation = conversations.find((c) => c.id === activeId) ?? null;
  const lastMessageContent = activeConversation?.messages.at(-1)?.content;
  const messageCount = activeConversation?.messages.length;

  useEffect(() => {
    threadEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messageCount, lastMessageContent]);

  function updateConversationMessages(
    conversationId: string,
    updater: (messages: Message[]) => Message[],
  ) {
    setConversations((prev) =>
      prev.map((c) =>
        c.id === conversationId ? { ...c, messages: updater(c.messages) } : c,
      ),
    );
  }

  function selectConversation(id: string) {
    setActiveId(id);
    const conv = conversations.find((c) => c.id === id);
    if (conv) setSelectedYear(conv.default_year);
    setMobileSidebarOpen(false);
  }

  function handleNewConversation() {
    const blank = createBlankConversation(selectedYear);
    setConversations((prev) => [blank, ...prev]);
    setActiveId(blank.id);
    setMobileSidebarOpen(false);
  }

  async function runQuestion(conversationId: string, question: string) {
    if (rateLimitUsed >= RATE_LIMIT_MAX) return;

    const userMessage: Message = {
      id: newId("msg"),
      role: "user",
      content: question,
    };
    const assistantMessageId = newId("msg");
    const assistantMessage: Message = {
      id: assistantMessageId,
      role: "assistant",
      content: "",
      phase: "routing",
    };

    updateConversationMessages(conversationId, (messages) => [
      ...messages,
      userMessage,
      assistantMessage,
    ]);
    setRateLimitUsed((n) => n + 1);
    setIsStreaming(true);

    function patchAssistant(patch: Partial<Message>) {
      updateConversationMessages(conversationId, (messages) =>
        messages.map((m) =>
          m.id === assistantMessageId ? { ...m, ...patch } : m,
        ),
      );
    }

    let streamedContent = "";

    for await (const event of mockAsk(question)) {
      switch (event.type) {
        case "route":
          patchAssistant({ route: event.route, phase: "routing" });
          break;
        case "verdict":
          patchAssistant({ verdict: event.verdict });
          break;
        case "citations":
          patchAssistant({ citations: event.citations });
          break;
        case "sql":
          patchAssistant({ sql: event.sql });
          break;
        case "result":
          patchAssistant({ result: event.result });
          break;
        case "token":
          streamedContent += event.text;
          patchAssistant({ content: streamedContent, phase: "streaming" });
          break;
        case "done":
          patchAssistant({ phase: "done" });
          break;
      }
    }

    setIsStreaming(false);
  }

  function handleSubmit() {
    const question = input.trim();
    if (!question || isStreaming) return;
    setInput("");
    void runQuestion(activeId, question);
  }

  function handleStatePick(conversationId: string) {
    setActiveId(conversationId);
    const conv = conversations.find((c) => c.id === conversationId);
    if (conv) setSelectedYear(conv.default_year);
  }

  function handlePlayStreamingReply() {
    void runQuestion(activeId, "What's the weighted mean BMI for adults in 2023?");
  }

  function handleTriggerRateLimit() {
    setRateLimitUsed(RATE_LIMIT_MAX);
  }

  const rateLimited = rateLimitUsed >= RATE_LIMIT_MAX;

  const sidebarPanel = (
    <ConversationSidebar
      conversations={conversations}
      activeId={activeId}
      onSelect={selectConversation}
      onNewConversation={handleNewConversation}
    />
  );

  return (
    <div className="flex h-screen flex-col">
      <header className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            size="icon"
            className="lg:hidden"
            onClick={() => setMobileSidebarOpen(true)}
            aria-label="Open conversations"
          >
            <Menu className="size-4" />
          </Button>
          <Link to="/" className="font-mono text-base font-semibold text-foreground">
            DIFFER
          </Link>
          <YearSelector value={selectedYear} onChange={setSelectedYear} />
        </div>
        <Button onClick={handleNewConversation} size="sm" variant="outline">
          + New conversation
        </Button>
      </header>

      <div className="flex flex-1 overflow-hidden">
        <aside className="hidden w-[280px] shrink-0 border-r border-sidebar-border lg:block">
          {sidebarPanel}
        </aside>

        <Sheet open={mobileSidebarOpen} onOpenChange={setMobileSidebarOpen}>
          <SheetContent>{sidebarPanel}</SheetContent>
        </Sheet>

        <main className="flex flex-1 flex-col overflow-hidden">
          {!activeConversation || activeConversation.messages.length === 0 ? (
            <EmptyState onPick={(q) => void runQuestion(activeId, q)} />
          ) : (
            <div className="flex-1 overflow-y-auto px-4 py-6">
              <div className="mx-auto flex max-w-3xl flex-col gap-6">
                {activeConversation.messages.map((message) => (
                  <MessageBubble key={message.id} message={message} />
                ))}
                <div ref={threadEndRef} />
              </div>
            </div>
          )}

          <div className="border-t border-border px-4 py-3">
            <div className="mx-auto flex max-w-3xl flex-col gap-2">
              <div className="flex items-center justify-between">
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="ghost" size="sm" className="text-muted-foreground">
                      States
                      <ChevronDown className="size-3.5" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="start">
                    <DropdownMenuLabel>Demo conversations</DropdownMenuLabel>
                    {MOCK_CONVERSATIONS.map((c) => (
                      <DropdownMenuItem key={c.id} onSelect={() => handleStatePick(c.id)}>
                        {c.title}
                      </DropdownMenuItem>
                    ))}
                    <DropdownMenuSeparator />
                    <DropdownMenuItem onSelect={handleNewConversation}>
                      Blank conversation
                    </DropdownMenuItem>
                    <DropdownMenuItem onSelect={handlePlayStreamingReply}>
                      Play streaming reply
                    </DropdownMenuItem>
                    <DropdownMenuItem onSelect={handleTriggerRateLimit}>
                      Trigger rate limit
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
                <RateLimitIndicator used={rateLimitUsed} />
              </div>

              {rateLimited ? (
                <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2.5 text-xs text-destructive">
                  You've reached the limit of {RATE_LIMIT_MAX} questions this hour. The
                  input is locked until the window resets.
                </div>
              ) : (
                <div className="flex items-end gap-2">
                  <Textarea
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        handleSubmit();
                      }
                    }}
                    placeholder="Ask a question about NHIS…"
                    className="min-h-11"
                    disabled={isStreaming}
                  />
                  <Button
                    onClick={handleSubmit}
                    disabled={isStreaming || !input.trim()}
                    size="icon"
                    aria-label="Send"
                  >
                    <Send className="size-4" />
                  </Button>
                </div>
              )}
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
