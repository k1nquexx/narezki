import { type ReactNode } from "react";
import {
  Cpu,
  Film,
  FolderKanban,
  Megaphone,
  RefreshCw,
  Scissors,
  Settings,
  Terminal,
  Trash2,
} from "lucide-react";
import { api } from "../lib/api";
import { useAppStore } from "../store/useAppStore";
import type { TabId } from "../types/api";
import { LogDrawer } from "./LogDrawer";

const NAV_ITEMS: Array<{ id: TabId; label: string; icon: typeof FolderKanban }> = [
  { id: "projects", label: "Проекты и очередь", icon: FolderKanban },
  { id: "studio", label: "Студия клипов", icon: Scissors },
  { id: "presets", label: "Реклама и стили", icon: Megaphone },
  { id: "settings", label: "Настройки", icon: Settings },
];

export function Layout({ children }: { children: ReactNode }) {
  const activeTab = useAppStore((s) => s.activeTab);
  const setActiveTab = useAppStore((s) => s.setActiveTab);
  const gpuInfo = useAppStore((s) => s.gpuInfo);
  const encoderProbe = useAppStore((s) => s.encoderProbe);
  const wsConnected = useAppStore((s) => s.wsConnected);
  const backendConnected = useAppStore((s) => s.backendConnected);
  const jobs = useAppStore((s) => s.jobs);
  const logDrawerOpen = useAppStore((s) => s.logDrawerOpen);
  const setLogDrawerOpen = useAppStore((s) => s.setLogDrawerOpen);
  const reconnectBackend = useAppStore((s) => s.reconnectBackend);
  const addToast = useAppStore((s) => s.addToast);

  const activeJobsCount = jobs.filter(
    (j) => j.status === "running" || j.status === "queued"
  ).length;

  const vramPct =
    gpuInfo && gpuInfo.vram_total_mb > 0
      ? Math.min(100, Math.round((gpuInfo.vram_used_mb / gpuInfo.vram_total_mb) * 100))
      : 0;

  const handleReleaseVram = async () => {
    try {
      const next = await api.releaseVram();
      useAppStore.setState({ gpuInfo: next });
      addToast({
        type: "info",
        title: "VRAM очищена",
        message: `${next.vram_used_mb} / ${next.vram_total_mb} MB занято`,
      });
    } catch (err) {
      addToast({
        type: "error",
        title: "Ошибка очистки VRAM",
        message: err instanceof Error ? err.message : String(err),
      });
    }
  };

  return (
    <div className="h-screen w-screen flex flex-col bg-surface-950 text-zinc-100 select-none">
      <header className="h-12 border-b border-surface-800 bg-surface-900 px-4 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-6">
          <div className="flex items-center gap-2.5">
            <div className="w-7 h-7 rounded-lg bg-accent-600 flex items-center justify-center shadow-lg shadow-accent-600/20">
              <Film className="w-4 h-4 text-white" />
            </div>
            <div>
              <span className="font-semibold tracking-tight text-sm text-white">Narezki</span>
              <span className="ml-2 text-[11px] text-zinc-400 hidden sm:inline">
                AI Shorts & Reels Studio
              </span>
            </div>
          </div>

          <nav className="flex items-center gap-1">
            {NAV_ITEMS.map((item) => {
              const Icon = item.icon;
              const active = activeTab === item.id;
              return (
                <button
                  key={item.id}
                  onClick={() => setActiveTab(item.id)}
                  className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                    active
                      ? "bg-accent-600 text-white shadow-sm"
                      : "text-zinc-400 hover:text-zinc-100 hover:bg-surface-800"
                  }`}
                >
                  <Icon className="w-3.5 h-3.5" />
                  <span>{item.label}</span>
                  {item.id === "projects" && activeJobsCount > 0 && (
                    <span className="px-1.5 py-0.2 rounded-full text-[10px] bg-amber-500/20 text-amber-300 border border-amber-500/30">
                      {activeJobsCount}
                    </span>
                  )}
                </button>
              );
            })}
          </nav>
        </div>

        <div className="flex items-center gap-3 text-xs">
          {encoderProbe && (
            <span className="hidden md:inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-surface-800 border border-surface-700 text-zinc-300">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
              {encoderProbe.selected}
            </span>
          )}

          {gpuInfo && (
            <div className="flex items-center gap-2 px-2.5 py-1 rounded-md bg-surface-800 border border-surface-700">
              <Cpu className="w-3.5 h-3.5 text-accent-400" />
              <span className="text-zinc-300 hidden lg:inline truncate max-w-[140px]">
                {gpuInfo.device_name}
              </span>
              <div className="w-16 h-1.5 bg-surface-950 rounded-full overflow-hidden">
                <div
                  className={`h-full transition-all ${
                    vramPct > 85
                      ? "bg-rose-500"
                      : vramPct > 65
                      ? "bg-amber-400"
                      : "bg-emerald-400"
                  }`}
                  style={{ width: `${vramPct}%` }}
                />
              </div>
              <span className="text-zinc-400 tabular-nums">
                {gpuInfo.vram_used_mb}/{gpuInfo.vram_total_mb} MB
              </span>
              <button
                onClick={handleReleaseVram}
                title="Очистить кэш VRAM"
                className="text-zinc-400 hover:text-zinc-100 transition-colors"
              >
                <Trash2 className="w-3 h-3" />
              </button>
            </div>
          )}

          <button
            onClick={() => void reconnectBackend()}
            title="Перезапустить локальный сервер Python"
            className="p-1.5 rounded-md bg-surface-800 border border-surface-700 text-zinc-400 hover:text-zinc-100 hover:bg-surface-700"
          >
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
        </div>
      </header>

      <main className="flex-1 min-h-0 overflow-hidden flex flex-col">{children}</main>

      <LogDrawer />

      <footer className="h-7 border-t border-surface-800 bg-surface-900 px-4 flex items-center justify-between text-[11px] text-zinc-400 shrink-0">
        <div className="flex items-center gap-4">
          <span className="inline-flex items-center gap-1.5">
            <span
              className={`w-2 h-2 rounded-full ${
                backendConnected && wsConnected
                  ? "bg-emerald-400"
                  : backendConnected
                  ? "bg-amber-400"
                  : "bg-rose-500"
              }`}
            />
            {backendConnected && wsConnected
              ? "FastAPI + WebSocket подключены"
              : backendConnected
              ? "FastAPI подключен (WS переподключение...)"
              : "Бэкенд отключён"}
          </span>
          <span>Активных задач: {activeJobsCount}</span>
        </div>

        <button
          onClick={() => setLogDrawerOpen(!logDrawerOpen)}
          className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded hover:bg-surface-800 transition-colors ${
            logDrawerOpen ? "text-accent-400" : "text-zinc-400 hover:text-zinc-200"
          }`}
        >
          <Terminal className="w-3 h-3" />
          <span>{logDrawerOpen ? "Скрыть логи" : "Открыть логи"}</span>
        </button>
      </footer>
    </div>
  );
}
