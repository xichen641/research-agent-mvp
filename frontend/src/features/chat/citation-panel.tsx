import type { Citation } from "./types";

type CitationPanelProps = {
  citation: Citation;
  onClose: () => void;
};

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export function CitationPanel({ citation, onClose }: CitationPanelProps) {
  const pageLabel =
    citation.pageStart === citation.pageEnd
      ? `第 ${citation.pageStart} 页`
      : `第 ${citation.pageStart}–${citation.pageEnd} 页`;
  const href = `${API_URL}/api/v1/documents/${citation.documentId}/pdf?page=${citation.pageStart}`;

  return (
    <aside
      aria-label="引用原文"
      className="rounded-2xl border border-blue-200 bg-blue-50 p-5 shadow-sm"
    >
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-blue-700">
            引用原文
          </p>
          <h3 className="mt-2 font-semibold text-slate-950">{citation.title}</h3>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg px-2 py-1 text-sm text-slate-600 hover:bg-white"
        >
          关闭
        </button>
      </div>
      <p className="mt-3 text-sm text-slate-600">
        {citation.sectionPath.join(" / ") || "未标注章节"} · {pageLabel}
      </p>
      <blockquote className="mt-4 border-l-4 border-blue-300 pl-4 text-sm leading-7 text-slate-800">
        {citation.quote}
      </blockquote>
      <a
        href={href}
        target="_blank"
        rel="noreferrer"
        className="mt-4 inline-flex rounded-lg bg-blue-700 px-3 py-2 text-sm font-medium text-white hover:bg-blue-800"
      >
        打开论文原页
      </a>
    </aside>
  );
}
