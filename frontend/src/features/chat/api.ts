import { apiRequest } from "@/lib/api";

import type { ChatMessage, CitationData } from "./types";


type ChatSessionWire = {
  id: string;
  title: string;
  document_ids: string[];
};

export type CreatedChatSession = {
  id: string;
  title: string;
  documentIds: string[];
};

export type ChatSessionSummary = CreatedChatSession & {
  createdAt: string;
  updatedAt: string;
};

export type ChatRunStatus = "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED";

export type ChatSessionActivity = {
  sessionId: string;
  runId: string | null;
  status: ChatRunStatus | "IDLE";
  errorCode: string | null;
  streamUrl: string | null;
};

export async function createChatSession(
  title: string,
  documentIds: string[],
): Promise<CreatedChatSession> {
  const session = await apiRequest<ChatSessionWire>("/api/v1/chat/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, document_ids: documentIds }),
  });
  return {
    id: session.id,
    title: session.title,
    documentIds: session.document_ids,
  };
}

type ChatSessionListWire = {
  items: Array<ChatSessionWire & {
    created_at: string;
    updated_at: string;
  }>;
};

export async function listChatSessions(): Promise<ChatSessionSummary[]> {
  const result = await apiRequest<ChatSessionListWire>("/api/v1/chat/sessions");
  return result.items.map((session) => ({
    id: session.id,
    title: session.title,
    documentIds: session.document_ids,
    createdAt: session.created_at,
    updatedAt: session.updated_at,
  }));
}

type ChatHistoryMessageWire = {
  id: string;
  role: "user" | "assistant";
  content: { text: string } | CitationData;
  created_at: string;
};

type ChatHistoryWire = {
  items: ChatHistoryMessageWire[];
};

export async function listChatMessages(sessionId: string): Promise<ChatMessage[]> {
  const history = await apiRequest<ChatHistoryWire>(
    `/api/v1/chat/sessions/${sessionId}/messages`,
  );
  return history.items.map((item) => {
    if (item.role === "user") {
      return {
        id: item.id,
        role: "user",
        text: "text" in item.content ? item.content.text : "",
      };
    }
    const content = item.content as CitationData;
    return {
      id: item.id,
      role: "assistant",
      blocks: content.blocks,
      citations: content.citations,
      streaming: false,
    };
  });
}

type ChatSessionActivityWire = {
  session_id: string;
  run_id: string | null;
  status: ChatRunStatus | "IDLE";
  error_code: string | null;
  stream_url: string | null;
};

export async function getChatSessionActivity(
  sessionId: string,
): Promise<ChatSessionActivity> {
  const activity = await apiRequest<ChatSessionActivityWire>(
    `/api/v1/chat/sessions/${sessionId}/activity`,
  );
  return {
    sessionId: activity.session_id,
    runId: activity.run_id,
    status: activity.status,
    errorCode: activity.error_code,
    streamUrl: activity.stream_url,
  };
}
