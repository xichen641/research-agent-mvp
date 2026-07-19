import { ChatWorkspace } from "@/features/chat/chat-workspace";

export default async function ChatPage({
  params,
}: {
  params: Promise<{ sessionId: string }>;
}) {
  const { sessionId } = await params;

  return (
    <main className="min-h-screen bg-slate-50 px-4 py-8 text-slate-950 sm:px-8 lg:py-12">
      <div className="mx-auto max-w-6xl">
        <header className="mb-8 max-w-3xl">
          <p className="text-sm font-semibold uppercase tracking-[0.2em] text-blue-700">
            ResearchAgent
          </p>
          <h1 className="mt-3 text-4xl font-semibold tracking-tight">论文问答</h1>
          <p className="mt-3 text-slate-600">
            仅在引用校验完成后展示回答，并明确区分论文证据与模型补充。
          </p>
        </header>
        <ChatWorkspace sessionId={sessionId} />
      </div>
    </main>
  );
}
