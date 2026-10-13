"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";

import { ApiProblem } from "@/lib/api";
import {
  getNoteRevisions,
  getPaperNote,
  regenerateNote,
  requestNoteGeneration,
  savePaperNote,
} from "@/features/notes/api";
import { findPendingDraft, NoteDiff } from "@/features/notes/note-diff";
import { NoteEditor } from "@/features/notes/note-editor";
import type {
  NoteRevision,
  PaperNote,
  PaperNoteContent,
} from "@/features/notes/types";


export default function PaperNotesPage() {
  const params = useParams<{ documentId: string }>();
  const documentId = params.documentId;
  const [note, setNote] = useState<PaperNote | null>(null);
  const [revisions, setRevisions] = useState<NoteRevision[]>([]);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [queuedBaseline, setQueuedBaseline] = useState<number | null>(null);
  const [discardedDraftId, setDiscardedDraftId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const next = await getPaperNote(documentId, signal);
      const history = await getNoteRevisions(next.id, signal);
      setNote(next);
      setRevisions(history.items);
      setError(null);
      return history.items.length;
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") return null;
      if (caught instanceof ApiProblem && caught.code === "NOTE_NOT_FOUND") {
        setNote(null);
        setRevisions([]);
        setError(null);
        return 0;
      }
      setError(caught instanceof ApiProblem ? caught.message : "无法加载论文笔记");
      return null;
    } finally {
      setLoading(false);
    }
  }, [documentId]);

  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => void load(controller.signal), 0);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [load]);

  useEffect(() => {
    if (queuedBaseline === null) return;
    const timer = setInterval(() => {
      void load().then((revisionCount) => {
        if (revisionCount !== null && revisionCount > queuedBaseline) {
          setQueuedBaseline(null);
        }
      });
    }, 2000);
    return () => clearInterval(timer);
  }, [load, queuedBaseline]);

  async function generate() {
    setWorking(true);
    setError(null);
    try {
      await requestNoteGeneration(documentId);
      setQueuedBaseline(0);
    } catch (caught) {
      setError(caught instanceof ApiProblem ? caught.message : "生成请求未能受理");
    } finally {
      setWorking(false);
    }
  }

  async function save(content: PaperNoteContent) {
    if (!note) return;
    setWorking(true);
    setError(null);
    try {
      await savePaperNote(note.id, content);
      await load();
    } catch (caught) {
      setError(caught instanceof ApiProblem ? caught.message : "笔记保存失败");
    } finally {
      setWorking(false);
    }
  }

  async function regenerate() {
    if (!note) return;
    setWorking(true);
    setError(null);
    try {
      await regenerateNote(note.id);
      setDiscardedDraftId(null);
      setQueuedBaseline(revisions.length);
    } catch (caught) {
      setError(caught instanceof ApiProblem ? caught.message : "重新生成请求未能受理");
    } finally {
      setWorking(false);
    }
  }

  const draft = note?.current_revision
    ? findPendingDraft(revisions, note.current_revision, discardedDraftId)
    : undefined;
  const queued = queuedBaseline !== null;

  return (
    <main className="min-h-screen bg-slate-50 px-4 py-8 text-slate-950 sm:px-8">
      <div className="mx-auto max-w-5xl space-y-7">
        <header className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="text-sm font-semibold uppercase tracking-[0.2em] text-blue-700">
              ResearchAgent
            </p>
            <h1 className="mt-2 text-4xl font-semibold">论文笔记</h1>
            <p className="mt-2 text-slate-600">
              笔记仅在你请求时生成，每次编辑都会保留版本。
            </p>
          </div>
          {note && (
            <button
              type="button"
              disabled={working || queued}
              onClick={() => void regenerate()}
              className="rounded-xl border border-slate-300 bg-white px-5 py-2.5 text-sm font-semibold disabled:opacity-50"
            >
              重新生成草稿
            </button>
          )}
        </header>

        {error && <p role="alert" className="rounded-xl bg-red-50 p-4 text-red-800">{error}</p>}
        {queued && (
          <p role="status" className="rounded-xl bg-blue-50 p-4 text-blue-800">
            生成任务已排队，当前版本不会被覆盖。
          </p>
        )}
        {loading ? (
          <p role="status">正在加载笔记…</p>
        ) : !note?.current_revision ? (
          <section className="rounded-3xl border border-dashed border-slate-300 bg-white p-10 text-center">
            <h2 className="text-xl font-semibold">尚未生成论文笔记</h2>
            <p className="mt-2 text-slate-600">上传和解析不会自动调用模型。</p>
            <button
              type="button"
              disabled={working || queued}
              onClick={() => void generate()}
              className="mt-5 rounded-xl bg-blue-700 px-5 py-3 font-semibold text-white disabled:opacity-50"
            >
              显式生成笔记
            </button>
          </section>
        ) : (
          <>
            {draft && (
              <NoteDiff
                current={note.current_revision}
                draft={draft}
                applying={working}
                onAccept={save}
                onDiscard={() => setDiscardedDraftId(draft.id)}
              />
            )}
            <NoteEditor
              key={note.current_revision.id}
              revision={note.current_revision}
              saving={working}
              onSave={save}
            />
          </>
        )}
      </div>
    </main>
  );
}
