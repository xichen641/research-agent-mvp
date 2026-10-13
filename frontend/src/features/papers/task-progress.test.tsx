import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { TaskState } from "./types";
import { TaskProgress } from "./task-progress";

const task = (overrides: Partial<TaskState> = {}): TaskState => ({
  id: "task-1",
  documentVersionId: "version-1",
  stage: "PARSING",
  status: "RUNNING",
  progressCurrent: 2,
  progressTotal: 8,
  retryCount: 0,
  errorCode: null,
  errorMessage: null,
  retryable: false,
  ...overrides,
});

describe("TaskProgress", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("polls without overlap and stops on durable failure", async () => {
    let active = 0;
    let maximumActive = 0;
    const fetchTask = vi.fn(async () => {
      active += 1;
      maximumActive = Math.max(maximumActive, active);
      const result = fetchTask.mock.calls.length === 1
        ? task()
        : task({
            status: "FAILED",
            errorCode: "PARSE_PARSER_FAILED",
            errorMessage: "Source document could not be parsed",
            retryable: true,
          });
      active -= 1;
      return result;
    });
    render(<TaskProgress taskId="task-1" fetchTask={fetchTask} />);

    await act(async () => undefined);
    expect(screen.getByText("PARSING · 2 / 8")).toBeVisible();
    await act(async () => vi.advanceTimersByTimeAsync(2000));
    expect(screen.getByText("Source document could not be parsed")).toBeVisible();
    await act(async () => vi.advanceTimersByTimeAsync(4000));

    expect(fetchTask).toHaveBeenCalledTimes(2);
    expect(maximumActive).toBe(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("reports durable task state to the surrounding workspace", async () => {
    const onStateChange = vi.fn();
    const ready = task({ status: "READY", progressCurrent: 8 });
    render(
      <TaskProgress
        taskId="task-1"
        fetchTask={vi.fn().mockResolvedValue(ready)}
        onStateChange={onStateChange}
      />,
    );

    await act(async () => undefined);

    expect(onStateChange).toHaveBeenCalledWith(ready);
  });

  it("cleans up its pending timeout when unmounted", async () => {
    const view = render(
      <TaskProgress taskId="task-1" fetchTask={vi.fn().mockResolvedValue(task())} />,
    );
    await act(async () => undefined);
    expect(screen.getByText("PARSING · 2 / 8")).toBeVisible();
    expect(vi.getTimerCount()).toBe(1);

    view.unmount();

    expect(vi.getTimerCount()).toBe(0);
  });

  it("offers retry only when allowed and resumes polling after acceptance", async () => {
    const failed = task({
      status: "FAILED",
      errorCode: "PARSE_PARSER_FAILED",
      errorMessage: "Source document could not be parsed",
      retryable: true,
    });
    const fetchTask = vi
      .fn()
      .mockResolvedValueOnce(failed)
      .mockResolvedValue(task({ retryCount: 1 }));
    const retryTask = vi.fn().mockResolvedValue(
      task({ status: "QUEUED", retryCount: 1, errorCode: failed.errorCode }),
    );
    render(
      <TaskProgress
        taskId="task-1"
        fetchTask={fetchTask}
        retryTask={retryTask}
      />,
    );

    await act(async () => undefined);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "重试任务" }));
    });
    expect(retryTask).toHaveBeenCalledWith("task-1", expect.anything());
    expect(screen.getByText("排队中")).toBeVisible();
    expect(screen.getByText("PARSING · 2 / 8")).toBeVisible();
    await act(async () => vi.advanceTimersByTimeAsync(2000));

    expect(fetchTask).toHaveBeenCalledTimes(2);
    expect(screen.getByText("PARSING · 2 / 8")).toBeVisible();
  });

  it("aborts an in-flight retry when the task changes", async () => {
    let resolveRetry: ((value: TaskState) => void) | undefined;
    let retrySignal: AbortSignal | undefined;
    const failed = task({ status: "FAILED", retryable: true });
    const fetchTask = vi.fn(async (taskId: string) => (
      taskId === "task-1"
        ? failed
        : task({ id: "task-2", stage: "EMBEDDING", progressCurrent: 5 })
    ));
    const retryTask = vi.fn((_taskId: string, signal?: AbortSignal) => {
      retrySignal = signal;
      return new Promise<TaskState>((resolve) => {
        resolveRetry = resolve;
      });
    });
    const view = render(
      <TaskProgress taskId="task-1" fetchTask={fetchTask} retryTask={retryTask} />,
    );
    await act(async () => undefined);
    fireEvent.click(screen.getByRole("button"));
    expect(retrySignal?.aborted).toBe(false);

    view.rerender(
      <TaskProgress taskId="task-2" fetchTask={fetchTask} retryTask={retryTask} />,
    );
    await act(async () => undefined);

    expect(retrySignal?.aborted).toBe(true);
    await act(async () => {
      resolveRetry?.(task({ status: "QUEUED" }));
    });
    expect(screen.getByText("EMBEDDING · 5 / 8")).toBeVisible();
  });

  it("does not show retry controls from the previous task while loading a new task", async () => {
    let resolveSecond: ((value: TaskState) => void) | undefined;
    const fetchTask = vi.fn((taskId: string) => (
      taskId === "task-1"
        ? Promise.resolve(task({ status: "FAILED", retryable: true }))
        : new Promise<TaskState>((resolve) => {
            resolveSecond = resolve;
          })
    ));
    const view = render(<TaskProgress taskId="task-1" fetchTask={fetchTask} />);
    await act(async () => undefined);
    expect(screen.getByRole("button", { name: "重试任务" })).toBeVisible();

    view.rerender(<TaskProgress taskId="task-2" fetchTask={fetchTask} />);
    await act(async () => undefined);

    expect(screen.queryByRole("button", { name: "重试任务" })).not.toBeInTheDocument();
    await act(async () => {
      resolveSecond?.(task({ id: "task-2" }));
    });
  });
});
