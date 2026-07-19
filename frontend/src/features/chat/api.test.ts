import { afterEach, expect, it, vi } from "vitest";

import { createChatSession } from "./api";


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
    "http://localhost:8000/api/v1/chat/sessions",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({
        title: "Paper workspace",
        document_ids: ["document-1"],
      }),
    }),
  );
});
