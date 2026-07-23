"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { createChatSession } from "@/features/chat/api";
import {
  documentPdfUrl,
  listDocuments,
} from "@/features/papers/api";
import { TaskProgress } from "@/features/papers/task-progress";
import type {
  PaperSummary,
  TaskState,
  UploadReceipt,
} from "@/features/papers/types";
import { UploadPanel } from "@/features/papers/upload-panel";
import { ApiProblem } from "@/lib/api";


type PaperFilter = "ALL" | "READY" | "ACTIVE" | "ATTENTION";
type ChatLaunch = "LIBRARY" | "SELECTION" | "PAPER";

const FILTERS: Array<{ value: PaperFilter; label: string }> = [
  { value: "ALL", label: "全部" },
  { value: "READY", label: "已就绪" },
  { value: "ACTIVE", label: "处理中" },
  { value: "ATTENTION", label: "需处理" },
];

export default function PapersPage() {
  const router = useRouter();
  const [papers, setPapers] = useState<PaperSummary[]>([]);
  const [taskStates, setTaskStates] = useState<Map<string, TaskState>>(new Map());
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [chatScopeIds, setChatScopeIds] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<PaperFilter>("ALL");
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [startingChat, setStartingChat] = useState<ChatLaunch | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const replacePapers = useCallback(async (signal?: AbortSignal) => {
    const response = await listDocuments(undefined, signal);
    setPapers(response.items);
    setNextCursor(response.nextCursor);
    setSelectedId((current) => (
      current && response.items.some((paper) => paper.id === current)
        ? current
        : response.items[0]?.id ?? null
    ));
    return response.items;
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void listDocuments(undefined, controller.signal)
      .then((response) => {
        setPapers(response.items);
        setNextCursor(response.nextCursor);
        setSelectedId(response.items[0]?.id ?? null);
        setError(null);
      })
      .catch((caught) => {
        if (caught instanceof DOMException && caught.name === "AbortError") return;
        setError(caught instanceof ApiProblem ? caught.message : "论文库暂时无法加载");
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [replacePapers]);

  useEffect(() => {
    if (!uploadOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setUploadOpen(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [uploadOpen]);

  const selected = papers.find((paper) => paper.id === selectedId) ?? null;
  const visiblePapers = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    return papers.filter((paper) => {
      const task = taskStates.get(paper.id);
      const status = effectiveStatus(paper, task);
      const matchesText = !normalized
        || paper.title.toLocaleLowerCase().includes(normalized)
        || paper.id.toLocaleLowerCase().includes(normalized);
      const matchesFilter = filter === "ALL"
        || (filter === "READY" && status === "READY")
        || (filter === "ACTIVE" && ["QUEUED", "RUNNING"].includes(status))
        || (filter === "ATTENTION" && ["FAILED", "CANCELLED", "NEEDS_REVIEW"].includes(status));
      return matchesText && matchesFilter;
    });
  }, [filter, papers, query, taskStates]);

  const readyCount = papers.filter((paper) => (
    effectiveStatus(paper, taskStates.get(paper.id)) === "READY"
  )).length;
  const activeCount = papers.filter((paper) => (
    ["QUEUED", "RUNNING"].includes(effectiveStatus(paper, taskStates.get(paper.id)))
  )).length;

  const selectedReadyScope = chatScopeIds.filter((documentId) => {
    const paper = papers.find((item) => item.id === documentId);
    return paper && isReady(paper, taskStates.get(documentId));
  });

  const recordTaskState = useCallback((documentId: string, task: TaskState) => {
    setTaskStates((current) => {
      if (current.get(documentId) === task) return current;
      const next = new Map(current);
      next.set(documentId, task);
      return next;
    });
  }, []);

  async function receiveUpload(receipt: UploadReceipt) {
    setSelectedId(receipt.documentId);
    try {
      await replacePapers();
      setSelectedId(receipt.documentId);
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiProblem ? caught.message : "上传已受理，但列表刷新失败");
    }
  }

  async function loadMore() {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const response = await listDocuments(nextCursor);
      setPapers((current) => deduplicatePapers([...current, ...response.items]));
      setNextCursor(response.nextCursor);
    } catch (caught) {
      setError(caught instanceof ApiProblem ? caught.message : "更多论文暂时无法加载");
    } finally {
      setLoadingMore(false);
    }
  }

  function toggleChatScope(documentId: string) {
    setActionError(null);
    if (chatScopeIds.includes(documentId)) {
      setChatScopeIds((current) => current.filter((item) => item !== documentId));
      return;
    }
    if (chatScopeIds.length >= 100) {
      setActionError("一次最多选择 100 篇论文");
      return;
    }
    setChatScopeIds((current) => [...current, documentId]);
  }

  async function startChat(
    launch: ChatLaunch,
    title: string,
    documentIds: string[],
  ) {
    if (startingChat || (launch !== "LIBRARY" && documentIds.length === 0)) return;
    setStartingChat(launch);
    setActionError(null);
    try {
      const session = await createChatSession(title, documentIds);
      router.push(`/chat/${session.id}`);
    } catch (caught) {
      setActionError(caught instanceof ApiProblem ? caught.message : "问答空间创建失败");
    } finally {
      setStartingChat(null);
    }
  }

  return (
    <main className="min-h-screen px-4 py-5 sm:px-6 lg:px-8 lg:py-7">
      <div className="mx-auto max-w-[1500px]">
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-blue-700">
              Research workspace
            </p>
            <h1 className="mt-1.5 text-3xl font-semibold tracking-tight text-slate-950">
              论文工作台
            </h1>
            <p className="mt-2 text-sm text-slate-500">
              选择单篇、组合多篇或检索全库，在同一上下文中进入问答、笔记与原文。
            </p>
          </div>
          <button
            type="button"
            onClick={() => setUploadOpen(true)}
            className="inline-flex items-center gap-2 rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-blue-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
          >
            <span aria-hidden="true" className="text-lg leading-none">+</span>
            导入论文
          </button>
        </header>

        <section aria-label="论文概览" className="mt-6 grid gap-3 sm:grid-cols-3">
          <Metric label="已载入论文" value={papers.length} tone="slate" />
          <Metric label="可研究" value={readyCount} tone="emerald" />
          <Metric label="处理中" value={activeCount} tone="blue" />
        </section>

        <section
          aria-labelledby="retrieval-chat-title"
          className="mt-5 rounded-2xl border border-blue-200 bg-gradient-to-r from-blue-50 to-white p-4 shadow-sm sm:p-5"
        >
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.16em] text-blue-700">Retrieval scope</p>
              <h2 id="retrieval-chat-title" className="mt-1 text-lg font-semibold text-slate-950">证据检索问答</h2>
              <p className="mt-1 text-sm text-slate-600">
                已选择 {selectedReadyScope.length} 篇就绪论文；全库模式会检索全部 READY 当前版本。
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              {chatScopeIds.length > 0 && (
                <button
                  type="button"
                  onClick={() => setChatScopeIds([])}
                  className="rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-600 hover:border-slate-400"
                >
                  清空选择
                </button>
              )}
              <button
                type="button"
                disabled={selectedReadyScope.length === 0 || startingChat !== null}
                onClick={() => void startChat(
                  "SELECTION",
                  `多篇论文问答（${selectedReadyScope.length} 篇）`,
                  selectedReadyScope,
                )}
                className="rounded-xl border border-blue-300 bg-white px-4 py-2.5 text-sm font-semibold text-blue-700 hover:border-blue-500 disabled:cursor-not-allowed disabled:border-slate-200 disabled:text-slate-400"
              >
                {startingChat === "SELECTION" ? "正在创建…" : `已选论文问答（${selectedReadyScope.length}）`}
              </button>
              <button
                type="button"
                disabled={loading || startingChat !== null}
                onClick={() => void startChat("LIBRARY", "全库论文问答", [])}
                className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-slate-300"
              >
                {startingChat === "LIBRARY" ? "正在创建…" : "开始全库问答"}
              </button>
            </div>
          </div>
          {actionError && <p className="mt-3 rounded-lg bg-red-50 p-3 text-sm text-red-700" role="alert">{actionError}</p>}
        </section>

        <div className="mt-5 grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px] xl:items-start">
          <section aria-labelledby="paper-list-title" className="min-w-0 rounded-2xl border border-slate-200 bg-white shadow-sm">
            <div className="border-b border-slate-200 p-4 sm:p-5">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h2 id="paper-list-title" className="text-lg font-semibold text-slate-950">论文库</h2>
                  <p className="mt-1 text-xs text-slate-500">当前显示 {visiblePapers.length} 篇</p>
                </div>
                <label className="relative block min-w-[220px] flex-1 sm:max-w-sm">
                  <span className="sr-only">搜索论文</span>
                  <span aria-hidden="true" className="pointer-events-none absolute left-3 top-2.5 text-slate-400">⌕</span>
                  <input
                    type="search"
                    aria-label="搜索论文"
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="搜索标题或文档 ID"
                    className="w-full rounded-xl border border-slate-200 bg-slate-50 py-2.5 pl-9 pr-3 text-sm outline-none transition focus:border-blue-400 focus:bg-white focus:ring-4 focus:ring-blue-100"
                  />
                </label>
              </div>
              <div className="mt-4 flex flex-wrap gap-2" aria-label="论文状态筛选">
                {FILTERS.map((item) => (
                  <button
                    key={item.value}
                    type="button"
                    aria-pressed={filter === item.value}
                    onClick={() => setFilter(item.value)}
                    className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
                      filter === item.value
                        ? "bg-slate-900 text-white"
                        : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                    }`}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
            </div>

            {loading && <p className="p-8 text-center text-sm text-slate-500" role="status">正在加载论文库…</p>}
            {error && <p className="m-5 rounded-xl bg-red-50 p-4 text-sm text-red-800" role="alert">{error}</p>}
            {!loading && !error && visiblePapers.length === 0 && (
              <div className="p-10 text-center">
                <p className="font-medium text-slate-700">没有匹配的论文</p>
                <p className="mt-1 text-sm text-slate-500">调整搜索或筛选条件，或者导入一篇新论文。</p>
              </div>
            )}
            <div className="divide-y divide-slate-100">
              {visiblePapers.map((paper) => {
                const task = taskStates.get(paper.id);
                const selectedPaper = paper.id === selectedId;
                return (
                  <article
                    key={paper.id}
                    aria-label={`${paper.title} 论文`}
                    className={`relative p-4 transition sm:p-5 ${selectedPaper ? "bg-blue-50/70" : "hover:bg-slate-50"}`}
                  >
                    <button
                      type="button"
                      aria-label={`选择 ${paper.title}`}
                      aria-pressed={selectedPaper}
                      onClick={() => {
                        setSelectedId(paper.id);
                        setActionError(null);
                      }}
                      className="w-full text-left focus-visible:rounded-lg focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-blue-600"
                    >
                      <div className="flex items-start justify-between gap-4">
                        <div className="min-w-0">
                          <p className="truncate font-semibold text-slate-950">{paper.title}</p>
                          <p className="mt-1 font-mono text-[11px] text-slate-400">{paper.id.slice(0, 12)}</p>
                        </div>
                        <StatusBadge status={effectiveStatus(paper, task)} />
                      </div>
                    </button>
                    <TaskProgress
                      taskId={paper.latestTaskId}
                      onStateChange={(state) => recordTaskState(paper.id, state)}
                    />
                    <label className={`mt-3 flex items-center gap-2 text-xs font-medium ${isReady(paper, task) ? "text-slate-600" : "text-slate-400"}`}>
                      <input
                        type="checkbox"
                        aria-label={`将 ${paper.title} 加入问答范围`}
                        checked={chatScopeIds.includes(paper.id)}
                        disabled={!isReady(paper, task)}
                        onChange={() => toggleChatScope(paper.id)}
                        className="size-4 rounded border-slate-300 text-blue-600 accent-blue-600"
                      />
                      {isReady(paper, task) ? "加入跨论文问答范围" : "达到 READY 后可加入问答范围"}
                    </label>
                  </article>
                );
              })}
            </div>
            {nextCursor && (
              <div className="border-t border-slate-200 p-4 text-center">
                <button
                  type="button"
                  disabled={loadingMore}
                  onClick={() => void loadMore()}
                  className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700 hover:border-blue-500 hover:text-blue-700 disabled:opacity-50"
                >
                  {loadingMore ? "加载中…" : "加载更多论文"}
                </button>
              </div>
            )}
          </section>

          <PaperInspector
            paper={selected}
            task={selected ? taskStates.get(selected.id) : undefined}
            startingChat={startingChat !== null}
            startingThisPaper={startingChat === "PAPER"}
            actionError={actionError}
            onStartChat={() => {
              if (!selected || !isReady(selected, taskStates.get(selected.id))) return;
              void startChat("PAPER", selected.title, [selected.id]);
            }}
          />
        </div>
      </div>

      {uploadOpen && (
        <div className="fixed inset-0 z-50 flex justify-end bg-slate-950/35 backdrop-blur-[2px]">
          <button
            type="button"
            aria-label="关闭导入面板"
            className="absolute inset-0 cursor-default"
            onClick={() => setUploadOpen(false)}
          />
          <section
            role="dialog"
            aria-modal="true"
            aria-label="导入论文"
            className="relative h-full w-full max-w-md overflow-y-auto bg-[#f7f9fc] p-5 shadow-2xl sm:p-6"
          >
            <div className="mb-5 flex items-center justify-between">
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.16em] text-blue-700">Import</p>
                <h2 className="mt-1 text-xl font-semibold text-slate-950">导入论文</h2>
              </div>
              <button
                type="button"
                onClick={() => setUploadOpen(false)}
                className="grid size-9 place-items-center rounded-full bg-white text-lg text-slate-500 shadow-sm hover:text-slate-950"
                aria-label="关闭"
              >
                ×
              </button>
            </div>
            <UploadPanel onReceipt={(receipt) => void receiveUpload(receipt)} />
          </section>
        </div>
      )}
    </main>
  );
}

function Metric({ label, value, tone }: { label: string; value: number; tone: "slate" | "emerald" | "blue" }) {
  const dot = tone === "emerald" ? "bg-emerald-500" : tone === "blue" ? "bg-blue-500" : "bg-slate-500";
  return (
    <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
      <div className="flex items-center gap-2 text-xs font-medium text-slate-500">
        <span className={`size-2 rounded-full ${dot}`} />
        {label}
      </div>
      <p className="mt-1 text-2xl font-semibold tracking-tight text-slate-950">{value}</p>
    </div>
  );
}

function PaperInspector({
  paper,
  task,
  startingChat,
  startingThisPaper,
  actionError,
  onStartChat,
}: {
  paper: PaperSummary | null;
  task?: TaskState;
  startingChat: boolean;
  startingThisPaper: boolean;
  actionError: string | null;
  onStartChat: () => void;
}) {
  if (!paper) {
    return (
      <aside aria-label="当前论文" className="rounded-2xl border border-dashed border-slate-300 bg-white p-7 text-center text-sm text-slate-500 xl:sticky xl:top-7">
        选择一篇论文后，可在这里进入问答、笔记和原始 PDF。
      </aside>
    );
  }
  const ready = isReady(paper, task);
  const status = effectiveStatus(paper, task);
  return (
    <aside aria-label="当前论文" className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm xl:sticky xl:top-7">
      <div className="border-b border-slate-200 bg-gradient-to-br from-slate-950 to-slate-800 p-5 text-white">
        <div className="flex items-center justify-between gap-3">
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-blue-300">Current paper</p>
          <StatusBadge status={status} dark />
        </div>
        <h2 className="mt-4 text-xl font-semibold leading-7">{paper.title}</h2>
        <p className="mt-2 font-mono text-[11px] text-slate-400">{paper.id}</p>
      </div>
      <div className="p-5">
        <p className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-400">研究操作</p>
        <div className="mt-3 space-y-2.5">
          <button
            type="button"
            disabled={!ready || startingChat}
            onClick={onStartChat}
            className="flex w-full items-center justify-between rounded-xl bg-blue-600 px-4 py-3 text-left text-sm font-semibold text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-500"
          >
            <span>{startingThisPaper ? "正在创建问答…" : "开始论文问答"}</span>
            <span aria-hidden="true">→</span>
          </button>
          {ready ? (
            <>
              <Link
                href={`/papers/${paper.id}/notes`}
                className="flex w-full items-center justify-between rounded-xl border border-slate-200 px-4 py-3 text-sm font-semibold text-slate-700 transition hover:border-blue-400 hover:text-blue-700"
              >
                打开论文笔记 <span aria-hidden="true">↗</span>
              </Link>
              <a
                href={documentPdfUrl(paper.id)}
                target="_blank"
                rel="noreferrer"
                className="flex w-full items-center justify-between rounded-xl border border-slate-200 px-4 py-3 text-sm font-semibold text-slate-700 transition hover:border-blue-400 hover:text-blue-700"
              >
                查看原始 PDF <span aria-hidden="true">↗</span>
              </a>
            </>
          ) : (
            <p className="rounded-xl bg-amber-50 p-3 text-xs leading-5 text-amber-800">
              论文达到 READY 后即可使用证据问答、结构化笔记和原文入口。
            </p>
          )}
        </div>
        {actionError && <p className="mt-3 rounded-lg bg-red-50 p-3 text-sm text-red-700" role="alert">{actionError}</p>}

        <dl className="mt-6 grid grid-cols-2 gap-3 border-t border-slate-100 pt-5 text-sm">
          <div className="rounded-xl bg-slate-50 p-3">
            <dt className="text-xs text-slate-500">当前阶段</dt>
            <dd className="mt-1 font-semibold text-slate-900">{task?.stage ?? paper.status}</dd>
          </div>
          <div className="rounded-xl bg-slate-50 p-3">
            <dt className="text-xs text-slate-500">最近更新</dt>
            <dd className="mt-1 font-semibold text-slate-900">{formatCompactDate(paper.updatedAt)}</dd>
          </div>
        </dl>
      </div>
    </aside>
  );
}

function effectiveStatus(paper: PaperSummary, task?: TaskState): string {
  if (task) return task.status;
  if (paper.status === "READY") return "READY";
  if (["FAILED", "CANCELLED", "NEEDS_REVIEW"].includes(paper.status)) return paper.status;
  return "RUNNING";
}

function isReady(paper: PaperSummary, task?: TaskState): boolean {
  return effectiveStatus(paper, task) === "READY";
}

function StatusBadge({ status, dark = false }: { status: string; dark?: boolean }) {
  const label = status === "READY"
    ? "已就绪"
    : status === "FAILED"
      ? "失败"
      : status === "CANCELLED"
        ? "已取消"
        : status === "NEEDS_REVIEW"
          ? "需复核"
          : status === "QUEUED"
            ? "排队中"
            : "处理中";
  const tone = status === "READY"
    ? dark ? "bg-emerald-400/15 text-emerald-200" : "bg-emerald-50 text-emerald-700"
    : ["FAILED", "CANCELLED", "NEEDS_REVIEW"].includes(status)
      ? dark ? "bg-red-400/15 text-red-200" : "bg-red-50 text-red-700"
      : dark ? "bg-blue-400/15 text-blue-200" : "bg-blue-50 text-blue-700";
  return <span className={`shrink-0 rounded-full px-2.5 py-1 text-[11px] font-semibold ${tone}`}>{label}</span>;
}

function formatCompactDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric" }).format(new Date(value));
}

function deduplicatePapers(items: PaperSummary[]): PaperSummary[] {
  return [...new Map(items.map((paper) => [paper.id, paper])).values()];
}
