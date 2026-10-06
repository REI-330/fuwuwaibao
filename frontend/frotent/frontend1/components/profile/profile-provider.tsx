"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { GrowthRecord } from "../../types/contracts/growth";
import type { GrowthState, ProfileCandidate } from "../../types/view-models/dynamic-profile";
import { confirmGrowthCandidate, emptyGrowthState } from "../../lib/client/profile-state";
import { listGrowthRecords } from "../../lib/client/growth-api";
import { getProfile, saveCandidateProfile } from "../../lib/client/profile-api";

/**
 * 成长记录时间线的服务端来源。
 *
 * 以前这里只有 `useState(emptyGrowthState)`：时间线全在前端内存里，**刷新就丢**。
 * 现在时间线的真身是服务端的成长记录（`GET /api/growth-records`，带位移分页），
 * 本地只保留「本次会话里刚发生、还没写进服务端」的那部分。
 *
 * 写入路径仍然是「写一条记录 → 派生**待确认**候选」，候选的确认权在用户手上，这里不代签。
 */
export const ARCHIVE_PAGE_SIZE = 20;

export type ArchiveState = {
  items: GrowthRecord[];
  /** 满足筛选条件的总条数（用于「还有多少条」这类如实提示） */
  total: number;
  nextCursor: string | null;
  hasMore: boolean;
};

const EMPTY_ARCHIVE: ArchiveState = { items: [], total: 0, nextCursor: null, hasMore: false };

type ProfileContextValue = GrowthState & {
  confirm: (candidate: ProfileCandidate) => Promise<void>;
  archive: ArchiveState;
  archiveStatus: "idle" | "loading" | "ready" | "error";
  archiveError: string;
  archiveKind: string | undefined;
  setArchiveKind: (kind: string | undefined) => void;
  refreshArchive: () => Promise<void>;
  loadMoreArchive: () => Promise<void>;
  /** 写入成功后把**服务端已落库的那一条**立刻放进列表（不是乐观猜测） */
  addArchiveRecord: (record: GrowthRecord) => void;
};

const ProfileContext = createContext<ProfileContextValue | null>(null);
const RECORDS_STORAGE_PREFIX = "xiangxin.confirmed-profile-records:";

export function ProfileProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<GrowthState>(emptyGrowthState);
  const [archive, setArchive] = useState<ArchiveState>(EMPTY_ARCHIVE);
  const [archiveStatus, setArchiveStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [archiveError, setArchiveError] = useState("");
  const [archiveKind, setArchiveKind] = useState<string | undefined>(undefined);
  const profileStorageKeyRef = useRef<string | null>(null);
  const hydratedRef = useRef(false);
  // 只看最新一次请求的结果：翻页/切筛选时旧响应回来不该覆盖新的
  const ticketRef = useRef(0);

  useEffect(() => {
    let active = true;
    getProfile().then(profile => {
      if (!active) return;
      const key = `${RECORDS_STORAGE_PREFIX}${profile.userId}`;
      profileStorageKeyRef.current = key;
      let records: GrowthState["records"] = [];
      try {
        const stored = window.localStorage.getItem(key);
        const parsed = stored ? JSON.parse(stored) : [];
        if (Array.isArray(parsed)) records = parsed;
      } catch {
        records = [];
      }
      setState(current => ({ ...current, records }));
      hydratedRef.current = true;
    }).catch(() => {
      // 后端不可用时仍保留本机用户的已确认记录，避免清空当前 UI。
      profileStorageKeyRef.current = `${RECORDS_STORAGE_PREFIX}user_local`;
      hydratedRef.current = true;
    });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    const key = profileStorageKeyRef.current;
    if (!hydratedRef.current || !key) return;
    try {
      window.localStorage.setItem(key, JSON.stringify(state.records));
    } catch {
      // 存储受限时服务端画像仍已保存，不阻断确认操作。
    }
  }, [state.records]);

  const fetchPage = useCallback(async (kind: string | undefined, cursor?: string) => {
    const ticket = ticketRef.current + 1;
    ticketRef.current = ticket;
    setArchiveStatus("loading");
    setArchiveError("");
    try {
      const page = await listGrowthRecords({ kind, limit: ARCHIVE_PAGE_SIZE, cursor });
      if (ticket !== ticketRef.current) return;
      setArchive(previous => (cursor
        ? {
          items: [...previous.items, ...page.items.filter(item => !previous.items.some(known => known.id === item.id))],
          total: page.total,
          nextCursor: page.nextCursor,
          hasMore: page.hasMore,
        }
        : { items: page.items, total: page.total, nextCursor: page.nextCursor, hasMore: page.hasMore }));
      setArchiveStatus("ready");
    } catch (caught) {
      if (ticket !== ticketRef.current) return;
      setArchiveStatus("error");
      setArchiveError(caught instanceof Error ? caught.message : "成长记录读取失败");
    }
  }, []);

  useEffect(() => {
    // 放到任务队列里再发：fetchPage 会先同步置一次「读取中」，直接在 effect 里调用
    // 会触发 React 的级联渲染告警（react-hooks/set-state-in-effect）。
    const handle = setTimeout(() => { void fetchPage(archiveKind); }, 0);
    return () => clearTimeout(handle);
  }, [fetchPage, archiveKind]);

  const refreshArchive = useCallback(() => fetchPage(archiveKind), [fetchPage, archiveKind]);

  const loadMoreArchive = useCallback(async () => {
    if (!archive.hasMore || !archive.nextCursor || archiveStatus === "loading") return;
    await fetchPage(archiveKind, archive.nextCursor);
  }, [archive.hasMore, archive.nextCursor, archiveStatus, archiveKind, fetchPage]);

  const addArchiveRecord = useCallback((record: GrowthRecord) => {
    setArchive(previous => (previous.items.some(item => item.id === record.id)
      ? previous
      : { ...previous, items: [record, ...previous.items], total: previous.total + 1 }));
  }, []);

  const confirm = useCallback(async (candidate: ProfileCandidate) => {
    const acceptedCandidate = { ...candidate, classificationRequired: false };
    // 登录/游客会话可能在 Provider 首次挂载后才建立；以确认接口返回的
    // userId 重新定位本地快照，避免先写到 user_local 后刷新丢失。
    const profile = await saveCandidateProfile(acceptedCandidate);
    profileStorageKeyRef.current = `${RECORDS_STORAGE_PREFIX}${profile.userId}`;
    setState(current => confirmGrowthCandidate(current, acceptedCandidate, new Date().toISOString()));
  }, []);

  return <ProfileContext.Provider value={{
    ...state,
    confirm,
    archive,
    archiveStatus,
    archiveError,
    archiveKind,
    setArchiveKind,
    refreshArchive,
    loadMoreArchive,
    addArchiveRecord,
  }}>{children}</ProfileContext.Provider>;
}

export function useDynamicProfile() {
  const context = useContext(ProfileContext);
  if (!context) throw new Error("Dynamic profile requires ProfileProvider");
  return context;
}
