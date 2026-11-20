import { useEffect, useState } from "react";
import {
  Activity,
  CheckCircle2,
  FolderOpen,
  Loader2,
  Play,
  Plus,
  Radio,
  RefreshCw,
  Scissors,
  Send,
  SlidersHorizontal,
  Square,
  Trash2,
} from "lucide-react";
import { api } from "../lib/api";
import { openInExplorer } from "../lib/tauri";
import { useAppStore } from "../store/useAppStore";
import type { CropMode, LiveAiMode, LiveChannel } from "../types/api";

const PLATFORM_STYLES: Record<string, string> = {
  twitch: "bg-purple-500/15 text-purple-300 border-purple-500/30",
  kick: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  youtube: "bg-rose-500/15 text-rose-300 border-rose-500/30",
};

const STATUS_STYLES: Record<string, string> = {
  recording: "bg-rose-500/20 text-rose-300 border-rose-500/40",
  online: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  offline: "bg-zinc-800 text-zinc-400 border-zinc-700",
  error: "bg-amber-500/15 text-amber-300 border-amber-500/30",
};

const STATUS_LABELS: Record<string, string> = {
  recording: "Идёт запись",
  online: "Онлайн",
  offline: "Оффлайн",
  error: "Ошибка",
};

function MiniSparkline({
  values,
  colorClass,
  minVal,
  maxVal,
}: {
  values: number[];
  colorClass: string;
  minVal: number;
  maxVal: number;
}) {
  const padded = values.length > 0 ? values.slice(-24) : [minVal];
  const span = Math.max(1e-3, maxVal - minVal);
  return (
    <div className="flex items-end gap-0.5 h-6">
      {padded.map((v, idx) => {
        const pct = Math.max(8, Math.min(100, Math.round(((v - minVal) / span) * 100)));
        return (
          <div
            key={idx}
            className={`w-1 rounded-t ${colorClass}`}
            style={{ height: `${pct}%` }}
          />
        );
      })}
    </div>
  );
}

export function LiveMonitorPage() {
  const liveChannels = useAppStore((s) => s.liveChannels);
  const liveClips = useAppStore((s) => s.liveClips);
  const subtitlePresets = useAppStore((s) => s.subtitlePresets);
  const adPresets = useAppStore((s) => s.adPresets);
  const backgrounds = useAppStore((s) => s.backgrounds);
  const socialAccounts = useAppStore((s) => s.socialAccounts);
  const settings = useAppStore((s) => s.settings);

  const refreshLive = useAppStore((s) => s.refreshLive);
  const refreshPublishing = useAppStore((s) => s.refreshPublishing);
  const selectProject = useAppStore((s) => s.selectProject);
  const selectClip = useAppStore((s) => s.selectClip);
  const setActiveTab = useAppStore((s) => s.setActiveTab);
  const addToast = useAppStore((s) => s.addToast);

  const [channelUrl, setChannelUrl] = useState("");
  const [channelName, setChannelName] = useState("");
  const [aiMode, setAiMode] = useState<LiveAiMode>(settings?.live_default_ai_mode ?? "zero_ai");
  const [bufferMinutes, setBufferMinutes] = useState(
    settings?.live_default_buffer_minutes ?? 20
  );
  const [checkIntervalSec, setCheckIntervalSec] = useState(
    settings?.live_check_interval_sec ?? 60
  );
  const [cropMode, setCropMode] = useState<CropMode>("auto_track");
  const [subtitlePresetId, setSubtitlePresetId] = useState<string>("");
  const [adPresetId, setAdPresetId] = useState<string>("");
  const [backgroundAssetId, setBackgroundAssetId] = useState<string>("");
  const [antiCidEnabled, setAntiCidEnabled] = useState(
    settings?.default_anti_cid_enabled ?? true
  );
  const [autoPublish, setAutoPublish] = useState(false);
  const [selectedAccounts, setSelectedAccounts] = useState<string[]>([]);

  const [adding, setAdding] = useState(false);
  const [busyIds, setBusyIds] = useState<Record<string, boolean>>({});
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [clipNowSec, setClipNowSec] = useState(45);

  useEffect(() => {
    void refreshLive();
    void refreshPublishing();
  }, [refreshLive, refreshPublishing]);

  const setBusy = (id: string, val: boolean) =>
    setBusyIds((prev) => ({ ...prev, [id]: val }));

  const toggleAccountChip = (accountId: string) => {
    setSelectedAccounts((prev) =>
      prev.includes(accountId) ? prev.filter((x) => x !== accountId) : [...prev, accountId]
    );
  };

  const handleAddChannel = async () => {
    if (!channelUrl.trim()) {
      addToast({ type: "warning", title: "Укажите ссылку на стрим Twitch / Kick / YouTube" });
      return;
    }
    setAdding(true);
    try {
      await api.createLiveChannel({
        channel_url: channelUrl.trim(),
        name: channelName.trim() || undefined,
        ai_mode: aiMode,
        buffer_minutes: bufferMinutes,
        check_interval_sec: checkIntervalSec,
        crop_mode: cropMode,
        subtitle_preset_id: subtitlePresetId || null,
        ad_preset_id: adPresetId || null,
        background_asset_id: cropMode === "split_screen" ? backgroundAssetId || null : null,
        anti_cid_enabled: antiCidEnabled,
        auto_publish: autoPublish,
        publish_account_ids_json: selectedAccounts,
      });
      setChannelUrl("");
      setChannelName("");
      await refreshLive();
      addToast({ type: "success", title: "Канал добавлен в Live Monitor" });
    } catch (err) {
      addToast({
        type: "error",
        title: "Не удалось добавить канал",
        message: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setAdding(false);
    }
  };

  const handleToggleRun = async (ch: LiveChannel) => {
    setBusy(ch.id, true);
    try {
      if (ch.status === "recording" || ch.enabled) {
        await api.stopLiveChannel(ch.id);
      } else {
        await api.startLiveChannel(ch.id);
      }
      await refreshLive();
    } catch (err) {
      addToast({
        type: "error",
        title: "Ошибка управления каналом",
        message: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setBusy(ch.id, false);
    }
  };

  const handleCheckOnline = async (ch: LiveChannel) => {
    setBusy(ch.id, true);
    try {
      const res = await api.checkLiveChannel(ch.id);
      await refreshLive();
      const isLive = Boolean(res.probe?.is_live);
      addToast({
        type: isLive ? "success" : "info",
        title: isLive ? `${ch.name} в эфире!` : `${ch.name} сейчас оффлайн`,
        message: typeof res.probe?.stream_title === "string" ? res.probe.stream_title : undefined,
      });
    } catch (err) {
      addToast({
        type: "error",
        title: "Не удалось проверить статус",
        message: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setBusy(ch.id, false);
    }
  };

  const handleClipNow = async (ch: LiveChannel) => {
    setBusy(ch.id, true);
    try {
      await api.clipLiveNow(ch.id, clipNowSec);
      await refreshLive();
      addToast({
        type: "success",
        title: "Момент вырезан из буфера",
        message: "Запущена обработка: YOLOv8 → .ass → Реклама → Anti-CID → NVENC",
      });
    } catch (err) {
      addToast({
        type: "error",
        title: "Не удалось вырезать клип",
        message: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setBusy(ch.id, false);
    }
  };

  const handleUpdateSensitivity = async (
    ch: LiveChannel,
    patch: Partial<LiveChannel>
  ) => {
    try {
      await api.updateLiveChannel(ch.id, patch);
      await refreshLive();
    } catch (err) {
      addToast({
        type: "error",
        title: "Ошибка обновления настроек канала",
        message: err instanceof Error ? err.message : String(err),
      });
    }
  };

  const handleDeleteChannel = async (ch: LiveChannel) => {
    setBusy(ch.id, true);
    try {
      await api.deleteLiveChannel(ch.id);
      await refreshLive();
    } catch (err) {
      addToast({
        type: "error",
        title: "Не удалось удалить канал",
        message: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setBusy(ch.id, false);
    }
  };

  const handleOpenClipInStudio = async (projectId: string, clipId: string) => {
    await selectProject(projectId);
    selectClip(clipId);
    setActiveTab("studio");
  };

  const handleEnqueueLiveClip = async (clipId: string) => {
    const enabledIds = socialAccounts.filter((a) => a.enabled).map((a) => a.id);
    if (enabledIds.length === 0) {
      addToast({
        type: "warning",
        title: "Нет подключённых аккаунтов",
        message: "Добавьте аккаунт во вкладке Настройки → Публикация и Автопостинг",
      });
      return;
    }
    try {
      await api.enqueuePublish({ clip_id: clipId, account_ids: enabledIds });
      await refreshPublishing();
      addToast({
        type: "success",
        title: "Клип поставлен в очередь публикаций",
        message: `Слот рассчитан для ${enabledIds.length} аккаунт(ов)`,
      });
    } catch (err) {
      addToast({
        type: "error",
        title: "Не удалось добавить в очередь",
        message: err instanceof Error ? err.message : String(err),
      });
    }
  };

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-7xl mx-auto space-y-6">
        {/* Header */}
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold text-white flex items-center gap-2">
              <Radio className="w-5 h-5 text-rose-400" />
              Live Monitor & Auto-Clipping
            </h1>
            <p className="text-xs text-zinc-400 mt-1">
              Кольцевой буфер Twitch / Kick / YouTube + анализ всплесков чата и громкости → авторендер 9:16
            </p>
          </div>
          <div className="flex items-center gap-2 text-xs">
            <span className="px-2.5 py-1 rounded-md bg-surface-900 border border-surface-800 text-zinc-300">
              Режим публикации:{" "}
              <strong className="text-white">
                {settings?.publish_mode === "auto_publish"
                  ? "Автопостинг по расписанию"
                  : "Только локальный экспорт"}
              </strong>
            </span>
            <button
              onClick={() => void refreshLive()}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-surface-800 hover:bg-surface-700 border border-surface-700 text-zinc-200"
            >
              <RefreshCw className="w-3.5 h-3.5" />
              Обновить
            </button>
          </div>
        </div>

        {/* Add Channel Panel */}
        <section className="rounded-xl border border-surface-800 bg-surface-900 p-5 space-y-4 text-xs">
          <div className="flex items-center gap-2 text-sm font-semibold text-white">
            <Plus className="w-4 h-4 text-accent-400" />
            <span>Добавить канал для отслеживания</span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
            <label className="block space-y-1 md:col-span-2">
              <span className="text-zinc-400">Ссылка (Twitch / Kick / YouTube)</span>
              <input
                value={channelUrl}
                onChange={(e) => setChannelUrl(e.target.value)}
                placeholder="https://www.twitch.tv/shroud или https://kick.com/..."
                className="w-full px-3 py-2 rounded-lg bg-surface-950 border border-surface-700 text-zinc-100 focus:outline-none focus:border-accent-500"
              />
            </label>

            <label className="block space-y-1">
              <span className="text-zinc-400">Имя канала (опционально)</span>
              <input
                value={channelName}
                onChange={(e) => setChannelName(e.target.value)}
                placeholder="Авто из URL"
                className="w-full px-3 py-2 rounded-lg bg-surface-950 border border-surface-700 text-zinc-100"
              />
            </label>

            <label className="block space-y-1">
              <span className="text-zinc-400">Режим поиска моментов</span>
              <select
                value={aiMode}
                onChange={(e) => setAiMode(e.target.value as LiveAiMode)}
                className="w-full px-3 py-2 rounded-lg bg-surface-950 border border-surface-700 text-zinc-100"
              >
                <option value="zero_ai">Zero-AI (Чат + Аудио пики)</option>
                <option value="auto">Auto (Zero-AI + LLM если задан ключ)</option>
                <option value="llm">Cloud LLM + Чат сигналы</option>
              </select>
            </label>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
            <label className="block space-y-1">
              <span className="text-zinc-400">Буфер (мин)</span>
              <input
                type="number"
                min={5}
                max={60}
                value={bufferMinutes}
                onChange={(e) => setBufferMinutes(Number(e.target.value))}
                className="w-full px-3 py-2 rounded-lg bg-surface-950 border border-surface-700 text-zinc-100"
              />
            </label>

            <label className="block space-y-1">
              <span className="text-zinc-400">Проверка (сек)</span>
              <input
                type="number"
                min={15}
                max={600}
                value={checkIntervalSec}
                onChange={(e) => setCheckIntervalSec(Number(e.target.value))}
                className="w-full px-3 py-2 rounded-lg bg-surface-950 border border-surface-700 text-zinc-100"
              />
            </label>

            <label className="block space-y-1">
              <span className="text-zinc-400">Формат 9:16</span>
              <select
                value={cropMode}
                onChange={(e) => setCropMode(e.target.value as CropMode)}
                className="w-full px-3 py-2 rounded-lg bg-surface-950 border border-surface-700 text-zinc-100"
              >
                <option value="auto_track">YOLOv8 Smart Crop</option>
                <option value="cinema_blur">Cinema Blur</option>
                <option value="split_screen">Split-Screen</option>
              </select>
            </label>

            <label className="block space-y-1">
              <span className="text-zinc-400">Стиль субтитров</span>
              <select
                value={subtitlePresetId}
                onChange={(e) => setSubtitlePresetId(e.target.value)}
                className="w-full px-3 py-2 rounded-lg bg-surface-950 border border-surface-700 text-zinc-100"
              >
                <option value="">По умолчанию</option>
                {subtitlePresets.map((sp) => (
                  <option key={sp.id} value={sp.id}>
                    {sp.name}
                  </option>
                ))}
              </select>
            </label>

            <label className="block space-y-1">
              <span className="text-zinc-400">Микрореклама</span>
              <select
                value={adPresetId}
                onChange={(e) => setAdPresetId(e.target.value)}
                className="w-full px-3 py-2 rounded-lg bg-surface-950 border border-surface-700 text-zinc-100"
              >
                <option value="">Без рекламы</option>
                {adPresets.map((ap) => (
                  <option key={ap.id} value={ap.id}>
                    {ap.name}
                  </option>
                ))}
              </select>
            </label>

            {cropMode === "split_screen" ? (
              <label className="block space-y-1">
                <span className="text-zinc-400">Нижний фон (gameplay)</span>
                <select
                  value={backgroundAssetId}
                  onChange={(e) => setBackgroundAssetId(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg bg-surface-950 border border-surface-700 text-zinc-100"
                >
                  <option value="">Случайный / Нет</option>
                  {backgrounds.map((bg) => (
                    <option key={bg.id} value={bg.id}>
                      {bg.name}
                    </option>
                  ))}
                </select>
              </label>
            ) : (
              <div className="flex items-end pb-1">
                <label className="inline-flex items-center gap-2 cursor-pointer text-zinc-200">
                  <input
                    type="checkbox"
                    checked={antiCidEnabled}
                    onChange={(e) => setAntiCidEnabled(e.target.checked)}
                    className="accent-accent-500"
                  />
                  Уникализация (Anti-CID)
                </label>
              </div>
            )}
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 pt-1">
            <div className="flex flex-wrap items-center gap-4">
              <label className="inline-flex items-center gap-2 cursor-pointer text-zinc-200">
                <input
                  type="checkbox"
                  checked={autoPublish}
                  onChange={(e) => setAutoPublish(e.target.checked)}
                  className="accent-accent-500"
                />
                Автопостинг клипов с этого канала
              </label>

              {autoPublish && socialAccounts.length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5">
                  {socialAccounts.map((acc) => {
                    const active = selectedAccounts.includes(acc.id);
                    return (
                      <button
                        key={acc.id}
                        type="button"
                        onClick={() => toggleAccountChip(acc.id)}
                        className={`px-2 py-1 rounded border text-[11px] ${
                          active
                            ? "bg-accent-600/20 border-accent-500 text-accent-300"
                            : "bg-surface-950 border-surface-700 text-zinc-400"
                        }`}
                      >
                        {acc.platform}: {acc.name}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>

            <button
              onClick={() => void handleAddChannel()}
              disabled={adding}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-accent-600 hover:bg-accent-500 disabled:opacity-50 font-semibold text-white"
            >
              {adding ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Plus className="w-3.5 h-3.5" />
              )}
              Добавить канал
            </button>
          </div>
        </section>

        {/* Monitored Channels */}
        <section className="space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-white">
              Отслеживаемые каналы ({liveChannels.length})
            </h2>
            <div className="flex items-center gap-2 text-xs text-zinc-400">
              <span>Ручная нарезка:</span>
              <input
                type="number"
                min={10}
                max={180}
                value={clipNowSec}
                onChange={(e) => setClipNowSec(Number(e.target.value))}
                className="w-16 px-2 py-1 rounded bg-surface-900 border border-surface-700 text-zinc-100"
              />
              <span>сек</span>
            </div>
          </div>

          {liveChannels.length === 0 ? (
            <div className="rounded-xl border border-dashed border-surface-800 bg-surface-900/50 p-8 text-center text-xs text-zinc-400">
              Добавьте ссылку на канал Twitch, Kick или YouTube выше, чтобы начать Live-мониторинг и запись кольцевого буфера.
            </div>
          ) : (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              {liveChannels.map((ch) => {
                const busy = Boolean(busyIds[ch.id]);
                const expanded = expandedId === ch.id;
                const rt = ch.runtime;
                return (
                  <div
                    key={ch.id}
                    className="rounded-xl border border-surface-800 bg-surface-900 p-4 space-y-3 text-xs"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="space-y-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span
                            className={`px-2 py-0.5 rounded border text-[10px] font-semibold uppercase ${
                              PLATFORM_STYLES[ch.platform] ?? "bg-surface-800 text-zinc-300"
                            }`}
                          >
                            {ch.platform}
                          </span>
                          <span className="font-semibold text-sm text-white truncate">
                            {ch.name}
                          </span>
                          <span
                            className={`px-2 py-0.5 rounded border text-[10px] font-medium ${
                              STATUS_STYLES[ch.status] ?? STATUS_STYLES.offline
                            }`}
                          >
                            {STATUS_LABELS[ch.status] ?? ch.status}
                          </span>
                          <span className="px-1.5 py-0.5 rounded bg-surface-950 border border-surface-800 text-[10px] text-zinc-400">
                            {ch.ai_mode === "zero_ai" ? "Zero-AI" : ch.ai_mode.toUpperCase()}
                          </span>
                        </div>
                        <p className="text-[11px] text-zinc-400 truncate">{ch.channel_url}</p>
                        {ch.stream_title && (
                          <p className="text-zinc-200 truncate">
                            {ch.stream_title}
                            {ch.viewer_count != null && (
                              <span className="ml-2 text-zinc-400">
                                ({ch.viewer_count.toLocaleString()} зрит.)
                              </span>
                            )}
                          </p>
                        )}
                      </div>

                      <div className="flex items-center gap-1.5 shrink-0">
                        <button
                          onClick={() => void handleToggleRun(ch)}
                          disabled={busy}
                          title={ch.status === "recording" || ch.enabled ? "Остановить" : "Запустить"}
                          className={`inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg font-medium border ${
                            ch.status === "recording" || ch.enabled
                              ? "bg-rose-500/15 hover:bg-rose-500/25 border-rose-500/30 text-rose-300"
                              : "bg-emerald-500/15 hover:bg-emerald-500/25 border-emerald-500/30 text-emerald-300"
                          }`}
                        >
                          {ch.status === "recording" || ch.enabled ? (
                            <>
                              <Square className="w-3 h-3" /> Стоп
                            </>
                          ) : (
                            <>
                              <Play className="w-3 h-3" /> Старт
                            </>
                          )}
                        </button>

                        <button
                          onClick={() => void handleCheckOnline(ch)}
                          disabled={busy}
                          title="Проверить онлайн сейчас"
                          className="p-1.5 rounded-lg bg-surface-800 hover:bg-surface-700 border border-surface-700 text-zinc-300"
                        >
                          <RefreshCw className={`w-3.5 h-3.5 ${busy ? "animate-spin" : ""}`} />
                        </button>

                        <button
                          onClick={() => setExpandedId(expanded ? null : ch.id)}
                          title="Чувствительность и настройки"
                          className="p-1.5 rounded-lg bg-surface-800 hover:bg-surface-700 border border-surface-700 text-zinc-300"
                        >
                          <SlidersHorizontal className="w-3.5 h-3.5" />
                        </button>

                        <button
                          onClick={() => void handleDeleteChannel(ch)}
                          disabled={busy}
                          title="Удалить канал"
                          className="p-1.5 rounded-lg bg-surface-800 hover:bg-rose-500/20 border border-surface-700 text-zinc-400 hover:text-rose-300"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </div>

                    {ch.last_error && (
                      <div className="p-2 rounded bg-amber-500/10 border border-amber-500/30 text-amber-200 text-[11px]">
                        {ch.last_error}
                      </div>
                    )}

                    {/* Live telemetry & ring buffer */}
                    <div className="grid grid-cols-3 gap-3 p-2.5 rounded-lg bg-surface-950 border border-surface-800">
                      <div>
                        <div className="text-[10px] text-zinc-400 mb-1 flex items-center gap-1">
                          <Activity className="w-3 h-3 text-emerald-400" /> Аудио (dBFS)
                        </div>
                        <MiniSparkline
                          values={rt?.audio_db_recent ?? []}
                          colorClass="bg-emerald-400/80"
                          minVal={-55}
                          maxVal={0}
                        />
                      </div>

                      <div>
                        <div className="text-[10px] text-zinc-400 mb-1">Чат (сообщ/с)</div>
                        <MiniSparkline
                          values={rt?.chat_rate_recent ?? []}
                          colorClass="bg-accent-500/80"
                          minVal={0}
                          maxVal={15}
                        />
                      </div>

                      <div className="flex flex-col justify-between items-end">
                        <div className="text-right text-[11px] text-zinc-300 tabular-nums">
                          Буфер: {Math.round(rt?.buffer_span_sec ?? 0)}с •{" "}
                          {rt?.buffer_disk_mb ?? 0} MB
                        </div>
                        <button
                          onClick={() => void handleClipNow(ch)}
                          disabled={busy || ch.status !== "recording"}
                          className="inline-flex items-center gap-1 px-2 py-1 rounded bg-accent-600 hover:bg-accent-500 disabled:opacity-40 text-white text-[11px] font-medium"
                        >
                          <Scissors className="w-3 h-3" /> Клип -{clipNowSec}с
                        </button>
                      </div>
                    </div>

                    {expanded && (
                      <div className="p-3 rounded-lg bg-surface-950 border border-surface-800 space-y-3">
                        <div className="grid grid-cols-2 gap-3">
                          <label className="block space-y-1">
                            <span className="text-zinc-400">
                              Чувствительность чата ({ch.chat_sensitivity.toFixed(1)}x)
                            </span>
                            <input
                              type="range"
                              min={1.1}
                              max={4.0}
                              step={0.1}
                              value={ch.chat_sensitivity}
                              onChange={(e) =>
                                void handleUpdateSensitivity(ch, {
                                  chat_sensitivity: Number(e.target.value),
                                })
                              }
                              className="w-full accent-accent-500"
                            />
                          </label>

                          <label className="block space-y-1">
                            <span className="text-zinc-400">
                              Чувствительность аудио ({ch.audio_sensitivity.toFixed(1)}x)
                            </span>
                            <input
                              type="range"
                              min={1.1}
                              max={4.0}
                              step={0.1}
                              value={ch.audio_sensitivity}
                              onChange={(e) =>
                                void handleUpdateSensitivity(ch, {
                                  audio_sensitivity: Number(e.target.value),
                                })
                              }
                              className="w-full accent-accent-500"
                            />
                          </label>
                        </div>

                        <div className="grid grid-cols-3 gap-3">
                          <label className="block space-y-1">
                            <span className="text-zinc-400">До пика (сек)</span>
                            <input
                              type="number"
                              min={5}
                              max={90}
                              value={ch.clip_pre_sec}
                              onChange={(e) =>
                                void handleUpdateSensitivity(ch, {
                                  clip_pre_sec: Number(e.target.value),
                                })
                              }
                              className="w-full px-2 py-1 rounded bg-surface-900 border border-surface-700 text-zinc-100"
                            />
                          </label>
                          <label className="block space-y-1">
                            <span className="text-zinc-400">После пика (сек)</span>
                            <input
                              type="number"
                              min={2}
                              max={60}
                              value={ch.clip_post_sec}
                              onChange={(e) =>
                                void handleUpdateSensitivity(ch, {
                                  clip_post_sec: Number(e.target.value),
                                })
                              }
                              className="w-full px-2 py-1 rounded bg-surface-900 border border-surface-700 text-zinc-100"
                            />
                          </label>
                          <label className="block space-y-1">
                            <span className="text-zinc-400">Пауза (сек)</span>
                            <input
                              type="number"
                              min={15}
                              max={600}
                              value={ch.min_clip_gap_sec}
                              onChange={(e) =>
                                void handleUpdateSensitivity(ch, {
                                  min_clip_gap_sec: Number(e.target.value),
                                })
                              }
                              className="w-full px-2 py-1 rounded bg-surface-900 border border-surface-700 text-zinc-100"
                            />
                          </label>
                        </div>

                        <div className="flex items-center justify-between pt-1">
                          <select
                            value={ch.ai_mode}
                            onChange={(e) =>
                              void handleUpdateSensitivity(ch, {
                                ai_mode: e.target.value as LiveAiMode,
                              })
                            }
                            className="px-2 py-1 rounded bg-surface-900 border border-surface-700 text-zinc-200"
                          >
                            <option value="zero_ai">Zero-AI</option>
                            <option value="auto">Auto (LLM fallback Zero-AI)</option>
                            <option value="llm">LLM + Чат</option>
                          </select>

                          <label className="inline-flex items-center gap-2 cursor-pointer text-zinc-200">
                            <input
                              type="checkbox"
                              checked={ch.auto_publish}
                              onChange={(e) =>
                                void handleUpdateSensitivity(ch, {
                                  auto_publish: e.target.checked,
                                })
                              }
                              className="accent-accent-500"
                            />
                            Автопостинг клипов канала
                          </label>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </section>

        {/* Live Clips Feed */}
        <section className="rounded-xl border border-surface-800 bg-surface-900 p-5 space-y-3 text-xs">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-white">
              Лента клипов с прямых эфиров ({liveClips.length})
            </h2>
            <span className="text-[11px] text-zinc-400">
              YOLOv8 9:16 + .ass субтитры + реклама + Anti-CID → NVENC
            </span>
          </div>

          {liveClips.length === 0 ? (
            <p className="text-zinc-400 py-4">
              Пока нет клипов, вырезанных в реальном времени. Они появятся здесь автоматически при всплеске чата/аудио или после нажатия «Клип».
            </p>
          ) : (
            <div className="divide-y divide-surface-800">
              {liveClips.map((item) => {
                const { clip } = item;
                const exported = Boolean(clip.export_path);
                return (
                  <div
                    key={clip.id}
                    className="py-3 flex flex-wrap items-center justify-between gap-3"
                  >
                    <div className="space-y-1 min-w-0 flex-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span
                          className={`px-1.5 py-0.5 rounded border text-[10px] uppercase ${
                            PLATFORM_STYLES[item.platform] ?? "bg-surface-800 text-zinc-300"
                          }`}
                        >
                          {item.platform || "live"}
                        </span>
                        <span className="text-zinc-400">{item.channel_name}</span>
                        <span className="font-semibold text-zinc-100 truncate">{clip.title}</span>
                        <span className="px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 text-[10px]">
                          {Math.round(clip.virality_score)} / 100
                        </span>
                        <span className="px-1.5 py-0.5 rounded bg-surface-950 border border-surface-800 text-[10px] text-zinc-300">
                          {clip.status}
                        </span>
                      </div>
                      <p className="text-[11px] text-zinc-400 truncate">
                        {clip.reason} • {Math.round(clip.end_time - clip.start_time)}с
                      </p>
                    </div>

                    <div className="flex items-center gap-2 shrink-0">
                      <button
                        onClick={() => void handleOpenClipInStudio(clip.project_id, clip.id)}
                        className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-surface-800 hover:bg-surface-700 border border-surface-700 text-zinc-200"
                      >
                        <Scissors className="w-3.5 h-3.5" /> В Студию
                      </button>

                      {exported && (
                        <button
                          onClick={() => void handleEnqueueLiveClip(clip.id)}
                          className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-accent-600/20 hover:bg-accent-600/30 border border-accent-500/40 text-accent-300"
                        >
                          <Send className="w-3.5 h-3.5" /> В очередь постинга
                        </button>
                      )}

                      {clip.export_path && (
                        <button
                          onClick={() => void openInExplorer(clip.export_path!)}
                          title="Открыть файл в проводнике"
                          className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-surface-800 hover:bg-surface-700 border border-surface-700 text-zinc-200"
                        >
                          <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
                          <FolderOpen className="w-3.5 h-3.5" />
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
