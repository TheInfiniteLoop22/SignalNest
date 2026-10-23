import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "./client";
import type { Incident, Monitor, MonitorInput, Run, User } from "./types";

/** How often pages poll while open (ms). Short enough to feel live, cheap enough. */
export const LIVE_REFETCH_MS = 10_000;

export const keys = {
  me: ["me"] as const,
  monitors: ["monitors"] as const,
  monitor: (id: string) => ["monitor", id] as const,
  runs: (id: string) => ["runs", id] as const,
  incidents: ["incidents"] as const,
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
    enabled: id !== "", // the "new monitor" form has no id: nothing to fetch
  });
}

export function useRuns(id: string) {
  return useQuery({
    queryKey: keys.runs(id),
    queryFn: () => api<Run[]>(`/monitors/${id}/runs`, { params: { limit: 50 } }),
    refetchInterval: LIVE_REFETCH_MS,
  });
}

export function useIncidents() {
  return useQuery({
    queryKey: keys.incidents,
    queryFn: () => api<Incident[]>("/incidents", { params: { limit: 100 } }),
    refetchInterval: LIVE_REFETCH_MS,
  });
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
