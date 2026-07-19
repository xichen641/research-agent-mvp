import { apiRequest } from "@/lib/api";


type ChatSessionWire = {
  id: string;
  title: string;
  document_ids: string[];
};

export type CreatedChatSession = {
  id: string;
  title: string;
  documentIds: string[];
};

export async function createChatSession(
  title: string,
  documentIds: string[],
): Promise<CreatedChatSession> {
  const session = await apiRequest<ChatSessionWire>("/api/v1/chat/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, document_ids: documentIds }),
  });
  return {
    id: session.id,
    title: session.title,
    documentIds: session.document_ids,
  };
}
