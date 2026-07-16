import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import PapersPage from "./page";

afterEach(() => vi.unstubAllGlobals());

it("rehydrates durable cards and deduplicates a duplicate upload receipt", async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/documents?") && (!init?.method || init.method === "GET")) {
      return new Response(
        JSON.stringify({
          items: [
            {
              id: "document-1",
              title: "Stored paper",
              status: "FAILED",
              current_version_id: "version-1",
              latest_task_id: "task-1",
              created_at: "2026-07-17T08:00:00Z",
              updated_at: "2026-07-17T08:00:00Z",
            },
          ],
          next_cursor: null,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    if (url.endsWith("/tasks/task-1")) {
      return new Response(
        JSON.stringify({
          id: "task-1",
          document_version_id: "version-1",
          stage: "PARSING",
          status: "FAILED",
          progress_current: 2,
          progress_total: 8,
          retry_count: 0,
          error_code: "PARSE_PARSER_FAILED",
          error_message: "Source document could not be parsed",
          retryable: true,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    if (url.endsWith("/documents") && init?.method === "POST") {
      return new Response(
        JSON.stringify({
          document_id: "document-1",
          version_id: "version-1",
          task_id: "task-1",
          duplicate: true,
        }),
        { status: 202, headers: { "Content-Type": "application/json" } },
      );
    }
    throw new Error(`Unexpected request: ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<PapersPage />);

  expect(await screen.findByRole("heading", { name: "Stored paper" })).toBeVisible();
  expect(screen.getAllByRole("article", { name: /Stored paper/ })).toHaveLength(1);
  fireEvent.change(screen.getByLabelText("选择 PDF"), {
    target: {
      files: [
        new File(["%PDF-malformed"], "stored.pdf", {
          type: "application/pdf",
        }),
      ],
    },
  });
  fireEvent.click(screen.getByRole("button", { name: "上传论文" }));

  expect(await screen.findByText("论文已存在，已显示原任务")).toBeVisible();
  expect(screen.getAllByRole("article", { name: /Stored paper/ })).toHaveLength(1);
});
