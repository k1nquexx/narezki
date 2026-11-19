import { useState } from "react";
import {
  Clock,
  Film,
  FolderOpen,
  Link2,
  Loader2,
  Play,
  Scissors,
  Sparkles,
  Trash2,
  Upload,
  Wand2,
  XCircle,
} from "lucide-react";
import { api } from "../lib/api";
import { isTauriRuntime, pickVideoFile } from "../lib/tauri";
import { useAppStore } from "../store/useAppStore";
import type { Project } from "../types/api";

function formatDuration(sec: number): string {
  const s = Math.max(0, Math.round(sec));
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return `${m}:${rem.toString().padStart(2, "0")}`;
}

export function ProjectsPage() {
  const projects = useAppStore((s) => s.projects);
  const selectedProjectId = useAppStore((s) => s.selectedProjectId);
  const jobs = useAppStore((s) => s.jobs);
  const settings = useAppStore((s) => s.settings);
  const selectProject = useAppStore((s) => s.selectProject);
  const setActiveTab = useAppStore((s) => s.setActiveTab);
  const refreshProjects = useAppStore((s) => s.refreshProjects);
  const refreshJobs = useAppStore((s) => s.refreshJobs);
  const addToast = useAppStore((s) => s.addToast);

  const [localPath, setLocalPath] = useState("");
  const [urlInput, setUrlInput] = useState("");
  const [busyImport, setBusyImport] = useState(false);

  const [cutProject, setCutProject] = useState<Project | null>(null);
  const [cutStart, setCutStart] = useState(0);
  const [cutEnd, setCutEnd] = useState(5);

  const [analyzeProject, setAnalyzeProject] = useState<Project | null>(null);
  const [analyzeMode, setAnalyzeMode] = useState<"auto" | "llm" | "heuristic">("auto");
  const [analyzeMaxHighlights, setAnalyzeMaxHighlights] = useState<number>(
    settings?.llm_max_highlights ?? 10
  );
  const [analyzeMinDur, setAnalyzeMinDur] = useState<number>(15);
  const [analyzeMaxDur, setAnalyzeMaxDur] = useState<number>(60);
  const [analyzePrompt, setAnalyzePrompt] = useState<string>("");

  const handleBrowseLocal = async () => {
    const file = await pickVideoFile();
    if (file) {
      setLocalPath(file);
      await handleImportLocal(file);
    }
  };

  const handleImportLocal = async (overridePath?: string) => {
    const pathToUse = (overridePath ?? localPath).trim();
    if (!pathToUse) {
      addToast({ type: "warning", title: "Укажите путь к видеофайлу" });
      return;
    }
    setBusyImport(true);
    try {
      const { project } = await api.importLocal(pathToUse);
      setLocalPath("");
      await refreshProjects();
      await selectProject(project.id);
      await refreshJobs();
      addToast({ type: "info", title: "Импорт запущен", message: project.title });
    } catch (err) {
      addToast({
        type: "error",
        title: "Ошибка импорта",
        message: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setBusyImport(false);
    }
  };

  const handleImportUrl = async () => {
    const url = urlInput.trim();
    if (!url) {
      addToast({ type: "warning", title: "Вставьте URL видео" });
      return;
    }
    setBusyImport(true);
    try {
      const { project } = await api.importUrl(url);
      setUrlInput("");
      await refreshProjects();
      await selectProject(project.id);
      await refreshJobs();
      addToast({ type: "info", title: "Скачивание поставлено в очередь" });
    } catch (err) {
      addToast({
        type: "error",
        title: "Ошибка импорта URL",
        message: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setBusyImport(false);
    }
  };

  const handleDeleteProject = async (p: Project) => {
    try {
      await api.deleteProject(p.id);
      await refreshProjects();
      addToast({ type: "info", title: `Проект «${p.title}» удалён` });
    } catch (err) {
      addToast({
        type: "error",
        title: "Ошибка удаления",
        message: err instanceof Error ? err.message : String(err),
      });
    }
  };

  const handleBasicCut = async () => {
    if (!cutProject) return;
    try {
      await api.basicCut(cutProject.id, {
        start_time: cutStart,
        end_time: cutEnd,
      });
      await refreshJobs();
      setCutProject(null);
      addToast({ type: "info", title: "Задача нарезки добавлена в очередь" });
    } catch (err) {
      addToast({
        type: "error",
        title: "Ошибка нарезки",
        message: err instanceof Error ? err.message : String(err),
      });
    }
  };

  const handleStartAnalyze = async () => {
    if (!analyzeProject) return;
    try {
      await api.analyzeProject(analyzeProject.id, {
        mode: analyzeMode,
        max_highlights: analyzeMaxHighlights,
        min_duration: analyzeMinDur,
        max_duration: analyzeMaxDur,
        focus_prompt: analyzePrompt,
      });
      await refreshJobs();
      setAnalyzeProject(null);
      addToast({
        type: "info",
        title: "AI-анализ запущен",
        message: "Whisper (CUDA) + SceneDetect + поиск виральных хайлайтов",
      });
    } catch (err) {
      addToast({
        type: "error",
        title: "Не удалось запустить анализ",
        message: err instanceof Error ? err.message : String(err),
      });
    }
  };

  const handleCancelJob = async (jobId: string) => {
    try {
      await api.cancelJob(jobId);
      await refreshJobs();
    } catch (err) {
      addToast({
        type: "error",
        title: "Не удалось отменить задачу",
        message: err instanceof Error ? err.message : String(err),
      });
    }
  };

  const handleClearCompleted = async () => {
    await api.clearCompletedJobs();
    await refreshJobs();
  };

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6">
      {/* Import Header */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="rounded-xl border border-surface-800 bg-surface-900 p-4 space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 text-sm font-semibold text-white">
              <Upload className="w-4 h-4 text-accent-400" />
              <span>Локальное видео (Фильм / Аниме / Стрим)</span>
            </div>
            {isTauriRuntime() && (
              <button
                onClick={handleBrowseLocal}
                disabled={busyImport}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent-600 hover:bg-accent-500 text-xs font-semibold text-white"
              >
                <FolderOpen className="w-3.5 h-3.5" />
                Выбрать файл
              </button>
            )}
          </div>
          <div className="flex gap-2">
            <input
              value={localPath}
              onChange={(e) => setLocalPath(e.target.value)}
              placeholder="Путь к видео (например F:\videos\anime_ep1.mkv)"
              className="flex-1 px-3 py-2 rounded-lg bg-surface-950 border border-surface-700 text-xs text-zinc-100 focus:outline-none focus:border-accent-500"
            />
            <button
              onClick={() => void handleImportLocal()}
              disabled={busyImport || !localPath.trim()}
              className="px-4 py-2 rounded-lg bg-surface-800 hover:bg-surface-700 disabled:opacity-40 border border-surface-700 text-xs font-medium text-zinc-100"
            >
              Импорт
            </button>
          </div>
        </div>

        <div className="rounded-xl border border-surface-800 bg-surface-900 p-4 space-y-3">
          <div className="flex items-center gap-2 text-sm font-semibold text-white">
            <Link2 className="w-4 h-4 text-accent-400" />
            <span>Скачать по ссылке (YouTube / Twitch / TikTok через yt-dlp)</span>
          </div>
          <div className="flex gap-2">
            <input
              value={urlInput}
              onChange={(e) => setUrlInput(e.target.value)}
              placeholder="https://www.youtube.com/watch?v=..."
              className="flex-1 px-3 py-2 rounded-lg bg-surface-950 border border-surface-700 text-xs text-zinc-100 focus:outline-none focus:border-accent-500"
            />
            <button
              onClick={() => void handleImportUrl()}
              disabled={busyImport || !urlInput.trim()}
              className="px-4 py-2 rounded-lg bg-accent-600 hover:bg-accent-500 disabled:opacity-40 text-xs font-semibold text-white"
            >
              Скачать
            </button>
          </div>
        </div>
      </div>

      {/* Main grid: Projects + Queue */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-5">
        <div className="xl:col-span-2 space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-white flex items-center gap-2">
              <Film className="w-4 h-4 text-accent-400" />
              <span>Проекты ({projects.length})</span>
            </h2>
          </div>

          {projects.length === 0 ? (
            <div className="rounded-xl border border-dashed border-surface-700 bg-surface-900/40 p-12 text-center space-y-2">
              <Film className="w-8 h-8 text-zinc-600 mx-auto" />
              <div className="text-sm font-medium text-zinc-300">Нет загруженных видео</div>
              <div className="text-xs text-zinc-500">
                Выберите локальный видеофайл или вставьте URL сверху для старта.
              </div>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3.5">
              {projects.map((p) => {
                const selected = p.id === selectedProjectId;
                const activeJob = jobs.find(
                  (j) =>
                    j.project_id === p.id &&
                    (j.status === "running" || j.status === "queued")
                );
                const statusBadge =
                  p.status === "analyzed"
                    ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/30"
                    : p.status === "ready"
                    ? "bg-sky-500/15 text-sky-300 border-sky-500/30"
                    : p.status === "error"
                    ? "bg-rose-500/15 text-rose-300 border-rose-500/30"
                    : "bg-amber-500/15 text-amber-300 border-amber-500/30";

                return (
                  <div
                    key={p.id}
                    onClick={() => void selectProject(p.id)}
                    className={`rounded-xl border bg-surface-900 overflow-hidden cursor-pointer transition-all ${
                      selected
                        ? "border-accent-500 ring-1 ring-accent-500/40"
                        : "border-surface-800 hover:border-surface-700"
                    }`}
                  >
                    <div className="aspect-video bg-surface-950 relative overflow-hidden">
                      {p.thumbnail_path ? (
                        <img
                          src={api.mediaUrl(p.thumbnail_path)}
                          alt={p.title}
                          className="w-full h-full object-cover"
                        />
                      ) : (
                        <div className="w-full h-full flex items-center justify-center text-zinc-600">
                          {p.status === "importing" ? (
                            <Loader2 className="w-6 h-6 animate-spin text-accent-400" />
                          ) : (
                            <Film className="w-6 h-6" />
                          )}
                        </div>
                      )}
                      <div className="absolute top-2 left-2 flex items-center gap-1.5">
                        <span
                          className={`px-2 py-0.5 rounded text-[10px] font-medium border backdrop-blur ${statusBadge}`}
                        >
                          {p.status}
                        </span>
                      </div>
                      {p.duration > 0 && (
                        <span className="absolute bottom-2 right-2 px-1.5 py-0.5 rounded bg-black/75 text-[11px] font-mono text-zinc-100 inline-flex items-center gap-1">
                          <Clock className="w-3 h-3" />
                          {formatDuration(p.duration)}
                        </span>
                      )}
                    </div>

                    <div className="p-3.5 space-y-3">
                      <div>
                        <div className="text-sm font-semibold text-zinc-100 truncate">
                          {p.title}
                        </div>
                        <div className="text-[11px] text-zinc-400 mt-0.5">
                          {p.width > 0 ? `${p.width}×${p.height} · ${p.fps} fps` : "Обработка..."}
                          {p.clip_count > 0 && ` · Клипов: ${p.clip_count}`}
                        </div>
                        {p.error_message && (
                          <div className="text-[11px] text-rose-400 mt-1 line-clamp-2">
                            {p.error_message}
                          </div>
                        )}
                      </div>

                      {activeJob && (
                        <div className="space-y-1">
                          <div className="flex justify-between text-[10px] text-accent-300">
                            <span className="truncate">{activeJob.message}</span>
                            <span className="tabular-nums">{Math.round(activeJob.progress)}%</span>
                          </div>
                          <div className="h-1 bg-surface-950 rounded-full overflow-hidden">
                            <div
                              className="h-full bg-accent-500 transition-all"
                              style={{ width: `${activeJob.progress}%` }}
                            />
                          </div>
                        </div>
                      )}

                      <div
                        className="flex items-center gap-2 pt-1"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <button
                          onClick={() => {
                            setAnalyzeProject(p);
                            setAnalyzeMaxHighlights(settings?.llm_max_highlights ?? 10);
                          }}
                          disabled={p.status === "importing" || p.status === "analyzing"}
                          className="inline-flex items-center justify-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-emerald-600/90 hover:bg-emerald-500 disabled:opacity-40 text-xs font-medium text-white"
                          title="Найти виральные моменты (Whisper + SceneDetect + LLM)"
                        >
                          <Wand2 className="w-3.5 h-3.5" />
                          AI Анализ
                        </button>
                        <button
                          onClick={async () => {
                            await selectProject(p.id);
                            setActiveTab("studio");
                          }}
                          disabled={p.status === "importing"}
                          className="flex-1 inline-flex items-center justify-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-accent-600 hover:bg-accent-500 disabled:opacity-40 text-xs font-medium text-white"
                        >
                          <Play className="w-3.5 h-3.5" />
                          Студия
                        </button>
                        <button
                          onClick={() => {
                            setCutProject(p);
                            setCutStart(0);
                            setCutEnd(Math.min(15, Math.max(1, Math.round(p.duration || 10))));
                          }}
                          disabled={p.status === "importing"}
                          title="Быстрая резка по таймкоду"
                          className="p-1.5 rounded-lg bg-surface-800 hover:bg-surface-700 border border-surface-700 text-zinc-300"
                        >
                          <Scissors className="w-3.5 h-3.5" />
                        </button>
                        <button
                          onClick={() => void handleDeleteProject(p)}
                          title="Удалить проект"
                          className="p-1.5 rounded-lg bg-surface-800 hover:bg-rose-500/20 border border-surface-700 hover:border-rose-500/40 text-zinc-400 hover:text-rose-300"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Queue column */}
        <div className="rounded-xl border border-surface-800 bg-surface-900 p-4 flex flex-col max-h-[640px]">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-white flex items-center gap-2">
              <Sparkles className="w-4 h-4 text-accent-400" />
              <span>Очередь задач ({jobs.length})</span>
            </h2>
            {jobs.some((j) => ["completed", "failed", "cancelled"].includes(j.status)) && (
              <button
                onClick={() => void handleClearCompleted()}
                className="text-[11px] text-zinc-400 hover:text-zinc-200"
              >
                Очистить завершённые
              </button>
            )}
          </div>

          <div className="flex-1 overflow-y-auto space-y-2.5 pr-1">
            {jobs.length === 0 ? (
              <div className="text-xs text-zinc-500 py-8 text-center">Очередь пуста.</div>
            ) : (
              jobs.map((job) => {
                const isActive = job.status === "running" || job.status === "queued";
                const barColor =
                  job.status === "completed"
                    ? "bg-emerald-500"
                    : job.status === "failed"
                    ? "bg-rose-500"
                    : job.status === "cancelled"
                    ? "bg-zinc-600"
                    : "bg-accent-500";
                return (
                  <div
                    key={job.id}
                    className="p-3 rounded-lg bg-surface-850 border border-surface-800 space-y-1.5"
                  >
                    <div className="flex items-center justify-between text-xs">
                      <span className="font-medium text-zinc-100">{job.job_type}</span>
                      <div className="flex items-center gap-2">
                        <span className="text-zinc-400 tabular-nums">
                          {Math.round(job.progress)}%
                        </span>
                        {isActive && (
                          <button
                            onClick={() => void handleCancelJob(job.id)}
                            className="text-zinc-400 hover:text-rose-400"
                            title="Отменить задачу"
                          >
                            <XCircle className="w-3.5 h-3.5" />
                          </button>
                        )}
                      </div>
                    </div>
                    <div className="h-1.5 bg-surface-950 rounded-full overflow-hidden">
                      <div
                        className={`h-full transition-all ${barColor}`}
                        style={{ width: `${job.progress}%` }}
                      />
                    </div>
                    <div className="text-[11px] text-zinc-400 break-words">
                      {job.error || job.message || job.stage}
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>

      {/* AI Analyze Modal */}
      {analyzeProject && (
        <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="w-full max-w-md rounded-xl border border-surface-700 bg-surface-900 p-5 space-y-4 shadow-2xl text-xs">
            <div>
              <h3 className="text-sm font-semibold text-white">
                AI-анализ: {analyzeProject.title}
              </h3>
              <p className="text-[11px] text-zinc-400 mt-0.5">
                Аудио-пики + PySceneDetect + faster-whisper (GPU) + поиск хайлайтов
              </p>
            </div>

            <label className="block space-y-1">
              <span className="text-zinc-400">Режим поиска хайлайтов</span>
              <select
                value={analyzeMode}
                onChange={(e) =>
                  setAnalyzeMode(e.target.value as "auto" | "llm" | "heuristic")
                }
                className="w-full px-3 py-2 rounded-lg bg-surface-950 border border-surface-700 text-zinc-100"
              >
                <option value="auto">
                  Авто (Cloud LLM если задан ключ, иначе локальный анализатор)
                </option>
                <option value="llm">Только Cloud LLM API</option>
                <option value="heuristic">Локальный мультимодальный (без API ключа)</option>
              </select>
            </label>

            <div className="grid grid-cols-3 gap-3">
              <label className="space-y-1">
                <span className="text-zinc-400">Макс. клипов</span>
                <input
                  type="number"
                  min={1}
                  max={30}
                  value={analyzeMaxHighlights}
                  onChange={(e) => setAnalyzeMaxHighlights(Number(e.target.value) || 8)}
                  className="w-full px-2.5 py-2 rounded-lg bg-surface-950 border border-surface-700 text-zinc-100"
                />
              </label>
              <label className="space-y-1">
                <span className="text-zinc-400">Мин. сек</span>
                <input
                  type="number"
                  min={5}
                  max={90}
                  value={analyzeMinDur}
                  onChange={(e) => setAnalyzeMinDur(Number(e.target.value) || 15)}
                  className="w-full px-2.5 py-2 rounded-lg bg-surface-950 border border-surface-700 text-zinc-100"
                />
              </label>
              <label className="space-y-1">
                <span className="text-zinc-400">Макс. сек</span>
                <input
                  type="number"
                  min={15}
                  max={180}
                  value={analyzeMaxDur}
                  onChange={(e) => setAnalyzeMaxDur(Number(e.target.value) || 60)}
                  className="w-full px-2.5 py-2 rounded-lg bg-surface-950 border border-surface-700 text-zinc-100"
                />
              </label>
            </div>

            <label className="block space-y-1">
              <span className="text-zinc-400">Пожелания для LLM (необязательно)</span>
              <input
                value={analyzePrompt}
                onChange={(e) => setAnalyzePrompt(e.target.value)}
                placeholder="Например: ищи драки в аниме или смешные диалоги..."
                className="w-full px-3 py-2 rounded-lg bg-surface-950 border border-surface-700 text-zinc-100"
              />
            </label>

            <div className="flex justify-end gap-2 pt-1">
              <button
                onClick={() => setAnalyzeProject(null)}
                className="px-3 py-1.5 rounded-lg bg-surface-800 text-zinc-300"
              >
                Отмена
              </button>
              <button
                onClick={() => void handleStartAnalyze()}
                className="px-4 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 font-semibold text-white"
              >
                Запустить анализ
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Quick Basic Cut Modal */}
      {cutProject && (
        <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="w-full max-w-sm rounded-xl border border-surface-700 bg-surface-900 p-5 space-y-4 shadow-2xl">
            <h3 className="text-sm font-semibold text-white">
              Быстрая нарезка: {cutProject.title}
            </h3>
            <div className="grid grid-cols-2 gap-3 text-xs">
              <label className="space-y-1">
                <span className="text-zinc-400">Начало (сек)</span>
                <input
                  type="number"
                  step="0.5"
                  min={0}
                  max={cutProject.duration}
                  value={cutStart}
                  onChange={(e) => setCutStart(Number(e.target.value))}
                  className="w-full px-3 py-2 rounded-lg bg-surface-950 border border-surface-700 text-zinc-100"
                />
              </label>
              <label className="space-y-1">
                <span className="text-zinc-400">Конец (сек)</span>
                <input
                  type="number"
                  step="0.5"
                  min={0.5}
                  max={cutProject.duration}
                  value={cutEnd}
                  onChange={(e) => setCutEnd(Number(e.target.value))}
                  className="w-full px-3 py-2 rounded-lg bg-surface-950 border border-surface-700 text-zinc-100"
                />
              </label>
            </div>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setCutProject(null)}
                className="px-3 py-1.5 rounded-lg bg-surface-800 text-xs text-zinc-300"
              >
                Отмена
              </button>
              <button
                onClick={() => void handleBasicCut()}
                className="px-4 py-1.5 rounded-lg bg-accent-600 hover:bg-accent-500 text-xs font-semibold text-white"
              >
                Нарезать
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
