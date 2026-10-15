import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "./client";
import type {
  Incident,
  IncidentDetail,
  Monitor,
  MonitorInput,
  Outcome,
  Page,
  ProbeTestResult,
  RunDetail,
  RunSummary,
  Stats,
  User,
} from "./types";

/** How often the monitor pages poll while open (ms). Short enough to feel live, cheap enough. */
export const LIVE_REFETCH_MS = 10_000;

export const keys = {
  me: ["me"] as const,
  monitors: ["monitors"] as const,
  monitor: (id: string) => ["monitor", id] as const,
  runs: (id: string, filters: object) => ["runs", id, filters] as const,
  recent: (id: string) => ["runs", id, "recent"] as const,
  stats: (id: string, window: string) => ["stats", id, window] as const,
  run: (id: number) => ["run", id] as const,
  incidents: (status?: string) => ["incidents", status ?? "all"] as const,
  incident: (id: string) => ["incident", id] as const,
};

export function useMe() {
  return useQuery({
    queryKey: keys.me,
    queryFn: async (): Promise<User | null> => {
      try {
        return await api<User>("/auth/me");
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null;
        throw e;
      }
    },
    staleTime: 60_000,
    retry: false,
  });
}

export function useMonitors() {
  return useQuery({
    queryKey: keys.monitors,
    queryFn: () => api<Monitor[]>("/monitors"),
    refetchInterval: LIVE_REFETCH_MS,
  });
}

export function useMonitor(id: string) {
  return useQuery({
    queryKey: keys.monitor(id),
    queryFn: () => api<Monitor>(`/monitors/${id}`),
    refetchInterval: LIVE_REFETCH_MS,
  });
}

export function useRecentRuns(id: string, limit = 50) {
  return useQuery({
    queryKey: keys.recent(id),
    queryFn: () => api<Page<RunSummary>>(`/monitors/${id}/runs`, { params: { limit } }),
    refetchInterval: LIVE_REFETCH_MS,
  });
}

export interface RunFilters {
  outcome?: Outcome;
  failure_class?: string;
}

export function useRuns(id: string, filters: RunFilters, cursor?: string) {
  return useQuery({
    queryKey: keys.runs(id, { ...filters, cursor }),
    queryFn: () =>
      api<Page<RunSummary>>(`/monitors/${id}/runs`, {
        params: { limit: 20, cursor, ...filters },
      }),
    placeholderData: (prev) => prev,
  });
}

export function useStats(id: string, window: "24h" | "7d") {
  return useQuery({
    queryKey: keys.stats(id, window),
    queryFn: () => api<Stats>(`/monitors/${id}/stats`, { params: { window } }),
    refetchInterval: LIVE_REFETCH_MS * 3,
  });
}

export function useRun(id: number) {
  return useQuery({ queryKey: keys.run(id), queryFn: () => api<RunDetail>(`/runs/${id}`) });
}

export function useIncidents(status?: "OPEN" | "RESOLVED") {
  return useQuery({
    queryKey: keys.incidents(status),
    queryFn: () => api<Page<Incident>>("/incidents", { params: { status, limit: 100 } }),
    refetchInterval: LIVE_REFETCH_MS,
  });
}

export function useIncident(id: string) {
  return useQuery({
    queryKey: keys.incident(id),
    queryFn: () => api<IncidentDetail>(`/incidents/${id}`),
    refetchInterval: LIVE_REFETCH_MS,
  });
}

export function useMonitorAction(id: string) {
  const qc = useQueryClient();
  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: keys.monitor(id) });
    void qc.invalidateQueries({ queryKey: keys.monitors });
  };
  return {
    pause: useMutation({
      mutationFn: () => api<void>(`/monitors/${id}/pause`, { method: "POST" }),
      onSuccess: invalidate,
    }),
    resume: useMutation({
      mutationFn: () => api<void>(`/monitors/${id}/resume`, { method: "POST" }),
      onSuccess: invalidate,
    }),
    checkNow: useMutation({
      mutationFn: () => api<{ status: string }>(`/monitors/${id}/check`, { method: "POST" }),
      onSuccess: () => {
        // The worker runs it on its next poll; refresh shortly after.
        window.setTimeout(() => {
          void qc.invalidateQueries({ queryKey: ["runs", id] });
          invalidate();
        }, 2500);
      },
    }),
  };
}

export function useSaveMonitor(id?: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: MonitorInput) =>
      id
        ? api<Monitor>(`/monitors/${id}`, { method: "PATCH", body })
        : api<Monitor>("/monitors", { method: "POST", body }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.monitors });
      if (id) void qc.invalidateQueries({ queryKey: keys.monitor(id) });
    },
  });
}

export function useDeleteMonitor() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api<void>(`/monitors/${id}`, { method: "DELETE" }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.monitors }),
  });
}

export function useProbeTest() {
  return useMutation({
    mutationFn: (body: Pick<MonitorInput, "url" | "method" | "timeout_ms" | "headers" | "assertions" | "degraded_ms">) =>
      api<ProbeTestResult>("/probe/test", { method: "POST", body }),
  });
}
