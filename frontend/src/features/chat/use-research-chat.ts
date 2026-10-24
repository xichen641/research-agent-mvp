"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getChatSessionActivity, getChatMessagePage } from "./api";
import { useChatActivity } from "./chat-activity";
import type {
  ChatMessage,
  CitationData,
  RetrievalMode,
  UIStreamPart,
  WorkflowUpdate,
} from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
type PendingRequest = {
  requestId: string;
  question: string;
  retrievalMode: RetrievalMode;
  retryOf?: string;
};

function mergeMessages(current: ChatMessage[], incoming: ChatMessage[]) {
  const merged = new Map(current.map((message) => [message.id, message]));
  for (const message of incoming) merged.set(message.id, message);
  return [...merged.values()].sort(
    (a, b) =>
      (a.sequence ?? Number.MAX_SAFE_INTEGER) -
      (b.sequence ?? Number.MAX_SAFE_INTEGER),
  );
}

export function useResearchChat(sessionId: string) {
  const { setActivity } = useChatActivity();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [workflow, setWorkflow] = useState<WorkflowUpdate | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [canResume, setCanResume] = useState(false);
  const [canRetry, setCanRetry] = useState(false);
  const [beforeSequence, setBeforeSequence] = useState<number | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const lastEventId = useRef(0);
  const runId = useRef<string | null>(null);
  const assistantMessageId = useRef<string | null>(null);
  const runFailed = useRef(false);
  const pendingRequest = useRef<PendingRequest | null>(null);
  const working = useRef(false);
  const generation = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const storageKey = `research-agent.pending-chat.${sessionId}`;

  const clearPending = useCallback(() => {
    pendingRequest.current = null;
    window.localStorage.removeItem(storageKey);
  }, [storageKey]);

  const consume = useCallback(
    async (response: Response, signal?: AbortSignal) => {
      if (!response.ok || !response.body)
        throw new Error(`连接失败（${response.status}），可恢复原任务`);
      let terminal = false;
      await parseEventStream(response.body, async (id, part) => {
        if (signal?.aborted) return;
        if (id !== null && id <= lastEventId.current) return;
        if (id !== null) lastEventId.current = id;
        if (part === "[DONE]") {
          terminal = true;
          return;
        }
        if (part.type === "start") {
          runId.current = part.messageId;
          setActivity({
            sessionId,
            runId: part.messageId,
            status: "RUNNING",
            updatedAt: new Date().toISOString(),
          });
        } else if (part.type === "data-workflow") {
          setWorkflow(part.data);
          if (part.data.status === "failed") runFailed.current = true;
        } else if (part.type === "text-start") {
          assistantMessageId.current = part.id;
          startAssistantMessage(part.id, setMessages);
        } else if (part.type === "text-delta") {
          assistantMessageId.current = part.id;
          appendAssistantDelta(part.id, part.delta, setMessages);
          await waitForPaint();
        } else if (part.type === "text-end") {
          finishAssistantMessage(part.id, setMessages);
        } else if (part.type === "data-citations") {
          applyCitationData(
            part.data,
            assistantMessageId.current ?? runId.current,
            setMessages,
            setWarnings,
          );
        }
      });
      if (signal?.aborted) return;
      if (!terminal)
        throw new Error("连接已中断，任务结果尚未确认，请恢复连接");
      setCanResume(false);
      setCanRetry(runFailed.current);
      if (!runFailed.current)
        setWorkflow({ stage: "answer", status: "completed" });
      setActivity({
        sessionId,
        runId: runId.current,
        status: runFailed.current ? "FAILED" : "SUCCEEDED",
        updatedAt: new Date().toISOString(),
      });
      const history = await getChatMessagePage(sessionId);
      if (!signal?.aborted) {
        setMessages((current) => mergeMessages(current, history.items));
        setWarnings(history.warnings);
      }
    },
    [sessionId, setActivity],
  );

  const connect = useCallback(
    async (id: string, signal?: AbortSignal) => {
      const response = await fetch(`${API_URL}/api/v1/chat/runs/${id}/stream`, {
        headers: {
          Accept: "text/event-stream",
          ...(lastEventId.current > 0
            ? { "Last-Event-ID": String(lastEventId.current) }
            : {}),
        },
        signal,
      });
      await consume(response, signal);
    },
    [consume],
  );

  const submitPending = useCallback(
    async (pending: PendingRequest, signal?: AbortSignal) => {
      const path = pending.retryOf
        ? `/api/v1/chat/runs/${pending.retryOf}/retry`
        : `/api/v1/chat/sessions/${sessionId}/messages`;
      const response = await fetch(`${API_URL}${path}`, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify(
          pending.retryOf
            ? { client_request_id: pending.requestId }
            : {
                question: pending.question,
                retrieval_mode: pending.retrievalMode,
                client_request_id: pending.requestId,
              },
        ),
        signal,
      });
      if (!response.ok) {
        if (
          response.status >= 400 &&
          response.status < 500 &&
          response.status !== 408
        )
          clearPending();
        const problem = await response.json().catch(() => ({}));
        throw new Error(
          typeof problem.message === "string"
            ? problem.message
            : `提交状态待核对（${response.status}）`,
        );
      }
      if (signal?.aborted) return;
      if (response.headers.get("Content-Type")?.includes("text/event-stream")) {
        clearPending();
        await consume(response, signal);
        return;
      }
      const accepted = (await response.json()) as {
        run_id: string;
        user_message_id?: string;
      };
      if (signal?.aborted) return;
      runId.current = accepted.run_id;
      clearPending();
      if (accepted.user_message_id)
        setMessages((current) =>
          mergeMessages(
            current.map((message) =>
              message.id === pending.requestId
                ? { ...message, id: accepted.user_message_id! }
                : message,
            ),
            [],
          ),
        );
      setActivity({
        sessionId,
        runId: accepted.run_id,
        status: "QUEUED",
        updatedAt: new Date().toISOString(),
      });
      await connect(accepted.run_id, signal);
    },
    [clearPending, consume, connect, sessionId, setActivity],
  );

  useEffect(() => {
    let cancelled = false;
    const restoring = generation.current;
    const abort = new AbortController();
    controller.current = abort;
    void (async () => {
      try {
        const [history, activity] = await Promise.all([
          getChatMessagePage(sessionId),
          getChatSessionActivity(sessionId),
        ]);
        if (cancelled) return;
        setMessages((current) => mergeMessages(current, history.items));
        setBeforeSequence(history.nextBeforeSequence);
        if (generation.current !== restoring) return;
        const saved = window.localStorage.getItem(storageKey);
        if (saved) {
          const pending = JSON.parse(saved) as PendingRequest;
          pendingRequest.current = pending;
          if (!pending.retryOf)
            setMessages((current) =>
              mergeMessages(current, [
                { id: pending.requestId, role: "user", text: pending.question },
              ]),
            );
          setBusy(true);
          working.current = true;
          await submitPending(pending, abort.signal);
          return;
        }
        setWarnings(
          activity.status === "SUCCEEDED" || activity.status === "IDLE"
            ? history.warnings
            : [],
        );
        if (activity.status === "IDLE" || !activity.runId) return;
        runId.current = activity.runId;
        runFailed.current = activity.status === "FAILED";
        setCanRetry(runFailed.current);
        setActivity({
          sessionId,
          runId: activity.runId,
          status: activity.status,
          updatedAt: new Date().toISOString(),
        });
        if (activity.status !== "QUEUED" && activity.status !== "RUNNING")
          return;
        setBusy(true);
        working.current = true;
        setWorkflow({ stage: "plan", status: "running" });
        await connect(activity.runId, abort.signal);
      } catch (caught) {
        if (!cancelled && generation.current === restoring) {
          setError(caught instanceof Error ? caught.message : "会话恢复失败");
          setCanResume(Boolean(pendingRequest.current || runId.current));
        }
      } finally {
        if (!cancelled && generation.current === restoring) {
          setBusy(false);
          working.current = false;
        }
      }
    })();
    return () => {
      cancelled = true;
      abort.abort();
      controller.current?.abort();
    };
  }, [sessionId, storageKey, submitPending, connect, setActivity]);

  const perform = useCallback(
    async (pending?: PendingRequest) => {
      if (working.current) return;
      working.current = true;
      generation.current += 1;
      const abort = new AbortController();
      controller.current = abort;
      setBusy(true);
      setError(null);
      setCanResume(false);
      setCanRetry(false);
      try {
        if (pending) {
          pendingRequest.current = pending;
          window.localStorage.setItem(storageKey, JSON.stringify(pending));
          lastEventId.current = 0;
          runId.current = null;
          runFailed.current = false;
          assistantMessageId.current = null;
          setWorkflow({ stage: "plan", status: "running" });
          setWarnings([]);
          if (!pending.retryOf)
            setMessages((current) =>
              mergeMessages(current, [
                { id: pending.requestId, role: "user", text: pending.question },
              ]),
            );
          await submitPending(pending, abort.signal);
        } else if (pendingRequest.current) {
          await submitPending(pendingRequest.current, abort.signal);
        } else if (runId.current) {
          await connect(runId.current, abort.signal);
        }
      } catch (caught) {
        if (!abort.signal.aborted) {
          setError(
            caught instanceof Error ? caught.message : "连接中断，请恢复原请求",
          );
          setCanResume(Boolean(pendingRequest.current || runId.current));
        }
      } finally {
        if (!abort.signal.aborted) {
          setBusy(false);
          working.current = false;
        }
      }
    },
    [storageKey, submitPending, connect],
  );

  const send = useCallback(
    async (question: string, retrievalMode: RetrievalMode = "auto") => {
      if (pendingRequest.current || canResume) {
        setError("请先恢复上一次发送或连接，核对原任务状态");
        return;
      }
      await perform({
        requestId: crypto.randomUUID(),
        question,
        retrievalMode,
      });
    },
    [canResume, perform],
  );
  const resume = useCallback(() => perform(), [perform]);
  const retry = useCallback(async () => {
    if (!runId.current || !canRetry) return;
    const original = runId.current;
    const failedMessage = assistantMessageId.current;
    setMessages((current) =>
      current.filter((message) => message.id !== failedMessage),
    );
    await perform({
      requestId: crypto.randomUUID(),
      question: "",
      retrievalMode: "auto",
      retryOf: original,
    });
  }, [canRetry, perform]);
  const loadOlder = useCallback(async () => {
    if (beforeSequence === null || loadingOlder) return;
    setLoadingOlder(true);
    try {
      const page = await getChatMessagePage(sessionId, beforeSequence);
      setMessages((current) => mergeMessages(current, page.items));
      setBeforeSequence(page.nextBeforeSequence);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "历史消息加载失败");
    } finally {
      setLoadingOlder(false);
    }
  }, [beforeSequence, loadingOlder, sessionId]);

  return {
    messages,
    workflow,
    warnings,
    busy,
    error,
    send,
    resume,
    retry,
    canResume,
    canRetry,
    loadOlder,
    loadingOlder,
    hasOlder: beforeSequence !== null,
  };
}

type MessageSetter = React.Dispatch<React.SetStateAction<ChatMessage[]>>;
type WarningSetter = React.Dispatch<React.SetStateAction<string[]>>;

function applyCitationData(
  data: CitationData,
  currentRunId: string | null,
  setMessages: MessageSetter,
  setWarnings: WarningSetter,
) {
  const messageId = currentRunId ?? crypto.randomUUID();
  setMessages((current) => {
    const existing = current.findIndex(
      (message) => message.role === "assistant" && message.id === messageId,
    );
    if (existing < 0) {
      return [
        ...current,
        {
          id: messageId,
          role: "assistant",
          blocks: data.blocks,
          citations: data.citations,
          streaming: false,
        },
      ];
    }
    return current.map((message, index) => {
      if (index !== existing || message.role !== "assistant") return message;
      return {
        ...message,
        blocks: data.blocks,
        citations: data.citations,
        streamingText:
          data.blocks.length > 0 ? undefined : message.streamingText,
        streaming: false,
      };
    });
  });
  setWarnings(data.warnings);
}

function startAssistantMessage(messageId: string, setMessages: MessageSetter) {
  setMessages((current) => {
    if (current.some((message) => message.id === messageId)) return current;
    return [
      ...current,
      {
        id: messageId,
        role: "assistant",
        blocks: [],
        streamingText: "",
        streaming: true,
      },
    ];
  });
}

function appendAssistantDelta(
  messageId: string,
  delta: string,
  setMessages: MessageSetter,
) {
  setMessages((current) => {
    const existing = current.findIndex(
      (message) => message.role === "assistant" && message.id === messageId,
    );
    if (existing < 0) {
      return [
        ...current,
        {
          id: messageId,
          role: "assistant",
          blocks: [],
          streamingText: delta,
          streaming: true,
        },
      ];
    }
    return current.map((message, index) =>
      index === existing && message.role === "assistant"
        ? {
            ...message,
            streamingText: `${message.streamingText ?? ""}${delta}`,
            streaming: true,
          }
        : message,
    );
  });
}

function finishAssistantMessage(messageId: string, setMessages: MessageSetter) {
  setMessages((current) =>
    current.map((message) =>
      message.role === "assistant" && message.id === messageId
        ? { ...message, streaming: false }
        : message,
    ),
  );
}

function waitForPaint(): Promise<void> {
  return new Promise((resolve) => {
    if (typeof requestAnimationFrame === "function") {
      requestAnimationFrame(() => resolve());
      return;
    }
    setTimeout(resolve, 0);
  });
}

export async function parseEventStream(
  stream: ReadableStream<Uint8Array>,
  onPart: (
    id: number | null,
    part: UIStreamPart | "[DONE]",
  ) => Promise<void> | void,
) {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done }).replaceAll("\r\n", "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const event = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      await parseEvent(event, onPart);
      boundary = buffer.indexOf("\n\n");
    }
    if (done) break;
  }
  if (buffer.trim()) await parseEvent(buffer, onPart);
}

async function parseEvent(
  event: string,
  onPart: (
    id: number | null,
    part: UIStreamPart | "[DONE]",
  ) => Promise<void> | void,
) {
  if (!event || event.startsWith(":")) return;
  let id: number | null = null;
  const dataLines: string[] = [];
  for (const line of event.split("\n")) {
    if (line.startsWith("id:")) {
      const parsed = Number.parseInt(line.slice(3).trim(), 10);
      if (Number.isFinite(parsed)) id = parsed;
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart());
    }
  }
  const data = dataLines.join("\n");
  if (!data) return;
  if (data === "[DONE]") {
    await onPart(id, "[DONE]");
    return;
  }
  await onPart(id, JSON.parse(data) as UIStreamPart);
}
