"use client";

import { ChatPanel } from "./chat-panel";
import { useResearchChat } from "./use-research-chat";

export function ChatWorkspace({ sessionId }: { sessionId: string }) {
  const chat = useResearchChat(sessionId);

  return (
    <>
      {chat.error && (
        <p
          role="alert"
          className="mb-4 rounded-xl bg-red-50 px-4 py-3 text-red-800"
        >
          {chat.error}
        </p>
      )}
      <div className="mb-4 flex flex-wrap gap-3">
        {chat.hasOlder && (
          <button
            disabled={chat.loadingOlder}
            onClick={() => void chat.loadOlder()}
            className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm"
          >
            {chat.loadingOlder ? "加载中…" : "加载更早消息"}
          </button>
        )}
        {chat.canResume && (
          <button
            disabled={chat.busy}
            onClick={() => void chat.resume()}
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm text-white"
          >
            恢复发送或连接
          </button>
        )}
        {chat.canRetry && (
          <button
            disabled={chat.busy}
            onClick={() => void chat.retry()}
            className="rounded-lg border border-blue-300 bg-white px-4 py-2 text-sm text-blue-800"
          >
            重试失败任务
          </button>
        )}
      </div>
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
