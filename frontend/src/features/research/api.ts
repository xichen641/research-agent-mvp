import { apiRequest } from "@/lib/api";

export type SpaceMaterial = {
  document_id: string;
  document_version_id: string;
  title: string;
  filename: string;
  status: string;
  is_current_version: boolean;
};

export type SpaceInput = {
  name: string;
  goal: string;
  task_settings: string;
  resource_constraints: string;
  current_question: string;
  material_version_ids: string[];
};

export type ResearchSpace = Omit<SpaceInput, "material_version_ids"> & {
  id: string;
  revision: number;
  created_at: string;
  updated_at: string;
  materials: SpaceMaterial[];
};

export type SpaceSession = {
  id: string;
  title: string;
  research_space_id: string | null;
};

const base = "/api/v1/research/spaces";
const json = (body: unknown) => ({
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export async function listSpaces() {
  return (
    await apiRequest<{ items: ResearchSpace[] }>(base, { cache: "no-store" })
  ).items;
}

export function getSpace(id: string) {
  return apiRequest<ResearchSpace>(`${base}/${id}`, { cache: "no-store" });
}

export function saveSpace(body: SpaceInput, space?: ResearchSpace) {
  return apiRequest<ResearchSpace>(space ? `${base}/${space.id}` : base, {
    method: space ? "PUT" : "POST",
    ...json(space ? { ...body, expected_revision: space.revision } : body),
  });
}

export async function listSpaceSessions(id: string) {
  return (
    await apiRequest<{ items: SpaceSession[] }>(`${base}/${id}/sessions`, {
      cache: "no-store",
    })
  ).items;
}

export async function listUnassignedSessions() {
  return (
    await apiRequest<{ items: SpaceSession[] }>(
      "/api/v1/chat/sessions?limit=500",
      { cache: "no-store" },
    )
  ).items.filter((item) => !item.research_space_id);
}

export function createSpaceSession(id: string, title: string) {
  return apiRequest<SpaceSession>(`${base}/${id}/sessions`, {
    method: "POST",
    ...json({ title }),
  });
}

export function assignSpaceSession(id: string, sessionId: string) {
  return apiRequest<SpaceSession>(`${base}/${id}/sessions/${sessionId}`, {
    method: "POST",
  });
}

export function getSessionSpace(sessionId: string) {
  return apiRequest<SpaceSession>(`/api/v1/chat/sessions/${sessionId}`, {
    cache: "no-store",
  });
}

export function materialStatus(status: string) {
  return (
    {
      READY: "可取证",
      PROCESSING: "处理中",
      NEEDS_REVIEW: "需检查",
      FAILED: "处理失败",
      DELETED: "已删除",
    }[status] ?? status
  );
}
