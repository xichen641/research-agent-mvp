"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { useChatActivity } from "@/features/chat/chat-activity";


export function WorkspaceShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { activity } = useChatActivity();
  const contextual = pathname.startsWith("/chat/")
    ? { href: pathname, label: "当前问答" }
    : pathname.includes("/notes")
      ? { href: pathname, label: "当前笔记" }
      : null;

  return (
    <div className="min-h-screen bg-[#f3f6fa] text-slate-950 lg:flex">
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col bg-[#0b1220] px-4 py-5 text-slate-200 lg:flex">
        <Link href="/papers" className="flex items-center gap-3 px-2">
          <span className="grid size-9 place-items-center rounded-xl bg-blue-500 font-bold text-white">
            R
          </span>
          <span>
            <span className="block text-sm font-semibold text-white">ResearchAgent</span>
            <span className="block text-[11px] text-slate-400">Paper workspace</span>
          </span>
        </Link>

        <nav aria-label="工作台导航" className="mt-8 space-y-1">
          <WorkspaceLink href="/research" label="研究空间" active={pathname.startsWith("/research")} icon="library" />
          <WorkspaceLink
            href="/papers"
            label="论文工作台"
            active={pathname === "/papers" || pathname === "/"}
            icon="library"
          />
          <WorkspaceLink
            href="/chats"
            label="历史问答"
            active={pathname === "/chats"}
            icon="chat"
          />
          {contextual && (
            <WorkspaceLink
              href={contextual.href}
              label={contextual.label}
              active
              icon={contextual.label === "当前问答" ? "chat" : "note"}
            />
          )}
          {activity && !pathname.startsWith(`/chat/${activity.sessionId}`) && (
            <Link
              href={`/chat/${activity.sessionId}`}
              className="mt-3 block rounded-xl border border-blue-400/20 bg-blue-500/10 px-3 py-3 text-sm text-blue-100"
            >
              <span className="flex items-center gap-2 font-medium">
                <span className={`size-2 rounded-full ${activity.status === "QUEUED" || activity.status === "RUNNING" ? "animate-pulse bg-amber-300" : activity.status === "SUCCEEDED" ? "bg-emerald-300" : "bg-red-300"}`} />
                {activityLabel(activity.status)}
              </span>
              <span className="mt-1 block text-xs text-slate-400">点击返回当前问答</span>
            </Link>
          )}
        </nav>

        <div className="mt-8 border-t border-white/10 pt-6">
          <p className="px-3 text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">
            研究能力
          </p>
          <ul className="mt-3 space-y-3 px-3 text-sm text-slate-400">
            <li className="flex items-center gap-2">
              <span className="size-1.5 rounded-full bg-emerald-400" />
              引用验证问答
            </li>
            <li className="flex items-center gap-2">
              <span className="size-1.5 rounded-full bg-blue-400" />
              版本化论文笔记
            </li>
          </ul>
        </div>

        <div className="mt-auto rounded-2xl border border-white/10 bg-white/5 p-3">
          <div className="flex items-center gap-2 text-sm font-medium text-slate-200">
            <span className="size-2 rounded-full bg-emerald-400 shadow-[0_0_0_4px_rgba(52,211,153,0.12)]" />
            本地研究空间
          </div>
          <p className="mt-1 pl-4 text-xs leading-5 text-slate-500">
            论文与修订持久保存在本地服务
          </p>
        </div>
      </aside>

      <div className="min-w-0 flex-1">
        <header className="flex h-16 items-center justify-between border-b border-slate-200 bg-white px-4 lg:hidden">
          <Link href="/papers" className="flex items-center gap-2 font-semibold text-slate-950">
            <span className="grid size-8 place-items-center rounded-lg bg-blue-600 text-sm text-white">R</span>
            ResearchAgent
          </Link>
          <Link href="/research" className="text-sm font-medium text-blue-700">
            研究空间
          </Link>
        </header>
        {children}
      </div>
    </div>
  );
}

function activityLabel(status: "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED") {
  if (status === "QUEUED") return "问答正在排队";
  if (status === "RUNNING") return "问答后台处理中";
  if (status === "SUCCEEDED") return "问答已完成";
  return "问答处理失败";
}

function WorkspaceLink({
  href,
  label,
  active,
  icon,
}: {
  href: string;
  label: string;
  active: boolean;
  icon: "library" | "chat" | "note";
}) {
  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      className={`flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition-colors ${
        active
          ? "bg-blue-500/15 text-blue-200 ring-1 ring-inset ring-blue-400/20"
          : "text-slate-400 hover:bg-white/5 hover:text-white"
      }`}
    >
      <NavIcon name={icon} />
      {label}
    </Link>
  );
}

function NavIcon({ name }: { name: "library" | "chat" | "note" }) {
  const path = name === "library"
    ? "M4 5.5A2.5 2.5 0 0 1 6.5 3H10v14H6.5A2.5 2.5 0 0 0 4 19.5v-14ZM20 5.5A2.5 2.5 0 0 0 17.5 3H14v14h3.5a2.5 2.5 0 0 1 2.5 2.5v-14Z"
    : name === "chat"
      ? "M21 12a8 8 0 0 1-8 8H6l-4 2 1.5-5A8 8 0 1 1 21 12Z"
      : "M6 3h9l3 3v15H6V3Zm3 6h6M9 13h6M9 17h4";
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      <path d={path} />
    </svg>
  );
}
