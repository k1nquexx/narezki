import { useEffect } from "react";
import { AlertTriangle, Loader2, RefreshCw } from "lucide-react";
import { Layout } from "./components/Layout";
import { ToastContainer } from "./components/ToastContainer";
import { ClipStudioPage } from "./pages/ClipStudioPage";
import { LiveMonitorPage } from "./pages/LiveMonitorPage";
import { PresetsPage } from "./pages/PresetsPage";
import { ProjectsPage } from "./pages/ProjectsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { useAppStore } from "./store/useAppStore";

export function App() {
  const activeTab = useAppStore((s) => s.activeTab);
  const initializing = useAppStore((s) => s.initializing);
  const backendConnected = useAppStore((s) => s.backendConnected);
  const backendError = useAppStore((s) => s.backendError);
  const bootstrap = useAppStore((s) => s.bootstrap);
  const reconnectBackend = useAppStore((s) => s.reconnectBackend);

  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);

  return (
    <Layout>
      {initializing ? (
        <div className="flex-1 flex flex-col items-center justify-center gap-3 text-zinc-300">
          <Loader2 className="w-8 h-8 text-accent-400 animate-spin" />
          <div className="text-sm font-medium">Запуск Python AI-движка и проверка GPU...</div>
        </div>
      ) : !backendConnected ? (
        <div className="flex-1 flex flex-col items-center justify-center p-6 text-center max-w-lg mx-auto gap-4">
          <AlertTriangle className="w-10 h-10 text-rose-400" />
          <div className="space-y-1">
            <h2 className="text-base font-semibold text-white">Бэкенд не подключён</h2>
            <p className="text-xs text-zinc-400 break-words">{backendError}</p>
          </div>
          <button
            onClick={() => void reconnectBackend()}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-accent-600 hover:bg-accent-500 text-xs font-semibold text-white"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            Повторить запуск
          </button>
        </div>
      ) : (
        <>
          {activeTab === "projects" && <ProjectsPage />}
          {activeTab === "live" && <LiveMonitorPage />}
          {activeTab === "studio" && <ClipStudioPage />}
          {activeTab === "presets" && <PresetsPage />}
          {activeTab === "settings" && <SettingsPage />}
        </>
      )}
      <ToastContainer />
    </Layout>
  );
}

export default App;
