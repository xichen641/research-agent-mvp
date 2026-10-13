"use client";

import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import type { ChatRunStatus } from "./api";


export type WorkbenchChatActivity = {
  sessionId: string;
  runId: string | null;
  status: ChatRunStatus;
  updatedAt: string;
};

type ChatActivityContextValue = {
  activity: WorkbenchChatActivity | null;
  setActivity: (activity: WorkbenchChatActivity | null) => void;
};

const STORAGE_KEY = "research-agent.active-chat";
const ChatActivityContext = createContext<ChatActivityContextValue>({
  activity: null,
  setActivity: () => undefined,
});

export function ChatActivityProvider({ children }: { children: ReactNode }) {
  const [activity, setActivityState] = useState<WorkbenchChatActivity | null>(null);

  useEffect(() => {
    const stored = readStoredActivity();
    if (!stored) return;
    const restore = window.setTimeout(() => setActivityState(stored), 0);
    return () => window.clearTimeout(restore);
  }, []);

  const setActivity = useCallback((next: WorkbenchChatActivity | null) => {
    setActivityState(next);
    if (typeof window === "undefined") return;
    if (next) {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } else {
      window.localStorage.removeItem(STORAGE_KEY);
    }
  }, []);

  const value = useMemo(() => ({ activity, setActivity }), [activity, setActivity]);
  return (
    <ChatActivityContext.Provider value={value}>
      {children}
    </ChatActivityContext.Provider>
  );
}

export function useChatActivity(): ChatActivityContextValue {
  return useContext(ChatActivityContext);
}

function readStoredActivity(): WorkbenchChatActivity | null {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<WorkbenchChatActivity>;
    if (
      typeof value.sessionId !== "string" ||
      !["QUEUED", "RUNNING", "SUCCEEDED", "FAILED"].includes(value.status ?? "") ||
      typeof value.updatedAt !== "string" ||
      !(typeof value.runId === "string" || value.runId === null)
    ) {
      window.localStorage.removeItem(STORAGE_KEY);
      return null;
    }
    return value as WorkbenchChatActivity;
  } catch {
    window.localStorage.removeItem(STORAGE_KEY);
    return null;
  }
}
