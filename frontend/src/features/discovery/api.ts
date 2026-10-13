import { apiRequest } from "@/lib/api";

import type {
  ArxivImportReceipt,
  ArxivPaper,
  ArxivSearchPage,
} from "./types";


type ArxivPaperWire = {
  arxiv_id: string;
  title: string;
  abstract: string;
  authors: string[];
  categories: string[];
  primary_category: string | null;
  published_at: string;
  updated_at: string;
  abstract_url: string;
  pdf_url: string;
  license_url: string | null;
};

type ArxivSearchPageWire = {
  topic: string;
  total_results: number;
  start: number;
  items_per_page: number;
  items: ArxivPaperWire[];
  cached: boolean;
};

type ArxivImportReceiptWire = {
  arxiv_id: string;
  document_id: string;
  document_version_id: string;
  task_id: string;
  duplicate: boolean;
};

export async function searchArxiv(
  topic: string,
  start = 0,
): Promise<ArxivSearchPage> {
  const params = new URLSearchParams({
    topic,
    start: String(start),
    limit: "10",
  });
  const result = await apiRequest<ArxivSearchPageWire>(
    `/api/v1/discovery/arxiv/search?${params.toString()}`,
  );
  return {
    topic: result.topic,
    totalResults: result.total_results,
    start: result.start,
    itemsPerPage: result.items_per_page,
    items: result.items.map(toPaper),
    cached: result.cached,
  };
}

export async function importArxivPaper(
  arxivId: string,
): Promise<ArxivImportReceipt> {
  const result = await apiRequest<ArxivImportReceiptWire>(
    "/api/v1/discovery/arxiv/import",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ arxiv_id: arxivId }),
    },
  );
  return {
    arxivId: result.arxiv_id,
    documentId: result.document_id,
    documentVersionId: result.document_version_id,
    taskId: result.task_id,
    duplicate: result.duplicate,
  };
}

function toPaper(value: ArxivPaperWire): ArxivPaper {
  return {
    arxivId: value.arxiv_id,
    title: value.title,
    abstract: value.abstract,
    authors: value.authors,
    categories: value.categories,
    primaryCategory: value.primary_category,
    publishedAt: value.published_at,
    updatedAt: value.updated_at,
    abstractUrl: value.abstract_url,
    pdfUrl: value.pdf_url,
    licenseUrl: value.license_url,
  };
}
