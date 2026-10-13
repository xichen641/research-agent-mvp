export type NoteFieldStatus = "AI_GENERATED" | "USER_CONFIRMED";

export type NoteField = {
  key: string;
  title: string;
  markdown: string;
  evidence_chunk_ids: string[];
  status: NoteFieldStatus;
};

export type PaperNoteContent = { fields: NoteField[] };

export type NoteEvidence = {
  field_key: string;
  chunk_id: string;
  document_id: string;
  document_version_id: string;
  title: string;
  section_path: string[];
  page_start: number;
  page_end: number;
  quote: string;
  pdf_url: string;
};

export type NoteRevision = {
  id: string;
  note_id: string;
  revision_number: number;
  source: "AI_DRAFT" | "USER_EDIT";
  content: PaperNoteContent;
  created_at: string;
  evidence: NoteEvidence[];
};

export type PaperNote = {
  id: string;
  document_id: string;
  current_revision_id: string | null;
  current_revision: NoteRevision | null;
};
