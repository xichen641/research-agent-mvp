export type ArxivPaper = {
  arxivId: string;
  title: string;
  abstract: string;
  authors: string[];
  categories: string[];
  primaryCategory: string | null;
  publishedAt: string;
  updatedAt: string;
  abstractUrl: string;
  pdfUrl: string;
  licenseUrl: string | null;
};

export type ArxivSearchPage = {
  topic: string;
  totalResults: number;
  start: number;
  itemsPerPage: number;
  items: ArxivPaper[];
  cached: boolean;
};

export type ArxivImportReceipt = {
  arxivId: string;
  documentId: string;
  documentVersionId: string;
  taskId: string;
  duplicate: boolean;
};
