"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { listSpaces } from "./api";
import type { ResearchSpace } from "./api";
import { SpaceForm } from "./space-form";

export function SpaceList() {
  const router = useRouter();
  const [spaces, setSpaces] = useState<ResearchSpace[]>([]);
  const [creating, setCreating] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    void listSpaces()
      .then((items) => {
        if (!cancelled) setSpaces(items);
      })
      .catch((caught) => {
        if (!cancelled)
          setError(caught instanceof Error ? caught.message : "空间加载失败");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className="mx-auto max-w-5xl px-4 py-8 sm:px-8 lg:py-12">
      <header className="mb-7 flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-semibold">研究空间</h1>
          <p className="mt-3 text-sm text-slate-600">
            把研究目标、约束、论文和讨论放在一起，随时回来继续。
          </p>
        </div>
        {!creating && (
          <button
            onClick={() => setCreating(true)}
            className="rounded-xl bg-blue-600 px-5 py-3 text-sm font-medium text-white"
          >
            新建研究空间
          </button>
        )}
      </header>
      {error && (
        <p role="alert" className="mb-5 rounded-lg bg-red-50 p-3 text-red-800">
          {error}
        </p>
      )}
      {creating && (
        <div className="mb-7">
          <SpaceForm
            onCancel={() => setCreating(false)}
            onSaved={(space) => router.push(`/research/${space.id}`)}
          />
        </div>
      )}
      {loading ? (
        <p role="status">正在加载研究空间…</p>
      ) : spaces.length === 0 ? (
        <p className="rounded-2xl border border-dashed border-slate-300 p-8 text-slate-600">
          还没有研究空间。创建一个空间，可以先讨论，也可以关联已上传的论文。
        </p>
      ) : (
        <section
          aria-label="研究空间列表"
          className="grid gap-4 sm:grid-cols-2"
        >
          {spaces.map((space) => (
            <Link
              key={space.id}
              href={`/research/${space.id}`}
              className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm hover:border-blue-400"
            >
              <h2 className="text-lg font-semibold">{space.name}</h2>
              <p className="mt-3 line-clamp-3 whitespace-pre-wrap text-sm text-slate-600">
                {space.goal || "尚未设置研究目标"}
              </p>
              <p className="mt-5 text-xs text-slate-500">
                {space.materials.length} 篇论文 · 修订 {space.revision}
              </p>
            </Link>
          ))}
        </section>
      )}
    </main>
  );
}
