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

  const consume = useCallback(async (response: Response) => {
    if (!response.ok || !response.body) {
      throw new Error(`Chat stream failed with status ${response.status}`);
    }
    await parseEventStream(response.body, (id, part) => {
      if (id !== null) lastEventId.current = id;
      if (part === "[DONE]") return;
      if (part.type === "start") {
        runId.current = part.messageId;
      } else if (part.type === "data-workflow") {
        setWorkflow(part.data);
      } else if (part.type === "data-citations") {
        applyCitationData(part.data, runId.current, setMessages, setWarnings);
      }
    });
  }, []);

  const send = useCallback(
    async (question: string) => {
      setBusy(true);
      setError(null);
      setWorkflow({ stage: "plan", status: "running" });
      lastEventId.current = 0;
      runId.current = null;
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
  setMessages((current) => [
    ...current,
    {
      id: currentRunId ?? crypto.randomUUID(),
      blocks: data.blocks,
      citations: data.citations,
    },
  ]);
  setWarnings(data.warnings);
}

export async function parseEventStream(
  stream: ReadableStream<Uint8Array>,
  onPart: (id: number | null, part: UIStreamPart | "[DONE]") => void,
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
      parseEvent(event, onPart);
      boundary = buffer.indexOf("\n\n");
    }
    if (done) break;
  }
  if (buffer.trim()) parseEvent(buffer, onPart);
}

function parseEvent(
  event: string,
  onPart: (id: number | null, part: UIStreamPart | "[DONE]") => void,
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
    onPart(id, "[DONE]");
    return;
  }
  onPart(id, JSON.parse(data) as UIStreamPart);
}
