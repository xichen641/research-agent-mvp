import { expect, it, vi } from "vitest";

import Home from "./page";


const { redirectMock } = vi.hoisted(() => ({ redirectMock: vi.fn() }));

vi.mock("next/navigation", () => ({ redirect: redirectMock }));


it("redirects the root route to the ResearchAgent paper library", () => {
  Home();

  expect(redirectMock).toHaveBeenCalledOnce();
  expect(redirectMock).toHaveBeenCalledWith("/papers");
});
