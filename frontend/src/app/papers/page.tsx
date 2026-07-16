"use client";

import { useEffect, useState } from "react";

import { ApiProblem } from "@/lib/api";
import { listDocuments } from "@/features/papers/api";
import { TaskProgress } from "@/features/papers/task-progress";
import type { PaperSummary, UploadReceipt } from "@/features/papers/types";
import { UploadPanel } from "@/features/papers/upload-panel";

type TaskCard = {
  taskId: string;
  documentId: string;
  title: string;
  createdAt: string;
};

export default function PapersPage() {
  const [papers, setPapers] = useState<PaperSummary[]>([]);
  const [cards, setCards] = useState<Map<string, TaskCard>>(new Map());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void listDocuments(undefined, controller.signal)
      .then((response) => {
        setPapers(response.items);
        setCards(
          new Map(
            response.items.map((paper) => [
              paper.latestTaskId,
              {
                taskId: paper.latestTaskId,
                documentId: paper.id,
                title: paper.title,
                createdAt: paper.createdAt,
              },
            ]),
          ),
        );
        setError(null);
      })
      .catch((caught) => {
        if (caught instanceof DOMException && caught.name === "AbortError") return;
        setError(caught instanceof ApiProblem ? caught.message : "论文库暂时无法加载");
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, []);

  function receiveUpload(receipt: UploadReceipt) {
    setCards((current) => {
      if (current.has(receipt.taskId)) return current;
      const next = new Map(current);
      next.set(receipt.taskId, {
        taskId: receipt.taskId,
        documentId: receipt.documentId,
        title: "新上传论文",
        createdAt: new Date().toISOString(),
      });
      return next;
    });
  }

  return (
    <main className="min-h-screen bg-slate-50 px-4 py-8 text-slate-950 sm:px-8 lg:py-12">
      <div className="mx-auto max-w-6xl">
        <header className="mb-8 max-w-3xl">
          <p className="text-sm font-semibold uppercase tracking-[0.2em] text-blue-700">
            ResearchAgent
          </p>
          <h1 className="mt-3 text-4xl font-semibold tracking-tight sm:text-5xl">论文库</h1>
          <p className="mt-4 text-base leading-7 text-slate-600">
            上传论文，查看持久化处理进度，并在可恢复失败时安全重试。
          </p>
        </header>

        <div className="grid gap-8 lg:grid-cols-[minmax(280px,360px)_1fr] lg:items-start">
          <div className="lg:sticky lg:top-8">
            <UploadPanel onReceipt={receiveUpload} />
            <dl className="mt-4 grid grid-cols-2 gap-3 rounded-2xl border border-slate-200 bg-white p-4 text-sm">
              <div>
                <dt className="text-slate-500">持久论文</dt>
                <dd className="mt-1 text-2xl font-semibold">{papers.length}</dd>
              </div>
              <div>
                <dt className="text-slate-500">任务卡片</dt>
                <dd className="mt-1 text-2xl font-semibold">{cards.size}</dd>
              </div>
            </dl>
          </div>

          <section aria-labelledby="paper-list-title">
            <div className="mb-4 flex items-end justify-between gap-4">
              <div>
                <p className="text-sm font-medium text-slate-500">最近上传</p>
                <h2 id="paper-list-title" className="mt-1 text-2xl font-semibold">处理任务</h2>
              </div>
            </div>
            {loading && <p className="rounded-2xl bg-white p-6 text-slate-600" role="status">正在加载论文库…</p>}
            {error && <p className="rounded-2xl bg-red-50 p-6 text-red-800" role="alert">{error}</p>}
            {!loading && !error && cards.size === 0 && (
              <p className="rounded-2xl border border-dashed border-slate-300 bg-white p-8 text-center text-slate-600">
                还没有论文。上传第一篇 PDF 开始处理。
              </p>
            )}
            <div className="space-y-4">
              {[...cards.values()].map((card) => (
                <article
                  key={card.taskId}
                  aria-label={`${card.title} 处理任务`}
                  className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <h3 className="text-lg font-semibold text-slate-950">{card.title}</h3>
                      <p className="mt-1 text-xs text-slate-500">
                        {new Date(card.createdAt).toLocaleString("zh-CN")}
                      </p>
                    </div>
                    <span className="rounded-full bg-slate-100 px-3 py-1 text-xs text-slate-600">
                      {card.documentId.slice(0, 8)}
                    </span>
                  </div>
                  <TaskProgress taskId={card.taskId} />
                </article>
              ))}
            </div>
          </section>
        </div>
      </div>
    </main>
  );
}
