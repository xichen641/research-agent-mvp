import { afterEach, expect, it, vi } from "vitest";

import {
  createChatSession,
  getChatSessionActivity,
  listChatMessages,
  listChatSessions,
} from "./api";


afterEach(() => vi.unstubAllGlobals());


it("creates a document-scoped chat session through the typed API boundary", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    Response.json(
      {
        id: "session-1",
        title: "Paper workspace",
        document_ids: ["document-1"],
      },
      { status: 201 },
    ),
  );
  vi.stubGlobal("fetch", fetchMock);

  await expect(
    createChatSession("Paper workspace", ["document-1"]),
  ).resolves.toEqual({
    id: "session-1",
    title: "Paper workspace",
    documentIds: ["document-1"],
  });
  expect(fetchMock).toHaveBeenCalledWith(
    "http://127.0.0.1:8000/api/v1/chat/sessions",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({
        title: "Paper workspace",
        document_ids: ["document-1"],
      }),
    }),
  );
});

it("loads durable user and verified assistant messages", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({
    items: [
      {
        id: "message-1",
        role: "user",
        content: { text: "What did we decide?" },
        created_at: "2026-08-30T00:00:00Z",
      },
      {
        id: "message-2",
        role: "assistant",
        content: {
          blocks: [{ type: "model_knowledge", text: "We chose bounded memory." }],
          citations: [],
          warnings: [],
        },
        created_at: "2026-08-30T00:00:01Z",
      },
    ],
  })));

  await expect(listChatMessages("session-1")).resolves.toEqual([
    { id: "message-1", role: "user", text: "What did we decide?" },
    {
      id: "message-2",
      role: "assistant",
      blocks: [{ type: "model_knowledge", text: "We chose bounded memory." }],
      citations: [],
      streaming: false,
    },
  ]);
});

it("lists conversations that can be reopened", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({
    items: [{
      id: "session-1",
      title: "Memory discussion",
      document_ids: ["document-1"],
      created_at: "2026-08-30T00:00:00Z",
      updated_at: "2026-08-30T00:05:00Z",
    }],
  })));

  await expect(listChatSessions()).resolves.toEqual([{
    id: "session-1",
    title: "Memory discussion",
    documentIds: ["document-1"],
    createdAt: "2026-08-30T00:00:00Z",
    updatedAt: "2026-08-30T00:05:00Z",
  }]);
});

it("loads the latest run needed to resume a conversation", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({
    session_id: "session-1",
    run_id: "run-1",
    status: "RUNNING",
    error_code: null,
    stream_url: "/api/v1/chat/runs/run-1/stream",
  })));

  await expect(getChatSessionActivity("session-1")).resolves.toEqual({
    sessionId: "session-1",
    runId: "run-1",
    status: "RUNNING",
    errorCode: null,
    streamUrl: "/api/v1/chat/runs/run-1/stream",
  });
});
