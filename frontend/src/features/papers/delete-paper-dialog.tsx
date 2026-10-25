"use client";

import { useEffect, useId, useRef, useState } from "react";

import { ApiProblem } from "@/lib/api";
import { deleteDocument } from "./api";
import type { PaperSummary } from "./types";

type DeletePaperDialogProps = {
  paper: PaperSummary;
  onDeleted: (documentId: string) => void;
  onCancel: () => void;
};

export function DeletePaperDialog({ paper, onDeleted, onCancel }: DeletePaperDialogProps) {
  const dialog = useRef<HTMLDialogElement>(null);
  const cancelButton = useRef<HTMLButtonElement>(null);
  const submitting = useRef(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const titleId = useId();
  const descriptionId = useId();

  useEffect(() => {
    const element = dialog.current;
    const trigger = document.activeElement;
    element?.showModal();
    cancelButton.current?.focus();
    return () => {
      element?.close();
      if (trigger instanceof HTMLElement && trigger.isConnected) trigger.focus();
    };
  }, []);

  async function confirmDelete() {
    if (submitting.current) return;
    submitting.current = true;
    setDeleting(true);
    setError(null);
    try {
      await deleteDocument(paper.id);
      onDeleted(paper.id);
    } catch (caught) {
      setError(caught instanceof ApiProblem
        ? caught.code === "DOCUMENT_DELETE_CONFLICT"
          ? "论文仍在处理中，请等任务结束后再删除。"
          : caught.message
        : "删除失败，请稍后重试。");
    } finally {
      submitting.current = false;
      setDeleting(false);
    }
  }

  return (
    <dialog
      ref={dialog}
      aria-labelledby={titleId}
      aria-describedby={descriptionId}
      aria-modal="true"
      onCancel={(event) => {
        event.preventDefault();
        if (!submitting.current) onCancel();
      }}
      className="fixed inset-0 m-auto max-h-[90dvh] w-[calc(100%-2rem)] max-w-md overflow-y-auto rounded-2xl border border-slate-200 bg-white p-6 text-slate-900 shadow-2xl backdrop:bg-slate-950/40 backdrop:backdrop-blur-[2px]"
    >
      <h2 id={titleId} className="text-lg font-semibold">删除论文</h2>
      <p className="mt-3 break-words font-medium">{paper.title}</p>
      <p id={descriptionId} className="mt-2 text-sm leading-6 text-slate-600">
        删除后将从论文库和后续检索中移除，已有对话和笔记记录保留。
      </p>
      {error && <p role="alert" className="mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</p>}
      <div className="mt-6 flex justify-end gap-3">
        <button
          ref={cancelButton}
          type="button"
          disabled={deleting}
          onClick={onCancel}
          className="rounded-xl border border-slate-300 px-4 py-2.5 text-sm font-semibold text-slate-700 hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 disabled:opacity-50"
        >
          取消
        </button>
        <button
          type="button"
          disabled={deleting}
          onClick={() => void confirmDelete()}
          className="rounded-xl bg-red-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-red-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-600 disabled:opacity-50"
        >
          {deleting ? "正在删除…" : "确认删除"}
        </button>
      </div>
    </dialog>
  );
}
