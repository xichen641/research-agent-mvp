"use client";

import { useState } from "react";

import { ApiProblem } from "@/lib/api";
import { uploadDocument } from "./api";
import type { UploadReceipt } from "./types";

type UploadPanelProps = {
  upload?: (file: File) => Promise<UploadReceipt>;
  onReceipt?: (receipt: UploadReceipt) => void;
};

export function UploadPanel({
  upload = uploadDocument,
  onReceipt,
}: UploadPanelProps) {
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  function selectFile(selected: File | undefined) {
    setMessage(null);
    if (!selected) {
      setFile(null);
      setError(null);
      return;
    }
    if (selected.type !== "application/pdf") {
      setFile(null);
      setError("仅支持 PDF 文件");
      return;
    }
    setFile(selected);
    setError(null);
  }

  async function submit() {
    if (!file || uploading) return;
    setUploading(true);
    setError(null);
    setMessage(null);
    try {
      const receipt = await upload(file);
      setMessage(receipt.duplicate ? "论文已存在，已显示原任务" : "上传已受理");
      onReceipt?.(receipt);
    } catch (caught) {
      setError(caught instanceof ApiProblem ? caught.message : "上传失败，请稍后重试");
    } finally {
      setUploading(false);
    }
  }

  return (
    <section className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="mb-5">
        <p className="text-sm font-semibold tracking-wide text-blue-700">论文摄取</p>
        <h2 className="mt-1 text-2xl font-semibold text-slate-950">上传 PDF</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">
          文件状态会持久保存，刷新页面后仍可继续查看处理进度。
        </p>
      </div>
      <label className="block text-sm font-medium text-slate-800" htmlFor="paper-file">
        选择 PDF
      </label>
      <input
        id="paper-file"
        className="mt-2 block w-full cursor-pointer rounded-xl border border-slate-300 bg-slate-50 p-3 text-sm file:mr-3 file:rounded-lg file:border-0 file:bg-blue-700 file:px-4 file:py-2 file:text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 disabled:cursor-not-allowed disabled:opacity-60"
        type="file"
        accept="application/pdf"
        disabled={uploading}
        onChange={(event) => selectFile(event.target.files?.[0])}
      />
      {file && (
        <div className="mt-4 rounded-xl bg-slate-100 p-3 text-sm text-slate-700">
          <span className="font-medium text-slate-950">{file.name}</span>
          <span className="ml-2">{formatSize(file.size)}</span>
        </div>
      )}
      {error && <p className="mt-3 text-sm font-medium text-red-700" role="alert">{error}</p>}
      {message && <p className="mt-3 text-sm font-medium text-emerald-700" role="status">{message}</p>}
      <button
        className="mt-5 w-full rounded-xl bg-slate-950 px-5 py-3 text-sm font-semibold text-white hover:bg-blue-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 disabled:cursor-not-allowed disabled:bg-slate-400"
        type="button"
        disabled={!file || uploading}
        onClick={submit}
      >
        {uploading ? "上传中…" : "上传论文"}
      </button>
    </section>
  );
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
