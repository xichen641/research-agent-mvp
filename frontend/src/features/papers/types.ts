export type UploadReceipt = {
  documentId: string;
  versionId: string;
  taskId: string;
  duplicate: boolean;
};

export type TaskStatus =
  | "QUEUED"
  | "RUNNING"
  | "READY"
  | "FAILED"
  | "CANCELLED";

export type TaskState = {
  id: string;
  documentVersionId: string;
  stage: string;
  status: TaskStatus;
  progressCurrent: number;
  progressTotal: number;
  retryCount: number;
  errorCode: string | null;
  errorMessage: string | null;
  retryable: boolean;
};

export type PaperSummary = {
  id: string;
  title: string;
  status: string;
  currentVersionId: string;
  latestTaskId: string;
  createdAt: string;
  updatedAt: string;
};

export type PaperList = {
  items: PaperSummary[];
  nextCursor: string | null;
};
