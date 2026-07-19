import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { findPendingDraft, NoteDiff } from "./note-diff";
import { NoteEditor } from "./note-editor";
import type { NoteRevision } from "./types";


function revision(overrides: Partial<NoteRevision> = {}): NoteRevision {
  return {
    id: "revision-1",
    note_id: "note-1",
    revision_number: 1,
    source: "AI_DRAFT",
    created_at: "2026-07-20T00:00:00Z",
    content: {
      fields: [
        {
          key: "innovations",
          title: "主要创新点",
          markdown: "Uses visible-region matching.",
          evidence_chunk_ids: ["chunk-1"],
          status: "AI_GENERATED",
        },
        {
          key: "user_comments",
          title: "用户备注",
          markdown: "",
          evidence_chunk_ids: [],
          status: "USER_CONFIRMED",
        },
      ],
    },
    evidence: [
      {
        field_key: "innovations",
        chunk_id: "chunk-1",
        document_id: "document-1",
        document_version_id: "version-1",
        title: "Paper",
        section_path: ["Method"],
        page_start: 3,
        page_end: 3,
        quote: "Uses visible-region matching.",
        pdf_url: "/api/v1/documents/document-1/pdf?page=3",
      },
    ],
    ...overrides,
  };
}


describe("NoteEditor", () => {
  it("does not surface an obsolete AI draft after the latest draft is discarded", () => {
    const current = revision({
      id: "revision-2",
      revision_number: 2,
      source: "USER_EDIT",
    });
    const latestDraft = revision({ id: "revision-3", revision_number: 3 });
    const initialDraft = revision({ id: "revision-1", revision_number: 1 });

    expect(
      findPendingDraft([latestDraft, current, initialDraft], current, null)?.id,
    ).toBe("revision-3");
    expect(
      findPendingDraft(
        [latestDraft, current, initialDraft],
        current,
        latestDraft.id,
      ),
    ).toBeUndefined();
  });

  it("renders field status and clickable evidence, then saves a user edit", () => {
    const onSave = vi.fn();
    render(<NoteEditor revision={revision()} onSave={onSave} />);

    expect(screen.getByText("AI 生成")).toBeVisible();
    expect(screen.getByRole("link", { name: /证据 1/ })).toHaveAttribute(
      "href",
      "/api/v1/documents/document-1/pdf?page=3",
    );
    fireEvent.change(screen.getByLabelText("主要创新点"), {
      target: { value: "Edited by the user." },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存新版本" }));

    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        fields: expect.arrayContaining([
          expect.objectContaining({
            key: "innovations",
            markdown: "Edited by the user.",
            status: "USER_CONFIRMED",
          }),
        ]),
      }),
    );
  });

  it("accepts or rejects regenerated fields without replacing current implicitly", () => {
    const onAccept = vi.fn();
    const draft = revision({
      id: "revision-2",
      revision_number: 2,
      content: {
        fields: [
          {
            key: "innovations",
            title: "主要创新点",
            markdown: "A regenerated innovation.",
            evidence_chunk_ids: ["chunk-1"],
            status: "AI_GENERATED",
          },
          {
            key: "user_comments",
            title: "用户备注",
            markdown: "",
            evidence_chunk_ids: [],
            status: "USER_CONFIRMED",
          },
        ],
      },
    });
    render(
      <NoteDiff
        current={revision()}
        draft={draft}
        onAccept={onAccept}
        onDiscard={vi.fn()}
      />,
    );

    expect(screen.getByText("Uses visible-region matching.")).toBeVisible();
    expect(screen.getByText("A regenerated innovation.")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "拒绝此字段" }));
    expect(screen.queryByText("A regenerated innovation.")).not.toBeInTheDocument();
    expect(onAccept).not.toHaveBeenCalled();
  });

  it("keeps user comments when accepting the whole AI draft", () => {
    const onAccept = vi.fn();
    const current = revision({
      content: {
        fields: revision().content.fields.map((field) =>
          field.key === "user_comments"
            ? { ...field, markdown: "My private research note." }
            : field,
        ),
      },
    });
    const draft = revision({
      id: "revision-2",
      content: {
        fields: revision().content.fields.map((field) =>
          field.key === "innovations"
            ? { ...field, markdown: "Regenerated evidence-backed text." }
            : field,
        ),
      },
    });
    render(
      <NoteDiff
        current={current}
        draft={draft}
        onAccept={onAccept}
        onDiscard={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "全部接受" }));

    const accepted = onAccept.mock.calls[0][0];
    expect(
      accepted.fields.find((field: { key: string }) => field.key === "user_comments")
        .markdown,
    ).toBe("My private research note.");
  });
});
