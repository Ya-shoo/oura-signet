// TanStack Query hooks wrapping the typed IPC surface.
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";

export const useDataStatus = () =>
  useQuery({ queryKey: ["dataStatus"], queryFn: api.dataStatus });
export const useDevice = () => useQuery({ queryKey: ["device"], queryFn: api.device });
export const useSyncStatus = () =>
  useQuery({ queryKey: ["syncStatus"], queryFn: api.syncStatus });
export const useEventCounts = () =>
  useQuery({ queryKey: ["eventCounts"], queryFn: api.eventCounts });
export const useHomeChannels = (date?: string | null) =>
  useQuery({ queryKey: ["homeChannels", date ?? "today"], queryFn: () => api.homeChannels(date) });
export const useAvailableDays = () =>
  useQuery({ queryKey: ["availableDays"], queryFn: api.availableDays });
export const useVo2History = () =>
  useQuery({ queryKey: ["vo2History"], queryFn: api.vo2History });
export const useVo2Trend = () =>
  useQuery({ queryKey: ["vo2Trend"], queryFn: api.vo2Trend });
export const useActivitySessions = (date?: string | null) =>
  useQuery({
    queryKey: ["activitySessions", date ?? "today"],
    queryFn: () => api.activitySessions(date),
  });
export const useProfile = () =>
  useQuery({ queryKey: ["profile"], queryFn: api.profile });
