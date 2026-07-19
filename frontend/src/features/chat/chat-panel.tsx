"use client";

import { FormEvent, useMemo, useState } from "react";

import { CitationPanel } from "./citation-panel";
import type { ChatMessage, Citation, WorkflowUpdate } from "./types";

type ChatPanelProps = {
  messages: ChatMessage[];
  workflow?: WorkflowUpdate | null;
  warnings?: string[];
  busy?: boolean;
  onSubmit?: (question: string) => Promise<void> | void;
};

const STAGE_LABELS: Record<WorkflowUpdate["stage"], string> = {
  plan: "正在分析问题",
  retrieval: "正在检索候选片段",
  rerank: "正在重排证据",
  citation_verification: "正在检查引用",
  answer: "回答生成失败",
};

export function ChatPanel({
  messages,
  workflow = null,
  warnings = [],
  busy = false,
  onSubmit,
}: ChatPanelProps) {
  const [question, setQuestion] = useState("");
  const [selectedCitationId, setSelectedCitationId] = useState<string | null>(
    null,
  );
  const citations = useMemo(
    () => messages.flatMap((message) => message.citations ?? []),
    [messages],
  );
  const selectedCitation = citations.find(
    (citation) => citation.id === selectedCitationId,
  );

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = question.trim();
    if (!value || !onSubmit || busy) return;
    setQuestion("");
    await onSubmit(value);
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
            {STAGE_LABELS[workflow.stage]}
          </p>
        )}

        <div className="space-y-5" aria-live="polite">
          {messages.length === 0 && (
            <p className="rounded-2xl border border-dashed border-slate-300 p-8 text-center text-slate-500">
              选择论文后提出一个问题，回答会在引用校验完成后显示。
            </p>
          )}
          {messages.map((message) => {
            const citationById = new Map(
              (message.citations ?? []).map((citation) => [citation.id, citation]),
            );
            return (
              <article key={message.id} className="space-y-4">
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
            {warnings.includes("INSUFFICIENT_PAPER_EVIDENCE")
              ? "当前论文证据不足，请缩小问题或增加论文范围。"
              : "回答带有校验警告，请检查引用原文。"}
          </div>
        )}

        {onSubmit && (
          <form onSubmit={submit} className="mt-6 flex gap-3">
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
