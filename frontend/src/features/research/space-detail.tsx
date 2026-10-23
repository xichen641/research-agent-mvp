"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  assignSpaceSession,
  createSpaceSession,
  getSpace,
  listSpaceSessions,
  listUnassignedSessions,
  materialStatus,
} from "./api";
import type { ResearchSpace, SpaceSession } from "./api";
import { SpaceForm } from "./space-form";

export function SpaceDetail({ spaceId }: { spaceId: string }) {
  const router = useRouter();
  const [space, setSpace] = useState<ResearchSpace | null>(null);
  const [sessions, setSessions] = useState<SpaceSession[]>([]);
  const [unassigned, setUnassigned] = useState<SpaceSession[]>([]);
  const [selected, setSelected] = useState("");
  const [title, setTitle] = useState("新讨论");
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [current, chats, legacy] = await Promise.all([
          getSpace(spaceId),
          listSpaceSessions(spaceId),
          listUnassignedSessions(),
        ]);
        if (!cancelled) {
          setSpace(current);
          setSessions(chats);
          setUnassigned(legacy);
        }
      } catch (caught) {
        if (!cancelled)
          setError(caught instanceof Error ? caught.message : "空间加载失败");
      }
    }
    void load();
    const timer = setInterval(() => void load(), 10000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [spaceId]);

  async function startDiscussion() {
    if (busy || !title.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const session = await createSpaceSession(spaceId, title.trim());
      router.push(`/chat/${session.id}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "创建讨论失败");
      setBusy(false);
    }
  }
  async function assign() {
    if (busy || !selected) return;
    setBusy(true);
    setError(null);
    try {
      const assigned = await assignSpaceSession(spaceId, selected);
      setSessions((items) => [assigned, ...items]);
      setUnassigned((items) => items.filter((item) => item.id !== selected));
      setSelected("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "归入空间失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto max-w-7xl px-4 py-8 sm:px-8 lg:py-12">
      <Link href="/research" className="text-sm text-blue-700">
        ← 研究空间
      </Link>
      {error && (
        <p role="alert" className="mt-5 rounded-lg bg-red-50 p-3 text-red-800">
          {error}
        </p>
      )}
      {!space ? (
        !error && (
          <p role="status" className="mt-5">
            正在加载空间…
          </p>
        )
      ) : (
        <>
          <header className="mb-7 mt-5 flex flex-wrap items-center justify-between gap-4">
            <div>
              <h1 className="text-3xl font-semibold">{space.name}</h1>
              <p className="mt-2 text-xs text-slate-500">
                修订 {space.revision}
              </p>
            </div>
            <button
              onClick={() => setEditing(!editing)}
              className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm"
            >
              {editing ? "收起编辑" : "编辑空间"}
            </button>
          </header>
          {editing && (
            <div className="mb-7">
              <SpaceForm
                key={space.id}
                space={space}
                onCancel={() => setEditing(false)}
                onSaved={(saved) => {
                  setSpace(saved);
                  setEditing(false);
                }}
              />
            </div>
          )}
          <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
            <section className="space-y-6">
              <div className="rounded-2xl border border-slate-200 bg-white p-6">
                <h2 className="font-semibold">研究设置</h2>
                <dl className="mt-4 space-y-4">
                  {[
                    ["研究目标", space.goal],
                    ["任务设定", space.task_settings],
                    ["资源约束", space.resource_constraints],
                    ["当前问题", space.current_question],
                  ].map(([label, value]) => (
                    <div key={label}>
                      <dt className="text-xs font-medium text-slate-500">
                        {label}
                      </dt>
                      <dd className="mt-1 whitespace-pre-wrap break-words text-sm leading-6">
                        {value || "尚未设置"}
                      </dd>
                    </div>
                  ))}
                </dl>
              </div>
              <div className="rounded-2xl border border-slate-200 bg-white p-6">
                <h2 className="font-semibold">开始讨论</h2>
                <label className="mt-4 block text-sm">
                  讨论标题
                  <input
                    value={title}
                    maxLength={500}
                    onChange={(event) => setTitle(event.target.value)}
                    className="mt-2 w-full rounded-lg border border-slate-300 p-3"
                  />
                </label>
                <button
                  disabled={busy || !title.trim()}
                  onClick={() => void startDiscussion()}
                  className="mt-4 rounded-lg bg-blue-600 px-5 py-3 text-sm font-medium text-white disabled:opacity-50"
                >
                  {busy ? "正在处理…" : "新建讨论"}
                </button>
                <p className="mt-3 text-xs leading-5 text-slate-500">
                  资料尚在处理时也可以讨论。每轮采用空间当前目标、约束及关联版本。
                </p>
              </div>
              <section
                aria-label="空间讨论"
                className="rounded-2xl border border-slate-200 bg-white p-6"
              >
                <h2 className="font-semibold">空间讨论</h2>
                {sessions.length === 0 ? (
                  <p className="mt-4 text-sm text-slate-500">暂无讨论。</p>
                ) : (
                  <ul className="mt-4 space-y-3">
                    {sessions.map((session) => (
                      <li key={session.id}>
                        <Link
                          href={`/chat/${session.id}`}
                          className="block rounded-lg bg-slate-50 p-3 text-sm text-blue-800 hover:bg-blue-50"
                        >
                          {session.title}
                        </Link>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
              <div className="rounded-2xl border border-slate-200 bg-white p-6">
                <h2 className="font-semibold">归入旧会话</h2>
                <p className="mt-2 text-xs leading-5 text-slate-500">
                  保留原消息和引用；之后的新提问采用本空间设置。正在运行的会话需等待结束。
                </p>
                <label className="mt-4 block text-sm">
                  未归属会话
                  <select
                    value={selected}
                    onChange={(event) => setSelected(event.target.value)}
                    className="mt-2 w-full rounded-lg border border-slate-300 bg-white p-3"
                  >
                    <option value="">选择旧会话</option>
                    {unassigned.map((session) => (
                      <option key={session.id} value={session.id}>
                        {session.title}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  disabled={busy || !selected}
                  onClick={() => void assign()}
                  className="mt-4 rounded-lg border border-slate-300 px-4 py-2 text-sm disabled:opacity-50"
                >
                  归入此空间
                </button>
              </div>
            </section>
            <aside
              aria-label="空间资料"
              className="rounded-2xl border border-slate-200 bg-white p-6"
            >
              <h2 className="font-semibold">
                空间资料 · {space.materials.length}
              </h2>
              <p className="mt-2 text-xs leading-5 text-slate-500">
                关联固定论文版本，处理状态每 10 秒更新。
              </p>
              {space.materials.length === 0 ? (
                <p className="mt-4 text-sm text-slate-500">尚未关联论文。</p>
              ) : (
                <ul className="mt-5 space-y-4">
                  {space.materials.map((material) => (
                    <li
                      key={material.document_version_id}
                      className="border-t border-slate-100 pt-4"
                    >
                      <p className="break-words text-sm font-medium">
                        {material.title}
                      </p>
                      <p className="mt-2 text-xs text-slate-500">
                        {materialStatus(material.status)} · 版本{" "}
                        {material.document_version_id.slice(0, 8)}
                        {!material.is_current_version && " · 历史版本"}
                      </p>
                    </li>
                  ))}
                </ul>
              )}
              <Link href="/papers" className="mt-5 block text-sm text-blue-700">
                前往论文工作台
              </Link>
            </aside>
          </div>
        </>
      )}
    </main>
  );
}
