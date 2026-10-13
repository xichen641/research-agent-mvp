"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { listChatSessions } from "@/features/chat/api";
import type { ChatSessionSummary } from "@/features/chat/api";


export default function ChatsPage() {
  const [sessions, setSessions] = useState<ChatSessionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void listChatSessions()
      .then((items) => {
        if (!cancelled) setSessions(items);
      })
      .catch((caught) => {
        if (!cancelled) {
          setError(caught instanceof Error ? caught.message : "历史问答加载失败");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className="min-h-screen px-4 py-8 sm:px-8 lg:py-12">
      <div className="mx-auto max-w-5xl">
        <header>
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-blue-700">
            Durable conversations
          </p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight">历史问答</h1>
          <p className="mt-2 text-sm text-slate-500">
            会话、回答与选定论文范围保存在本地，重新打开后可继续多轮讨论。
          </p>
        </header>

        {error && (
          <p role="alert" className="mt-6 rounded-xl bg-red-50 px-4 py-3 text-red-800">
            {error}
          </p>
        )}
        {loading ? (
          <p role="status" className="mt-8 text-sm text-slate-500">正在加载会话…</p>
        ) : sessions.length === 0 ? (
          <section className="mt-8 rounded-2xl border border-dashed border-slate-300 bg-white p-10 text-center">
            <p className="text-slate-600">尚未创建问答会话。</p>
            <Link href="/papers" className="mt-4 inline-flex rounded-xl bg-blue-600 px-4 py-2 text-sm font-semibold text-white">
              从论文工作台开始
            </Link>
          </section>
        ) : (
          <section aria-label="历史问答列表" className="mt-8 grid gap-3">
            {sessions.map((session) => (
              <Link
                key={session.id}
                href={`/chat/${session.id}`}
                className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm transition hover:border-blue-300 hover:shadow-md"
              >
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <h2 className="font-semibold text-slate-950">{session.title}</h2>
                  <time className="text-xs text-slate-500" dateTime={session.updatedAt}>
                    {new Date(session.updatedAt).toLocaleString("zh-CN")}
                  </time>
                </div>
                <p className="mt-2 text-sm text-slate-500">
                  {session.documentIds.length > 0
                    ? `限定 ${session.documentIds.length} 篇论文`
                    : "全论文库范围"}
                </p>
              </Link>
            ))}
          </section>
        )}
      </div>
    </main>
  );
}
