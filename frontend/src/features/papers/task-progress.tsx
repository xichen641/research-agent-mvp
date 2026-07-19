"use client";

import { useEffect, useRef, useState } from "react";

import { ApiProblem } from "@/lib/api";
import { getTask, retryTask as requestRetry } from "./api";
import type { TaskState } from "./types";

type TaskProgressProps = {
  taskId: string;
  fetchTask?: (taskId: string, signal?: AbortSignal) => Promise<TaskState>;
  retryTask?: (taskId: string, signal?: AbortSignal) => Promise<TaskState>;
  onStateChange?: (task: TaskState) => void;
};

const TERMINAL = new Set<TaskState["status"]>([
  "READY",
  "FAILED",
  "CANCELLED",
]);

export function TaskProgress({
  taskId,
  fetchTask = getTask,
  retryTask = requestRetry,
  onStateChange,
}: TaskProgressProps) {
  return (
    <TaskProgressState
      key={taskId}
      taskId={taskId}
      fetchTask={fetchTask}
      retryTask={retryTask}
      onStateChange={onStateChange}
    />
  );
}

function TaskProgressState({
  taskId,
  fetchTask = getTask,
  retryTask = requestRetry,
  onStateChange,
}: TaskProgressProps) {
  const [task, setTask] = useState<TaskState | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);
  const [pollGeneration, setPollGeneration] = useState(0);
  const mounted = useRef(true);
  const retryController = useRef<AbortController | null>(null);
  const stateListener = useRef(onStateChange);

  useEffect(() => {
    stateListener.current = onStateChange;
  }, [onStateChange]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      retryController.current?.abort();
    };
  }, []);

  useEffect(() => {
    let stale = false;
    let timeout: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | undefined;

    const poll = async () => {
      controller = new AbortController();
      try {
        const next = await fetchTask(taskId, controller.signal);
        if (stale) return;
        setTask(next);
        stateListener.current?.(next);
        setProblem(null);
        if (!TERMINAL.has(next.status)) {
          timeout = setTimeout(poll, 2000);
        }
      } catch (caught) {
        if (stale || (caught instanceof DOMException && caught.name === "AbortError")) {
          return;
        }
        setProblem(caught instanceof ApiProblem ? caught.message : "暂时无法读取任务进度");
        timeout = setTimeout(poll, 2000);
      }
    };

    if (pollGeneration === 0) {
      void poll();
    } else {
      timeout = setTimeout(poll, 2000);
    }
    return () => {
      stale = true;
      if (timeout) clearTimeout(timeout);
      controller?.abort();
    };
  }, [fetchTask, pollGeneration, taskId]);

  async function retry() {
    if (!task?.retryable || retrying) return;
    setRetrying(true);
    setProblem(null);
    const controller = new AbortController();
    retryController.current?.abort();
    retryController.current = controller;
    try {
      const queued = await retryTask(taskId, controller.signal);
      if (!mounted.current || controller.signal.aborted) return;
      setTask(queued);
      stateListener.current?.(queued);
      setPollGeneration((value) => value + 1);
    } catch (caught) {
      if (!mounted.current || controller.signal.aborted) return;
      setProblem(caught instanceof ApiProblem ? caught.message : "重试未能受理");
    } finally {
      if (retryController.current === controller) {
        retryController.current = null;
        if (mounted.current) setRetrying(false);
      }
    }
  }

  if (!task && !problem) {
    return <p className="text-sm text-slate-500" role="status">正在读取任务进度…</p>;
  }

  return (
    <div className="mt-4 border-t border-slate-200 pt-4">
      {task && (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm font-semibold text-slate-900">
              {task.stage} · {task.progressCurrent} / {task.progressTotal}
            </p>
            <span className={statusClass(task.status)}>{statusLabel(task.status)}</span>
          </div>
          <progress
            className="mt-3 h-2 w-full accent-blue-700"
            max={Math.max(task.progressTotal, 1)}
            value={task.progressCurrent}
            aria-label="任务进度"
          />
          <p className="mt-2 text-xs text-slate-500">已重试 {task.retryCount} 次</p>
          {task.errorMessage && (
            <p className="mt-3 rounded-lg bg-red-50 p-3 text-sm text-red-800" role="alert">
              {task.errorMessage}
            </p>
          )}
          {task.retryable && (
            <button
              className="mt-3 rounded-lg border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-800 hover:border-blue-600 hover:text-blue-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 disabled:opacity-60"
              type="button"
              disabled={retrying}
              onClick={retry}
            >
              {retrying ? "重试受理中…" : "重试任务"}
            </button>
          )}
        </>
      )}
      {problem && <p className="mt-3 text-sm text-red-700" role="alert">{problem}</p>}
    </div>
  );
}

function statusLabel(status: TaskState["status"]): string {
  return {
    QUEUED: "排队中",
    RUNNING: "处理中",
    READY: "已完成",
    FAILED: "失败",
    CANCELLED: "已取消",
  }[status];
}

function statusClass(status: TaskState["status"]): string {
  const tone = status === "FAILED"
    ? "bg-red-100 text-red-800"
    : status === "READY"
      ? "bg-emerald-100 text-emerald-800"
      : "bg-blue-100 text-blue-800";
  return `rounded-full px-3 py-1 text-xs font-semibold ${tone}`;
}
