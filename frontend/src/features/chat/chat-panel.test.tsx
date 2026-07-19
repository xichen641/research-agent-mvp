import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ChatPanel } from "./chat-panel";

describe("ChatPanel", () => {
  it("renders paper evidence separately from model knowledge", () => {
    render(
      <ChatPanel
        messages={[
          {
            id: "m1",
            blocks: [
              {
                type: "paper_evidence",
                text: "Paper claim",
                citationIds: ["c1"],
              },
              { type: "model_knowledge", text: "Background" },
            ],
          },
        ]}
      />,
    );

    expect(screen.getByText("论文证据")).toBeVisible();
    expect(screen.getByText("模型补充")).toBeVisible();
  });
});
