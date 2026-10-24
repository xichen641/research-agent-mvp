import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { ChatWorkspace } from "./chat-workspace";

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

it("restores the saved context warning when reopening a completed discussion", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).endsWith("/messages")) {
      return Response.json({
        items: [{
          id: "answer",
          sequence: 2,
          role: "assistant",
          content: {
            blocks: [{ type: "model_knowledge", text: "当前显存12GB。" }],
            citations: [],
            warnings: ["CONTEXT_COMPACTION_FALLBACK"],
          },
          created_at: "2026-10-24T00:00:00Z",
        }],
      });
    }
    return Response.json({
      session_id: "session-1",
      run_id: "run-1",
      status: "SUCCEEDED",
      error_code: null,
      stream_url: null,
    });
  }));
  render(<ChatWorkspace sessionId="session-1" />);
  expect(await screen.findByText(/本轮只使用了部分讨论上下文/)).toBeVisible();
  expect(screen.queryByText("回答带有校验警告，请检查引用原文。")).not.toBeInTheDocument();
});

it("shows the user question immediately and renders answer deltas before completion", async () => {
  const encoder = new TextEncoder();
  let finishStream: (() => void) | undefined;
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(
        encoder.encode(
          [
            'id: 1\ndata: {"type":"start","messageId":"run-1"}\n\n',
            'id: 2\ndata: {"type":"text-start","id":"answer-1"}\n\n',
            'id: 3\ndata: {"type":"text-delta","id":"answer-1","delta":"正在生成的回答"}\n\n',
          ].join(""),
        ),
      );
      finishStream = () => {
        controller.enqueue(
          encoder.encode(
            [
              'id: 4\ndata: {"type":"text-delta","id":"answer-1","delta":"已经完成。"}\n\n',
              'id: 5\ndata: {"type":"text-end","id":"answer-1"}\n\n',
              'id: 6\ndata: {"type":"data-citations","data":{"blocks":[{"type":"paper_evidence","text":"正在生成的回答已经完成。","citationIds":[]}],"citations":[],"warnings":[]}}\n\n',
              'id: 7\ndata: {"type":"finish"}\n\n',
              "id: 8\ndata: [DONE]\n\n",
            ].join(""),
          ),
        );
        controller.close();
      };
    },
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
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
    }),
  );

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

  const request = vi
    .mocked(fetch)
    .mock.calls.find((call) => call[1]?.method === "POST")?.[1];
  expect(JSON.parse(String(request?.body))).toEqual({
    question: "这篇论文的核心方法是什么？",
    retrieval_mode: "off",
    client_request_id: expect.any(String),
  });

  await act(async () => finishStream?.());
  expect(await screen.findByText("正在生成的回答已经完成。")).toBeVisible();
});

it("reconnects to a running answer when the chat workspace is reopened", async () => {
  const encoder = new TextEncoder();
  const resumedStream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(
        encoder.encode(
          [
            'id: 1\ndata: {"type":"start","messageId":"run-resume"}\n\n',
            'id: 2\ndata: {"type":"text-start","id":"answer-resume"}\n\n',
            'id: 3\ndata: {"type":"text-delta","id":"answer-resume","delta":"恢复后的答案"}\n\n',
            'id: 4\ndata: {"type":"text-end","id":"answer-resume"}\n\n',
            'id: 5\ndata: {"type":"data-citations","data":{"blocks":[{"type":"model_knowledge","text":"恢复后的答案"}],"citations":[],"warnings":[]}}\n\n',
            'id: 6\ndata: {"type":"finish"}\n\n',
            "id: 7\ndata: [DONE]\n\n",
          ].join(""),
        ),
      );
      controller.close();
    },
  });
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/api/v1/chat/sessions/session-1/messages")) {
      return Response.json({
        items: [
          {
            id: "question-1",
            role: "user",
            content: { text: "离开页面前的问题" },
            created_at: "2026-08-30T00:00:00Z",
          },
        ],
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
  await waitFor(() => expect(screen.getByText("恢复后的答案")).toBeVisible());
  expect(fetchMock).toHaveBeenCalledWith(
    "http://127.0.0.1:8000/api/v1/chat/runs/run-resume/stream",
    expect.objectContaining({
      headers: { Accept: "text/event-stream" },
      signal: expect.any(AbortSignal),
    }),
  );
});

it("recovers an unknown submission with the same request ID and canonical message identities", async () => {
  let posts = 0;
  const bodies: Array<{ client_request_id: string }> = [];
  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "POST") {
        bodies.push(JSON.parse(String(init.body)));
        posts += 1;
        if (posts === 1) throw new TypeError("network response lost");
        return Response.json(
          {
            run_id: "run-1",
            user_message_id: "user-1",
            stream_url: "/api/v1/chat/runs/run-1/stream",
          },
          { status: 202 },
        );
      }
      if (url.endsWith("/activity"))
        return Response.json({
          session_id: "session-1",
          run_id: null,
          status: "IDLE",
          error_code: null,
          stream_url: null,
        });
      if (url.endsWith("/messages"))
        return Response.json({
          items:
            posts < 2
              ? []
              : [
                  {
                    id: "user-1",
                    role: "user",
                    sequence: 1,
                    content: { text: "断网验收" },
                  },
                  {
                    id: "assistant-1",
                    role: "assistant",
                    sequence: 2,
                    content: {
                      blocks: [{ type: "model_knowledge", text: "已恢复" }],
                      citations: [],
                    },
                  },
                ],
        });
      if (url.endsWith("/stream"))
        return new Response(
          [
            'id: 1\ndata: {"type":"start","messageId":"run-1"}\n\n',
            'id: 2\ndata: {"type":"text-start","id":"assistant-1"}\n\n',
            'id: 3\ndata: {"type":"text-delta","id":"assistant-1","delta":"已恢复"}\n\n',
            'id: 4\ndata: {"type":"data-citations","data":{"blocks":[{"type":"model_knowledge","text":"已恢复"}],"citations":[],"warnings":[]}}\n\n',
            'id: 5\ndata: {"type":"finish"}\n\n',
            "id: 6\ndata: [DONE]\n\n",
          ].join(""),
          { headers: { "Content-Type": "text/event-stream" } },
        );
      throw new Error(`Unexpected URL: ${url}`);
    },
  );
  vi.stubGlobal("fetch", fetchMock);
  render(<ChatWorkspace sessionId="session-1" />);
  fireEvent.change(screen.getByLabelText("研究问题"), {
    target: { value: "断网验收" },
  });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  fireEvent.click(
    await screen.findByRole("button", { name: "恢复发送或连接" }),
  );
  await waitFor(() => expect(screen.getAllByText("已恢复")).toHaveLength(1));
  fireEvent.change(screen.getByLabelText("研究问题"), {
    target: { value: "下一问题" },
  });
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "发送" })).not.toBeDisabled(),
  );
  expect(bodies).toHaveLength(2);
  expect(bodies[0].client_request_id).toBe(bodies[1].client_request_id);
  expect(screen.getAllByText("断网验收")).toHaveLength(1);
  expect(
    window.localStorage.getItem("research-agent.pending-chat.session-1"),
  ).toBeNull();
});

it("loads older history by sequence without duplicating the current page", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/activity"))
        return Response.json({
          session_id: "session-1",
          run_id: null,
          status: "IDLE",
          stream_url: null,
          error_code: null,
        });
      const older = url.includes("before_sequence=3");
      return Response.json({
        items: [
          {
            id: older ? "old" : "new",
            role: "user",
            sequence: older ? 1 : 3,
            content: { text: older ? "更早问题" : "最近问题" },
          },
        ],
        next_before_sequence: older ? null : 3,
      });
    }),
  );
  render(<ChatWorkspace sessionId="session-1" />);
  fireEvent.click(await screen.findByRole("button", { name: "加载更早消息" }));
  expect(await screen.findByText("更早问题")).toBeVisible();
  expect(screen.getAllByText("最近问题")).toHaveLength(1);
  await waitFor(() =>
    expect(
      screen.queryByRole("button", { name: "加载更早消息" }),
    ).not.toBeInTheDocument(),
  );
});

it("retries a failed run through the retry endpoint without creating a new user message", async () => {
  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "POST")
        return Response.json(
          { run_id: "retry-1", user_message_id: "user-1" },
          { status: 202 },
        );
      if (url.endsWith("/activity"))
        return Response.json({
          session_id: "session-1",
          run_id: "failed-1",
          status: "FAILED",
          stream_url: "/api/v1/chat/runs/failed-1/stream",
          error_code: "TIMEOUT",
        });
      if (url.endsWith("/messages"))
        return Response.json({
          items: [
            {
              id: "user-1",
              role: "user",
              sequence: 1,
              content: { text: "原始问题" },
            },
          ],
        });
      return new Response(
        'id: 1\ndata: {"type":"start","messageId":"retry-1"}\n\nid: 2\ndata: [DONE]\n\n',
        { headers: { "Content-Type": "text/event-stream" } },
      );
    },
  );
  vi.stubGlobal("fetch", fetchMock);
  render(<ChatWorkspace sessionId="session-1" />);
  fireEvent.click(await screen.findByRole("button", { name: "重试失败任务" }));
  await waitFor(() =>
    expect(
      screen.queryByRole("button", { name: "重试失败任务" }),
    ).not.toBeInTheDocument(),
  );
  const posts = fetchMock.mock.calls.filter(
    ([, init]) => init?.method === "POST",
  );
  expect(posts).toHaveLength(1);
  expect(String(posts[0][0])).toContain("/runs/failed-1/retry");
  expect(JSON.parse(String(posts[0][1]?.body))).toEqual({
    client_request_id: expect.any(String),
  });
  expect(screen.getAllByText("原始问题")).toHaveLength(1);
});
