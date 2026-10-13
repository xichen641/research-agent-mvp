import { render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { ChatActivityProvider } from "@/features/chat/chat-activity";
import { WorkspaceShell } from "./workspace-shell";


vi.mock("next/navigation", () => ({ usePathname: () => "/papers" }));


it("provides a persistent ResearchAgent workspace frame", () => {
  render(
    <WorkspaceShell>
      <p>Workspace content</p>
    </WorkspaceShell>,
  );

  const navigation = screen.getByRole("navigation", { name: "工作台导航" });
  expect(navigation).toBeVisible();
  expect(screen.getAllByText("ResearchAgent").length).toBeGreaterThan(0);
  expect(within(navigation).getByRole("link", { name: "论文工作台" })).toHaveAttribute(
    "href",
    "/papers",
  );
  expect(screen.getByText("本地研究空间")).toBeVisible();
  expect(screen.getByText("Workspace content")).toBeVisible();
});

it("keeps a background chat visible while the paper workspace is open", async () => {
  window.localStorage.setItem("research-agent.active-chat", JSON.stringify({
    sessionId: "session-42",
    runId: "run-42",
    status: "RUNNING",
    updatedAt: "2026-08-30T00:00:00Z",
  }));
  render(
    <ChatActivityProvider>
      <WorkspaceShell>
        <p>Paper workspace</p>
      </WorkspaceShell>
    </ChatActivityProvider>,
  );

  const link = await screen.findByRole("link", { name: /问答后台处理中/ });
  expect(link).toHaveAttribute("href", "/chat/session-42");
  expect(screen.getByText("点击返回当前问答")).toBeVisible();
  window.localStorage.clear();
});
