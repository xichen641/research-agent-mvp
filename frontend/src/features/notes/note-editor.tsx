"use client";

import { useState } from "react";

import type { NoteRevision, PaperNoteContent } from "./types";

type NoteEditorProps = {
  revision: NoteRevision;
  saving?: boolean;
  onSave: (content: PaperNoteContent) => Promise<void> | void;
};

export function NoteEditor({ revision, saving = false, onSave }: NoteEditorProps) {
  const [content, setContent] = useState(revision.content);

  function updateField(key: string, markdown: string) {
    setContent((current) => ({
      fields: current.fields.map((field) =>
        field.key === key
          ? { ...field, markdown, status: "USER_CONFIRMED" }
          : field,
      ),
    }));
  }

  return (
    <section aria-labelledby="note-editor-title" className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 id="note-editor-title" className="text-2xl font-semibold">
            当前论文笔记
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            第 {revision.revision_number} 版 · 修改后会创建新的用户版本
          </p>
        </div>
        <button
          type="button"
          disabled={saving}
          onClick={() => void onSave(content)}
          className="rounded-xl bg-slate-950 px-5 py-2.5 text-sm font-semibold text-white disabled:opacity-50"
        >
          {saving ? "保存中…" : "保存新版本"}
        </button>
      </div>

      {content.fields.map((field) => {
        const evidence = revision.evidence.filter(
          (item) =>
            item.field_key === field.key &&
            field.evidence_chunk_ids.includes(item.chunk_id),
        );
        return (
          <article
            key={field.key}
            className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"
          >
            <div className="flex items-center justify-between gap-3">
              <label htmlFor={`note-${field.key}`} className="font-semibold">
                {field.title}
              </label>
              <span
                className={`rounded-full px-3 py-1 text-xs font-medium ${
                  field.status === "AI_GENERATED"
                    ? "bg-blue-100 text-blue-800"
                    : "bg-emerald-100 text-emerald-800"
                }`}
              >
                {field.status === "AI_GENERATED" ? "AI 生成" : "用户确认"}
              </span>
            </div>
            <textarea
              id={`note-${field.key}`}
              aria-label={field.title}
              value={field.markdown}
              onChange={(event) => updateField(field.key, event.target.value)}
              rows={Math.max(3, Math.min(10, field.markdown.split("\n").length + 2))}
              className="mt-3 w-full rounded-xl border border-slate-300 px-4 py-3 leading-7 outline-none focus:border-blue-500"
            />
            {evidence.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-2" aria-label={`${field.title}证据`}>
                {evidence.map((item, index) => (
                  <a
                    key={`${item.field_key}-${item.chunk_id}`}
                    href={item.pdf_url}
                    target="_blank"
                    rel="noreferrer"
                    title={item.quote}
                    className="rounded-full bg-blue-50 px-3 py-1 text-xs text-blue-800 hover:bg-blue-100"
                  >
                    证据 {index + 1} · 第 {item.page_start} 页
                  </a>
                ))}
              </div>
            )}
          </article>
        );
      })}
    </section>
  );
}
