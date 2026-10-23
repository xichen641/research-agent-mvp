"use client";

import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { listDocuments } from "@/features/papers/api";
import type { PaperSummary } from "@/features/papers/types";
import { materialStatus, saveSpace } from "./api";
import type { ResearchSpace, SpaceInput } from "./api";

const fields = [
  ["goal", "研究目标", "例如：理解文本语义如何改善行人重识别"],
  ["task_settings", "任务设定", "例如：Market-1501，单图像 ReID，ViT-B/16"],
  ["resource_constraints", "资源约束", "例如：12GB 显存，一天内完成最小对照"],
  ["current_question", "当前问题", "例如：如何区分语义收益与额外参数收益？"],
] as const;

export function SpaceForm({
  space,
  onSaved,
  onCancel,
}: {
  space?: ResearchSpace;
  onSaved: (space: ResearchSpace) => void;
  onCancel?: () => void;
}) {
  const [baseSpace] = useState(space);
  const [form, setForm] = useState<SpaceInput>(() => ({
    name: space?.name ?? "",
    goal: space?.goal ?? "",
    task_settings: space?.task_settings ?? "",
    resource_constraints: space?.resource_constraints ?? "",
    current_question: space?.current_question ?? "",
    material_version_ids:
      space?.materials.map((item) => item.document_version_id) ?? [],
  }));
  const [papers, setPapers] = useState<PaperSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      const all: PaperSummary[] = [];
      let cursor: string | undefined;
      do {
        const page = await listDocuments(cursor, controller.signal);
        all.push(...page.items);
        cursor = page.nextCursor ?? undefined;
      } while (cursor);
      if (!controller.signal.aborted) setPapers(all);
    })()
      .catch((caught) => {
        if (!controller.signal.aborted)
          setError(
            caught instanceof Error ? caught.message : "论文列表加载失败",
          );
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      onSaved(await saveSpace(form, baseSpace));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "空间保存失败");
    } finally {
      setBusy(false);
    }
  }

  const choices = new Map(
    papers.map((paper) => [
      paper.currentVersionId,
      {
        id: paper.currentVersionId,
        documentId: paper.id,
        title: paper.title,
        status: paper.status,
      },
    ]),
  );
  for (const material of baseSpace?.materials ?? [])
    choices.set(material.document_version_id, {
      id: material.document_version_id,
      documentId: material.document_id,
      title: material.title,
      status: material.status,
    });

  return (
    <form
      onSubmit={submit}
      className="space-y-5 rounded-2xl border border-slate-200 bg-white p-5 sm:p-7"
    >
      <label className="block text-sm font-medium">
        空间名称
        <input
          required
          maxLength={200}
          value={form.name}
          onChange={(event) => setForm({ ...form, name: event.target.value })}
          className="mt-2 w-full rounded-lg border border-slate-300 p-3"
        />
      </label>
      {fields.map(([key, label, placeholder]) => (
        <label key={key} className="block text-sm font-medium">
          {label}
          <textarea
            maxLength={4000}
            value={form[key]}
            placeholder={placeholder}
            onChange={(event) =>
              setForm({ ...form, [key]: event.target.value })
            }
            className="mt-2 min-h-20 w-full rounded-lg border border-slate-300 p-3 font-normal"
          />
        </label>
      ))}
      <fieldset
        disabled={loading || busy}
        className="rounded-xl border border-slate-200 p-4"
      >
        <legend className="px-2 text-sm font-medium">关联论文版本</legend>
        <p className="mb-3 text-xs text-slate-500">
          可以暂不关联论文，先讨论目标。处理中资料可以加入，完成后即可取证。每篇论文选择一个版本。
        </p>
        {loading ? (
          <p role="status">正在加载论文…</p>
        ) : choices.size === 0 ? (
          <p className="text-sm text-slate-500">
            尚无论文，可先到论文工作台上传。
          </p>
        ) : (
          <div className="max-h-72 space-y-2 overflow-y-auto">
            {[...choices.values()].map((item) => {
              const otherSelected = [...choices.values()].some(
                (other) =>
                  other.documentId === item.documentId &&
                  other.id !== item.id &&
                  form.material_version_ids.includes(other.id),
              );
              return (
                <label
                  key={item.id}
                  className="flex items-start gap-3 rounded-lg bg-slate-50 p-3 text-sm"
                >
                  <input
                    type="checkbox"
                    checked={form.material_version_ids.includes(item.id)}
                    disabled={otherSelected}
                    onChange={(event) =>
                      setForm({
                        ...form,
                        material_version_ids: event.target.checked
                          ? [...form.material_version_ids, item.id]
                          : form.material_version_ids.filter(
                              (id) => id !== item.id,
                            ),
                      })
                    }
                  />
                  <span className="min-w-0">
                    <span className="block break-words">{item.title}</span>
                    <span className="mt-1 block text-xs text-slate-500">
                      {materialStatus(item.status)} · 版本 {item.id.slice(0, 8)}
                    </span>
                  </span>
                </label>
              );
            })}
          </div>
        )}
      </fieldset>
      {error && (
        <p
          role="alert"
          className="rounded-lg bg-red-50 p-3 text-sm text-red-800"
        >
          {error}
        </p>
      )}
      <div className="flex gap-3">
        <button
          disabled={busy}
          className="rounded-lg bg-blue-600 px-5 py-3 text-sm font-medium text-white disabled:opacity-50"
        >
          {busy ? "正在保存…" : space ? "保存修订" : "创建空间"}
        </button>
        {onCancel && (
          <button
            type="button"
            disabled={busy}
            onClick={onCancel}
            className="rounded-lg border border-slate-300 px-5 py-3 text-sm"
          >
            取消
          </button>
        )}
      </div>
    </form>
  );
}
