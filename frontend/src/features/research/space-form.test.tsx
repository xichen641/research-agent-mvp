import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { SpaceForm } from "./space-form";

afterEach(() => vi.unstubAllGlobals());

it("creates a space without waiting for papers and sends user constraints", async () => {
  const saved = vi.fn();
  const fetch = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) =>
    init?.method === "POST"
      ? Response.json({ id: "space-1" })
      : Response.json({ items: [], next_cursor: null }),
  );
  vi.stubGlobal("fetch", fetch);
  render(<SpaceForm onSaved={saved} />);
  fireEvent.change(screen.getByLabelText("空间名称"), {
    target: { value: "ReID" },
  });
  fireEvent.change(screen.getByLabelText("资源约束"), {
    target: { value: "12GB" },
  });
  fireEvent.click(screen.getByRole("button", { name: "创建空间" }));
  await waitFor(() => expect(saved).toHaveBeenCalledWith({ id: "space-1" }));
  const post = fetch.mock.calls.find(([, init]) => init?.method === "POST");
  expect(JSON.parse(String(post?.[1]?.body))).toMatchObject({
    name: "ReID",
    resource_constraints: "12GB",
    material_version_ids: [],
  });
});

it("preserves pinned material versions and surfaces revision conflicts", async () => {
  const saved = vi.fn();
  const fetch = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) =>
    init?.method === "PUT"
      ? Response.json(
          {
            code: "RESEARCH_SPACE_REVISION_CONFLICT",
            message: "空间已被修改，请重新加载后再保存",
          },
          { status: 409 },
        )
      : Response.json({ items: [], next_cursor: null }),
  );
  vi.stubGlobal("fetch", fetch);
  render(
    <SpaceForm
      space={{
        id: "space-1",
        name: "ReID",
        goal: "比较机制",
        resource_constraints: "12GB",
        task_settings: "",
        current_question: "",
        revision: 3,
        created_at: "",
        updated_at: "",
        materials: [
          {
            document_id: "paper-1",
            document_version_id: "old-version",
            title: "原论文",
            filename: "paper.pdf",
            status: "READY",
            is_current_version: false,
          },
        ],
      }}
      onSaved={saved}
    />,
  );
  await screen.findByText("原论文");
  fireEvent.click(screen.getByRole("button", { name: "保存修订" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("重新加载");
  expect(saved).not.toHaveBeenCalled();
  const put = fetch.mock.calls.find(([, init]) => init?.method === "PUT");
  expect(JSON.parse(String(put?.[1]?.body))).toMatchObject({
    expected_revision: 3,
    material_version_ids: ["old-version"],
  });
});

it("keeps the original edit revision when background polling receives another update", async () => {
  const fetch = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) =>
    init?.method === "PUT"
      ? Response.json({ id: "space-1" })
      : Response.json({ items: [], next_cursor: null }),
  );
  vi.stubGlobal("fetch", fetch);
  const space = {
    id: "space-1",
    name: "ReID",
    goal: "",
    resource_constraints: "24GB",
    task_settings: "",
    current_question: "",
    revision: 1,
    created_at: "",
    updated_at: "",
    materials: [],
  };
  const { rerender } = render(<SpaceForm space={space} onSaved={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("资源约束"), {
    target: { value: "12GB" },
  });
  rerender(
    <SpaceForm
      space={{ ...space, revision: 2, resource_constraints: "48GB" }}
      onSaved={vi.fn()}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "保存修订" }));
  await waitFor(() =>
    expect(fetch.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(
      true,
    ),
  );
  const put = fetch.mock.calls.find(([, init]) => init?.method === "PUT");
  expect(JSON.parse(String(put?.[1]?.body))).toMatchObject({
    expected_revision: 1,
    resource_constraints: "12GB",
  });
});

it("shows deleted materials without selecting them again when saving", async () => {
  const saved = vi.fn();
  const fetch = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) =>
    init?.method === "PUT"
      ? Response.json({ id: "space-1" })
      : Response.json({ items: [], next_cursor: null }),
  );
  vi.stubGlobal("fetch", fetch);
  render(<SpaceForm space={{
    id: "space-1", name: "ReID", goal: "", resource_constraints: "",
    task_settings: "", current_question: "", revision: 2, created_at: "", updated_at: "",
    materials: [
      { document_id: "deleted", document_version_id: "deleted-version", title: "已移除论文", filename: "deleted.pdf", status: "DELETED", is_current_version: true },
      { document_id: "ready", document_version_id: "ready-version", title: "保留论文", filename: "ready.pdf", status: "READY", is_current_version: true },
    ],
  }} onSaved={saved} />);

  const deleted = await screen.findByRole("checkbox", { name: /已移除论文/ });
  expect(deleted).toBeDisabled();
  expect(deleted).not.toBeChecked();
  expect(screen.getByRole("checkbox", { name: /保留论文/ })).toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "保存修订" }));
  await waitFor(() => expect(saved).toHaveBeenCalledWith({ id: "space-1" }));
  const put = fetch.mock.calls.find(([, init]) => init?.method === "PUT");
  expect(JSON.parse(String(put?.[1]?.body))).toMatchObject({
    expected_revision: 2,
    material_version_ids: ["ready-version"],
  });
});
