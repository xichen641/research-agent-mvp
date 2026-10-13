"use client";

import { useState } from "react";

import type { NoteField, NoteRevision, PaperNoteContent } from "./types";

type NoteDiffProps = {
  current: NoteRevision;
  draft: NoteRevision;
  applying?: boolean;
  onAccept: (content: PaperNoteContent) => Promise<void> | void;
  onDiscard: () => void;
};

export function findPendingDraft(
  revisions: NoteRevision[],
  current: NoteRevision,
  discardedDraftId: string | null,
) {
  return revisions.find(
    (revision) =>
      revision.source === "AI_DRAFT" &&
      revision.revision_number > current.revision_number &&
      revision.id !== discardedDraftId,
  );
}

export function NoteDiff({
  current,
  draft,
  applying = false,
  onAccept,
  onDiscard,
}: NoteDiffProps) {
  const [rejected, setRejected] = useState<Set<string>>(new Set());

  function acceptField(key: string) {
    const selected = draft.content.fields.find((field) => field.key === key);
    if (!selected) return;
    void onAccept(mergeFields(current.content, [selected]));
  }

  function rejectField(key: string) {
    setRejected((value) => new Set(value).add(key));
  }

  const changed = draft.content.fields.filter((field) => {
    const previous = current.content.fields.find((item) => item.key === field.key);
    return previous?.markdown !== field.markdown && !rejected.has(field.key);
  });

  return (
    <section className="rounded-3xl border border-amber-200 bg-amber-50 p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold">AI 重新生成草稿</h2>
          <p className="mt-1 text-sm text-slate-600">
            当前版本保持不变，只有你接受的内容才会保存。
          </p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            disabled={applying}
            onClick={() => void onAccept(confirmAll(draft.content, current.content))}
            className="rounded-lg bg-amber-700 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
          >
            全部接受
          </button>
          <button
            type="button"
            onClick={onDiscard}
            className="rounded-lg border border-amber-400 px-4 py-2 text-sm font-semibold"
          >
            丢弃草稿
          </button>
        </div>
      </div>

      <div className="mt-5 space-y-4">
        {changed.map((field) => {
          const previous = current.content.fields.find(
            (item) => item.key === field.key,
          );
          return (
            <article key={field.key} className="rounded-2xl bg-white p-4">
              <h3 className="font-semibold">{field.title}</h3>
              <div className="mt-3 grid gap-3 md:grid-cols-2">
                <div className="rounded-xl bg-red-50 p-3">
                  <p className="text-xs font-semibold text-red-700">当前内容</p>
                  <p className="mt-2 whitespace-pre-wrap text-sm">{previous?.markdown}</p>
                </div>
                <div className="rounded-xl bg-emerald-50 p-3">
                  <p className="text-xs font-semibold text-emerald-700">新草稿</p>
                  <p className="mt-2 whitespace-pre-wrap text-sm">{field.markdown}</p>
                </div>
              </div>
              <div className="mt-3 flex gap-2">
                <button
                  type="button"
                  disabled={applying}
                  onClick={() => acceptField(field.key)}
                  className="rounded-lg bg-emerald-700 px-3 py-1.5 text-sm text-white disabled:opacity-50"
                >
                  接受此字段
                </button>
                <button
                  type="button"
                  onClick={() => rejectField(field.key)}
                  className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
                >
                  拒绝此字段
                </button>
              </div>
            </article>
          );
        })}
        {changed.length === 0 && (
          <p className="rounded-xl bg-white p-4 text-sm text-slate-600">
            没有待处理的字段差异。
          </p>
        )}
      </div>
    </section>
  );
}

function mergeFields(
  current: PaperNoteContent,
  accepted: NoteField[],
): PaperNoteContent {
  const byKey = new Map(accepted.map((field) => [field.key, field]));
  return {
    fields: current.fields.map((field) => {
      const replacement = byKey.get(field.key);
      return replacement
        ? { ...replacement, status: "USER_CONFIRMED" }
        : { ...field, status: "USER_CONFIRMED" };
    }),
  };
}

function confirmAll(
  content: PaperNoteContent,
  current: PaperNoteContent,
): PaperNoteContent {
  const userComments = current.fields.find(
    (field) => field.key === "user_comments",
  );
  return {
    fields: content.fields.map((field) => ({
      ...(field.key === "user_comments" && userComments ? userComments : field),
      status: "USER_CONFIRMED",
    })),
  };
}
