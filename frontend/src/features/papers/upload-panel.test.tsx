import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { UploadPanel } from "./upload-panel";

describe("UploadPanel", () => {
  it("rejects a non-PDF before calling the API", async () => {
    const upload = vi.fn();
    render(<UploadPanel upload={upload} />);
    const input = screen.getByLabelText("选择 PDF");
    const file = new File(["text"], "paper.txt", { type: "text/plain" });

    fireEvent.change(input, { target: { files: [file] } });

    expect(await screen.findByText("仅支持 PDF 文件")).toBeVisible();
    expect(upload).not.toHaveBeenCalled();
  });

  it("shows the selected file and disables submission while uploading", async () => {
    let resolveUpload!: (value: {
      documentId: string;
      versionId: string;
      taskId: string;
      duplicate: boolean;
    }) => void;
    const upload = vi.fn(
      () =>
        new Promise<{
          documentId: string;
          versionId: string;
          taskId: string;
          duplicate: boolean;
        }>((resolve) => {
          resolveUpload = resolve;
        }),
    );
    render(<UploadPanel upload={upload} />);
    const file = new File([new Uint8Array(2048)], "study.pdf", {
      type: "application/pdf",
    });

    fireEvent.change(screen.getByLabelText("选择 PDF"), {
      target: { files: [file] },
    });
    expect(screen.getByText("study.pdf")).toBeVisible();
    expect(screen.getByText("2 KB")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "上传论文" }));
    expect(screen.getByRole("button", { name: "上传中…" })).toBeDisabled();

    resolveUpload({
      documentId: "document-1",
      versionId: "version-1",
      taskId: "task-1",
      duplicate: false,
    });
    expect(await screen.findByText("上传已受理")).toBeVisible();
  });

  it("reports a duplicate receipt to the owner without creating UI state itself", async () => {
    const onReceipt = vi.fn();
    const receipt = {
      documentId: "document-1",
      versionId: "version-1",
      taskId: "task-1",
      duplicate: true,
    };
    render(
      <UploadPanel upload={vi.fn().mockResolvedValue(receipt)} onReceipt={onReceipt} />,
    );
    fireEvent.change(screen.getByLabelText("选择 PDF"), {
      target: {
        files: [
          new File(["%PDF-"], "duplicate.pdf", { type: "application/pdf" }),
        ],
      },
    });
    fireEvent.click(screen.getByRole("button", { name: "上传论文" }));

    expect(await screen.findByText("论文已存在，已显示原任务")).toBeVisible();
    expect(onReceipt).toHaveBeenCalledWith(receipt);
  });
});
