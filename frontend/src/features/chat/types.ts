export type PaperEvidenceBlock = {
  type: "paper_evidence";
  text: string;
  citationIds: string[];
};

export type ModelKnowledgeBlock = {
  type: "model_knowledge";
  text: string;
};

export type AnswerBlock = PaperEvidenceBlock | ModelKnowledgeBlock;

export type Citation = {
  id: string;
  chunkId: string;
  documentId: string;
  documentVersionId: string;
  title: string;
  sectionPath: string[];
  pageStart: number;
  pageEnd: number;
  quote: string;
};

export type UserChatMessage = {
  id: string;
  role: "user";
  text: string;
};

export type AssistantChatMessage = {
  id: string;
  role: "assistant";
  blocks: AnswerBlock[];
  citations?: Citation[];
  streamingText?: string;
  streaming?: boolean;
};

export type ChatMessage = UserChatMessage | AssistantChatMessage;

export type RetrievalMode = "auto" | "off" | "on";

export type WorkflowUpdate = {
  stage: "plan" | "retrieval" | "rerank" | "citation_verification" | "answer";
  status: "running" | "completed" | "failed";
  errorCode?: string;
};

export type CitationData = {
  blocks: AnswerBlock[];
  citations: Citation[];
  warnings: string[];
};

export type UIStreamPart =
  | { type: "start"; messageId: string }
  | { type: "data-workflow"; data: WorkflowUpdate }
  | { type: "text-start"; id: string }
  | { type: "text-delta"; id: string; delta: string }
  | { type: "text-end"; id: string }
  | { type: "data-citations"; data: CitationData }
  | { type: "finish" };
