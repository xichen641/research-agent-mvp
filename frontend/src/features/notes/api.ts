import { apiRequest } from "@/lib/api";
import type { NoteRevision, PaperNote, PaperNoteContent } from "./types";

export function getPaperNote(documentId: string, signal?: AbortSignal) {
  return apiRequest<PaperNote>(`/api/v1/documents/${documentId}/notes`, {
    signal,
  });
}

export function getNoteRevisions(noteId: string, signal?: AbortSignal) {
  return apiRequest<{ items: NoteRevision[] }>(
    `/api/v1/notes/${noteId}/revisions`,
    { signal },
  );
}

export function requestNoteGeneration(documentId: string) {
  return apiRequest<{ status: "QUEUED" }>(
    `/api/v1/documents/${documentId}/notes/generate`,
    { method: "POST" },
  );
}

export function regenerateNote(noteId: string) {
  return apiRequest<{ status: "QUEUED" }>(
    `/api/v1/notes/${noteId}/regenerate`,
    { method: "POST" },
  );
}

export function savePaperNote(noteId: string, content: PaperNoteContent) {
  return apiRequest<NoteRevision>(`/api/v1/notes/${noteId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
}
