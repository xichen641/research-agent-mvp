import { expect, test } from "@playwright/test";

const api = process.env.PLAYWRIGHT_API_URL ?? "http://127.0.0.1:8000";

test("research space survives reload, pins materials and explicitly adopts a legacy chat", async ({
  page,
  request,
}) => {
  const name = `T1.1 验收 ${Date.now()}`;
  await page.goto("/research");
  await page.getByRole("button", { name: "新建研究空间" }).click();
  await page.getByLabel("空间名称").fill(name);
  await page.getByLabel("研究目标").fill("区分语义引导与额外参数的收益");
  await page.getByLabel("资源约束").fill("12GB 显存，实验尚未执行");
  await page.getByRole("button", { name: "创建空间", exact: true }).click();
  await expect(page).toHaveURL(/\/research\/[0-9a-f-]+$/);
  const spaceUrl = page.url();
  const spaceId = spaceUrl.split("/").at(-1)!;
  await page.reload();
  await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();
  await expect(
    page.getByText("12GB 显存，实验尚未执行", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "编辑空间" }).click();
  await page.getByLabel("资源约束").fill("24GB 显存，实验尚未执行");
  await page.getByRole("button", { name: "保存修订" }).click();
  await expect(page.getByText("修订 2", { exact: true })).toBeVisible();
  const space = await (
    await request.get(`${api}/api/v1/research/spaces/${spaceId}`)
  ).json();
  expect(space.materials).toEqual([]);
  expect(space.resource_constraints).toContain("24GB");
  const docs = await (
    await request.get(`${api}/api/v1/documents?limit=1`)
  ).json();
  if (docs.items.length) {
    const version = docs.items[0].current_version_id;
    const updated = await request.put(
      `${api}/api/v1/research/spaces/${spaceId}`,
      {
        data: {
          name,
          goal: space.goal,
          resource_constraints: space.resource_constraints,
          expected_revision: 2,
          material_version_ids: [version],
        },
      },
    );
    expect(updated.ok()).toBeTruthy();
    await page.reload();
    await expect(
      page.getByRole("complementary", { name: "空间资料" }),
    ).toContainText(version.slice(0, 8));
  }
  const legacy = await (
    await request.post(`${api}/api/v1/chat/sessions`, {
      data: { title: `待归入 ${name}`, document_ids: [] },
    })
  ).json();
  await page.reload();
  await page.getByLabel("未归属会话").selectOption(legacy.id);
  await page.getByRole("button", { name: "归入此空间" }).click();
  await expect(
    page
      .getByRole("region", { name: "空间讨论" })
      .getByRole("link", { name: `待归入 ${name}` }),
  ).toBeVisible();
  await page.getByLabel("讨论标题").fill("空间内讨论");
  await page.getByRole("button", { name: "新建讨论", exact: true }).click();
  await expect(page).toHaveURL(/\/chat\/[0-9a-f-]+$/);
  await expect(
    page.getByRole("complementary", { name: "当前研究空间" }),
  ).toContainText(name);
  await page.reload();
  await expect(
    page.getByRole("complementary", { name: "当前研究空间" }),
  ).toContainText("24GB");
});
