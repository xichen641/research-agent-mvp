import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import PapersPage from "./page";


const { pushMock } = vi.hoisted(() => ({ pushMock: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));


beforeEach(() => pushMock.mockReset());
afterEach(() => vi.unstubAllGlobals());


function installApi(options: { uploadDuplicate?: boolean; secondReady?: boolean } = {}) {
  const documents = [
    {
      id: "document-ready",
      title: "Visible region matching",
      status: "READY",
      current_version_id: "version-ready",
      latest_task_id: "task-ready",
      created_at: "2026-07-17T08:00:00Z",
      updated_at: "2026-07-17T08:00:00Z",
    },
    {
      id: "document-running",
      title: "Prompt guided ReID",
      status: options.secondReady ? "READY" : "PROCESSING",
      current_version_id: "version-running",
      latest_task_id: "task-running",
      created_at: "2026-07-18T08:00:00Z",
      updated_at: "2026-07-18T08:00:00Z",
    },
  ];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/documents?") && (!init?.method || init.method === "GET")) {
      return Response.json({ items: documents, next_cursor: null });
    }
    if (url.endsWith("/tasks/task-ready")) {
      return Response.json({
        id: "task-ready",
        document_version_id: "version-ready",
        stage: "EMBEDDING",
        status: "READY",
        progress_current: 8,
        progress_total: 8,
        retry_count: 0,
        error_code: null,
        error_message: null,
        retryable: false,
      });
    }
    if (url.endsWith("/tasks/task-running")) {
      return Response.json({
        id: "task-running",
        document_version_id: "version-running",
        stage: "PARSING",
        status: options.secondReady ? "READY" : "RUNNING",
        progress_current: options.secondReady ? 8 : 3,
        progress_total: 8,
        retry_count: 0,
        error_code: null,
        error_message: null,
        retryable: false,
      });
    }
    if (url.endsWith("/chat/sessions") && init?.method === "POST") {
      return Response.json(
        {
          id: "session-1",
          title: "Visible region matching",
          document_ids: ["document-ready"],
        },
        { status: 201 },
      );
    }
    if (url.endsWith("/documents") && init?.method === "POST") {
      return Response.json(
        {
          document_id: "document-ready",
          version_id: "version-ready",
          task_id: "task-ready",
          duplicate: options.uploadDuplicate ?? false,
        },
        { status: 202 },
      );
    }
    throw new Error(`Unexpected request: ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}


it("selects a paper and exposes connected research actions", async () => {
  const fetchMock = installApi();
  render(<PapersPage />);

  expect(await screen.findByRole("heading", { name: "论文工作台" })).toBeVisible();
  expect(screen.getByRole("searchbox", { name: "搜索论文" })).toBeVisible();
  const inspector = screen.getByRole("complementary", { name: "当前论文" });
  expect(within(inspector).getByText("Visible region matching")).toBeVisible();
  expect(within(inspector).getByRole("link", { name: "打开论文笔记" })).toHaveAttribute(
    "href",
    "/papers/document-ready/notes",
  );
  expect(within(inspector).getByRole("link", { name: "查看原始 PDF" })).toHaveAttribute(
    "href",
    "http://localhost:8000/api/v1/documents/document-ready/pdf",
  );

  fireEvent.click(within(inspector).getByRole("button", { name: "开始论文问答" }));
  await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/chat/session-1"));
  const chatRequest = fetchMock.mock.calls.find(([input]) =>
    String(input).endsWith("/chat/sessions"),
  );
  expect(JSON.parse(String(chatRequest?.[1]?.body))).toEqual({
    title: "Visible region matching",
    document_ids: ["document-ready"],
  });
});


it("starts library-wide chat with an empty document scope", async () => {
  const fetchMock = installApi();
  render(<PapersPage />);
  await screen.findByRole("heading", { name: "Visible region matching" });

  fireEvent.click(screen.getByRole("button", { name: "开始全库问答" }));

  await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/chat/session-1"));
  const chatRequest = fetchMock.mock.calls.find(([input]) =>
    String(input).endsWith("/chat/sessions"),
  );
  expect(JSON.parse(String(chatRequest?.[1]?.body))).toEqual({
    title: "全库论文问答",
    document_ids: [],
  });
});


it("starts a multi-paper chat with the selected ready documents", async () => {
  const fetchMock = installApi({ secondReady: true });
  render(<PapersPage />);
  await screen.findByRole("heading", { name: "Visible region matching" });

  fireEvent.click(screen.getByRole("checkbox", {
    name: "将 Visible region matching 加入问答范围",
  }));
  fireEvent.click(screen.getByRole("checkbox", {
    name: "将 Prompt guided ReID 加入问答范围",
  }));
  fireEvent.click(screen.getByRole("button", { name: "已选论文问答（2）" }));

  await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/chat/session-1"));
  const chatRequest = fetchMock.mock.calls.find(([input]) =>
    String(input).endsWith("/chat/sessions"),
  );
  expect(JSON.parse(String(chatRequest?.[1]?.body))).toEqual({
    title: "多篇论文问答（2 篇）",
    document_ids: ["document-ready", "document-running"],
  });
});


it("searches, filters, and changes the selected paper context", async () => {
  installApi();
  render(<PapersPage />);
  await screen.findByRole("heading", { name: "Visible region matching" });

  fireEvent.change(screen.getByRole("searchbox", { name: "搜索论文" }), {
    target: { value: "Prompt" },
  });
  expect(screen.queryByRole("article", { name: /Visible region matching/ })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "选择 Prompt guided ReID" }));
  expect(
    within(screen.getByRole("complementary", { name: "当前论文" })).getByText(
      "Prompt guided ReID",
    ),
  ).toBeVisible();
  expect(screen.getByRole("button", { name: "开始论文问答" })).toBeDisabled();

  fireEvent.change(screen.getByRole("searchbox", { name: "搜索论文" }), {
    target: { value: "" },
  });
  fireEvent.click(screen.getByRole("button", { name: "已就绪" }));
  expect(screen.getByRole("article", { name: /Visible region matching/ })).toBeVisible();
  expect(screen.queryByRole("article", { name: /Prompt guided ReID/ })).not.toBeInTheDocument();
});


it("opens the upload drawer and keeps a duplicate paper deduplicated", async () => {
  installApi({ uploadDuplicate: true });
  render(<PapersPage />);
  expect(await screen.findAllByRole("article")).toHaveLength(2);

  fireEvent.click(screen.getByRole("button", { name: "导入论文" }));
  const drawer = screen.getByRole("dialog", { name: "导入论文" });
  fireEvent.change(within(drawer).getByLabelText("选择 PDF"), {
    target: {
      files: [
        new File(["%PDF-malformed"], "stored.pdf", {
          type: "application/pdf",
        }),
      ],
    },
  });
  fireEvent.click(within(drawer).getByRole("button", { name: "上传论文" }));

  expect(await within(drawer).findByText("论文已存在，已显示原任务")).toBeVisible();
  await waitFor(() => expect(screen.getAllByRole("article")).toHaveLength(2));
});
