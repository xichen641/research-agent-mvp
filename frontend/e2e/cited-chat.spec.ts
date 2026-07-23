import { expect, test } from "@playwright/test";

test("verified answer keeps model knowledge separate and opens its citation", async ({
  page,
}) => {
  await page.route("**/api/v1/chat/sessions/session-1/messages", async (route) => {
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
