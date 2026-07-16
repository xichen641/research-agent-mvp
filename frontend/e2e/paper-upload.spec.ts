import { expect, test } from "@playwright/test";

test("upload, refresh, and durable worker failure remain visible", async ({ page }) => {
  await page.goto("/papers");
  await expect(page.getByRole("heading", { name: "论文库" })).toBeVisible();
  await expect(page.getByText("正在加载论文库…")).toBeHidden();

  const cards = page.getByRole("article");
  const beforeTextUpload = await cards.count();
  await page.getByLabel("选择 PDF").setInputFiles({
    name: "not-a-paper.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("plain text"),
  });
  await expect(page.getByText("仅支持 PDF 文件")).toBeVisible();
  await expect(cards).toHaveCount(beforeTextUpload);

  await page.getByLabel("选择 PDF").setInputFiles({
    name: "task8-broken.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.7\nmalformed Task 8 body"),
  });
  await page.getByRole("button", { name: "上传论文" }).click();
  await expect(
    page.getByText(/上传已受理|论文已存在，已显示原任务/),
  ).toBeVisible();
  await expect(cards).toHaveCount(1);
  await expect(page.getByText("Source document could not be parsed")).toBeVisible();
  await expect(page.getByText("失败", { exact: true })).toBeVisible();

  await page.reload();
  const durableCard = page.getByRole("article", { name: /task8-broken/ });
  await expect(durableCard).toBeVisible();
  await expect(
    durableCard.getByText("Source document could not be parsed"),
  ).toBeVisible();
  await expect(cards).toHaveCount(1);
});
