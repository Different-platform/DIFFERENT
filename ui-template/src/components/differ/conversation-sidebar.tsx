import { Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { formatRelativeDate } from "@/lib/differ/format";
import { cn } from "@/lib/utils";
import type { Conversation } from "@/lib/differ/types";

export function ConversationSidebar({
  conversations,
  activeId,
  onSelect,
  onNewConversation,
}: {
  conversations: Conversation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNewConversation: () => void;
}) {
  return (
    <div className="flex h-full flex-col bg-sidebar text-sidebar-foreground">
      <div className="flex items-center justify-between border-b border-sidebar-border px-3 py-3">
        <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Conversations
        </span>
        <Button
          variant="ghost"
          size="icon"
          onClick={onNewConversation}
          aria-label="New conversation"
          className="size-7"
        >
          <Plus className="size-4" />
        </Button>
      </div>
      <nav className="flex-1 overflow-y-auto p-2">
        <ul className="flex flex-col gap-0.5">
          {conversations.map((conversation) => (
            <li key={conversation.id}>
              <button
                type="button"
                onClick={() => onSelect(conversation.id)}
                className={cn(
                  "flex w-full flex-col items-start gap-0.5 rounded-md px-2.5 py-2 text-left transition-colors hover:bg-sidebar-accent",
                  activeId === conversation.id && "bg-sidebar-accent",
                )}
              >
                <span className="w-full truncate text-sm">{conversation.title}</span>
                <span className="text-xs text-muted-foreground">
                  {formatRelativeDate(conversation.created_at)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </nav>
    </div>
  );
}
