"use client";

import { useCallback, useRef, useState } from "react";

import type {
  ChatMessage,
  CitationData,
  UIStreamPart,
  WorkflowUpdate,
} from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export function useResearchChat(sessionId: string) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [workflow, setWorkflow] = useState<WorkflowUpdate | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lastEventId = useRef(0);
  const runId = useRef<string | null>(null);
  const assistantMessageId = useRef<string | null>(null);

  const consume = useCallback(async (response: Response) => {
    if (!response.ok || !response.body) {
      throw new Error(`Chat stream failed with status ${response.status}`);
    }
    await parseEventStream(response.body, async (id, part) => {
      if (id !== null) lastEventId.current = id;
      if (part === "[DONE]") return;
      if (part.type === "start") {
        runId.current = part.messageId;
      } else if (part.type === "data-workflow") {
        setWorkflow(part.data);
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
  }, []);

  const send = useCallback(
    async (question: string) => {
      setBusy(true);
      setError(null);
      setWorkflow({ stage: "plan", status: "running" });
      setWarnings([]);
      lastEventId.current = 0;
      runId.current = null;
      assistantMessageId.current = null;
      setMessages((current) => [
        ...current,
        { id: crypto.randomUUID(), role: "user", text: question },
      ]);
      try {
        const response = await fetch(
          `${API_URL}/api/v1/chat/sessions/${sessionId}/messages`,
          {
            method: "POST",
            headers: {
              Accept: "text/event-stream",
              "Content-Type": "application/json",
            },
            body: JSON.stringify({ question }),
          },
        );
        await consume(response);
      } catch (caught) {
        if (runId.current) {
          try {
            const resumed = await fetch(
              `${API_URL}/api/v1/chat/runs/${runId.current}/stream`,
              {
                headers: {
                  Accept: "text/event-stream",
                  "Last-Event-ID": String(lastEventId.current),
                },
              },
            );
            await consume(resumed);
            return;
          } catch {
            // Fall through to the stable user-facing failure below.
          }
        }
        setError(caught instanceof Error ? caught.message : "聊天请求失败");
        setWorkflow({ stage: "answer", status: "failed" });
      } finally {
        setBusy(false);
      }
    },
    [consume, sessionId],
  );

  return { messages, workflow, warnings, busy, error, send };
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
    const existing = current.findIndex((message) => (
      message.role === "assistant" && message.id === messageId
    ));
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
        streamingText: data.blocks.length > 0 ? undefined : message.streamingText,
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
    const existing = current.findIndex((message) => (
      message.role === "assistant" && message.id === messageId
    ));
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
    return current.map((message, index) => (
      index === existing && message.role === "assistant"
        ? {
            ...message,
            streamingText: `${message.streamingText ?? ""}${delta}`,
            streaming: true,
          }
        : message
    ));
  });
}

function finishAssistantMessage(messageId: string, setMessages: MessageSetter) {
  setMessages((current) => current.map((message) => (
    message.role === "assistant" && message.id === messageId
      ? { ...message, streaming: false }
      : message
  )));
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
