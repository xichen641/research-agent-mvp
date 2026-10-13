import type { Metadata } from "next";
import { ChatActivityProvider } from "@/features/chat/chat-activity";
import { WorkspaceShell } from "@/features/workspace/workspace-shell";
import "./globals.css";

export const metadata: Metadata = {
  title: "ResearchAgent 研究工作台",
  description: "管理论文、证据问答与版本化研究笔记",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" className="h-full antialiased">
      <body className="min-h-full">
        <ChatActivityProvider>
          <WorkspaceShell>{children}</WorkspaceShell>
        </ChatActivityProvider>
      </body>
    </html>
  );
}
