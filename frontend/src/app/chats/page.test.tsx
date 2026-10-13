import { render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import ChatsPage from "./page";


afterEach(() => vi.unstubAllGlobals());


it("shows durable conversations with their paper scope", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({
    items: [{
      id: "session-1",
      title: "Agent memory comparison",
      document_ids: ["paper-1", "paper-2"],
      created_at: "2026-08-30T00:00:00Z",
      updated_at: "2026-08-30T00:05:00Z",
    }],
  })));

  render(<ChatsPage />);

  expect(await screen.findByRole("link", { name: /Agent memory comparison/ })).toHaveAttribute(
    "href",
    "/chat/session-1",
  );
  expect(screen.getByText("限定 2 篇论文")).toBeVisible();
});
