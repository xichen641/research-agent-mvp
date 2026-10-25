import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiProblem } from "@/lib/api";
import { deleteDocument, getTask, listDocuments } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("paper API", () => {
  it("deletes only the requested document and accepts the terminal receipt", async () => {
    const fetch = vi.fn().mockResolvedValue(Response.json({ id: "paper-1", status: "DELETED" }));
    vi.stubGlobal("fetch", fetch);

    await expect(deleteDocument("paper-1")).resolves.toBeUndefined();

    expect(fetch).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/api/v1/documents/paper-1",
      { method: "DELETE" },
    );
  });

  it("maps snake_case document payloads to camelCase", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            items: [
              {
                id: "document-1",
                title: "Paper",
                status: "PROCESSING",
                current_version_id: "version-1",
                latest_task_id: "task-1",
                created_at: "2026-07-17T08:00:00Z",
                updated_at: "2026-07-17T08:00:00Z",
              },
            ],
            next_cursor: null,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    await expect(listDocuments()).resolves.toMatchObject({
      items: [
        {
          currentVersionId: "version-1",
          latestTaskId: "task-1",
          createdAt: "2026-07-17T08:00:00Z",
        },
      ],
      nextCursor: null,
    });
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/documents?limit=50"),
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  it("throws a sanitized ApiProblem without retaining arbitrary response fields", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            code: "TASK_NOT_FOUND",
            message: "Task was not found",
            retryable: false,
            trace_id: "trace-1",
            details: { field: "task" },
            database_token: "must-not-survive",
          }),
          { status: 404, headers: { "Content-Type": "application/problem+json" } },
        ),
      ),
    );

    const error = await getTask("missing").catch((caught) => caught);
    expect(error).toBeInstanceOf(ApiProblem);
    expect(error).toMatchObject({
      status: 404,
      code: "TASK_NOT_FOUND",
      message: "Task was not found",
      retryable: false,
      traceId: "trace-1",
      details: { field: "task" },
    });
    expect(JSON.stringify(error)).not.toContain("database_token");
    expect(JSON.stringify(error)).not.toContain("must-not-survive");
  });

  it("uses a generic problem for malformed failures", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("private backend exception", {
          status: 502,
          headers: { "Content-Type": "text/plain" },
        }),
      ),
    );

    const error = await getTask("task-1").catch((caught) => caught);
    expect(error).toMatchObject({ status: 502, code: "HTTP_502" });
    expect(error.message).not.toContain("private backend exception");
  });
});
