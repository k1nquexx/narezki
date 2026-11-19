import { useEffect, useMemo, useState } from "react";
import {
  CheckSquare,
  Crosshair,
  Download,
  FolderOpen,
  Megaphone,
  Play,
  Plus,
  Save,
  Shield,
  Sparkles,
  Square,
  Subtitles,
  Trash2,
  Wand2,
} from "lucide-react";
import { ClipTimelineEditor } from "../components/ClipTimelineEditor";
import { SubtitleCueEditor } from "../components/SubtitleCueEditor";
import { VideoPreview916 } from "../components/VideoPreview916";
import { api } from "../lib/api";
import { openInExplorer } from "../lib/tauri";
import { useAppStore } from "../store/useAppStore";
import type { AntiCidConfig, Clip } from "../types/api";

type InspectorTab = "crop" | "subs" | "ad" | "cid";

const DEFAULT_CID: AntiCidConfig = {
  zoom: 1.03,
  speed: 1.015,
  brightness: 0.01,
  contrast: 1.02,
  saturation: 1.03,
  strip_metadata: true,
};

export function ClipStudioPage() {
  const projects = useAppStore((s) => s.projects);
  const selectedProjectId = useAppStore((s) => s.selectedProjectId);
  const clips = useAppStore((s) => s.clips);
  const selectedClipId = useAppStore((s) => s.selectedClipId);
  const subtitlePresets = useAppStore((s) => s.subtitlePresets);
  const adPresets = useAppStore((s) => s.adPresets);
  const backgrounds = useAppStore((s) => s.backgrounds);
  const settings = useAppStore((s) => s.settings);

  const selectProject = useAppStore((s) => s.selectProject);
  const selectClip = useAppStore((s) => s.selectClip);
  const refreshClips = useAppStore((s) => s.refreshClips);
  const refreshJobs = useAppStore((s) => s.refreshJobs);
  const addToast = useAppStore((s) => s.addToast);

  const [inspectorTab, setInspectorTab] = useState<InspectorTab>("crop");
  const [draft, setDraft] = useState<Clip | null>(null);
  const [saving, setSaving] = useState(false);
  const [resyncing, setResyncing] = useState(false);

  const project = useMemo(
    () => projects.find((p) => p.id === selectedProjectId) ?? null,
    [projects, selectedProjectId]
  );

  const currentClip = useMemo(
    () => clips.find((c) => c.id === selectedClipId) ?? clips[0] ?? null,
    [clips, selectedClipId]
  );

  useEffect(() => {
    setDraft(currentClip ? structuredClone(currentClip) : null);
  }, [currentClip]);

  const activeSubPreset = useMemo(
    () =>
      subtitlePresets.find((s) => s.id === draft?.subtitle_preset_id) ??
      subtitlePresets.find((s) => s.is_default) ??
      subtitlePresets[0] ??
      null,
    [subtitlePresets, draft?.subtitle_preset_id]
  );

  const activeAdPreset = useMemo(
    () => adPresets.find((a) => a.id === draft?.ad_preset_id) ?? null,
    [adPresets, draft?.ad_preset_id]
  );

  const activeBg = useMemo(
    () => backgrounds.find((b) => b.id === draft?.background_asset_id) ?? null,
    [backgrounds, draft?.background_asset_id]
  );

  const selectedExportCount = clips.filter((c) => c.selected_for_export).length;

  const saveDraft = async (override?: Clip): Promise<Clip | null> => {
    const target = override ?? draft;
    if (!target) return null;
    setSaving(true);
    try {
      const updated = await api.updateClip(target.id, {
        title: target.title,
        hook_text: target.hook_text,
        hook_enabled: target.hook_enabled,
        description: target.description,
        tags_json: target.tags_json,
        start_time: target.start_time,
        end_time: target.end_time,
        crop_mode: target.crop_mode,
        background_asset_id: target.background_asset_id,
        subtitle_preset_id: target.subtitle_preset_id,
        subtitles_enabled: target.subtitles_enabled,
        subtitles_json: target.subtitles_json,
        ad_preset_id: target.ad_preset_id,
        ad_override_json: target.ad_override_json,
        anti_cid_enabled: target.anti_cid_enabled,
        anti_cid_json: target.anti_cid_json,
        selected_for_export: target.selected_for_export,
      });
      await refreshClips(target.project_id);
      return updated;
    } catch (err) {
      addToast({
        type: "error",
        title: "Ошибка сохранения клипа",
        message: err instanceof Error ? err.message : String(err),
      });
      return null;
    } finally {
      setSaving(false);
    }
  };

  const handleCreateManualClip = async () => {
    if (!project) return;
    const dur = Math.max(5, project.duration || 30);
    try {
      const created = await api.createClip({
        project_id: project.id,
        title: `Ручной клип #${clips.length + 1}`,
        start_time: 0,
        end_time: Math.min(20, dur),
        crop_mode: "auto_track",
        category: "manual",
      });
      await refreshClips(project.id);
      selectClip(created.id);
    } catch (err) {
      addToast({
        type: "error",
        title: "Ошибка создания клипа",
        message: err instanceof Error ? err.message : String(err),
      });
    }
  };

  const handleDeleteClip = async (clipId: string) => {
    if (!project) return;
    await api.deleteClip(clipId);
    await refreshClips(project.id);
  };

  const handleToggleSelectExport = async (c: Clip) => {
    await api.updateClip(c.id, { selected_for_export: !c.selected_for_export });
    if (project) await refreshClips(project.id);
  };

  const handleTrackClip = async () => {
    if (!draft) return;
    const saved = await saveDraft();
    if (!saved) return;
    await api.trackClip(saved.id);
    await refreshJobs();
    addToast({ type: "info", title: "YOLOv8 трекинг поставлен в очередь" });
  };

  const handleRenderPreview = async () => {
    if (!draft) return;
    const saved = await saveDraft();
    if (!saved) return;
    await api.renderPreview(saved.id);
    await refreshJobs();
    addToast({ type: "info", title: "Рендер превью 9:16 запущен" });
  };

  const handleExportSingle = async () => {
    if (!draft) return;
    const saved = await saveDraft();
    if (!saved) return;
    await api.exportClip(saved.id);
    await refreshJobs();
    addToast({ type: "info", title: "Экспорт клипа (.mp4 + .txt) поставлен в очередь" });
  };

  const handleBatchExport = async () => {
    if (!project) return;
    await saveDraft();
    try {
      await api.exportBatch(project.id);
      await refreshJobs();
      addToast({
        type: "info",
        title: `Батч-экспорт (${selectedExportCount} клипов) запущен`,
      });
    } catch (err) {
      addToast({
        type: "error",
        title: "Ошибка батч-экспорта",
        message: err instanceof Error ? err.message : String(err),
      });
    }
  };

  const handleResyncSubtitles = async () => {
    if (!draft) return;
    setResyncing(true);
    try {
      await saveDraft();
      const res = await api.resyncSubtitles(draft.id);
      setDraft(res.clip);
      await refreshClips(draft.project_id);
      addToast({ type: "success", title: "Субтитры синхронизированы с транскриптом" });
    } catch (err) {
      addToast({
        type: "error",
        title: "Ошибка синхронизации",
        message: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setResyncing(false);
    }
  };

  const handleOpenExportsFolder = async () => {
    const dir = settings?.export_dir;
    if (dir) {
      await openInExplorer(dir);
    }
  };

  if (!project) {
    return (
      <div className="flex-1 flex items-center justify-center p-8 text-center">
        <div className="space-y-2 max-w-md">
          <Sparkles className="w-8 h-8 text-accent-400 mx-auto" />
          <div className="text-base font-semibold text-white">Нет выбранного проекта</div>
          <div className="text-xs text-zinc-400">
            Перейдите во вкладку «Проекты и очередь» и импортируйте видео.
          </div>
        </div>
      </div>
    );
  }

  const clipDuration = draft ? Math.max(0.5, draft.end_time - draft.start_time) : 15;
  const cidCfg: AntiCidConfig = draft?.anti_cid_json ?? DEFAULT_CID;

  return (
    <div className="flex-1 min-h-0 flex flex-col">
      {/* Studio Top Bar */}
      <div className="h-12 border-b border-surface-800 bg-surface-900/70 px-4 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-3">
          <select
            value={project.id}
            onChange={(e) => void selectProject(e.target.value)}
            className="px-2.5 py-1.5 rounded-lg bg-surface-950 border border-surface-700 text-xs text-zinc-100 max-w-[260px]"
          >
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.title} ({p.clip_count})
              </option>
            ))}
          </select>

          <button
            onClick={async () => {
              try {
                await api.analyzeProject(project.id, { mode: "auto" });
                await refreshJobs();
                addToast({ type: "info", title: "AI-анализ поставлен в очередь" });
              } catch (err) {
                addToast({
                  type: "error",
                  title: "Не удалось запустить анализ",
                  message: err instanceof Error ? err.message : String(err),
                });
              }
            }}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-emerald-600/90 hover:bg-emerald-500 text-xs font-medium text-white"
          >
            <Wand2 className="w-3.5 h-3.5" />
            Найти хайлайты
          </button>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={handleBatchExport}
            disabled={selectedExportCount === 0}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent-600 hover:bg-accent-500 disabled:opacity-40 text-xs font-semibold text-white"
          >
            <Download className="w-3.5 h-3.5" />
            Батч-экспорт ({selectedExportCount})
          </button>
          <button
            onClick={handleOpenExportsFolder}
            title="Открыть папку экспорта"
            className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-surface-800 hover:bg-surface-700 border border-surface-700 text-xs text-zinc-200"
          >
            <FolderOpen className="w-3.5 h-3.5" />
            Папка
          </button>
        </div>
      </div>

      {/* 3-Column Studio Body */}
      <div className="flex-1 min-h-0 flex">
        {/* Left: Clips list */}
        <aside className="w-72 border-r border-surface-800 bg-surface-900/50 flex flex-col shrink-0">
          <div className="p-3 border-b border-surface-800 flex items-center justify-between">
            <span className="text-xs font-semibold text-zinc-200">
              Клипы ({clips.length})
            </span>
            <button
              onClick={handleCreateManualClip}
              className="inline-flex items-center gap-1 px-2 py-1 rounded bg-surface-800 hover:bg-surface-700 border border-surface-700 text-[11px] text-zinc-200"
            >
              <Plus className="w-3 h-3" />
              Новый
            </button>
          </div>

          <div className="flex-1 overflow-y-auto p-2 space-y-2">
            {clips.length === 0 ? (
              <div className="text-xs text-zinc-500 p-4 text-center">
                Запустите AI-анализ или создайте клип вручную.
              </div>
            ) : (
              clips.map((c) => {
                const active = c.id === draft?.id;
                const catColor =
                  c.category === "action"
                    ? "text-rose-300 bg-rose-500/15"
                    : c.category === "hybrid"
                    ? "text-amber-300 bg-amber-500/15"
                    : c.category === "manual"
                    ? "text-zinc-300 bg-zinc-500/15"
                    : "text-sky-300 bg-sky-500/15";

                return (
                  <div
                    key={c.id}
                    onClick={() => selectClip(c.id)}
                    className={`p-2.5 rounded-xl border cursor-pointer transition-colors space-y-1.5 ${
                      active
                        ? "border-accent-500 bg-accent-500/10"
                        : "border-surface-800 bg-surface-900 hover:border-surface-700"
                    }`}
                  >
                    <div className="flex items-start gap-2">
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          void handleToggleSelectExport(c);
                        }}
                        className="mt-0.5 text-zinc-400 hover:text-accent-400"
                      >
                        {c.selected_for_export ? (
                          <CheckSquare className="w-4 h-4 text-accent-400" />
                        ) : (
                          <Square className="w-4 h-4" />
                        )}
                      </button>
                      <div className="flex-1 min-w-0">
                        <div className="text-xs font-medium text-zinc-100 truncate">
                          {c.title}
                        </div>
                        <div className="flex items-center gap-1.5 mt-1 text-[10px]">
                          <span className={`px-1.5 py-0.2 rounded ${catColor}`}>
                            {c.category}
                          </span>
                          <span className="text-emerald-400 font-semibold">
                            {Math.round(c.virality_score)}/100
                          </span>
                          <span className="text-zinc-400 font-mono">
                            {(c.end_time - c.start_time).toFixed(1)}s
                          </span>
                        </div>
                      </div>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          void handleDeleteClip(c.id);
                        }}
                        className="text-zinc-500 hover:text-rose-400"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                    <div className="text-[10px] text-zinc-500 flex justify-between px-6">
                      <span>{c.crop_mode}</span>
                      <span>{c.status}</span>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </aside>

        {/* Center: 9:16 Preview + Timeline */}
        <section className="flex-1 min-w-0 overflow-y-auto p-4 flex flex-col items-center justify-between gap-4 bg-surface-950/60">
          {draft ? (
            <>
              <VideoPreview916
                project={project}
                clip={draft}
                subtitlePreset={activeSubPreset}
                adPreset={activeAdPreset}
                backgroundAsset={activeBg}
              />
              <div className="w-full max-w-2xl">
                <ClipTimelineEditor
                  project={project}
                  clip={draft}
                  adPreset={activeAdPreset}
                  onChangeRange={(start, end) =>
                    setDraft({ ...draft, start_time: start, end_time: end })
                  }
                />
              </div>
            </>
          ) : (
            <div className="m-auto text-xs text-zinc-500">Выберите клип слева</div>
          )}
        </section>

        {/* Right: Inspector */}
        {draft && (
          <aside className="w-[390px] border-l border-surface-800 bg-surface-900 flex flex-col shrink-0">
            <div className="grid grid-cols-4 border-b border-surface-800 text-[11px] font-medium">
              {(
                [
                  { id: "crop", label: "Кроп", icon: Crosshair },
                  { id: "subs", label: "Титры", icon: Subtitles },
                  { id: "ad", label: "Реклама", icon: Megaphone },
                  { id: "cid", label: "Уник.", icon: Shield },
                ] as const
              ).map((t) => {
                const Icon = t.icon;
                const active = inspectorTab === t.id;
                return (
                  <button
                    key={t.id}
                    onClick={() => setInspectorTab(t.id)}
                    className={`py-2.5 flex items-center justify-center gap-1 border-b-2 transition-colors ${
                      active
                        ? "border-accent-500 text-white bg-surface-850"
                        : "border-transparent text-zinc-400 hover:text-zinc-200"
                    }`}
                  >
                    <Icon className="w-3.5 h-3.5" />
                    <span>{t.label}</span>
                  </button>
                );
              })}
            </div>

            <div className="flex-1 overflow-y-auto p-4 space-y-4 text-xs">
              {inspectorTab === "crop" && (
                <>
                  <div className="space-y-2">
                    <span className="text-zinc-400 font-medium">Режим кадрирования (9:16)</span>
                    <div className="grid grid-cols-1 gap-2">
                      {(
                        [
                          {
                            id: "auto_track",
                            title: "Auto-Track (YOLOv8)",
                            desc: "Плавное слежение за персонажем с Kalman-сглаживанием",
                          },
                          {
                            id: "cinema_blur",
                            title: "Cinema / Anime Full + Blur",
                            desc: "Полный кадр 16:9 по центру на размытом фоне (для экшена)",
                          },
                          {
                            id: "split_screen",
                            title: "Split Screen",
                            desc: "Верх — основной ролик, низ — залипательный фон",
                          },
                        ] as const
                      ).map((opt) => (
                        <button
                          key={opt.id}
                          onClick={() => setDraft({ ...draft, crop_mode: opt.id })}
                          className={`text-left p-3 rounded-lg border transition-colors ${
                            draft.crop_mode === opt.id
                              ? "border-accent-500 bg-accent-500/10"
                              : "border-surface-800 bg-surface-950 hover:border-surface-700"
                          }`}
                        >
                          <div className="font-semibold text-zinc-100">{opt.title}</div>
                          <div className="text-[11px] text-zinc-400 mt-0.5">{opt.desc}</div>
                        </button>
                      ))}
                    </div>
                  </div>

                  {draft.crop_mode === "auto_track" && (
                    <div className="p-3 rounded-lg bg-surface-950 border border-surface-800 space-y-2">
                      <div className="flex items-center justify-between">
                        <span className="text-zinc-300">Траектория камеры</span>
                        <span className="text-[11px] text-zinc-400">
                          {draft.track_data_json?.points?.length
                            ? `${draft.track_data_json.points.length} точек`
                            : "Не рассчитано"}
                        </span>
                      </div>
                      <button
                        onClick={handleTrackClip}
                        className="w-full py-2 rounded-lg bg-surface-800 hover:bg-surface-700 border border-surface-700 font-medium text-zinc-100"
                      >
                        Рассчитать / обновить YOLOv8 трекинг
                      </button>
                    </div>
                  )}

                  {draft.crop_mode === "split_screen" && (
                    <label className="block space-y-1.5">
                      <span className="text-zinc-400">Фоновый ролик (нижняя половина)</span>
                      <select
                        value={draft.background_asset_id ?? ""}
                        onChange={(e) =>
                          setDraft({
                            ...draft,
                            background_asset_id: e.target.value || null,
                          })
                        }
                        className="w-full px-3 py-2 rounded-lg bg-surface-950 border border-surface-700 text-zinc-100"
                      >
                        <option value="">— Выберите фон —</option>
                        {backgrounds.map((b) => (
                          <option key={b.id} value={b.id}>
                            {b.name} ({b.duration.toFixed(0)}с)
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                </>
              )}

              {inspectorTab === "subs" && (
                <>
                  <div className="p-3 rounded-lg bg-surface-950 border border-surface-800 space-y-2.5">
                    <label className="flex items-center justify-between">
                      <span className="font-medium text-zinc-200">Хук (первые 3 секунды)</span>
                      <input
                        type="checkbox"
                        checked={draft.hook_enabled}
                        onChange={(e) =>
                          setDraft({ ...draft, hook_enabled: e.target.checked })
                        }
                        className="accent-accent-500"
                      />
                    </label>
                    <input
                      value={draft.hook_text}
                      onChange={(e) => setDraft({ ...draft, hook_text: e.target.value })}
                      placeholder="Текст хука сверху..."
                      className="w-full px-2.5 py-1.5 rounded bg-surface-900 border border-surface-700 text-zinc-100"
                    />
                  </div>

                  <div className="p-3 rounded-lg bg-surface-950 border border-surface-800 space-y-2.5">
                    <label className="flex items-center justify-between">
                      <span className="font-medium text-zinc-200">Динамические субтитры</span>
                      <input
                        type="checkbox"
                        checked={draft.subtitles_enabled}
                        onChange={(e) =>
                          setDraft({ ...draft, subtitles_enabled: e.target.checked })
                        }
                        className="accent-accent-500"
                      />
                    </label>
                    <select
                      value={draft.subtitle_preset_id ?? ""}
                      onChange={(e) =>
                        setDraft({
                          ...draft,
                          subtitle_preset_id: e.target.value || null,
                        })
                      }
                      className="w-full px-2.5 py-1.5 rounded bg-surface-900 border border-surface-700 text-zinc-100"
                    >
                      {subtitlePresets.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name} ({p.karaoke_mode})
                        </option>
                      ))}
                    </select>
                  </div>

                  <SubtitleCueEditor
                    cues={draft.subtitles_json}
                    clipDuration={clipDuration}
                    onChange={(nextCues) => setDraft({ ...draft, subtitles_json: nextCues })}
                    onResync={() => void handleResyncSubtitles()}
                    resyncing={resyncing}
                  />
                </>
              )}

              {inspectorTab === "ad" && (
                <>
                  <label className="block space-y-1.5">
                    <span className="text-zinc-400">Рекламный пресет</span>
                    <select
                      value={draft.ad_preset_id ?? ""}
                      onChange={(e) =>
                        setDraft({
                          ...draft,
                          ad_preset_id: e.target.value || null,
                          ad_override_json: null,
                        })
                      }
                      className="w-full px-3 py-2 rounded-lg bg-surface-950 border border-surface-700 text-zinc-100"
                    >
                      <option value="">Без рекламы</option>
                      {adPresets.map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.name} ({a.ad_type === "video_insert" ? "Видеовклейка" : "Оверлей"})
                        </option>
                      ))}
                    </select>
                  </label>

                  {activeAdPreset && activeAdPreset.ad_type === "video_insert" && (
                    <div className="p-3 rounded-lg bg-surface-950 border border-surface-800 space-y-3">
                      <label className="block space-y-1">
                        <span className="text-zinc-400">Позиция вклейки</span>
                        <select
                          value={
                            draft.ad_override_json?.insert_position ??
                            activeAdPreset.insert_position
                          }
                          onChange={(e) =>
                            setDraft({
                              ...draft,
                              ad_override_json: {
                                ...(draft.ad_override_json ?? {}),
                                insert_position: e.target.value as "mid_roll" | "outro",
                              },
                            })
                          }
                          className="w-full px-2.5 py-1.5 rounded bg-surface-900 border border-surface-700 text-zinc-100"
                        >
                          <option value="mid_roll">Mid-roll (внутрь клипа)</option>
                          <option value="outro">Outro (в конец клипа)</option>
                        </select>
                      </label>

                      {(draft.ad_override_json?.insert_position ??
                        activeAdPreset.insert_position) === "mid_roll" && (
                        <label className="block space-y-1">
                          <span className="text-zinc-400">
                            Точка вставки (сек от начала клипа):{" "}
                            {(
                              draft.ad_override_json?.insert_at_sec ??
                              activeAdPreset.insert_at_sec ??
                              clipDuration * 0.5
                            ).toFixed(1)}
                            с
                          </span>
                          <input
                            type="range"
                            min={0.5}
                            max={Math.max(1.0, clipDuration - 0.5)}
                            step={0.5}
                            value={
                              draft.ad_override_json?.insert_at_sec ??
                              activeAdPreset.insert_at_sec ??
                              clipDuration * 0.5
                            }
                            onChange={(e) =>
                              setDraft({
                                ...draft,
                                ad_override_json: {
                                  ...(draft.ad_override_json ?? {}),
                                  insert_at_sec: Number(e.target.value),
                                },
                              })
                            }
                            className="w-full accent-amber-400"
                          />
                        </label>
                      )}
                    </div>
                  )}

                  {activeAdPreset && activeAdPreset.ad_type === "overlay" && (
                    <div className="p-3 rounded-lg bg-surface-950 border border-surface-800 space-y-2.5">
                      <div className="grid grid-cols-2 gap-2">
                        <label className="space-y-1">
                          <span className="text-zinc-400">
                            X ({draft.ad_override_json?.overlay_x ?? activeAdPreset.overlay_x}%)
                          </span>
                          <input
                            type="range"
                            min={5}
                            max={95}
                            value={draft.ad_override_json?.overlay_x ?? activeAdPreset.overlay_x}
                            onChange={(e) =>
                              setDraft({
                                ...draft,
                                ad_override_json: {
                                  ...(draft.ad_override_json ?? {}),
                                  overlay_x: Number(e.target.value),
                                },
                              })
                            }
                            className="w-full accent-accent-500"
                          />
                        </label>
                        <label className="space-y-1">
                          <span className="text-zinc-400">
                            Y ({draft.ad_override_json?.overlay_y ?? activeAdPreset.overlay_y}%)
                          </span>
                          <input
                            type="range"
                            min={5}
                            max={95}
                            value={draft.ad_override_json?.overlay_y ?? activeAdPreset.overlay_y}
                            onChange={(e) =>
                              setDraft({
                                ...draft,
                                ad_override_json: {
                                  ...(draft.ad_override_json ?? {}),
                                  overlay_y: Number(e.target.value),
                                },
                              })
                            }
                            className="w-full accent-accent-500"
                          />
                        </label>
                      </div>

                      <label className="block space-y-1">
                        <span className="text-zinc-400">
                          Масштаб ({draft.ad_override_json?.overlay_scale ?? activeAdPreset.overlay_scale}%)
                        </span>
                        <input
                          type="range"
                          min={10}
                          max={90}
                          value={
                            draft.ad_override_json?.overlay_scale ?? activeAdPreset.overlay_scale
                          }
                          onChange={(e) =>
                            setDraft({
                              ...draft,
                              ad_override_json: {
                                ...(draft.ad_override_json ?? {}),
                                overlay_scale: Number(e.target.value),
                              },
                            })
                          }
                          className="w-full accent-accent-500"
                        />
                      </label>

                      <div className="grid grid-cols-3 gap-2">
                        <label className="space-y-1">
                          <span className="text-zinc-400">Старт (с)</span>
                          <input
                            type="number"
                            step="0.5"
                            value={
                              draft.ad_override_json?.overlay_start ?? activeAdPreset.overlay_start
                            }
                            onChange={(e) =>
                              setDraft({
                                ...draft,
                                ad_override_json: {
                                  ...(draft.ad_override_json ?? {}),
                                  overlay_start: Number(e.target.value),
                                },
                              })
                            }
                            className="w-full px-2 py-1 rounded bg-surface-900 border border-surface-700"
                          />
                        </label>
                        <label className="space-y-1">
                          <span className="text-zinc-400">Конец (с)</span>
                          <input
                            type="number"
                            step="0.5"
                            value={draft.ad_override_json?.overlay_end ?? activeAdPreset.overlay_end}
                            onChange={(e) =>
                              setDraft({
                                ...draft,
                                ad_override_json: {
                                  ...(draft.ad_override_json ?? {}),
                                  overlay_end: Number(e.target.value),
                                },
                              })
                            }
                            className="w-full px-2 py-1 rounded bg-surface-900 border border-surface-700"
                          />
                        </label>
                        <label className="space-y-1">
                          <span className="text-zinc-400">Fade (с)</span>
                          <input
                            type="number"
                            step="0.1"
                            min={0}
                            max={2}
                            value={
                              draft.ad_override_json?.fade_duration ?? activeAdPreset.fade_duration
                            }
                            onChange={(e) =>
                              setDraft({
                                ...draft,
                                ad_override_json: {
                                  ...(draft.ad_override_json ?? {}),
                                  fade_duration: Number(e.target.value),
                                },
                              })
                            }
                            className="w-full px-2 py-1 rounded bg-surface-900 border border-surface-700"
                          />
                        </label>
                      </div>
                    </div>
                  )}
                </>
              )}

              {inspectorTab === "cid" && (
                <>
                  <div className="p-3 rounded-lg bg-surface-950 border border-surface-800 space-y-3">
                    <label className="flex items-center justify-between">
                      <span className="font-semibold text-zinc-100">
                        Уникализация (Anti-Content ID)
                      </span>
                      <input
                        type="checkbox"
                        checked={draft.anti_cid_enabled}
                        onChange={(e) =>
                          setDraft({
                            ...draft,
                            anti_cid_enabled: e.target.checked,
                            anti_cid_json: draft.anti_cid_json ?? DEFAULT_CID,
                          })
                        }
                        className="accent-accent-500"
                      />
                    </label>

                    {draft.anti_cid_enabled && (
                      <div className="space-y-2.5 pt-1">
                        <label className="block space-y-1">
                          <span className="text-zinc-400">
                            Микро-зум: {cidCfg.zoom.toFixed(2)}x
                          </span>
                          <input
                            type="range"
                            min={1.0}
                            max={1.12}
                            step={0.01}
                            value={cidCfg.zoom}
                            onChange={(e) =>
                              setDraft({
                                ...draft,
                                anti_cid_json: { ...cidCfg, zoom: Number(e.target.value) },
                              })
                            }
                            className="w-full accent-accent-500"
                          />
                        </label>

                        <label className="block space-y-1">
                          <span className="text-zinc-400">
                            Скорость и питч аудио: {((cidCfg.speed - 1) * 100).toFixed(1)}%
                          </span>
                          <input
                            type="range"
                            min={0.97}
                            max={1.06}
                            step={0.005}
                            value={cidCfg.speed}
                            onChange={(e) =>
                              setDraft({
                                ...draft,
                                anti_cid_json: { ...cidCfg, speed: Number(e.target.value) },
                              })
                            }
                            className="w-full accent-accent-500"
                          />
                        </label>

                        <label className="block space-y-1">
                          <span className="text-zinc-400">
                            Контраст: {cidCfg.contrast.toFixed(2)}
                          </span>
                          <input
                            type="range"
                            min={0.94}
                            max={1.12}
                            step={0.01}
                            value={cidCfg.contrast}
                            onChange={(e) =>
                              setDraft({
                                ...draft,
                                anti_cid_json: { ...cidCfg, contrast: Number(e.target.value) },
                              })
                            }
                            className="w-full accent-accent-500"
                          />
                        </label>

                        <label className="flex items-center justify-between text-zinc-300">
                          <span>Очистить EXIF/метаданные</span>
                          <input
                            type="checkbox"
                            checked={cidCfg.strip_metadata}
                            onChange={(e) =>
                              setDraft({
                                ...draft,
                                anti_cid_json: { ...cidCfg, strip_metadata: e.target.checked },
                              })
                            }
                            className="accent-accent-500"
                          />
                        </label>
                      </div>
                    )}
                  </div>

                  {/* Metadata for .txt export */}
                  <div className="space-y-2.5">
                    <label className="block space-y-1">
                      <span className="text-zinc-400">Заголовок для .txt</span>
                      <input
                        value={draft.title}
                        onChange={(e) => setDraft({ ...draft, title: e.target.value })}
                        className="w-full px-2.5 py-1.5 rounded bg-surface-950 border border-surface-700 text-zinc-100"
                      />
                    </label>
                    <label className="block space-y-1">
                      <span className="text-zinc-400">Описание</span>
                      <textarea
                        rows={3}
                        value={draft.description}
                        onChange={(e) => setDraft({ ...draft, description: e.target.value })}
                        className="w-full px-2.5 py-1.5 rounded bg-surface-950 border border-surface-700 text-zinc-100"
                      />
                    </label>
                    <label className="block space-y-1">
                      <span className="text-zinc-400">Хэштеги (через пробел)</span>
                      <input
                        value={draft.tags_json.join(" ")}
                        onChange={(e) =>
                          setDraft({
                            ...draft,
                            tags_json: e.target.value.split(/\s+/).filter(Boolean),
                          })
                        }
                        className="w-full px-2.5 py-1.5 rounded bg-surface-950 border border-surface-700 text-zinc-100"
                      />
                    </label>
                    {draft.reason && (
                      <div className="p-2.5 rounded-lg bg-surface-950 border border-surface-800 text-[11px] text-zinc-400">
                        <span className="text-zinc-300 font-medium">Почему выбрано AI: </span>
                        {draft.reason}
                      </div>
                    )}
                  </div>
                </>
              )}
            </div>

            {/* Inspector Footer Actions */}
            <div className="p-3 border-t border-surface-800 bg-surface-950 space-y-2 shrink-0">
              <div className="grid grid-cols-2 gap-2">
                <button
                  onClick={() => void saveDraft()}
                  disabled={saving}
                  className="inline-flex items-center justify-center gap-1.5 py-2 rounded-lg bg-surface-800 hover:bg-surface-700 border border-surface-700 text-xs font-medium text-zinc-100"
                >
                  <Save className="w-3.5 h-3.5" />
                  Сохранить
                </button>
                <button
                  onClick={() => void handleRenderPreview()}
                  className="inline-flex items-center justify-center gap-1.5 py-2 rounded-lg bg-surface-800 hover:bg-surface-700 border border-surface-700 text-xs font-medium text-sky-300"
                >
                  <Play className="w-3.5 h-3.5" />
                  Рендер превью
                </button>
              </div>
              <button
                onClick={() => void handleExportSingle()}
                className="w-full inline-flex items-center justify-center gap-2 py-2.5 rounded-lg bg-accent-600 hover:bg-accent-500 text-xs font-semibold text-white shadow-lg shadow-accent-600/20"
              >
                <Download className="w-4 h-4" />
                Экспортировать клип (.mp4 + .txt)
              </button>
            </div>
          </aside>
        )}
      </div>
    </div>
  );
}
