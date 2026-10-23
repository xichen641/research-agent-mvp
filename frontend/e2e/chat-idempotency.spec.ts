import { expect, test } from "@playwright/test";

const api = process.env.PLAYWRIGHT_API_URL ?? "http://127.0.0.1:8000";

test("lost POST response is recovered without duplicate messages or runs", async ({
  page,
  request,
}) => {
  const session = await (
    await request.post(`${api}/api/v1/chat/sessions`, {
      data: { title: "T1.2 断网重发验收", document_ids: [] },
    })
  ).json();
  let accepted: { run_id: string; user_message_id: string } | undefined;
  const clientIds: string[] = [];
  await page.route(
    `**/api/v1/chat/sessions/${session.id}/messages`,
    async (route) => {
      if (route.request().method() !== "POST") {
        await route.continue();
        return;
      }
      clientIds.push(route.request().postDataJSON().client_request_id);
      if (clientIds.length === 1) {
        const response = await route.fetch();
        expect(response.status()).toBe(202);
        accepted = await response.json();
        await route.abort("connectionfailed");
      } else {
        await route.continue();
      }
    },
  );
  await page.goto(`/chat/${session.id}`);
  await expect(page.locator("form")).toHaveAttribute("data-hydrated", "true");
  await page.getByLabel("本轮论文检索").selectOption("off");
  const question = "只回复：T1.2 连接恢复成功。";
  await page.getByLabel("研究问题").fill(question);
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await page.getByRole("button", { name: "恢复发送或连接" }).click();
  await page.getByLabel("研究问题").fill("下一问题（未发送）");
  await expect(
    page.getByRole("button", { name: "发送", exact: true }),
  ).toBeEnabled();
  await expect(page.getByText(question, { exact: true })).toHaveCount(1);
  expect(clientIds.length).toBe(2);
  expect(clientIds[0]).toBe(clientIds[1]);
  const activity = await (
    await request.get(`${api}/api/v1/chat/sessions/${session.id}/activity`)
  ).json();
  expect(activity.run_id).toBe(accepted!.run_id);
  expect(activity.status).toBe("SUCCEEDED");
  const history = await (
    await request.get(`${api}/api/v1/chat/sessions/${session.id}/messages`)
  ).json();
  expect(history.items).toHaveLength(2);
  expect(history.items[0].id).toBe(accepted!.user_message_id);
  expect(history.items[1].id).toBe(activity.assistant_message_id);
  expect(
    history.items.map((item: { sequence: number }) => item.sequence),
  ).toEqual([1, 2]);
  await page.reload();
  await expect(page.getByText(question, { exact: true })).toHaveCount(1);
  const latest = await (
    await request.get(
      `${api}/api/v1/chat/sessions/${session.id}/messages?limit=1`,
    )
  ).json();
  expect(latest.items[0].sequence).toBe(2);
  expect(latest.next_before_sequence).toBe(2);
  const older = await (
    await request.get(
      `${api}/api/v1/chat/sessions/${session.id}/messages?limit=1&before_sequence=2`,
    )
  ).json();
  expect(older.items[0].sequence).toBe(1);
  expect(older.next_before_sequence).toBeNull();
});
