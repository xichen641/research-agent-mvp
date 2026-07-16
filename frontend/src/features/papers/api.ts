import { apiRequest } from "@/lib/api";
import type { PaperList, TaskState, UploadReceipt } from "./types";

type UploadReceiptWire = {
  document_id: string;
  version_id: string;
  task_id: string;
  duplicate: boolean;
};

type PaperSummaryWire = {
  id: string;
  title: string;
  status: string;
  current_version_id: string;
  latest_task_id: string;
  created_at: string;
  updated_at: string;
};

type PaperListWire = {
  items: PaperSummaryWire[];
  next_cursor: string | null;
};

type TaskStateWire = {
  id: string;
  document_version_id: string;
  stage: string;
  status: TaskState["status"];
  progress_current: number;
  progress_total: number;
  retry_count: number;
  error_code: string | null;
  error_message: string | null;
  retryable: boolean;
};

export async function uploadDocument(file: File): Promise<UploadReceipt> {
  const body = new FormData();
  body.append("file", file);
  const receipt = await apiRequest<UploadReceiptWire>("/api/v1/documents", {
    method: "POST",
    body,
  });
  return {
    documentId: receipt.document_id,
    versionId: receipt.version_id,
    taskId: receipt.task_id,
    duplicate: receipt.duplicate,
  };
}

export async function listDocuments(
  cursor?: string,
  signal?: AbortSignal,
): Promise<PaperList> {
  const query = new URLSearchParams({ limit: "50" });
  if (cursor) query.set("cursor", cursor);
  const response = await apiRequest<PaperListWire>(
    `/api/v1/documents?${query.toString()}`,
    { cache: "no-store", signal },
  );
  return {
    items: response.items.map((item) => ({
      id: item.id,
      title: item.title,
      status: item.status,
      currentVersionId: item.current_version_id,
      latestTaskId: item.latest_task_id,
      createdAt: item.created_at,
      updatedAt: item.updated_at,
    })),
    nextCursor: response.next_cursor,
  };
}

export async function getTask(
  taskId: string,
  signal?: AbortSignal,
): Promise<TaskState> {
  return mapTask(
    await apiRequest<TaskStateWire>(`/api/v1/tasks/${taskId}`, { signal }),
  );
}

export async function retryTask(
  taskId: string,
  signal?: AbortSignal,
): Promise<TaskState> {
  return mapTask(
    await apiRequest<TaskStateWire>(`/api/v1/tasks/${taskId}/retry`, {
      method: "POST",
      signal,
    }),
  );
}

function mapTask(task: TaskStateWire): TaskState {
  return {
    id: task.id,
    documentVersionId: task.document_version_id,
    stage: task.stage,
    status: task.status,
    progressCurrent: task.progress_current,
    progressTotal: task.progress_total,
    retryCount: task.retry_count,
    errorCode: task.error_code,
    errorMessage: task.error_message,
    retryable: task.retryable,
  };
}
