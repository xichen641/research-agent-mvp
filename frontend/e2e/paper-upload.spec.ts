import { expect, test } from "@playwright/test";

test("upload, refresh, and durable worker failure remain visible", async ({ page }) => {
  await page.goto("/papers");
  await expect(page.getByRole("heading", { name: "论文工作台" })).toBeVisible();
  await expect(page.getByText("正在加载论文库…")).toBeHidden();

  const cards = page.getByRole("article");
  const beforeTextUpload = await cards.count();
  const existingBrokenPaper = page.getByRole("article", { name: /task8-broken/ });
  const brokenPaperAlreadyExists = await existingBrokenPaper.count() > 0;

  await page.getByRole("button", { name: "导入论文" }).click();
  const importDialog = page.getByRole("dialog", { name: "导入论文" });
  await expect(importDialog).toBeVisible();

  await importDialog.getByLabel("选择 PDF").setInputFiles({
    name: "not-a-paper.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("plain text"),
  });
  await expect(importDialog.getByText("仅支持 PDF 文件")).toBeVisible();
  await expect(cards).toHaveCount(beforeTextUpload);

  await importDialog.getByLabel("选择 PDF").setInputFiles({
    name: "task8-broken.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.7\nmalformed Task 8 body"),
  });
  await importDialog.getByRole("button", { name: "上传论文" }).click();
  await expect(
    importDialog.getByText(/上传已受理|论文已存在，已显示原任务/),
  ).toBeVisible();
  await expect(cards).toHaveCount(beforeTextUpload + (brokenPaperAlreadyExists ? 0 : 1));
  await expect(
    existingBrokenPaper.getByText("Source document could not be parsed"),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "选择 task8-broken" }).getByText("失败", { exact: true }),
  ).toBeVisible();

  await page.reload();
  const durableCard = page.getByRole("article", { name: /task8-broken/ });
  await expect(durableCard).toBeVisible();
  await expect(
    durableCard.getByText("Source document could not be parsed"),
  ).toBeVisible();
  await expect(cards).toHaveCount(beforeTextUpload + (brokenPaperAlreadyExists ? 0 : 1));
});

test("paper workspace controls remain interactive", async ({ page }) => {
  await page.goto("/papers");

  await expect(page.getByRole("heading", { name: "论文工作台" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "工作台导航" })).toBeVisible();
  await expect(page.getByRole("searchbox", { name: "搜索论文" })).toBeVisible();
  await expect(page.getByText("正在加载论文库…")).toBeHidden();

  await page.getByRole("button", { name: "已就绪" }).click();
  await expect(page.getByRole("button", { name: "已就绪" })).toHaveAttribute("aria-pressed", "true");

  await page.getByRole("button", { name: "导入论文" }).click();
  await expect(page.getByRole("dialog", { name: "导入论文" })).toBeVisible();
  await page.getByRole("button", { name: "关闭", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "导入论文" })).toBeHidden();
});
