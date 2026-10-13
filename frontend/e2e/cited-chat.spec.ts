import { expect, test } from "@playwright/test";

test("verified answer keeps model knowledge separate and opens its citation", async ({
  page,
}) => {
  await page.route("**/api/v1/chat/sessions/session-1/activity", async (route) => {
    await route.fulfill({ json: {
      session_id: "session-1",
      run_id: null,
      status: "IDLE",
      error_code: null,
      stream_url: null,
    } });
  });
  await page.route("**/api/v1/chat/sessions/session-1/messages", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: { items: [] } });
      return;
    }
    const parts = [
      { type: "start", messageId: "run-1" },
      { type: "data-workflow", data: { stage: "plan", status: "completed" } },
      {
        type: "data-workflow",
        data: { stage: "citation_verification", status: "completed" },
      },
      { type: "text-start", id: "answer-1" },
      { type: "text-delta", id: "answer-1", delta: "Verified answer" },
      { type: "text-end", id: "answer-1" },
      {
        type: "data-citations",
        data: {
          blocks: [
            {
              type: "paper_evidence",
              text: "Visible-region matching improves precision.",
              citationIds: ["c1"],
            },
            { type: "model_knowledge", text: "This is general background." },
          ],
          citations: [
            {
              id: "c1",
              chunkId: "chunk-1",
              documentId: "document-1",
              documentVersionId: "version-1",
              title: "A Verified Paper",
              sectionPath: ["Methods", "Matching"],
              pageStart: 7,
              pageEnd: 8,
              quote: "We match only features inside the visible region.",
            },
          ],
          warnings: [],
        },
      },
      { type: "finish" },
    ];
    const body = parts
      .map((part, index) => `id: ${index + 1}\ndata: ${JSON.stringify(part)}\n\n`)
      .join("") + `id: ${parts.length + 1}\ndata: [DONE]\n\n`;
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      headers: { "x-vercel-ai-ui-message-stream": "v1" },
      body,
    });
  });

  await page.goto("/chat/session-1");
  await expect(page.locator("form")).toHaveAttribute("data-hydrated", "true");
  await page.locator("textarea").fill("What is the core method?");
  await page.locator("form button[type=submit]").click();

  await expect(page.getByText("What is the core method?")).toBeVisible();
  await expect(page.getByText("论文证据", { exact: true })).toBeVisible();
  await expect(page.getByText("模型补充", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "引用 1" }).click();
  await expect(page.getByText("A Verified Paper")).toBeVisible();
  await expect(page.getByText("Methods / Matching")).toBeVisible();
  await expect(
    page.getByText("We match only features inside the visible region."),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: "打开论文原页" })).toHaveAttribute(
    "href",
    /\/api\/v1\/documents\/document-1\/pdf\?page=7$/,
  );
});

test("a running answer survives navigation to the paper workspace", async ({ page }) => {
  let releaseAnswer: (() => void) | undefined;
  let completed = false;
  const answerGate = new Promise<void>((resolve) => {
    releaseAnswer = resolve;
  });
  await page.route("**/api/v1/documents?**", async (route) => {
    await route.fulfill({ json: { items: [], next_cursor: null } });
  });
  await page.route("**/api/v1/chat/sessions/session-nav/activity", async (route) => {
    await route.fulfill({ json: {
      session_id: "session-nav",
      run_id: completed ? "run-nav" : null,
      status: completed ? "SUCCEEDED" : "IDLE",
      error_code: null,
      stream_url: completed ? "/api/v1/chat/runs/run-nav/stream" : null,
    } });
  });
  await page.route("**/api/v1/chat/sessions/session-nav/messages", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: {
        items: completed ? [
          {
            id: "question-nav",
            role: "user",
            content: { text: "这个方法的核心贡献是什么？" },
            created_at: "2026-08-30T00:00:00Z",
          },
          {
            id: "answer-nav",
            role: "assistant",
            content: {
              blocks: [{ type: "model_knowledge", text: "后台完成的回答" }],
              citations: [],
              warnings: [],
            },
            created_at: "2026-08-30T00:00:01Z",
          },
        ] : [],
      } });
      return;
    }
    await answerGate;
    completed = true;
    const parts = [
      { type: "start", messageId: "run-nav" },
      { type: "text-start", id: "answer-nav" },
      { type: "text-delta", id: "answer-nav", delta: "后台完成的回答" },
      { type: "text-end", id: "answer-nav" },
      {
        type: "data-citations",
        data: {
          blocks: [{ type: "model_knowledge", text: "后台完成的回答" }],
          citations: [],
          warnings: [],
        },
      },
      { type: "finish" },
    ];
    const body = parts
      .map((part, index) => `id: ${index + 1}\ndata: ${JSON.stringify(part)}\n\n`)
      .join("") + `id: ${parts.length + 1}\ndata: [DONE]\n\n`;
    await route.fulfill({ status: 200, contentType: "text/event-stream", body });
  });

  await page.goto("/chat/session-nav");
  await expect(page.locator("form")).toHaveAttribute("data-hydrated", "true");
  await page.locator("textarea").fill("这个方法的核心贡献是什么？");
  await page.locator("form button[type=submit]").click();
  await expect(page.getByText("这个方法的核心贡献是什么？")).toBeVisible();

  await page.getByRole("link", { name: "论文工作台" }).click();
  await expect(page.getByRole("heading", { name: "论文工作台" })).toBeVisible();
  await expect(page.getByRole("link", { name: /问答正在排队/ })).toBeVisible();

  releaseAnswer?.();
  await expect(page.getByRole("link", { name: /问答已完成/ })).toBeVisible();
  await page.getByRole("link", { name: /问答已完成/ }).click();
  await expect(page.getByText("后台完成的回答")).toBeVisible();
});
