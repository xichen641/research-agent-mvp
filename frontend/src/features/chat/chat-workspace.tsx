"use client";

import { ChatPanel } from "./chat-panel";
import { useResearchChat } from "./use-research-chat";

export function ChatWorkspace({ sessionId }: { sessionId: string }) {
  const chat = useResearchChat(sessionId);

  return (
    <>
      {chat.error && (
        <p role="alert" className="mb-4 rounded-xl bg-red-50 px-4 py-3 text-red-800">
          {chat.error}
        </p>
      )}
      <ChatPanel
        messages={chat.messages}
        workflow={chat.workflow}
        warnings={chat.warnings}
        busy={chat.busy}
        onSubmit={chat.send}
      />
    </>
  );
}
