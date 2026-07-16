import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "论文库 | ResearchAgent",
  description: "上传论文并查看持久化处理进度",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" className="h-full antialiased">
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
