import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { ChatWorkspace } from "./chat-workspace";


afterEach(() => vi.unstubAllGlobals());


it("shows the user question immediately and renders answer deltas before completion", async () => {
  const encoder = new TextEncoder();
  let finishStream: (() => void) | undefined;
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encoder.encode([
        'id: 1\ndata: {"type":"start","messageId":"run-1"}\n\n',
        'id: 2\ndata: {"type":"text-start","id":"answer-1"}\n\n',
        'id: 3\ndata: {"type":"text-delta","id":"answer-1","delta":"正在生成的回答"}\n\n',
      ].join("")));
      finishStream = () => {
        controller.enqueue(encoder.encode([
          'id: 4\ndata: {"type":"text-delta","id":"answer-1","delta":"已经完成。"}\n\n',
          'id: 5\ndata: {"type":"text-end","id":"answer-1"}\n\n',
          'id: 6\ndata: {"type":"data-citations","data":{"blocks":[{"type":"paper_evidence","text":"正在生成的回答已经完成。","citationIds":[]}],"citations":[],"warnings":[]}}\n\n',
          'id: 7\ndata: {"type":"finish"}\n\n',
          'id: 8\ndata: [DONE]\n\n',
        ].join("")));
        controller.close();
      };
    },
  });
  vi.stubGlobal("fetch", vi.fn(async () => new Response(stream, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  })));

  render(<ChatWorkspace sessionId="session-1" />);
  fireEvent.change(screen.getByLabelText("研究问题"), {
    target: { value: "这篇论文的核心方法是什么？" },
  });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));

  expect(await screen.findByText("这篇论文的核心方法是什么？")).toBeVisible();
  expect(await screen.findByText("正在生成的回答")).toBeVisible();

  await act(async () => finishStream?.());
  expect(await screen.findByText("正在生成的回答已经完成。")).toBeVisible();
});
