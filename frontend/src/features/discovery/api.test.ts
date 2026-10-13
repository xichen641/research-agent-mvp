import { afterEach, expect, it, vi } from "vitest";

import { searchArxiv } from "./api";


afterEach(() => vi.unstubAllGlobals());


it("requests an exact ten-item arXiv page", async () => {
  const fetchMock = vi.fn().mockResolvedValue(Response.json({
    topic: "agent memory",
    total_results: 21,
    start: 10,
    items_per_page: 10,
    items: [],
    cached: false,
  }));
  vi.stubGlobal("fetch", fetchMock);

  await searchArxiv("agent memory", 10);

  expect(fetchMock).toHaveBeenCalledWith(
    "http://127.0.0.1:8000/api/v1/discovery/arxiv/search?topic=agent+memory&start=10&limit=10",
    expect.any(Object),
  );
});
