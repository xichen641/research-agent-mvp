"use client";

import { FormEvent, useState } from "react";

import { ApiProblem } from "@/lib/api";

import { importArxivPaper, searchArxiv } from "./api";
import type { ArxivImportReceipt, ArxivSearchPage } from "./types";


type ArxivSearchProps = {
  onImported: (receipt: ArxivImportReceipt) => Promise<void> | void;
};

const PAGE_SIZE = 10;

export function ArxivSearch({ onImported }: ArxivSearchProps) {
  const [topic, setTopic] = useState("");
  const [result, setResult] = useState<ArxivSearchPage | null>(null);
  const [searching, setSearching] = useState(false);
  const [importingId, setImportingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = topic.trim();
    if (!value || searching) return;
    await runSearch(value, 0);
  }

  async function runSearch(query: string, start: number) {
    setSearching(true);
    setError(null);
    setNotice(null);
    try {
      setResult(await searchArxiv(query, start));
    } catch (caught) {
      setError(caught instanceof ApiProblem ? caught.message : "arXiv 搜索暂时不可用");
    } finally {
      setSearching(false);
    }
  }

  function changePage(start: number) {
    if (!result || searching || importingId || start < 0 || start > 10_000) return;
    void runSearch(result.topic, start);
  }

  async function importPaper(arxivId: string, title: string) {
    if (importingId) return;
    setImportingId(arxivId);
    setError(null);
    setNotice(null);
    try {
      const receipt = await importArxivPaper(arxivId);
      await onImported(receipt);
      setNotice(
        receipt.duplicate
          ? `《${title}》已在论文库中。`
          : `《${title}》已进入下载与解析流程。`,
      );
    } catch (caught) {
      setError(caught instanceof ApiProblem ? caught.message : "论文导入失败");
    } finally {
      setImportingId(null);
    }
  }

  return (
    <section className="mt-5 rounded-2xl border border-violet-200 bg-gradient-to-r from-violet-50 to-white p-4 shadow-sm sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-violet-700">
            Paper discovery
          </p>
          <h2 className="mt-1 text-lg font-semibold text-slate-950">按主题发现论文</h2>
          <p className="mt-1 text-sm text-slate-600">
            搜索只加载元数据；你明确选择后，PDF 才会私有下载并进入解析管线。
          </p>
        </div>
        <form onSubmit={submit} className="flex min-w-72 flex-1 gap-2 sm:max-w-2xl">
          <label htmlFor="arxiv-topic" className="sr-only">论文主题</label>
          <input
            id="arxiv-topic"
            value={topic}
            onChange={(event) => setTopic(event.target.value)}
            maxLength={500}
            placeholder="例如：agent memory context compression"
            className="min-w-0 flex-1 rounded-xl border border-violet-200 bg-white px-4 py-2.5 text-sm outline-none focus:border-violet-500"
          />
          <button
            type="submit"
            disabled={searching || !topic.trim()}
            className="rounded-xl bg-violet-700 px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-40"
          >
            {searching ? "搜索中" : "搜索"}
          </button>
        </form>
      </div>

      {error && <p role="alert" className="mt-4 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-800">{error}</p>}
      {notice && <p role="status" className="mt-4 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-800">{notice}</p>}

      {result && (
        <div className="mt-5 space-y-3">
          <p className="text-xs text-slate-500">
            按首次发布日期从新到旧 · 找到 {result.totalResults} 条结果，本页 {result.items.length} 条
            {result.cached ? "（来自今日缓存）" : ""}
          </p>
          {result.items.map((paper) => (
            <article key={paper.arxivId} className="rounded-2xl border border-slate-200 bg-white p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <h3 className="font-semibold leading-6 text-slate-950">{paper.title}</h3>
                  <p className="mt-1 text-xs text-slate-500">
                    {paper.authors.slice(0, 5).join("、")}
                    {paper.authors.length > 5 ? " 等" : ""}
                    {paper.primaryCategory ? ` · ${paper.primaryCategory}` : ""}
                    {` · ${new Date(paper.publishedAt).toLocaleDateString("zh-CN")}`}
                  </p>
                </div>
                <div className="flex gap-2">
                  <a
                    href={paper.abstractUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="rounded-xl border border-slate-300 px-3 py-2 text-xs font-semibold text-slate-700"
                  >
                    查看摘要
                  </a>
                  <button
                    type="button"
                    disabled={importingId !== null}
                    onClick={() => void importPaper(paper.arxivId, paper.title)}
                    className="rounded-xl bg-slate-950 px-3 py-2 text-xs font-semibold text-white disabled:opacity-40"
                  >
                    {importingId === paper.arxivId ? "导入中" : "导入论文库"}
                  </button>
                </div>
              </div>
              <p className="mt-3 line-clamp-3 text-sm leading-6 text-slate-600">{paper.abstract}</p>
            </article>
          ))}
          <PageControls
            result={result}
            disabled={searching || importingId !== null}
            onChange={changePage}
          />
          <p className="text-xs leading-5 text-slate-500">
            Thank you to arXiv for use of its open access interoperability. PDF 仅用于你的科研工作，不由本平台公开再分发。
          </p>
        </div>
      )}
    </section>
  );
}

type PageControlsProps = {
  result: ArxivSearchPage;
  disabled: boolean;
  onChange: (start: number) => void;
};

function PageControls({ result, disabled, onChange }: PageControlsProps) {
  const currentPage = Math.floor(result.start / PAGE_SIZE) + 1;
  const totalPages = Math.max(1, Math.ceil(result.totalResults / PAGE_SIZE));
  const previousStart = Math.max(0, result.start - PAGE_SIZE);
  const nextStart = result.start + PAGE_SIZE;
  const hasPrevious = result.start > 0;
  const hasNext = nextStart < result.totalResults && nextStart <= 10_000;

  return (
    <nav aria-label="arXiv 搜索结果分页" className="flex items-center justify-end gap-2">
      <button
        type="button"
        disabled={disabled || !hasPrevious}
        onClick={() => onChange(previousStart)}
        className="rounded-xl border border-slate-300 bg-white px-3 py-2 text-xs font-semibold text-slate-700 disabled:opacity-40"
      >
        上一页
      </button>
      <span className="min-w-20 text-center text-xs text-slate-500">
        第 {currentPage} / {totalPages} 页
      </span>
      <button
        type="button"
        disabled={disabled || !hasNext}
        onClick={() => onChange(nextStart)}
        className="rounded-xl border border-slate-300 bg-white px-3 py-2 text-xs font-semibold text-slate-700 disabled:opacity-40"
      >
        下一页
      </button>
    </nav>
  );
}
