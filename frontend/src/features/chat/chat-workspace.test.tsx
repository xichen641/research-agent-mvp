import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { ChatWorkspace } from "./chat-workspace";


afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});


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
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (
      String(input).endsWith("/api/v1/chat/sessions/session-1/messages") &&
      init?.method !== "POST"
    ) {
      return Response.json({ items: [] });
    }
    if (String(input).endsWith("/api/v1/chat/sessions/session-1/activity")) {
      return Response.json({
        session_id: "session-1",
        run_id: null,
        status: "IDLE",
        error_code: null,
        stream_url: null,
      });
    }
    return new Response(stream, {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    });
  }));

  render(<ChatWorkspace sessionId="session-1" />);
  fireEvent.change(screen.getByLabelText("本轮论文检索"), {
    target: { value: "off" },
  });
  fireEvent.change(screen.getByLabelText("研究问题"), {
    target: { value: "这篇论文的核心方法是什么？" },
  });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));

  expect(await screen.findByText("这篇论文的核心方法是什么？")).toBeVisible();
  expect(await screen.findByText("正在生成的回答")).toBeVisible();

  const request = vi.mocked(fetch).mock.calls.find((call) => call[1]?.method === "POST")?.[1];
  expect(JSON.parse(String(request?.body))).toEqual({
    question: "这篇论文的核心方法是什么？",
    retrieval_mode: "off",
  });

  await act(async () => finishStream?.());
  expect(await screen.findByText("正在生成的回答已经完成。")).toBeVisible();
});

it("reconnects to a running answer when the chat workspace is reopened", async () => {
  const encoder = new TextEncoder();
  const resumedStream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encoder.encode([
        'id: 1\ndata: {"type":"start","messageId":"run-resume"}\n\n',
        'id: 2\ndata: {"type":"text-start","id":"answer-resume"}\n\n',
        'id: 3\ndata: {"type":"text-delta","id":"answer-resume","delta":"恢复后的答案"}\n\n',
        'id: 4\ndata: {"type":"text-end","id":"answer-resume"}\n\n',
        'id: 5\ndata: {"type":"data-citations","data":{"blocks":[{"type":"model_knowledge","text":"恢复后的答案"}],"citations":[],"warnings":[]}}\n\n',
        'id: 6\ndata: {"type":"finish"}\n\n',
        'id: 7\ndata: [DONE]\n\n',
      ].join("")));
      controller.close();
    },
  });
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/api/v1/chat/sessions/session-1/messages")) {
      return Response.json({
        items: [{
          id: "question-1",
          role: "user",
          content: { text: "离开页面前的问题" },
          created_at: "2026-08-30T00:00:00Z",
        }],
      });
    }
    if (url.endsWith("/api/v1/chat/sessions/session-1/activity")) {
      return Response.json({
        session_id: "session-1",
        run_id: "run-resume",
        status: "RUNNING",
        error_code: null,
        stream_url: "/api/v1/chat/runs/run-resume/stream",
      });
    }
    if (url.endsWith("/api/v1/chat/runs/run-resume/stream")) {
      return new Response(resumedStream, {
        headers: { "Content-Type": "text/event-stream" },
      });
    }
    throw new Error(`Unexpected URL: ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);

  render(<ChatWorkspace sessionId="session-1" />);

  expect(await screen.findByText("离开页面前的问题")).toBeVisible();
  expect(await screen.findByText("恢复后的答案")).toBeVisible();
  expect(fetchMock).toHaveBeenCalledWith(
    "http://127.0.0.1:8000/api/v1/chat/runs/run-resume/stream",
    { headers: { Accept: "text/event-stream" } },
  );
});
