import { render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";

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
