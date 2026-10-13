import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { importArxivPaper, searchArxiv } from "./api";
import { ArxivSearch } from "./arxiv-search";


vi.mock("./api", () => ({
  searchArxiv: vi.fn(),
  importArxivPaper: vi.fn(),
}));

beforeEach(() => {
  vi.mocked(searchArxiv).mockReset();
  vi.mocked(importArxivPaper).mockReset();
});

it("searches metadata first and downloads only after explicit import", async () => {
  vi.mocked(searchArxiv).mockResolvedValue({
    topic: "agent memory",
    totalResults: 1,
    start: 0,
    itemsPerPage: 10,
    cached: false,
    items: [
      {
        arxivId: "2401.12345v2",
        title: "Reliable Research Memory",
        abstract: "A paper about durable context compression.",
        authors: ["Alice Example"],
        categories: ["cs.AI"],
        primaryCategory: "cs.AI",
        publishedAt: "2026-08-01T10:00:00Z",
        updatedAt: "2026-08-20T10:00:00Z",
        abstractUrl: "https://arxiv.org/abs/2401.12345v2",
        pdfUrl: "https://arxiv.org/pdf/2401.12345v2",
        licenseUrl: null,
      },
    ],
  });
  vi.mocked(importArxivPaper).mockResolvedValue({
    arxivId: "2401.12345v2",
    documentId: "document-1",
    documentVersionId: "version-1",
    taskId: "task-1",
    duplicate: false,
  });
  const onImported = vi.fn();
  render(<ArxivSearch onImported={onImported} />);

  fireEvent.change(screen.getByLabelText("论文主题"), {
    target: { value: "agent memory" },
  });
  fireEvent.click(screen.getByRole("button", { name: "搜索" }));

  expect(await screen.findByText("Reliable Research Memory")).toBeVisible();
  expect(importArxivPaper).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("button", { name: "导入论文库" }));

  expect(await screen.findByText("《Reliable Research Memory》已进入下载与解析流程。")).toBeVisible();
  expect(importArxivPaper).toHaveBeenCalledWith("2401.12345v2");
  expect(onImported).toHaveBeenCalledWith(expect.objectContaining({
    documentId: "document-1",
    taskId: "task-1",
  }));
});

it("loads ten results per page and keeps the submitted topic while paging", async () => {
  vi.mocked(searchArxiv)
    .mockResolvedValueOnce({
      topic: "agent memory",
      totalResults: 21,
      start: 0,
      itemsPerPage: 10,
      cached: false,
      items: [],
    })
    .mockResolvedValueOnce({
      topic: "agent memory",
      totalResults: 21,
      start: 10,
      itemsPerPage: 10,
      cached: false,
      items: [],
    });
  render(<ArxivSearch onImported={vi.fn()} />);

  fireEvent.change(screen.getByLabelText("论文主题"), {
    target: { value: "agent memory" },
  });
  fireEvent.click(screen.getByRole("button", { name: "搜索" }));

  expect(await screen.findByText("第 1 / 3 页")).toBeVisible();
  expect(searchArxiv).toHaveBeenNthCalledWith(1, "agent memory", 0);

  fireEvent.change(screen.getByLabelText("论文主题"), {
    target: { value: "a different draft" },
  });
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));

  expect(await screen.findByText("第 2 / 3 页")).toBeVisible();
  expect(searchArxiv).toHaveBeenNthCalledWith(2, "agent memory", 10);
  expect(screen.getByRole("button", { name: "上一页" })).toBeEnabled();
});
