"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { getSessionSpace, getSpace, materialStatus } from "./api";
import type { ResearchSpace } from "./api";

export function SessionResearchPanel({ sessionId }: { sessionId: string }) {
  const [space, setSpace] = useState<ResearchSpace | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const session = await getSessionSpace(sessionId);
        const current = session.research_space_id
          ? await getSpace(session.research_space_id)
          : null;
        if (!cancelled) {
          setSpace(current);
          setError(null);
        }
      } catch (caught) {
        if (!cancelled)
          setError(
            caught instanceof Error ? caught.message : "空间资料加载失败",
          );
      }
    }
    void load();
    const timer = setInterval(() => void load(), 10000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [sessionId]);
  if (error)
    return (
      <aside
        role="alert"
        className="mt-6 rounded-xl bg-red-50 p-4 text-sm text-red-800"
      >
        {error}
      </aside>
    );
  if (!space) return null;
  return (
    <aside
      aria-label="当前研究空间"
      className="mb-6 rounded-2xl border border-blue-100 bg-white p-5"
    >
      <Link
        href={`/research/${space.id}`}
        className="font-semibold text-blue-800"
      >
        {space.name}
      </Link>
      <span className="ml-3 text-xs text-slate-500">修订 {space.revision}</span>
      {space.goal && (
        <p className="mt-3 whitespace-pre-wrap text-sm text-slate-700">
          {space.goal}
        </p>
      )}
      {space.resource_constraints && (
        <p className="mt-2 whitespace-pre-wrap text-xs text-slate-600">
          资源约束：{space.resource_constraints}
        </p>
      )}
      <details className="mt-3 text-sm">
        <summary className="cursor-pointer text-slate-600">
          关联资料（{space.materials.length}）
        </summary>
        {space.materials.length === 0 ? (
          <p className="mt-2 text-xs text-slate-500">
            尚未关联论文，可先讨论研究目标。
          </p>
        ) : (
          <ul className="mt-3 space-y-2">
            {space.materials.map((item) => (
              <li
                key={item.document_version_id}
                className="break-words text-xs text-slate-600"
              >
                {item.title} · {materialStatus(item.status)} · 版本{" "}
                {item.document_version_id.slice(0, 8)}
              </li>
            ))}
          </ul>
        )}
      </details>
    </aside>
  );
}
