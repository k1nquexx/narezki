import { create } from "zustand";
import { api } from "../lib/api";
import { getBackendInfo, listenBackendRestart, restartBackend } from "../lib/tauri";
import { wsClient } from "../lib/ws";
import type {
  AdPreset,
  AppSettings,
  BackgroundAsset,
  Clip,
  EncoderProbe,
  GPUInfo,
  Job,
  LogEntry,
  Project,
  SubtitlePreset,
  TabId,
} from "../types/api";

export interface ToastItem {
  id: string;
  type: "info" | "success" | "warning" | "error";
  title: string;
  message?: string;
}

interface AppState {
  activeTab: TabId;
  backendConnected: boolean;
  wsConnected: boolean;
  backendError: string | null;
  backendUrl: string;
  initializing: boolean;

  gpuInfo: GPUInfo | null;
  encoderProbe: EncoderProbe | null;
  settings: AppSettings | null;

  projects: Project[];
  selectedProjectId: string | null;
  clips: Clip[];
  selectedClipId: string | null;
  jobs: Job[];

  subtitlePresets: SubtitlePreset[];
  adPresets: AdPreset[];
  backgrounds: BackgroundAsset[];

  logs: LogEntry[];
  logDrawerOpen: boolean;
  toasts: ToastItem[];

  setActiveTab: (tab: TabId) => void;
  setLogDrawerOpen: (open: boolean) => void;
  selectProject: (projectId: string | null) => Promise<void>;
  selectClip: (clipId: string | null) => void;

  addToast: (toast: Omit<ToastItem, "id">) => void;
  removeToast: (id: string) => void;
  clearLogs: () => void;

  bootstrap: () => Promise<void>;
  reconnectBackend: () => Promise<void>;
  refreshAll: () => Promise<void>;
  refreshProjects: () => Promise<void>;
  refreshClips: (projectId: string) => Promise<void>;
  refreshPresets: () => Promise<void>;
  refreshJobs: () => Promise<void>;
  saveSettings: (next: AppSettings) => Promise<AppSettings>;
}

let wsUnsub: (() => void) | null = null;
let wsStatusUnsub: (() => void) | null = null;
let restartUnsub: (() => void) | null = null;

function makeId(): string {
  return `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

async function waitForHealth(maxAttempts = 25): Promise<boolean> {
  for (let i = 0; i < maxAttempts; i++) {
    try {
      await api.health();
      return true;
    } catch {
      await new Promise((r) => setTimeout(r, 400));
    }
  }
  return false;
}

export const useAppStore = create<AppState>((set, get) => ({
  activeTab: "projects",
  backendConnected: false,
  wsConnected: false,
  backendError: null,
  backendUrl: "http://127.0.0.1:8765",
  initializing: true,

  gpuInfo: null,
  encoderProbe: null,
  settings: null,

  projects: [],
  selectedProjectId: null,
  clips: [],
  selectedClipId: null,
  jobs: [],

  subtitlePresets: [],
  adPresets: [],
  backgrounds: [],

  logs: [],
  logDrawerOpen: false,
  toasts: [],

  setActiveTab: (tab) => set({ activeTab: tab }),
  setLogDrawerOpen: (open) => set({ logDrawerOpen: open }),

  selectProject: async (projectId) => {
    set({ selectedProjectId: projectId, selectedClipId: null });
    if (projectId) {
      await get().refreshClips(projectId);
    } else {
      set({ clips: [] });
    }
  },

  selectClip: (clipId) => set({ selectedClipId: clipId }),

  addToast: (toast) => {
    const id = makeId();
    set((s) => ({ toasts: [...s.toasts, { ...toast, id }] }));
    window.setTimeout(() => {
      get().removeToast(id);
    }, 5000);
  },

  removeToast: (id) =>
    set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),

  clearLogs: () => set({ logs: [] }),

  bootstrap: async () => {
    set({ initializing: true, backendError: null });
    try {
      const info = await getBackendInfo();
      api.setBaseUrl(info.url);
      set({ backendUrl: info.url });

      if (info.error) {
        set({ backendError: info.error });
      }

      const healthy = await waitForHealth(30);
      if (!healthy) {
        set({
          backendConnected: false,
          backendError: info.error || "Не удалось подключиться к Python FastAPI бэкенду.",
          initializing: false,
        });
        return;
      }

      set({ backendConnected: true, backendError: null });

      if (wsUnsub) wsUnsub();
      if (wsStatusUnsub) wsStatusUnsub();
      if (restartUnsub) restartUnsub();

      wsStatusUnsub = wsClient.onStatus((connected) => {
        set({ wsConnected: connected });
      });

      wsUnsub = wsClient.subscribe((evt) => {
        const data = evt.data ?? {};
        if (evt.type === "gpu_status") {
          set({ gpuInfo: data as unknown as GPUInfo });
        } else if (evt.type === "log") {
          const entry: LogEntry = {
            id: makeId(),
            timestamp: evt.timestamp || new Date().toISOString(),
            level: (data.level as LogEntry["level"]) || "info",
            message: String(data.message ?? ""),
            job_id: (data.job_id as string | null) ?? null,
            stage: (data.stage as string | null) ?? null,
          };
          set((s) => ({ logs: [...s.logs.slice(-299), entry] }));
        } else if (evt.type === "job_update") {
          const job = data as unknown as Job;
          set((s) => {
            const exists = s.jobs.some((j) => j.id === job.id);
            const nextJobs = exists
              ? s.jobs.map((j) => (j.id === job.id ? job : j))
              : [job, ...s.jobs];
            return { jobs: nextJobs.slice(0, 150) };
          });

          if (job.status === "completed") {
            if (job.job_type === "analyze" || job.job_type.startsWith("ingest")) {
              void get().refreshProjects();
              if (job.project_id && job.project_id === get().selectedProjectId) {
                void get().refreshClips(job.project_id);
              }
            } else if (
              job.job_type === "track_clip" ||
              job.job_type === "render_preview" ||
              job.job_type === "export_clip" ||
              job.job_type === "export_batch" ||
              job.job_type === "basic_cut"
            ) {
              const pid = job.project_id || get().selectedProjectId;
              if (pid) void get().refreshClips(pid);
            }
          } else if (job.status === "failed" && job.error) {
            get().addToast({
              type: "error",
              title: `Ошибка задачи (${job.job_type})`,
              message: job.error,
            });
          }
        } else if (evt.type === "project_update") {
          void get().refreshProjects();
        } else if (evt.type === "clip_update") {
          const pid = String(data.project_id || "");
          if (pid && pid === get().selectedProjectId) {
            void get().refreshClips(pid);
          }
        }
      });

      wsClient.connect(info.ws_url);

      restartUnsub = await listenBackendRestart(async (nextInfo) => {
        api.setBaseUrl(nextInfo.url);
        set({ backendUrl: nextInfo.url });
        wsClient.disconnect();
        const ok = await waitForHealth(30);
        set({ backendConnected: ok, backendError: nextInfo.error });
        if (ok) {
          wsClient.connect(nextInfo.ws_url);
          await get().refreshAll();
        }
      });

      await get().refreshAll();
    } catch (err) {
      set({
        backendConnected: false,
        backendError: err instanceof Error ? err.message : String(err),
      });
    } finally {
      set({ initializing: false });
    }
  },

  reconnectBackend: async () => {
    set({ initializing: true, backendError: null });
    wsClient.disconnect();
    const info = await restartBackend();
    api.setBaseUrl(info.url);
    set({ backendUrl: info.url });
    const ok = await waitForHealth(30);
    set({
      backendConnected: ok,
      backendError: ok ? null : info.error || "Бэкенд не отвечает после перезапуска.",
      initializing: false,
    });
    if (ok) {
      wsClient.connect(info.ws_url);
      await get().refreshAll();
    }
  },

  refreshAll: async () => {
    const [gpuInfo, encoderProbe, settings] = await Promise.all([
      api.gpu(),
      api.encoders(),
      api.getSettings(),
    ]);
    set({ gpuInfo, encoderProbe, settings });
    await Promise.all([
      get().refreshProjects(),
      get().refreshPresets(),
      get().refreshJobs(),
    ]);
  },

  refreshProjects: async () => {
    try {
      const projects = await api.listProjects();
      const current = get().selectedProjectId;
      const nextSelected =
        current && projects.some((p) => p.id === current)
          ? current
          : projects[0]?.id ?? null;
      set({ projects, selectedProjectId: nextSelected });
      if (nextSelected) {
        await get().refreshClips(nextSelected);
      } else {
        set({ clips: [], selectedClipId: null });
      }
    } catch {
      // Projects router may be initializing
    }
  },

  refreshClips: async (projectId) => {
    try {
      const clips = await api.listClips(projectId);
      const current = get().selectedClipId;
      const nextClip =
        current && clips.some((c) => c.id === current) ? current : clips[0]?.id ?? null;
      set({ clips, selectedClipId: nextClip });
    } catch {
      // ignore if not ready
    }
  },

  refreshPresets: async () => {
    const [subtitlePresets, adPresets, backgrounds] = await Promise.all([
      api.listSubtitlePresets(),
      api.listAdPresets(),
      api.listBackgrounds(),
    ]);
    set({ subtitlePresets, adPresets, backgrounds });
  },

  refreshJobs: async () => {
    const jobs = await api.listJobs();
    set({ jobs });
  },

  saveSettings: async (next) => {
    const saved = await api.updateSettings(next);
    set({ settings: saved });
    get().addToast({ type: "success", title: "Настройки сохранены" });
    return saved;
  },
}));
