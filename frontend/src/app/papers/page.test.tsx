import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, beforeEach, expect, it, vi } from "vitest";

import PapersPage from "./page";


const { pushMock } = vi.hoisted(() => ({ pushMock: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));


beforeEach(() => pushMock.mockReset());
afterEach(() => vi.unstubAllGlobals());

// jsdom does not implement native dialog methods; browser checks cover the modal.
beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
});
afterAll(() => {
  delete (HTMLDialogElement.prototype as Partial<HTMLDialogElement>).showModal;
  delete (HTMLDialogElement.prototype as Partial<HTMLDialogElement>).close;
});


function installApi(options: {
  uploadDuplicate?: boolean;
  secondReady?: boolean;
  secondFailed?: boolean;
  deleteResponse?: () => Promise<Response>;
  nextCursor?: string;
  moreResponse?: () => Promise<Response>;
} = {}) {
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
      status: options.secondReady ? "READY" : options.secondFailed ? "FAILED" : "PROCESSING",
      current_version_id: "version-running",
      latest_task_id: "task-running",
      created_at: "2026-07-18T08:00:00Z",
      updated_at: "2026-07-18T08:00:00Z",
    },
  ];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/documents?") && (!init?.method || init.method === "GET")) {
      if (url.includes("cursor=") && options.moreResponse) return options.moreResponse();
      return Response.json({ items: documents, next_cursor: options.nextCursor ?? null });
    }
    if (url.includes("/documents/") && init?.method === "DELETE") {
      return options.deleteResponse?.() ?? Response.json({ id: url.split("/").at(-1), status: "DELETED" });
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
        status: options.secondReady ? "READY" : options.secondFailed ? "FAILED" : "RUNNING",
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
    "http://127.0.0.1:8000/api/v1/documents/document-ready/pdf",
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

it("deletes a ready paper after confirmation and removes it from research selections", async () => {
  const fetch = installApi({ secondReady: true });
  render(<PapersPage />);
  await screen.findByRole("heading", { name: "Visible region matching" });
  fireEvent.click(screen.getByRole("checkbox", { name: "将 Visible region matching 加入问答范围" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "将 Prompt guided ReID 加入问答范围" }));
  fireEvent.click(screen.getByRole("button", { name: "删除 Visible region matching" }));

  const dialog = screen.getByRole("dialog", { name: "删除论文" });
  expect(within(dialog).getByText("Visible region matching")).toBeVisible();
  expect(within(dialog).getByText(/已有对话和笔记记录保留/)).toBeVisible();
  expect(within(dialog).getByRole("button", { name: "取消" })).toHaveFocus();
  expect(fetch.mock.calls.filter(([, init]) => init?.method === "DELETE")).toHaveLength(0);
  fireEvent.click(within(dialog).getByRole("button", { name: "确认删除" }));

  await waitFor(() => expect(screen.queryByRole("dialog", { name: "删除论文" })).not.toBeInTheDocument());
  expect(screen.queryByRole("article", { name: "Visible region matching 论文" })).not.toBeInTheDocument();
  expect(screen.getAllByRole("article")).toHaveLength(1);
  expect(screen.getByText("当前显示 1 篇")).toBeVisible();
  expect(within(screen.getByRole("complementary", { name: "当前论文" })).getByText("Prompt guided ReID")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "已选论文问答（1）" }));
  await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/chat/session-1"));
  const request = fetch.mock.calls.find(([input]) => String(input).endsWith("/chat/sessions"));
  expect(JSON.parse(String(request?.[1]?.body))).toMatchObject({ document_ids: ["document-running"] });
  expect(fetch.mock.calls.filter(([, init]) => init?.method === "DELETE")).toHaveLength(1);
});

it("deletes failed papers and clears the inspector when the library becomes empty", async () => {
  const fetch = installApi({ secondFailed: true });
  render(<PapersPage />);
  await screen.findByRole("article", { name: "Prompt guided ReID 论文" });

  for (const title of ["Prompt guided ReID", "Visible region matching"]) {
    fireEvent.click(screen.getByRole("button", { name: `删除 ${title}` }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(screen.queryByRole("article", { name: `${title} 论文` })).not.toBeInTheDocument());
  }

  expect(screen.queryAllByRole("article")).toHaveLength(0);
  expect(screen.getByText("当前显示 0 篇")).toBeVisible();
  expect(screen.getByRole("complementary", { name: "当前论文" })).toHaveTextContent("选择一篇论文后");
  expect(fetch.mock.calls.filter(([, init]) => init?.method === "DELETE")).toHaveLength(2);
});

it("cancels with the button or Escape without submitting deletion", async () => {
  const fetch = installApi();
  render(<PapersPage />);
  const trigger = await screen.findByRole("button", { name: "删除 Visible region matching" });
  trigger.focus();
  fireEvent.click(trigger);
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "取消" }));
  expect(trigger).toHaveFocus();
  fireEvent.click(trigger);
  fireEvent(screen.getByRole("dialog"), new Event("cancel", { bubbles: false, cancelable: true }));

  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.getAllByRole("article")).toHaveLength(2);
  expect(fetch.mock.calls.filter(([, init]) => init?.method === "DELETE")).toHaveLength(0);
});

it("disables deletion while a task is running", async () => {
  const fetch = installApi();
  render(<PapersPage />);
  const running = await screen.findByRole("article", { name: "Prompt guided ReID 论文" });

  expect(within(running).getByRole("button", { name: "删除 Prompt guided ReID" })).toBeDisabled();
  expect(within(running).getByText("任务结束后可删除")).toBeVisible();
  fireEvent.click(within(running).getByRole("button", { name: "删除 Prompt guided ReID" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(fetch.mock.calls.filter(([, init]) => init?.method === "DELETE")).toHaveLength(0);
});

it("keeps a failed deletion retryable and prevents duplicate in-flight requests", async () => {
  let finish!: (value: Response) => void;
  const pending = new Promise<Response>((resolve) => { finish = resolve; });
  const deleteResponse = vi.fn()
    .mockResolvedValueOnce(Response.json({ code: "DOCUMENT_DELETE_CONFLICT", message: "Processing" }, { status: 409 }))
    .mockReturnValueOnce(pending);
  const fetch = installApi({ deleteResponse });
  render(<PapersPage />);
  fireEvent.click(await screen.findByRole("button", { name: "删除 Visible region matching" }));
  const dialog = screen.getByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", { name: "确认删除" }));
  expect(await within(dialog).findByRole("alert")).toHaveTextContent("论文仍在处理中");
  expect(screen.getByRole("article", { name: "Visible region matching 论文" })).toBeInTheDocument();

  fireEvent.click(within(dialog).getByRole("button", { name: "确认删除" }));
  const busy = within(dialog).getByRole("button", { name: "正在删除…" });
  expect(busy).toBeDisabled();
  expect(within(dialog).getByRole("button", { name: "取消" })).toBeDisabled();
  fireEvent.click(busy);
  fireEvent(dialog, new Event("cancel", { cancelable: true }));
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  expect(fetch.mock.calls.filter(([, init]) => init?.method === "DELETE")).toHaveLength(2);
  await act(async () => finish(Response.json({ id: "document-ready", status: "DELETED" })));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.queryByRole("article", { name: "Visible region matching 论文" })).not.toBeInTheDocument();
});

it("does not restore a deleted paper from a late pagination response", async () => {
  let finish!: (value: Response) => void;
  const pending = new Promise<Response>((resolve) => { finish = resolve; });
  installApi({ nextCursor: "page-2", moreResponse: () => pending });
  render(<PapersPage />);
  fireEvent.click(await screen.findByRole("button", { name: "加载更多论文" }));
  fireEvent.click(screen.getByRole("button", { name: "删除 Visible region matching" }));
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "确认删除" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

  await act(async () => finish(Response.json({ items: [{
    id: "document-ready", title: "Visible region matching", status: "READY",
    current_version_id: "version-ready", latest_task_id: "task-ready",
    created_at: "2026-07-17T08:00:00Z", updated_at: "2026-07-17T08:00:00Z",
  }], next_cursor: null })));

  expect(screen.queryByRole("article", { name: "Visible region matching 论文" })).not.toBeInTheDocument();
  expect(screen.getAllByRole("article")).toHaveLength(1);
});
