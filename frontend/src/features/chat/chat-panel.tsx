"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

import { CitationPanel } from "./citation-panel";
import type {
  ChatMessage,
  Citation,
  RetrievalMode,
  WorkflowUpdate,
} from "./types";

type ChatPanelProps = {
  messages: ChatMessage[];
  workflow?: WorkflowUpdate | null;
  warnings?: string[];
  busy?: boolean;
  onSubmit?: (
    question: string,
    retrievalMode: RetrievalMode,
  ) => Promise<void> | void;
};

const STAGE_LABELS: Record<Exclude<WorkflowUpdate["stage"], "answer">, string> = {
  plan: "正在分析问题",
  retrieval: "正在检索候选片段",
  rerank: "正在重排证据",
  citation_verification: "正在检查引用",
};

export function ChatPanel({
  messages,
  workflow = null,
  warnings = [],
  busy = false,
  onSubmit,
}: ChatPanelProps) {
  const [question, setQuestion] = useState("");
  const [retrievalMode, setRetrievalMode] = useState<RetrievalMode>("auto");
  const formRef = useRef<HTMLFormElement>(null);
  const [selectedCitationId, setSelectedCitationId] = useState<string | null>(
    null,
  );
  const citations = useMemo(
    () => messages.flatMap((message) => (
      message.role === "assistant" ? message.citations ?? [] : []
    )),
    [messages],
  );
  const selectedCitation = citations.find(
    (citation) => citation.id === selectedCitationId,
  );

  useEffect(() => {
    formRef.current?.setAttribute("data-hydrated", "true");
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = question.trim();
    if (!value || !onSubmit || busy) return;
    setQuestion("");
    await onSubmit(value, retrievalMode);
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
      <section className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm sm:p-7">
        {workflow && (
          <p
            role={workflow.status === "failed" ? "alert" : "status"}
            className={`mb-5 rounded-xl px-4 py-3 text-sm ${
              workflow.status === "failed"
                ? "bg-red-50 text-red-800"
                : "bg-blue-50 text-blue-800"
            }`}
          >
            {workflowLabel(workflow)}
          </p>
        )}

        <div className="space-y-5" aria-live="polite">
          {messages.length === 0 && (
            <p className="rounded-2xl border border-dashed border-slate-300 p-8 text-center text-slate-500">
              提出研究问题后，问题与经过引用校验的回答会以对话形式显示。
            </p>
          )}
          {messages.map((message) => {
            if (message.role === "user") {
              return (
                <article key={message.id} className="flex justify-end">
                  <div className="max-w-[85%] rounded-2xl rounded-br-md bg-blue-600 px-5 py-3.5 text-white shadow-sm">
                    <p className="mb-1 text-xs font-semibold text-blue-100">你</p>
                    <p className="whitespace-pre-wrap leading-7">{message.text}</p>
                  </div>
                </article>
              );
            }
            const citationById = new Map(
              (message.citations ?? []).map((citation) => [citation.id, citation]),
            );
            return (
              <article key={message.id} className="space-y-4">
                <p className="text-xs font-semibold text-slate-500">ResearchAgent</p>
                {message.streamingText !== undefined && message.blocks.length === 0 && (
                  <div
                    aria-label="ResearchAgent 回答"
                    className="rounded-2xl rounded-bl-md border border-blue-100 bg-blue-50 p-5 text-slate-900"
                  >
                    <p className="whitespace-pre-wrap leading-7">
                      {message.streamingText || "正在生成回答…"}
                    </p>
                    {message.streaming && (
                      <span className="mt-3 inline-flex items-center gap-2 text-xs font-medium text-blue-700" role="status">
                        <span className="size-2 animate-pulse rounded-full bg-blue-500" />
                        正在流式生成
                      </span>
                    )}
                  </div>
                )}
                {message.blocks.map((block, index) => (
                  <div
                    key={`${message.id}-${index}`}
                    className={`rounded-2xl border p-5 ${
                      block.type === "paper_evidence"
                        ? "border-emerald-200 bg-emerald-50"
                        : "border-amber-200 bg-amber-50"
                    }`}
                  >
                    <p className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-700">
                      {block.type === "paper_evidence" ? "论文证据" : "模型补充"}
                    </p>
                    <p className="mt-3 whitespace-pre-wrap leading-7 text-slate-900">
                      {block.text}
                    </p>
                    {block.type === "paper_evidence" && (
                      <div className="mt-4 flex flex-wrap gap-2">
                        {block.citationIds.map((citationId, citationIndex) => {
                          const citation = citationById.get(citationId);
                          return (
                            <button
                              type="button"
                              key={citationId}
                              disabled={!citation}
                              onClick={() => setSelectedCitationId(citationId)}
                              className="rounded-full bg-white px-3 py-1 text-xs font-medium text-emerald-800 shadow-sm disabled:opacity-40"
                            >
                              引用 {citationIndex + 1}
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </div>
                ))}
              </article>
            );
          })}
        </div>

        {warnings.length > 0 && (
          <div role="alert" className="mt-5 rounded-xl bg-amber-100 px-4 py-3 text-sm text-amber-900">
            {warnings.includes("INSUFFICIENT_PAPER_EVIDENCE") && <p>当前论文证据不足，请缩小问题或增加论文范围。</p>}
            {warnings.includes("CONTEXT_INPUT_SEGMENTED") && <p>较长的输入已分段整理，原文仍可在对话中查看。</p>}
            {warnings.some((warning) => ["CONTEXT_PREPARATION_FALLBACK", "CONTEXT_COMPACTION_FALLBACK", "CONTEXT_HISTORY_PARTIAL", "CONTEXT_MEMORY_SHORTENED", "CONTEXT_OPTIONAL_CONTENT_SHORTENED", "CONTEXT_CONSTRAINTS_PARTIAL"].includes(warning)) && <p>本轮只使用了部分讨论上下文，原文已保留；请核对关键约束，必要时缩小问题范围。</p>}
            {warnings.includes("CONTEXT_EVIDENCE_SHORTENED") && <p>本轮使用了预算范围内的部分论文原文，可打开引用查看来源。</p>}
            {warnings.some((warning) => !warning.startsWith("CONTEXT_") && warning !== "INSUFFICIENT_PAPER_EVIDENCE") && <p>回答带有校验警告，请检查引用原文。</p>}
          </div>
        )}

        {onSubmit && (
          <form
            ref={formRef}
            onSubmit={submit}
            data-hydrated="false"
            className="mt-6 space-y-3"
          >
            <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3">
              <label htmlFor="retrieval-mode" className="text-sm font-medium text-slate-700">
                本轮论文检索
              </label>
              <select
                id="retrieval-mode"
                value={retrievalMode}
                disabled={busy}
                onChange={(event) => setRetrievalMode(event.target.value as RetrievalMode)}
                className="rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 outline-none focus:border-blue-500"
              >
                <option value="auto">自动判断</option>
                <option value="off">不检索，仅继续讨论</option>
                <option value="on">强制检索选定论文</option>
              </select>
              <span className="text-xs text-slate-500">
                Memory 用于延续上下文，只有检索结果可以作为论文证据。
              </span>
            </div>
            <div className="flex gap-3">
            <label htmlFor="chat-question" className="sr-only">
              研究问题
            </label>
            <textarea
              id="chat-question"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              rows={2}
              maxLength={20_000}
              placeholder="询问方法差异、实验结论或论文背景……"
              className="min-h-14 flex-1 resize-none rounded-2xl border border-slate-300 px-4 py-3 outline-none focus:border-blue-500"
            />
            <button
              type="submit"
              disabled={busy || !question.trim()}
              className="rounded-2xl bg-slate-950 px-5 py-3 font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
            >
              {busy ? "处理中" : "发送"}
            </button>
            </div>
          </form>
        )}
      </section>

      <div>
        {selectedCitation ? (
          <CitationPanel
            citation={selectedCitation as Citation}
            onClose={() => setSelectedCitationId(null)}
          />
        ) : (
          <aside className="rounded-2xl border border-dashed border-slate-300 p-5 text-sm leading-6 text-slate-500">
            点击论文证据中的引用按钮，可查看标题、章节、页码和原文。
          </aside>
        )}
      </div>
    </div>
  );
}

function workflowLabel(workflow: WorkflowUpdate): string {
  if (workflow.stage === "answer") {
    return workflow.status === "failed" ? "回答生成失败" : "正在组织引用回答";
  }
  return STAGE_LABELS[workflow.stage];
}
